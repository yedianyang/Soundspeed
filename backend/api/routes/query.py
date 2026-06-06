"""QP 直连入口（spec §10）：POST /api/v1/query + 共享 run_qp_and_broadcast。

run_qp_and_broadcast：跑 QP 循环 → 广播 qp.answer.{conn_id} → 返回答案。
post_query（直连 demo，同步返回）与入口调度器 query 分支（fire-and-forget）共用此 helper。
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from backend.api.auth import require_admin
from backend.core.events import QP_ANSWER, QpAnswerPayload
from backend.pipelines.qp_query import run_qp_query

if TYPE_CHECKING:
    from backend.db.dal import DAL
    from backend.llm.service import LLMService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["query"])


class QueryRequest(BaseModel):
    text: str
    conn_id: str


async def run_qp_and_broadcast(
    text: str,
    conn_id: str,
    *,
    dal: "DAL",
    service: "LLMService",
    cm,
) -> str:
    """跑 QP 两步走循环 → 广播 qp.answer.{conn_id} → 返回答案。

    run_qp_query 把 TimeoutError 等放行到这里，兜成友好自然语言、不抛穿（caller 可能是
    fire-and-forget task，没有人接异常）。CancelledError（BaseException）不在此捕获。
    """
    try:
        answer = await run_qp_query(text=text, dal=dal, service=service)
    except Exception as exc:  # noqa: BLE001
        logger.warning("qp query 失败 conn_id=%s: %r", conn_id, exc)
        answer = "抱歉，这次查询出错了，请换种说法再试一次。"
    cm.broadcast(
        f"{QP_ANSWER}.{conn_id}",
        QpAnswerPayload(connection_id=conn_id, answer_text=answer),
    )
    return answer


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
    answer = await run_qp_and_broadcast(
        body.text,
        body.conn_id,
        dal=orchestrator.dal,
        service=service,
        cm=request.app.state.connection_manager,
    )
    return {"status": "ok", "answer": answer}
