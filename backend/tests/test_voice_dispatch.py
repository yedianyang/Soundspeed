"""语音调度器两步走管线（形态 A）。

hop A（infer_voice 文本注入 + _scrape_tool_name 抠名）→ 分流：
  structure_note → note 分支（hop B forced 取参 → _persist_np_output_callable）
  QP 工具        → query 分支（hop B forced 取参 → asyncio.to_thread(_run_executor) → run_tool_loop → broadcast）
  None（抠不到）→ fail-closed note 分支

单测全用 mock service，不加载真模型。
"""
import asyncio
import json
from unittest.mock import AsyncMock, MagicMock


WAV_BYTES = b"\x00" * 200   # stub audio


# ── stub builders ──────────────────────────────────────────────────────────────

def _make_infer_voice_resp(tool_name: str, args: dict) -> str:
    """生成 corrected-C3 格式的 hop A 输出文本（对齐 _TOOL_NAME_RE）。"""
    return f"<|tool_call>call:{tool_name}{json.dumps(args)}<tool_call|>"


class _StubService:
    """stub LLMService：audio 是位置参数（对齐真实 service.py 签名）。"""

    def __init__(self, hop_a_name: str | None, hop_a_args: dict, hop_b_args: dict):
        self._hop_a_name = hop_a_name
        self._hop_a_output = (
            _make_infer_voice_resp(hop_a_name, hop_a_args) if hop_a_name else "我不太明白"
        )
        self._hop_b_result = {
            "function": {
                "name": hop_a_name or "structure_note",
                "arguments": json.dumps(hop_b_args),
            }
        }

    async def infer_voice(self, messages, audio, task_type, **kw) -> str:
        return self._hop_a_output

    async def infer_voice_tool(self, messages, audio, task_type, tool_choice=None, **kw) -> dict:
        return self._hop_b_result


class _StubDAL:
    pass


class _StubCM:
    def __init__(self):
        self.broadcasts = []

    def broadcast(self, topic, payload):
        self.broadcasts.append((topic, payload))


# ── tests ──────────────────────────────────────────────────────────────────────

def _patch_build_hop_a_system(monkeypatch) -> None:
    """patch build_hop_a_system 避免 extract_tool_declarations_text 加载 GGUF（单测无模型）。"""
    monkeypatch.setattr(
        "backend.pipelines.voice_dispatch.build_hop_a_system",
        lambda scene_context="": "stub system",
    )


def test_note_branch_calls_persist(monkeypatch):
    """hop A 抠到 structure_note → 走 note 分支，调 _persist_np_output_callable。"""
    _patch_build_hop_a_system(monkeypatch)
    from backend.pipelines.voice_dispatch import run_voice_dispatch

    persisted = {}

    async def _fake_persist(output, *, ts, client_id, raw_text_override):
        persisted["done"] = True
        persisted["output"] = output

    monkeypatch.setattr(
        "backend.pipelines.voice_dispatch._persist_np_output_callable",
        _fake_persist,
    )

    svc = _StubService(
        "structure_note", {},
        {"category": "pass", "content": "这条过了", "take_id": 1},
    )
    asyncio.run(run_voice_dispatch(
        WAV_BYTES,
        conn_id="c1",
        ts=1000.0,
        client_id="cid1",
        dal=_StubDAL(),
        service=svc,
        cm=_StubCM(),
        scene_context="",
    ))
    assert persisted.get("done") is True


def test_query_branch_broadcasts(monkeypatch):
    """hop A 抠到 count_takes → 走 query 分支，广播 qp.answer.{conn_id}。

    _run_executor 是同步 MagicMock（asyncio.to_thread 在线程里调用它）。
    run_tool_loop 是 fake 异步函数，断言 messages[-1]['content'] 含工具返回 JSON。
    _schedule_qp_broadcast 是 AsyncMock（验 conn_id 被传到）。
    """
    _patch_build_hop_a_system(monkeypatch)
    from backend.pipelines.voice_dispatch import run_voice_dispatch  # noqa: PLC0415

    # _run_executor 是同步函数，被 asyncio.to_thread 包装，mock 需为普通 MagicMock
    sync_executor = MagicMock(return_value={"count": 7})
    monkeypatch.setattr(
        "backend.pipelines.voice_dispatch._run_executor",
        sync_executor,
    )

    captured_tool_messages: list[dict] = []

    async def _fake_loop(messages_arg, *, service, dal, **kw):
        captured_tool_messages.extend(messages_arg)
        return "第一场拍了 7 条。"

    monkeypatch.setattr(
        "backend.pipelines.voice_dispatch.run_tool_loop",
        _fake_loop,
    )
    broadcast_calls = []

    async def _fake_broadcast(answer_text, conn_id, *, dal, service, cm):
        broadcast_calls.append(conn_id)

    monkeypatch.setattr(
        "backend.pipelines.voice_dispatch._schedule_qp_broadcast",
        _fake_broadcast,
    )

    svc = _StubService("count_takes", {"scene_id": 1}, {"scene_id": 1})
    cm = _StubCM()
    asyncio.run(run_voice_dispatch(
        WAV_BYTES,
        conn_id="c2",
        ts=1000.0,
        client_id="cid2",
        dal=_StubDAL(),
        service=svc,
        cm=cm,
        scene_context="Scene 1: 大堂",
    ))
    assert broadcast_calls == ["c2"]
    # 验证工具返回 JSON 正确注入 messages 最后一帧
    expected_tool_return = json.dumps({"count": 7}, ensure_ascii=False)
    assert captured_tool_messages, "run_tool_loop 未被调用"
    assert expected_tool_return in captured_tool_messages[-1]["content"]


def test_fail_closed_note_when_scrape_returns_none(monkeypatch):
    """hop A 抠不到工具名 → fail-closed 走 note 分支（hop B forced structure_note）。"""
    _patch_build_hop_a_system(monkeypatch)
    from backend.pipelines.voice_dispatch import run_voice_dispatch

    persisted = {}

    async def _fake_persist(output, *, ts, client_id, raw_text_override):
        persisted["done"] = True

    monkeypatch.setattr(
        "backend.pipelines.voice_dispatch._persist_np_output_callable",
        _fake_persist,
    )

    # hop A 返回自然语言散文（无 tool_call 格式）
    svc = _StubService(None, {}, {"category": "pass", "content": "这条过了", "take_id": 1})
    asyncio.run(run_voice_dispatch(
        WAV_BYTES,
        conn_id="c3",
        ts=1000.0,
        client_id="cid3",
        dal=_StubDAL(),
        service=svc,
        cm=_StubCM(),
        scene_context="",
    ))
    assert persisted.get("done") is True


def test_query_hop_messages_structure(monkeypatch):
    """query 续跳 messages 是全新拼的纯文本 4 帧，不含 image_url（AUDIO_SENTINEL）。

    帧结构：system → user(占位) → assistant(hop_a_text) → user(工具返回)。
    run_tool_loop 真签名：(messages, *, service, dal, ...)。
    fake 收到 positional messages_arg，断言帧数/角色/无 image_url。
    """
    _patch_build_hop_a_system(monkeypatch)
    from backend.pipelines.voice_dispatch import run_voice_dispatch

    captured: dict = {}

    async def _fake_loop(messages_arg, *, service, dal, **kw):
        captured["messages"] = messages_arg
        # 断言不含任何 image_url content part
        for msg in messages_arg:
            content = msg.get("content", "")
            if isinstance(content, list):
                for part in content:
                    if part.get("type") == "image_url":
                        captured["has_sentinel"] = True
        return "7 条"

    monkeypatch.setattr("backend.pipelines.voice_dispatch.run_tool_loop", _fake_loop)
    monkeypatch.setattr(
        "backend.pipelines.voice_dispatch._run_executor",
        MagicMock(return_value={"count": 7}),
    )
    monkeypatch.setattr(
        "backend.pipelines.voice_dispatch._schedule_qp_broadcast",
        AsyncMock(),
    )

    svc = _StubService("count_takes", {"scene_id": 1}, {"scene_id": 1})
    asyncio.run(run_voice_dispatch(
        WAV_BYTES, conn_id="c4", ts=1000.0, client_id="cid4",
        dal=_StubDAL(), service=svc, cm=_StubCM(), scene_context="Scene 1",
    ))
    msgs = captured.get("messages", [])
    assert msgs, "run_tool_loop 未被调用"
    assert len(msgs) == 4, f"期望 4 帧，实际 {len(msgs)}"
    roles = [m["role"] for m in msgs]
    assert roles == ["system", "user", "assistant", "user"], f"帧角色不对: {roles}"
    assert not captured.get("has_sentinel"), "续跳 messages 含 image_url（AUDIO_SENTINEL 未剔除）"
    # system 帧含 _QP_SYSTEM 内容（场记查询助手）且含 scene_context
    assert "场记查询助手" in msgs[0]["content"]
    assert "Scene 1" in msgs[0]["content"]
    # 最后一帧含工具返回 JSON
    assert json.dumps({"count": 7}, ensure_ascii=False) in msgs[-1]["content"]
