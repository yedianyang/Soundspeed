"""structure_note 工具定义（文本 NP forced tool-call，对标 tools/script.py）。

build_note_tool() 构造符合 OpenAI function calling 格式的 tool dict：模型把录音师备注
归置到某条 take 并结构化为 {take_id, category, content}。

category enum 从 backend.pipelines.np_note._VALID_NOTE_CATEGORIES 函数级 lazy import，
与 pipeline 校验（_validate_data_dict）同源（防 schema 与校验漂移）。

循环 import 规避（同 build_l2_tool）：lazy import 放函数体内，config 模块级构造此 tool 时
np_note 已可安全加载（np_note 不在模块级 import config）。
"""

from __future__ import annotations

# 工具名（config tool_choice / registry 注册 / 本构造器三处须一致）。
NOTE_TOOL_NAME = "structure_note"


def build_note_tool() -> dict:
    """构造 structure_note OpenAI 风格 tool dict。

    Returns:
        符合 OpenAI function calling spec 的 tool 字典，type="function"，
        name="structure_note"，参数 take_id / category / content 全必填。
    """
    from backend.pipelines.np_note import _VALID_NOTE_CATEGORIES  # noqa: PLC0415

    return {
        "type": "function",
        "function": {
            "name": NOTE_TOOL_NAME,
            "description": (
                "把录音师的这条备注归置到正确的素材（take）并结构化输出："
                "判断它属于哪一条 take，提取类别与去掉指代词/编号后的纯净正文。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "take_id": {
                        "type": "integer",
                        "description": (
                            "备注归属的 take_id（从上下文给出的本场 take 列表里选）。"
                            "\"这条\"=当前活跃 take，\"上一条\"=最近一条，"
                            "\"第N条\"=当前场当前镜第 N 条。"
                        ),
                    },
                    "category": {
                        "type": "string",
                        "enum": list(_VALID_NOTE_CATEGORIES),
                        "description": (
                            "备注类别：note 一般备注 / issue 问题 / keeper 保留可用 / "
                            "ng NG / hold 待定。"
                        ),
                    },
                    "content": {
                        "type": "string",
                        "description": "去掉指代词（这条/上一条/第N条）和类别标记后的纯净正文。",
                    },
                },
                "required": ["take_id", "category", "content"],
            },
        },
    }
