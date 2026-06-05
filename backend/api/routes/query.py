"""QP 直连入口（spec §10）：POST /api/v1/query。

请求体带发起方 conn_id；QP 完成后把答案广播到 topic qp.answer.{conn_id}
（复用现有 ConnectionManager 广播 seam，客户端按 conn_id 前缀认领，spec §9）。
v1 同时同步返回答案，便于 demo / 测试（不依赖 WS 客户端）。
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from backend.api.auth import require_admin
from backend.core.events import QP_ANSWER, QpAnswerPayload
from backend.pipelines.qp_query import run_qp_query

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["query"])


class QueryRequest(BaseModel):
    text: str
    conn_id: str


@router.post("/query")
async def post_query(
    body: QueryRequest,
    request: Request,
    _: None = Depends(require_admin),
) -> dict:
    """跑 QP 两步走循环 → 广播 qp.answer.{conn_id} + 同步返回答案。"""
    orchestrator = request.app.state.orchestrator
    service = getattr(request.app.state, "llm_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="LLM service 未就绪")

    # run_tool_loop 把 TimeoutError 等放行给这里（异常契约见 qp_query.run_tool_loop docstring）。
    # 兜成友好自然语言答案、不让 route 500——demo/前端拿到的始终是一句话。CancelledError
    # （取消，BaseException 非 Exception）不在此捕获，照常向上传播。
    try:
        answer = await run_qp_query(text=body.text, dal=orchestrator.dal, service=service)
    except Exception as exc:  # noqa: BLE001
        logger.warning("qp query 失败 conn_id=%s: %r", body.conn_id, exc)
        answer = "抱歉，这次查询出错了，请换种说法再试一次。"

    cm = request.app.state.connection_manager
    cm.broadcast(
        f"{QP_ANSWER}.{body.conn_id}",
        # conn_id（API/WS topic 缩写）→ connection_id（payload 字段，对齐 QueryRequestPayload）
        QpAnswerPayload(connection_id=body.conn_id, answer_text=answer),
    )
    return {"status": "ok", "answer": answer}
