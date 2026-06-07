"""extract_np tool_call → NPExtraction 解析 + 校验（纯 unit，无模型）。"""

import json

import pytest

from backend.pipelines.np_extract import (
    NPExtraction,
    NPParseError,
    parse_extract_tool_call,
)


def _tc(args: dict) -> dict:
    return {
        "id": "call_x",
        "type": "function",
        "function": {"name": "extract_np", "arguments": json.dumps(args, ensure_ascii=False)},
    }


def _full(**over) -> dict:
    base = {
        "scene_ordinal": 0, "shot_ordinal": 0, "take_ordinals": [],
        "deictic": "current", "mark": "none", "note_text": "", "note_category": "note",
    }
    base.update(over)
    return base


def test_parse_full() -> None:
    e = parse_extract_tool_call(_tc(_full(shot_ordinal=4, take_ordinals=[1], mark="ng")))
    assert e == NPExtraction(
        scene_ordinal=0, shot_ordinal=4, take_ordinals=[1],
        deictic="current", mark="ng", note_text="", note_category="note",
    )


def test_bad_json() -> None:
    bad = {"id": "x", "type": "function", "function": {"name": "extract_np", "arguments": "{not json"}}
    with pytest.raises(NPParseError):
        parse_extract_tool_call(bad)


def test_invalid_mark_enum() -> None:
    with pytest.raises(NPParseError):
        parse_extract_tool_call(_tc(_full(mark="bogus")))


def test_invalid_deictic_enum() -> None:
    with pytest.raises(NPParseError):
        parse_extract_tool_call(_tc(_full(deictic="sideways")))


def test_invalid_note_category() -> None:
    with pytest.raises(NPParseError):
        parse_extract_tool_call(_tc(_full(note_category="weird")))


def test_take_ordinals_must_be_int_list() -> None:
    with pytest.raises(NPParseError):
        parse_extract_tool_call(_tc(_full(take_ordinals=["a"])))
    with pytest.raises(NPParseError):
        parse_extract_tool_call(_tc(_full(take_ordinals="3")))


def test_ordinals_must_be_int() -> None:
    with pytest.raises(NPParseError):
        parse_extract_tool_call(_tc(_full(scene_ordinal="1")))
