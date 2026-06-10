"""ChannelVADSegmenter.peek_pending() / in_speech 测试（流式 partial spec §3.1）。

peek_pending 是录制中读「在制语音段」给 partial 重转用的纯只读窗口。
头号不变量（用户硬约束 C2）：反复 peek 绝不能扰动 final 段的切分时间轴。
"""
from dataclasses import replace

import numpy as np

from backend.vad.models import VadConfig
from backend.vad.segmenter import ChannelVADSegmenter


class _AmplitudeVad:
    """帧 RMS 超阈值即判语音。确定性、零下载。"""

    def __init__(self, amp_threshold: float = 1000.0) -> None:
        self._amp = amp_threshold

    def speech_prob(self, frame: np.ndarray) -> float:
        rms = float(np.sqrt(np.mean(frame.astype(np.float64) ** 2)))
        return 1.0 if rms > self._amp else 0.0

    def reset(self) -> None:
        pass


def _silence(n: int) -> np.ndarray:
    return np.zeros(n, dtype=np.int16)


def _speech(n: int, amp: int = 8000) -> np.ndarray:
    rng = np.random.default_rng(0)
    return rng.integers(-amp, amp, size=n, endpoint=True).astype(np.int16)


def _cfg(**kw) -> VadConfig:
    base = VadConfig(
        frame_samples=512,
        threshold=0.5,
        min_silence_ms=300,
        min_speech_ms=250,
        pre_roll_ms=200,
        post_roll_ms=200,
        max_segment_ms=30000,
    )
    return replace(base, **kw)


def _seg_tuple(seg) -> tuple:
    return (seg.ch, seg.start_frame, seg.end_frame, seg.audio.tobytes())


def test_peek_pending_pure_read_preserves_segmentation():
    """C2 守门：录制中反复 peek 在制段，绝不能扰动 final 段切分（逐字节一致）。"""
    cfg = _cfg()
    part1 = np.concatenate([_silence(4000), _speech(8000)])  # 进入 SPEECH，未收尾
    part2 = _silence(8000)                                   # 静音收尾出 final

    # 对照：同样两次 push，中间不 peek
    ctrl = ChannelVADSegmenter(ch=0, detector=_AmplitudeVad(), config=cfg)
    ctrl_segs = ctrl.push(part1, 0)
    ctrl_segs += ctrl.push(part2, len(part1))
    ctrl_segs += ctrl.flush()

    # 实验：push part1 后狂 peek，再 push part2 —— peek 是唯一差异
    peeked = ChannelVADSegmenter(ch=0, detector=_AmplitudeVad(), config=cfg)
    seg1 = peeked.push(part1, 0)
    assert seg1 == []  # 还没收尾
    pend = peeked.peek_pending()
    assert pend is not None  # 正在说话，应给出在制缓冲
    audio, start_abs = pend
    assert isinstance(audio, np.ndarray)
    assert audio.dtype == np.int16
    assert len(audio) > 0
    for _ in range(5):
        peeked.peek_pending()
    exp_segs = peeked.push(part2, len(part1))
    exp_segs += peeked.flush()

    assert len(ctrl_segs) == 1
    assert [_seg_tuple(s) for s in exp_segs] == [_seg_tuple(s) for s in ctrl_segs]


def test_in_speech_reflects_state():
    """in_speech 只读属性：静音 False，进入语音 True，收尾回 False。"""
    cfg = _cfg()
    sm = ChannelVADSegmenter(ch=0, detector=_AmplitudeVad(), config=cfg)
    assert sm.in_speech is False
    sm.push(np.concatenate([_silence(2000), _speech(8000)]), 0)
    assert sm.in_speech is True
    sm.push(_silence(8000), 10000)  # 静音收尾
    assert sm.in_speech is False


def test_peek_pending_freezes_past_window_cap():
    """长句超 partial_max_window_ms：仍在 SPEECH，但 peek 返回 None（冻结），封顶单次解码量。"""
    cfg = _cfg(partial_max_window_ms=300)  # 300ms = 4800 样本
    sm = ChannelVADSegmenter(ch=0, detector=_AmplitudeVad(), config=cfg)
    # 语音跨度 ~12000 样本（750ms）> 封顶，无静音收尾 → 仍在说话但应冻结
    sm.push(np.concatenate([_silence(2000), _speech(12000)]), 0)
    assert sm.in_speech is True
    assert sm.peek_pending() is None

    # 同样音频、大封顶（默认）→ 不冻结，给出在制缓冲
    sm2 = ChannelVADSegmenter(ch=0, detector=_AmplitudeVad(), config=_cfg())
    sm2.push(np.concatenate([_silence(2000), _speech(12000)]), 0)
    assert sm2.peek_pending() is not None
