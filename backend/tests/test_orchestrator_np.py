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
    NOTE_CONFIRM,
    NOTE_FAILED,
    TAKE_CHANGED,
    TAKE_START,
    NoteAppliedPayload,
    NoteClarifyPayload,
    NoteConfirmPayload,
    NoteFailedPayload,
    TakeChangedPayload,
    TakeStartPayload,
)
from backend.core.orchestrator import create_orchestrator
from backend.core.session import SessionState
from backend.db.dal import DAL
from backend.pipelines.np_extract import NPConfirmNeeded, NPExtraction, NPParseError


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


# ── 语音双跑 + confirm 分流（Task 3 重做：双跑在 np_voice_adapter，分歧经 NPConfirmNeeded 上抛）──
#
# 全路径测试：orch 的 voice_runner 用 create_orchestrator 默认绑定的真 np_voice_adapter，
# monkeypatch backend.pipelines.np_extract.run_extract_np_voice 按调用计数返回不同 NPExtraction。
# finalize 层测试：fake voice_runner 直接 raise NPConfirmNeeded（包成 awaitable 抛异常的 stub 模式），
# 验 _finalize_np 的 confirm 分支与 _build_confirm_options。


def _seq_extract_voice(monkeypatch: pytest.MonkeyPatch, *results):
    """monkeypatch run_extract_np_voice：按调用计数依次返回预设值（Exception 实例则抛出）。

    返回 call_count（list[int]）供断言调用次数。超出预设次数后重复最后一个。
    """
    call_count = [0]

    async def _stub(audio, svc, timeout=60.0, context_line=""):
        idx = min(call_count[0], len(results) - 1)
        call_count[0] += 1
        item = results[idx]
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr("backend.pipelines.np_extract.run_extract_np_voice", _stub)
    return call_count


def _confirm_raising_runner(extraction: NPExtraction, disagreement: list[str]):
    """fake voice_runner：await 时直接抛 NPConfirmNeeded（测 _finalize_np confirm 分支）。"""
    async def _runner(input_data, audio, svc):
        raise NPConfirmNeeded(extraction, disagreement)
    return _runner


@pytest.mark.asyncio
async def test_voice_double_run_consistent_emits_note_applied(
    tmp_dal: DAL, make_orch_with_active_take, monkeypatch: pytest.MonkeyPatch
) -> None:
    """两跑一致 → run_extract_np_voice 调 2 次（双跑在真 np_voice_adapter 内），发 note.applied。"""
    orch, take_id = make_orch_with_active_take(tmp_dal)
    extraction = NPExtraction(
        scene_ordinal=0, shot_ordinal=0, take_ordinals=[],
        deictic="current", mark="keep", note_text="好的", note_category="note",
    )
    calls = _seq_extract_voice(monkeypatch, extraction, extraction)

    applied: list[NoteAppliedPayload] = []
    confirmed: list = []
    orch.subscribe(NOTE_APPLIED, lambda p: applied.append(p))
    orch.subscribe(NOTE_CONFIRM, lambda p: confirmed.append(p))

    orch.run_np_voice_async(audio=b"wav", ts=1.0, client_id="cid-v")
    assert orch._np_task is not None
    await orch._np_task

    assert calls[0] == 2, "run_extract_np_voice 应被调 2 次（adapter 双跑）"
    assert len(applied) == 1, "一致时发 note.applied"
    assert applied[0].client_id == "cid-v"
    assert confirmed == [], "一致时不发 note.confirm"


@pytest.mark.asyncio
async def test_voice_double_run_disagreement_emits_note_confirm(
    tmp_dal: DAL, make_orch_with_active_take, monkeypatch: pytest.MonkeyPatch
) -> None:
    """两跑分歧(take_ordinals 不同) → 发 note.confirm，不落库(无 note.applied，notes 数不变)。"""
    orch, take_id = make_orch_with_active_take(tmp_dal)
    e1 = NPExtraction(
        scene_ordinal=0, shot_ordinal=0, take_ordinals=[1],
        deictic="none", mark="ng", note_text="第一条废", note_category="note",
    )
    e2 = NPExtraction(
        scene_ordinal=0, shot_ordinal=0, take_ordinals=[3],
        deictic="none", mark="ng", note_text="第三条废", note_category="note",
    )
    _seq_extract_voice(monkeypatch, e1, e2)

    confirmed: list[NoteConfirmPayload] = []
    applied: list = []
    orch.subscribe(NOTE_CONFIRM, lambda p: confirmed.append(p))
    orch.subscribe(NOTE_APPLIED, lambda p: applied.append(p))

    notes_before = len(tmp_dal.list_notes(take_id))

    orch.run_np_voice_async(audio=b"wav", ts=2.0, client_id="cid-disagree")
    assert orch._np_task is not None
    await orch._np_task

    # 分歧 → note.confirm
    assert len(confirmed) == 1
    p = confirmed[0]
    assert p.client_id == "cid-disagree"
    assert "take_ordinals" in p.disagreement
    assert p.ts == 2.0

    # extraction == 第一跑 asdict（第一跑结果为准）
    from dataclasses import asdict
    assert p.extraction == asdict(e1)

    # 不落库
    assert applied == [], "分歧时不发 note.applied"
    assert len(tmp_dal.list_notes(take_id)) == notes_before, "分歧时不应写库(note 数不变)"

    # options 形状
    assert "scenes" in p.options
    assert "shots" in p.options
    assert "take_numbers" in p.options
    assert isinstance(p.options["scenes"], list)


@pytest.mark.asyncio
async def test_voice_confirm_options_with_existing_scene(
    tmp_dal: DAL, make_orch_with_active_take
) -> None:
    """options 装配（finalize 层）：scene_ordinal==0 → session 当前场，shots 来自该场。"""
    orch, take_id = make_orch_with_active_take(tmp_dal, scene_code="SC_OPTS")

    # 在当前场再建镜 B（A 来自 TAKE_START）
    scene_id = orch.session.scene_id
    tmp_dal.start_take(scene_id=scene_id, shot="B", start_ts=10.0)

    e1 = NPExtraction(
        scene_ordinal=0, shot_ordinal=0, take_ordinals=[1],
        deictic="none", mark="pass", note_text="过", note_category="note",
    )
    orch._deps.voice_runner = _confirm_raising_runner(e1, ["take_ordinals"])

    confirmed: list[NoteConfirmPayload] = []
    orch.subscribe(NOTE_CONFIRM, lambda p: confirmed.append(p))

    orch.run_np_voice_async(audio=b"wav", ts=3.0, client_id="cid-opts")
    assert orch._np_task is not None
    await orch._np_task

    assert len(confirmed) == 1
    opts = confirmed[0].options
    assert "A" in opts["shots"]
    assert "B" in opts["shots"]
    assert "SC_OPTS" in opts["scenes"]


@pytest.mark.asyncio
async def test_voice_confirm_options_scene_ordinal_zero_no_active_scene(
    tmp_dal: DAL
) -> None:
    """scene_ordinal==0 但 session 无当前场 → shots/take_numbers 空列表，scenes 仍全量。"""
    tmp_dal.create_scene("SC_NOACTIVE")
    session = SessionState()
    # 不调 session.activate_scene → session.scene_id = None
    orch = create_orchestrator(tmp_dal, session, llm_service=MagicMock())

    e1 = NPExtraction(
        scene_ordinal=0, shot_ordinal=0, take_ordinals=[1],
        deictic="none", mark="ng", note_text="", note_category="note",
    )
    orch._deps.voice_runner = _confirm_raising_runner(e1, ["take_ordinals"])

    confirmed: list[NoteConfirmPayload] = []
    orch.subscribe(NOTE_CONFIRM, lambda p: confirmed.append(p))

    orch.run_np_voice_async(audio=b"wav", ts=4.0, client_id="cid-noactive")
    assert orch._np_task is not None
    await orch._np_task

    assert len(confirmed) == 1
    opts = confirmed[0].options
    assert opts["shots"] == []
    assert opts["take_numbers"] == []
    assert "SC_NOACTIVE" in opts["scenes"]


@pytest.mark.asyncio
async def test_voice_confirm_options_nonzero_scene_and_shot(tmp_dal: DAL) -> None:
    """scene_ordinal!=0 → resolve_scene_id(str(ordinal)) 定场；shot_ordinal!=0 → str 转换匹配。

    验证 int→str 转换正确（DB shot 是字符串，shot_ordinal 是 int）。
    """
    scene_id = tmp_dal.create_scene("1")
    tmp_dal.start_take(scene_id=scene_id, shot="2", start_ts=1.0)
    tmp_dal.start_take(scene_id=scene_id, shot="2", start_ts=2.0)
    tmp_dal.start_take(scene_id=scene_id, shot="3", start_ts=3.0)

    session = SessionState()
    orch = create_orchestrator(tmp_dal, session, llm_service=MagicMock())

    e1 = NPExtraction(
        scene_ordinal=1, shot_ordinal=2, take_ordinals=[1],
        deictic="none", mark="ng", note_text="", note_category="note",
    )
    orch._deps.voice_runner = _confirm_raising_runner(e1, ["take_ordinals"])

    confirmed: list[NoteConfirmPayload] = []
    orch.subscribe(NOTE_CONFIRM, lambda p: confirmed.append(p))

    orch.run_np_voice_async(audio=b"wav", ts=9.0, client_id="cid-nonzero")
    assert orch._np_task is not None
    await orch._np_task

    assert len(confirmed) == 1
    opts = confirmed[0].options
    assert "1" in opts["scenes"]
    assert "2" in opts["shots"] and "3" in opts["shots"]
    # take_numbers 是镜 "2" 下的（shot_ordinal=2 → str("2") → list_take_numbers）
    assert 1 in opts["take_numbers"] and 2 in opts["take_numbers"]
    assert len(opts["take_numbers"]) == 2, "镜 3 的 take 不应混入"


@pytest.mark.asyncio
async def test_voice_second_run_exception_fail_open(
    tmp_dal: DAL, make_orch_with_active_take,
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    """第二跑抛异常 → fail-open：adapter 直落第一跑结果（发 note.applied），logger.warning 记录。"""
    import logging
    orch, take_id = make_orch_with_active_take(tmp_dal)
    e1 = NPExtraction(
        scene_ordinal=0, shot_ordinal=0, take_ordinals=[],
        deictic="current", mark="pass", note_text="过", note_category="note",
    )
    _seq_extract_voice(monkeypatch, e1, asyncio.TimeoutError("second run timeout"))

    applied: list[NoteAppliedPayload] = []
    confirmed: list = []
    failed: list = []
    orch.subscribe(NOTE_APPLIED, lambda p: applied.append(p))
    orch.subscribe(NOTE_CONFIRM, lambda p: confirmed.append(p))
    orch.subscribe(NOTE_FAILED, lambda p: failed.append(p))

    with caplog.at_level(logging.WARNING, logger="backend.pipelines.np_extract"):
        orch.run_np_voice_async(audio=b"wav", ts=5.0, client_id="cid-failopen")
        assert orch._np_task is not None
        await orch._np_task

    # fail-open → 第一跑结果直落（单跑语义）
    assert len(applied) == 1, "第二跑故障时应 fail-open 发 note.applied"
    assert applied[0].client_id == "cid-failopen"
    assert confirmed == [], "fail-open 不发 note.confirm"
    assert failed == [], "fail-open 不算失败"
    # warning 已记录（np_extract 模块 logger）
    assert any("第二跑" in r.message or "fail-open" in r.message for r in caplog.records), \
        f"应有第二跑 fail-open warning，实际: {[r.message for r in caplog.records]}"


@pytest.mark.asyncio
async def test_voice_first_run_exception_maps_note_failed(
    tmp_dal: DAL, make_orch_with_active_take, monkeypatch: pytest.MonkeyPatch
) -> None:
    """第一跑抛 NPParseError → 原样上抛经 _finalize_np 映射 note.failed(parse_error)（不双跑）。"""
    orch, _ = make_orch_with_active_take(tmp_dal)
    calls = _seq_extract_voice(monkeypatch, NPParseError("bad json from audio"))

    failed: list = []
    confirmed: list = []
    orch.subscribe(NOTE_FAILED, lambda p: failed.append(p))
    orch.subscribe(NOTE_CONFIRM, lambda p: confirmed.append(p))

    orch.run_np_voice_async(audio=b"wav", ts=6.0, client_id="cid-r1fail")
    assert orch._np_task is not None
    await orch._np_task

    assert calls[0] == 1, "第一跑失败后不应再跑第二跑"
    assert len(failed) == 1 and failed[0].reason == "parse_error"
    assert confirmed == []


@pytest.mark.asyncio
async def test_text_path_still_single_run(tmp_dal: DAL, make_orch_with_active_take) -> None:
    """文本路径 run_np_async 仍是单跑，np_runner 只被调 1 次（双跑只在语音 adapter）。"""
    orch, _ = make_orch_with_active_take(tmp_dal)
    call_count = [0]
    extraction = NPExtraction(
        scene_ordinal=0, shot_ordinal=0, take_ordinals=[],
        deictic="current", mark="keep", note_text="保", note_category="note",
    )

    async def _counting_runner(input_data, svc, timeout=30.0, context_line=""):
        call_count[0] += 1
        return extraction

    orch._deps.np_runner = _counting_runner

    applied: list = []
    orch.subscribe(NOTE_APPLIED, lambda p: applied.append(p))

    orch.run_np_async(raw_text="这条保", parsed_category="note", ts=6.0, client_id="cid-text")
    assert orch._np_task is not None
    await orch._np_task

    assert call_count[0] == 1, "文本路径 np_runner 仅调 1 次"
    assert len(applied) == 1
