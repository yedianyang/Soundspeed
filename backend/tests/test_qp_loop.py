"""两步走循环（L2）：StubService 喂固定 auto content + forced tool_calls。

断言：抠名正确、forced 取参正确、≤5 跳终止、出错回喂、终止条件、最终答案返回。
"""
from __future__ import annotations

import json

import pytest

from backend.pipelines.qp_query import _scrape_tool_name, run_tool_loop


def test_scrape_tool_name_functiongemma() -> None:
    # FunctionGemma auto content 格式（FC spec §3.2）：<|tool_call>call:NAME{...}
    text = "<|tool_call>call:count_takes{scene_ref:<|\"|>1<|\"|>}<tool_call|>"
    assert _scrape_tool_name(text) == "count_takes"


def test_scrape_tool_name_none_when_plain_text() -> None:
    assert _scrape_tool_name("第一场一共拍了 3 条。") is None
    assert _scrape_tool_name("") is None


class _ScriptedService:
    """按脚本依次返回 auto content（infer）/ forced tool_calls（infer_tool）。

    auto_replies：每跳 step A 的 content 串（含 <|tool_call> 则继续，否则为最终答案）。
    forced_args：每次 step B 返回的 arguments dict（按调用顺序）。
    """

    def __init__(self, auto_replies: list[str], forced_args: list[dict]) -> None:
        self._auto = list(auto_replies)
        self._forced = list(forced_args)
        self.infer_calls = 0
        self.infer_tool_calls = 0

    async def infer(self, messages, task_type, priority=None, timeout=None, tool_choice=None) -> str:
        self.infer_calls += 1
        return self._auto.pop(0)

    async def infer_tool(self, messages, task_type, priority=None, timeout=None, tool_choice=None) -> dict:
        self.infer_tool_calls += 1
        args = self._forced.pop(0)
        return {"function": {"name": tool_choice["function"]["name"], "arguments": json.dumps(args)}}


class _StubDAL:
    """executor 在循环里被调用，这里只需返回可序列化结果。"""

    def resolve_scene_id(self, ref):
        return 1 if ref in {"1", "第一场"} else None

    def count_takes(self, scene_id, status=None):
        return 3


@pytest.mark.asyncio
async def test_loop_single_hop_then_answer() -> None:
    # hop1: 调 count_takes；hop2: 不再调工具，给最终答案
    svc = _ScriptedService(
        auto_replies=[
            "<|tool_call>call:count_takes{scene_ref:<|\"|>1<|\"|>}<tool_call|>",
            "第一场一共拍了 3 条。",
        ],
        forced_args=[{"scene_ref": "1"}],
    )
    messages = [{"role": "user", "content": "第一场拍了多少条"}]
    answer = await run_tool_loop(messages, service=svc, dal=_StubDAL())
    assert answer == "第一场一共拍了 3 条。"
    assert svc.infer_tool_calls == 1
    # 工具结果被回喂进 messages（纯文本格式，Task 7.5 实证：assistant 原始 content + user「工具…返回…」）
    assert any(m["role"] == "user" and m["content"].startswith("工具 ") for m in messages)


@pytest.mark.asyncio
async def test_loop_terminates_at_max_hops() -> None:
    # 每跳都调工具、永不收尾 → 走到 5 跳兜底
    looping = "<|tool_call>call:count_takes{scene_ref:<|\"|>1<|\"|>}<tool_call|>"
    svc = _ScriptedService(
        auto_replies=[looping] * 5,
        forced_args=[{"scene_ref": "1"}] * 5,
    )
    answer = await run_tool_loop(
        [{"role": "user", "content": "x"}], service=svc, dal=_StubDAL()
    )
    assert "超过上限" in answer or "轮数" in answer
    assert svc.infer_tool_calls == 5  # 恰好 5 跳


@pytest.mark.asyncio
async def test_loop_feeds_executor_error_back() -> None:
    # 找不到场次 → executor 返回 error，回喂后模型收尾
    svc = _ScriptedService(
        auto_replies=[
            "<|tool_call>call:count_takes{scene_ref:<|\"|>999<|\"|>}<tool_call|>",
            "数据库里没有第 999 场。",
        ],
        forced_args=[{"scene_ref": "999"}],
    )
    messages = [{"role": "user", "content": "第999场拍了多少"}]
    answer = await run_tool_loop(messages, service=svc, dal=_StubDAL())
    assert "999" in answer
    # error 串被回喂（纯文本 user 消息「工具 count_takes 返回：{...error...}」）
    fed = [m for m in messages if m["role"] == "user" and m["content"].startswith("工具 ")][0]
    assert "error" in fed["content"]
