"""语音调度器管线（块④ 形态 A，2026-06-06 spike 坐实）。

两步走 hop A/B：
  hop A: infer_voice（文本注入 6 工具声明，voice_dispatch_free 纯生成）
         + _scrape_tool_name 抠工具名
  按工具名分流：
    structure_note → note 分支（hop B forced note_struct 取参 → _persist_np_output_callable）
    QP 工具        → query 分支（hop B forced query_session 取参
                                → asyncio.to_thread(_run_executor)
                                → run_tool_loop 续跳
                                → _schedule_qp_broadcast 广播答案）
    None           → fail-closed note 分支（同 structure_note 路径）

query 续跳：全新拼纯文本 4 帧（对齐 probe_qp_voice_e2e.py 239-247）：
  system(_QP_SYSTEM + catalog) → user(占位) → assistant(hop_a_text) → user(工具返回)
  hop A/B 的音频 messages 在续跳前丢弃，不含 AUDIO_SENTINEL，纯文本续跳安全。

  已知限制：probe 续跳 user 帧是真实转写 phrase，生产无转写，改用静态占位文本。
  此结构 probe 未覆盖，模型能否稳定收尾取决于真机测试。

注意：run_tool_loop 纯文本续跳不用 role=tool——
那会撞这个 GGUF 的 Jinja 模板 `raise_exception undefined`（probe 实证）。
回喂格式：assistant 喂 hop A 原始 content + user 喂工具返回文本（对齐 qp_query.py 115-122）。

机制参考：
  experiments/2026-06-06-voice-dispatch-spike/probe_c3_text_decl.py
  experiments/2026-06-06-voice-dispatch-spike/probe_qp_voice_e2e.py
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Any

from backend.pipelines.qp_query import (
    _QP_SYSTEM,
    _run_executor,
    _scrape_tool_name,
    run_tool_loop,
)
from backend.pipelines.voice_dispatch_helpers import (
    NOTE_TOOL_NAMES,
    build_hop_a_system,
)

if TYPE_CHECKING:
    from backend.db.dal import DAL
    from backend.llm.service import LLMService

logger = logging.getLogger(__name__)

# ── 注入点（由 Part C 入口接线赋值，测试可 monkeypatch） ─────────────────────
#
# _persist_np_output_callable: async (output: NPOutput, *, ts, client_id, raw_text_override) -> None
#   note 分支落库副作用。接线时注意：orchestrator._finalize_np 收 awaitable，
#   调用方需包 adapter 使契约对齐（Part C 负责）。
#
# _schedule_qp_broadcast: async (answer_text: str, conn_id: str, *, dal, service, cm) -> None
#   query 分支广播已算好的答案（不是重新跑 run_qp_query）。
#   注意与 run_qp_and_broadcast 区别：后者把 answer_text 当新 query 重跑一遍。
#
_persist_np_output_callable = None
_schedule_qp_broadcast = None


async def run_voice_dispatch(
    audio: bytes,
    *,
    conn_id: str,
    ts: float,
    client_id: str | None,
    dal: "DAL",
    service: "LLMService",
    cm: Any,
    scene_context: str = "",
) -> dict:
    """语音调度器入口。返回 {"kind": "note"|"query"|"error"}。

    hop A 失败或抠不到工具名 → fail-closed 走 note 分支（hop B forced structure_note）。
    hop B 失败 → 返回 {"kind": "error"}。

    Args:
        audio:        原始音频字节（wav）。
        conn_id:      WS 连接 ID，query 答案广播到 qp.answer.{conn_id}。
        ts:           时间戳（秒）。
        client_id:    前端乐观 pending 去重键，透传 NP 落库。
        dal:          DAL 实例。
        service:      LLMService 实例（须支持 infer_voice + infer_voice_tool）。
        cm:           ConnectionManager，广播用。
        scene_context: 场次目录文本（_build_scene_catalog 输出），注入 system。
    """
    # ── 组装 hop A messages ──────────────────────────────────────────────────
    from backend.llm.multimodal import AUDIO_SENTINEL  # noqa: PLC0415  延迟导入避免顶层拉 llama_cpp

    system_text = build_hop_a_system(scene_context)
    messages: list[dict] = [
        {"role": "system", "content": system_text},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "听这段语音，需要的话调用工具回答。"},
                {
                    "type": "image_url",
                    "image_url": {"url": AUDIO_SENTINEL},
                },
            ],
        },
    ]

    # ── hop A：infer_voice（纯生成，文本注入 6 工具声明，无 tools/tool_choice） ──
    # audio 是 infer_voice 的位置参数（service.py:289）
    tool_name: str | None = None
    hop_a_text: str = ""
    try:
        hop_a_text = await service.infer_voice(
            messages,
            audio,
            task_type="voice_dispatch_free",
        )
        tool_name = _scrape_tool_name(hop_a_text)
        logger.debug("hop A 抠名: %s", tool_name)
    except Exception as exc:
        logger.warning("hop A 失败，fail-closed note: %r", exc)
        tool_name = None

    is_note = tool_name is None or tool_name in NOTE_TOOL_NAMES

    # ── hop B：forced audio 取参（task_type 按分支选） ───────────────────────
    # note → note_struct（对齐 run_np_voice task_type）
    # query → query_session（对齐 probe step B）
    # is_note=False 时 tool_name 必然非 None（None→is_note=True）
    if is_note:
        forced_name: str = NOTE_TOOL_NAMES[0]
    else:
        assert tool_name is not None  # mypy narrowing
        forced_name = tool_name
    hop_b_task_type = "note_struct" if is_note else "query_session"
    try:
        hop_b_result = await service.infer_voice_tool(
            messages,
            audio,
            task_type=hop_b_task_type,
            tool_choice={"type": "function", "function": {"name": forced_name}},
        )
    except Exception as exc:
        logger.warning("hop B 失败 forced=%s: %r", forced_name, exc)
        return {"kind": "error"}

    # ── 分流 ──────────────────────────────────────────────────────────────────
    if is_note:
        return await _handle_note_branch(hop_b_result, ts=ts, client_id=client_id)

    return await _handle_query_branch(
        hop_b_result,
        hop_a_text=hop_a_text,
        tool_name=forced_name,
        conn_id=conn_id,
        scene_context=scene_context,
        dal=dal,
        service=service,
        cm=cm,
    )


# ── 分支实现 ──────────────────────────────────────────────────────────────────

async def _handle_note_branch(
    hop_b_result: dict,
    *,
    ts: float,
    client_id: str | None,
) -> dict:
    """note 分支：_parse_tool_call → _persist_np_output_callable。

    _persist_np_output_callable 由 Part C 入口接线注入。
    接线注意：orchestrator._finalize_np 收 awaitable runner，与此处签名不同，
    Part C 须包 adapter（await _finalize_np(coroutine_returning_NPOutput, ...)）。
    """
    from backend.pipelines.np_note import _parse_tool_call  # noqa: PLC0415

    try:
        np_output = _parse_tool_call(hop_b_result)
        if _persist_np_output_callable is not None:
            await _persist_np_output_callable(
                np_output,
                ts=ts,
                client_id=client_id,
                raw_text_override=None,
            )
    except Exception as exc:
        logger.error("note 分支落库失败: %r", exc)
    return {"kind": "note"}


async def _handle_query_branch(
    hop_b_result: dict,
    *,
    hop_a_text: str,
    tool_name: str,
    conn_id: str,
    scene_context: str,
    dal: "DAL",
    service: "LLMService",
    cm: Any,
) -> dict:
    """query 分支：_run_executor → run_tool_loop 续跳 → _schedule_qp_broadcast 广播。

    _run_executor 是同步函数，用 asyncio.to_thread 包装（对齐 probe_qp_voice_e2e.py step C）。

    续跳 messages 全新拼纯文本 4 帧（对齐 probe_qp_voice_e2e.py 239-247）：
      system(_QP_SYSTEM + catalog) → user(占位) → assistant(hop_a_text) → user(工具返回)
    不复用 hop A/B 的音频 messages，避免 build_hop_a_system 的工具声明文本带入续跳。

    已知限制：probe 续跳 user 帧用真实转写 phrase，生产无转写改用静态占位。
    此形态 probe 未覆盖，模型收尾稳定性待真机验证。

    续跳不用 role=tool——这个 GGUF 的 Jinja 模板会 raise_exception undefined（probe 实证）。
    回喂格式：
      assistant  ← hop A 原始输出文本（含 <|tool_call>... 特殊 token，模型认得）
      user       ← "工具 NAME 返回：{JSON}"
    （对齐 qp_query.py run_tool_loop 的回喂格式，119-122 行）
    """
    answer = "抱歉，这次语音查询出错了，请换种说法再试。"
    try:
        args = json.loads(hop_b_result["function"]["arguments"])
        result = await asyncio.to_thread(_run_executor, tool_name, args, dal)

        # 纯文本续跳 messages：全新拼，不含 AUDIO_SENTINEL
        # user 占位：生产无转写，probe 里是真实 phrase；此差异已在 docstring 标注
        system_content = _QP_SYSTEM + ("\n\n" + scene_context if scene_context else "")
        tool_messages: list[dict] = [
            {"role": "system", "content": system_content},
            {"role": "user", "content": "请根据工具返回结果回答。"},
            {"role": "assistant", "content": hop_a_text},
            {
                "role": "user",
                "content": f"工具 {tool_name} 返回：{json.dumps(result, ensure_ascii=False)}",
            },
        ]

        # run_tool_loop 真签名：(messages, *, service, dal, ...)，第一参数是 messages
        answer = await run_tool_loop(
            tool_messages,
            service=service,
            dal=dal,
        )
    except asyncio.TimeoutError:
        raise  # 放行给 caller（对齐 run_tool_loop 的 TimeoutError 契约）
    except Exception as exc:
        logger.error("query 分支失败 tool_name=%s: %r", tool_name, exc)

    if _schedule_qp_broadcast is not None:
        await _schedule_qp_broadcast(answer, conn_id, dal=dal, service=service, cm=cm)
    return {"kind": "query"}


