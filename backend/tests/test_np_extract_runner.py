"""run_extract_np（文本）+ run_extract_np_voice（语音两步：ASR→extract）契约（stub 模型）。

末段：np_voice_adapter 双跑自一致性契约（Task 3——双跑下沉 adapter，两入口共用）。
"""

import json
import logging

import pytest
from unittest.mock import AsyncMock, MagicMock

from backend.llm.service import LLMService
from backend.pipelines.np_extract import (
    NPConfirmNeeded,
    NPExtraction,
    NPParseError,
    np_voice_adapter,
    run_extract_np,
    run_extract_np_voice,
)


def _tc(args: dict) -> dict:
    return {"id": "x", "type": "function",
            "function": {"name": "extract_np", "arguments": json.dumps(args, ensure_ascii=False)}}


_ARGS = {
    "scene_ordinal": 0, "shot_ordinal": 4, "take_ordinals": [1],
    "deictic": "none", "mark": "ng", "note_text": "", "note_category": "note",
}


@pytest.mark.asyncio
async def test_run_extract_np_text() -> None:
    svc = MagicMock(spec=LLMService)
    svc.infer_tool = AsyncMock(return_value=_tc(_ARGS))
    out = await run_extract_np("第四进第一次 NG", svc, timeout=10.0)
    assert isinstance(out, NPExtraction) and out.shot_ordinal == 4 and out.mark == "ng"
    # 走 note_extract task
    assert svc.infer_tool.await_args.kwargs["task_type"] == "note_extract"


@pytest.mark.asyncio
async def test_run_extract_np_text_lookup_error_maps_parse() -> None:
    svc = MagicMock(spec=LLMService)
    svc.infer_tool = AsyncMock(side_effect=LookupError("no tool_calls"))
    with pytest.raises(NPParseError):
        await run_extract_np("x", svc, timeout=10.0)


@pytest.mark.asyncio
async def test_run_extract_np_voice_two_calls() -> None:
    svc = MagicMock(spec=LLMService)
    # 第一步 ASR：infer_voice 用无强制工具 task，返回转写
    svc.infer_voice = AsyncMock(return_value="第四进第一次 NG")
    # 第二步：infer_tool 提取
    svc.infer_tool = AsyncMock(return_value=_tc(_ARGS))
    out = await run_extract_np_voice(b"WAVDATA", svc, timeout=20.0)
    assert isinstance(out, NPExtraction) and out.shot_ordinal == 4
    # ASR 走无强制工具 task（不能 forced，否则 content-path guardrail LookupError）
    assert svc.infer_voice.await_args.kwargs["task_type"] == "voice_dispatch_free"
    # 提取走 note_extract（文字路径同一条）
    assert svc.infer_tool.await_args.kwargs["task_type"] == "note_extract"


# ── np_voice_adapter 双跑自一致性契约（Task 3）──────────────────────────────────


class _FakeNPInput:
    """duck-typed input_data：np_voice_adapter 只经 _np_context_line 读这几个属性。"""

    raw_text = ""
    parsed_category = "note"
    current_scene_id = 1
    current_scene_code = "1A"
    current_shot = "A"
    current_take_id = 5
    current_take_number = 3
    take_context: list = []
    ts = 0.0


def _e(take_ordinals: list[int], mark: str = "ng") -> NPExtraction:
    return NPExtraction(
        scene_ordinal=0, shot_ordinal=0, take_ordinals=take_ordinals,
        deictic="none", mark=mark, note_text="x", note_category="note",
    )


def _seq_stub(monkeypatch: pytest.MonkeyPatch, *results):
    """monkeypatch run_extract_np_voice：按调用计数依次返回（Exception 实例则抛出）。"""
    call_count = [0]

    async def _stub(audio, svc, timeout=60.0, context_line=""):
        idx = min(call_count[0], len(results) - 1)
        call_count[0] += 1
        item = results[idx]
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr("backend.pipelines.np_extract.run_extract_np_voice", _stub)
    return call_count


@pytest.mark.asyncio
async def test_np_voice_adapter_double_run_consistent_returns_e1(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """两跑一致 → 返回第一跑结果，run_extract_np_voice 恰被调 2 次。"""
    e1 = _e([1])
    calls = _seq_stub(monkeypatch, e1, _e([1]))
    out = await np_voice_adapter(_FakeNPInput(), b"wav", MagicMock())
    assert out == e1
    assert calls[0] == 2


@pytest.mark.asyncio
async def test_np_voice_adapter_disagreement_raises_confirm_needed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """承重字段分歧 → raise NPConfirmNeeded，携带第一跑结果 + 分歧字段名。"""
    e1, e2 = _e([1]), _e([3])
    _seq_stub(monkeypatch, e1, e2)
    with pytest.raises(NPConfirmNeeded) as exc_info:
        await np_voice_adapter(_FakeNPInput(), b"wav", MagicMock())
    assert exc_info.value.extraction == e1, "确认卡预填值 = 第一跑结果"
    assert exc_info.value.disagreement == ["take_ordinals"]


@pytest.mark.asyncio
async def test_np_voice_adapter_second_run_failure_fail_open(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """第二跑抛异常 → fail-open 返回第一跑结果 + logger.warning（不上抛、不 confirm）。"""
    e1 = _e([2])
    _seq_stub(monkeypatch, e1, TimeoutError("run-2 boom"))
    with caplog.at_level(logging.WARNING, logger="backend.pipelines.np_extract"):
        out = await np_voice_adapter(_FakeNPInput(), b"wav", MagicMock())
    assert out == e1
    assert any("第二跑" in r.message for r in caplog.records), "应记 fail-open warning"


@pytest.mark.asyncio
async def test_np_voice_adapter_first_run_error_propagates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """第一跑抛 NPParseError → 原样上抛（_finalize_np 既有映射处理），不跑第二跑。"""
    calls = _seq_stub(monkeypatch, NPParseError("bad"))
    with pytest.raises(NPParseError):
        await np_voice_adapter(_FakeNPInput(), b"wav", MagicMock())
    assert calls[0] == 1, "第一跑失败后不应再跑第二跑"
