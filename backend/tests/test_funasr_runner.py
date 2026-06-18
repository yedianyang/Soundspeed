"""FunAsrRunner 契约测试:空格归一、int16→float32、懒 import、未安装报错、设备探测。"""
import sys
import types

import numpy as np
import pytest

from backend.asr.funasr_runner import (
    FunAsrNotInstalled,
    FunAsrRunner,
    normalize_funasr_text,
    select_funasr_device,
)


# ── normalize_funasr_text:去 CJK 字间空格,保留英文词间空格 ──

def test_normalize_removes_cjk_gaps():
    assert normalize_funasr_text("你 好 世 界") == "你好世界"


def test_normalize_keeps_english_word_gaps():
    assert normalize_funasr_text("我 用 iPhone 打 电话") == "我用 iPhone 打电话"


def test_normalize_cjk_punctuation_joins():
    assert normalize_funasr_text("你 好 ， 世 界") == "你好，世界"


def test_normalize_pure_english_unchanged():
    assert normalize_funasr_text("hello world") == "hello world"


def test_normalize_strips_outer_whitespace():
    assert normalize_funasr_text("  你 好  ") == "你好"


# ── FunAsrRunner:注入假 AutoModel(同 WhisperRunner 注入范式) ──

class _FakeAutoModel:
    def __init__(self):
        self.calls = []

    def generate(self, input):  # noqa: A002 - funasr 的真实参数名就叫 input
        self.calls.append(input)
        return [{"text": "你 好 世 界"}]


def test_transcribe_pcm_returns_normalized_text():
    fake = _FakeAutoModel()
    runner = FunAsrRunner(model=fake)
    out = runner.transcribe_pcm(np.zeros(1600, dtype=np.int16))
    assert out == "你好世界"


def test_transcribe_pcm_feeds_float32_unit_scale():
    fake = _FakeAutoModel()
    runner = FunAsrRunner(model=fake)
    pcm = np.full(160, 16384, dtype=np.int16)  # 半满幅
    runner.transcribe_pcm(pcm)
    fed = fake.calls[0]
    assert fed.dtype == np.float32
    assert abs(float(fed[0]) - 0.5) < 1e-6


def test_transcribe_pcm_empty_result_returns_empty_string():
    class _Empty:
        def generate(self, input):  # noqa: A002
            return []

    assert FunAsrRunner(model=_Empty()).transcribe_pcm(np.zeros(160, dtype=np.int16)) == ""


def test_language_fixed_zh_and_model_size():
    runner = FunAsrRunner(model=_FakeAutoModel())
    assert runner.language == "zh"
    assert runner.model_size == "paraformer-zh"
    runner.set_language("en")  # 仅 zh:忽略 + 告警,不抛错
    assert runner.language == "zh"


def test_missing_funasr_raises_not_installed(monkeypatch):
    monkeypatch.setitem(sys.modules, "funasr", None)  # import funasr → ImportError
    runner = FunAsrRunner()
    with pytest.raises(FunAsrNotInstalled):
        runner.warmup()


# ── select_funasr_device:Apple Silicon 上用 MPS(实测 ~10x CPU 且输出逐字一致),否则 CPU ──

def _fake_torch(mps_available: bool):
    return types.SimpleNamespace(
        backends=types.SimpleNamespace(
            mps=types.SimpleNamespace(is_available=lambda: mps_available)
        )
    )


def test_select_device_mps_when_available(monkeypatch):
    monkeypatch.setitem(sys.modules, "torch", _fake_torch(True))
    assert select_funasr_device() == "mps"


def test_select_device_cpu_when_mps_unavailable(monkeypatch):
    monkeypatch.setitem(sys.modules, "torch", _fake_torch(False))
    assert select_funasr_device() == "cpu"


def test_select_device_cpu_when_torch_missing(monkeypatch):
    monkeypatch.setitem(sys.modules, "torch", None)  # import torch → ImportError
    assert select_funasr_device() == "cpu"


# ── _ensure_model:把探测到的设备透传给 AutoModel(CPU 慢→MPS 快的命门) ──

def test_ensure_model_passes_selected_device_to_automodel(monkeypatch):
    captured: dict = {}

    class _RecordingAutoModel:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    fake_funasr = types.ModuleType("funasr")
    fake_funasr.AutoModel = _RecordingAutoModel
    monkeypatch.setitem(sys.modules, "funasr", fake_funasr)
    monkeypatch.setattr("backend.asr.funasr_runner.select_funasr_device", lambda: "mps")

    FunAsrRunner().warmup()
    assert captured.get("device") == "mps"
    assert captured.get("model") == "paraformer-zh"
