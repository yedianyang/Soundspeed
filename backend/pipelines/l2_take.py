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

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

from backend.llm.config import TASK_CONFIG

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
# 内部常量
# ---------------------------------------------------------------------------

_TRANSCRIPT_CHAR_LIMIT = 2500
_VALID_DIFF_TYPES = frozenset({"match", "missing", "substitution", "insertion"})


# ---------------------------------------------------------------------------
# Prompt 构建（§5）
# ---------------------------------------------------------------------------


def _build_system_prompt() -> str:
    """从 TASK_CONFIG["l2_take"]["system"] 读取模板，追加格式约束。"""
    base = TASK_CONFIG["l2_take"]["system"]
    format_constraint = (
        "\n\n输出格式要求（严格遵守）：\n"
        "- 只输出合法 JSON，不要 markdown 代码块，不要注释，不要额外解释。\n"
        "- JSON schema：\n"
        "  {\n"
        '    "script_diff_summary": "<str 或 null>",\n'
        '    "line_matches": [\n'
        '      {"line_no": <int>, "diff_type": "<match|missing|substitution|insertion>", "detail": "<str 或 null>"}\n'
        "    ]\n"
        "  }\n"
        "- line_matches 只列出 script_lines 提供的行，不自创行号。\n"
        "- insertion 类型（演员台词剧本无对应行）line_no 固定填 -1。"
    )
    return f"{base}{format_constraint}"


def _build_script_lines_block(script_lines: list[dict]) -> str:
    """格式化剧本行为 [行N] 角色：台词 文本。"""
    if not script_lines:
        return "（无剧本，跳过偏差检测）"
    lines = []
    for item in script_lines:
        character = item.get("character")
        char_label = character if character is not None else "（舞台指示）"
        lines.append(f"[行{item['line_no']}] {char_label}：{item['text']}")
    return "\n".join(lines)


def _build_transcript_block(segments: list[dict], truncated: bool) -> str:
    """格式化 transcript_segments 为 [speaker] text 文本。

    truncated=True 时在头部加截断警告。
    """
    if not segments:
        return "（无转录片段）"
    lines = []
    if truncated:
        lines.append("[WARNING: transcript truncated to 2500 chars, earliest segments dropped]")
    for seg in segments:
        speaker = seg.get("speaker")
        label = speaker if speaker is not None else "未知说话人"
        lines.append(f"[{label}] {seg['text']}")
    return "\n".join(lines)


def _truncate_segments(segments: list[dict]) -> tuple[list[dict], bool]:
    """截断 transcript_segments 使总字符数不超过 _TRANSCRIPT_CHAR_LIMIT。

    保留末尾段落（take 末尾更可能包含关键台词，§3 决策）。
    返回 (截断后的 segments, 是否发生了截断)。
    """
    total_chars = sum(len(s["text"]) for s in segments)
    if total_chars <= _TRANSCRIPT_CHAR_LIMIT:
        return segments, False

    # 从末尾开始累加，直到达到上限
    result: list[dict] = []
    accumulated = 0
    for seg in reversed(segments):
        text_len = len(seg["text"])
        if accumulated + text_len > _TRANSCRIPT_CHAR_LIMIT:
            break
        result.insert(0, seg)
        accumulated += text_len

    return result, True


def _build_user_message(input_data: L2Input) -> str:
    """组装 user message（§5 模板）。"""
    truncated_segments, was_truncated = _truncate_segments(input_data.transcript_segments)

    script_block = _build_script_lines_block(input_data.script_lines)
    transcript_block = _build_transcript_block(truncated_segments, was_truncated)

    previous_section = ""
    if input_data.previous_notes:
        n = len(input_data.previous_notes)
        notes_text = "\n".join(input_data.previous_notes)
        previous_section = f"\n## 历史偏差参考（最近 {n} 条）\n\n{notes_text}\n"

    return (
        f"## 剧本台词（场次 {input_data.scene_id}）\n\n"
        f"{script_block}\n\n"
        f"## Take {input_data.take_number} 转录记录\n\n"
        f"{transcript_block}\n"
        f"{previous_section}\n"
        "请按要求输出 JSON 偏差报告。"
    )


# ---------------------------------------------------------------------------
# 输出解析（§6）
# ---------------------------------------------------------------------------


def _strip_markdown_fence(text: str) -> str:
    """剥除 ```json ... ``` 或 ``` ... ``` 包裹，只剥一层。"""
    stripped = text.strip()
    if stripped.startswith("```"):
        # 找第一行结束和最后一个 ``` 开始的位置
        first_newline = stripped.find("\n")
        if first_newline == -1:
            return stripped
        content_start = first_newline + 1
        # 从末尾找最后一个 ```
        last_fence = stripped.rfind("```")
        if last_fence > content_start:
            return stripped[content_start:last_fence].strip()
    return stripped


def _parse_llm_output(raw_text: str) -> L2Output:
    """解析 LLM 输出文本为 L2Output。

    解析流程：strip_markdown_fence → json.loads → validate_fields → L2Output。

    Raises:
        L2ParseError: 空响应 / JSON 解析失败 / 字段缺失 / 枚举值非法 / line_no 非整数。
    """
    if not raw_text or not raw_text.strip():
        raise L2ParseError("LLM returned empty response")

    cleaned = _strip_markdown_fence(raw_text)

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise L2ParseError("LLM response is not valid JSON", cause=exc) from exc

    if not isinstance(data, dict):
        raise L2ParseError("LLM response JSON is not a dict object")

    # 字段缺失检查（key 完全不存在时抛错，null 值合法）
    if "script_diff_summary" not in data:
        raise L2ParseError("LLM response missing required field: 'script_diff_summary'")
    if "line_matches" not in data:
        raise L2ParseError("LLM response missing required field: 'line_matches'")

    script_diff_summary: str | None = data["script_diff_summary"]
    raw_matches = data["line_matches"]

    if not isinstance(raw_matches, list):
        raise L2ParseError("LLM response 'line_matches' is not a list")

    line_matches: list[LineMatch] = []
    for i, item in enumerate(raw_matches):
        if not isinstance(item, dict):
            raise L2ParseError(f"line_matches[{i}] is not a dict")

        # line_no 类型校验
        line_no = item.get("line_no")
        if not isinstance(line_no, int):
            raise L2ParseError(
                f"line_matches[{i}].line_no is not an integer: {line_no!r}"
            )

        # diff_type 枚举校验
        diff_type = item.get("diff_type")
        if diff_type not in _VALID_DIFF_TYPES:
            raise L2ParseError(
                f"line_matches[{i}].diff_type invalid: {diff_type!r}, "
                f"must be one of {sorted(_VALID_DIFF_TYPES)}"
            )

        detail: str | None = item.get("detail")

        line_matches.append(LineMatch(line_no=line_no, diff_type=diff_type, detail=detail))

    return L2Output(script_diff_summary=script_diff_summary, line_matches=line_matches)


# ---------------------------------------------------------------------------
# 核心函数
# ---------------------------------------------------------------------------


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
    system_prompt = _build_system_prompt()
    user_message = _build_user_message(input_data)

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
    ]

    # asyncio.TimeoutError 从 infer 传出，pipeline 不捕获（让 caller 感知）
    raw_text: str = await llm_service.infer(
        messages,
        task_type="l2_take",
        priority=2,
        timeout=timeout,
    )

    return _parse_llm_output(raw_text)
