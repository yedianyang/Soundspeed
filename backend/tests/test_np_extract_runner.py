"""run_extract_np（文本）+ run_extract_np_voice（语音两步：ASR→extract）契约（stub 模型）。"""

import json

import pytest
from unittest.mock import AsyncMock, MagicMock

from backend.llm.service import LLMService
from backend.pipelines.np_extract import (
    NPExtraction,
    NPParseError,
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
