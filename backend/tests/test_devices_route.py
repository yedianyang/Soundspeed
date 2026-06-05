"""GET/POST /api/v1/devices 路由测试。最小 app + 假 session + monkeypatch 枚举。"""
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import backend.api.routes.devices as devices_mod
from backend.api.routes.devices import router
from backend.audio.devices import InputDevice
from backend.db.dal import DAL

_TOKEN = "t"
_HDR = {"Authorization": f"Bearer {_TOKEN}"}

_FAKE = [
    InputDevice(index=0, name="麦克风 A", max_input_channels=2, is_default=True),
    InputDevice(index=2, name="USB 接口", max_input_channels=8, is_default=False),
]
# 系统默认 index（与 _FAKE[0] 对应）
_DEFAULT_INDEX = 0


class _FakeSession:
    def __init__(self, device=None):
        self._device = device

    @property
    def device(self):
        return self._device

    def set_device(self, d):
        self._device = d


def _client(live_asr, dal=None) -> TestClient:
    app = FastAPI()
    app.state.admin_token = _TOKEN
    app.state.live_asr = live_asr
    # 通过 orchestrator-like 对象提供 dal（路由通过 request.app.state.orchestrator.dal 访问）
    if dal is not None:
        class _FakeOrchestrator:
            pass
        orch = _FakeOrchestrator()
        orch.dal = dal  # type: ignore[attr-defined]
        app.state.orchestrator = orch
    app.include_router(router)
    return TestClient(app)


@pytest.fixture(autouse=True)
def _patch_enum(monkeypatch):
    monkeypatch.setattr(devices_mod, "list_input_devices", lambda: _FAKE)


@pytest.fixture(autouse=True)
def _patch_default_index(monkeypatch):
    """让路由获取系统默认 index 时返回固定值（避免依赖真实 sounddevice）。"""
    monkeypatch.setattr(devices_mod, "get_default_input_index", lambda: _DEFAULT_INDEX)


# ── 原有测试（已存在行为，保持通过）──────────────────────────────────────────


def test_get_devices_lists_real_inputs():
    r = _client(_FakeSession(device=None)).get("/api/v1/devices", headers=_HDR)
    assert r.status_code == 200
    body = r.json()
    assert [d["name"] for d in body["devices"]] == ["麦克风 A", "USB 接口"]
    assert body["devices"][0]["is_default"] is True


def test_get_devices_requires_auth():
    assert _client(_FakeSession()).get("/api/v1/devices").status_code == 401


def test_select_invalid_index_422():
    r = _client(_FakeSession()).post("/api/v1/devices/select", json={"index": 99}, headers=_HDR)
    assert r.status_code == 422


def test_select_without_live_asr_409():
    r = _client(None).post("/api/v1/devices/select", json={"index": 0}, headers=_HDR)
    assert r.status_code == 409


# ── 新测试：持久化 + 新响应字段 ────────────────────────────────────────────────


def test_get_devices_selected_and_available_when_device_present(tmp_path: Path):
    """session._device 是在场的设备名 → selected=该 index, selected_available=True。"""
    dal = DAL(tmp_path / "test.db")
    session = _FakeSession(device="USB 接口")  # index=2
    r = _client(session, dal=dal).get("/api/v1/devices", headers=_HDR)
    assert r.status_code == 200
    body = r.json()
    assert body["selected"] == 2
    assert body["selected_available"] is True
    assert body["selected_name"] == "USB 接口"
    dal.close()


def test_get_devices_selected_falls_to_default_when_device_absent(tmp_path: Path):
    """session._device 是不在场的设备名 → selected=系统默认 index, selected_available=False。"""
    dal = DAL(tmp_path / "test.db")
    session = _FakeSession(device="Dead Device")
    r = _client(session, dal=dal).get("/api/v1/devices", headers=_HDR)
    assert r.status_code == 200
    body = r.json()
    assert body["selected"] == _DEFAULT_INDEX
    assert body["selected_available"] is False
    assert body["selected_name"] == "Dead Device"
    dal.close()


def test_get_devices_selected_none_when_session_none():
    """live_asr=None → selected=None, selected_available=None, selected_name=None。"""
    r = _client(None).get("/api/v1/devices", headers=_HDR)
    body = r.json()
    assert body["selected"] is None
    assert body["selected_available"] is None
    assert body["selected_name"] is None


def test_select_device_persists_name_and_sets_session(tmp_path: Path):
    """POST /devices/select index=2 → session._device 变成名字；get_setting 能读回。"""
    dal = DAL(tmp_path / "test.db")
    session = _FakeSession()
    r = _client(session, dal=dal).post(
        "/api/v1/devices/select", json={"index": 2}, headers=_HDR
    )
    assert r.status_code == 200
    assert r.json()["selected"] == 2
    # session 存名字
    assert session.device == "USB 接口"
    # 持久化
    assert dal.get_setting("audio_input_device") == "USB 接口"
    dal.close()


def test_select_device_roundtrip(tmp_path: Path):
    """POST select index=N → GET selected 仍等于 N（index→name→index 往返闭合）。"""
    dal = DAL(tmp_path / "test.db")
    session = _FakeSession()
    client = _client(session, dal=dal)

    client.post("/api/v1/devices/select", json={"index": 2}, headers=_HDR)

    r = client.get("/api/v1/devices", headers=_HDR)
    assert r.json()["selected"] == 2
    dal.close()


def test_new_session_restores_persisted_device(tmp_path: Path):
    """第一个 session 选了设备持久化；新建 session 读持久化后 GET selected 正确。"""
    dal = DAL(tmp_path / "test.db")

    # 第一个 session 选 index=2
    session1 = _FakeSession()
    _client(session1, dal=dal).post(
        "/api/v1/devices/select", json={"index": 2}, headers=_HDR
    )
    assert dal.get_setting("audio_input_device") == "USB 接口"

    # 新建 session，设备从持久化恢复
    session2 = _FakeSession(device=dal.get_setting("audio_input_device"))
    r = _client(session2, dal=dal).get("/api/v1/devices", headers=_HDR)
    assert r.json()["selected"] == 2
    dal.close()
