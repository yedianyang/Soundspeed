"""BufferedAudioSource：把设备 read 挪到独立 reader 线程，消除转录阻塞致的采集溢出（issue #17）。

问题：StreamDriver.run() 单线程里 read → VAD → 同步 transcribe_pcm，转录那 0.4~0.6s 内没人
read()，PortAudio HAL 输入缓冲堆满溢出丢音频（device_source.py overflowed=True）。

解法：reader 线程只做「inner.read → 无界队列」，无论消费端（StreamDriver）转录多慢都持续抽干
HAL，采集节奏不被转录拖累。消费端从队列取 chunk，逐 chunk 喂 audio_sink/VAD（顺序、完整、
start_frame 不变）→ C1/C2 拿到无丢帧输入。转录仍内联在消费端，final 落库时序不变（不动 take.end）。

队列无界、绝不丢（丢=破 C1）；turbo 下消费端平均跟得上实时，队列稳定小。
"""
from __future__ import annotations

import logging
import queue
import threading
from typing import Any

logger = logging.getLogger(__name__)

_SENTINEL = object()


class BufferedAudioSource:
    """包一层 reader 线程的 AudioSource。透传上下文管理器 + 迭代器协议与 overflow_count。"""

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self._q: queue.Queue = queue.Queue()  # 无界：绝不丢帧
        self._stop = threading.Event()
        self._reader: threading.Thread | None = None

    @property
    def overflow_count(self) -> int:
        """透传内层 DeviceSource 的溢出计数（理想情况下 reader 持续抽干，应一直为 0）。"""
        return getattr(self._inner, "overflow_count", 0)

    def __enter__(self) -> "BufferedAudioSource":
        # 在调用线程打开内层（设备 open 失败立即抛 DeviceError），再起 reader。
        self._inner.__enter__()
        self._stop.clear()
        self._reader = threading.Thread(
            target=self._read_loop, name="audio-reader", daemon=True
        )
        self._reader.start()
        return self

    def _read_loop(self) -> None:
        try:
            for chunk in self._inner:
                if self._stop.is_set():
                    break
                self._q.put(chunk)  # 无界 put 永不阻塞 → 始终回去 read()，HAL 不溢出
        except Exception:  # noqa: BLE001
            logger.exception("audio reader 线程异常退出")
        finally:
            self._q.put(_SENTINEL)

    def begin_drain(self) -> None:
        """take.end：让 reader 停读新块、在已缓冲项之后投 SENTINEL。

        消费端（StreamDriver）据此把队列里剩余的 chunk 排空处理完再停，不丢 take 尾巴
        （护 C1/C2 —— 尾段 PCM 进 TakeAudioBuffer、尾句进 VAD/final）。
        """
        self._stop.set()

    def __iter__(self) -> "BufferedAudioSource":
        return self

    def __next__(self) -> Any:
        item = self._q.get()
        if item is _SENTINEL:
            raise StopIteration
        return item

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        if self._reader is not None:
            self._reader.join(timeout=2.0)
        self._inner.__exit__(*exc)
