"""NP 提取评测 harness（B 类门控）。打真 E4B，固定解码，跑 np_extract.jsonl 全用例，
按每例 load-bearing 字段判分，断言总准确率 FLOOR。schema/prompt/fixture 任一退化 → 红。

默认 skip（GEMMA_MODEL_PATH 未设）。跑法：
  GEMMA_MODEL_PATH=/Users/.../models/gemma-4-E4B-it-Q4_K_M.gguf uv run pytest \
    backend/tests/test_np_extract_eval.py -q -s
只跑评测：加 -m np_eval。全量排除：-m "not np_eval and not smoke"。
"""

import json
import os
from pathlib import Path

import pytest

# 导入顺序坑（test_np_function_calling.py:24 注释）：pipelines 在 config/service 前，避免
# config↔pipelines 循环初始化。本 harness 直连 client，不经 pipeline，但保持习惯无害。
from backend.llm.tools.note_extract import EXTRACT_NP_TOOL_NAME, build_extract_np_tool

pytestmark = [
    pytest.mark.np_eval,
    pytest.mark.skipif(
        not os.environ.get("GEMMA_MODEL_PATH"),
        reason="GEMMA_MODEL_PATH 未设置，跳过 NP 提取评测 harness",
    ),
]

FIXTURE = Path(__file__).parent / "fixtures" / "np_extract.jsonl"
RUNS_PER_CASE = 3
ACCURACY_FLOOR = 0.80  # 6 字段基线 21/24≈0.875；加第 7 字段 + temp>0 方差，留头距设 0.80

# spike 复跑系统 prompt（probe_extract_flat.py:81，21/24）。note_category 句子补在末尾。
# 上下文行内嵌（当前第1场/第1进/活跃第3条/上一条第2条）—— 与 fixture expected 一致。
SYSTEM_PROMPT = (
    "你是场记助手。把录音师这句话提取成固定结构，每个字段都要填（用哨兵值表示'没有'）。"
    "映射：第N进/第N镜=shot_ordinal，第N条/第N次=take_ordinal，第N场=scene_ordinal；"
    "当前场/镜填0；这条=deictic current，上一条/刚才那条=deictic prev，用了编号=deictic none；"
    "过/通过=mark pass，保/留=keep，废/NG/不行=ng，没打标意图=mark none；"
    "note_text 永远填这句话里要记下来的实际描述（包括技术问题本身那句，如'收音有点小'），"
    "只有完全没有可记内容时才填空串；note_category 只是给这段 note 贴标签，不替代 note_text："
    "技术问题(收音小/灯光暗/穿帮/对焦虚/杂音)=issue，否则=note。"
    "当前：第1场 / 第1进 / 当前活跃第3条；上一条=第2条。"
)


def _load_cases() -> list[dict]:
    with FIXTURE.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def _judge(expected: dict, check: list[str], args: dict) -> tuple[bool, str]:
    """只比 check 列出的 load-bearing 字段（镜像 probe judges）。note_text:'*'=非空断言。"""
    reasons = []
    for field in check:
        exp = expected[field]
        got = args.get(field)
        if field == "note_text" and exp == "*":
            ok = bool(got and isinstance(got, str) and got.strip())
        elif field == "take_ordinals":
            ok = sorted(got or []) == sorted(exp)
        else:
            ok = got == exp
        if not ok:
            reasons.append(f"{field}={got!r} (期望 {exp!r})")
    return (not reasons), "; ".join(reasons)


@pytest.fixture(scope="module")
def gemma_client():
    """单实例真模型（17GB，Metal teardown 已知崩；结果在崩前落定，故 module-scope 复用）。"""
    from backend.llm.client import GemmaClient
    from backend.llm.service import resolve_model_path

    path = resolve_model_path(download=False)
    assert path, "resolve_model_path 返回空：GEMMA_MODEL_PATH / HF cache 均无模型"
    client = GemmaClient(model_path=path)
    yield client
    # 不显式关闭：Metal teardown GGML_ASSERT 已知问题，进程退出时清理。


def _run_once(client, utterance: str) -> dict:
    resp = client.create_chat_completion(
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": utterance},
        ],
        tools=[build_extract_np_tool()],
        tool_choice={"type": "function", "function": {"name": EXTRACT_NP_TOOL_NAME}},
        temperature=0.2,
        max_tokens=256,
    )
    tc = resp["choices"][0]["message"]["tool_calls"][0]
    return json.loads(tc["function"]["arguments"])


def test_extract_np_accuracy_floor(gemma_client) -> None:
    cases = _load_cases()
    total = 0
    passed = 0
    print()  # -s 下表头
    for c in cases:
        case_pass = 0
        last_reason = ""
        for _ in range(RUNS_PER_CASE):
            total += 1
            try:
                args = _run_once(gemma_client, c["utterance"])
            except Exception as exc:  # noqa: BLE001
                last_reason = f"调用异常: {exc}"
                continue
            ok, reason = _judge(c["expected"], c["check"], args)
            if ok:
                passed += 1
                case_pass += 1
            else:
                last_reason = reason
        flag = "OK " if case_pass == RUNS_PER_CASE else "!! "
        print(f"{flag}{case_pass}/{RUNS_PER_CASE}  {c['utterance']!r}  {last_reason}", flush=True)
    acc = passed / total if total else 0.0
    print(f"\n总计 {passed}/{total} = {acc:.3f}  (FLOOR {ACCURACY_FLOOR})", flush=True)
    assert acc >= ACCURACY_FLOOR, f"NP 提取准确率 {acc:.3f} 跌破门控 {ACCURACY_FLOOR}"
