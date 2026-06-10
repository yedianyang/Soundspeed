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
# 基线 2026-06-11 实测 17/30=0.567(commit ecdaaa6,E4B Q4_K_M 真模型):
# 挂的 case 与修复任务一一对应——mic-advice(prompt 禁建议)、agg-time(无聚合工具,编造 10日6夜)、
# search-line(FTS trigram 2字查询 0 命中)、content-16(无场内容工具)、single-count 2/3(转述失误)。
# FLOOR = 基线 - 0.10 向下取一位;后续修复任务逐步抬。
ACCURACY_FLOOR = 0.46


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


@pytest.fixture(scope="module")
def qp_service():
    from backend.llm.service import _reset_service, get_service

    _reset_service()
    svc = get_service()
    yield svc
    _reset_service()


@pytest.fixture(scope="module")
def seeded_dal(tmp_path_factory):
    from backend.db.dal import DAL
    from backend.tests.qp_eval_seed import seed_qp_eval_db

    d = DAL(tmp_path_factory.mktemp("qp_eval") / "eval.db")
    seed_qp_eval_db(d)
    yield d
    d.close()


@pytest.mark.qp_eval
@pytest.mark.skipif(
    not os.environ.get("GEMMA_MODEL_PATH"),
    reason="GEMMA_MODEL_PATH 未设置,跳过 QP 评测 harness",
)
@pytest.mark.asyncio
async def test_qp_accuracy_floor(qp_service, seeded_dal) -> None:
    from backend.pipelines.qp_query import run_qp_query

    cases = _load_cases()
    total = passed = 0
    print()
    for c in cases:
        case_pass = 0
        last_reason = ""
        last_trace: list[dict] = []
        for _ in range(RUNS_PER_CASE):
            total += 1
            trace: list[dict] = []
            try:
                answer = await run_qp_query(
                    text=c["question"], dal=seeded_dal, service=qp_service,
                    timeout=120.0, trace=trace,
                )
            except Exception as exc:  # noqa: BLE001
                last_reason, last_trace = f"调用异常: {exc}", trace
                continue
            ok, reason = judge_answer(answer, c)
            last_trace = trace  # 无论 pass/fail 都记,用于诊断
            if ok:
                passed += 1
                case_pass += 1
            else:
                last_reason = f"{reason} ← 答:{answer[:80]!r}"
        flag = "OK " if case_pass == RUNS_PER_CASE else "!! "
        tools_used = "→".join(t["tool"] for t in last_trace) or "(无工具)"
        print(f"{flag}{case_pass}/{RUNS_PER_CASE}  [{c['id']}] {tools_used}  {last_reason or '(pass)'}", flush=True)
    acc = passed / total if total else 0.0
    print(f"\n总计 {passed}/{total} = {acc:.3f}  (FLOOR {ACCURACY_FLOOR})", flush=True)
    assert acc >= ACCURACY_FLOOR, f"QP 准确率 {acc:.3f} 跌破门控 {ACCURACY_FLOOR}"
