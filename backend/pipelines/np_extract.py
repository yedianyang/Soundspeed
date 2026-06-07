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
