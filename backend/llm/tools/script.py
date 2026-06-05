"""report_script_analysis 工具定义。

build_l2_tool() 构造符合 OpenAI function calling 格式的 tool dict。

diff_type enum 值从 backend.pipelines.l2_take._VALID_DIFF_TYPES 函数级 lazy import，
保证与 pipeline 校验逻辑同源（spec §4.1 约束）。

循环 import 规避策略：
  config.py  → module 级不 import script.py；_build_l2_task_config() 函数级 lazy import build_l2_tool
  script.py  → build_l2_tool() 函数级 lazy import _VALID_DIFF_TYPES（不在 module 级）
  l2_take.py → 不导入 script.py / config.py（TYPE_CHECKING 下除外）

整个链路无 module 级循环，函数调用时所有模块均已初始化完毕。
"""

from __future__ import annotations

# l2_constants 是中性模块，不依赖 config，可在 module 级安全 import。
from backend.pipelines.l2_constants import _VALID_DIFF_TYPES


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
                    "corrected_segments": {
                        "type": "array",
                        "description": "需要纠错的转录片段（仅限错别字/口误，不含剧本差异）",
                        "items": {
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
                        },
                    },
                },
                "required": ["script_diff_summary", "line_matches", "corrected_segments"],
            },
        },
    }
