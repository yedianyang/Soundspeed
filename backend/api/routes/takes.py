"""take 端点（contract C3）：POST /api/v1/take/start | /api/v1/take/end + GET 端点（1.J-1.L）。

请求契约（design §3）：
  - POST /take/start body = {scene_id:int, shot:str|null}
  - POST /take/end   body 空（end_ts 服务端生成）
  - ts 一律服务端 time.time() 生成
  - 映射到 TakeStartPayload(scene_id, shot, start_ts) / TakeEndPayload(end_ts)

端点必须 async def（决策 1）：
  take.end 的 fire-and-forget L2 靠 asyncio.get_running_loop()
  （orchestrator.py，仅在注入 llm_service/l2_runner 时走到）。async 端点由
  FastAPI 在 event loop 内直接 await，get_running_loop() 能拿到 loop，L2 task 被调度。
  若改成 def，FastAPI 丢线程池跑 → 无 running loop → get_running_loop() 抛 RuntimeError
  → 被 publish() 吞掉 → 生产 L2 静默不触发。
  GET 端点同理：DAL 单连接 check_same_thread=False，同步路由跑线程池会与 event-loop 线程
  上的 L2 async 任务并发操作同一 _conn（SQLite 对同一连接对象的并发调用不安全）。

本切片不做 take/end 的 NP Pipeline（architecture §10.1 「L2+NP」中的 NP 属后续 ticket）。

TakeDTO 有意省略 performer_issues / audio_quality（codex P2）：
  这两个字段属 NP Pipeline 输出，1.J-1.L 不暴露，故意不进 DTO。
  前端从 script_diff.line_matches 读行比对（codex P1），不单列 line_matches 字段。
"""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict

from backend.api.auth import require_admin
from backend.core.events import TAKE_END, TAKE_START, TakeEndPayload, TakeStartPayload

router = APIRouter(prefix="/api/v1", tags=["takes"])


# ── DTO 定义（1.J-1.L GET 端点响应结构）──────────────────────────────────────


class SegmentOut(BaseModel):
    """transcript_segments 行的响应投影（spec §2.1 shape）。

    有意省略 take_id：前端 TranscriptSegmentDTO 不含此字段，减少响应体冗余。
    """

    segment_id: int
    ch: int
    speaker: str | None
    text: str
    start_frame: int
    end_frame: int

    model_config = ConfigDict(from_attributes=True)


class TakeDTO(BaseModel):
    """take 行的响应投影（11 字段，有意省略 performer_issues / audio_quality）。

    有意省略字段：performer_issues、audio_quality 属 NP Pipeline 输出，1.J-1.L 不暴露。
    前端从 script_diff.line_matches 读行比对（codex P1），不单列 line_matches 字段。
    """

    take_id: int
    scene_id: int
    take_number: int
    shot: str | None
    start_ts: float
    end_ts: float | None
    status: str
    script_diff: dict | None
    notes: str | None
    created_at: float
    updated_at: float

    model_config = ConfigDict(from_attributes=True)


class TakeDetailDTO(TakeDTO):
    """take 详情：TakeDTO + segments 列表。"""

    segments: list[SegmentOut]


# ── POST 端点（contract C3）────────────────────────────────────────────────────


class TakeStartBody(BaseModel):
    """POST /take/start 请求体。"""

    scene_id: int
    shot: str | None = None


@router.post("/take/start")
async def take_start(
    body: TakeStartBody,
    request: Request,
    _: None = Depends(require_admin),
) -> dict[str, str]:
    """起一个 take：服务端生成 start_ts，publish TAKE_START。"""
    orchestrator = request.app.state.orchestrator
    payload = TakeStartPayload(
        scene_id=body.scene_id,
        shot=body.shot,
        start_ts=time.time(),
    )
    orchestrator.publish(TAKE_START, payload)
    return {"status": "ok"}


@router.post("/take/end")
async def take_end(
    request: Request,
    _: None = Depends(require_admin),
) -> dict[str, str]:
    """结束当前 take：服务端生成 end_ts，publish TAKE_END（body 空）。

    async def 是决策 1 的硬约束——见模块 docstring。
    """
    orchestrator = request.app.state.orchestrator
    payload = TakeEndPayload(end_ts=time.time())
    orchestrator.publish(TAKE_END, payload)
    return {"status": "ok"}


# ── GET 端点（1.J-1.L）────────────────────────────────────────────────────────


@router.get("/takes")
async def list_takes(
    request: Request,
    scene_id: int | None = None,
    _: None = Depends(require_admin),
) -> dict[str, list[TakeDTO]]:
    """列出 take（可按 scene_id 过滤），不含 segments（避免 N+1）。

    省略 scene_id 返回全部（dal.list_takes(None)）。
    """
    dal = request.app.state.orchestrator.dal
    takes = dal.list_takes(scene_id)
    return {"takes": [TakeDTO.model_validate(t, from_attributes=True) for t in takes]}


@router.get("/takes/{take_id}")
async def get_take(
    take_id: int,
    request: Request,
    _: None = Depends(require_admin),
) -> TakeDetailDTO:
    """返回 take 详情（含 segments），take 不存在 → 404。"""
    dal = request.app.state.orchestrator.dal
    take = dal.get_take(take_id)
    if take is None:
        raise HTTPException(status_code=404, detail="take not found")
    segments = dal.list_segments(take_id)
    return TakeDetailDTO.model_validate(
        {**take.__dict__, "segments": segments},
        from_attributes=True,
    )
