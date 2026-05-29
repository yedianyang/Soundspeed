"""take 端点（contract C3）：POST /api/v1/take/start | /api/v1/take/end。

请求契约（design §3）：
  - POST /take/start body = {scene_id:int, shot:str|null}
  - POST /take/end   body 空（end_ts 服务端生成）
  - ts 一律服务端 time.time() 生成
  - 映射到 TakeStartPayload(scene_id, shot, start_ts) / TakeEndPayload(end_ts)

端点必须 async def（决策 1）：
  take.end 的 fire-and-forget L2 靠 asyncio.get_running_loop()
  （orchestrator.py:214，仅在注入 llm_service/l2_runner 时走到）。async 端点由
  FastAPI 在 event loop 内直接 await，get_running_loop() 能拿到 loop，L2 task 被调度。
  若改成 def，FastAPI 丢线程池跑 → 无 running loop → get_running_loop() 抛 RuntimeError
  → 被 publish() 吞掉（orchestrator.py:79-82）→ 生产 L2 静默不触发。
  不能把 publish offload 到线程池（会丢 loop ref）。接受 SQLite 同步写带来的短暂
  loop 阻塞（此规模可接受）。

本切片不做 take/end 的 NP Pipeline（architecture §10.1 「L2+NP」中的 NP 属后续 ticket）。
"""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from backend.api.auth import require_admin
from backend.core.events import TAKE_END, TAKE_START, TakeEndPayload, TakeStartPayload

router = APIRouter(prefix="/api/v1", tags=["takes"])


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
