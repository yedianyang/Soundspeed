"""Task 4：POST /notes/confirm 端点测试（TDD）。

四条路径：
  1. 合法 extraction + session 有活跃 take → 202；后台 task 完成后发 note.applied，DB 落库
  2. extraction 非法（mark="bad"）→ 400，detail 含错误原因；不发任何 note.* 事件
  3. extraction 指向不存在的场（scene_ordinal=99）→ 202 + 后台发 note.clarify
  4. ts/client_id 均缺省 → 202，run_np_confirm_async 以 client_id=None 被调，ts 由服务端补全

等后台 task：poll orch._np_task.done()（与 run_np_async 同惯例，_np_task 在 confirm 路径也被赋值）。
走真实 orchestrator._resolve_apply_publish（无 LLM，确定性段）。
"""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.core.events import NOTE_APPLIED, NOTE_CLARIFY
from backend.core.orchestrator import create_orchestrator
from backend.db.dal import DAL

_TOKEN = "devtoken"
AUTH = {"Authorization": f"Bearer {_TOKEN}"}

# 合法 extraction：7 字段全满，deictic="current"（依赖 session 有活跃 take）。
_VALID_EXTRACTION = {
    "scene_ordinal": 0,
    "shot_ordinal": 0,
    "take_ordinals": [],
    "deictic": "current",
    "mark": "ng",
    "note_text": "声音有点小",
    "note_category": "note",
}


def _make_client(dal: DAL, monkeypatch) -> TestClient:
    monkeypatch.setenv("ADMIN_TOKEN", _TOKEN)
    orch = create_orchestrator(dal)
    app = create_app(orch)
    return TestClient(app)


@pytest.fixture
def dal(tmp_path):
    d = DAL(tmp_path / "test.db")
    yield d
    d.close()


def _setup_scene_and_take(dal: DAL, scene_code: str = "1A") -> tuple[int, int]:
    """建 scene + take（DAL 公开方法，照 test_orchestrator_np.py 的种法），返回 (scene_id, take_id)。"""
    scene_id = dal.create_scene(scene_code)
    take_id, _ = dal.start_take(scene_id=scene_id, shot="", start_ts=1000.0)
    return scene_id, take_id


def _wait_np_task(app, timeout: float = 2.0) -> None:
    """轮询 orch._np_task 直到 done（TestClient with 块内保持 loop 存活）。

    调用方须在 post 返回后先 assert orch._np_task is not None：202 响应返回
    意味着 run_np_confirm_async 已同步执行完 create_task + 赋值（FastAPI 端点
    同步路径，赋值 happens-before 响应），此处只管等 done。
    """
    deadline = time.monotonic() + timeout
    orch = app.state.orchestrator
    while time.monotonic() < deadline:
        task = orch._np_task
        if task is not None and task.done():
            return
        time.sleep(0.02)
    raise TimeoutError("orch._np_task 未在超时内完成")


# ── 测试 1：合法 extraction，deictic=current，session 有活跃 take ─────────────


def test_confirm_valid_extraction_202_and_applied(dal: DAL, monkeypatch) -> None:
    """POST /notes/confirm，合法 extraction，后台 task 发 note.applied，DB 写库（notes +1）。

    deictic="current" + session 有活跃 take → resolve 直接定位，无 clarify。
    extraction 含 mark="ng"（取回 status） + note_text 非空（insert_note）。
    """
    scene_id, take_id = _setup_scene_and_take(dal)
    monkeypatch.setenv("ADMIN_TOKEN", _TOKEN)

    orch = create_orchestrator(dal)
    # 注入 session 使 resolve 能找到 current_take_id
    orch.session.scene_id = scene_id
    orch.session.take_id = take_id
    orch.session.take_active = True

    published: list[tuple] = []
    _orig = orch.publish

    def _spy(event, payload):
        published.append((event, payload))
        _orig(event, payload)

    orch.publish = _spy

    app = create_app(orch)

    with TestClient(app) as client:
        resp = client.post(
            "/api/v1/notes/confirm",
            json={"extraction": _VALID_EXTRACTION, "ts": 1234.0, "client_id": "c1"},
            headers=AUTH,
        )
        assert resp.status_code == 202, resp.text
        assert resp.json().get("status") == "processing"

        # 202 已返回 ⇒ run_np_confirm_async 已同步完成 create_task+赋值（happens-before）
        assert orch._np_task is not None
        _wait_np_task(app)

    # 后台 task 已完成：应发 note.applied
    applied_events = [p for e, p in published if e == NOTE_APPLIED]
    assert len(applied_events) == 1, f"应有 1 个 note.applied，实际 {len(applied_events)}"
    assert applied_events[0].client_id == "c1"

    # DB 落库：take_events 表应有 1 条 manual.note
    notes = dal.list_notes(take_id)
    assert len(notes) >= 1, f"DB 应有 ≥1 条 note，实际 {len(notes)}"
    assert notes[0].payload.get("content") == "声音有点小"


# ── 测试 2：非法 extraction → 400，无副作用 ──────────────────────────────────


def test_confirm_invalid_extraction_400_no_side_effects(dal: DAL, monkeypatch) -> None:
    """extraction.mark="bad" → 400，detail 含错误原因；不发任何 note.* 事件。"""
    scene_id, take_id = _setup_scene_and_take(dal)
    monkeypatch.setenv("ADMIN_TOKEN", _TOKEN)

    orch = create_orchestrator(dal)
    orch.session.scene_id = scene_id
    orch.session.take_id = take_id
    orch.session.take_active = True

    published: list[tuple] = []
    _orig = orch.publish

    def _spy(event, payload):
        published.append((event, payload))
        _orig(event, payload)

    orch.publish = _spy

    app = create_app(orch)

    bad_extraction = dict(_VALID_EXTRACTION, mark="bad")

    with TestClient(app) as client:
        resp = client.post(
            "/api/v1/notes/confirm",
            json={"extraction": bad_extraction, "ts": 1234.0, "client_id": "c1"},
            headers=AUTH,
        )
    # 400 在 create_task 前同步抛出，无后台 task 需等

    assert resp.status_code == 400, resp.text
    detail = resp.json().get("detail", "")
    assert "mark" in detail, f"detail 应含 'mark'，实际：{detail!r}"

    # 无任何 note.* 事件（400 在 create_task 之前同步抛出）
    note_events = [(e, p) for e, p in published if e in (NOTE_APPLIED, NOTE_CLARIFY)]
    assert note_events == [], f"不应发任何 note.* 事件，实际 {note_events}"

    # DB 无新 note
    notes = dal.list_notes(take_id)
    assert len(notes) == 0, f"DB 不应有 note，实际 {notes}"


# ── 测试 3：scene_ordinal=99（不存在）→ 202 + 后台发 note.clarify ────────────


def test_confirm_unknown_scene_202_clarify(dal: DAL, monkeypatch) -> None:
    """extraction 指向不存在的场（scene_ordinal=99）→ 202，后台发 note.clarify（resolve 兜底）。"""
    _setup_scene_and_take(dal)
    monkeypatch.setenv("ADMIN_TOKEN", _TOKEN)

    orch = create_orchestrator(dal)

    published: list[tuple] = []
    _orig = orch.publish

    def _spy(event, payload):
        published.append((event, payload))
        _orig(event, payload)

    orch.publish = _spy

    app = create_app(orch)

    clarify_extraction = dict(_VALID_EXTRACTION, scene_ordinal=99, deictic="none")

    with TestClient(app) as client:
        resp = client.post(
            "/api/v1/notes/confirm",
            json={"extraction": clarify_extraction, "ts": 1234.0, "client_id": "c2"},
            headers=AUTH,
        )
        assert resp.status_code == 202, resp.text

        # 202 已返回 ⇒ run_np_confirm_async 已同步完成 create_task+赋值（happens-before）
        assert orch._np_task is not None
        _wait_np_task(app)

    clarify_events = [p for e, p in published if e == NOTE_CLARIFY]
    assert len(clarify_events) == 1, f"应有 1 个 note.clarify，实际 {len(clarify_events)}"
    assert clarify_events[0].client_id == "c2"


# ── 测试 4：ts/client_id 缺省 → server 补 ts，client_id=None 透传 ──────────


def test_confirm_default_ts_and_client_id(dal: DAL, monkeypatch) -> None:
    """ts 缺省 → 服务端 time.time() 兜底；client_id 缺省 → None 透传到 run_np_confirm_async。

    spy run_np_confirm_async 捕获参数（不跑真实后台 task，只验接线）。
    """
    _setup_scene_and_take(dal)
    monkeypatch.setenv("ADMIN_TOKEN", _TOKEN)

    orch = create_orchestrator(dal)
    app = create_app(orch)

    confirm_calls: list[dict] = []
    _before = time.time()

    with TestClient(app) as client:
        orch_ref = client.app.state.orchestrator

        def _capture_confirm(extraction, ts, client_id):
            confirm_calls.append({"ts": ts, "client_id": client_id})

        orch_ref.run_np_confirm_async = _capture_confirm

        resp = client.post(
            "/api/v1/notes/confirm",
            json={"extraction": _VALID_EXTRACTION},  # 不传 ts/client_id
            headers=AUTH,
        )

    _after = time.time()

    assert resp.status_code == 202, resp.text
    assert len(confirm_calls) == 1, f"run_np_confirm_async 应被调 1 次，实际 {confirm_calls}"
    captured = confirm_calls[0]

    # ts 由服务端补全，应在请求前后时间范围内
    assert _before <= captured["ts"] <= _after + 1, (
        f"ts 应在 [{_before}, {_after + 1}]，实际 {captured['ts']}"
    )
    # client_id 缺省 → None
    assert captured["client_id"] is None, (
        f"client_id 应为 None，实际 {captured['client_id']!r}"
    )


# ── 测试 5：Bug①——确认卡显式 take_ordinals 不被 deictic 静默覆盖 ──────────────


def test_confirm_explicit_ordinals_override_deictic(dal: DAL, monkeypatch) -> None:
    """take_ordinals 非空 + deictic="prev" → 路由层归一 deictic="none"，resolve 走 ordinals 分支。

    种法：scene + take1（已完成，end_ts 落）+ take2（活跃）。
    extraction = {take_ordinals:[2], deictic:"prev", ...}。
    Bug 现行为：deictic=prev 赢 → 落到 take1（最近完成）。
    期望修后：note 落到 take2（take_number=2），take1 状态不动。
    """
    scene_id = dal.create_scene("1")
    # take1：第 1 条，已完成（start + end）
    take1_id, _ = dal.start_take(scene_id=scene_id, shot="", start_ts=1000.0)
    dal.end_take(take1_id, end_ts=1001.0)
    # take2：第 2 条，活跃（only start_take，不 end）
    take2_id, _ = dal.start_take(scene_id=scene_id, shot="", start_ts=1002.0)

    monkeypatch.setenv("ADMIN_TOKEN", _TOKEN)
    orch = create_orchestrator(dal)
    orch.session.scene_id = scene_id
    orch.session.take_id = take2_id
    orch.session.take_active = True

    published: list[tuple] = []
    _orig = orch.publish

    def _spy(event, payload):
        published.append((event, payload))
        _orig(event, payload)

    orch.publish = _spy

    app = create_app(orch)

    # extraction：用户在确认卡显式填了 take_ordinals=[2]，但 deictic 保留了第一跑的 "prev"
    extraction_with_conflict = {
        "scene_ordinal": 0,
        "shot_ordinal": 0,
        "take_ordinals": [2],
        "deictic": "prev",
        "mark": "keep",
        "note_text": "",
        "note_category": "note",
    }

    with TestClient(app) as client:
        resp = client.post(
            "/api/v1/notes/confirm",
            json={"extraction": extraction_with_conflict, "ts": 2000.0, "client_id": "c-ordinal"},
            headers=AUTH,
        )
        assert resp.status_code == 202, resp.text

        assert orch._np_task is not None
        _wait_np_task(app)

    # 期望：note 落到 take2（take_number=2，用户显式编号优先）
    applied_events = [p for e, p in published if e == NOTE_APPLIED]
    assert len(applied_events) == 1, f"应有 1 个 note.applied，实际 {len(applied_events)}"
    changes = applied_events[0].changes
    applied_take_ids = [c["take_id"] for c in changes]
    assert take2_id in applied_take_ids, (
        f"note 应落到 take2（id={take2_id}），实际 changes={changes}"
    )
    # take1 状态不动（原来是 tbd，不应被打 mark）
    take1 = dal.get_take(take1_id)
    assert take1 is not None
    assert take1.status == "tbd", (
        f"take1 状态应为 tbd（未被改动），实际 {take1.status!r}"
    )
