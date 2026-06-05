"""NP Pipeline：Note 归置（ticket 4.x）。

根据录音师文字备注 + 上下文（场次、take 列表、当前录制状态），
调用 LLM 判断备注属于哪一条 take，并提取类别与正文。

公共 API：
  NPInput       输入 dataclass
  NPOutput      输出 dataclass
  NPParseError  LLM 输出解析失败异常
  run_np_note   纯异步函数，执行一次 NP Pipeline
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.llm.service import LLMService


class NPParseError(Exception):
    """LLM 输出解析失败。"""

    def __init__(self, message: str, cause: Exception | None = None) -> None:
        super().__init__(message)
        self.cause = cause


@dataclass(frozen=True)
class NPInput:
    raw_text: str
    parsed_category: str  # 规则解析的类别，默认 "note"
    current_scene_id: int | None
    current_take_id: int | None  # 当前活跃 take，None 表示不在录制中
    take_context: list[dict]  # [{"take_id", "scene_code", "shot", "take_number", "summary"}, ...]
    ts: float
    # 4.H 场镜次补全：当前活跃 take 的人类可读 场/镜/次（current_take_id 为 None 时全 None）
    current_scene_code: str | None = None
    current_shot: str | None = None
    current_take_number: int | None = None


@dataclass(frozen=True)
class NPOutput:
    take_id: int
    category: str
    content: str


# note 合法类别（单一真源）：_validate_data_dict 校验 + tools/note.py 的 schema enum 同取它，
# 改类别一处改两路同步（与 L2 的 _VALID_DIFF_TYPES 同思路）。
_VALID_NOTE_CATEGORIES = ("note", "issue", "keeper", "ng", "hold")


# NP 输出契约（文本/语音 system prompt 共用，单一真源）：严格 JSON、无 markdown。
# 与 _parse_llm_output 的字段（take_id/category/content）对齐，改格式时一处改两路同步。
_NP_OUTPUT_FORMAT = (
    "输出格式（严格遵守）：\n"
    "只输出合法 JSON，不要 markdown 代码块，不要注释。\n"
    '{"take_id": <int>, "category": "<str>", "content": "<str>"}'
)


def _build_system_prompt() -> str:
    return (
        "你是场记助手，负责归置录音师的文字备注。\n\n"
        "职责：\n"
        "1. 根据备注内容和上下文，判断备注属于哪一条 take。\n"
        "2. 提取备注的类别和正文（去掉指代词如\"这条\"\"上一条\"等）。\n\n"
        "规则：\n"
        "- \"这条\"/\"这个\"/\"本条\" → 当前活跃 take（current take）\n"
        "- \"上一条\"/\"上一个\"/\"前一条\" → 最近一条已完成的 take\n"
        "- \"第N条\"（如\"第三条\"）→ 当前场当前镜的第 N 条 take；跨镜/跨场时备注须显式带镜次/场次，否则按当前场当前镜解析\n"
        "- 无明确指代 → 默认当前活跃 take，若无则最近一条\n"
        "- category 取值：note（一般备注）、issue（问题记录）、keeper（保留）、ng（NG）、hold（待定）\n"
        "- content 是去掉指代词和类别标记后的纯净正文\n\n"
        + _NP_OUTPUT_FORMAT
    )


def _build_context_lines(input_data: NPInput) -> list[str]:
    """场镜次上下文（文本/语音 NP 共用）：当前场镜次 + 本场已有 take 列表。"""
    parts: list[str] = []

    parts.append("=== 当前拍摄上下文 ===")
    scene = input_data.current_scene_code or (
        f"场 id {input_data.current_scene_id}"
        if input_data.current_scene_id is not None
        else "未知场"
    )
    if input_data.current_take_id is not None:
        shot = input_data.current_shot or "无镜"
        parts.append(
            f"当前场={scene}  当前镜={shot}  "
            f"当前活跃 take={scene}/{shot}/第{input_data.current_take_number}条"
        )
    else:
        parts.append(f"当前场={scene}  当前无活跃录制")

    if input_data.take_context:
        parts.append("\n本场已有 take：")
        for t in input_data.take_context:
            sc = t.get("scene_code") or "?"
            sh = t.get("shot") or "无镜"
            num = t.get("take_number", "?")
            summary = t.get("summary", "") or ""
            line = f"  take_id={t['take_id']}  {sc}/{sh}/第{num}条"
            if summary:
                line += f"  [{summary}]"
            parts.append(line)
    else:
        parts.append("\n（无历史 take）")

    return parts


def _build_user_message(input_data: NPInput) -> str:
    """文本 NP user message：场镜次上下文 + 备注文字 + 预解析类别。"""
    parts = _build_context_lines(input_data)
    parts.append("\n=== 备注文字 ===")
    parts.append(input_data.raw_text)
    parts.append(f"\n预解析类别: {input_data.parsed_category}")
    return "\n".join(parts)


def _build_voice_user_message(input_data: NPInput) -> str:
    """语音 NP user message：场镜次上下文 + 「听这段音频」标记（正文/类别由模型从音频听+判，
    不带 raw_text / parsed_category）。音频本体经哨兵 content 走多模态通道（run_np_voice 组装）。"""
    parts = _build_context_lines(input_data)
    parts.append("\n=== 语音备注（听下面这段音频，转写内容并归置到正确 take）===")
    return "\n".join(parts)


def _validate_data_dict(data: dict) -> NPOutput:
    """校验已解析 dict（FC tool_call arguments 或语音裸 JSON 通路共用）→ NPOutput。

    字段：take_id（int，非 bool）、category（5 类枚举）、content（str）。
    """
    if not isinstance(data, dict):
        raise NPParseError(f"LLM output is not a JSON object: {str(data)[:200]}")

    take_id = data.get("take_id")
    if not isinstance(take_id, int) or isinstance(take_id, bool):
        raise NPParseError(f"take_id is not an integer: {take_id!r}")

    category = data.get("category", "note")
    if not isinstance(category, str) or category not in _VALID_NOTE_CATEGORIES:
        raise NPParseError(f"category invalid: {category!r}")

    content = data.get("content", "")
    if not isinstance(content, str):
        raise NPParseError(f"content is not a string: {content!r}")

    return NPOutput(take_id=take_id, category=category, content=content)


def _parse_llm_output(raw_text: str) -> NPOutput:
    """裸文本 JSON 通路（语音 NP，Tier 2）：strip markdown → json.loads → _validate_data_dict。"""
    if not raw_text or not raw_text.strip():
        raise NPParseError("LLM returned empty response")

    text = raw_text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise NPParseError(f"Failed to parse LLM output as JSON: {text[:200]}", cause=e)

    return _validate_data_dict(data)


async def run_np_note(
    input_data: NPInput,
    llm_service: "LLMService",
    timeout: float = 30.0,
) -> NPOutput:
    """文本 NP：forced tool-call（对标 L2 #25）。note_struct 配 tools + 强制 tool_choice →
    infer_tool 取 tool_calls[0] → 解析 arguments JSON → _validate_data_dict → NPOutput。

    asyncio.TimeoutError 从 infer_tool 透出（不吞，orchestrator 4.I 兜底为 timeout）。
    """
    system_prompt = _build_system_prompt()
    user_message = _build_user_message(input_data)

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
    ]

    # FC 路径：调 infer_tool，取 tool_calls[0]，解析 arguments JSON（镜像 run_l2_take）。
    try:
        tool_call: dict = await llm_service.infer_tool(
            messages,
            task_type="note_struct",
            priority=2,
            timeout=timeout,
        )
    except LookupError as exc:
        raise NPParseError("tool_calls 缺失或为空，模型未走 function calling 路径", cause=exc)

    try:
        args_json: str = tool_call["function"]["arguments"]
        data = json.loads(args_json)
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise NPParseError("tool_call arguments 解析失败", cause=exc)

    return _validate_data_dict(data)


def _build_voice_system_prompt() -> str:
    """语音 NP system prompt：在文本职责上叠「听懂中文语音」，输出同一 JSON 契约。"""
    return (
        "你是场记助手，负责把录音师的口头语音备注归置到正确的素材（take）。\n\n"
        "职责：\n"
        "1. 听懂这段中文语音备注的内容。\n"
        "2. 根据语音内容和上下文，判断它属于哪一条 take。\n"
        "3. 提取类别和正文（去掉指代词如\"这条\"\"上一条\"和编号）。\n\n"
        "规则：\n"
        "- \"这条\"/\"这个\"/\"本条\" → 当前活跃 take（current take）\n"
        "- \"上一条\"/\"上一个\"/\"前一条\" → 最近一条已完成的 take\n"
        "- \"第N条\"（如\"第三条\"）→ 当前场当前镜的第 N 条 take；跨镜/跨场时语音须显式带镜次/场次，否则按当前场当前镜解析\n"
        "- 无明确指代 → 默认当前活跃 take，若无则最近一条\n"
        "- category 取值：note（一般备注）、issue（问题记录）、keeper（保留、可以用）、ng（NG）、hold（待定）\n"
        "- content 是去掉指代词和编号后的纯净正文\n\n"
        + _NP_OUTPUT_FORMAT
    )


async def run_np_voice(
    input_data: NPInput,
    audio: bytes,
    llm_service: "LLMService",
    timeout: float = 60.0,
) -> NPOutput:
    """语音 NP：场镜次上下文 + 音频哨兵 → 多模态 infer_voice → 解析 {take_id, category, content}。

    与 run_np_note 共用 NPInput / _parse_llm_output / 场镜次上下文，唯一分叉：user content 是
    `[text, 音频哨兵]` 多模态 list，且走 llm_service.infer_voice（带 audio 字节）。失败（解析/超时/
    FK）分类沿用文本路径（NPParseError / TimeoutError / IntegrityError，由 orchestrator 4.I 兜底）。
    """
    from backend.llm.multimodal import AUDIO_SENTINEL  # noqa: PLC0415  延迟导入避免顶层拉 llama_cpp

    messages: list[dict] = [
        {"role": "system", "content": _build_voice_system_prompt()},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": _build_voice_user_message(input_data)},
                {"type": "image_url", "image_url": {"url": AUDIO_SENTINEL}},
            ],
        },
    ]

    raw_text = await llm_service.infer_voice(
        messages,
        audio,
        task_type="note_struct",
        priority=2,
        timeout=timeout,
    )

    return _parse_llm_output(raw_text)
