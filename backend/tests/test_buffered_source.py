"""BufferedAudioSource 测试：把设备 read 挪到独立线程，转录阻塞消费端时仍持续抽干 HAL。

根因（spec 2026-06-07 流式 partial §问题1）：采集与转录同线程，transcribe 阻塞 0.4s 期间不 read
→ PortAudio HAL 输入缓冲溢出丢帧 → 安全阀反复跳闸 → partial 中段消失。BufferedAudioSource 用
reader 线程只做 read→queue，消费端（StreamDriver）无论转录多慢都不影响 read 节奏，HAL 不再溢出。
"""
import time

from backend.audio.buffered_source import BufferedAudioSource


class _FakeInner:
    """假内层源：有限 chunk 列表 + 上下文管理器 + 迭代器，可带 overflow_count。"""

    def __init__(self, items: list, overflow_count: int = 0) -> None:
        self._items = list(items)
        self.overflow_count = overflow_count
        self.entered = False
        self.exited = False

    def __enter__(self) -> "_FakeInner":
        self.entered = True
        return self

    def __exit__(self, *exc: object) -> None:
        self.exited = True

    def __iter__(self):
        return iter(self._items)


def test_yields_all_chunks_in_order():
    """reader 线程把内层所有 chunk 原序投递，消费端一个不少。"""
    inner = _FakeInner(list(range(5)))
    out = []
    with BufferedAudioSource(inner) as src:
        for c in src:
            out.append(c)
    assert out == [0, 1, 2, 3, 4]
    assert inner.entered and inner.exited  # 生命周期透传


def test_no_drop_with_slow_consumer():
    """消费端慢（模拟转录阻塞）→ reader 已读进队列，逐项不丢。"""
    inner = _FakeInner(list(range(10)))
    out = []
    with BufferedAudioSource(inner) as src:
        for c in src:
            out.append(c)
            time.sleep(0.003)  # 慢消费
    assert out == list(range(10))


def test_overflow_count_delegates_to_inner():
    """overflow_count 透传内层（DeviceSource），供 StreamDriver 安全阀读取。"""
    inner = _FakeInner([], overflow_count=7)
    with BufferedAudioSource(inner) as src:
        assert src.overflow_count == 7


def test_empty_inner_stops_cleanly():
    """空内层 → 立即 StopIteration，reader 干净退出。"""
    inner = _FakeInner([])
    out = list(BufferedAudioSource(inner).__enter__())  # 进入后迭代
    assert out == []


class _InfiniteInner:
    """无限源（模拟 DeviceSource）：每 read 带微小 sleep 模拟实时节奏；begin_drain 不停就一直产。"""

    def __init__(self, cap: int = 1000) -> None:
        self._n = 0
        self._cap = cap
        self.entered = False
        self.exited = False

    def __enter__(self) -> "_InfiniteInner":
        self.entered = True
        return self

    def __exit__(self, *exc: object) -> None:
        self.exited = True

    def __iter__(self) -> "_InfiniteInner":
        return self

    def __next__(self) -> int:
        self._n += 1
        if self._n > self._cap:
            raise StopIteration
        time.sleep(0.002)  # 模拟设备实时节奏，避免 reader 空转刷满队列
        return self._n


def test_begin_drain_stops_infinite_reader():
    """begin_drain 让无限 reader（真设备）停下：消费端有限步内排空到 StopIteration。"""
    inner = _InfiniteInner(cap=1000)
    with BufferedAudioSource(inner) as src:
        it = iter(src)
        first = next(it)       # 证明在产
        assert first >= 1
        src.begin_drain()      # take.end：停读新
        rest = list(it)        # 排空已缓冲 + SENTINEL → 必须有限终止
    assert inner.exited
    assert len(rest) < inner._cap  # 远没跑到上限 = begin_drain 真停住了无限 reader
