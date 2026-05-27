"""Orchestrator：同步 pub/sub 事件分发中心。

内置 handler（asr.final.ch1 / asr.final.ch2）在构造时通过 subscribe 注册，
直接写入 DAL transcript_segments 表。
任一 handler 抛异常时记 ERROR 日志并继续调用剩余 handler，不向 publish 调用方传播。

工厂函数 create_orchestrator 支持依赖注入（llm_service / l2_runner），
供 1.H take handler 使用。老签名 Orchestrator(dal, session) 零改动。
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from backend.core.events import ASR_FINAL_CH1, ASR_FINAL_CH2, AsrFinalPayload
from backend.core.session import SessionState
from backend.db.dal import DAL

Handler = Callable[[object], None]

logger = logging.getLogger(__name__)


@dataclass
class Dependencies:
    """Orchestrator 可选外部依赖，用于 1.H take handler。

    llm_service: LLMService 实例，注入到 l2_runner。
    l2_runner: 异步可调用，签名 (L2Input, LLMService) -> Awaitable[L2Output]。
    """

    llm_service: Any = field(default=None)
    l2_runner: Any = field(default=None)


class Orchestrator:
    """同步事件总线，持有 DAL 与 SessionState，内置 ASR segment handler。"""

    def __init__(
        self,
        dal: DAL,
        session: SessionState | None = None,
        *,
        llm_service: Any = None,
        l2_runner: Any = None,
    ) -> None:
        self.dal = dal
        self.session: SessionState = session if session is not None else SessionState()
        self._deps = Dependencies(llm_service=llm_service, l2_runner=l2_runner)
        self._handlers: dict[str, list[Handler]] = {}
        self._register_builtin_handlers()

    def subscribe(self, event_type: str, handler: Handler) -> None:
        """注册 event_type 的 handler，同一 event_type 可注册多个，按 subscribe 顺序调用。"""
        self._handlers.setdefault(event_type, []).append(handler)

    def publish(self, event_type: str, payload: object) -> None:
        """发布事件，逐个同步调用已注册 handler。

        handler 抛异常时记 ERROR 日志 + 继续后续 handler，不向调用方传播。
        未注册的 event_type 为 no-op。
        """
        handlers = self._handlers.get(event_type, [])
        for handler in handlers:
            try:
                handler(payload)
            except Exception:
                logger.exception("handler error for event %r", event_type)

    def _register_builtin_handlers(self) -> None:
        """注册内置 handler（asr.final.ch1 / asr.final.ch2）。"""
        self.subscribe(
            ASR_FINAL_CH1,
            lambda p: self._on_asr_final(p, ch=1, force_speaker_none=False),
        )
        self.subscribe(
            ASR_FINAL_CH2,
            lambda p: self._on_asr_final(p, ch=2, force_speaker_none=True),
        )

    def _resolve_take_id(self, payload_take_id: int | None, event_label: str) -> int | None:
        """payload.take_id 优先用，None 时回退 session.take_id。

        payload 与 session 都非 None 且不匹配时记 warning（跨 take 边界迟到 segment），仍按 payload 写库。
        """
        session_take_id = self.session.take_id
        if payload_take_id is None:
            return session_take_id

        if session_take_id is not None and payload_take_id != session_take_id:
            logger.warning(
                "%s: payload.take_id=%d != session.take_id=%d, using payload (cross-take boundary)",
                event_label,
                payload_take_id,
                session_take_id,
            )
        return payload_take_id

    def _on_asr_final(
        self,
        payload: object,
        *,
        ch: int,
        force_speaker_none: bool,
    ) -> None:
        """处理 asr.final.chN 事件：take_active 时写 transcript_segments。

        ch1 保留 payload.speaker；ch2 强制 speaker=None（diarization 只跑 ch1）。
        """
        assert isinstance(payload, AsrFinalPayload)
        event_label = f"asr.final.ch{ch}"
        if not self.session.take_active:
            logger.debug("%s: take inactive, skipping segment write", event_label)
            return

        target_take_id = self._resolve_take_id(payload.take_id, event_label)
        if target_take_id is None:
            return

        self.dal.insert_segment(
            take_id=target_take_id,
            ch=ch,
            speaker=None if force_speaker_none else payload.speaker,
            text=payload.text,
            start_frame=payload.start_frame,
            end_frame=payload.end_frame,
        )


def create_orchestrator(
    dal: DAL,
    session: SessionState | None = None,
    *,
    llm_service: Any = None,
    l2_runner: Any = None,
) -> Orchestrator:
    """模块级工厂函数，供生产代码与测试注入依赖。

    老签名 Orchestrator(dal, session) 零改动，此函数提供更明确的依赖注入入口。
    """
    return Orchestrator(dal, session, llm_service=llm_service, l2_runner=l2_runner)
