"""4.C Note API 端点测试。"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.core.orchestrator import create_orchestrator
from backend.db.dal import DAL

_TOKEN = "test-admin-token"
AUTH = {"Authorization": f"Bearer {_TOKEN}"}


def _make_client(orchestrator, monkeypatch) -> TestClient:
    monkeypatch.setenv("ADMIN_TOKEN", _TOKEN)
    app = create_app(orchestrator)
    return TestClient(app)


@pytest.fixture
def dal(tmp_path):
    """自建 DAL fixture，绕过 conftest.py 的 Python 3.8 兼容问题。"""
    d = DAL(tmp_path / "test.db")
    yield d
    d.close()


def _setup_scene_and_take(dal: DAL, scene_code: str = "3A") -> int:
    """创建 scene + take，返回 take_id。"""
    dal._conn.execute(
        "INSERT INTO scenes (scene_code, is_active) VALUES (?, 1)",
        (scene_code,),
    )
    scene_id = dal._conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    dal._conn.execute(
        "INSERT INTO takes (scene_id, take_number, start_ts, status) VALUES (?, 1, 1000.0, 'tbd')",
        (scene_id,),
    )
    dal._conn.commit()
    return dal._conn.execute("SELECT last_insert_rowid()").fetchone()[0]


# ── POST /notes ──────────────────────────────────────────────────────────────


def test_post_note_success_current_take(dal: DAL, monkeypatch) -> None:
    """POST /api/v1/notes 当前 take → 201。"""
    take_id = _setup_scene_and_take(dal)
    orch = create_orchestrator(dal)
    orch.session.take_active = True
    orch.session.take_id = take_id
    client = _make_client(orch, monkeypatch)

    resp = client.post("/api/v1/notes", json={"text": "飞机声"}, headers=AUTH)
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert "event_id" in data
    assert data["category"] == "note"
    assert data["content"] == "飞机声"


def test_post_note_success_explicit_take(dal: DAL, monkeypatch) -> None:
    """POST /api/v1/notes 显式指定 take → 201。"""
    take_id = _setup_scene_and_take(dal, scene_code="3A")
    orch = create_orchestrator(dal)
    client = _make_client(orch, monkeypatch)

    resp = client.post(
        "/api/v1/notes",
        json={"text": "3A 1 @issue 开头有飞机声"},
        headers=AUTH,
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["take_id"] == take_id
    assert data["category"] == "issue"
    assert data["content"] == "开头有飞机声"


def test_post_note_no_active_take_409(dal: DAL, monkeypatch) -> None:
    """无活跃 take → 409。"""
    orch = create_orchestrator(dal)
    orch.session.take_active = False
    orch.session.take_id = None
    client = _make_client(orch, monkeypatch)

    resp = client.post("/api/v1/notes", json={"text": "飞机声"}, headers=AUTH)
    assert resp.status_code == 409, resp.text


def test_post_note_scene_not_found_404(dal: DAL, monkeypatch) -> None:
    """scene_code 不存在 → 404。"""
    orch = create_orchestrator(dal)
    client = _make_client(orch, monkeypatch)

    resp = client.post(
        "/api/v1/notes",
        json={"text": "XX 1 飞机声"},
        headers=AUTH,
    )
    assert resp.status_code == 404, resp.text


def test_post_note_take_not_found_404(dal: DAL, monkeypatch) -> None:
    """take 不存在 → 404。"""
    # 创建 scene（scene_code="3A"），但不创建 take #99
    dal._conn.execute(
        "INSERT INTO scenes (scene_code, is_active) VALUES (?, 1)",
        ("3A",),
    )
    orch = create_orchestrator(dal)
    client = _make_client(orch, monkeypatch)

    resp = client.post(
        "/api/v1/notes",
        json={"text": "3A 99 飞机声"},
        headers=AUTH,
    )
    assert resp.status_code == 404, resp.text


def test_post_note_unknown_category_400(dal: DAL, monkeypatch) -> None:
    """未知类别 → 400。"""
    _setup_scene_and_take(dal, scene_code="3A")
    orch = create_orchestrator(dal)
    client = _make_client(orch, monkeypatch)

    resp = client.post(
        "/api/v1/notes",
        json={"text": "3A 1 @invalid 测试"},
        headers=AUTH,
    )
    assert resp.status_code == 400, resp.text


def test_post_note_content_too_long_400(dal: DAL, monkeypatch) -> None:
    """内容超长 → 400。"""
    orch = create_orchestrator(dal)
    client = _make_client(orch, monkeypatch)

    resp = client.post(
        "/api/v1/notes",
        json={"text": "x" * 2001},
        headers=AUTH,
    )
    assert resp.status_code == 400, resp.text


def test_post_note_unauthorized_401(dal: DAL, monkeypatch) -> None:
    """无 auth header → 401。"""
    orch = create_orchestrator(dal)
    client = _make_client(orch, monkeypatch)

    resp = client.post("/api/v1/notes", json={"text": "飞机声"})
    assert resp.status_code == 401, resp.text


# ── GET /takes/{take_id}/notes ───────────────────────────────────────────────


def test_get_notes_returns_aggregated_and_events(dal: DAL, monkeypatch) -> None:
    """POST 后 GET → 200，含 aggregated + events。"""
    take_id = _setup_scene_and_take(dal, scene_code="3A")
    orch = create_orchestrator(dal)
    client = _make_client(orch, monkeypatch)

    # 先创建一条 note
    post_resp = client.post(
        "/api/v1/notes",
        json={"text": "3A 1 @issue 开头有飞机声"},
        headers=AUTH,
    )
    assert post_resp.status_code == 201, post_resp.text

    # 再 GET
    resp = client.get(f"/api/v1/takes/{take_id}/notes", headers=AUTH)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["take_id"] == take_id
    assert data["notes_aggregated"] is not None
    assert len(data["events"]) >= 1
    assert data["events"][0]["category"] == "issue"


def test_get_notes_empty_take(dal: DAL, monkeypatch) -> None:
    """无 note 的 take → 200，events=[]。"""
    take_id = _setup_scene_and_take(dal)
    orch = create_orchestrator(dal)
    client = _make_client(orch, monkeypatch)

    resp = client.get(f"/api/v1/takes/{take_id}/notes", headers=AUTH)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["take_id"] == take_id
    assert data["events"] == []
    # notes_aggregated 为 None 或空
    assert data["notes_aggregated"] is None or data["notes_aggregated"] == ""


def test_get_notes_take_not_found_404(dal: DAL, monkeypatch) -> None:
    """不存在的 take → 404。"""
    orch = create_orchestrator(dal)
    client = _make_client(orch, monkeypatch)

    resp = client.get("/api/v1/takes/99999/notes", headers=AUTH)
    assert resp.status_code == 404, resp.text
