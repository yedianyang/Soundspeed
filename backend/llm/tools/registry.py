"""LLM 工具注册表（Tier 2 脚手架，当前无生产消费者）。

轻量名字 → (schema, executor) 映射。
Tier 1 工具只有 schema，executor 留 None 占位（Tier 2 接入时填充）。

接线状态：Tier 1 的 L2 路径**不经过本注册表**——
config.py 直接调 `tools.script.build_l2_tool()` 拿 schema。本模块及其
`_bootstrap()` 只在被 import 时才注册（生产路径目前无人 import），
留作 Tier 2 多工具 auto 路由（spec §3.3 / §4.2）接入时的落点。
改 Tier 1 工具 schema 时改 build_l2_tool，不要只改这里。

公共 API：
  register(name, schema, executor=None)  注册工具
  get_tool_schema(name) -> dict           按名取 schema
  get_executor(name) -> Callable | None  按名取 executor（Tier 2 用）
  list_tools(domain=None) -> list[str]   列出已注册工具名
"""

from __future__ import annotations

from typing import Callable

# 内部存储：名字 → (schema_dict, executor_or_None)
_REGISTRY: dict[str, tuple[dict, Callable | None]] = {}


def register(
    name: str,
    schema: dict,
    executor: Callable | None = None,
) -> None:
    """注册工具。

    Args:
        name: 工具名（与 schema["function"]["name"] 保持一致）。
        schema: OpenAI 风格 tool dict。
        executor: 可选 Python 可调用对象，Tier 2 时填充；Tier 1 留 None。
    """
    _REGISTRY[name] = (schema, executor)


def _require(name: str) -> tuple[dict, Callable | None]:
    """取注册项，未注册抛 KeyError。get_tool_schema / get_executor 共用。"""
    if name not in _REGISTRY:
        raise KeyError(f"工具 {name!r} 未注册，已注册工具: {list(_REGISTRY)}")
    return _REGISTRY[name]


def get_tool_schema(name: str) -> dict:
    """按名取工具 schema。

    Raises:
        KeyError: 工具名未注册。
    """
    return _require(name)[0]


def get_executor(name: str) -> Callable | None:
    """按名取工具 executor（Tier 2 用）。

    Raises:
        KeyError: 工具名未注册。
    """
    return _require(name)[1]


def list_tools(domain: str | None = None) -> list[str]:
    """列出已注册工具名。

    Args:
        domain: 可选命名空间前缀过滤（如 "script"），None 表示全部。

    Returns:
        工具名列表，按注册顺序排列。
    """
    names = list(_REGISTRY.keys())
    if domain is not None:
        names = [n for n in names if n.startswith(domain)]
    return names


# ---------------------------------------------------------------------------
# 模块级注册：report_script_analysis（L2）、structure_note（文本 NP）（Tier 1）
# ---------------------------------------------------------------------------

def _bootstrap() -> None:
    """在 module 导入时注册所有 Tier 1 工具。"""
    from backend.llm.tools.note import NOTE_TOOL_NAME, build_note_tool  # noqa: PLC0415
    from backend.llm.tools.script import build_l2_tool  # noqa: PLC0415

    register("report_script_analysis", build_l2_tool(), executor=None)
    register(NOTE_TOOL_NAME, build_note_tool(), executor=None)


_bootstrap()
