"""NP Pipeline orchestrator 异步链路测试（4.x Bug C）：client_id 透传到 note.processed。

Bug C 的根：前端乐观 pending 要按 client_id 精确移除——content 会被 NP 的 LLM 改写（去指代词），
pending.ts（前端 Date.now()）与 note.processed.ts（后端 time.time()）又不同源，旧的三元匹配必失败，
pending 永久卡「处理中」。修复要求后端把前端传入的 client_id 原样透传回 note.processed。
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.core.events import (
    NOTE_FAILED,
    NOTE_PROCESSED,
    TAKE_START,
    NoteFailedPayload,
    NoteProcessedPayload,
    TakeStartPayload,
)
from backend.core.orchestrator import create_orchestrator
from backend.core.session import SessionState
from backend.db.dal import DAL
from backend.pipelines.np_note import NPOutput, NPParseError


@pytest.mark.asyncio
async def test_run_np_async_propagates_client_id(tmp_dal: DAL) -> None:
    """run_np_async 的 client_id 原样透传到 note.processed payload，且 content 确实被改写。"""
    scene_id = tmp_dal.create_scene("scene_np1")
    session = SessionState()
    session.activate_scene(scene_id)

    stub_np = AsyncMock()
    orch = create_orchestrator(
        tmp_dal, session, llm_service=MagicMock(), np_runner=stub_np
    )

    # 建 take，让 np_runner 归到一个真实 take_id（insert_note 需要 take 存在）
    orch.publish(TAKE_START, TakeStartPayload(scene_id=scene_id, shot=None, start_ts=time.time()))
    assert session.take_id is not None

    # np_runner 归到该 take 且改写 content（模拟 LLM 去掉指代词「这条」）
    stub_np.return_value = NPOutput(
        take_id=session.take_id, category="keeper", content="很好可以用"
    )

    captured: list[NoteProcessedPayload] = []
    orch.subscribe(NOTE_PROCESSED, lambda p: captured.append(p))  # type: ignore[arg-type]

    orch.run_np_async(
        raw_text="这条很好可以用",
        parsed_category="keeper",
        ts=111.0,
        client_id="cid-abc-123",
    )
    assert orch._np_task is not None  # type: ignore[attr-defined]
    await orch._np_task  # type: ignore[attr-defined]

    assert len(captured) == 1
    payload = captured[0]
    assert payload.client_id == "cid-abc-123"  # 原样透传 → 前端据此精确移除 pending
    assert payload.take_id == session.take_id
    # content 被改写（raw「这条很好可以用」→「很好可以用」），证明 content 不能当匹配键，client_id 才可靠
    assert payload.content == "很好可以用"


@pytest.mark.asyncio
async def test_run_np_async_client_id_none_when_omitted(tmp_dal: DAL) -> None:
    """未传 client_id（旧前端/异常）时 payload.client_id 为 None，前端据此走「不误删」分支。"""
    scene_id = tmp_dal.create_scene("scene_np2")
    session = SessionState()
    session.activate_scene(scene_id)

    stub_np = AsyncMock()
    orch = create_orchestrator(
        tmp_dal, session, llm_service=MagicMock(), np_runner=stub_np
    )
    orch.publish(TAKE_START, TakeStartPayload(scene_id=scene_id, shot=None, start_ts=time.time()))
    assert session.take_id is not None
    stub_np.return_value = NPOutput(take_id=session.take_id, category="note", content="备注")

    captured: list[NoteProcessedPayload] = []
    orch.subscribe(NOTE_PROCESSED, lambda p: captured.append(p))  # type: ignore[arg-type]

    orch.run_np_async(raw_text="备注", parsed_category="note", ts=1.0)
    assert orch._np_task is not None  # type: ignore[attr-defined]
    await orch._np_task  # type: ignore[attr-defined]

    assert len(captured) == 1
    assert captured[0].client_id is None


@pytest.mark.asyncio
async def test_run_np_async_threads_shot_and_scene_code(tmp_dal: DAL) -> None:
    """4.H：take_context 每条带 shot，当前场 scene_code 解析进 NPInput（场镜次 prompt 补全）。"""
    scene_id = tmp_dal.create_scene("Scene_1")
    # 一条历史 take（shot=A），无活跃 take → take_context 不排除它
    hist_id, _ = tmp_dal.start_take(scene_id=scene_id, shot="A", start_ts=1.0)
    session = SessionState()
    session.activate_scene(scene_id)

    stub_np = AsyncMock()
    orch = create_orchestrator(
        tmp_dal, session, llm_service=MagicMock(), np_runner=stub_np
    )
    stub_np.return_value = NPOutput(take_id=hist_id, category="note", content="x")

    orch.run_np_async(raw_text="备注", parsed_category="note", ts=1.0)
    assert orch._np_task is not None  # type: ignore[attr-defined]
    await orch._np_task  # type: ignore[attr-defined]

    np_input = stub_np.call_args.args[0]
    assert np_input.current_scene_code == "Scene_1"  # scene_id → scene_code 已解析
    ctx_by_id = {t["take_id"]: t for t in np_input.take_context}
    assert ctx_by_id[hist_id]["shot"] == "A"  # shot 从 DAL 透传进上下文


# ---- 4.I：NP 失败兜底 note.failed ----
# 失败时不发 note.processed，改发 note.failed（带 client_id + 机制可检测的 reason），
# 让前端把对应 pending 从「处理中」转失败态，而非永久卡死。


@pytest.mark.asyncio
async def test_run_np_async_take_not_found_emits_note_failed(tmp_dal: DAL) -> None:
    """LLM 归到不存在的 take_id → insert_note 撞 FK → note.failed(take_not_found)，不发 processed。"""
    scene_id = tmp_dal.create_scene("scene_fail1")
    session = SessionState()
    session.activate_scene(scene_id)

    stub_np = AsyncMock()
    orch = create_orchestrator(
        tmp_dal, session, llm_service=MagicMock(), np_runner=stub_np
    )
    stub_np.return_value = NPOutput(take_id=999999, category="note", content="x")

    failed: list[NoteFailedPayload] = []
    processed: list[NoteProcessedPayload] = []
    orch.subscribe(NOTE_FAILED, lambda p: failed.append(p))  # type: ignore[arg-type]
    orch.subscribe(NOTE_PROCESSED, lambda p: processed.append(p))  # type: ignore[arg-type]

    orch.run_np_async(raw_text="备注", parsed_category="note", ts=7.0, client_id="cid-x")
    assert orch._np_task is not None  # type: ignore[attr-defined]
    await orch._np_task  # type: ignore[attr-defined]

    assert len(processed) == 0  # 失败不落库、不发 processed
    assert len(failed) == 1
    assert failed[0].reason == "take_not_found"
    assert failed[0].client_id == "cid-x"  # 原样透传供前端精确定位
    assert failed[0].ts == 7.0


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
    orch.subscribe(NOTE_FAILED, lambda p: failed.append(p))  # type: ignore[arg-type]

    orch.run_np_async(raw_text="备注", parsed_category="note", ts=1.0, client_id="cid-y")
    assert orch._np_task is not None  # type: ignore[attr-defined]
    await orch._np_task  # type: ignore[attr-defined]

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
    orch.subscribe(NOTE_FAILED, lambda p: failed.append(p))  # type: ignore[arg-type]

    orch.run_np_async(raw_text="备注", parsed_category="note", ts=1.0)
    assert orch._np_task is not None  # type: ignore[attr-defined]
    await orch._np_task  # type: ignore[attr-defined]

    assert len(failed) == 1
    assert failed[0].reason == "timeout"
    assert failed[0].client_id is None


# ---------------------------------------------------------------------------
# 语音 NP orchestrator 链路（4.J-4）：run_np_voice_async —— 透传 audio + 转写正文存 raw_text +
# 共用 4.I 失败兜底。
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_np_voice_async_processed_threads_audio_and_stores_transcript(
    tmp_dal: DAL,
) -> None:
    """run_np_voice_async：audio 透传给 voice_runner，note.processed 带 client_id，
    raw_text 存模型转写正文（§8，语音无原始文字）。"""
    scene_id = tmp_dal.create_scene("scene_voice1")
    session = SessionState()
    session.activate_scene(scene_id)

    stub_voice = AsyncMock()
    orch = create_orchestrator(
        tmp_dal, session, llm_service=MagicMock(), voice_runner=stub_voice
    )
    orch.publish(TAKE_START, TakeStartPayload(scene_id=scene_id, shot=None, start_ts=time.time()))
    assert session.take_id is not None
    stub_voice.return_value = NPOutput(
        take_id=session.take_id, category="keeper", content="结尾好可以用"
    )

    captured: list[NoteProcessedPayload] = []
    orch.subscribe(NOTE_PROCESSED, lambda p: captured.append(p))  # type: ignore[arg-type]

    orch.run_np_voice_async(audio=b"WAVBYTES", ts=222.0, client_id="cid-voice")
    assert orch._np_task is not None  # type: ignore[attr-defined]
    await orch._np_task  # type: ignore[attr-defined]

    assert len(captured) == 1
    assert captured[0].client_id == "cid-voice"
    assert captured[0].take_id == session.take_id
    assert captured[0].content == "结尾好可以用"
    # voice_runner 第二位参数是 audio 字节
    assert stub_voice.call_args.args[1] == b"WAVBYTES"
    # raw_text 存模型转写正文（语音路径无原始文字，§8）
    notes = tmp_dal.list_notes(session.take_id)
    assert notes[-1].payload["raw_text"] == "结尾好可以用"


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
    orch.subscribe(NOTE_FAILED, lambda p: failed.append(p))  # type: ignore[arg-type]

    orch.run_np_voice_async(audio=b"x", ts=3.0, client_id="cid-vpe")
    assert orch._np_task is not None  # type: ignore[attr-defined]
    await orch._np_task  # type: ignore[attr-defined]

    assert len(failed) == 1
    assert failed[0].reason == "parse_error"
    assert failed[0].client_id == "cid-vpe"


@pytest.mark.asyncio
async def test_run_np_voice_async_take_not_found_emits_note_failed(tmp_dal: DAL) -> None:
    """模型归到不存在 take_id → insert_note 撞 FK → note.failed(take_not_found)。"""
    scene_id = tmp_dal.create_scene("scene_voice3")
    session = SessionState()
    session.activate_scene(scene_id)

    stub_voice = AsyncMock(
        return_value=NPOutput(take_id=999999, category="note", content="听到的内容")
    )
    orch = create_orchestrator(
        tmp_dal, session, llm_service=MagicMock(), voice_runner=stub_voice
    )
    failed: list[NoteFailedPayload] = []
    processed: list[NoteProcessedPayload] = []
    orch.subscribe(NOTE_FAILED, lambda p: failed.append(p))  # type: ignore[arg-type]
    orch.subscribe(NOTE_PROCESSED, lambda p: processed.append(p))  # type: ignore[arg-type]

    orch.run_np_voice_async(audio=b"x", ts=4.0, client_id="cid-vnf")
    assert orch._np_task is not None  # type: ignore[attr-defined]
    await orch._np_task  # type: ignore[attr-defined]

    assert len(failed) == 1
    assert failed[0].reason == "take_not_found"
    assert len(processed) == 0


@pytest.mark.asyncio
async def test_run_np_voice_async_model_unavailable_emits_note_failed(tmp_dal: DAL) -> None:
    """mmproj 不可用 → 纯文本 client → 音频推理 RuntimeError → note.failed(model_unavailable)。

    安全网：setup 失败在源头抛 ModelUnavailableError（client 无 handler / multimodal mtmd 自检失败），
    _finalize_np 干净映射成 model_unavailable，不能命中 `else: raise` 静默退出（否则前端 pending
    永久卡，复活 4.I 的 bug）。
    """
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
    orch.subscribe(NOTE_FAILED, lambda p: failed.append(p))  # type: ignore[arg-type]

    orch.run_np_voice_async(audio=b"x", ts=5.0, client_id="cid-mu")
    assert orch._np_task is not None  # type: ignore[attr-defined]
    await orch._np_task  # type: ignore[attr-defined]

    assert len(failed) == 1
    assert failed[0].reason == "model_unavailable"
    assert failed[0].client_id == "cid-mu"
