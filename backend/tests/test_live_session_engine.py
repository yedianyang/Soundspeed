"""LiveAsrSession 引擎切换:默认 whisper、set_engine 切换/校验/录制中拒绝。"""
import pytest

from backend.asr.live_session import LiveAsrSession
from backend.vad.models import VadConfig


class _FakeRunner:
    def __init__(self, model_size="fake-whisper"):
        self._lang = "zh"
        self._model = model_size
        self.warmed = False

    @property
    def language(self):
        return self._lang

    @property
    def model_size(self):
        return self._model

    def set_language(self, lang):
        self._lang = lang

    def warmup(self):
        self.warmed = True


def _session(funasr_factory=None):
    return LiveAsrSession(
        runner=_FakeRunner(),
        publish=lambda topic, payload: None,
        source_factory=lambda device: iter(()),
        vad_config=VadConfig(),
        detector_factory=lambda: None,
        funasr_runner_factory=funasr_factory,
    )


def test_default_engine_is_whisper():
    assert _session().engine == "whisper"


def test_set_engine_funasr_constructs_warms_and_switches():
    fake_funasr = _FakeRunner(model_size="paraformer-zh")
    s = _session(funasr_factory=lambda: fake_funasr)
    s.set_engine("funasr")
    assert s.engine == "funasr"
    assert fake_funasr.warmed is True
    assert s.model_size == "paraformer-zh"


def test_set_engine_back_to_whisper_reuses_original_runner():
    s = _session(funasr_factory=lambda: _FakeRunner(model_size="paraformer-zh"))
    s.set_engine("funasr")
    s.set_engine("whisper")
    assert s.engine == "whisper"
    assert s.model_size == "fake-whisper"


def test_set_engine_same_engine_is_noop():
    s = _session()
    s.set_engine("whisper")  # 不抛、不变
    assert s.engine == "whisper"


def test_set_engine_unknown_raises_value_error():
    with pytest.raises(ValueError):
        _session().set_engine("kaldi")


def test_funasr_runner_constructed_once_across_switches():
    built = []

    def factory():
        r = _FakeRunner(model_size="paraformer-zh")
        built.append(r)
        return r

    s = _session(funasr_factory=factory)
    s.set_engine("funasr")
    s.set_engine("whisper")
    s.set_engine("funasr")
    assert len(built) == 1


def test_set_engine_rejected_while_running(monkeypatch):
    s = _session(funasr_factory=lambda: _FakeRunner())
    monkeypatch.setattr(type(s), "running", property(lambda self: True))
    with pytest.raises(RuntimeError):
        s.set_engine("funasr")
