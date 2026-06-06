"""report_script_analysis 工具定义。

build_l2_tool() 构造符合 OpenAI function calling 格式的 tool dict。

diff_type enum 值取自 backend.pipelines.l2_constants._VALID_DIFF_TYPES（module 级 import），
与 l2_take.py 同源，保证 schema 与 pipeline 校验逻辑一致（spec §4.1 约束）。

无循环 import：本模块只依赖中性的 l2_constants（不 import config / l2_take），
config.py 反过来 module 级 import 本模块的 build_l2_tool，依赖单向无环。
（enum 抽到 l2_constants 正是为此——若留在 l2_take，本模块 import l2_take 会与
 config→tools、l2_take→config 形成环。）
"""

from __future__ import annotations

# l2_constants 是中性叶子模块（不 import config / l2_take），可在 module 级安全 import。
from backend.pipelines.l2_constants import _VALID_DIFF_TYPES

# items 子树（idx/original/corrected）两个 builder 完全相同，单点定义。
_CORRECTED_SEGMENT_ITEMS: dict = {
    "type": "object",
    "properties": {
        "idx": {
            "type": "integer",
            "description": "转录段索引（0-indexed）",
        },
        "original": {
            "type": "string",
            "description": "原始转录文本",
        },
        "corrected": {
            "type": "string",
            "description": "纠错后文本",
        },
    },
    "required": ["idx", "original", "corrected"],
}


def _corrected_segments_property(description: str) -> dict:
    """返回 corrected_segments array property 字典，items 子树单点复用。

    两个 builder 只有外层 description 不同（无剧本版 vs 有剧本版），
    items 子树（idx/original/corrected 三 property + required）完全共享。
    """
    return {
        "type": "array",
        "description": description,
        "items": _CORRECTED_SEGMENT_ITEMS,
    }


def build_l2_no_script_tool() -> dict:
    """构造 report_corrections_only OpenAI 风格 tool dict（无剧本纯纠错路径）。

    只含 corrected_segments 一个字段，不含 script_diff_summary / line_matches，
    grammar 层面约束模型不能生成剧本比对相关输出。

    Returns:
        符合 OpenAI function calling spec 的 tool 字典，
        type="function"，name="report_corrections_only"。
    """
    return {
        "type": "function",
        "function": {
            "name": "report_corrections_only",
            "description": "报告转录文本中的错别字修正结果，只含纠错条目，不含剧本比对信息。",
            "parameters": {
                "type": "object",
                "properties": {
                    "corrected_segments": _corrected_segments_property(
                        "需要纠错的转录片段（仅限错别字/口误），无需修正时输出空列表 []"
                    ),
                },
                "required": ["corrected_segments"],
            },
        },
    }


def build_l2_tool() -> dict:
    """构造 report_script_analysis OpenAI 风格 tool dict。

    Returns:
        符合 OpenAI function calling spec 的 tool 字典，
        type="function"，name="report_script_analysis"。
    """

    return {
        "type": "function",
        "function": {
            "name": "report_script_analysis",
            "description": (
                "报告本次 take 的转录文本与剧本台词对比结果，"
                "包含逐行匹配情况、替换/遗漏位置和纠错后文本。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "script_diff_summary": {
                        "type": "string",
                        "description": "整体对比摘要，50 字以内",
                    },
                    "line_matches": {
                        "type": "array",
                        "description": "逐行匹配结果",
                        "items": {
                            "type": "object",
                            "properties": {
                                "line_no": {
                                    "type": "integer",
                                    "description": "剧本行号（1-indexed）",
                                },
                                "diff_type": {
                                    "type": "string",
                                    "enum": sorted(_VALID_DIFF_TYPES),
                                    "description": "匹配类型，与代码 _VALID_DIFF_TYPES 同源",
                                },
                                "detail": {
                                    "type": "string",
                                    "description": "具体差异描述，substitution 时写出实际说的内容",
                                },
                            },
                            # detail 不入 required：match 行无差异可省略，避免 grammar
                            # 逼模型给每行都生成填充文本。validator 用 .get("detail")
                            # 容忍缺失（None）。
                            "required": ["line_no", "diff_type"],
                        },
                    },
                    "corrected_segments": _corrected_segments_property(
                        "需要纠错的转录片段（仅限错别字/口误，不含剧本差异）"
                    ),
                },
                "required": ["script_diff_summary", "line_matches", "corrected_segments"],
            },
        },
    }
