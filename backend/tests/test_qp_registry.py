"""QP 工具注册：schema + 真实 executor（executor!=None 首个真实消费者）。"""
from __future__ import annotations

from backend.db.dal import DAL
from backend.llm.tools import registry

_QP_NAMES = ["count_takes", "get_scene_info", "list_characters", "search_script_lines", "query_database"]


def test_qp_tools_registered_with_schema() -> None:
    for name in _QP_NAMES:
        schema = registry.get_tool_schema(name)
        assert schema["function"]["name"] == name


def test_qp_tools_have_real_executor() -> None:
    for name in _QP_NAMES:
        assert registry.get_executor(name) is not None, f"{name} executor 不能为 None（QP Tier 2）"


def test_qp_executor_callable_end_to_end(tmp_path) -> None:
    dal = DAL(tmp_path / "reg.db")
    dal.create_scene("Scene_1")
    executor = registry.get_executor("count_takes")
    res = executor({"scene_ref": "1"}, dal)
    assert res["count"] == 0
    dal.close()
