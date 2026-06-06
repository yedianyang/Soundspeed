"""Gemma 4 原生函数调用编解码单测：backend/llm/gemma_tools.py。

测试用例取自官方文档格式 + 本机真模型实测抓到的真实输出样本。
"""

from __future__ import annotations

import pytest

from backend.llm.gemma_tools import (
    ToolCall,
    UnknownToolError,
    dispatch_tool_calls,
    encode_tool_response,
    parse_tool_calls,
)

# 真实样本：探针里 Gemma 在剧本输入上吐出的（截断前）原生格式
_REAL_SAMPLE = (
    '<|tool_call>call:parse_script{raw_text:<|"|>内 咖啡馆 日\n罗湘：我们先聊聊。<|"|>}<tool_call|>'
)


# ── parse_tool_calls ─────────────────────────────────────────────────────────


def test_parse_real_sample():
    calls = parse_tool_calls(_REAL_SAMPLE)
    assert len(calls) == 1
    assert calls[0].name == "parse_script"
    assert calls[0].arguments == {"raw_text": "内 咖啡馆 日\n罗湘：我们先聊聊。"}


def test_parse_string_and_numeric_args():
    text = '<|tool_call>call:analyze_take{take_transcript:<|"|>罗湘：好的<|"|>,scene:3}<tool_call|>'
    calls = parse_tool_calls(text)
    assert len(calls) == 1
    assert calls[0].name == "analyze_take"
    assert calls[0].arguments == {"take_transcript": "罗湘：好的", "scene": 3}


def test_parse_no_args():
    calls = parse_tool_calls("<|tool_call>call:list_scenes{}<tool_call|>")
    assert calls == [ToolCall(name="list_scenes", arguments={})]


def test_parse_no_tool_call_returns_empty():
    assert parse_tool_calls("这是一段普通回答，没有函数调用。") == []
    assert parse_tool_calls("") == []


def test_parse_multiple_calls():
    text = (
        "<|tool_call>call:a{x:1}<tool_call|>"
        "随便夹点文字"
        '<|tool_call>call:b{y:<|"|>hi<|"|>}<tool_call|>'
    )
    calls = parse_tool_calls(text)
    assert [c.name for c in calls] == ["a", "b"]
    assert calls[0].arguments == {"x": 1}
    assert calls[1].arguments == {"y": "hi"}


def test_parse_truncated_missing_close_marker():
    # 缺 <tool_call|> 闭标记（max_tokens 截断）→ 仍能解析到文末
    text = '<|tool_call>call:parse_script{raw_text:<|"|>很长的剧本被截断了'
    calls = parse_tool_calls(text)
    assert len(calls) == 1
    assert calls[0].name == "parse_script"
    assert calls[0].arguments["raw_text"] == "很长的剧本被截断了"


def test_parse_string_value_with_comma_and_brace():
    # 逗号/花括号在 <|"|> 内不应被当分隔符
    text = '<|tool_call>call:f{s:<|"|>a,b{c}d<|"|>}<tool_call|>'
    calls = parse_tool_calls(text)
    assert calls[0].arguments == {"s": "a,b{c}d"}


def test_parse_value_coercion():
    text = '<|tool_call>call:f{i:42,fl:3.14,b:true,nb:false,nul:null}<tool_call|>'
    args = parse_tool_calls(text)[0].arguments
    assert args["i"] == 42
    assert args["fl"] == 3.14
    assert args["b"] is True
    assert args["nb"] is False
    assert args["nul"] is None


# ── encode_tool_response ─────────────────────────────────────────────────────


def test_encode_response_mixed_types():
    out = encode_tool_response("parse_script", {"scene_count": 5, "status": "ok"})
    assert out == 'response:parse_script{scene_count:5,status:<|"|>ok<|"|>}'


def test_encode_response_bool_and_null():
    out = encode_tool_response("f", {"ok": True, "err": None})
    assert out == "response:f{ok:true,err:null}"


def test_encode_response_nested_json():
    out = encode_tool_response("f", {"data": {"a": 1}})
    assert out == 'response:f{data:<|"|>{"a": 1}<|"|>}'


# ── dispatch_tool_calls ──────────────────────────────────────────────────────


def test_dispatch_routes_to_handler():
    seen = {}

    def parse_handler(args):
        seen["parse"] = args
        return "parsed"

    def analyze_handler(args):
        return "analyzed"

    calls = [
        ToolCall("parse_script", {"raw_text": "x"}),
        ToolCall("analyze_take", {"scene": 3}),
    ]
    results = dispatch_tool_calls(
        calls, {"parse_script": parse_handler, "analyze_take": analyze_handler}
    )
    assert results == [("parse_script", "parsed"), ("analyze_take", "analyzed")]
    assert seen["parse"] == {"raw_text": "x"}


def test_dispatch_unknown_tool_raises():
    with pytest.raises(UnknownToolError):
        dispatch_tool_calls([ToolCall("nope", {})], {"parse_script": lambda a: None})
