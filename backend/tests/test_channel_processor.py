import numpy as np

from backend.audio.channel import ChannelProcessor


def _sine(freq, rate, n):
    t = np.arange(n, dtype=np.float32) / rate
    return (0.5 * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def test_process_outputs_int16():
    proc = ChannelProcessor(in_rate=48000)
    out = proc.process(_sine(440, 48000, 9600))
    assert out.dtype == np.int16


def test_process_resamples_48k_to_16k_length():
    """48k -> 16k：累计输出帧数约为输入的三分之一。"""
    proc = ChannelProcessor(in_rate=48000)
    total_in = 0
    total_out = 0
    for _ in range(10):
        block = _sine(440, 48000, 9600)
        total_in += len(block)
        total_out += len(proc.process(block))
    ratio = total_out / total_in
    assert abs(ratio - 1 / 3) < 0.01


def test_process_passthrough_when_already_16k():
    proc = ChannelProcessor(in_rate=16000)
    total_in = 0
    total_out = 0
    for _ in range(10):
        block = _sine(440, 16000, 3200)
        total_in += len(block)
        total_out += len(proc.process(block))
    assert abs(total_out / total_in - 1.0) < 0.01


def test_process_preserves_amplitude():
    """半幅正弦重采样后仍是有信号、量级合理的 int16，不被静默清零。"""
    proc = ChannelProcessor(in_rate=48000)
    out = np.concatenate([proc.process(_sine(440, 48000, 9600)) for _ in range(5)])
    peak = int(np.max(np.abs(out)))
    assert 8000 < peak < 20000  # 半幅 ~16384 附近
