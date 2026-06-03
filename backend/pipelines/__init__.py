"""Pipelines 包：各种 LLM Pipeline 的实现。

当前模块：
  l2_take: L2 Pipeline（台词 diff 检测）
  np_note: NP Pipeline（Note 归置）
"""
from backend.pipelines.l2_take import L2Input, L2Output, L2ParseError, LineMatch, CorrectedSegment, run_l2_take
from backend.pipelines.np_note import NPInput, NPOutput, NPParseError, run_np_note

__all__ = [
    "L2Input",
    "L2Output",
    "L2ParseError",
    "LineMatch",
    "CorrectedSegment",
    "run_l2_take",
    "NPInput",
    "NPOutput",
    "NPParseError",
    "run_np_note",
]
