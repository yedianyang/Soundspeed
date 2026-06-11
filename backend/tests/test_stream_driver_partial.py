"""StreamDriver partial 管线:键相等/序/墓碑三场景/熔断/whisper 回归卫兵。"""
import numpy as np

from backend.asr.stream_driver import StreamDriver
from backend.audio.source import AudioChunk
from backend.core.events import ASR_FINAL_CH1, ASR_PARTIAL_CH1
from backend.vad.models import VadConfig

_FS = 512


class _ScriptedDetector:
    def __init__(self, probs):
        self._probs = list(probs)

    def speech_prob(self, frame):
        return self._probs.pop(0) if self._probs else 0.0

    def reset(self) -> None:
        pass


class _FakeSource:
    """有限 chunk 列表源。"""

    def __init__(self, chunks):
        self._chunks = chunks

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        pass

    def __iter__(self):
        return iter(self._chunks)


class _FakeOffline:
    def __init__(self, text="最终全文"):
        self.text = text

    def transcribe_pcm(self, pcm):
        return self.text


class _FakePartialRunner:
    """脚本化 feed 返回;记录生命周期调用。"""

    def __init__(self, feeds=None, raise_on_feed=False, raise_after_feeds=None):
        self._feeds = list(feeds or [])
        self.raise_on_feed = raise_on_feed
        self.raise_after_feeds = raise_after_feeds  # 第 N+1 次 feed 才抛
        self._feed_count = 0
        self.log: list[str] = []

    def start_turn(self):
        self.log.append("start")

    def end_turn(self):
        self.log.append("end")

    def feed(self, pcm):
        self._feed_count += 1
        if self.raise_on_feed or (
            self.raise_after_feeds is not None and self._feed_count > self.raise_after_feeds
        ):
            raise RuntimeError("boom")
        self.log.append("feed")
        return self._feeds.pop(0) if self._feeds else None


def _chunks(n_chunks, frames_per_chunk=2):
    out = []
    for k in range(n_chunks):
        audio = np.ones(frames_per_chunk * _FS, dtype=np.int16)
        out.append(AudioChunk(seq=k, channels=[audio], n_frames=frames_per_chunk * _FS,
                              start_frame=k * frames_per_chunk * _FS))
    return out


def _cfg():
    return VadConfig(min_silence_ms=64, min_speech_ms=32, pre_roll_ms=0, post_roll_ms=0)


def _run(probs, feeds=None, partial_runner=..., n_chunks=6, offline_text="最终全文",
         raise_on_feed=False, raise_after_feeds=None):
    """跑一遍 driver,返回 (非 audio.level 的 publishes, runner)。partial_runner=None 显式传 None。"""
    pub = []
    runner = (_FakePartialRunner(feeds, raise_on_feed, raise_after_feeds)
              if partial_runner is ... else partial_runner)
    d = StreamDriver(
        runner=_FakeOffline(offline_text),
        publish=lambda topic, payload: pub.append((topic, payload)),
        vad_config=_cfg(),
        detector_factory=lambda: _ScriptedDetector(list(probs)),
        partial_runner=runner,
    )
    d.run(_FakeSource(_chunks(n_chunks)))
    return [p for p in pub if p[0] not in ("audio.level",)], runner


def test_whisper_guard_no_partial_runner_zero_partial_topics():
    """回归卫兵:partial_runner=None → 零 asr.partial topic,final 照常。"""
    probs = [0.9] * 4 + [0.0] * 8
    pubs, _ = _run(probs, partial_runner=None)
    assert all(t == ASR_FINAL_CH1 for t, _ in pubs)
    assert len(pubs) == 1


def test_partial_key_equals_final_key_and_end_monotonic():
    probs = [0.9] * 8 + [0.0] * 4
    pubs, _ = _run(probs, feeds=["你", "你好", "你好吗"])
    partials = [p for t, p in pubs if t == ASR_PARTIAL_CH1 and p.text]
    finals = [p for t, p in pubs if t == ASR_FINAL_CH1]
    assert len(finals) == 1 and len(partials) >= 1
    assert all(p.start_frame == finals[0].start_frame for p in partials)  # 逐位同键
    ends = [p.end_frame for p in partials]
    assert ends == sorted(ends)                                            # end 单调
    assert all(p.is_partial for p in partials) and not finals[0].is_partial


def test_partials_precede_final_per_turn():
    probs = [0.9] * 8 + [0.0] * 4
    pubs, _ = _run(probs, feeds=["a", "ab"])
    topics = [t for t, _ in pubs]
    assert topics.index(ASR_FINAL_CH1) == len(topics) - 1  # final 是该 turn 最后一条


def test_tombstone_when_final_filtered():
    """final 被滤(空转录)→ 发过 partial 的 turn 收墓碑(空文本/同键)。"""
    probs = [0.9] * 8 + [0.0] * 4
    pubs, _ = _run(probs, feeds=["你好"], offline_text="")  # _emit 空文本静默 return
    partials = [p for t, p in pubs if t == ASR_PARTIAL_CH1]
    assert [p.text for p in partials] == ["你好", ""]       # 墓碑恰一条
    assert partials[0].start_frame == partials[1].start_frame
    assert all(t == ASR_PARTIAL_CH1 for t, _ in pubs)       # 无 final


def test_no_tombstone_when_no_partial_sent():
    probs = [0.9] * 8 + [0.0] * 4
    pubs, _ = _run(probs, feeds=[None, None], offline_text="")  # 从未出 partial
    assert pubs == []


def test_fuse_on_feed_exception_final_unaffected():
    probs = [0.9] * 8 + [0.0] * 4
    pubs, runner = _run(probs, raise_on_feed=True)
    partials = [p for t, p in pubs if t == ASR_PARTIAL_CH1]
    finals = [p for t, p in pubs if t == ASR_FINAL_CH1]
    assert len(finals) == 1                  # final 不受熔断影响
    assert all(p.text == "" for p in partials) and len(partials) <= 1  # 至多一条墓碑


def test_fuse_after_partial_sent_emits_tombstone():
    """熔断时活跃 turn 已发过 partial → 墓碑恰一条收尾,final 照发。"""
    probs = [0.9] * 8 + [0.0] * 4
    pubs, _ = _run(probs, feeds=["你好"], raise_after_feeds=1)
    partials = [p for t, p in pubs if t == ASR_PARTIAL_CH1]
    finals = [p for t, p in pubs if t == ASR_FINAL_CH1]
    assert [p.text for p in partials] == ["你好", ""]                  # partial + 墓碑恰一条
    assert partials[1].start_frame == partials[0].start_frame          # 同键
    assert len(finals) == 1                                            # final 不受熔断影响


def test_flush_settles_open_turn_with_tombstone():
    """语音顶到流末 + 尾段 final 被滤 → flush 路径发墓碑结清。"""
    probs = [0.9] * 12                                                 # 不收尾,靠 flush
    pubs, runner = _run(probs, feeds=["你好"], offline_text="")
    partials = [p for t, p in pubs if t == ASR_PARTIAL_CH1]
    assert [p.text for p in partials] == ["你好", ""]                  # flush 墓碑
    assert "end" in runner.log


def test_stop_with_begin_drain_drains_tail():
    """stop 置位 + source 有 begin_drain → 排空尾巴,尾段 final 照出。"""
    class _DrainableSource(_FakeSource):
        def __init__(self, chunks, driver_ref):
            super().__init__(chunks)
            self.drain_calls = 0
            self._driver_ref = driver_ref

        def begin_drain(self):
            self.drain_calls += 1

        def __iter__(self):
            for k, c in enumerate(self._chunks):
                if k == 2:
                    self._driver_ref[0].stop()   # 第 3 个 chunk 前 stop
                yield c

    probs = [0.9] * 8 + [0.0] * 4
    pub = []
    driver_ref = []
    d = StreamDriver(
        runner=_FakeOffline(),
        publish=lambda topic, payload: pub.append((topic, payload)),
        vad_config=_cfg(),
        detector_factory=lambda: _ScriptedDetector(list(probs)),
        partial_runner=_FakePartialRunner(),
    )
    driver_ref.append(d)
    src = _DrainableSource(_chunks(6), driver_ref)
    d.run(src)
    finals = [p for t, p in pub if t == ASR_FINAL_CH1]
    assert src.drain_calls == 1                  # begin_drain 恰一次
    assert len(finals) == 1                      # 尾巴排空,final 没丢


def test_turn_lifecycle_calls():
    """start_turn 在进语音时调一次;end_turn 在结清时调。"""
    probs = [0.9] * 8 + [0.0] * 4
    _, runner = _run(probs, feeds=["x"])
    assert runner.log[0] == "start"
    assert "end" in runner.log
