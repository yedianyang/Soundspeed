"""GET/POST /api/v1/devices 路由测试。最小 app + 假 session + monkeypatch 枚举。"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import backend.api.routes.devices as devices_mod
from backend.api.routes.devices import router
from backend.audio.devices import InputDevice

_TOKEN = "t"
_HDR = {"Authorization": f"Bearer {_TOKEN}"}

_FAKE = [
    InputDevice(index=0, name="麦克风 A", max_input_channels=2, is_default=True),
    InputDevice(index=2, name="USB 接口", max_input_channels=8, is_default=False),
]


class _FakeSession:
    def __init__(self, device=None):
        self._device = device

    @property
    def device(self):
        return self._device

    def set_device(self, d):
        self._device = d


def _client(live_asr) -> TestClient:
    app = FastAPI()
    app.state.admin_token = _TOKEN
    app.state.live_asr = live_asr
    app.include_router(router)
    return TestClient(app)


@pytest.fixture(autouse=True)
def _patch_enum(monkeypatch):
    monkeypatch.setattr(devices_mod, "list_input_devices", lambda: _FAKE)


def test_get_devices_lists_real_inputs():
    r = _client(_FakeSession(device=None)).get("/api/v1/devices", headers=_HDR)
    assert r.status_code == 200
    body = r.json()
    assert [d["name"] for d in body["devices"]] == ["麦克风 A", "USB 接口"]
    assert body["devices"][0]["is_default"] is True
    assert body["selected"] is None


def test_get_devices_requires_auth():
    assert _client(_FakeSession()).get("/api/v1/devices").status_code == 401


def test_get_devices_reports_selected():
    r = _client(_FakeSession(device=2)).get("/api/v1/devices", headers=_HDR)
    assert r.json()["selected"] == 2


def test_select_device_sets_session():
    session = _FakeSession()
    client = _client(session)
    r = client.post("/api/v1/devices/select", json={"index": 2}, headers=_HDR)
    assert r.status_code == 200
    assert r.json()["selected"] == 2
    assert session.device == 2


def test_select_invalid_index_422():
    r = _client(_FakeSession()).post("/api/v1/devices/select", json={"index": 99}, headers=_HDR)
    assert r.status_code == 422


def test_select_without_live_asr_409():
    r = _client(None).post("/api/v1/devices/select", json={"index": 0}, headers=_HDR)
    assert r.status_code == 409
