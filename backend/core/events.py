"""事件类型常量与 payload dataclass（contract C1 + C3）。

所有 Orchestrator 内部事件在此定义。事件类型字符串值与 contract C1 / WS topic 命名完全一致。
Payload 使用 frozen=True 的 dataclass，防止 handler 间互相篡改。
"""
from __future__ import annotations

from dataclasses import dataclass

# ── 事件类型常量 ──────────────────────────────────────────────────────────────

# ASR 事件（contract C1）
ASR_PARTIAL_CH1 = "asr.partial.ch1"
ASR_PARTIAL_CH2 = "asr.partial.ch2"
ASR_FINAL_CH1 = "asr.final.ch1"
ASR_FINAL_CH2 = "asr.final.ch2"

# Take 事件（contract C3：FastAPI 调用 publish）
TAKE_START = "take.start"
TAKE_END = "take.end"
TAKE_CHANGED = "take.changed"

# 其他事件（本 ticket 只定义常量，不注册 handler）
MANUAL_MARK = "manual.mark"
QUERY_REQUEST = "query.request"
SCRIPT_UPLOAD = "script.upload"


# ── Payload dataclass ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class AsrPartialPayload:
    """asr.partial.ch1 / asr.partial.ch2 的 payload。"""

    text: str
    start_frame: int
    end_frame: int
    speaker: str | None
    take_id: int | None
    is_partial: bool


@dataclass(frozen=True)
class AsrFinalPayload:
    """asr.final.ch1 / asr.final.ch2 的 payload。"""

    text: str
    start_frame: int
    end_frame: int
    speaker: str | None
    take_id: int | None
    is_partial: bool


@dataclass(frozen=True)
class TakeStartPayload:
    """take.start 的 payload（contract C3）。"""

    scene_id: int
    shot: str | None
    start_ts: float


@dataclass(frozen=True)
class TakeEndPayload:
    """take.end 的 payload。"""

    end_ts: float


@dataclass(frozen=True)
class ManualMarkPayload:
    """manual.mark 的 payload。"""

    mark_type: str
    note: str | None
    ts: float


@dataclass(frozen=True)
class QueryRequestPayload:
    """query.request 的 payload。"""

    connection_id: str
    query: str


@dataclass(frozen=True)
class ScriptUploadPayload:
    """script.upload 的 payload。"""

    scene_id: int
    raw_text: str


@dataclass(frozen=True)
class TakeChangedPayload:
    """take.changed 的 payload（1.H L2 pipeline 完成后 publish）。

    status 取值与 takes.status 一致：'keeper' | 'ng' | 'hold' | 'tbd'。
    script_diff=None 表示 L2 未完成或失败（降级状态）。
    """

    take_id: int
    scene_id: int
    take_number: int
    status: str
    script_diff: dict | None
