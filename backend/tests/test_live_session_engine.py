"""LiveAsrSession 引擎切换:默认 whisper、set_engine 切换/校验/录制中拒绝。

2pass 流式(spec 2026-06-11-funasr-2pass-streaming):funasr 切换顺带 online warmup、
软降级 final-only(R6)、env 杀开关零构造、跨切换自愈、streaming 观测属性。
"""
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


class _FakeOnline:
    def __init__(self, fail_warmup=False):
        self.fail_warmup = fail_warmup
        self.warmups = 0

    def warmup(self):
        self.warmups += 1
        if self.fail_warmup:
            raise RuntimeError("download failed")


class _FakeSource:
    """最小 AudioSource:空迭代 + 上下文协议(start() 起的线程立即收束)。"""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __iter__(self):
        return iter(())


def _session(funasr_factory=None, online_factory=None, funasr_partials=True, source_factory=None):
    # online_factory 默认注入假实例:venv 装了 funasr,默认 FunAsrOnlineRunner 会真下载模型
    return LiveAsrSession(
        runner=_FakeRunner(),
        publish=lambda topic, payload: None,
        source_factory=source_factory or (lambda device: iter(())),
        vad_config=VadConfig(),
        detector_factory=lambda: None,
        funasr_runner_factory=funasr_factory,
        funasr_online_factory=online_factory or _FakeOnline,
        funasr_partials=funasr_partials,
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


def test_set_engine_funasr_warmup_failure_leaves_engine_unchanged():
    class _FailingRunner(_FakeRunner):
        def warmup(self):
            raise RuntimeError("FunASR 未安装")

    fail = _FailingRunner(model_size="paraformer-zh")
    runners = iter([fail])
    s = _session(funasr_factory=lambda: next(runners))
    with pytest.raises(RuntimeError):
        s.set_engine("funasr")
    assert s.engine == "whisper"
    assert s.model_size == "fake-whisper"


def test_set_engine_rejected_while_switch_in_progress():
    s = _session(funasr_factory=lambda: _FakeRunner(model_size="paraformer-zh"))
    assert s._switch_lock.acquire(blocking=False)
    try:
        with pytest.raises(RuntimeError):
            s.set_engine("funasr")
    finally:
        s._switch_lock.release()
    s.set_engine("funasr")  # 释放后可正常切换
    assert s.engine == "funasr"


# ---- 2pass 流式:online warmup / 软降级 / env 杀开关 / streaming 属性 ----


def test_funasr_switch_constructs_online_and_streams():
    online = _FakeOnline()
    s = _session(
        funasr_factory=lambda: _FakeRunner(model_size="paraformer-zh"),
        online_factory=lambda: online,
    )
    s.set_engine("funasr")
    assert online.warmups == 1
    assert s.streaming is True


def test_online_warmup_failure_soft_degrades_to_final_only():
    s = _session(
        funasr_factory=lambda: _FakeRunner(model_size="paraformer-zh"),
        online_factory=lambda: _FakeOnline(fail_warmup=True),
    )
    s.set_engine("funasr")  # 不抛:online 失败只杀 partial,切换照常成功(R6)
    assert s.engine == "funasr"
    assert s.streaming is False


def test_partials_env_off_never_constructs_online():
    built = []

    def factory():
        built.append(1)
        return _FakeOnline()

    s = _session(
        funasr_factory=lambda: _FakeRunner(model_size="paraformer-zh"),
        online_factory=factory,
        funasr_partials=False,
    )
    s.set_engine("funasr")
    assert built == []  # 杀开关=成本开关:零构造/零下载(spec §3 Q3)
    assert s.streaming is False


def test_online_warmup_retries_across_engine_switches():
    online = _FakeOnline(fail_warmup=True)
    s = _session(
        funasr_factory=lambda: _FakeRunner(model_size="paraformer-zh"),
        online_factory=lambda: online,
    )
    s.set_engine("funasr")
    assert s.streaming is False  # 首切软降级
    online.fail_warmup = False  # 瞬态网络故障恢复
    s.set_engine("whisper")
    s.set_engine("funasr")
    assert online.warmups == 2  # 每次切到 funasr 重新评估并重试 warmup
    assert s.streaming is True  # 跨切换自愈(spec §3 Q4)


def test_whisper_engine_never_streams():
    assert _session().streaming is False


def test_soft_degraded_start_injects_no_partial_runner():
    s = _session(
        funasr_factory=lambda: _FakeRunner(model_size="paraformer-zh"),
        online_factory=lambda: _FakeOnline(fail_warmup=True),
        source_factory=lambda device: _FakeSource(),
    )
    s.set_engine("funasr")
    s.start()
    try:
        # 软降级 take:driver 无 partial_runner → partial 代码一行不跑,零 asr.partial.* publish
        assert s._driver._partial_runner is None
    finally:
        s.stop()


def test_streaming_start_injects_online_partial_runner():
    online = _FakeOnline()
    s = _session(
        funasr_factory=lambda: _FakeRunner(model_size="paraformer-zh"),
        online_factory=lambda: online,
        source_factory=lambda device: _FakeSource(),
    )
    s.set_engine("funasr")
    s.start()
    try:
        assert s._driver._partial_runner is online
    finally:
        s.stop()


def test_whisper_start_injects_no_partial_runner():
    s = _session(source_factory=lambda device: _FakeSource())
    s.start()
    try:
        assert s._driver._partial_runner is None
    finally:
        s.stop()


def test_start_wraps_source_only_when_streaming(monkeypatch):
    """start() 的 wrap_buffered 接线:streaming 会话包 BufferedAudioSource,whisper 不包。"""
    import backend.audio.buffered_source as bs

    wrapped = []

    class _SpyBuffered(bs.BufferedAudioSource):
        def __init__(self, inner):
            wrapped.append(inner)
            super().__init__(inner)

    # _run_safe 调用时才解析模块属性,monkeypatch 模块属性即可拦到
    monkeypatch.setattr(bs, "BufferedAudioSource", _SpyBuffered)

    s = _session(
        funasr_factory=lambda: _FakeRunner(model_size="paraformer-zh"),
        source_factory=lambda device: _FakeSource(),
    )
    s.set_engine("funasr")
    s.start()
    s.stop()  # join 线程,_run_safe 已完整执行
    assert len(wrapped) == 1  # streaming 会话包了

    s2 = _session(source_factory=lambda device: _FakeSource())
    s2.start()
    s2.stop()
    assert len(wrapped) == 1  # whisper 会话没包(红线 R3)


class _RecordingDriver:
    def __init__(self):
        self.source = None

    def run(self, source):
        self.source = source


def test_run_safe_wraps_buffered_only_when_partials_active():
    from backend.audio.buffered_source import BufferedAudioSource

    raw = _FakeSource()
    s = _session(source_factory=lambda device: raw)

    wrapped = _RecordingDriver()
    s._run_safe(wrapped, wrap_buffered=True)
    assert isinstance(wrapped.source, BufferedAudioSource)

    plain = _RecordingDriver()
    s._run_safe(plain, wrap_buffered=False)
    assert plain.source is raw  # whisper 默认路径永不包 BufferedAudioSource(红线 R3)


def test_start_passes_engine_to_driver(monkeypatch):
    """start() 把当前引擎透传给 StreamDriver。

    funasr 引擎下 driver 必须收到 engine='funasr'，否则退回默认 whisper、trust_asr 恒 False，
    paraformer 信任去门在生产路径直接失效（engine-aware 信任开关的命门）。
    """
    import backend.asr.live_session as ls

    session = _session(funasr_factory=_FakeRunner, funasr_partials=False)
    session.set_engine("funasr")

    captured: dict = {}

    def _fake_driver(*args, **kwargs):
        captured.update(kwargs)

        class _D:
            def run(self, *a):
                pass

            def stop(self):
                pass

        return _D()

    monkeypatch.setattr(ls, "StreamDriver", _fake_driver)
    monkeypatch.setattr(session, "_run_safe", lambda *a, **k: None)
    session.start()

    assert captured.get("engine") == "funasr"
