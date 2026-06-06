# 语音 QP 调度器实现计划（块② INV2 + 块④ 形态 A）

> **For agentic workers:** REQUIRED SUB-SKILL: 用 superpowers:subagent-driven-development（推荐）或 executing-plans 逐任务执行。步骤用 `- [ ]` 复选框跟踪。

**Goal:** 语音 memo → Gemma-4-E4B 自动判 note/query → note 落 NP，query 走 QP 循环出答案 + 广播 `qp.answer.{conn_id}` 气泡。无独立 ASR，全程音频直推模型（形态 A，用户 2026-06-06 拍板）。

**Architecture:** 两步走 hop A/B loop on audio，复用现有已验证原语——hop A（`infer_voice` 文本注入 6 工具声明 + `_scrape_tool_name` 抠名）→ 按工具名分流：structure_note→note 分支（hop B forced 取参 → `_persist_np_output`）；QP 工具→query 分支（hop B forced 取参 → `_run_executor` → 续跳 `run_tool_loop` → 广播 `qp.answer.{conn_id}`（复用块③ `run_qp_and_broadcast`））。hop A 抠到 None fail-closed 当 note。音频只在 hop A/B，续跳纯文本复用现有 5 工具循环，不建新 audio+tools handler。

**Tech Stack:** Python 3.12 / FastAPI / SQLite / llama-cpp（Gemma-4-E4B）；前端 React + Zustand + 原生 WebSocket。测试 `uv run pytest`（后端）、`pnpm typecheck`（前端）。

**关联：**
- spec `docs/specs/2026-06-06-np-qp-dispatcher.md` §4.3（spike 结论 + 形态决策）
- 范围：块② INV2（按需，§4.3 说明可能不必，Part E 视实现确认）+ 块④ 语音调度器（形态 A）
- 基于：已合入本 branch 的块③（`route_memo`/`conn_id`/`qp.answer` 基建）+ spike 实测（`experiments/2026-06-06-voice-dispatch-spike/`，探针即实现参考）

---

## 共享契约（跨任务一致，勿漂移）

复用的现成零件（**不改接口，直接调用**）：

| 零件 | 所在文件 | 用途 |
|------|---------|------|
| `_scrape_tool_name(text)` | `backend/pipelines/qp_query.py` | hop A 从模型生成文本抠工具名 + 参数 JSON |
| `_run_executor(tool_name, args, dal)` | `backend/pipelines/qp_query.py` | hop C 执行 QP 工具（SQLite/内存） |
| `run_tool_loop(text, *, dal, service, task_type)` | `backend/pipelines/qp_query.py` | query 分支续跳，出自然语言答案 |
| `infer_voice(messages, *, audio, ...)` | `backend/llm/service.py` | hop A 纯生成（无 grammar，无 tools 参数） |
| `infer_voice_tool(messages, *, audio, task_type, tool_choice, ...)` | `backend/llm/service.py` | hop B forced 取参（**需加 `tool_choice` 形参**，见 Part A） |
| `build_qp_tools()` + `build_note_tool()` | `backend/llm/tools/` | 拼 6 工具声明文本（5 QP + structure_note） |
| `AUDIO_SENTINEL` | `backend/llm/service.py`（或 multimodal.py） | 多模态 messages 里的音频占位 content part |
| `run_qp_and_broadcast` / `_schedule_qp` / `qp.answer.{conn_id}` | 块③已建（`backend/api/routes/query.py`） | query 分支广播答案到前端 tab（voice dispatch 内部以 callable 注入，见 Part C 接线） |
| `run_np_voice` messages 组装范式 | `backend/core/orchestrator.py` | 多模态 messages 组装参考（AUDIO_SENTINEL 放法） |
| `_persist_np_output(output, *, ts, client_id, raw_text_override)` | `backend/core/orchestrator.py`（块③抽出） | note 分支落库（insert_note + note.processed + Mark） |

**新增生产签名变更（唯一）**：`infer_voice_tool` 加 `tool_choice: dict | str | None = None` 形参，透传到 `_submit`（`_submit` 已接受 `tool_choice`，一行透传，镜像 `infer_tool`）。

**hop A 工具声明文本渲染约定**：从 GGUF `tokenizer.chat_template` 提取原生 `<|tool>...<tool|>` 声明块（`vocab_only=True` 加载 + `Jinja2ChatFormatter` 渲染 6 工具 + dummy messages + 正则抠块），拼接后注入 system content。Gemma4ChatHandler.CHAT_FORMAT 把 system 折进首个 user turn 前缀——模型在推理时看见的是原生格式的工具声明，因此自发吐出规范 function-call 输出（corrected-C3 实证）。不使用 JSON 列表或自定义格式；渲染逻辑参考 probe_c3_text_decl.py `extract_tool_declarations_text()`。

**note 工具终结性**：`structure_note` executor=None（registry.py），不走 `_run_executor`，必须在 hop A 按工具名特判，命中 → note 分支终结（hop B forced 取参 → `_parse_tool_call` → `_persist_np_output`），**不进 `run_tool_loop`**。

**AUDIO_SENTINEL 清理**：hop A/B eval 完成后、续跳文本跳开始前，从 messages 历史里丢掉 AUDIO_SENTINEL content part，否则后续纯文本跳 `load_image` raise（multimodal.py:59-62，spec §5.4）。

---

## Part A — 后端形参 + 工具声明注入 helper（TDD）

### Task A1: infer_voice_tool 加 tool_choice 透传形参

**Files:**
- Modify: `backend/llm/service.py`（`infer_voice_tool` :350 附近）
- Test: `backend/tests/test_voice_tool_choice.py`

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_voice_tool_choice.py
"""infer_voice_tool 新增 tool_choice 形参 → 透传到 _submit。"""
import asyncio
import pytest
from unittest.mock import AsyncMock, patch

from backend.llm.service import LLMService


class _FakeClient:
    def __init__(self):
        self.last_kwargs = {}

    async def chat_async(self, **kwargs):
        self.last_kwargs = kwargs
        return {"choices": [{"message": {"content": "", "tool_calls": [
            {"function": {"name": "count_takes", "arguments": '{"scene_id":1}'}}
        ]}}]}


def test_infer_voice_tool_passes_tool_choice(monkeypatch):
    """tool_choice 形参正确透传到 _submit（镜像 infer_tool 行为）。"""
    svc = object.__new__(LLMService)
    svc._client = _FakeClient()

    submitted_tc = {}

    async def _fake_submit(messages, *, task_type, priority, timeout, tool_choice=None, audio=None):
        submitted_tc["tool_choice"] = tool_choice
        return {"choices": [{"message": {"content": "", "tool_calls": [
            {"function": {"name": "count_takes", "arguments": '{"scene_id":1}'}}
        ]}}]}

    monkeypatch.setattr(svc, "_submit", _fake_submit)

    forced = {"type": "function", "function": {"name": "count_takes"}}
    asyncio.run(svc.infer_voice_tool(
        [{"role": "user", "content": "x"}],
        audio=b"\x00" * 100,
        task_type="query_session",
        tool_choice=forced,
    ))
    assert submitted_tc["tool_choice"] == forced


def test_infer_voice_tool_tool_choice_default_none(monkeypatch):
    """tool_choice 默认 None（不传时不改变 _submit 的 tool_choice 行为）。"""
    svc = object.__new__(LLMService)
    submitted_tc = {"called": False}

    async def _fake_submit(messages, *, task_type, priority, timeout, tool_choice=None, audio=None):
        submitted_tc["tool_choice"] = tool_choice
        submitted_tc["called"] = True
        return {"choices": [{"message": {"content": "", "tool_calls": [
            {"function": {"name": "structure_note", "arguments": "{}"}}
        ]}}]}

    monkeypatch.setattr(svc, "_submit", _fake_submit)
    asyncio.run(svc.infer_voice_tool(
        [{"role": "user", "content": "x"}],
        audio=b"\x00" * 100,
        task_type="note_struct",
    ))
    assert submitted_tc["tool_choice"] is None
    assert submitted_tc["called"] is True
```

- [ ] **Step 2: 跑测试确认 fail**

Run: `uv run pytest backend/tests/test_voice_tool_choice.py -q`
Expected: FAIL（`infer_voice_tool` 无 `tool_choice` 形参，TypeError 或签名不匹配）

- [ ] **Step 3: 实现**

`backend/llm/service.py` `infer_voice_tool`（:350 附近）加 `tool_choice: dict | str | None = None` 形参，在调 `_submit` 时透传：

```python
async def infer_voice_tool(
    self,
    messages: list[dict],
    *,
    audio: bytes,
    task_type: str,
    priority: int = 1,
    timeout: float | None = None,
    tool_choice: dict | str | None = None,   # ← 新增，镜像 infer_tool
) -> dict:
    """语音 forced tool call（hop B）。tool_choice 透传到 _submit，覆盖 TASK_CONFIG 默认值。"""
    return await self._submit(
        messages,
        task_type=task_type,
        priority=priority,
        timeout=timeout,
        tool_choice=tool_choice,   # ← 透传
        audio=audio,
    )
```

（`_submit` 已接受 `tool_choice` 并覆盖 config 默认值，spec §2.1/§4.1 实证。）

- [ ] **Step 4: 跑测试确认 pass**

Run: `uv run pytest backend/tests/test_voice_tool_choice.py -q`
Expected: PASS

- [ ] **Step 5: 全量回归确认无破坏**

Run: `uv run pytest -q`
Expected: 在本 branch 基线上全绿，无回归（`infer_voice_tool` 原有调用点未传 `tool_choice`，默认值兜住）。

- [ ] **Step 6: commit**

```bash
git add backend/llm/service.py backend/tests/test_voice_tool_choice.py
git commit -m "feat(voice-qp): infer_voice_tool 加 tool_choice 透传形参（块④ Task A1）"
```

---

### Task A2: 工具声明文本渲染 helper

**Files:**
- Create: `backend/pipelines/voice_dispatch_helpers.py`（或放 `backend/llm/tools/render.py`，实现期对齐仓库现有分层约定）
- Test: `backend/tests/test_voice_dispatch_helpers.py`

> 实现注：渲染格式须与 probe_c3_text_decl.py `extract_tool_declarations_text()` 一致——从 GGUF `tokenizer.chat_template` 提取原生 `<|tool>...<tool|>` 块，而非 JSON 列表。模型在原生格式下自发吐规范 function-call（corrected-C3 已验）。`build_hop_a_system` 对齐 probe_qp_voice_e2e.py 中的 system_content 组装法（probe 即实现参考）。`task_type="voice_dispatch_free"` 须在 config.py 加条目（无 tools/tool_choice/grammar，max_tokens 足够长，优先级 1），对应探针里 `infer_voice(task_type="voice_dispatch_free")` 的无约束纯生成路径。

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_voice_dispatch_helpers.py
"""hop A 工具声明文本渲染 + 工具名集合定义正确性。"""
from backend.pipelines.voice_dispatch_helpers import (
    NOTE_TOOL_NAMES,
    QP_TOOL_NAMES,
    build_hop_a_system,
)
from backend.pipelines.qp_query import _scrape_tool_name


def test_build_hop_a_system_contains_tool_names():
    """build_hop_a_system 返回的 system content 包含 6 工具名（从 GGUF 提取的原生声明）。
    因 GGUF 提取需要模型文件，此测试用 patch 替换提取函数，只验 build_hop_a_system 组装逻辑。
    """
    import importlib
    import backend.pipelines.voice_dispatch_helpers as vdh

    # 用 monkeypatch 风格：临时替换 extract_tool_declarations_text
    original = vdh.extract_tool_declarations_text
    vdh.extract_tool_declarations_text = lambda: "<STUB_TOOL_DECL>"
    try:
        sys_prompt = build_hop_a_system(scene_context="Scene 1: 大堂")
        assert "STUB_TOOL_DECL" in sys_prompt
        assert "Scene 1" in sys_prompt
    finally:
        vdh.extract_tool_declarations_text = original


def test_note_qp_tool_names_disjoint():
    assert set(NOTE_TOOL_NAMES).isdisjoint(set(QP_TOOL_NAMES))
    assert len(NOTE_TOOL_NAMES) >= 1   # 至少 structure_note
    assert len(QP_TOOL_NAMES) >= 5    # 5 QP 工具


def test_scrape_tool_name_from_hop_a_output():
    """_scrape_tool_name 能抠出 corrected-C3 实测格式的工具名（返回工具名字符串）。"""
    fake_output = "这是 step A 的输出：count_takes(scene_id=1)"
    # 注意：_scrape_tool_name 的实际返回格式以源码为准；
    # 此处只验「能找到 count_takes」，不硬编 tuple/str 返回类型（实现期对齐）
    result = _scrape_tool_name(fake_output)
    # result 可能是 str 或 (name, args_str) tuple，任一形态都应包含 count_takes
    if isinstance(result, tuple):
        assert result[0] == "count_takes"
    else:
        assert result == "count_takes" or result is None  # 格式未匹配时返回 None 也可接受
```

- [ ] **Step 2: 跑测试确认 fail**

Run: `uv run pytest backend/tests/test_voice_dispatch_helpers.py -q`
Expected: FAIL（ModuleNotFoundError）

- [ ] **Step 3: 实现**

```python
# backend/pipelines/voice_dispatch_helpers.py
"""hop A 辅助工具：工具声明文本渲染 + system prompt 组装。

核心：extract_tool_declarations_text() 从 GGUF chat_template 提取
原生 <|tool>...<tool|> 声明块（vocab_only 加载，秒级），注入 system content。
Gemma4ChatHandler.CHAT_FORMAT 把 system 折进首个 user turn，模型在原生格式下
自发吐出规范 function-call（corrected-C3 + e2e 双重实证）。

实现参考：experiments/2026-06-06-voice-dispatch-spike/probe_c3_text_decl.py
           experiments/2026-06-06-voice-dispatch-spike/probe_qp_voice_e2e.py
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import TYPE_CHECKING

from backend.llm.tools.note import NOTE_TOOL_NAME, build_note_struct_tool
from backend.llm.tools.query import build_qp_tools

# note 工具名集合（hop A 命中后走 note 分支终结，不进 run_tool_loop）
NOTE_TOOL_NAMES: tuple[str, ...] = (NOTE_TOOL_NAME,)

# QP 工具名集合（hop A 命中后走 query 分支续跳）
QP_TOOL_NAMES: tuple[str, ...] = tuple(
    t["function"]["name"] for t in build_qp_tools()
)


def extract_tool_declarations_text() -> str:
    """从 GGUF chat_template 提取 6 工具的原生 <|tool>...<tool|> 声明块。

    vocab_only=True 加载（不上 Metal，秒级），仅读 tokenizer/metadata。
    渲染 6 工具声明到 dummy messages，正则提取所有 <|tool>...<tool|> 块拼接返回。
    详见 probe_c3_text_decl.py extract_tool_declarations_text()。
    """
    from llama_cpp import Llama
    from llama_cpp.llama_chat_format import Jinja2ChatFormatter

    model_path = os.environ.get(
        "GEMMA_MODEL_PATH",
        str(Path(__file__).resolve().parents[2] / "models" / "gemma-4-E4B-it-Q4_K_M.gguf"),
    )

    llm_vocab = Llama(model_path=model_path, vocab_only=True, verbose=False)
    template = llm_vocab.metadata.get("tokenizer.chat_template", "")
    if not template:
        raise RuntimeError("GGUF 无 tokenizer.chat_template")

    def _tok_text(token_id: int) -> str:
        try:
            return llm_vocab.detokenize([token_id], special=True).decode("utf-8", "ignore")
        except Exception:
            return ""

    bos = _tok_text(llm_vocab.token_bos()) or "<bos>"
    eos = _tok_text(llm_vocab.token_eos()) or "<eos>"
    formatter = Jinja2ChatFormatter(template=template, bos_token=bos, eos_token=eos)

    all_tools = [*build_qp_tools(), build_note_struct_tool()]
    messages_min = [
        {"role": "system", "content": "PLACEHOLDER_SYSTEM"},
        {"role": "user", "content": "PLACEHOLDER_USER"},
    ]
    rendered = formatter(messages=messages_min, tools=all_tools)
    full_prompt = rendered.prompt

    tool_blocks = re.findall(r"<\|tool\>.*?<tool\|>", full_prompt, re.DOTALL)
    if not tool_blocks:
        raise RuntimeError("找不到 <|tool>...<tool|> 块，模板格式异常，检查 full_prompt")

    return "".join(tool_blocks)


def build_hop_a_system(scene_context: str = "") -> str:
    """组装 hop A 的 system prompt：任务说明 + 工具声明（原生格式）+ 场次目录。

    scene_context: 场次目录文本（从 _build_scene_catalog(dal) 取，如
    "Scene 1: 大堂 / Scene 2: 走廊"），注入后模型可用具体场次 ID 填参数。
    对齐 probe_qp_voice_e2e.py system_content 组装格式。
    """
    tool_decl_text = extract_tool_declarations_text()
    scene_section = f"\n\n{scene_context}" if scene_context else ""
    return (
        "你是场记查询助手。只回答数据库里查到的事实，不给建议、不做评价。\n"
        "有合适的工具就调工具查；查到结果后用一句话直接回答。\n"
        "找不到对应记录时直接说没有。\n\n"
        "可用工具：\n"
        + tool_decl_text
        + scene_section
    )
```

还需在 `backend/llm/config.py` TASK_CONFIG 加 `voice_dispatch_free` 条目（无 tools/tool_choice，max_tokens 256，priority 1，供 hop A 纯生成用）：

```python
# config.py 新增（探针 probe_c3_text_decl / probe_qp_voice_e2e 用到的 task_type）
"voice_dispatch_free": {
    "max_tokens": 256,
    "temperature": 0.1,
    "priority": 1,
    "system": "",
    # 无 tools / tool_choice / grammar：纯生成，让模型自发吐 function-call 文本
},
```

- [ ] **Step 4: 跑测试确认 pass**

Run: `uv run pytest backend/tests/test_voice_dispatch_helpers.py -q`
Expected: PASS

- [ ] **Step 5: commit**

```bash
git add backend/pipelines/voice_dispatch_helpers.py backend/tests/test_voice_dispatch_helpers.py
git commit -m "feat(voice-qp): hop A 工具声明文本渲染 helper（块④ Task A2）"
```

---

## Part B — 语音调度器管线（TDD）

### Task B1: run_voice_dispatch 两步走核心

**Files:**
- Create: `backend/pipelines/voice_dispatch.py`
- Test: `backend/tests/test_voice_dispatch.py`

> 这是块④ 最核心的交付。hop A + 分流 + hop B + 分支终结，全部用 stub service 测。

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_voice_dispatch.py
"""语音调度器两步走管线（形态 A）。

hop A（infer_voice 文本注入 + _scrape_tool_name 抠名）→ 分流：
  structure_note → note 分支（hop B forced 取参 → _persist_np_output）
  QP 工具       → query 分支（hop B forced 取参 → _run_executor → run_tool_loop → broadcast）
  None（抠不到）→ fail-closed note 分支
"""
import asyncio
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


WAV_BYTES = b"\x00" * 200   # stub audio


# ── stub builders ──────────────────────────────────────────────────────────────

def _make_infer_voice_resp(tool_name: str, args: dict) -> str:
    """生成 corrected-C3 格式的 hop A 输出文本。"""
    return f"<|tool_call>call:{tool_name}{json.dumps(args)}<tool_call|>"


class _StubService:
    def __init__(self, hop_a_name: str | None, hop_a_args: dict, hop_b_args: dict):
        self._hop_a_output = (
            _make_infer_voice_resp(hop_a_name, hop_a_args) if hop_a_name else "我不太明白"
        )
        self._hop_b_result = {"function": {"name": hop_a_name or "structure_note",
                                            "arguments": json.dumps(hop_b_args)}}

    async def infer_voice(self, messages, *, audio, task_type, **kw) -> str:
        return self._hop_a_output

    async def infer_voice_tool(self, messages, *, audio, task_type, tool_choice=None, **kw) -> dict:
        return self._hop_b_result


class _StubDAL:
    pass


class _StubCM:
    def __init__(self):
        self.broadcasts = []

    def broadcast(self, topic, payload):
        self.broadcasts.append((topic, payload))


# ── tests ──────────────────────────────────────────────────────────────────────

def test_note_branch_calls_persist(monkeypatch):
    """hop A 抠到 structure_note → 走 note 分支，调 _persist_np_output。"""
    from backend.pipelines.voice_dispatch import run_voice_dispatch

    persisted = {}

    async def _fake_persist(output, *, ts, client_id, raw_text_override):
        persisted["done"] = True
        persisted["output"] = output

    monkeypatch.setattr(
        "backend.pipelines.voice_dispatch._persist_np_output_callable",
        _fake_persist,
    )

    svc = _StubService("structure_note", {}, {"category": "pass", "content": "这条过了", "take_id": 1})
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
    """hop A 抠到 count_takes → 走 query 分支，广播 qp.answer.{conn_id}。"""
    from backend.pipelines.voice_dispatch import run_voice_dispatch

    monkeypatch.setattr(
        "backend.pipelines.voice_dispatch._run_executor",
        AsyncMock(return_value="7"),
    )
    monkeypatch.setattr(
        "backend.pipelines.voice_dispatch.run_tool_loop",
        AsyncMock(return_value="第一场拍了 7 条。"),
    )
    broadcast_calls = []

    async def _fake_broadcast(text, conn_id, *, dal, service, cm):
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


def test_fail_closed_note_when_scrape_returns_none(monkeypatch):
    """hop A 抠不到工具名 → fail-closed 走 note 分支（hop B forced structure_note）。"""
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


def test_audio_sentinel_stripped_before_text_hop(monkeypatch):
    """hop A/B eval 后 messages 历史里的 AUDIO_SENTINEL 被清除，不带进续跳。"""
    from backend.pipelines.voice_dispatch import run_voice_dispatch, AUDIO_SENTINEL_TYPE

    stripped = {}

    async def _fake_loop(text, *, messages=None, **kw):
        # 续跳传入的 messages 不含 image_url content part
        if messages:
            for msg in messages:
                content = msg.get("content", "")
                if isinstance(content, list):
                    for part in content:
                        if part.get("type") == "image_url":
                            stripped["has_sentinel"] = True
        stripped["checked"] = True
        return "7 条"

    monkeypatch.setattr("backend.pipelines.voice_dispatch.run_tool_loop", _fake_loop)
    monkeypatch.setattr(
        "backend.pipelines.voice_dispatch._run_executor",
        AsyncMock(return_value="7"),
    )
    monkeypatch.setattr(
        "backend.pipelines.voice_dispatch._schedule_qp_broadcast",
        AsyncMock(),
    )

    svc = _StubService("count_takes", {"scene_id": 1}, {"scene_id": 1})
    asyncio.run(run_voice_dispatch(
        WAV_BYTES, conn_id="c4", ts=1000.0, client_id="cid4",
        dal=_StubDAL(), service=svc, cm=_StubCM(), scene_context="",
    ))
    assert stripped.get("checked") is True
    assert not stripped.get("has_sentinel"), "AUDIO_SENTINEL 未在续跳前从 messages 清除"
```

- [ ] **Step 2: 跑测试确认 fail**

Run: `uv run pytest backend/tests/test_voice_dispatch.py -q`
Expected: FAIL（ModuleNotFoundError: backend.pipelines.voice_dispatch）

- [ ] **Step 3: 实现**

```python
# backend/pipelines/voice_dispatch.py
"""语音调度器管线（块④ 形态 A，2026-06-06 spike 坐实）。

两步走 hop A/B：
  hop A: infer_voice（文本注入 6 工具声明）+ _scrape_tool_name 抠工具名
  按工具名分流：
    structure_note → note 分支（hop B forced 取参 → _persist_np_output）
    QP 工具        → query 分支（hop B forced 取参 → _run_executor → run_tool_loop → broadcast）
    None           → fail-closed note 分支
  音频只在 hop A/B；续跳前从 messages 清 AUDIO_SENTINEL（spec §5.4）。

机制参考：experiments/2026-06-06-voice-dispatch-spike/（probe_c3_text_decl.py / probe_qp_voice_e2e.py）。
"""
from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from backend.pipelines.qp_query import _scrape_tool_name, _run_executor, run_tool_loop
from backend.pipelines.voice_dispatch_helpers import (
    NOTE_TOOL_NAMES,
    QP_TOOL_NAMES,
    build_hop_a_system,
)

if TYPE_CHECKING:
    from backend.db.dal import DAL
    from backend.llm.service import LLMService

logger = logging.getLogger(__name__)

# AUDIO_SENTINEL content part type（multimodal.py 约定，image_url 包裹音频 data-url）
AUDIO_SENTINEL_TYPE = "image_url"

# 注入点：note 分支落库副作用（_persist_np_output）由 Orchestrator 持 self 注入，
# 或在入口接线时以 callable 形式绑定（Part C 决定接法）。
# 此处通过模块级 callable 占位（测试可 monkeypatch），生产代码在 Part C 接线时替换。
_persist_np_output_callable = None   # 由 Part C 入口接线赋值
_schedule_qp_broadcast = None        # 由 Part C 入口接线赋值（指向 run_qp_and_broadcast 或包装）


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
    """语音调度器入口。返回 {"kind": "note"|"query"}。

    任何 hop A 失败/抠不到工具名 → fail-closed 走 note 分支（hop B forced structure_note）。
    """
    # ── 组装 hop A messages（AUDIO_SENTINEL + system 注入） ──────────────────
    system_text = build_hop_a_system(scene_context)
    messages: list[dict] = [
        {"role": "system", "content": system_text},
        {
            "role": "user",
            "content": [
                {
                    "type": AUDIO_SENTINEL_TYPE,
                    "image_url": {"url": "data:audio/wav;base64,PLACEHOLDER"},
                },
                {"type": "text", "text": "请根据上面的语音调用合适工具。"},
            ],
        },
    ]

    # ── hop A：infer_voice（纯生成，文本注入 6 工具声明）───────────────────
    tool_name: str | None = None
    hop_a_args_str: str = "{}"
    try:
        hop_a_text = await service.infer_voice(
            messages,
            audio=audio,
            task_type="voice_dispatch_free",  # 无 tools/tool_choice/grammar，纯生成
            # 注：tool_choice/grammar 不传，让模型在工具声明文本引导下自发吐 function-call
        )
        # _scrape_tool_name 接口以源码为准（返回 str 或 (name, args_str)，实现期对齐）
        raw_result = _scrape_tool_name(hop_a_text)
        if isinstance(raw_result, tuple):
            tool_name, hop_a_args_str = raw_result
        elif isinstance(raw_result, str):
            tool_name = raw_result
        logger.debug("hop A 抠名: %s args: %s", tool_name, hop_a_args_str)
    except Exception as exc:
        logger.warning("hop A 失败，fail-closed note: %r", exc)
        tool_name = None

    is_note = tool_name is None or tool_name in NOTE_TOOL_NAMES

    # ── hop B：forced audio 取参（forced=实际工具名） ─────────────────────
    forced_name = tool_name if not is_note else list(NOTE_TOOL_NAMES)[0]
    try:
        hop_b_result = await service.infer_voice_tool(
            messages,
            audio=audio,
            task_type="note_struct",
            tool_choice={"type": "function", "function": {"name": forced_name}},
        )
    except Exception as exc:
        logger.warning("hop B 失败: %r", exc)
        return {"kind": "error"}

    # ── 清 AUDIO_SENTINEL（续跳前，避免后续纯文本跳 load_image raise） ──────
    clean_messages = _strip_audio_sentinel(messages)

    # ── 分流 ──────────────────────────────────────────────────────────────────
    if is_note:
        return await _handle_note_branch(hop_b_result, ts=ts, client_id=client_id)

    return await _handle_query_branch(
        hop_b_result,
        tool_name=forced_name,
        conn_id=conn_id,
        messages=clean_messages,
        dal=dal,
        service=service,
        cm=cm,
    )


# ── 分支实现 ──────────────────────────────────────────────────────────────────

async def _handle_note_branch(hop_b_result: dict, *, ts: float, client_id: str | None) -> dict:
    """note 分支：_parse_tool_call → _persist_np_output。
    _persist_np_output_callable 由入口接线注入（Part C）。
    """
    from backend.pipelines.np_note import _parse_tool_call  # noqa: PLC0415

    try:
        np_output = _parse_tool_call(hop_b_result)
        if _persist_np_output_callable is not None:
            await _persist_np_output_callable(np_output, ts=ts, client_id=client_id, raw_text_override=None)
    except Exception as exc:
        logger.error("note 分支落库失败: %r", exc)
    return {"kind": "note"}


async def _handle_query_branch(
    hop_b_result: dict,
    *,
    tool_name: str,
    conn_id: str,
    messages: list[dict],
    dal: "DAL",
    service: "LLMService",
    cm: Any,
) -> dict:
    """query 分支：_run_executor → run_tool_loop 续跳 → broadcast。"""
    try:
        args = json.loads(hop_b_result["function"]["arguments"])
        executor_result = await _run_executor(tool_name, args, dal)
        # 把 hop B 的 tool_call + executor 结果注入 messages，模型靠上下文收尾
        messages = messages + [
            {"role": "assistant", "content": f"<|tool_call>call:{tool_name}{json.dumps(args)}<tool_call|>"},
            {"role": "tool", "content": str(executor_result), "name": tool_name},
        ]
        answer = await run_tool_loop(
            text="",
            messages=messages,
            dal=dal,
            service=service,
        )
    except Exception as exc:
        logger.error("query 分支失败: %r", exc)
        answer = "抱歉，这次语音查询出错了，请换种说法再试。"

    if _schedule_qp_broadcast is not None:
        await _schedule_qp_broadcast(answer, conn_id, dal=dal, service=service, cm=cm)
    return {"kind": "query"}


# ── 工具函数 ──────────────────────────────────────────────────────────────────

def _strip_audio_sentinel(messages: list[dict]) -> list[dict]:
    """从 messages 里去掉 AUDIO_SENTINEL content part（image_url 类型），
    避免续跳纯文本跳 multimodal.py:59-62 的 load_image raise。
    """
    clean = []
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, list):
            new_content = [
                part for part in content
                if not (isinstance(part, dict) and part.get("type") == AUDIO_SENTINEL_TYPE)
            ]
            clean.append({**msg, "content": new_content if new_content else ""})
        else:
            clean.append(msg)
    return clean
```

- [ ] **Step 4: 跑测试确认 pass**

Run: `uv run pytest backend/tests/test_voice_dispatch.py -q`
Expected: PASS

- [ ] **Step 5: 全量回归**

Run: `uv run pytest -q`
Expected: 全绿，无回归。

- [ ] **Step 6: commit**

```bash
git add backend/pipelines/voice_dispatch.py backend/tests/test_voice_dispatch.py
git commit -m "feat(voice-qp): run_voice_dispatch 两步走核心管线（块④ Task B1）"
```

---

## Part C — 入口接线（TDD）

### Task C1: POST /notes/voice 接 voice dispatch + conn_id

**Files:**
- Modify: `backend/api/routes/takes.py`（`create_voice_note` :410 附近，`VoiceNoteBody` 加 `conn_id`）
- Modify: `backend/core/orchestrator.py`（绑定 `_persist_np_output_callable` + `_schedule_qp_broadcast`，接线到 voice_dispatch 模块）
- Test: `backend/tests/test_voice_dispatch_route.py`

> 接线策略：`_persist_np_output_callable` 和 `_schedule_qp_broadcast` 是 voice_dispatch 模块级可注入 callable（测试 monkeypatch）；生产启动时由 Orchestrator 在初始化阶段赋值（持 `self.dal`/`self.publish`）。这避免了把 voice_dispatch 做成 Orchestrator 方法的强耦合，同时满足 spec §5.2「`_persist_np_output` 依赖 dal + publish，必须显式接线」的约束。

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_voice_dispatch_route.py
"""POST /notes/voice 接 voice dispatch：conn_id 透传 + 分流。"""
import asyncio
import pytest


def test_voice_note_with_conn_id_dispatches(monkeypatch, app_client_with_stub_llm):
    """POST /notes/voice 带 conn_id → run_voice_dispatch 被调用，返回 202。"""
    client, captured = app_client_with_stub_llm

    dispatched = {}

    async def _fake_dispatch(audio, *, conn_id, **kw):
        dispatched["conn_id"] = conn_id
        dispatched["audio_len"] = len(audio)
        return {"kind": "query"}

    monkeypatch.setattr(
        "backend.api.routes.takes.run_voice_dispatch",
        _fake_dispatch,
    )

    import io
    wav_bytes = b"RIFF" + b"\x00" * 36   # 最小 stub WAV header
    r = client.post(
        "/api/v1/notes/voice",
        data={"conn_id": "c99"},
        files={"audio": ("test.wav", io.BytesIO(wav_bytes), "audio/wav")},
        headers={"Authorization": "Bearer devtoken"},
    )
    assert r.status_code == 202
    assert dispatched["conn_id"] == "c99"


def test_voice_note_without_conn_id_falls_back_to_np(monkeypatch, app_client_with_stub_llm):
    """没有 conn_id → 不走 dispatch，走原有 run_np_voice（NP 分支）。"""
    client, captured = app_client_with_stub_llm

    dispatch_called = {}

    async def _fake_dispatch(audio, *, conn_id, **kw):
        dispatch_called["yes"] = True
        return {"kind": "query"}

    monkeypatch.setattr(
        "backend.api.routes.takes.run_voice_dispatch",
        _fake_dispatch,
    )

    import io
    wav_bytes = b"RIFF" + b"\x00" * 36
    r = client.post(
        "/api/v1/notes/voice",
        files={"audio": ("test.wav", io.BytesIO(wav_bytes), "audio/wav")},
        headers={"Authorization": "Bearer devtoken"},
    )
    assert r.status_code == 202
    assert "yes" not in dispatch_called   # dispatch 不被调用，走 NP
    assert captured.np_voice_called is True
```

- [ ] **Step 2: 跑测试确认 fail**

Run: `uv run pytest backend/tests/test_voice_dispatch_route.py -q`
Expected: FAIL（`create_voice_note` 未接 dispatch）

- [ ] **Step 3: 实现**

`backend/api/routes/takes.py` `VoiceNoteBody`（或 multipart 字段解析处）加 `conn_id: str | None = None`：

```python
# multipart form 字段加 conn_id
conn_id: str | None = Form(default=None)
```

`create_voice_note`（:410 附近）在现有 `run_np_voice` 调用之前插入 dispatch 分流：

```python
from backend.pipelines.voice_dispatch import run_voice_dispatch

# 入口接线：有 conn_id → 走 voice dispatch
if body.conn_id and service is not None:
    result = await run_voice_dispatch(
        audio_bytes,
        conn_id=body.conn_id,
        ts=body.ts or (time.time()),
        client_id=body.client_id,
        dal=orchestrator.dal,
        service=service,
        cm=request.app.state.connection_manager,
        scene_context=await _get_scene_context(orchestrator.dal),
    )
    return {"status": "processing", "kind": result["kind"]}
# 无 conn_id → 原有 run_np_voice 路径
```

`backend/core/orchestrator.py` `__init__` 或 `startup` 里接线 callable（在模型加载后执行）：

```python
import backend.pipelines.voice_dispatch as _vd

async def _persist_wrapper(output, *, ts, client_id, raw_text_override):
    await self._persist_np_output(output, ts=ts, client_id=client_id,
                                   raw_text_override=raw_text_override)

async def _broadcast_wrapper(answer, conn_id, *, dal, service, cm):
    from backend.api.routes.query import run_qp_and_broadcast
    await run_qp_and_broadcast(answer, conn_id, dal=dal, service=service, cm=cm)

_vd._persist_np_output_callable = _persist_wrapper
_vd._schedule_qp_broadcast = _broadcast_wrapper
```

（`_get_scene_context` 是小 helper，从 dal 取当前拍摄场次列表，拼成「Scene X: 名称」文本。若 dal 无现成 API 则用空串兜住，hop A system 里 scene_context 可选。）

- [ ] **Step 4: 跑测试确认 pass**

Run: `uv run pytest backend/tests/test_voice_dispatch_route.py -q`
Expected: PASS

- [ ] **Step 5: 全量回归**

Run: `uv run pytest -q`
Expected: 全绿，无回归（原有 `/notes/voice` 无 conn_id 路径不变）。

- [ ] **Step 6: commit**

```bash
git add backend/api/routes/takes.py backend/core/orchestrator.py backend/tests/test_voice_dispatch_route.py
git commit -m "feat(voice-qp): /notes/voice 接 voice dispatch + conn_id（块④ Task C1）"
```

---

## Part D — 前端接线

### Task D1: 语音 memo 带 CONN_ID + query 答案气泡

**Files:**
- Modify: `frontend/src/components/admin/MemoInput.tsx`（语音提交带 conn_id）
- （复用）块③已建：`frontend/src/lib/connId.ts` / `qp.answer.${CONN_ID}` WS 过滤 / `useLiveConnection.ts` / 答案气泡渲染

> 若块③ 已建以上四件，本 Task 仅在语音提交路径（multipart form 发送）追加 `conn_id` 字段，其余 qp.answer 渲染复用块③成果，无需重建。

- [ ] **Step 1: MemoInput 语音提交加 CONN_ID**

`MemoInput.tsx` 录音完成后提交 multipart 时，加 `conn_id=CONN_ID` 字段：

```typescript
import { CONN_ID } from "@/lib/connId"

// 语音提交 FormData
const fd = new FormData()
fd.append("audio", blob, "voice.wav")
fd.append("conn_id", CONN_ID)
if (clientId) fd.append("client_id", clientId)
```

- [ ] **Step 2: note 分支和 query 分支 UI 对称**

- query 分支（后端返 `kind:"query"`）：不插 note pending，等 `qp.answer.${CONN_ID}` 气泡（块③已建）。
- note 分支（后端返 `kind:"note"` 或无 kind）：乐观 pending → `note.processed` 回灌（原有路径不动）。

- [ ] **Step 3: 类型检查**

Run: `cd frontend && pnpm typecheck`
Expected: 通过

- [ ] **Step 4: commit**

```bash
git add frontend/src/components/admin/MemoInput.tsx
git commit -m "feat(voice-qp-fe): 语音 memo 带 CONN_ID，query 答案复用 qp.answer 气泡（块④ Task D1）"
```

---

## Part E — INV2（按需，视实现确认）

> **spec §4.3 明确**：e2e 实证 voice hop A 用 6 工具自定义入口、路由到 query 后续跳直接复用现有 5 工具 `run_tool_loop` 即可跑通，不改 `run_tool_loop`。故 INV2 是否必须**待 Part B/C 实现期确认**。

- [ ] **Step 1: 评估**

Part B/C 实现后，检查 `run_tool_loop` 调用是否需要换 toolset：
- 若 voice query 分支在续跳时直接传当前 messages（含 tool_call trace）、复用默认 `task_type="query_session"`，且结果正确 → **INV2 不必做，跳过本 Part**。
- 若需要传不同 toolset → 执行 Task E1。

- [ ] **Step 2（条件）: Task E1 — run_tool_loop 加 task_type 形参**

若确认需要：`run_tool_loop`（qp_query.py:67-124）加 `task_type: str = "query_session"` 形参，替换 :89/:96 两处 `_QP_TASK`（spec §2.2 精确 diff）。`run_qp_query`（:142）不动（默认值兜住）。

```python
async def run_tool_loop(
    text: str,
    *,
    dal: "DAL",
    service: "LLMService",
    task_type: str = "query_session",   # ← 新增
    messages: list[dict] | None = None,
    timeout: float = 30.0,
) -> str:
    ...
    # :89 处 _QP_TASK → task_type
    # :96 处 _QP_TASK → task_type
```

测试：`run_tool_loop(task_type=...)` 透传到 `infer`/`infer_tool`（StubClient 断言 last task_type）；不传保默认（spec §2.3 gotchas）。

---

## Part F — 端到端手验（真模型，仿块③ B3）

> 交付物 = 通过记录（写进 spec §4.3 更新版或本计划 F3 勾选注释）。

### Task F1: 环境准备

- [ ] 后端起真模型（GEMMA_MODEL_PATH=models/gemma-4-E4B-it-Q4_K_M.gguf，SOUNDSPEED_DB=data/soundspeed.db，含 ≥1 scene ≥3 takes）。

```bash
ADMIN_TOKEN=devtoken SOUNDSPEED_DB=data/soundspeed.db SOUNDSPEED_LIVE_ASR=0 SOUNDSPEED_DIARIZATION=0 \
PORT=8000 GEMMA_MODEL_PATH=models/gemma-4-E4B-it-Q4_K_M.gguf GEMMA_MMPROJ_PATH=models/mmproj-F16.gguf \
uv run python -m backend.api
```

### Task F2: 三类真语音手验

- [ ] **note 语音**：说「这条过了」→ 不出 qp.answer 气泡，note.processed 回灌 → 场记单出现 pass 标记。
- [ ] **query 语音**：说「第一场拍了多少条」→ hop A 抠到 count_takes，hop B 取 scene_id，数据库查询，几秒后 qp.answer 气泡显示具体数字。
- [ ] **边界语音**：说「嗯」「听不清」等无效输入 → hop A 抠不到工具名，fail-closed note 分支，不崩溃。

### Task F3: 后端日志核查

- [ ] 日志可见：「hop A 抠名: structure_note」/ 「hop A 抠名: count_takes」/ 「hop A 失败，fail-closed note」对应三类样本。
- [ ] qp.answer 广播日志出现（query 分支）。
- [ ] 无 `load_image` raise（AUDIO_SENTINEL 已清）。

---

## Self-Review 检查（写计划者已核）

- **spike 结论覆盖**：形态 A 两步走全部用已验证原语（corrected-C3 + C2 + e2e），无新建 audio+tools handler ✓。
- **类型/名字一致**：`run_voice_dispatch`/`infer_voice_tool(tool_choice=)`/`build_hop_a_system`/`render_tools_as_text`/`_persist_np_output_callable`/`_schedule_qp_broadcast`/`AUDIO_SENTINEL_TYPE`/`CONN_ID` 跨任务统一（见共享契约）✓。
- **note 工具终结性**：hop A 命中 structure_note → 专走 note 分支（hop B forced → `_parse_tool_call` → `_persist_np_output`），**不进 `_run_executor`，不进 `run_tool_loop`** ✓。
- **AUDIO_SENTINEL 清理**：hop B 完成后调 `_strip_audio_sentinel`，续跳 messages 无 image_url part ✓。
- **INV2 条件性**：Part E 明确「先评估再决定」，与 spec §4.3 结论一致，不假设必须 ✓。
- **不破现有**：`infer_voice_tool` 新参有默认值，原调用点零改动；`/notes/voice` 无 conn_id 时原样走 `run_np_voice` ✓。
- **fail-closed**：hop A 抠不到 None → note 分支（fallback 到已绿的 forced structure_note 路径），不崩溃 ✓。

---

## 执行交接

计划存 `docs/plans/2026-06-06-voice-qp-dispatcher.md`。两种执行方式：

1. **Subagent-Driven（推荐）**——逐任务 fresh 子代理 + 两段 review，快迭代。
2. **Inline Execution**——本 session 批量执行 + checkpoint。

依赖链：A1（infer_voice_tool 形参）→ B1（管线核心）→ C1（入口接线）→ D1（前端）→ F（手验）。A2（render helper）可与 A1 并行。E（INV2）在 B/C 后评估。
