"""FunAsrOnlineRunner:600ms 凑块/余量留存/cache per-turn/增量累计/end_turn 不冲尾/设备探测。"""
import sys
import types

import numpy as np
import pytest

from backend.asr.funasr_online import BLOCK_SAMPLES, FunAsrOnlineRunner


class _FakeModel:
    """记录 generate 调用;按脚本吐增量 text。"""

    def __init__(self, pieces: list[str] | None = None) -> None:
        self.calls: list[dict] = []
        self._pieces = list(pieces or [])

    def generate(self, *, input, cache, is_final, chunk_size, encoder_chunk_look_back, decoder_chunk_look_back):
        self.calls.append({
            "n": len(input), "dtype": input.dtype, "max": float(np.abs(input).max(initial=0.0)),
            "cache_id": id(cache), "is_final": is_final,
        })
        return [{"text": self._pieces.pop(0) if self._pieces else ""}]


def _pcm(n: int) -> np.ndarray:
    return np.full(n, 16384, dtype=np.int16)


def test_block_boundary_9599_no_call():
    m = _FakeModel()
    r = FunAsrOnlineRunner(model=m)
    r.start_turn()
    assert r.feed(_pcm(BLOCK_SAMPLES - 1)) is None
    assert m.calls == []


def test_block_boundary_9600_one_call():
    m = _FakeModel(["你好"])
    r = FunAsrOnlineRunner(model=m)
    r.start_turn()
    assert r.feed(_pcm(BLOCK_SAMPLES)) == "你好"
    assert len(m.calls) == 1
    assert m.calls[0]["n"] == BLOCK_SAMPLES


def test_leftover_carries_over():
    """9601 样本 → 一次推理 + 1 样本余量;再喂 9599 凑满第二块。"""
    m = _FakeModel(["a", "b"])
    r = FunAsrOnlineRunner(model=m)
    r.start_turn()
    r.feed(_pcm(BLOCK_SAMPLES + 1))
    assert len(m.calls) == 1
    r.feed(_pcm(BLOCK_SAMPLES - 1))
    assert len(m.calls) == 2


def test_accumulates_pieces_returns_full_text():
    m = _FakeModel(["你 好", "世 界"])
    r = FunAsrOnlineRunner(model=m)
    r.start_turn()
    assert r.feed(_pcm(BLOCK_SAMPLES)) == "你好"      # normalize 去 CJK 字间空格
    assert r.feed(_pcm(BLOCK_SAMPLES)) == "你好世界"  # 累计全文,非增量


def test_empty_piece_returns_none():
    m = _FakeModel(["", "好"])
    r = FunAsrOnlineRunner(model=m)
    r.start_turn()
    assert r.feed(_pcm(BLOCK_SAMPLES)) is None       # 静音块:无新文本不发
    assert r.feed(_pcm(BLOCK_SAMPLES)) == "好"


def test_cache_same_dict_within_turn_new_after_start_turn():
    m = _FakeModel(["a", "b", "c"])
    r = FunAsrOnlineRunner(model=m)
    r.start_turn()
    r.feed(_pcm(BLOCK_SAMPLES))
    r.feed(_pcm(BLOCK_SAMPLES))
    assert m.calls[0]["cache_id"] == m.calls[1]["cache_id"]  # turn 内同一 dict 维持状态
    r.start_turn()
    r.feed(_pcm(BLOCK_SAMPLES))
    assert m.calls[2]["cache_id"] != m.calls[0]["cache_id"]  # 新 turn 新 cache


def test_feed_never_passes_is_final_and_end_turn_resets():
    m = _FakeModel(["a"])
    r = FunAsrOnlineRunner(model=m)
    r.start_turn()
    r.feed(_pcm(BLOCK_SAMPLES))
    r.feed(_pcm(100))          # 余量留在缓冲
    r.end_turn()               # 丢弃,不冲尾
    assert all(c["is_final"] is False for c in m.calls)
    assert len(m.calls) == 1   # end_turn 没有触发额外推理
    r.start_turn()
    assert r.feed(_pcm(BLOCK_SAMPLES - 1)) is None  # 旧余量已清,9599 不够一块
    assert len(m.calls) == 1


def test_feed_does_not_alias_caller_buffer():
    """余量缓冲不得别名调用方数组(上游可能复用采集 buffer)。"""
    m = _FakeModel(["a"])
    r = FunAsrOnlineRunner(model=m)
    r.start_turn()
    src = _pcm(100)
    r.feed(src)            # 不满一块,全部进余量缓冲
    src[:] = 0             # 上游复用/改写
    r.feed(_pcm(BLOCK_SAMPLES - 100))  # 凑满一块
    assert m.calls[0]["max"] == pytest.approx(0.5)  # 余量未被改写(16384→0.5)


def test_int16_to_f32_scaling():
    m = _FakeModel(["x"])
    r = FunAsrOnlineRunner(model=m)
    r.start_turn()
    r.feed(_pcm(BLOCK_SAMPLES))  # 16384 → 0.5
    assert m.calls[0]["dtype"] == np.float32
    assert m.calls[0]["max"] == pytest.approx(0.5)


def test_warmup_uses_throwaway_cache_and_is_final():
    """warmup 用独立即弃 cache 且 is_final=True,不污染 turn cache。"""
    m = _FakeModel(["x"])
    r = FunAsrOnlineRunner(model=m)
    r.start_turn()
    r.feed(_pcm(BLOCK_SAMPLES))  # 先建立 turn cache 基线
    r.warmup()
    assert m.calls[-1]["is_final"] is True
    assert m.calls[-1]["cache_id"] != m.calls[0]["cache_id"]  # 独立 cache
    r.feed(_pcm(BLOCK_SAMPLES))  # warmup 后 turn 仍可继续
    assert m.calls[-1]["cache_id"] == m.calls[0]["cache_id"]  # turn cache 未被替换


def test_ensure_model_passes_selected_device_to_automodel(monkeypatch):
    """流式 AutoModel 同样吃探测设备:CPU 上 RTF≈2.4 追不上实时,MPS≈0.36 才稳。"""
    captured: dict = {}

    class _RecordingAutoModel:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def generate(self, **kwargs):
            return [{"text": ""}]

    fake_funasr = types.ModuleType("funasr")
    fake_funasr.AutoModel = _RecordingAutoModel
    monkeypatch.setitem(sys.modules, "funasr", fake_funasr)
    monkeypatch.setattr("backend.asr.funasr_online.select_funasr_device", lambda: "mps")

    FunAsrOnlineRunner().warmup()  # 载入 + 600ms 零样本预热,均走 _RecordingAutoModel
    assert captured.get("device") == "mps"
    assert captured.get("model") == "paraformer-zh-streaming"
