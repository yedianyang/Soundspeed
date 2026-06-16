"""语音 QP 评测 harness(B 类门控)。用户真实录音 → run_voice_dispatch 全链路(hop A/B→executor→续跳)
→ 捕获广播答案 → 关键事实判分。判分复用 test_qp_eval.judge_answer。

跑法:GEMMA_MODEL_PATH=<gguf> .venv/bin/python -m pytest backend/tests/test_qp_voice_eval.py -q -s -m qp_voice_eval
(mmproj 经 HF cache 自动解析;语音单次含音频编码,显著慢于文本)
"""
import json
import os
from pathlib import Path

import pytest

from backend.tests.test_qp_eval import judge_answer

FIXTURE = Path(__file__).parent / "fixtures" / "qp_voice_eval.jsonl"
AUDIO_DIR = Path(__file__).parent / "fixtures"
RUNS_PER_CASE = 3
# 基线 2026-06-11:两轮全 1.000(12/12,用户真实录音含两条防顶替哨兵「沈默」全过)。
# FLOOR 0.85:容 1 次 run 抖动(11/12=0.917 过),2 次(10/12=0.833)即红。小样本(4 case)从严。
ACCURACY_FLOOR = 0.85


def _load_cases() -> list[dict]:
    with FIXTURE.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


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

    d = DAL(tmp_path_factory.mktemp("qp_voice_eval") / "eval.db")
    seed_qp_eval_db(d)
    yield d
    d.close()


@pytest.mark.qp_voice_eval
@pytest.mark.skipif(
    not os.environ.get("GEMMA_MODEL_PATH"),
    reason="GEMMA_MODEL_PATH 未设置,跳过语音 QP 评测 harness",
)
@pytest.mark.asyncio
async def test_qp_voice_accuracy_floor(qp_service, seeded_dal, monkeypatch) -> None:
    from backend.pipelines import voice_dispatch
    from backend.pipelines.qp_query import build_scene_catalog

    captured: list[str] = []

    async def _capture_broadcast(answer_text, conn_id, *, client_id=None, dal=None, service=None, cm=None):
        captured.append(answer_text)

    monkeypatch.setattr(voice_dispatch, "_schedule_qp_broadcast", _capture_broadcast)
    catalog = build_scene_catalog(seeded_dal)

    cases = _load_cases()
    total = passed = 0
    print()
    for c in cases:
        audio = (AUDIO_DIR / c["audio"]).read_bytes()
        case_pass = 0
        last_reason = ""
        for _ in range(RUNS_PER_CASE):
            total += 1
            captured.clear()
            try:
                result = await voice_dispatch.run_voice_dispatch(
                    audio,
                    conn_id="eval",
                    ts=0.0,
                    client_id=None,
                    dal=seeded_dal,
                    service=qp_service,
                    cm=None,
                    scene_context=catalog,
                    np_input=None,
                    voice_runner=None,
                )
            except Exception as exc:  # noqa: BLE001
                last_reason = f"调用异常: {exc}"
                continue
            if not captured:
                last_reason = f"无广播答案(kind={result.get('kind') if isinstance(result, dict) else result!r})"
                continue
            answer = captured[-1]
            ok, reason = judge_answer(answer, c)
            if ok:
                passed += 1
                case_pass += 1
            else:
                last_reason = f"{reason} ← 答:{answer[:80]!r}"
        flag = "OK " if case_pass == RUNS_PER_CASE else "!! "
        print(f"{flag}{case_pass}/{RUNS_PER_CASE}  [{c['id']}]  {last_reason or '(pass)'}", flush=True)
    acc = passed / total if total else 0.0
    print(f"\n总计 {passed}/{total} = {acc:.3f}  (FLOOR {ACCURACY_FLOOR})", flush=True)
    assert acc >= ACCURACY_FLOOR, f"语音 QP 准确率 {acc:.3f} 跌破门控 {ACCURACY_FLOOR}"
