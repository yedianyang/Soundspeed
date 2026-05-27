"""Orchestrator：同步 pub/sub 事件分发中心。

内置 handler（asr.final.ch1 / asr.final.ch2）在构造时通过 subscribe 注册，
直接写入 DAL transcript_segments 表。
任一 handler 抛异常时记 ERROR 日志并继续调用剩余 handler，不向 publish 调用方传播。

工厂函数 create_orchestrator 支持依赖注入（llm_service / l2_runner），
供 1.H take handler 使用。老签名 Orchestrator(dal, session) 零改动。
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from backend.core.events import (
    ASR_FINAL_CH1,
    ASR_FINAL_CH2,
    TAKE_CHANGED,
    TAKE_END,
    TAKE_START,
    AsrFinalPayload,
    TakeChangedPayload,
    TakeEndPayload,
    TakeStartPayload,
)
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
        self._l2_task: asyncio.Task[None] | None = None  # 最近一次 L2 后台 task，供测试 await
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
        """注册内置 handler（asr.final.ch1 / asr.final.ch2 / take.start / take.end）。"""
        self.subscribe(
            ASR_FINAL_CH1,
            lambda p: self._on_asr_final(p, ch=1, force_speaker_none=False),
        )
        self.subscribe(
            ASR_FINAL_CH2,
            lambda p: self._on_asr_final(p, ch=2, force_speaker_none=True),
        )
        self.subscribe(TAKE_START, self._on_take_start)
        self.subscribe(TAKE_END, self._on_take_end)

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

    def _on_take_start(self, payload: object) -> None:
        """处理 take.start 事件：写 DAL + 更新 SessionState + publish take.changed。"""
        assert isinstance(payload, TakeStartPayload)

        scene_id = payload.scene_id
        # take_number = 当前 scene 已有 take 数量 + 1
        existing = self.dal.list_takes(scene_id)
        take_number = len(existing) + 1

        take_id = self.dal.start_take(
            scene_id=scene_id,
            take_number=take_number,
            start_ts=payload.start_ts,
            shot=payload.shot,
        )
        self.session.take_start(
            take_id=take_id,
            take_number=take_number,
            start_ts=payload.start_ts,
            shot=payload.shot,
        )

        self.publish(
            TAKE_CHANGED,
            TakeChangedPayload(
                take_id=take_id,
                scene_id=scene_id,
                take_number=take_number,
                status="tbd",
                script_diff=None,
            ),
        )

    def _on_take_end(self, payload: object) -> None:
        """处理 take.end 事件：写 DAL + publish take.changed（同步）+ fire-and-forget L2。

        同步阶段 publish 第一次 take.changed（script_diff=None）。
        异步阶段 L2 完成后（或失败后）publish 第二次。
        """
        assert isinstance(payload, TakeEndPayload)

        take_id = self.session.take_id
        if take_id is None:
            logger.warning("take.end: session.take_id is None, skipping")
            return

        self.session.take_end()
        self.dal.end_take(take_id=take_id, end_ts=payload.end_ts, status="tbd")

        # 第一次 publish（同步，script_diff=None）
        self.publish(
            TAKE_CHANGED,
            TakeChangedPayload(
                take_id=take_id,
                scene_id=self.session.scene_id or 0,
                take_number=self.session.take_number,
                status="tbd",
                script_diff=None,
            ),
        )

        # fire-and-forget L2（需要 event loop）
        loop = asyncio.get_running_loop()  # 无 loop 时抛 RuntimeError，不降级
        scene_id = self.session.scene_id or 0
        take_number = self.session.take_number
        self._l2_task = loop.create_task(self._run_l2_async(take_id))
        # callback 用闭包绑定 take_id/scene_id/take_number，避免 session 被后续 take.start 覆盖
        self._l2_task.add_done_callback(
            lambda t: self._l2_done_callback(t, take_id=take_id, scene_id=scene_id, take_number=take_number)
        )

    async def _run_l2_async(self, take_id: int) -> None:
        """后台异步 L2 Pipeline：组装 L2Input → 调 l2_runner → 写库 → publish take.changed。

        L2ParseError / asyncio.TimeoutError 等异常不在此捕获，传到 task.exception() 由 callback 处理。
        """
        from backend.pipelines.l2_take import L2Input

        take = self.dal.get_take(take_id)
        if take is None:
            raise RuntimeError(f"_run_l2_async: take_id={take_id} not found in DB")

        # 收集 ch1 转录记录
        segments = self.dal.list_segments(take_id, ch=1)
        transcript_segments = [
            {
                "speaker": s.speaker,
                "text": s.text,
                "start_frame": s.start_frame,
                "end_frame": s.end_frame,
            }
            for s in segments
        ]

        # 收集剧本行（如果 session 有活跃 script）
        script_lines: list[dict] = []
        if self.session.scene_id is not None:
            script_info = self.dal.get_latest_script(self.session.scene_id)
            if script_info is not None:
                script_lines = self.dal.list_script_lines(script_info["script_id"])

        # 从历史 take 提取 previous_notes
        previous_notes: list[str] = []
        if self.session.scene_id is not None:
            history = self.dal.list_takes(self.session.scene_id)
            for t in history:
                if t.take_id == take_id:
                    continue  # 跳过当前 take
                if t.script_diff and isinstance(t.script_diff, dict):
                    summary = t.script_diff.get("script_diff_summary")
                    if summary:
                        previous_notes.append(str(summary))

        input_data = L2Input(
            take_id=take_id,
            scene_id=take.scene_id,
            take_number=take.take_number,
            transcript_segments=transcript_segments,
            script_lines=script_lines,
            previous_notes=previous_notes,
        )

        l2_output = await self._deps.l2_runner(input_data, self._deps.llm_service)

        # 写库：script_diff
        script_diff_dict = {
            "script_diff_summary": l2_output.script_diff_summary,
            "line_matches": [
                {"line_no": m.line_no, "diff_type": m.diff_type, "detail": m.detail}
                for m in l2_output.line_matches
            ],
        }
        self.dal.update_take_l2_output(take_id, script_diff_dict)

        # 写库：take_line_matches（需要 line_no → line_id 映射）
        line_no_to_id: dict[int, int] = {
            ln["line_no"]: ln["line_id"] for ln in script_lines
        }
        matches_for_dal: list[dict] = []
        for m in l2_output.line_matches:
            if m.line_no == -1:
                continue  # insertion，跳过（DAL 会再过滤，双重保险）
            line_id = line_no_to_id.get(m.line_no)
            if line_id is None:
                logger.warning(
                    "_run_l2_async: line_no=%d not found in script_lines, skipping",
                    m.line_no,
                )
                continue
            matches_for_dal.append(
                {"line_no": m.line_no, "line_id": line_id, "diff_type": m.diff_type, "detail": m.detail}
            )
        if matches_for_dal:
            self.dal.insert_take_line_matches(take_id, matches_for_dal)

        # 第二次 publish（含 script_diff）
        self.publish(
            TAKE_CHANGED,
            TakeChangedPayload(
                take_id=take_id,
                scene_id=take.scene_id,
                take_number=take.take_number,
                status="tbd",
                script_diff=script_diff_dict,
            ),
        )

    def _l2_done_callback(
        self, task: Any, *, take_id: int, scene_id: int, take_number: int
    ) -> None:
        """L2 task done callback：仅在异常时 log + publish 降级 take.changed。

        成功路径：_run_l2_async 已 publish 第二次 take.changed，callback 无需操作。
        失败路径：记 WARNING + publish 降级 take.changed（script_diff=None）。

        take_id / scene_id / take_number 由 add_done_callback lambda 闭包绑定，
        避免 session 被后续 take.start 覆盖（race condition 防护）。
        """
        if task.cancelled():
            return
        exc = task.exception()
        if exc is None:
            return  # 成功路径，_run_l2_async 已处理
        logger.warning("L2 pipeline failed for task, publishing degraded take.changed: %r", exc)
        self.publish(
            TAKE_CHANGED,
            TakeChangedPayload(
                take_id=take_id,
                scene_id=scene_id,
                take_number=take_number,
                status="tbd",
                script_diff=None,
            ),
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
