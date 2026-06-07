"""NP 一步式提取 pipeline（替代 np_note 的 runner 角色）。

模型一次调 extract_np（扁平全 required 哨兵）→ 解析成 NPExtraction → 交确定性解析（np_resolve）
→ 多目标 apply（np_apply）。文本/语音共用，语音先 ASR。

公共 API：
  NPExtraction          解析后的扁平意图 dataclass
  NPParseError          tool_call 解析/校验失败
  parse_extract_tool_call(tool_call) -> NPExtraction
  run_extract_np(...)        文本路径（PART 5）
  run_extract_np_voice(...)  语音路径（PART 5）
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.llm.service import LLMService

_VALID_DEICTIC = ("none", "current", "prev")
_VALID_MARK = ("pass", "ng", "keep", "tbd", "none")
_VALID_NOTE_CATEGORY = ("note", "issue")


class NPParseError(Exception):
    """extract_np 输出解析/校验失败。"""

    def __init__(self, message: str, cause: Exception | None = None) -> None:
        super().__init__(message)
        self.cause = cause


@dataclass(frozen=True)
class NPExtraction:
    scene_ordinal: int
    shot_ordinal: int
    take_ordinals: list[int]
    deictic: str  # none|current|prev
    mark: str  # pass|ng|keep|tbd|none
    note_text: str
    note_category: str  # note|issue


def _as_int(value: object, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise NPParseError(f"{field} 非整数: {value!r}")
    return value


def _validate_extraction(data: dict) -> NPExtraction:
    if not isinstance(data, dict):
        raise NPParseError(f"extract_np 输出非对象: {str(data)[:200]}")

    scene_ordinal = _as_int(data.get("scene_ordinal"), "scene_ordinal")
    shot_ordinal = _as_int(data.get("shot_ordinal"), "shot_ordinal")

    raw_takes = data.get("take_ordinals")
    if not isinstance(raw_takes, list):
        raise NPParseError(f"take_ordinals 非数组: {raw_takes!r}")
    take_ordinals = [_as_int(t, "take_ordinals[]") for t in raw_takes]

    deictic = data.get("deictic")
    if deictic not in _VALID_DEICTIC:
        raise NPParseError(f"deictic 非法: {deictic!r}")

    mark = data.get("mark")
    if mark not in _VALID_MARK:
        raise NPParseError(f"mark 非法: {mark!r}")

    note_text = data.get("note_text")
    if not isinstance(note_text, str):
        raise NPParseError(f"note_text 非字符串: {note_text!r}")

    note_category = data.get("note_category")
    if note_category not in _VALID_NOTE_CATEGORY:
        raise NPParseError(f"note_category 非法: {note_category!r}")

    return NPExtraction(
        scene_ordinal=scene_ordinal,
        shot_ordinal=shot_ordinal,
        take_ordinals=take_ordinals,
        deictic=deictic,
        mark=mark,
        note_text=note_text,
        note_category=note_category,
    )


def parse_extract_tool_call(tool_call: dict) -> NPExtraction:
    """tool_calls[0] → NPExtraction：解析 arguments JSON 字符串 → 校验枚举/类型。"""
    try:
        args_json: str = tool_call["function"]["arguments"]
        data = json.loads(args_json)
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise NPParseError("extract_np tool_call arguments 解析失败", cause=exc)
    return _validate_extraction(data)


def _build_extract_system_prompt() -> str:
    """提取 system prompt（spike 21/24 复跑版 + note_category 句）。上下文行由 orchestrator 拼接追加。"""
    return (
        "你是场记助手。把录音师这句话提取成固定结构，每个字段都要填（用哨兵值表示'没有'）。"
        "映射：第N进/第N镜=shot_ordinal，第N条/第N次=take_ordinal，第N场=scene_ordinal；"
        "当前场/镜填0；这条=deictic current，上一条/刚才那条=deictic prev，用了编号=deictic none；"
        "过/通过=mark pass，保/留=keep，废/NG/不行=ng，没打标意图=mark none；"
        "描述性内容放 note_text，没有填空串；"
        "技术问题(收音小/灯光暗/穿帮/对焦虚)note_category=issue，否则 note。"
    )


def build_extract_messages(raw_text: str, context_line: str) -> list[dict]:
    """文本提取 messages：system（语义 + 上下文行）+ user（原话）。"""
    system = _build_extract_system_prompt()
    if context_line:
        system = system + "\n" + context_line
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": raw_text},
    ]


async def run_extract_np(
    raw_text: str,
    llm_service: "LLMService",
    timeout: float = 30.0,
    context_line: str = "",
) -> NPExtraction:
    """文本 NP 提取：forced extract_np → NPExtraction。解析失败 → NPParseError。"""
    messages = build_extract_messages(raw_text, context_line)
    try:
        tool_call = await llm_service.infer_tool(
            messages, task_type="note_extract", priority=2, timeout=timeout
        )
    except LookupError as exc:
        raise NPParseError("extract_np tool_calls 缺失，模型未走 FC", cause=exc)
    return parse_extract_tool_call(tool_call)


def _build_voice_asr_messages(context_line: str) -> list[dict]:
    """ASR messages：text 提示 + 音频哨兵（多模态通道）。无强制工具（content 路径）。"""
    from backend.llm.multimodal import AUDIO_SENTINEL  # noqa: PLC0415

    prompt = "把这段中文语音逐字转写成文本，只输出转写内容，不要解释。"
    if context_line:
        prompt = context_line + "\n" + prompt
    return [
        {"role": "system", "content": "你是中文语音转写助手。"},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": AUDIO_SENTINEL}},
            ],
        },
    ]


async def run_extract_np_voice(
    audio: bytes,
    llm_service: "LLMService",
    timeout: float = 60.0,
    context_line: str = "",
) -> NPExtraction:
    """语音 NP（设计 §3.3 两步）：① 多模态 ASR 转写 ② 转写当 raw_text 走同一 extract_np。

    ASR 用无强制工具 task（voice_dispatch_free），避免 forced tool 撞 content-path guardrail。
    """
    asr_messages = _build_voice_asr_messages(context_line)
    transcript = await llm_service.infer_voice(
        asr_messages, audio, task_type="voice_dispatch_free", priority=1, timeout=timeout
    )
    if not transcript or not transcript.strip():
        raise NPParseError("语音转写为空")
    return await run_extract_np(
        transcript.strip(), llm_service, timeout=timeout, context_line=context_line
    )


# ── 适配器（调度器-兼容签名）────────────────────────────────────────────────────
# voice_dispatch.py（§9 不可碰）调用 voice_runner(input_data, audio, service) 位置参数固定；
# 以下适配器保持该签名，内部转调新 extract runner。duck-typed on input_data。


def _np_context_line(input_data) -> str:
    """从 input_data 拼一行提取上下文（spike 格式）。current_* 为 None 时省略活跃条。"""
    scene = input_data.current_scene_code or (
        f"场id{input_data.current_scene_id}" if input_data.current_scene_id is not None else "未知场"
    )
    if input_data.current_take_id is not None:
        shot = input_data.current_shot or "无镜"
        return f"当前：{scene} / {shot} / 当前活跃第{input_data.current_take_number}条。"
    return f"当前：{scene} / 当前无活跃录制。"


async def np_text_adapter(input_data, svc, timeout: float = 30.0):
    """文本 NP 适配器：保持 (input_data, svc) 位置签名，转调 run_extract_np。"""
    return await run_extract_np(
        input_data.raw_text, svc, timeout=timeout, context_line=_np_context_line(input_data)
    )


async def np_voice_adapter(input_data, audio: bytes, svc, timeout: float = 60.0):
    """语音 NP 适配器：保持 (input_data, audio, svc) 位置签名，转调 run_extract_np_voice。"""
    return await run_extract_np_voice(
        audio, svc, timeout=timeout, context_line=_np_context_line(input_data)
    )
