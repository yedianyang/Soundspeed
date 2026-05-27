"""L2 Pipeline：台词 diff 检测（ticket 1.G）。

根据 ch1 转录记录与剧本台词，调用 LLM 生成逐行对比结果（script_diff）。

公共 API：
  L2Input       输入 dataclass（frozen）
  L2Output      输出 dataclass（frozen）
  LineMatch     逐行比对结果（frozen）
  L2ParseError  LLM 输出解析失败异常
  run_l2_take   纯异步函数，执行一次 L2 Pipeline

设计依据：
  docs/specs/2026-05-27-l2-pipeline.md §3-§7
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.llm.service import LLMService


# ---------------------------------------------------------------------------
# 异常
# ---------------------------------------------------------------------------


class L2ParseError(Exception):
    """LLM 输出解析失败。

    cause 串联原始异常（json.JSONDecodeError / KeyError 等），
    调用方可通过 e.cause 取原始异常细节。
    """

    def __init__(self, message: str, cause: Exception | None = None) -> None:
        super().__init__(message)
        self.cause = cause


# ---------------------------------------------------------------------------
# 数据类型
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LineMatch:
    """单行台词比对结果。

    diff_type 取值：match / missing / substitution / insertion。
    insertion 类型 line_no 固定为 -1（无对应剧本行）。
    """

    line_no: int
    diff_type: str
    detail: str | None


@dataclass(frozen=True)
class L2Input:
    """L2 Pipeline 输入（caller 组装后传入）。

    transcript_segments 总字符数上限 2500 字符（§3 决策），
    超限时 pipeline 截断保留末尾段落。

    script_lines 总字符数上限 1000 字符，由 caller（1.H）在组装时截断，
    pipeline 不处理 script_lines 截断。
    """

    take_id: int
    scene_id: int
    take_number: int
    transcript_segments: list[dict]
    script_lines: list[dict]
    previous_notes: list[str]


@dataclass(frozen=True)
class L2Output:
    """L2 Pipeline 输出。

    script_diff_summary → takes.script_diff（JSON 顶层字段，由 caller 写库）。
    line_matches → take_line_matches 表（由 caller 1.H 写库）。
    insertion 类型 line_no=-1 时，caller 跳过写 take_line_matches（§4 决策）。
    """

    script_diff_summary: str | None
    line_matches: list[LineMatch]


# ---------------------------------------------------------------------------
# 核心函数
# ---------------------------------------------------------------------------

_TRANSCRIPT_CHAR_LIMIT = 2500
_VALID_DIFF_TYPES = frozenset({"match", "missing", "substitution", "insertion"})


async def run_l2_take(
    input_data: L2Input,
    llm_service: "LLMService",
    timeout: float = 60.0,
) -> L2Output:
    """执行一次 L2 Pipeline：台词 diff 检测。

    Args:
        input_data: L2 输入，含转录记录、剧本行、历史偏差。
        llm_service: 注入的 LLMService 实例（不调 get_service()）。
        timeout: 最大等待时间（含排队 + 推理），默认 60s。

    Returns:
        L2Output，含 script_diff_summary 和 line_matches。

    Raises:
        L2ParseError: LLM 输出非合法 JSON / 字段缺失 / 枚举值非法 / 响应为空。
        asyncio.TimeoutError: 排队 + 推理总耗时超 timeout（由 LLMService 抛出，不吞）。
    """
    raise NotImplementedError
