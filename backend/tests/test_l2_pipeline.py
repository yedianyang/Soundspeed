"""1.G L2 Pipeline 单元测试。

覆盖 docs/specs/2026-05-27-l2-pipeline.md §7 全部 14 个用例。
全部使用 AsyncMock 注入 llm_service.infer，不加载真实模型。
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.llm.service import LLMService
from backend.pipelines.l2_take import (
    L2Input,
    L2Output,
    L2ParseError,
    LineMatch,
    run_l2_take,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def stub_segments() -> list[dict]:
    """3 段 ch1 转录，含 2 个 speaker + 1 个 None speaker。"""
    return [
        {
            "speaker": "SPEAKER_00",
            "text": "我不想走，请别拦着我。",
            "start_frame": 0,
            "end_frame": 16000,
        },
        {
            "speaker": "SPEAKER_01",
            "text": "你必须留下来。",
            "start_frame": 16000,
            "end_frame": 32000,
        },
        {
            "speaker": None,
            "text": "（背景噪音片段）",
            "start_frame": 32000,
            "end_frame": 40000,
        },
    ]


@pytest.fixture
def stub_script_lines() -> list[dict]:
    """3 行剧本，含角色行 + 舞台指示行。"""
    return [
        {"line_no": 1, "character": "主角", "text": "我不想走，请别拦着我。"},
        {"line_no": 2, "character": "配角", "text": "你必须留下来，不然一切都完了。"},
        {"line_no": 3, "character": None, "text": "（配角伸手拦住主角）"},
    ]


@pytest.fixture
def l2_input(stub_segments: list[dict], stub_script_lines: list[dict]) -> L2Input:
    return L2Input(
        take_id=1,
        scene_id=1,
        take_number=3,
        transcript_segments=stub_segments,
        script_lines=stub_script_lines,
        previous_notes=[],
    )


def _mock_llm(response: str) -> MagicMock:
    """创建注入 AsyncMock.infer 的 LLMService mock。"""
    svc = MagicMock(spec=LLMService)
    svc.infer = AsyncMock(return_value=response)
    return svc


def _normal_response() -> str:
    return json.dumps(
        {
            "script_diff_summary": "台词基本匹配，第2行配角漏说「不然一切都完了」。",
            "line_matches": [
                {"line_no": 1, "diff_type": "match", "detail": None},
                {"line_no": 2, "diff_type": "missing", "detail": "漏词：「不然一切都完了」"},
                {"line_no": 3, "diff_type": "match", "detail": None},
            ],
        },
        ensure_ascii=False,
    )


# ---------------------------------------------------------------------------
# 测试用例
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_l2_take_normal(l2_input: L2Input) -> None:
    """正常返回：合法 JSON，L2Output 字段正确，diff_type 全部合法枚举。"""
    svc = _mock_llm(_normal_response())
    result = await run_l2_take(l2_input, svc)

    assert isinstance(result, L2Output)
    assert result.script_diff_summary is not None
    assert len(result.line_matches) == 3
    valid_types = {"match", "missing", "substitution", "insertion"}
    for lm in result.line_matches:
        assert isinstance(lm, LineMatch)
        assert lm.diff_type in valid_types


@pytest.mark.asyncio
async def test_run_l2_take_empty_transcript(stub_script_lines: list[dict]) -> None:
    """transcript_segments 为空列表，pipeline 不抛错，line_matches 为空列表。"""
    inp = L2Input(
        take_id=2,
        scene_id=1,
        take_number=1,
        transcript_segments=[],
        script_lines=stub_script_lines,
        previous_notes=[],
    )
    empty_response = json.dumps(
        {"script_diff_summary": None, "line_matches": []}, ensure_ascii=False
    )
    svc = _mock_llm(empty_response)
    result = await run_l2_take(inp, svc)

    assert result.line_matches == []


@pytest.mark.asyncio
async def test_run_l2_take_empty_script_lines(stub_segments: list[dict]) -> None:
    """script_lines 为空列表，pipeline 不抛错，line_matches=[]，script_diff_summary=None。"""
    inp = L2Input(
        take_id=3,
        scene_id=1,
        take_number=1,
        transcript_segments=stub_segments,
        script_lines=[],
        previous_notes=[],
    )
    no_script_response = json.dumps(
        {"script_diff_summary": None, "line_matches": []}, ensure_ascii=False
    )
    svc = _mock_llm(no_script_response)
    result = await run_l2_take(inp, svc)

    assert result.line_matches == []
    assert result.script_diff_summary is None


@pytest.mark.asyncio
async def test_run_l2_take_invalid_json_raises(l2_input: L2Input) -> None:
    """LLM 返回非 JSON 字符串，抛 L2ParseError，e.cause 为 JSONDecodeError。"""
    svc = _mock_llm("这不是JSON内容，只是一段文本。")
    with pytest.raises(L2ParseError) as exc_info:
        await run_l2_take(l2_input, svc)

    import json as _json

    assert isinstance(exc_info.value.cause, _json.JSONDecodeError)


@pytest.mark.asyncio
async def test_run_l2_take_markdown_wrapped_json(l2_input: L2Input) -> None:
    """LLM 返回 ```json ... ``` 包裹的 JSON，pipeline 能成功解析。"""
    wrapped = "```json\n" + _normal_response() + "\n```"
    svc = _mock_llm(wrapped)
    result = await run_l2_take(l2_input, svc)

    assert isinstance(result, L2Output)
    assert len(result.line_matches) == 3


@pytest.mark.asyncio
async def test_run_l2_take_missing_field_raises(l2_input: L2Input) -> None:
    """LLM 返回缺少 line_matches 字段的 JSON，抛 L2ParseError。"""
    bad_response = json.dumps({"script_diff_summary": "没有line_matches字段"})
    svc = _mock_llm(bad_response)
    with pytest.raises(L2ParseError):
        await run_l2_take(l2_input, svc)


@pytest.mark.asyncio
async def test_run_l2_take_invalid_diff_type_raises(l2_input: L2Input) -> None:
    """LLM 返回 diff_type="unknown" 的 JSON，抛 L2ParseError。"""
    bad_response = json.dumps(
        {
            "script_diff_summary": "有非法枚举",
            "line_matches": [
                {"line_no": 1, "diff_type": "unknown", "detail": None},
            ],
        }
    )
    svc = _mock_llm(bad_response)
    with pytest.raises(L2ParseError):
        await run_l2_take(l2_input, svc)


@pytest.mark.asyncio
async def test_run_l2_take_transcript_truncated(stub_script_lines: list[dict]) -> None:
    """transcript_segments 总字符数超 2500，pipeline 截断后仍能调 LLMService。

    验证：user message 中含 WARNING: transcript truncated 标注。
    """
    # 构造超过 2500 字符的 transcript_segments
    long_segments = [
        {
            "speaker": "SPEAKER_00",
            "text": "这是很长的台词文本，用于测试截断逻辑。" * 20,  # 约 400+ 字符
            "start_frame": i * 16000,
            "end_frame": (i + 1) * 16000,
        }
        for i in range(10)  # 10 段，总字符数远超 2500
    ]
    inp = L2Input(
        take_id=4,
        scene_id=1,
        take_number=1,
        transcript_segments=long_segments,
        script_lines=stub_script_lines,
        previous_notes=[],
    )
    svc = _mock_llm(_normal_response())
    result = await run_l2_take(inp, svc)

    assert isinstance(result, L2Output)
    # 验证 infer 被调用，且 messages[1]["content"] 含截断警告
    assert svc.infer.called
    call_args = svc.infer.call_args
    messages = call_args[0][0]  # positional 第一个参数是 messages
    user_message = next(m["content"] for m in messages if m["role"] == "user")
    assert "WARNING: transcript truncated" in user_message


@pytest.mark.asyncio
async def test_run_l2_take_insertion_line_no_minus_one(l2_input: L2Input) -> None:
    """LLM 返回含 line_no=-1, diff_type="insertion" 的输出，pipeline 能正常解析为 LineMatch。"""
    response = json.dumps(
        {
            "script_diff_summary": "演员添加了剧本外台词。",
            "line_matches": [
                {"line_no": 1, "diff_type": "match", "detail": None},
                {"line_no": -1, "diff_type": "insertion", "detail": "额外台词：「等等，我还有话说。」"},
            ],
        },
        ensure_ascii=False,
    )
    svc = _mock_llm(response)
    result = await run_l2_take(l2_input, svc)

    insertion_matches = [lm for lm in result.line_matches if lm.diff_type == "insertion"]
    assert len(insertion_matches) == 1
    assert insertion_matches[0].line_no == -1


@pytest.mark.asyncio
async def test_run_l2_take_previous_notes_in_prompt(
    stub_segments: list[dict], stub_script_lines: list[dict]
) -> None:
    """previous_notes 非空时，user message 包含「历史偏差参考」节。"""
    inp = L2Input(
        take_id=5,
        scene_id=1,
        take_number=2,
        transcript_segments=stub_segments,
        script_lines=stub_script_lines,
        previous_notes=["take 1 偏差摘要：配角漏词明显。"],
    )
    svc = _mock_llm(_normal_response())
    await run_l2_take(inp, svc)

    call_args = svc.infer.call_args
    messages = call_args[0][0]
    user_message = next(m["content"] for m in messages if m["role"] == "user")
    assert "历史偏差参考" in user_message


@pytest.mark.asyncio
async def test_run_l2_take_no_previous_notes_section_absent(
    stub_segments: list[dict], stub_script_lines: list[dict]
) -> None:
    """previous_notes 为空时，user message 不包含「历史偏差参考」节。"""
    inp = L2Input(
        take_id=6,
        scene_id=1,
        take_number=1,
        transcript_segments=stub_segments,
        script_lines=stub_script_lines,
        previous_notes=[],
    )
    svc = _mock_llm(_normal_response())
    await run_l2_take(inp, svc)

    call_args = svc.infer.call_args
    messages = call_args[0][0]
    user_message = next(m["content"] for m in messages if m["role"] == "user")
    assert "历史偏差参考" not in user_message


@pytest.mark.asyncio
async def test_run_l2_take_empty_llm_response_raises(l2_input: L2Input) -> None:
    """LLM 返回空字符串，抛 L2ParseError。"""
    svc = _mock_llm("")
    with pytest.raises(L2ParseError):
        await run_l2_take(l2_input, svc)


@pytest.mark.asyncio
async def test_run_l2_take_timeout_propagated(l2_input: L2Input) -> None:
    """LLMService.infer 抛 asyncio.TimeoutError，pipeline 不吞，让其穿透到 caller。"""
    svc = MagicMock(spec=LLMService)
    svc.infer = AsyncMock(side_effect=asyncio.TimeoutError())
    with pytest.raises(asyncio.TimeoutError):
        await run_l2_take(l2_input, svc, timeout=0.1)


@pytest.mark.asyncio
async def test_run_l2_take_uses_priority_2(l2_input: L2Input) -> None:
    """run_l2_take 调用 llm_service.infer 时 priority=2 且 task_type="l2_take"。"""
    svc = _mock_llm(_normal_response())
    await run_l2_take(l2_input, svc)

    svc.infer.assert_called_once()
    call_kwargs = svc.infer.call_args
    # 检查 keyword 参数
    assert call_kwargs.kwargs.get("task_type") == "l2_take" or call_kwargs[1].get(
        "task_type"
    ) == "l2_take"
    assert call_kwargs.kwargs.get("priority") == 2 or call_kwargs[1].get("priority") == 2


# ---------------------------------------------------------------------------
# P2 #2：script_lines 截断 1000 字符（orchestrator helper）
# ---------------------------------------------------------------------------


def test_truncate_script_lines_caps_at_1000_chars() -> None:
    """50 行各 50 字符，_truncate_script_lines 截断后总字符 <= 1000，超出的行被丢弃。"""
    from backend.core.orchestrator import Orchestrator

    lines = [
        {"line_no": i, "character": "A", "text": "X" * 50}
        for i in range(1, 51)
    ]
    result = Orchestrator._truncate_script_lines(lines, max_chars=1000)
    total_chars = sum(len(item["text"]) for item in result)
    assert total_chars <= 1000
    # 50 行总共 2500 字符，截断后应只有 20 行
    assert len(result) < 50


# ---------------------------------------------------------------------------
# P2 #4：单 segment > 2500 字符保留 tail
# ---------------------------------------------------------------------------


def test_truncate_segments_keeps_tail_of_oversized_segment() -> None:
    """单 segment 4000 字符 -> 截断后返回 1 个 segment，text 长度 <= 2500。"""
    from backend.pipelines.l2_take import _truncate_segments

    segments = [{"speaker": "A", "text": "Y" * 4000, "start_frame": 0, "end_frame": 1}]
    result, was_truncated = _truncate_segments(segments)
    assert was_truncated is True
    assert len(result) == 1
    assert len(result[0]["text"]) <= 2500
    # 其他字段保持原值
    assert result[0]["speaker"] == "A"
    assert result[0]["start_frame"] == 0
