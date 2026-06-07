"""NP Pipeline orchestrator 异步链路测试（Task 5.2 cutover）。

_finalize_np 现在走 extract → resolve → clarify-or-apply → note.applied/note.clarify/note.failed。
成功路径发 note.applied（多 changes）+ 逐 marked take 发 take.changed（coexistence）。
clarify 不是失败，不经 note.failed。失败（parse/timeout/model_unavailable）→ note.failed。
"""

from __future__ import annotations

import asyncio
import sqlite3
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.core.events import (
    NOTE_APPLIED,
    NOTE_CLARIFY,
    NOTE_FAILED,
    TAKE_CHANGED,
    TAKE_START,
    NoteAppliedPayload,
    NoteClarifyPayload,
    NoteFailedPayload,
    TakeChangedPayload,
    TakeStartPayload,
)
from backend.core.orchestrator import create_orchestrator
from backend.core.session import SessionState
from backend.db.dal import DAL
from backend.pipelines.np_extract import NPExtraction, NPParseError


# ── 工具函数 ──────────────────────────────────────────────────────────────────


def _fake_extract(extraction: NPExtraction):
    """返回给定 NPExtraction 的假 runner（鸭子类型，与 orch._deps.np_runner 签名匹配）。"""
    async def _runner(input_data, svc, timeout=30.0, context_line=""):
        return extraction
    return _runner


# ── 共享 fixture ──────────────────────────────────────────────────────────────


@pytest.fixture
def make_orch_with_active_take(tmp_dal: DAL):
    """返回一个工厂 callable：(dal) → (orch, take_id)。

    建场 + 发 TAKE_START（激活 take），orch._deps.np_runner 未注入（测试各自覆盖）。
    """
    def _factory(dal: DAL, scene_code: str = "SC_TEST"):
        scene_id = dal.create_scene(scene_code)
        session = SessionState()
        session.activate_scene(scene_id)
        orch = create_orchestrator(dal, session, llm_service=MagicMock())
        orch.publish(TAKE_START, TakeStartPayload(scene_id=scene_id, shot="A", start_ts=time.time()))
        assert session.take_id is not None
        return orch, session.take_id
    return _factory


# ── 新成功路径测试（Task 5.2）────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_np_async_emits_note_applied_with_changes(tmp_dal: DAL, make_orch_with_active_take) -> None:
    """提取出 current+keep → note.applied 带 1 条 mark change + 逐 take take.changed。"""
    orch, take_id = make_orch_with_active_take(tmp_dal)
    extraction = NPExtraction(
        scene_ordinal=0, shot_ordinal=0, take_ordinals=[],
        deictic="current", mark="keep", note_text="", note_category="note",
    )
    orch._deps.np_runner = _fake_extract(extraction)

    applied: list[NoteAppliedPayload] = []
    changed: list = []
    orch.subscribe(NOTE_APPLIED, lambda p: applied.append(p))
    orch.subscribe(TAKE_CHANGED, lambda p: changed.append(p))

    orch.run_np_async(raw_text="这条保", parsed_category="note", ts=5.0, client_id="cid")
    await asyncio.sleep(0.05)

    assert len(applied) == 1
    assert applied[0].client_id == "cid"
    assert applied[0].changes[0]["op"] == "mark"
    assert applied[0].changes[0]["take_id"] == take_id
    assert applied[0].changes[0]["status"] == "keep"
    # take.changed 一条（驱动卡片），与 note.applied 共存
    assert any(c.take_id == take_id for c in changed)


@pytest.mark.asyncio
async def test_run_np_async_unresolved_emits_clarify_not_applied(tmp_dal: DAL, make_orch_with_active_take) -> None:
    """提取出不存在的 take_ordinal → note.clarify（不写库、不发 note.applied/note.failed）。"""
    orch, _ = make_orch_with_active_take(tmp_dal)
    extraction = NPExtraction(
        scene_ordinal=0, shot_ordinal=0, take_ordinals=[99],
        deictic="none", mark="ng", note_text="", note_category="note",
    )
    orch._deps.np_runner = _fake_extract(extraction)

    clarify: list[NoteClarifyPayload] = []
    applied: list = []
    failed: list = []
    orch.subscribe(NOTE_CLARIFY, lambda p: clarify.append(p))
    orch.subscribe(NOTE_APPLIED, lambda p: applied.append(p))
    orch.subscribe(NOTE_FAILED, lambda p: failed.append(p))

    orch.run_np_async(raw_text="第99条废", parsed_category="note", ts=5.0, client_id="cid")
    await asyncio.sleep(0.05)

    assert len(clarify) == 1 and clarify[0].client_id == "cid"
    assert applied == [] and failed == []


@pytest.mark.asyncio
async def test_pure_mark_still_emits_note_applied(tmp_dal: DAL, make_orch_with_active_take) -> None:
    """纯 mark（note_text=\"\"）也必须发 note.applied（否则前端 pending 挂死，复活 4.I）。"""
    orch, take_id = make_orch_with_active_take(tmp_dal)
    extraction = NPExtraction(
        scene_ordinal=0, shot_ordinal=0, take_ordinals=[],
        deictic="current", mark="pass", note_text="", note_category="note",
    )
    orch._deps.np_runner = _fake_extract(extraction)
    applied: list[NoteAppliedPayload] = []
    orch.subscribe(NOTE_APPLIED, lambda p: applied.append(p))
    orch.run_np_async(raw_text="这条过", parsed_category="note", ts=1.0, client_id="cid")
    await asyncio.sleep(0.05)
    assert len(applied) == 1 and applied[0].changes[0]["op"] == "mark"


# ── 失败路径测试（4.I 继承，runner 已改成返回 NPExtraction）─────────────────


@pytest.mark.asyncio
async def test_run_np_async_parse_error_emits_note_failed(tmp_dal: DAL) -> None:
    """np_runner 抛 NPParseError（LLM 输出非法 JSON）→ note.failed(parse_error)。"""
    scene_id = tmp_dal.create_scene("scene_fail2")
    session = SessionState()
    session.activate_scene(scene_id)

    stub_np = AsyncMock(side_effect=NPParseError("bad json"))
    orch = create_orchestrator(
        tmp_dal, session, llm_service=MagicMock(), np_runner=stub_np
    )
    failed: list[NoteFailedPayload] = []
    orch.subscribe(NOTE_FAILED, lambda p: failed.append(p))

    orch.run_np_async(raw_text="备注", parsed_category="note", ts=1.0, client_id="cid-y")
    assert orch._np_task is not None
    await orch._np_task

    assert len(failed) == 1
    assert failed[0].reason == "parse_error"
    assert failed[0].client_id == "cid-y"


@pytest.mark.asyncio
async def test_run_np_async_timeout_emits_note_failed(tmp_dal: DAL) -> None:
    """np_runner 抛 asyncio.TimeoutError（infer 超时）→ note.failed(timeout)，client_id 缺省为 None。"""
    scene_id = tmp_dal.create_scene("scene_fail3")
    session = SessionState()
    session.activate_scene(scene_id)

    stub_np = AsyncMock(side_effect=asyncio.TimeoutError())
    orch = create_orchestrator(
        tmp_dal, session, llm_service=MagicMock(), np_runner=stub_np
    )
    failed: list[NoteFailedPayload] = []
    orch.subscribe(NOTE_FAILED, lambda p: failed.append(p))

    orch.run_np_async(raw_text="备注", parsed_category="note", ts=1.0)
    assert orch._np_task is not None
    await orch._np_task

    assert len(failed) == 1
    assert failed[0].reason == "timeout"
    assert failed[0].client_id is None


@pytest.mark.asyncio
async def test_run_np_async_take_not_found_emits_note_failed(tmp_dal: DAL, make_orch_with_active_take, monkeypatch: pytest.MonkeyPatch) -> None:
    """apply_targets 撞 FK（set_take_status 抛 IntegrityError）→ note.failed(take_not_found)。"""
    orch, take_id = make_orch_with_active_take(tmp_dal)
    extraction = NPExtraction(
        scene_ordinal=0, shot_ordinal=0, take_ordinals=[],
        deictic="current", mark="keep", note_text="", note_category="note",
    )
    orch._deps.np_runner = _fake_extract(extraction)

    def boom(*_a, **_k):
        raise sqlite3.IntegrityError("FOREIGN KEY constraint failed")

    monkeypatch.setattr(tmp_dal, "set_take_status", boom)

    failed: list[NoteFailedPayload] = []
    applied: list = []
    orch.subscribe(NOTE_FAILED, lambda p: failed.append(p))
    orch.subscribe(NOTE_APPLIED, lambda p: applied.append(p))

    orch.run_np_async(raw_text="这条保", parsed_category="note", ts=7.0, client_id="cid-x")
    assert orch._np_task is not None
    await orch._np_task

    assert len(applied) == 0
    assert len(failed) == 1
    assert failed[0].reason == "take_not_found"
    assert failed[0].client_id == "cid-x"
    assert failed[0].ts == 7.0


@pytest.mark.asyncio
async def test_run_np_voice_async_parse_error_emits_note_failed(tmp_dal: DAL) -> None:
    """voice_runner 抛 NPParseError → note.failed(parse_error)，复用文本失败分类。"""
    scene_id = tmp_dal.create_scene("scene_voice2")
    session = SessionState()
    session.activate_scene(scene_id)

    stub_voice = AsyncMock(side_effect=NPParseError("bad json from audio"))
    orch = create_orchestrator(
        tmp_dal, session, llm_service=MagicMock(), voice_runner=stub_voice
    )
    failed: list[NoteFailedPayload] = []
    orch.subscribe(NOTE_FAILED, lambda p: failed.append(p))

    orch.run_np_voice_async(audio=b"x", ts=3.0, client_id="cid-vpe")
    assert orch._np_task is not None
    await orch._np_task

    assert len(failed) == 1
    assert failed[0].reason == "parse_error"
    assert failed[0].client_id == "cid-vpe"


@pytest.mark.asyncio
async def test_run_np_voice_async_take_not_found_emits_note_failed(tmp_dal: DAL, make_orch_with_active_take, monkeypatch: pytest.MonkeyPatch) -> None:
    """voice apply 撞 FK → note.failed(take_not_found)。"""
    orch, take_id = make_orch_with_active_take(tmp_dal)
    extraction = NPExtraction(
        scene_ordinal=0, shot_ordinal=0, take_ordinals=[],
        deictic="current", mark="keep", note_text="", note_category="note",
    )

    async def _voice_runner(input_data, audio, svc, timeout=60.0, context_line=""):
        return extraction

    orch._deps.voice_runner = _voice_runner

    def boom(*_a, **_k):
        raise sqlite3.IntegrityError("FOREIGN KEY constraint failed")

    monkeypatch.setattr(tmp_dal, "set_take_status", boom)

    failed: list[NoteFailedPayload] = []
    applied: list = []
    orch.subscribe(NOTE_FAILED, lambda p: failed.append(p))
    orch.subscribe(NOTE_APPLIED, lambda p: applied.append(p))

    orch.run_np_voice_async(audio=b"x", ts=4.0, client_id="cid-vnf")
    assert orch._np_task is not None
    await orch._np_task

    assert len(failed) == 1
    assert failed[0].reason == "take_not_found"
    assert len(applied) == 0


@pytest.mark.asyncio
async def test_run_np_voice_async_model_unavailable_emits_note_failed(tmp_dal: DAL) -> None:
    """mmproj 不可用 → ModelUnavailableError → note.failed(model_unavailable)。"""
    from backend.llm.errors import ModelUnavailableError  # noqa: PLC0415

    scene_id = tmp_dal.create_scene("scene_voice4")
    session = SessionState()
    session.activate_scene(scene_id)

    stub_voice = AsyncMock(
        side_effect=ModelUnavailableError("纯文本 GemmaClient 不支持音频推理（未挂多模态 handler）")
    )
    orch = create_orchestrator(
        tmp_dal, session, llm_service=MagicMock(), voice_runner=stub_voice
    )
    failed: list[NoteFailedPayload] = []
    orch.subscribe(NOTE_FAILED, lambda p: failed.append(p))

    orch.run_np_voice_async(audio=b"x", ts=5.0, client_id="cid-mu")
    assert orch._np_task is not None
    await orch._np_task

    assert len(failed) == 1
    assert failed[0].reason == "model_unavailable"
    assert failed[0].client_id == "cid-mu"


# ── context line + input_data threading test ─────────────────────────────────


@pytest.mark.asyncio
async def test_run_np_async_threads_shot_and_scene_code(tmp_dal: DAL) -> None:
    """4.H 继承：np_runner 收到的 input_data 带 current_scene_code + take_context[shot]。"""
    scene_id = tmp_dal.create_scene("Scene_1")
    hist_id, _ = tmp_dal.start_take(scene_id=scene_id, shot="A", start_ts=1.0)
    session = SessionState()
    session.activate_scene(scene_id)

    captured_input = []

    async def _spy_runner(input_data, svc, timeout=30.0, context_line=""):
        captured_input.append(input_data)
        # 返回一个「当前活跃 take / mark none / note 空」，触发 clarify（无活跃 take）或 applied
        raise NPParseError("spy stop early")

    orch = create_orchestrator(tmp_dal, session, llm_service=MagicMock(), np_runner=_spy_runner)

    orch.run_np_async(raw_text="备注", parsed_category="note", ts=1.0)
    assert orch._np_task is not None
    await orch._np_task  # 会抛 NPParseError → note.failed，不影响 input_data 已捕获

    assert len(captured_input) == 1
    np_input = captured_input[0]
    assert np_input.current_scene_code == "Scene_1"
    ctx_by_id = {t["take_id"]: t for t in np_input.take_context}
    assert ctx_by_id[hist_id]["shot"] == "A"


# ── 不变量守卫 ────────────────────────────────────────────────────────────────


def test_status_categories_coupling_holds(tmp_dal: DAL) -> None:
    """守不变量：_STATUS_CATEGORIES 内的值必须是合法 note 类别且合法 take.status。"""
    from backend.core.orchestrator import _STATUS_CATEGORIES
    from backend.pipelines.np_note import _VALID_NOTE_CATEGORIES

    # ① 每个会打 Mark 的 category 都是合法 note 类别
    assert _STATUS_CATEGORIES <= set(_VALID_NOTE_CATEGORIES)

    # ② 每个又都是合法 take.status
    scene_id = tmp_dal.create_scene("sc_invariant")
    take_id, _ = tmp_dal.start_take(scene_id=scene_id, shot="", start_ts=0.0)
    for cat in _STATUS_CATEGORIES:
        tmp_dal.set_take_status(take_id, cat)
