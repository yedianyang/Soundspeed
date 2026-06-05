"""QP 工具家（FC spec §3.3 预留）：5 个 build_*_tool() schema + build_qp_tools()。

executor（Task 5）也住本模块，与 schema 共置。本模块只 import 中性叶子，
DAL 仅 TYPE_CHECKING——避开 config→tools→pipeline→config 循环（D-QP-09）。
所有参数扁平标量（spec §4）：auto 跳的 FunctionGemma 字符串解析对扁平标量稳，
对嵌套数组截断崩溃。
"""
from __future__ import annotations


def build_count_takes_tool() -> dict:
    return {
        "type": "function",
        "function": {
            "name": "count_takes",
            "description": "统计某场次已拍摄的 take 条数（可选按状态过滤）。问「第N场拍了多少条」用它。",
            "parameters": {
                "type": "object",
                "properties": {
                    "scene_ref": {
                        "type": "string",
                        "description": "场次引用，如 '第一场' / '72' / 'Scene_3A'",
                    },
                    "status": {
                        "type": "string",
                        "description": "可选状态过滤：keep/ng/pass/tbd，不填则统计全部",
                    },
                },
                "required": ["scene_ref"],
            },
        },
    }


def build_get_scene_info_tool() -> dict:
    return {
        "type": "function",
        "function": {
            "name": "get_scene_info",
            "description": "返回某场次的地点/内外景/时间/拍摄日期/角色数。问「第N场在哪拍」用它。",
            "parameters": {
                "type": "object",
                "properties": {
                    "scene_ref": {
                        "type": "string",
                        "description": "场次引用，如 '第72场' / '72' / 'Scene_72'",
                    },
                },
                "required": ["scene_ref"],
            },
        },
    }


def build_list_characters_tool() -> dict:
    return {
        "type": "function",
        "function": {
            "name": "list_characters",
            "description": "返回某场次剧本里出现的角色清单。问「这场有几个角色/都有谁」用它。",
            "parameters": {
                "type": "object",
                "properties": {
                    "scene_ref": {
                        "type": "string",
                        "description": "场次引用，如 '第3场' / '3' / 'Scene_3'",
                    },
                },
                "required": ["scene_ref"],
            },
        },
    }


def build_search_script_lines_tool() -> dict:
    return {
        "type": "function",
        "function": {
            "name": "search_script_lines",
            "description": "按关键词全文检索剧本台词，返回匹配行。问「哪句台词提到X / 某句台词在第几行」用它。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "要检索的台词关键词",
                    },
                    "scene_ref": {
                        "type": "string",
                        "description": "可选场次引用，限定检索范围；不填则全剧本检索",
                    },
                },
                "required": ["query"],
            },
        },
    }


def build_query_database_tool() -> dict:
    return {
        "type": "function",
        "function": {
            "name": "query_database",
            "description": (
                "万能笔：当上面四个工具都覆盖不了时，写一条只读 SQL 直接查数据库。"
                "只允许 SELECT。主要表：scenes(scene_id,scene_code,location,int_ext,time_of_day,shoot_date)、"
                "takes(take_id,scene_id,shot,take_number,status,deleted_at)、"
                "script_lines(line_no,character,text,script_id)、scripts(script_id,scene_id)。"
                "软删行 deleted_at IS NOT NULL，统计 take 记得加 deleted_at IS NULL。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "sql": {
                        "type": "string",
                        "description": "一条只读 SELECT 语句（单句，不要分号分隔多句）",
                    },
                },
                "required": ["sql"],
            },
        },
    }


def build_qp_tools() -> list[dict]:
    """返回 QP 全部 5 个工具 schema（顺序固定，供 config.query_session 与测试用）。"""
    return [
        build_count_takes_tool(),
        build_get_scene_info_tool(),
        build_list_characters_tool(),
        build_search_script_lines_tool(),
        build_query_database_tool(),
    ]
