"""Gemma 4 原生函数调用（Native Function Calling）编解码。

依据 Google 官方文档 ai.google.dev/gemma/docs/capabilities/text/function-calling-gemma4，
并经本机真模型实测确认。Gemma 4 的原生函数调用输出格式：

    <|tool_call>call:函数名{参数:<|"|>字符串值<|"|>,参数:数字值}<tool_call|>

  - <|tool_call> … <tool_call|>  包住整个调用（注意开/闭标记不对称）
  - call: 前缀 + 函数名 + {参数体}
  - 字符串参数用 <|"|>值<|"|> 包裹；数字/布尔/null 裸写
  - 可出现多个调用块

函数响应回传格式（开发者把执行结果喂回模型那一轮）：

    response:函数名{字段:值,字段:<|"|>字符串<|"|>}

为什么需要本模块：
  llama-cpp 的 gemma 模板会把 tools= 渲染进 prompt（实测 Gemma 据此吐出上面格式），
  但 llama-cpp **不会**把该输出解析回标准 tool_calls 字段——就晾在 content 里。
  本模块补上「解析 + 编码」这一层，让上层拿到结构化的 ToolCall 去 dispatch。

公共 API：
  ToolCall              解析出的单个函数调用（name + arguments）
  parse_tool_calls      从模型输出文本解析出 ToolCall 列表（无调用 → 空列表）
  encode_tool_response  把函数返回值编码成 Gemma 响应回传字符串
  UnknownToolError      dispatch 到未注册工具名时抛出
  dispatch_tool_calls   按工具名把 ToolCall 分发到处理函数，收集结果

本模块是纯字符串编解码 + 分发，不依赖模型/网络，可独立单测。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field

# Gemma 4 原生函数调用标记（官方文档 + 实测确认）
_OPEN = "<|tool_call>"
_CLOSE = "<tool_call|>"
_QUOTE = '<|"|>'  # 字符串参数的左右界定符（同一标记）
_CALL_PREFIX = "call:"


@dataclass
class ToolCall:
    """解析出的单个函数调用。

    name:      函数名（如 "parse_script"）。
    arguments: 参数字典；字符串值原样保留，数字/布尔/null 已转成 Python 类型。
    """

    name: str
    arguments: dict = field(default_factory=dict)


def _coerce(raw: str):
    """裸值（非字符串参数）转 Python 类型：int / float / bool / None / 原样字符串。"""
    s = raw.strip()
    low = s.lower()
    if low in ("null", "none"):
        return None
    if low == "true":
        return True
    if low == "false":
        return False
    try:
        return int(s)
    except ValueError:
        pass
    try:
        return float(s)
    except ValueError:
        pass
    return s  # 无法识别 → 当字符串


def _parse_args(body: str) -> dict:
    """解析 {…} 内的参数体为 dict（字符串感知，逗号/花括号在 <|"|> 内不当分隔符）。"""
    args: dict = {}
    i, n = 0, len(body)
    while i < n:
        # 跳过分隔符与空白
        while i < n and body[i] in " ,\n\t\r":
            i += 1
        if i < n and body[i] == "}":  # 参数体结束
            break
        if i >= n:
            break
        # 读 key 到 ':'
        colon = body.find(":", i)
        if colon == -1:
            break
        key = body[i:colon].strip()
        i = colon + 1
        while i < n and body[i] == " ":
            i += 1
        # 读 value
        if body.startswith(_QUOTE, i):
            # 字符串值：读到下一个 <|"|>
            i += len(_QUOTE)
            qend = body.find(_QUOTE, i)
            if qend == -1:  # 未闭合（截断）→ 取到末尾
                args[key] = body[i:]
                i = n
            else:
                args[key] = body[i:qend]
                i = qend + len(_QUOTE)
        else:
            # 裸值：读到 ',' 或 '}'（这俩不会出现在裸数字/布尔里）
            j = i
            while j < n and body[j] not in ",}":
                j += 1
            args[key] = _coerce(body[i:j])
            i = j
    return args


def _parse_segment(segment: str) -> ToolCall | None:
    """解析单个 call:NAME{…} 片段为 ToolCall；非法返回 None。"""
    segment = segment.strip()
    if not segment.startswith(_CALL_PREFIX):
        return None
    rest = segment[len(_CALL_PREFIX):]
    brace = rest.find("{")
    if brace == -1:
        # 无参调用：call:name
        name = rest.strip().rstrip("}").strip()
        return ToolCall(name=name) if name else None
    name = rest[:brace].strip()
    if not name:
        return None
    return ToolCall(name=name, arguments=_parse_args(rest[brace + 1:]))


def parse_tool_calls(text: str) -> list[ToolCall]:
    """从模型输出文本解析出所有 ToolCall。

    无 <|tool_call> 标记 → 返回空列表（表示模型没发起函数调用，content 是普通回答）。
    对截断（缺 <tool_call|> 闭标记）宽容：取到文末。
    """
    if not text or _OPEN not in text:
        return []

    calls: list[ToolCall] = []
    i = 0
    while True:
        start = text.find(_OPEN, i)
        if start == -1:
            break
        seg_start = start + len(_OPEN)
        end = text.find(_CLOSE, seg_start)
        if end == -1:
            segment = text[seg_start:]
            i = len(text)
        else:
            segment = text[seg_start:end]
            i = end + len(_CLOSE)
        call = _parse_segment(segment)
        if call is not None:
            calls.append(call)
    return calls


def _encode_value(value) -> str:
    """把单个响应字段值编码成 Gemma 格式：字符串包 <|"|>，标量裸写，复合 JSON 串后包。"""
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return f"{_QUOTE}{value}{_QUOTE}"
    # dict / list 等复合 → JSON 串后当字符串回传
    return f"{_QUOTE}{json.dumps(value, ensure_ascii=False)}{_QUOTE}"


def encode_tool_response(name: str, response: dict) -> str:
    """把函数返回值编码成 Gemma 响应回传字符串（喂回模型那一轮）。

    形如：response:函数名{字段:值,字段:<|"|>字符串<|"|>}
    response 必须是 dict（顶层字段→值）。
    """
    fields = ",".join(f"{k}:{_encode_value(v)}" for k, v in response.items())
    return f"response:{name}{{{fields}}}"


# ── 分发 ─────────────────────────────────────────────────────────────────────


class UnknownToolError(Exception):
    """ToolCall 的 name 不在 handlers 注册表中。"""


def dispatch_tool_calls(
    calls: list[ToolCall],
    handlers: dict[str, Callable[[dict], object]],
) -> list[tuple[str, object]]:
    """按工具名把每个 ToolCall 分发到对应处理函数，返回 (name, 结果) 列表。

    handler 签名 = handler(arguments: dict) -> result。
    把整个参数 dict 交给 handler 自行取用（解耦：handler 不必逐个声明形参，
    也方便忽略模型给的冗余参数，如 parse_script 改用手上已有的文档而非模型回显）。

    未注册的工具名 → UnknownToolError（不静默吞，让上层决定如何处理）。
    """
    results: list[tuple[str, object]] = []
    for call in calls:
        handler = handlers.get(call.name)
        if handler is None:
            raise UnknownToolError(call.name)
        results.append((call.name, handler(call.arguments)))
    return results
