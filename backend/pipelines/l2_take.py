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
from backend.pipelines.l2_constants import _VALID_DIFF_TYPES  # noqa: F401  # re-exported

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
class CorrectedSegment:
    """单个转录片段的错别字修正结果（v0.2 新增）。

    idx 指向 L2Input.transcript_segments 截断后列表的下标（从 0 开始）。
    只对真正有修改的 segment 输出；未改动的 segment 不出现。
    """

    idx: int
    original: str
    corrected: str


@dataclass(frozen=True)
class L2Output:
    """L2 Pipeline 输出。

    script_diff_summary → takes.script_diff（JSON 顶层字段，由 caller 写库）。
    line_matches → take_line_matches 表（由 caller 1.H 写库）。
    corrected_segments → takes.script_diff（JSON 字段，由 caller 写库）。
    insertion 类型 line_no=-1 时，caller 跳过写 take_line_matches（§4 决策）。
    """

    script_diff_summary: str | None
    line_matches: list[LineMatch]
    corrected_segments: list[CorrectedSegment]


# ---------------------------------------------------------------------------
# 内部常量
# ---------------------------------------------------------------------------

_TRANSCRIPT_CHAR_LIMIT = 2500
# _VALID_DIFF_TYPES 从 l2_constants 导入（module 顶部 import 语句），此处不重复定义。
# 测试文件 `from backend.pipelines.l2_take import _VALID_DIFF_TYPES` 仍然有效（re-export）。


# ---------------------------------------------------------------------------
# Prompt 构建（§5）
# ---------------------------------------------------------------------------


def _build_system_prompt(*, no_script: bool = False) -> str:
    """返回对应 task_type 的 system prompt。

    no_script=True 时返回纯纠错 prompt（l2_take_no_script），
    no_script=False 时返回有剧本 prompt（l2_take）。
    """
    task_type = "l2_take_no_script" if no_script else "l2_take"
    return TASK_CONFIG[task_type]["system"]


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
    """格式化 transcript_segments 为 [idx][speaker] text 文本。

    truncated=True 时在头部加截断警告。
    idx 从 0 开始，与 corrected_segments.idx 对应。
    """
    if not segments:
        return "（无转录片段）"
    lines = []
    if truncated:
        lines.append("[WARNING: transcript truncated to 2500 chars, earliest segments dropped]")
    for idx, seg in enumerate(segments):
        speaker = seg.get("speaker")
        label = speaker if speaker is not None else "未知说话人"
        lines.append(f"[{idx}][{label}] {seg['text']}")
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
            # P2 #4：如果第一段（result 为空）就超长，保留 tail 而非跳过
            if not result:
                tail_text = seg["text"][-_TRANSCRIPT_CHAR_LIMIT:]
                tail_seg = {**seg, "text": tail_text}
                result.insert(0, tail_seg)
            break
        result.insert(0, seg)
        accumulated += text_len

    return result, True


def _build_user_message(input_data: L2Input) -> str:
    """组装 user message（§5 模板）。

    script_lines 为空时走无剧本路径：不含剧本块、不含 insertion/比对任务，
    只含转录记录和纯纠错任务。
    """
    truncated_segments, was_truncated = _truncate_segments(input_data.transcript_segments)
    transcript_block = _build_transcript_block(truncated_segments, was_truncated)

    if not input_data.script_lines:
        # 无剧本路径：只含转录记录 + 纯纠错任务
        return (
            f"## Take {input_data.take_number} 转录记录（含下标索引）\n\n"
            f"{transcript_block}\n\n"
            "任务：识别转录文本中的 ASR 错别字（同音字、形近字误识别），输出到 corrected_segments。"
        )

    # 有剧本路径（原有逻辑，不变）
    script_block = _build_script_lines_block(input_data.script_lines)

    previous_section = ""
    if input_data.previous_notes:
        n = len(input_data.previous_notes)
        notes_text = "\n".join(input_data.previous_notes)
        previous_section = f"\n## 历史偏差参考（最近 {n} 条）\n\n{notes_text}\n"

    return (
        f"## 剧本台词（场次 {input_data.scene_id}）\n\n"
        f"{script_block}\n\n"
        f"## Take {input_data.take_number} 转录记录（含下标索引）\n\n"
        f"{transcript_block}\n"
        f"{previous_section}\n"
        "任务：①找出转录中剧本完全没有对应的内容标为 insertion（line_no=-1），"
        "②逐行比对剧本标 match/substitution/missing，"
        "③识别 ASR 错别字放入 corrected_segments。\n"
        "直接输出 JSON。"
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


def _validate_data_dict(data: dict, *, strict: bool = True) -> L2Output:
    """校验已解析的 dict，构造并返回 L2Output。

    接受来自 json.loads 的 dict（无论是 FC 路径还是旧文本路径），
    执行字段存在性校验、枚举值校验、类型校验，最终构造 L2Output。

    strict=True（有剧本路径）：script_diff_summary / line_matches 必须存在，
        缺失时抛 L2ParseError（保留原有严格校验）。
    strict=False（无剧本路径）：script_diff_summary / line_matches 缺失时给默认值
        （None / []），corrected_segments 仍要求必须存在。

    两条路径均执行：
    - original==corrected 的 corrected_segments 条目被过滤丢弃
    - line_matches 中 detail 为字符串 "null"/"none"/"" 时归一化为 Python None

    Raises:
        L2ParseError: 字段缺失（strict 模式）/ 枚举值非法 / 类型错误。
    """
    if not isinstance(data, dict):
        raise L2ParseError("LLM response JSON is not a dict object")

    # 字段存在性检查
    if strict:
        if "script_diff_summary" not in data:
            raise L2ParseError("LLM response missing required field: 'script_diff_summary'")
        if "line_matches" not in data:
            raise L2ParseError("LLM response missing required field: 'line_matches'")
        if "corrected_segments" not in data:
            raise L2ParseError("LLM response missing required field: 'corrected_segments'")
        script_diff_summary: str | None = data["script_diff_summary"]
        raw_matches = data["line_matches"]
        raw_corrections = data["corrected_segments"]
    else:
        # 宽松模式：缺失字段给默认值
        script_diff_summary = data.get("script_diff_summary")  # 缺失 → None
        raw_matches = data.get("line_matches", [])              # 缺失 → []
        raw_corrections = data.get("corrected_segments")        # 仍要求必须存在
        if raw_corrections is None:
            raise L2ParseError("LLM response missing required field: 'corrected_segments'")

    if not isinstance(raw_matches, list):
        raise L2ParseError("LLM response 'line_matches' is not a list")
    if not isinstance(raw_corrections, list):
        raise L2ParseError("LLM response 'corrected_segments' is not a list")

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

        # detail 归一化：字符串 "null"/"none"/"" → Python None
        raw_detail = item.get("detail")
        if isinstance(raw_detail, str):
            stripped = raw_detail.strip().lower()
            detail: str | None = None if stripped in ("null", "none", "") else raw_detail
        else:
            detail = raw_detail  # None 直接保留

        line_matches.append(LineMatch(line_no=line_no, diff_type=diff_type, detail=detail))

    corrected_segments: list[CorrectedSegment] = []
    for j, cs_item in enumerate(raw_corrections):
        if not isinstance(cs_item, dict):
            raise L2ParseError(f"corrected_segments[{j}] is not a dict")

        idx = cs_item.get("idx")
        if not isinstance(idx, int) or isinstance(idx, bool) or idx < 0:
            raise L2ParseError(
                f"corrected_segments[{j}].idx is not a non-negative integer: {idx!r}"
            )

        original = cs_item.get("original")
        if not isinstance(original, str):
            raise L2ParseError(
                f"corrected_segments[{j}].original is not a string: {original!r}"
            )

        corrected = cs_item.get("corrected")
        if not isinstance(corrected, str):
            raise L2ParseError(
                f"corrected_segments[{j}].corrected is not a string: {corrected!r}"
            )

        # 过滤无效纠错段（original == corrected）
        if original == corrected:
            continue

        corrected_segments.append(CorrectedSegment(idx=idx, original=original, corrected=corrected))

    return L2Output(
        script_diff_summary=script_diff_summary,
        line_matches=line_matches,
        corrected_segments=corrected_segments,
    )


def _parse_llm_output(raw_text: str, *, strict: bool = True) -> L2Output:
    """解析 LLM 输出文本为 L2Output（旧文本路径，保留供回退/测试）。

    解析流程：strip_markdown_fence → json.loads → _validate_data_dict → L2Output。

    strict 参数透传给 _validate_data_dict：
    - strict=True（默认）：有剧本路径，三字段必须存在。
    - strict=False：无剧本路径，script_diff_summary/line_matches 缺失时给默认值。

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

    return _validate_data_dict(data, strict=strict)


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
    no_script = not input_data.script_lines
    task_type = "l2_take_no_script" if no_script else "l2_take"

    system_prompt = _build_system_prompt(no_script=no_script)
    user_message = _build_user_message(input_data)

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
    ]

    # FC 路径：调 infer_tool，取 tool_calls[0]，解析 arguments JSON
    # asyncio.TimeoutError 从 infer_tool 传出，pipeline 不捕获（让 caller 感知）
    try:
        tool_call: dict = await llm_service.infer_tool(
            messages,
            task_type=task_type,
            priority=2,
            timeout=timeout,
        )
    except LookupError as exc:
        raise L2ParseError("tool_calls 缺失或为空，模型未走 function calling 路径", cause=exc) from exc

    try:
        args_json: str = tool_call["function"]["arguments"]
        data = json.loads(args_json)
    except (KeyError, json.JSONDecodeError) as exc:
        raise L2ParseError("tool_call arguments 解析失败", cause=exc) from exc

    # 有剧本路径用 strict=True（原有严格校验），无剧本路径用 strict=False（宽松）
    return _validate_data_dict(data, strict=not no_script)
