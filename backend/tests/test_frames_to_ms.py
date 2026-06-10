"""frames_to_ms:16k 绝对帧 → 毫秒的唯一换算点(partial/final 同键的承重墙)。"""
from backend.vad.models import frames_to_ms


def test_basic_conversion():
    assert frames_to_ms(0) == 0
    assert frames_to_ms(16) == 1
    assert frames_to_ms(16000) == 1000


def test_banker_rounding_boundaries():
    """Python round() 在 .5 上四舍六入五成双 —— 与原 stream_driver 行为逐位一致的规格钉点。"""
    assert frames_to_ms(8) == 0    # 8/16=0.5 → round-to-even → 0
    assert frames_to_ms(24) == 2   # 24/16=1.5 → round-to-even → 2
    assert frames_to_ms(40) == 2   # 40/16=2.5 → round-to-even → 2
    assert frames_to_ms(16 * 123 + 8) == 124  # 123.5 → 124(成双)
