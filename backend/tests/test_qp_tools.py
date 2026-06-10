"""QP 工具 schema（L0）+ executor（L1）单测。"""
from __future__ import annotations

import pytest

from backend.db.dal import DAL
from backend.llm.tools.transcript import (
    build_count_takes_tool,
    build_get_scene_info_tool,
    build_list_characters_tool,
    build_qp_tools,
    build_query_database_tool,
    build_search_script_lines_tool,
    count_takes_executor,
    get_scene_info_executor,
    list_characters_executor,
    query_database_executor,
    search_script_lines_executor,
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


# ---------------------------------------------------------------------------
# L1 executor 测试（Task 5）
# ---------------------------------------------------------------------------


@pytest.fixture
def seeded_dal(tmp_path) -> DAL:
    d = DAL(tmp_path / "qp_exec.db")
    sid = d.get_or_create_scene("Scene_7", int_ext="室外", time_of_day="夜", location="天台")[0]
    d.start_take(sid, "", 1000.0)
    d.start_take(sid, "", 1001.0)
    script_id = d.insert_script(sid, "raw")
    d.insert_script_line(script_id, 1, "阿强", "我们走吧。")
    d.insert_script_line(script_id, 2, "小美", "再等等。")
    yield d
    d.close()


def test_count_takes_executor(seeded_dal: DAL) -> None:
    res = count_takes_executor({"scene_ref": "7"}, seeded_dal)
    assert res["条数"] == 2
    assert res["场次"] == "7"


def test_count_takes_executor_status_localized(seeded_dal: DAL) -> None:
    # D5：status 枚举码回喂前译成中文（模型只转述，不翻译）
    res = count_takes_executor({"scene_ref": "7", "status": "tbd"}, seeded_dal)
    assert res["条数"] == 2
    assert res["状态"] == "待定"


def test_count_takes_executor_missing_scene(seeded_dal: DAL) -> None:
    res = count_takes_executor({"scene_ref": "999"}, seeded_dal)
    assert "error" in res  # 找不到老实说没有（spec §7.3）


def test_get_scene_info_executor(seeded_dal: DAL) -> None:
    res = get_scene_info_executor({"scene_ref": "Scene_7"}, seeded_dal)
    assert res["场次"] == "Scene_7"
    assert res["地点"] == "天台"
    assert res["内外景"] == "室外"
    assert res["时间"] == "夜"
    assert res["角色数"] == 2
    assert "scene_id" not in res  # 裸 DB 主键不回喂


def test_get_scene_info_executor_none_fields_localized(seeded_dal: DAL) -> None:
    # D5：NULL 字段译成「未注明」，不回喂 null 让模型瞎猜
    seeded_dal.create_scene("8")
    res = get_scene_info_executor({"scene_ref": "8"}, seeded_dal)
    assert res["地点"] == "未注明"
    assert res["内外景"] == "未注明"
    assert res["时间"] == "未注明"
    assert res["拍摄日期"] == "未注明"


def test_list_characters_executor(seeded_dal: DAL) -> None:
    res = list_characters_executor({"scene_ref": "7"}, seeded_dal)
    assert sorted(res["角色"]) == ["小美", "阿强"]
    assert res["人数"] == 2


def test_search_script_lines_executor(seeded_dal: DAL) -> None:
    res = search_script_lines_executor({"query": "我们走吧"}, seeded_dal)
    assert res["匹配数"] >= 1
    assert any("走吧" in m["台词"] for m in res["匹配"])
    assert all(m["场次"] == "Scene_7" for m in res["匹配"])  # 全剧检索带场次


def test_search_script_lines_executor_stage_direction(seeded_dal: DAL) -> None:
    # 舞台指示行 character=NULL → 回喂「(舞台指示)」而非 null
    sid = seeded_dal.resolve_scene_id("7")
    script_id = seeded_dal.insert_script(sid, "v2 raw")
    seeded_dal.insert_script_line(script_id, 1, None, "（两人对视良久）")
    res = search_script_lines_executor({"query": "两人对视良久"}, seeded_dal)
    assert res["匹配数"] >= 1
    assert res["匹配"][0]["角色"] == "(舞台指示)"


def test_query_database_executor(seeded_dal: DAL) -> None:
    res = query_database_executor({"sql": "SELECT COUNT(*) AS n FROM scenes;"}, seeded_dal)
    assert res["结果"][0]["n"] == 1
    assert res["行数"] == 1
    assert res["截断"] is False
    assert "columns" not in res  # 裸列名清单不回喂


def test_query_database_executor_blocks_write(seeded_dal: DAL) -> None:
    res = query_database_executor({"sql": "DELETE FROM scenes;"}, seeded_dal)
    assert "error" in res


# ---------------------------------------------------------------------------
# 健壮性补测（必修 1/2 + minor 3/4）
# ---------------------------------------------------------------------------


def test_search_script_lines_executor_empty_query(seeded_dal: DAL) -> None:
    # 空 query 返 error，不抛穿
    res = search_script_lines_executor({"query": ""}, seeded_dal)
    assert "error" in res


def test_search_script_lines_executor_fts_syntax(seeded_dal: DAL) -> None:
    # FTS 保留字触发 OperationalError，被 try/except 包成 error，不抛穿
    res = search_script_lines_executor({"query": "AND"}, seeded_dal)
    assert "error" in res


def test_list_characters_executor_missing_scene(seeded_dal: DAL) -> None:
    # 找不到场次返 error，不返空列表
    res = list_characters_executor({"scene_ref": "999"}, seeded_dal)
    assert "error" in res


def test_scene_ref_accepts_int(seeded_dal: DAL) -> None:
    # 模型可能吐整数 {scene_ref: 7}，强转后不抛 AttributeError
    res = count_takes_executor({"scene_ref": 7}, seeded_dal)
    # 要么正常解析到场次，要么返 error；无论哪种都不能抛异常
    assert isinstance(res, dict)
    assert "条数" in res or "error" in res
