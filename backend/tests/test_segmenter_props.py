"""segmenter 只读属性:in_speech / segment_start_frame(partial 喂料判定 + turn 键)。"""
import numpy as np

from backend.vad.models import VadConfig
from backend.vad.segmenter import ChannelVADSegmenter

_FS = 512


class _ScriptedDetector:
    """按脚本吐 speech_prob:>=0.5 算语音。"""

    def __init__(self, probs: list[float]) -> None:
        self._probs = list(probs)

    def speech_prob(self, frame) -> float:
        return self._probs.pop(0) if self._probs else 0.0

    def reset(self) -> None:
        pass


def _frames(n: int) -> np.ndarray:
    return np.ones(n * _FS, dtype=np.int16)


def _cfg() -> VadConfig:
    return VadConfig(min_silence_ms=64, min_speech_ms=32, pre_roll_ms=0, post_roll_ms=0)


def test_silence_state_props():
    sm = ChannelVADSegmenter(ch=0, detector=_ScriptedDetector([0.0, 0.0]), config=_cfg())
    sm.push(_frames(2), 0)
    assert sm.in_speech is False
    assert sm.segment_start_frame is None


def test_speech_state_exposes_seg_start():
    sm = ChannelVADSegmenter(ch=0, detector=_ScriptedDetector([0.0, 0.9, 0.9]), config=_cfg())
    sm.push(_frames(3), 0)
    assert sm.in_speech is True
    # pre_roll=0 → 语音起音对齐 frame 1 的 abs_start=512
    assert sm.segment_start_frame == _FS


def test_props_match_emitted_final_key():
    """承重断言:语音态读到的 segment_start_frame == 后续 final 的 seg.start_frame。"""
    probs = [0.9, 0.9, 0.9] + [0.0] * 4  # 3 帧语音 + 静音收尾(min_silence=64ms→2帧)
    sm = ChannelVADSegmenter(ch=0, detector=_ScriptedDetector(probs), config=_cfg())
    sm.push(_frames(2), 0)
    key = sm.segment_start_frame
    assert key is not None
    segs = sm.push(_frames(5), 2 * _FS)
    assert len(segs) == 1
    assert segs[0].start_frame == key


def test_props_are_pure_reads():
    """连读属性不改内部状态:读 N 次后吐出的 final 与从不读的对照完全一致。"""
    probs = [0.9, 0.9, 0.9] + [0.0] * 4

    def run(touch: bool):
        sm = ChannelVADSegmenter(ch=0, detector=_ScriptedDetector(list(probs)), config=_cfg())
        out = []
        for k in range(7):
            if touch:
                _ = sm.in_speech, sm.segment_start_frame
            out += sm.push(_frames(1), k * _FS)
            if touch:
                _ = sm.in_speech, sm.segment_start_frame
        return [(s.start_frame, s.end_frame, s.audio.tobytes()) for s in out]

    assert run(touch=True) == run(touch=False)
