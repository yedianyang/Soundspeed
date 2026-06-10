"""StreamDriver 测试：AudioChunk 流 → 每声道 VAD → whisper_runner → publish asr.final.chN。

用假 source（直接构造 AudioChunk）+ 假探测器（幅度）+ 假 runner + 捕获 publish，
端到端验证驱动逻辑，无需真模型 / 真音频文件。
"""
import numpy as np

from backend.audio.source import AudioChunk
from backend.core.events import (
    ASR_FINAL_CH1,
    ASR_FINAL_CH2,
    ASR_PARTIAL_CH1,
    ASR_PARTIAL_CH2,
    AsrFinalPayload,
    AsrPartialPayload,
)
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
        self.audio_ctx_seen: list[int | None] = []

    def transcribe_pcm(self, pcm: np.ndarray, audio_ctx: int | None = None) -> str:
        self.seen.append(pcm)
        self.audio_ctx_seen.append(audio_ctx)
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


def _asr_events(published: list[tuple[str, object]]) -> list[tuple[str, object]]:
    """过滤出 ASR 相关事件（排除 audio.level）。"""
    return [(t, p) for t, p in published if t.startswith("asr.")]


def test_ch1_segment_publishes_asr_final_ch1():
    audio = np.concatenate([_silence(4000), _speech(8000), _silence(8000)])
    published, _ = _drive([_chunk(0, 0, [audio])])
    asr = _asr_events(published)
    assert len(asr) == 1
    topic, payload = asr[0]
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
    asr = _asr_events(published)
    assert len(asr) == 1
    _, payload = asr[0]
    assert isinstance(payload, AsrFinalPayload)
    # 段起点绝对帧 ≥ 32000，转 ms 应 ≈ frame/16
    assert payload.end_frame > payload.start_frame
    assert payload.start_frame >= 32000 // 16


def test_empty_transcription_skipped():
    audio = np.concatenate([_silence(4000), _speech(8000), _silence(8000)])
    published, _ = _drive([_chunk(0, 0, [audio])], runner=_FakeRunner(text="  "))
    # 空文本不推 ASR 事件（audio.level 仍会推，不计入此断言）
    assert _asr_events(published) == []


def test_two_channels_independent_topics():
    ch1 = np.concatenate([_silence(4000), _speech(8000), _silence(8000)])
    ch2 = np.concatenate([_silence(4000), _speech(8000), _silence(8000)])
    published, _ = _drive([_chunk(0, 0, [ch1, ch2])])
    asr_topics = sorted(t for t, _ in _asr_events(published))
    assert asr_topics == sorted([ASR_FINAL_CH1, ASR_FINAL_CH2])


def test_process_channels_ch1_only_skips_ch2():
    # 双声道同源（设备复制），process_channels=(0,) → 只出 ch1，不重复 ch2
    ch1 = np.concatenate([_silence(4000), _speech(8000), _silence(8000)])
    ch2 = np.concatenate([_silence(4000), _speech(8000), _silence(8000)])
    published, _ = _drive([_chunk(0, 0, [ch1, ch2])], process_channels=(0,))
    asr_topics = [t for t, _ in _asr_events(published)]
    assert asr_topics == [ASR_FINAL_CH1]
    assert ASR_FINAL_CH2 not in asr_topics


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
    # 第一个 chunk 出 1 条 ASR + 1 条 audio.level；第二个 chunk 被 stop 截断，不处理
    assert len(_asr_events(published)) == 1


def test_flush_emits_trailing_segment_across_chunks():
    # 语音延续到流末（无收尾静音），分两个 chunk 喂，flush 补出段
    c0 = _chunk(0, 0, [np.concatenate([_silence(4000), _speech(4000)])])
    c1 = _chunk(1, 8000, [_speech(4000)])
    published, _ = _drive([c0, c1])
    asr = _asr_events(published)
    assert len(asr) == 1
    assert asr[0][0] == ASR_FINAL_CH1


# ── AUDIO_LEVEL 测试 ───────────────────────────────────────────────────────────


def test_audio_level_published_per_chunk():
    """每个 chunk 对 ch1 算 RMS 并 publish AUDIO_LEVEL。"""
    from backend.core.events import AUDIO_LEVEL, AudioLevelPayload  # noqa: PLC0415

    chunk = _chunk(0, 0, [_speech(3200, amp=8000)])
    published, _ = _drive([chunk])
    level_events = [(t, p) for t, p in published if t == AUDIO_LEVEL]
    assert len(level_events) == 1
    _, payload = level_events[0]
    assert isinstance(payload, AudioLevelPayload)
    assert 0.0 <= payload.rms <= 1.0


def test_audio_level_silence_chunk_rms_near_zero():
    """静音 chunk → rms 约为 0。"""
    from backend.core.events import AUDIO_LEVEL, AudioLevelPayload  # noqa: PLC0415

    chunk = _chunk(0, 0, [_silence(3200)])
    published, _ = _drive([chunk])
    level_events = [(t, p) for t, p in published if t == AUDIO_LEVEL]
    assert len(level_events) == 1
    _, payload = level_events[0]
    assert isinstance(payload, AudioLevelPayload)
    assert payload.rms == 0.0


def test_audio_level_loud_chunk_rms_positive():
    """响的 chunk → rms > 0。"""
    from backend.core.events import AUDIO_LEVEL, AudioLevelPayload  # noqa: PLC0415

    chunk = _chunk(0, 0, [_speech(3200, amp=16000)])
    published, _ = _drive([chunk])
    level_events = [(t, p) for t, p in published if t == AUDIO_LEVEL]
    assert len(level_events) == 1
    _, payload = level_events[0]
    assert isinstance(payload, AudioLevelPayload)
    assert payload.rms > 0.0


def test_audio_level_rms_clamped_to_one():
    """超大振幅（int16 满幅 32767）→ rms 不超过 1.0。"""
    from backend.core.events import AUDIO_LEVEL, AudioLevelPayload  # noqa: PLC0415

    # 全满幅正弦替代：直接填 32767
    loud = np.full(3200, 32767, dtype=np.int16)
    chunk = _chunk(0, 0, [loud])
    published, _ = _drive([chunk])
    level_events = [(t, p) for t, p in published if t == AUDIO_LEVEL]
    assert len(level_events) == 1
    _, payload = level_events[0]
    assert isinstance(payload, AudioLevelPayload)
    assert payload.rms <= 1.0


def test_audio_level_one_per_chunk_multiple_chunks():
    """多个 chunk → AUDIO_LEVEL 每 chunk 一条（数量与 chunk 数相同）。"""
    from backend.core.events import AUDIO_LEVEL  # noqa: PLC0415

    chunks = [_chunk(i, i * 3200, [_speech(3200)]) for i in range(3)]
    published, _ = _drive(chunks)
    level_events = [t for t, _ in published if t == AUDIO_LEVEL]
    assert len(level_events) == 3


# ── 繁简转换测试 ────────────────────────────────────────────────────────────────


def test_emit_normalizes_trad_to_simplified():
    """_emit 产出的 AsrFinalPayload.text 应已是简体（繁体输入被转换）。

    用「漢字測試」（繁体，4 字符，不在幻觉表，不被长度过滤）→ 应转为「汉字测试」。
    """
    audio = np.concatenate([_silence(4000), _speech(8000), _silence(8000)])
    runner = _FakeRunner(text="漢字測試")
    published, _ = _drive([_chunk(0, 0, [audio])], runner=runner)
    asr = _asr_events(published)
    assert len(asr) == 1
    _, payload = asr[0]
    assert isinstance(payload, AsrFinalPayload)
    assert payload.text == "汉字测试"


def test_normalize_idempotent_on_simplified():
    """_normalize_to_simplified 对已是简体或英文文本幂等（输出 == 输入）。"""
    from backend.asr.stream_driver import _normalize_to_simplified  # noqa: PLC0415

    assert _normalize_to_simplified("汉字测试") == "汉字测试"
    assert _normalize_to_simplified("hello world") == "hello world"


def test_normalize_filters_trad_hallucination():
    """繁体幻觉「謝謝觀看」在转简后被 _is_hallucination 拦截，不推送 payload。

    选「謝謝觀看」（4 字符）：未转换时不在幻觉表（繁体），转换后得「谢谢观看」命中表。
    只要转换在过滤之前执行，就应无 ASR 事件输出。
    """
    audio = np.concatenate([_silence(4000), _speech(8000), _silence(8000)])
    runner = _FakeRunner(text="謝謝觀看")
    published, _ = _drive([_chunk(0, 0, [audio])], runner=runner)
    assert _asr_events(published) == []


# ── 流式 partial 测试（spec §3） ────────────────────────────────────────────────


def _partials(published, topic=ASR_PARTIAL_CH1):
    return [(t, p) for t, p in published if t == topic]


def test_throttle_emits_partial_while_speaking():
    """SPEECH 态每 partial_every_chunks 个 chunk 发一条 asr.partial（is_partial=True）。"""
    cfg = _vad_cfg(partial_every_chunks=1)
    # 三个纯语音 chunk：持续说话不收尾 → 期间应有 partial
    chunks = [_chunk(i, i * 8000, [_speech(8000)]) for i in range(3)]
    published, _ = _drive(chunks, vad_config=cfg)
    partials = _partials(published)
    assert len(partials) >= 1
    for _, p in partials:
        assert isinstance(p, AsrPartialPayload)
        assert p.is_partial is True
        assert p.text == "转录文本"
        assert p.speaker is None
        assert p.take_id is None


def test_partials_disabled_when_every_chunks_non_positive():
    """partial_every_chunks <= 0 → 关闭 partial，全程只出 final。"""
    cfg = _vad_cfg(partial_every_chunks=0)
    chunks = [_chunk(i, i * 8000, [_speech(8000)]) for i in range(3)]
    published, _ = _drive(chunks, vad_config=cfg)
    assert _partials(published) == []
    assert _partials(published, ASR_PARTIAL_CH2) == []


def test_dangling_partial_cleared_when_turn_dropped():
    """turn 低于 min_speech 被丢（无 final），但已发过 partial → 发空 partial 清除悬挂行。"""
    cfg = _vad_cfg(partial_every_chunks=1, min_speech_ms=250)  # 250ms = 4000 样本
    # 语音 2000 样本（< min_speech）→ 进入 speech 发 partial；随后静音收尾被丢，无 final
    chunks = [
        _chunk(0, 0, [_speech(2000)]),
        _chunk(1, 2000, [_silence(8000)]),
    ]
    published, _ = _drive(chunks, vad_config=cfg)
    # 段被 min_speech 丢 → 无 final
    assert [t for t, _ in published if t == ASR_FINAL_CH1] == []
    partials = _partials(published)
    assert any(p.text != "" for _, p in partials)   # 收尾前的内容 partial
    assert any(p.text == "" for _, p in partials)   # turn 结束的清除信号（空文本）


class _OverflowSource:
    """从第 overflow_after 个 chunk 起 overflow_count 上涨，模拟采集溢出（安全阀触发）。"""

    def __init__(self, chunks: list, overflow_after: int) -> None:
        self._chunks = chunks
        self._overflow_after = overflow_after
        self.overflow_count = 0

    def __enter__(self) -> "_OverflowSource":
        return self

    def __exit__(self, *exc: object) -> None:
        pass

    def __iter__(self):
        for idx, c in enumerate(self._chunks):
            if idx >= self._overflow_after:
                self.overflow_count = 1
            yield c


def test_overflow_disables_partials_finals_unaffected():
    """采集溢出（overflow_count 上涨）→ 本 take 停发 partial，回退 final-only；final 不受影响。"""
    # grace=0 直测跳闸；rearm 调高避免测试期内重启，隔离「跳闸」行为。
    cfg = _vad_cfg(partial_every_chunks=1, partial_grace_chunks=0, partial_rearm_chunks=999)
    chunks = [_chunk(i, i * 8000, [_speech(8000)]) for i in range(5)]
    published: list[tuple[str, object]] = []
    driver = StreamDriver(
        runner=_FakeRunner(),
        publish=lambda t, p: published.append((t, p)),
        vad_config=cfg,
        detector_factory=lambda: _AmplitudeVad(),
    )
    driver.run(_OverflowSource(chunks, overflow_after=2))
    # 溢出前 chunk 0/1 各发一条 partial，chunk 2 起停 → 恰 2 条
    assert len(_partials(published)) == 2
    # final 链路不受安全阀影响：flush 仍出最终段
    assert any(t == ASR_FINAL_CH1 for t, _ in published)


def test_startup_overflow_within_grace_keeps_partials():
    """开录第一下的启动毛刺溢出（PortAudio stream.start）落在宽限期内 → 不焊死 partial。"""
    cfg = _vad_cfg(partial_every_chunks=1, partial_grace_chunks=8)
    chunks = [_chunk(i, i * 8000, [_speech(8000)]) for i in range(6)]
    published: list[tuple[str, object]] = []
    driver = StreamDriver(
        runner=_FakeRunner(),
        publish=lambda t, p: published.append((t, p)),
        vad_config=cfg,
        detector_factory=lambda: _AmplitudeVad(),
    )
    driver.run(_OverflowSource(chunks, overflow_after=0))  # 第一下就溢出（启动毛刺）
    assert driver._partials_enabled is True        # 宽限吞掉毛刺，没被永久关
    assert len(_partials(published)) >= 1           # partial 照常发


def test_partials_rearm_after_stable_period():
    """跳闸后采集平稳够久 → 重新启用 partial（不再单次溢出永久焊死）。"""
    cfg = _vad_cfg(partial_every_chunks=1, partial_grace_chunks=0, partial_rearm_chunks=3)
    chunks = [_chunk(i, i * 8000, [_speech(8000)]) for i in range(8)]
    published: list[tuple[str, object]] = []
    driver = StreamDriver(
        runner=_FakeRunner(),
        publish=lambda t, p: published.append((t, p)),
        vad_config=cfg,
        detector_factory=lambda: _AmplitudeVad(),
    )
    # chunk1 溢出一次后不再涨；grace=0 当场跳闸，之后 3 个无溢出 chunk 应重启
    driver.run(_OverflowSource(chunks, overflow_after=1))
    assert driver._partials_enabled is True         # 已重启
    assert len(_partials(published)) >= 2            # 跳闸前 + 重启后都发过


def _chunkify(audio: np.ndarray, size: int) -> list:
    chunks, pos, seq = [], 0, 0
    while pos < len(audio):
        block = audio[pos : pos + size]
        chunks.append(_chunk(seq, pos, [block]))
        pos += len(block)
        seq += 1
    return chunks


def test_partials_do_not_perturb_authoritative_path():
    """C1/C2 数据分离：partial 开/关，audio_sink 的 PCM 序列 + 落库 final 段逐字节一致。

    注意：假源不丢帧，本测只证『partial 逻辑不碰权威逻辑』，不证实时饿死路径
    （那条由 overflow 安全阀 + 设备 overflow_count + 真机 [手动测试] 覆盖）。
    """
    audio = np.concatenate([
        _silence(4000), _speech(8000), _silence(8000),  # turn 1
        _speech(8000), _silence(8000),                  # turn 2
    ])

    def _run(partial_every: int):
        sink_calls: list[tuple[bytes, int]] = []
        published: list[tuple[str, object]] = []
        driver = StreamDriver(
            runner=_FakeRunner(),
            publish=lambda t, p: published.append((t, p)),
            vad_config=_vad_cfg(partial_every_chunks=partial_every),
            detector_factory=lambda: _AmplitudeVad(),
            audio_sink=lambda pcm, sf: sink_calls.append((pcm.tobytes(), sf)),
        )
        driver.run(_FakeSource(_chunkify(audio, 800)))
        finals = [
            (t, p.text, p.start_frame, p.end_frame)
            for t, p in published
            if t in (ASR_FINAL_CH1, ASR_FINAL_CH2)
        ]
        return sink_calls, finals

    sink_on, finals_on = _run(partial_every=1)   # partial 开
    sink_off, finals_off = _run(partial_every=0)  # partial 关

    assert sink_on == sink_off        # 喂 buffer 的 PCM 逐字节一致 → pyannote 输入不变（C1）
    assert finals_on == finals_off    # 落库 final 段不变 → 切分时间轴不变（C2）
    assert len(finals_off) == 2       # 两个 turn 都出 final（基线完整）


class _SlowMeterRunner:
    """转录每调一次按 cost 推进共享时钟，模拟转录占用采集线程的“墙钟”。"""

    def __init__(self, meter: dict, cost: int, text: str = "转录文本") -> None:
        self._meter = meter
        self._cost = cost
        self._text = text

    def transcribe_pcm(self, pcm: np.ndarray, **kw: object) -> str:
        self._meter["n"] += self._cost
        return self._text


class _PacedLossySource:
    """忠实建模 DeviceSource 阻塞读 + 有限缓冲：消费者越慢，缓冲越易溢出丢帧。

    每拉一个 chunk，看上个 chunk 处理期间共享时钟推进了多少（= 期间“产生”的 chunk 数），
    超过 buffer_depth 即丢帧，overflow_count += 1（与 PortAudio overflowed 同义）。
    """

    def __init__(self, chunks: list, meter: dict, buffer_depth: int) -> None:
        self._chunks = chunks
        self._meter = meter
        self._depth = buffer_depth
        self.overflow_count = 0

    def __enter__(self) -> "_PacedLossySource":
        return self

    def __exit__(self, *exc: object) -> None:
        pass

    def __iter__(self):
        last = self._meter["n"]
        for c in self._chunks:
            now = self._meter["n"]
            produced = now - last  # 上个 chunk 处理期间产生的 chunk 数
            last = now
            if produced > self._depth:
                self.overflow_count += 1
            yield c


def test_starvation_slow_partial_trips_safety_valve():
    """实时饿死路径：partial 重转过慢撑爆采集缓冲 → 溢出 → 安全阀跳闸停 partial（降级安全）。"""
    meter = {"n": 0}
    chunks = [_chunk(i, i * 8000, [_speech(8000)]) for i in range(6)]
    published: list[tuple[str, object]] = []
    driver = StreamDriver(
        runner=_SlowMeterRunner(meter, cost=5),  # 慢转录：一次 partial 即撑爆 depth=2 缓冲
        publish=lambda t, p: published.append((t, p)),
        vad_config=_vad_cfg(partial_every_chunks=1, partial_grace_chunks=0, partial_rearm_chunks=999),
        detector_factory=lambda: _AmplitudeVad(),
    )
    src = _PacedLossySource(chunks, meter, buffer_depth=2)
    driver.run(src)
    assert src.overflow_count >= 1                # 慢转录撑爆缓冲，确有丢帧
    assert driver._partials_enabled is False       # 安全阀跳闸
    assert any(t == ASR_FINAL_CH1 for t, _ in published)  # final 链路仍活（降级 final-only）


def test_fast_partial_no_overflow_valve_stays_open():
    """快 runner：不撑爆缓冲，无溢出，partial 全程不被安全阀关掉。"""
    meter = {"n": 0}
    chunks = [_chunk(i, i * 8000, [_speech(8000)]) for i in range(6)]
    published: list[tuple[str, object]] = []
    driver = StreamDriver(
        runner=_SlowMeterRunner(meter, cost=0),  # 快转录：时钟不推进，缓冲不溢出
        publish=lambda t, p: published.append((t, p)),
        vad_config=_vad_cfg(partial_every_chunks=1),
        detector_factory=lambda: _AmplitudeVad(),
    )
    src = _PacedLossySource(chunks, meter, buffer_depth=2)
    driver.run(src)
    assert src.overflow_count == 0
    assert driver._partials_enabled is True
    assert len(_partials(published)) >= 1


# ── 短词漏检修复（final 幻觉门放宽：剥标点 + 只丢单字 + 指令白名单） ──────────────


def test_short_command_words_not_dropped_as_hallucination():
    """片场高频单字指令（停/走/好/过/卡/开）不再被 len 门当幻觉丢。"""
    from backend.asr.stream_driver import _is_hallucination  # noqa: PLC0415

    for w in ("停", "走", "好", "过", "卡", "开"):
        assert _is_hallucination(w) is False, f"指令词 {w!r} 不该被丢"


def test_short_command_with_trailing_punct_not_dropped():
    """带句末标点的短指令（停。 / 走！）先剥标点再判，不被丢。"""
    from backend.asr.stream_driver import _is_hallucination  # noqa: PLC0415

    assert _is_hallucination("停。") is False
    assert _is_hallucination("走！") is False


def test_two_char_real_word_not_dropped():
    """双字真词（过来/快走）不再被 len<=2 一刀切丢。"""
    from backend.asr.stream_driver import _is_hallucination  # noqa: PLC0415

    assert _is_hallucination("过来") is False
    assert _is_hallucination("快走") is False


def test_unknown_single_char_still_dropped():
    """非指令的孤立单字（噪声幻觉）仍丢。"""
    from backend.asr.stream_driver import _is_hallucination  # noqa: PLC0415

    assert _is_hallucination("嗯") is True
    assert _is_hallucination("x") is True


def test_hallucination_patterns_still_filtered():
    """整句幻觉模式（谢谢观看 等）仍被拦。"""
    from backend.asr.stream_driver import _is_hallucination  # noqa: PLC0415

    assert _is_hallucination("谢谢观看") is True
    assert _is_hallucination("thanks for watching") is True
    assert _is_hallucination("这是一句正常的台词") is False


def test_take_end_drains_buffered_tail():
    """take.end：stop 后 source 里已缓冲的 chunk 仍被排空处理（不丢尾巴）。"""
    published: list[tuple[str, object]] = []
    sink: list[int] = []
    driver = StreamDriver(
        runner=_FakeRunner(),
        publish=lambda t, p: published.append((t, p)),
        vad_config=_vad_cfg(),
        detector_factory=lambda: _AmplitudeVad(),
        audio_sink=lambda pcm, sf: sink.append(sf),
    )
    speech = np.concatenate([_silence(2000), _speech(12000), _silence(8000)])
    chunks = _chunkify(speech, 4000)
    drain_called = {"v": False}

    class _Src:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def begin_drain(self):
            drain_called["v"] = True

        def __iter__(self):
            yield chunks[0]
            driver.stop()  # 第一块后 take.end
            yield from chunks[1:]  # 已缓冲的尾巴，不该被丢

    driver.run(_Src())
    assert drain_called["v"] is True   # begin_drain 被调（停读新 + 排空）
    assert len(sink) == len(chunks)    # 所有 chunk 的 ch1 都进 audio_sink，尾巴没丢


def test_partial_uses_configured_audio_ctx_final_does_not():
    """partial 重转用配置的 partial_audio_ctx（砍墙钟）；final 仍满窗（audio_ctx=None）。"""
    runner = _FakeRunner()
    chunks = [_chunk(i, i * 8000, [_speech(8000)]) for i in range(2)]
    driver = StreamDriver(
        runner=runner,
        publish=lambda t, p: None,
        vad_config=_vad_cfg(partial_every_chunks=1),
        detector_factory=lambda: _AmplitudeVad(),
        partial_audio_ctx=64,
    )
    driver.run(_FakeSource(chunks))
    assert 64 in runner.audio_ctx_seen     # 至少一次 partial 用了配置的 audio_ctx
    assert None in runner.audio_ctx_seen   # final（flush）不传 audio_ctx，满窗
