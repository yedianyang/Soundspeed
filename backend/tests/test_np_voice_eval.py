"""NP 语音 A/B 评测 harness。同一批真实录音，两步式（现生产：ASR 转写→extract_np）
vs 一步式 v2（音频直推 extract_np + user 帧哨兵句），逐字段判分，输出两模式对比行。

本文件为纯报告工具，不设准确率 FLOOR 断言（A/B 收口切生产后改造成门控）。
断言只确认两模式都真的跑了（total > 0）。

跑法：
  GEMMA_MODEL_PATH=/path/to/gemma-4-E4B-it-Q4_K_M.gguf \\
    .venv/bin/python -m pytest backend/tests/test_np_voice_eval.py -q -s -m np_voice_eval

无模型常跑（schema 检查）：
  .venv/bin/python -m pytest backend/tests/test_np_voice_eval.py -q

排除本文件：-m "not np_voice_eval"
"""

import dataclasses
import json
import os
from pathlib import Path

import pytest

# 跨测试复用判分（先例：test_qp_voice_eval import judge_answer）：
# _judge=结构字段逐字段 exact（take_ordinals sorted 比）；judge_answer=关键词外层 AND 内层 OR。
# test_np_extract_eval 的模块级 pytestmark 只影响其测试收集，不影响 import（已验证无副作用）。
from backend.tests.test_np_extract_eval import _judge
from backend.tests.test_qp_eval import judge_answer

FIXTURE = Path(__file__).parent / "fixtures" / "np_voice_eval.jsonl"
AUDIO_DIR = Path(__file__).parent / "fixtures"
RUNS_PER_CASE = 3

# 与 test_np_extract_eval 保持一致（上下文行格式一致）。
CONTEXT = "当前：第1场 / 第1进 / 当前活跃第3条；上一条=第2条。"

# NPExtraction 合法字段全集（用于 schema 检查）
_VALID_NP_FIELDS = frozenset(
    {"scene_ordinal", "shot_ordinal", "take_ordinals", "deictic", "mark", "note_text", "note_category"}
)


def _load_cases() -> list[dict]:
    with FIXTURE.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def _judge_note_text(note_text: str, note_text_keywords: list[list[str]]) -> tuple[bool, str]:
    """关键词判分：外层全须（AND），内层任一（OR）。

    取巧复用 test_qp_eval.judge_answer 语义：把 note_text_keywords 包成
    {"must_contain_all": kw, "must_not_contain": []} 调它。
    """
    case_like = {"must_contain_all": note_text_keywords, "must_not_contain": []}
    return judge_answer(note_text, case_like)


async def _run_one_step(audio: bytes, svc, context_line: str):
    """一步式 v2 runner（harness 内部 helper）。

    音频直推 forced extract_np + user 帧哨兵句「没听到明确条/次编号就填空数组，不要编造」。
    A/B 赢了才进生产 np_extract.py，替代 run_extract_np_voice。

    等价于探针一步式 v2（docs/superpowers/specs/2026-06-11-np-voice-design.md §1）：
    直接把音频喂 infer_voice_tool，不经 ASR 转写步骤，消除转写污染
    （「噪声」→「操聲」、幻觉词等）。迁生产时从该 design doc 找完整上下文。
    """
    from backend.llm.multimodal import AUDIO_SENTINEL
    from backend.llm.tools.note_extract import EXTRACT_NP_TOOL_NAME
    from backend.pipelines.np_extract import _build_extract_system_prompt, parse_extract_tool_call

    system = _build_extract_system_prompt() + "\n" + context_line
    messages = [
        {"role": "system", "content": system},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "听这段录音师的话,提取成固定结构。第N次/第N条=take_ordinals 数组元素;没听到明确的条/次编号就填空数组,不要编造。"},
                {"type": "image_url", "image_url": {"url": AUDIO_SENTINEL}},
            ],
        },
    ]
    tool_call = await svc.infer_voice_tool(
        messages, audio, task_type="note_extract", priority=1, timeout=120.0,
        tool_choice={"type": "function", "function": {"name": EXTRACT_NP_TOOL_NAME}},
    )
    return parse_extract_tool_call(tool_call)


# ── fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def np_service():
    """单实例 LLMService（module-scope，同 test_qp_voice_eval 模式）。"""
    from backend.llm.service import _reset_service, get_service

    _reset_service()
    svc = get_service()
    yield svc
    _reset_service()


# ── A/B 主测试 ─────────────────────────────────────────────────────────────────

@pytest.mark.np_voice_eval
@pytest.mark.skipif(
    not os.environ.get("GEMMA_MODEL_PATH"),
    reason="GEMMA_MODEL_PATH 未设置,跳过语音 NP A/B 评测 harness",
)
@pytest.mark.asyncio
async def test_np_voice_ab(np_service) -> None:
    from backend.pipelines.np_extract import run_extract_np_voice

    cases = _load_cases()

    modes = {
        "two_step": lambda audio: run_extract_np_voice(audio, np_service, timeout=120.0, context_line=CONTEXT),
        "one_step": lambda audio: _run_one_step(audio, np_service, CONTEXT),
    }

    totals: dict[str, int] = {m: 0 for m in modes}
    passed_counts: dict[str, int] = {m: 0 for m in modes}
    error_counts: dict[tuple[str, str], int] = {}  # (case_id, mode) → 调用异常次数

    print()
    for c in cases:
        audio = (AUDIO_DIR / c["audio"]).read_bytes()
        for mode_name, runner in modes.items():
            case_pass = 0
            last_reason = ""
            for _ in range(RUNS_PER_CASE):
                totals[mode_name] += 1
                try:
                    extraction = await runner(audio)
                except Exception as exc:  # noqa: BLE001
                    error_counts[(c["id"], mode_name)] = error_counts.get((c["id"], mode_name), 0) + 1
                    last_reason = f"调用异常: {exc}"
                    continue

                ext_dict = dataclasses.asdict(extraction)

                ok_fields, reason_fields = _judge(c["expected"], c["check"], ext_dict)
                note_text = ext_dict.get("note_text", "")
                ok_kw, reason_kw = _judge_note_text(note_text, c["note_text_keywords"])

                if ok_fields and ok_kw:
                    passed_counts[mode_name] += 1
                    case_pass += 1
                    last_reason = ""
                else:
                    parts = []
                    if not ok_fields:
                        parts.append(reason_fields)
                    if not ok_kw:
                        parts.append(f"note_text 关键词: {reason_kw} ← {note_text!r:.60}")
                    last_reason = "; ".join(parts)

            n_err = error_counts.get((c["id"], mode_name), 0)
            err_suffix = f"({n_err}err)" if n_err else ""
            flag = "OK " if case_pass == RUNS_PER_CASE else "!! "
            print(
                f"{flag}{case_pass}/{RUNS_PER_CASE}{err_suffix}  [{c['id']}]  mode={mode_name}  "
                f"{last_reason or '(pass)'}",
                flush=True,
            )

    # 汇总对比行（err>0 时附带显示）
    def _fmt(mode: str) -> str:
        total = totals[mode]
        good = passed_counts[mode]
        n_err = sum(v for (_, m), v in error_counts.items() if m == mode)
        acc = good / total if total else 0.0
        err_suffix = f"({n_err}err)" if n_err else ""
        return f"{good}/{total}{err_suffix}={acc:.3f}"

    print(f"\nA/B: two_step {_fmt('two_step')} vs one_step {_fmt('one_step')}", flush=True)

    # 唯一断言：两模式都实际跑了
    assert totals["two_step"] > 0, "two_step 没有跑任何 case"
    assert totals["one_step"] > 0, "one_step 没有跑任何 case"


# ── 契约测试（无模型常跑）────────────────────────────────────────────────────────

def test_np_voice_fixture_schema() -> None:
    """每条 fixture 契约检查（无模型，常态 CI 可跑）：

    - 必须字段存在：id / audio / utterance / expected / check / note_text_keywords
    - audio 文件存在且 RIFF 头
    - expected 每个键 ∈ NPExtraction 七字段
    - check 每个键 ∈ NPExtraction 七字段，且 check ⊆ expected.keys()
    """
    cases = _load_cases()
    assert cases, "np_voice_eval.jsonl 为空"

    for c in cases:
        cid = c.get("id", "<无id>")

        # 必须字段
        for required_key in ("id", "audio", "utterance", "expected", "check", "note_text_keywords"):
            assert required_key in c, f"[{cid}] 缺字段 {required_key!r}"

        # audio 文件存在且 RIFF 头
        audio_path = AUDIO_DIR / c["audio"]
        assert audio_path.exists(), f"[{cid}] 音频文件不存在: {audio_path}"
        header = audio_path.read_bytes()[:4]
        assert header == b"RIFF", f"[{cid}] 非 RIFF wav 文件，头={header!r}"

        # expected 字段名合法
        expected: dict = c["expected"]
        assert isinstance(expected, dict), f"[{cid}] expected 非对象"
        for key in expected:
            assert key in _VALID_NP_FIELDS, f"[{cid}] expected 含非法字段 {key!r}"

        # check 字段名合法，且是 expected 的子集
        check: list = c["check"]
        assert isinstance(check, list), f"[{cid}] check 非数组"
        for key in check:
            assert key in _VALID_NP_FIELDS, f"[{cid}] check 含非法字段 {key!r}"
            assert key in expected, f"[{cid}] check 字段 {key!r} 不在 expected 中"

        # note_text_keywords 结构：外层 list，内层 list[str]
        kws = c["note_text_keywords"]
        assert isinstance(kws, list), f"[{cid}] note_text_keywords 非数组"
        for i, group in enumerate(kws):
            assert isinstance(group, list) and all(isinstance(s, str) for s in group), (
                f"[{cid}] note_text_keywords[{i}] 内层须为 list[str]"
            )
            assert group, f"{c['id']}: note_text_keywords 内层组不能为空"
