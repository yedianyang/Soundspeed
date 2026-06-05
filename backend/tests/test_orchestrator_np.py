"""NP Pipeline orchestrator 异步链路测试（4.x Bug C）：client_id 透传到 note.processed。

Bug C 的根：前端乐观 pending 要按 client_id 精确移除——content 会被 NP 的 LLM 改写（去指代词），
pending.ts（前端 Date.now()）与 note.processed.ts（后端 time.time()）又不同源，旧的三元匹配必失败，
pending 永久卡「处理中」。修复要求后端把前端传入的 client_id 原样透传回 note.processed。
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.core.events import (
    NOTE_PROCESSED,
    TAKE_START,
    NoteProcessedPayload,
    TakeStartPayload,
)
from backend.core.orchestrator import create_orchestrator
from backend.core.session import SessionState
from backend.db.dal import DAL
from backend.pipelines.np_note import NPOutput


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
