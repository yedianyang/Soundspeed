"""QP 工具 schema（L0）+ executor（L1）单测。"""
from __future__ import annotations

import pytest

from backend.llm.tools.transcript import (
    build_count_takes_tool,
    build_get_scene_info_tool,
    build_list_characters_tool,
    build_qp_tools,
    build_query_database_tool,
    build_search_script_lines_tool,
)

_BUILDERS = [
    build_count_takes_tool,
    build_get_scene_info_tool,
    build_list_characters_tool,
    build_search_script_lines_tool,
    build_query_database_tool,
]


def _is_flat_scalar(prop: dict) -> bool:
    return prop.get("type") in {"string", "integer", "boolean", "number"}


@pytest.mark.parametrize("builder", _BUILDERS)
def test_tool_is_openai_style(builder) -> None:
    schema = builder()
    assert schema["type"] == "function"
    fn = schema["function"]
    assert isinstance(fn["name"], str) and fn["name"]
    assert isinstance(fn["description"], str) and fn["description"]
    assert fn["parameters"]["type"] == "object"


@pytest.mark.parametrize("builder", _BUILDERS)
def test_tool_params_all_flat_scalar(builder) -> None:
    # spec §4：所有参数必须扁平标量，不许嵌套数组/对象（auto 跳解析对嵌套截断崩溃）
    props = builder()["function"]["parameters"]["properties"]
    assert props, "工具至少要有一个参数"
    for name, prop in props.items():
        assert _is_flat_scalar(prop), f"参数 {name} 非扁平标量: {prop}"


def test_build_qp_tools_returns_five_named() -> None:
    tools = build_qp_tools()
    names = [t["function"]["name"] for t in tools]
    assert names == [
        "count_takes",
        "get_scene_info",
        "list_characters",
        "search_script_lines",
        "query_database",
    ]


def test_query_database_has_single_sql_param() -> None:
    props = build_query_database_tool()["function"]["parameters"]["properties"]
    assert list(props) == ["sql"]
    assert props["sql"]["type"] == "string"


@pytest.mark.parametrize("builder", _BUILDERS)
def test_required_fields_exist_in_properties(builder) -> None:
    # 防将来改 builder 手滑删了 properties 但 required 留着
    schema = builder()["function"]["parameters"]
    props = schema["properties"]
    for name in schema.get("required", []):
        assert name in props, f"required 字段 '{name}' 不在 properties 里"


def test_count_takes_status_has_enum() -> None:
    # 必修1：status 必须用 enum 约束，防模型传中文或非法值
    props = build_count_takes_tool()["function"]["parameters"]["properties"]
    assert "enum" in props["status"], "status 参数缺少 enum 约束"
    assert set(props["status"]["enum"]) == {"keep", "ng", "pass", "tbd"}
