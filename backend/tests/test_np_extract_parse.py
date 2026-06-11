"""extract_np tool_call → NPExtraction 解析 + 校验（纯 unit，无模型）。"""

import json

import pytest

from backend.pipelines.np_extract import (
    NPExtraction,
    NPParseError,
    consistency_disagreement,
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


# ── consistency_disagreement 自一致性比对 ────────────────────────────────────


def _npe(**kwargs) -> NPExtraction:
    """构建 NPExtraction，只需传差异字段，其余用哨兵默认值。"""
    defaults = dict(
        scene_ordinal=1,
        shot_ordinal=2,
        take_ordinals=[1],
        deictic="none",
        mark="none",
        note_text="ok",
        note_category="note",
    )
    defaults.update(kwargs)
    return NPExtraction(**defaults)


def test_consistency_identical_returns_empty() -> None:
    e = _npe()
    assert consistency_disagreement(e, e) == []


def test_consistency_take_ordinals_order_ignored() -> None:
    """take_ordinals 顺序不同但值相同 → 视为一致，返回空。"""
    e1 = _npe(take_ordinals=[2, 3])
    e2 = _npe(take_ordinals=[3, 2])
    assert consistency_disagreement(e1, e2) == []


def test_consistency_scene_differs() -> None:
    e1 = _npe(scene_ordinal=1)
    e2 = _npe(scene_ordinal=2)
    assert consistency_disagreement(e1, e2) == ["scene_ordinal"]


def test_consistency_multiple_fields_differ_in_order() -> None:
    """多字段不同时，返回列表遵循 _CONSISTENCY_FIELDS 顺序。"""
    e1 = _npe(scene_ordinal=1, shot_ordinal=2, mark="none")
    e2 = _npe(scene_ordinal=9, shot_ordinal=9, mark="ng")
    diff = consistency_disagreement(e1, e2)
    assert diff == ["scene_ordinal", "shot_ordinal", "mark"]


def test_consistency_note_fields_excluded() -> None:
    """note_text / note_category 不参与比对，即使不同也不触发。"""
    e1 = _npe(note_text="大声点", note_category="note")
    e2 = _npe(note_text="声音小", note_category="issue")
    assert consistency_disagreement(e1, e2) == []
