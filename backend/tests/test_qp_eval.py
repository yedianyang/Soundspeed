"""QP 评测 harness(B 类门控)。打真 E4B 跑 run_qp_query 全管线,关键事实判分,断言准确率 FLOOR。

判分函数 judge_answer 是纯函数,单测无模型常跑;真模型部分 marker=qp_eval、GEMMA_MODEL_PATH gate。
跑法:GEMMA_MODEL_PATH=<gguf> .venv/bin/python -m pytest backend/tests/test_qp_eval.py -q -s -m qp_eval
"""
import json
import os
from pathlib import Path

import pytest

FIXTURE = Path(__file__).parent / "fixtures" / "qp_eval.jsonl"
RUNS_PER_CASE = 3
ACCURACY_FLOOR = 0.0  # Task 6 基线实测后定为 基线-0.10,此前 0.0 占位(只产报告不拦截)


def _load_cases() -> list[dict]:
    with FIXTURE.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def judge_answer(answer: str, case: dict) -> tuple[bool, str]:
    """外层全须命中,内层任一命中;must_not_contain 命中即败。"""
    reasons = []
    for group in case["must_contain_all"]:
        if not any(s in answer for s in group):
            reasons.append(f"缺关键事实(任一): {group}")
    for s in case["must_not_contain"]:
        if s in answer:
            reasons.append(f"出现禁词: {s!r}")
    return (not reasons), "; ".join(reasons)


def test_judge_answer_pass_and_fail() -> None:
    case = {"must_contain_all": [["4", "四"], ["夜"]], "must_not_contain": ["没有"]}
    ok, _ = judge_answer("第16场是夜戏,有四个角色。", case)
    assert ok
    ok, _ = judge_answer("有4个角色,这场是夜戏。", case)
    assert ok  # OR-group 只命中「4」一个候选也过
    ok, reason = judge_answer("第16场是夜戏。", case)
    assert not ok and "缺关键事实" in reason
    ok, reason = judge_answer("有4个角色,夜戏,但没有更多信息。", case)
    assert not ok and "出现禁词" in reason
