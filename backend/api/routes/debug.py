"""dev-only 合成 ASR 注入端点（1.J-1.L §2.4）。

仅在 SOUNDSPEED_DEV=1 时由 create_app 挂载，生产不暴露。
POST /api/v1/debug/asr → 合成 AsrPartialPayload / AsrFinalPayload → orchestrator.publish。

用途：1.C（结构化 ASR 输出）落地前手动驱动 1.J transcript 面板验收。
start_frame / end_frame 用 int(time.time()*1000)，使存库 segments 按 start_frame 排序正确。
take_id=None → _on_asr_final 通过 _resolve_take_id 回退 session.take_id；
active take 时 final ASR 既推 WS 也存库（take detail 可见 segments）。
"""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from backend.api.auth import require_admin
from backend.core.events import (
    ASR_FINAL_CH1,
    ASR_FINAL_CH2,
    ASR_PARTIAL_CH1,
    ASR_PARTIAL_CH2,
    AsrFinalPayload,
    AsrPartialPayload,
)

router = APIRouter(prefix="/api/v1/debug", tags=["debug"])


class DebugAsrBody(BaseModel):
    """POST /api/v1/debug/asr 请求体。"""

    ch: int
    text: str
    speaker: str | None = None
    is_partial: bool


@router.post("/asr")
async def debug_asr(
    body: DebugAsrBody,
    request: Request,
    _: None = Depends(require_admin),
) -> dict[str, str]:
    """合成 ASR 事件并 publish 到 orchestrator。

    ch 必须为 1 或 2，否则 422。
    start_frame / end_frame 用 int(time.time()*1000)，保证存库顺序正确。
    """
    if body.ch not in (1, 2):
        raise HTTPException(status_code=422, detail="ch must be 1 or 2")

    orch = request.app.state.orchestrator
    frame_ts = int(time.time() * 1000)

    if body.is_partial:
        topic = ASR_PARTIAL_CH1 if body.ch == 1 else ASR_PARTIAL_CH2
        payload: AsrPartialPayload | AsrFinalPayload = AsrPartialPayload(
            text=body.text,
            start_frame=frame_ts,
            end_frame=frame_ts,
            speaker=body.speaker,
            take_id=None,
            is_partial=True,
        )
    else:
        topic = ASR_FINAL_CH1 if body.ch == 1 else ASR_FINAL_CH2
        payload = AsrFinalPayload(
            text=body.text,
            start_frame=frame_ts,
            end_frame=frame_ts,
            speaker=body.speaker,
            take_id=None,
            is_partial=False,
        )

    orch.publish(topic, payload)
    return {"status": "ok"}
