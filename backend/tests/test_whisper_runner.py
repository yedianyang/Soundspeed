"""WhisperRunner 测试：int16 16k PCM → 文字。

用假转录器（_FakeModel）测封装逻辑（int16→float32 转换、语言透传、文本拼接），
真实 pywhispercpp 模型留 smoke / [手动测试]，不在此覆盖。
"""
import numpy as np

from backend.asr.config import ASRConfig
from backend.asr.whisper_runner import WhisperRunner


class _FakeSeg:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeModel:
    """记录 transcribe 调用，返回预置 segment。"""

    def __init__(self, segs: list[str] | None = None) -> None:
        self.calls: list[tuple[np.ndarray, str]] = []
        self.kwargs: list[dict[str, object]] = []  # 每次 transcribe 收到的调参 kwargs
        self._segs = segs if segs is not None else ["你好", "世界"]

    def transcribe(self, audio: np.ndarray, language: str = "", **kw: object) -> list[_FakeSeg]:
        self.calls.append((audio, language))
        self.kwargs.append(dict(kw))
        return [_FakeSeg(t) for t in self._segs]


def _pcm(n: int = 16000, amp: int = 1000) -> np.ndarray:
    return (np.ones(n) * amp).astype(np.int16)


def test_transcribe_pcm_joins_segments():
    runner = WhisperRunner(ASRConfig(language="zh"), model=_FakeModel(["你好", "世界"]))
    assert runner.transcribe_pcm(_pcm()) == "你好世界"


def test_int16_converted_to_float32_in_range():
    model = _FakeModel()
    runner = WhisperRunner(ASRConfig(), model=model)
    # 极值 int16，验证转换后落在 [-1, 1] 且为 float32
    pcm = np.array([32767, -32768, 0], dtype=np.int16)
    runner.transcribe_pcm(pcm)
    seen = model.calls[0][0]
    assert seen.dtype == np.float32
    assert seen.max() <= 1.0
    assert seen.min() >= -1.0


def test_language_passed_from_config():
    model = _FakeModel()
    runner = WhisperRunner(ASRConfig(language="zh"), model=model)
    runner.transcribe_pcm(_pcm())
    assert model.calls[0][1] == "zh"


def test_empty_segments_returns_empty_string():
    runner = WhisperRunner(ASRConfig(), model=_FakeModel([]))
    assert runner.transcribe_pcm(_pcm()) == ""


def test_outer_whitespace_stripped():
    runner = WhisperRunner(ASRConfig(), model=_FakeModel([" 你好世界 "]))
    assert runner.transcribe_pcm(_pcm()) == "你好世界"


def test_set_language_overrides_config():
    model = _FakeModel()
    runner = WhisperRunner(ASRConfig(language="zh"), model=model)
    runner.set_language("en")
    assert runner.language == "en"
    runner.transcribe_pcm(_pcm())
    assert model.calls[0][1] == "en"  # 用切换后的语言转录


def test_model_size_property():
    runner = WhisperRunner(ASRConfig(model_size="medium"), model=_FakeModel())
    assert runner.model_size == "medium"


def test_injected_model_reused_not_rebuilt():
    model = _FakeModel()
    runner = WhisperRunner(ASRConfig(), model=model)
    runner.transcribe_pcm(_pcm())
    runner.transcribe_pcm(_pcm())
    assert len(model.calls) == 2  # 同一注入实例被复用


# --- 调参透传（whisper 调参第一期：参数可配 + 精度优先默认） ---


def test_default_config_uses_beam_search_not_greedy():
    # 当前 runner 不传任何参数 → pywhispercpp 库默认 beam_size=-1（贪心）。
    # 场记工具精度优先，默认应回到 whisper.cpp CLI 标准 beam_size=5（束搜索）。
    cfg = ASRConfig()
    assert cfg.beam_size == 5


def test_tuning_params_passed_to_model():
    model = _FakeModel()
    cfg = ASRConfig(
        beam_size=8,
        temperature=0.0,
        temperature_inc=0.2,
        entropy_thold=2.4,
        logprob_thold=-1.0,
        no_speech_thold=0.6,
    )
    runner = WhisperRunner(cfg, model=model)
    runner.transcribe_pcm(_pcm())
    kw = model.kwargs[0]
    # beam_search 是嵌套 dict（pywhispercpp 1.5.0 形状），不是裸 int
    assert kw["beam_search"] == {"beam_size": 8, "patience": -1.0}
    assert kw["temperature"] == 0.0
    assert kw["temperature_inc"] == 0.2  # 保留 temperature 回退序列
    assert kw["entropy_thold"] == 2.4
    assert kw["logprob_thold"] == -1.0
    assert kw["no_speech_thold"] == 0.6


def test_initial_prompt_passed_when_set():
    model = _FakeModel()
    runner = WhisperRunner(ASRConfig(initial_prompt="以下是普通话的句子。"), model=model)
    runner.transcribe_pcm(_pcm())
    assert model.kwargs[0]["initial_prompt"] == "以下是普通话的句子。"


def test_initial_prompt_omitted_when_none():
    model = _FakeModel()
    runner = WhisperRunner(ASRConfig(initial_prompt=None), model=model)
    runner.transcribe_pcm(_pcm())
    # 不传 initial_prompt（默认 None），避免无谓地偏置解码
    assert "initial_prompt" not in model.kwargs[0]


def test_internal_vad_never_enabled():
    # 上游已有 backend/vad/ 切段，whisper 内置 vad 必须保持关闭，否则双重 VAD 咬字。
    model = _FakeModel()
    runner = WhisperRunner(ASRConfig(), model=model)
    runner.transcribe_pcm(_pcm())
    assert model.kwargs[0].get("vad", False) is False


def test_all_param_keys_valid_against_pywhispercpp_schema():
    # 防 transcribe(**params) 静默吞掉打错的 key：每个 key 必须是 pywhispercpp 已知参数。
    from backend.asr.whisper_runner import build_transcribe_params

    import pywhispercpp.constants as c

    params = build_transcribe_params(ASRConfig(initial_prompt="x"))
    known = set(c.PARAMS_SCHEMA.keys())
    unknown = set(params) - known
    assert unknown == set(), f"未知参数会被静默吞掉: {unknown}"


def test_build_params_rejects_unknown_key():
    from backend.asr.whisper_runner import validate_param_keys

    import pytest

    with pytest.raises(ValueError, match="未知"):
        validate_param_keys({"beam_search": {}, "bogus_typo": 1})
