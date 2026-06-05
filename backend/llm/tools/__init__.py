"""LLM 工具注册表包。

公共 API（均为函数级 lazy import，避免 config → tools → l2_take → config 循环）：
  build_l2_tool     构造 report_script_analysis tool dict（从 script 模块）
  registry          工具注册表相关函数（见 registry.py）
"""

# 故意不在 module 级做任何 import。
# 调用方按需直接 import：
#   from backend.llm.tools.script import build_l2_tool
#   from backend.llm.tools.registry import get_tool_schema, ...
