"""StreamDriver 测试：AudioChunk 流 → 每声道 VAD → whisper_runner → publish asr.final.chN。

用假 source（直接构造 AudioChunk）+ 假探测器（幅度）+ 假 runner + 捕获 publish，
端到端验证驱动逻辑，无需真模型 / 真音频文件。
"""
import numpy as np

from backend.audio.source import AudioChunk
from backend.core.events import ASR_FINAL_CH1, ASR_FINAL_CH2, AsrFinalPayload
from backend.vad.models import VadConfig
from backend.asr.stream_driver import StreamDriver


# ── 假件 ──────────────────────────────────────────────────────────────────────


class _FakeSource:
    """上下文管理器 + AudioChunk 迭代器，喂预置 chunk。"""

    def __init__(self, chunks: list[AudioChunk]) -> None:
        self._chunks = chunks

    def __enter__(self) -> "_FakeSource":
        return self

    def __exit__(self, *exc: object) -> None:
        pass

    def __iter__(self):
        return iter(self._chunks)


class _AmplitudeVad:
    def __init__(self, amp: float = 1000.0) -> None:
        self._amp = amp

    def speech_prob(self, frame: np.ndarray) -> float:
        return 1.0 if float(np.sqrt(np.mean(frame.astype(np.float64) ** 2))) > self._amp else 0.0

    def reset(self) -> None:
        pass


class _FakeRunner:
    def __init__(self, text: str = "转录文本") -> None:
        self._text = text
        self.seen: list[np.ndarray] = []

    def transcribe_pcm(self, pcm: np.ndarray) -> str:
        self.seen.append(pcm)
        return self._text


def _silence(n: int) -> np.ndarray:
    return np.zeros(n, dtype=np.int16)


def _speech(n: int, amp: int = 8000) -> np.ndarray:
    rng = np.random.default_rng(0)
    return rng.integers(-amp, amp, size=n, endpoint=True).astype(np.int16)


def _vad_cfg(**kw) -> VadConfig:
    from dataclasses import replace
    base = VadConfig(frame_samples=512, threshold=0.5, min_silence_ms=300,
                     min_speech_ms=250, pre_roll_ms=200, post_roll_ms=200, max_segment_ms=30000)
    return replace(base, **kw)


def _chunk(seq: int, start_frame: int, channels: list[np.ndarray]) -> AudioChunk:
    return AudioChunk(seq=seq, channels=channels, n_frames=len(channels[0]), start_frame=start_frame)


def _drive(chunks, runner=None, detector_factory=None, vad_config=None, process_channels=None):
    runner = runner or _FakeRunner()
    published: list[tuple[str, object]] = []
    driver = StreamDriver(
        runner=runner,
        publish=lambda topic, payload: published.append((topic, payload)),
        vad_config=vad_config or _vad_cfg(),
        detector_factory=detector_factory or (lambda: _AmplitudeVad()),
        process_channels=process_channels,
    )
    driver.run(_FakeSource(chunks))
    return published, runner


# ── 测试 ──────────────────────────────────────────────────────────────────────


def test_ch1_segment_publishes_asr_final_ch1():
    audio = np.concatenate([_silence(4000), _speech(8000), _silence(8000)])
    published, _ = _drive([_chunk(0, 0, [audio])])
    assert len(published) == 1
    topic, payload = published[0]
    assert topic == ASR_FINAL_CH1
    assert isinstance(payload, AsrFinalPayload)
    assert payload.text == "转录文本"
    assert payload.speaker is None
    assert payload.is_partial is False
    assert payload.take_id is None


def test_ch2_segment_publishes_asr_final_ch2():
    silent = _silence(20000)
    voice = np.concatenate([_silence(4000), _speech(8000), _silence(8000)])
    published, _ = _drive([_chunk(0, 0, [silent, voice])])
    topics = [t for t, _ in published]
    assert ASR_FINAL_CH2 in topics
    assert ASR_FINAL_CH1 not in topics  # ch1 全静音，无段


def test_frames_converted_to_ms():
    # start_frame(16k 帧) → payload.start_frame(ms) = round(frame/16)
    audio = np.concatenate([_silence(4000), _speech(8000), _silence(8000)])
    published, _ = _drive([_chunk(0, 32000, [audio])])
    _, payload = published[0]
    assert isinstance(payload, AsrFinalPayload)
    # 段起点绝对帧 ≥ 32000，转 ms 应 ≈ frame/16
    assert payload.end_frame > payload.start_frame
    assert payload.start_frame >= 32000 // 16


def test_empty_transcription_skipped():
    audio = np.concatenate([_silence(4000), _speech(8000), _silence(8000)])
    published, _ = _drive([_chunk(0, 0, [audio])], runner=_FakeRunner(text="  "))
    assert published == []  # 空文本不 publish


def test_two_channels_independent_topics():
    ch1 = np.concatenate([_silence(4000), _speech(8000), _silence(8000)])
    ch2 = np.concatenate([_silence(4000), _speech(8000), _silence(8000)])
    published, _ = _drive([_chunk(0, 0, [ch1, ch2])])
    topics = sorted(t for t, _ in published)
    assert topics == sorted([ASR_FINAL_CH1, ASR_FINAL_CH2])


def test_process_channels_ch1_only_skips_ch2():
    # 双声道同源（设备复制），process_channels=(0,) → 只出 ch1，不重复 ch2
    ch1 = np.concatenate([_silence(4000), _speech(8000), _silence(8000)])
    ch2 = np.concatenate([_silence(4000), _speech(8000), _silence(8000)])
    published, _ = _drive([_chunk(0, 0, [ch1, ch2])], process_channels=(0,))
    topics = [t for t, _ in published]
    assert topics == [ASR_FINAL_CH1]
    assert ASR_FINAL_CH2 not in topics


def test_stop_breaks_loop_before_next_chunk():
    # 第一个 chunk 后置 stop；第二个 chunk 不应被处理（模拟 take.end 停采集）
    runner = _FakeRunner()
    published: list[tuple[str, object]] = []
    driver = StreamDriver(
        runner=runner,
        publish=lambda topic, payload: published.append((topic, payload)),
        vad_config=_vad_cfg(),
        detector_factory=lambda: _AmplitudeVad(),
    )
    seg_audio = np.concatenate([_silence(4000), _speech(8000), _silence(8000)])

    def _gen():
        yield _chunk(0, 0, [seg_audio])
        driver.stop()
        yield _chunk(1, 32000, [_speech(8000)])  # stop 后，不应处理

    class _Src:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def __iter__(self):
            return _gen()

    driver.run(_Src())
    assert len(published) == 1  # 仅第一个 chunk 的段


def test_flush_emits_trailing_segment_across_chunks():
    # 语音延续到流末（无收尾静音），分两个 chunk 喂，flush 补出段
    c0 = _chunk(0, 0, [np.concatenate([_silence(4000), _speech(4000)])])
    c1 = _chunk(1, 8000, [_speech(4000)])
    published, _ = _drive([c0, c1])
    assert len(published) == 1
    assert published[0][0] == ASR_FINAL_CH1
