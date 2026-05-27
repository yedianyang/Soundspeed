"""Orchestrator：同步 pub/sub 事件分发中心。

内置 handler（asr.final.ch1 / asr.final.ch2）在构造时通过 subscribe 注册，
直接写入 DAL transcript_segments 表。
任一 handler 抛异常时记 ERROR 日志并继续调用剩余 handler，不向 publish 调用方传播。
"""
from __future__ import annotations

import logging
from collections.abc import Callable

from backend.db.dal import DAL
from backend.core.session import SessionState

Handler = Callable[[object], None]

logger = logging.getLogger(__name__)


class Orchestrator:
    """同步事件总线，持有 DAL 与 SessionState，内置 ASR segment handler。"""

    def __init__(self, dal: DAL, session: SessionState | None = None) -> None:
        self.dal = dal
        self.session: SessionState = session if session is not None else SessionState()
        self._handlers: dict[str, list[Handler]] = {}
        # TODO 绿 commit 实现内置 handler 注册
        # _register_builtin_handlers 红阶段不调用

    def subscribe(self, event_type: str, handler: Handler) -> None:
        """注册 event_type 的 handler，同一 event_type 可注册多个，按 subscribe 顺序调用。"""
        raise NotImplementedError("1.E green")

    def publish(self, event_type: str, payload: object) -> None:
        """发布事件，逐个同步调用已注册 handler。

        handler 抛异常时记 ERROR 日志 + 继续后续 handler，不向调用方传播。
        未注册的 event_type 为 no-op。
        """
        raise NotImplementedError("1.E green")

    def _register_builtin_handlers(self) -> None:
        """注册内置 handler（asr.final.ch1 / asr.final.ch2）。绿 commit 实现。"""
        raise NotImplementedError("1.E green")
