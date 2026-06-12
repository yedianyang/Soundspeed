"""QP (Query Pipeline) 两步走 tool-loop（spec §5，核心）。

每一跳统一两步，彻底不在脆弱的 auto 字符串里解析参数：
  step A — auto 跳：service.infer(tool_choice=config 默认 "auto") 返回 FunctionGemma
           content 串；正则抠工具名（名字在第一个 { 之前，铁稳）。无 tool_call → 最终答案。
  step B — forced 跳：service.infer_tool(tool_choice=forced(name))，grammar 约束出干净 JSON 参数。
  step C — executor(args, dal) 执行 + 结果回喂；executor 包在 to_thread 里跑（不阻塞事件
           循环 + 只读连接跨线程安全，D-QP-12）。

公共 API：
  run_qp_query(text, dal, service) -> str   QP 入口：拼场次目录 + system → 跑循环
  run_tool_loop(messages, *, service, dal)  纯循环（可注入 service/dal，便于测试）
  build_scene_catalog(dal) -> str           场次目录文本（QP 文本/语音共用）
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.db.dal import DAL
    from backend.llm.service import LLMService

logger = logging.getLogger(__name__)

_QP_TASK = "query_session"
_MAX_HOPS = 5
_FALLBACK_TEXT = "抱歉，这个问题查询轮数超过上限，没能得出结论。"
# FunctionGemma auto content：<|tool_call>call:NAME{...}（FC spec §3.2）。名字 \w+ 在第一个 { 前。
_TOOL_NAME_RE = re.compile(r"<\|tool_call>call:(\w+)")
_TOOL_CALL_BLOCK_RE = re.compile(r"<\|tool_call>.*?(?:<tool_call\|>|$)", re.S)
_CLOSING_PROMPT = (
    "查询轮数已到上限。请只用以上工具返回的信息直接回答用户最初的问题；"
    "如果信息确实不足，就说明目前已查到的内容。不要再调用工具。"
)

# 极简 system prompt（spec §5.5 预算纪律：先按极简跑，不稳才加且每加必量化）。
_QP_SYSTEM = (
    "你是场记查询助手。只回答数据库里查到的事实，不给建议、不做评价。\n"
    "有合适的工具就调工具查；查到结果后用一句话直接回答，不要反复查询。\n"
    "找不到对应记录时直接说没有，禁止编造或用相近的场次/数据顶替。"
)


def _scrape_tool_name(text: str) -> str | None:
    """从 FunctionGemma auto content 抠工具名；无 tool_call 标记返回 None（最终答案）。"""
    if not text:
        return None
    m = _TOOL_NAME_RE.search(text)
    return m.group(1) if m else None


def _run_executor(name: str, args: dict, dal: "DAL") -> dict:
    """同步执行 executor（在 to_thread worker 跑）。错误不抛穿，包成 error 串回喂（spec §5.3）。"""
    from backend.llm.tools import registry  # noqa: PLC0415

    try:
        executor = registry.get_executor(name)
    except KeyError:
        return {"error": f"未知工具 {name!r}"}
    if executor is None:
        return {"error": f"工具 {name!r} 无 executor"}
    try:
        return executor(args, dal)
    except Exception as exc:  # noqa: BLE001  executor 内部异常也包成 error 回喂
        logger.warning("qp executor %s 异常: %r", name, exc)
        return {"error": f"工具执行失败：{exc}"}


async def run_tool_loop(
    messages: list[dict],
    *,
    service: "LLMService",
    dal: "DAL",
    max_hops: int = _MAX_HOPS,
    timeout: float = 30.0,
    trace: list[dict] | None = None,
    native_toolfeed: bool = True,
) -> str:
    """两步走循环：≤max_hops 跳，返回最终自然语言文本。messages 原地追加每跳的 tool 往返。

    trace：评测诊断用，传入非 None 的列表则每跳 append {"tool", "args", "result"}，
           默认 None 不收集（不影响任何现有调用方行为）。

    native_toolfeed：控制工具结果回喂格式（B2，A/B 裁决后默认 True）。
    A/B 评测：native 两轮一致 0.900 vs text 两轮 0.833，agg-time 抖动被 native 接地修平。
    - True（默认，生产）：OpenAI 风格 native 回喂——assistant{tool_calls}+role=tool 两帧。
      Task 14 probe 结论：client.py:249 路由下带 tools kwarg 的调用走 GGUF 原生
      Jinja2ChatFormatter，该模板原生支持 role=tool，渲染成 <|tool_call>/<|tool_response>
      block（Gemma 原生期待格式）。
    - False（兼容回退）：纯文本回喂——assistant 喂 auto 步原始 content（含
      <|tool_call> 特殊 token），工具结果用 user 消息「工具 NAME 返回：...」。
      历史背景：路由守卫建立前的 workaround，彼时 role=tool 会撞 Jinja raise_exception。

    异常契约：
    - asyncio.TimeoutError / asyncio.CancelledError 放行给 caller（route 兜底返回友好错误）。
      收尾跳的 infer 超时同样放行（与主循环跳对齐）。
    - step B 取参失败（LookupError/KeyError/JSONDecodeError/TypeError）→ 包成 error 回喂，
      模型下一跳自纠；不抛穿，循环继续。
    - step C executor 错误（_run_executor 内已包）→ 同样回喂，不抛穿。
    - 跑满 max_hops 后追加无工具收尾跳（D1 修复）：已回喂的工具结果全在 messages 里，
      收尾跳返回自然语言 → 直接作答；收尾跳仍含 tool_call 标记 → strip 后有文本则返回，
      strip 后为空 → 落 _FALLBACK_TEXT（双兜底）。
    """
    for hop_idx in range(max_hops):
        # step A — auto 跳：抠工具名。
        # ✅ Task 7.5 probe 已实证「假设 7」成立：service.infer 在 auto 跳返回 FunctionGemma
        #   content 串（finish_reason=stop，不撞 service 护栏），happy path 即此分支。
        #   （故不需要 infer_message 变体，分诊 A 未触发。）
        # infer 的 TimeoutError 放行给 caller（与 l2_take 约定对齐）。
        text = await service.infer(messages, task_type=_QP_TASK, priority=1, timeout=timeout)
        name = _scrape_tool_name(text)
        if name is None:
            return text  # 模型给的是自然语言最终答案，终止

        # step B — forced 跳：grammar 出干净 JSON 参数
        try:
            tool_call = await service.infer_tool(
                messages,
                task_type=_QP_TASK,
                priority=1,
                timeout=timeout,
                tool_choice={"type": "function", "function": {"name": name}},
            )
            args = json.loads(tool_call["function"]["arguments"])
        except asyncio.TimeoutError:
            raise  # 超时放行给 caller（route 兜底返回友好错误），对齐 l2_take 约定
        except (LookupError, KeyError, json.JSONDecodeError, TypeError) as exc:
            # forced 没拿到干净参数（模型没走 FC / arguments 缺失或非法）→ 当工具失败回喂，模型自纠，不抛穿
            logger.warning("qp forced 跳取参失败 name=%s: %r", name, exc)
            args = {}  # 取参失败；trace 用空 dict
            result: dict = {"error": f"工具 {name} 调用失败：参数无法解析，请换种方式或直接回答。"}
        else:
            # step C — 执行（executor 包 to_thread，错误不抛穿）
            result = await asyncio.to_thread(_run_executor, name, args, dal)

        # trace 收集（评测诊断用，默认 None 跳过）
        if trace is not None:
            trace.append({"tool": name, "args": args, "result": result})

        # 回喂（单点）：native/text 取舍与 A/B 数字见函数 docstring。
        if native_toolfeed:
            call_id = f"call_{hop_idx}"
            messages.append({
                "role": "assistant",
                "tool_calls": [{
                    "id": call_id,
                    "type": "function",
                    "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)},
                }],
            })
            messages.append({
                "role": "tool",
                "tool_call_id": call_id,
                "content": json.dumps(result, ensure_ascii=False),
            })
        else:
            messages.append({"role": "assistant", "content": text})
            messages.append(
                {"role": "user", "content": f"工具 {name} 返回：{json.dumps(result, ensure_ascii=False)}"}
            )

    # D1：跑满不丢已查数据——追加无工具收尾跳，已回喂的工具结果都在 messages 里。
    messages.append({"role": "user", "content": _CLOSING_PROMPT})
    text = await service.infer(messages, task_type=_QP_TASK, priority=1, timeout=timeout)
    if _scrape_tool_name(text) is None and text.strip():
        return text
    stripped = _TOOL_CALL_BLOCK_RE.sub("", text).strip()
    return stripped or _FALLBACK_TEXT


def build_scene_catalog(dal: "DAL") -> str:
    """场次目录注入:只列场次编号(供场次引用解析),地点/时间等事实一律走工具查。"""
    scenes = dal.list_scenes_readonly()
    if not scenes:
        return "（当前项目还没有任何场次记录。）"
    # 单行长度 tradeoff 有意接受:200 场≈1.8KB 可控;scene_code 命名变长或千场级再加截断
    codes = "、".join(s["scene_code"] for s in scenes)
    return f"当前项目已有场次（编号）：{codes}（共 {len(scenes)} 场）。地点/时间/角色等信息用工具查询。"


async def run_qp_query(
    *,
    text: str,
    dal: "DAL",
    service: "LLMService",
    timeout: float = 30.0,
    trace: list[dict] | None = None,
    native_toolfeed: bool = True,
) -> str:
    """QP 入口：拼场次目录 + 极简 system → 跑两步走循环 → 返回自然语言答案。

    trace：评测诊断用，传入非 None 的列表则每跳 append {"tool", "args", "result"}，
           透传给 run_tool_loop，默认 None 不收集。

    native_toolfeed：透传给 run_tool_loop；默认 True（native role=tool 回喂，
    A/B 裁决 native 0.900×2 vs text 0.833×2），False 为兼容回退（纯文本回喂）。
    """
    # 场次目录是同步 SQLite I/O，包 to_thread 不阻塞事件循环（与 step C executor 的 to_thread 一致）。
    catalog = await asyncio.to_thread(build_scene_catalog, dal)
    messages = [
        {"role": "system", "content": _QP_SYSTEM},
        {"role": "user", "content": f"{catalog}\n\n用户提问：{text}"},
    ]
    return await run_tool_loop(
        messages, service=service, dal=dal, timeout=timeout, trace=trace,
        native_toolfeed=native_toolfeed,
    )
