"""extract_np 工具 dict 结构契约（纯 unit，无模型）。

Schema 法则（spike 实证）：扁平 + 全字段 required + 哨兵值。这条测试钉住该结构，
任何把字段改可选 / 嵌套 / 漏 required 的改动都红——免得无声退化回 0/24。
"""

from backend.llm.tools.note_extract import EXTRACT_NP_TOOL_NAME, build_extract_np_tool


def test_tool_name_constant() -> None:
    assert EXTRACT_NP_TOOL_NAME == "extract_np"


def test_tool_shape_flat_all_required() -> None:
    tool = build_extract_np_tool()
    assert tool["type"] == "function"
    fn = tool["function"]
    assert fn["name"] == "extract_np"
    params = fn["parameters"]
    assert params["type"] == "object"
    props = params["properties"]
    expected = {
        "scene_ordinal", "shot_ordinal", "take_ordinals",
        "deictic", "mark", "note_text", "note_category",
    }
    assert set(props) == expected
    assert set(params["required"]) == expected
    for name, spec in props.items():
        assert spec["type"] != "object", f"{name} 不能是嵌套对象"


def test_enums() -> None:
    props = build_extract_np_tool()["function"]["parameters"]["properties"]
    assert props["deictic"]["enum"] == ["none", "current", "prev"]
    assert props["mark"]["enum"] == ["pass", "ng", "keep", "tbd", "none"]
    assert props["note_category"]["enum"] == ["note", "issue"]
    assert props["scene_ordinal"]["type"] == "integer"
    assert props["shot_ordinal"]["type"] == "integer"
    assert props["take_ordinals"]["type"] == "array"
    assert props["take_ordinals"]["items"]["type"] == "integer"
    assert props["note_text"]["type"] == "string"
