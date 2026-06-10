"""GET/POST /api/v1/asr 路由测试。最小 app + 假 session。"""
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.routes.asr import router

_TOKEN = "t"
_HDR = {"Authorization": f"Bearer {_TOKEN}"}


class _FakeSession:
    def __init__(self, language="zh", model_size="base", engine="whisper", running=False):
        self._lang = language
        self._model = model_size
        self._engine = engine
        self.running = running
        self.engine_errors: dict[str, Exception] = {}  # engine → 抛出的异常(测试注入)

    @property
    def language(self):
        return self._lang

    @property
    def model_size(self):
        return self._model

    @property
    def engine(self):
        return self._engine

    def set_language(self, lang):
        self._lang = lang

    def set_engine(self, engine):
        if engine in self.engine_errors:
            raise self.engine_errors[engine]
        if self.running:
            raise RuntimeError("录制中不可切换引擎")
        self._engine = engine


def _client(live_asr) -> TestClient:
    app = FastAPI()
    app.state.admin_token = _TOKEN
    app.state.live_asr = live_asr
    app.include_router(router)
    return TestClient(app)


def test_get_asr_reports_language_and_model():
    r = _client(_FakeSession(language="zh", model_size="medium")).get("/api/v1/asr", headers=_HDR)
    assert r.status_code == 200
    body = r.json()
    assert body["enabled"] is True
    assert body["language"] == "zh"
    assert body["model"] == "medium"
    assert "zh" in body["languages"]


def test_get_asr_disabled_when_no_session():
    body = _client(None).get("/api/v1/asr", headers=_HDR).json()
    assert body["enabled"] is False
    assert body["language"] is None


def test_get_asr_requires_auth():
    assert _client(_FakeSession()).get("/api/v1/asr").status_code == 401


def test_set_language_updates_session():
    session = _FakeSession(language="zh")
    r = _client(session).post("/api/v1/asr/language", json={"language": "en"}, headers=_HDR)
    assert r.status_code == 200
    assert r.json()["language"] == "en"
    assert session.language == "en"


def test_set_language_empty_422():
    r = _client(_FakeSession()).post("/api/v1/asr/language", json={"language": "  "}, headers=_HDR)
    assert r.status_code == 422


def test_set_language_without_live_asr_409():
    r = _client(None).post("/api/v1/asr/language", json={"language": "en"}, headers=_HDR)
    assert r.status_code == 409


def test_get_asr_includes_engine_and_engines_list():
    body = _client(_FakeSession(engine="whisper")).get("/api/v1/asr", headers=_HDR).json()
    assert body["engine"] == "whisper"
    ids = {e["id"]: e for e in body["engines"]}
    assert ids["whisper"]["languages"] == ["zh", "en", "auto"]
    assert ids["funasr"]["languages"] == ["zh"]
    assert isinstance(ids["funasr"]["installed"], bool)


def test_get_asr_disabled_still_lists_engines():
    body = _client(None).get("/api/v1/asr", headers=_HDR).json()
    assert body["engine"] is None
    assert {e["id"] for e in body["engines"]} == {"whisper", "funasr"}


def test_set_engine_switches_and_returns_language():
    session = _FakeSession(engine="whisper")
    r = _client(session).post("/api/v1/asr/engine", json={"engine": "funasr"}, headers=_HDR)
    assert r.status_code == 200
    assert r.json()["engine"] == "funasr"
    assert "language" in r.json()
    assert session.engine == "funasr"


def test_set_engine_409_when_no_session():
    r = _client(None).post("/api/v1/asr/engine", json={"engine": "funasr"}, headers=_HDR)
    assert r.status_code == 409


def test_set_engine_409_while_recording():
    session = _FakeSession(running=True)
    r = _client(session).post("/api/v1/asr/engine", json={"engine": "funasr"}, headers=_HDR)
    assert r.status_code == 409
    assert session.engine == "whisper"


def test_set_engine_409_when_funasr_not_installed():
    from backend.asr.funasr_runner import FunAsrNotInstalled

    session = _FakeSession()
    session.engine_errors["funasr"] = FunAsrNotInstalled("FunASR 未安装")
    r = _client(session).post("/api/v1/asr/engine", json={"engine": "funasr"}, headers=_HDR)
    assert r.status_code == 409
    assert "FunASR 未安装" in r.json()["detail"]


def test_set_engine_422_unknown_engine():
    r = _client(_FakeSession()).post("/api/v1/asr/engine", json={"engine": "kaldi"}, headers=_HDR)
    assert r.status_code == 422


def test_set_engine_requires_auth():
    assert _client(_FakeSession()).post("/api/v1/asr/engine", json={"engine": "funasr"}).status_code == 401


def test_set_language_422_when_not_supported_by_engine():
    session = _FakeSession(engine="funasr")
    r = _client(session).post("/api/v1/asr/language", json={"language": "en"}, headers=_HDR)
    assert r.status_code == 422
