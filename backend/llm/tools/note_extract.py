"""extract_np 工具（NP 一步式提取 forced FC，对标 tools/route.py）。

build_extract_np_tool() 返回 OpenAI function-calling 风格 tool dict：模型一次把录音师话语
提取成固定结构。Schema 法则（spike 2026-06-07 实证，打真 E4B）：扁平 + 全字段 required +
哨兵值。可选字段会被 4B 跳、嵌套对象会退化（optional+nested 探针 0/24，扁平全 required 21/24）。

本模块 import-neutral（字面量枚举，不拉 pipelines），config 可模块级 eager import
（无循环 import 风险，同 build_route_memo_tool / build_l2_tool）。
"""

from __future__ import annotations

# 工具名（config tool_choice / 本构造器 / 解析处三处须一致）。
EXTRACT_NP_TOOL_NAME = "extract_np"


def build_extract_np_tool() -> dict:
    """构造 extract_np tool dict（扁平 7 字段，全 required，哨兵值）。"""
    return {
        "type": "function",
        "function": {
            "name": EXTRACT_NP_TOOL_NAME,
            "description": "把录音师话语提取成固定结构，所有字段必填，用哨兵值表示'没有'",
            "parameters": {
                "type": "object",
                "properties": {
                    "scene_ordinal": {
                        "type": "integer",
                        "description": "第几场；当前场填 0",
                    },
                    "shot_ordinal": {
                        "type": "integer",
                        "description": "第几进/第几镜；当前镜填 0",
                    },
                    "take_ordinals": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": "第几条/第几次，可多个；不按编号(用这条/上一条)时填 []",
                    },
                    "deictic": {
                        "type": "string",
                        "enum": ["none", "current", "prev"],
                        "description": "这条=current，上一条/刚才那条=prev，用了编号=none",
                    },
                    "mark": {
                        "type": "string",
                        "enum": ["pass", "ng", "keep", "tbd", "none"],
                        "description": "打标意图；没有就填 none",
                    },
                    "note_text": {
                        "type": "string",
                        "description": "要记的描述性备注；没有就填空串 \"\"",
                    },
                    "note_category": {
                        "type": "string",
                        "enum": ["note", "issue"],
                        "description": "note=一般备注；issue=技术问题(收音/灯光/穿帮/对焦虚等)。无备注时填 note",
                    },
                },
                "required": [
                    "scene_ordinal",
                    "shot_ordinal",
                    "take_ordinals",
                    "deictic",
                    "mark",
                    "note_text",
                    "note_category",
                ],
            },
        },
    }
