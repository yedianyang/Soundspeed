# NP/QP 文本调度器 + 语音 spike 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: 用 superpowers:subagent-driven-development（推荐）或 executing-plans 逐任务执行。步骤用 `- [ ]` 复选框跟踪。

**Goal:** memo 框打字问 QP（如「第一场拍了多少条」）→ Gemma 自动判定 query → 路由到 QP → 答案经 `qp.answer` WS 回灌前端显示；普通备注照旧走 NP。外加语音 spike 出结论定 follow-up 怎么建语音调度器。

**Architecture:** 入口层薄分流。`POST /notes` 在 `parse_note` 之后插一个 forced 二分类器（`route_memo(kind)`，Tier-1 grammar 焊死），note→现有 NP（只被 gate 不改），query→`run_qp_and_broadcast` fire-and-forget + 广播 `qp.answer.{conn_id}`。内核（`run_tool_loop`/QP 工具/只读墙）零改动。语音 spike 是独立调研（先跑便宜的 binary-first probe）。

**Tech Stack:** Python 3.12 / FastAPI / SQLite / llama-cpp（Gemma-4-E4B）；前端 React + Zustand + react-query + 原生 WebSocket。测试 `uv run pytest`（后端）、`pnpm test` / `pnpm typecheck`（前端）。

**关联：** 设计稿 `docs/specs/2026-06-06-np-qp-dispatcher.md`。范围：本计划 = 块③ 文本调度器 + 块① 语音 spike（调研）。INV2② + 语音实现④ 拆 follow-up。

---

## 共享契约（跨任务一致，勿漂移）

- 工具名常量 `ROUTE_TOOL_NAME = "route_memo"`，单参 `kind: Literal["note","query"]`（required，扁平枚举）。
- task_type `"memo_route"`：forced `tool_choice` 锁 route_memo，`max_tokens` 16、低温、priority 1。
- `classify_memo(text: str, service, *, timeout: float = 5.0) -> str`（`backend/pipelines/memo_route.py`）：返回 `"note"` 或 `"query"`，**任何异常/超时/畸形 → fail-closed `"note"`**。
- `run_qp_and_broadcast(text: str, conn_id: str, *, dal, service, cm) -> str`（`backend/api/routes/query.py`）：跑 `run_qp_query` → `cm.broadcast(f"{QP_ANSWER}.{conn_id}", QpAnswerPayload(connection_id=conn_id, answer_text=answer))` → 返回 answer。`post_query` 与调度器 query 分支共用。
- `NoteCreateBody.conn_id: str | None = None`。
- 前端 `CONN_ID`（`frontend/src/lib/connId.ts`，import 时生成一次，每 tab 唯一）；`postNote` 带 `conn_id`；`useLiveConnection` 按 `qp.answer.${CONN_ID}` 过滤。

---

## Part A — 后端文本调度器（块③，TDD）

### Task A1: route_memo 工具 builder

**Files:**
- Create: `backend/llm/tools/route.py`
- Test: `backend/tests/test_route_tool.py`

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_route_tool.py
from backend.llm.tools.route import ROUTE_TOOL_NAME, build_route_memo_tool


def test_route_tool_shape():
    tool = build_route_memo_tool()
    assert tool["type"] == "function"
    fn = tool["function"]
    assert fn["name"] == ROUTE_TOOL_NAME == "route_memo"
    params = fn["parameters"]
    assert params["type"] == "object"
    kind = params["properties"]["kind"]
    assert kind["type"] == "string"
    assert kind["enum"] == ["note", "query"]   # 顺序固定
    assert params["required"] == ["kind"]
```

- [ ] **Step 2: 跑测试确认 fail**

Run: `uv run pytest backend/tests/test_route_tool.py -q`
Expected: FAIL（ModuleNotFoundError: backend.llm.tools.route）

- [ ] **Step 3: 实现**

```python
# backend/llm/tools/route.py
"""route_memo 工具（入口调度器 forced 二分类，对标 tools/note.py）。

build_route_memo_tool() 返回 OpenAI function calling 风格 tool dict：模型读这条 memo
是「记录（note）」还是「查询（query）」。Tier-1 forced + grammar，零解析风险。

kind enum 是字面量 ["note","query"]，本模块 import-neutral（不拉 pipelines），
config 可模块级 eager import（无循环 import 风险，同 build_l2_tool）。
"""

from __future__ import annotations

# 工具名（config tool_choice / registry 注册 / 本构造器三处须一致）。
ROUTE_TOOL_NAME = "route_memo"


def build_route_memo_tool() -> dict:
    """构造 route_memo OpenAI 风格 tool dict（单参 kind: note|query）。"""
    return {
        "type": "function",
        "function": {
            "name": ROUTE_TOOL_NAME,
            "description": (
                "判断录音师这条输入是「记录一条备注」还是「查询场记信息」。"
                "想知道/查/问某个事实（拍了多少条、在哪拍、有几个角色、第几场……）→ query；"
                "对某条素材的评价、好坏、问题、要保要过要废 → note。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "kind": {
                        "type": "string",
                        "enum": ["note", "query"],
                        "description": "note=记录备注；query=查询信息。",
                    },
                },
                "required": ["kind"],
            },
        },
    }
```

- [ ] **Step 4: 跑测试确认 pass**

Run: `uv run pytest backend/tests/test_route_tool.py -q`
Expected: PASS

- [ ] **Step 5: commit**

```bash
git add backend/llm/tools/route.py backend/tests/test_route_tool.py
git commit -m "feat(dispatcher): route_memo forced 二分类工具 schema（块③ Task A1）"
```

---

### Task A2: memo_route task_type 进 config + registry

**Files:**
- Modify: `backend/llm/config.py`（import 块 :28-29；TASK_CONFIG 加条目）
- Modify: `backend/llm/tools/registry.py`（`_bootstrap` :88-94 追加注册）
- Test: `backend/tests/test_memo_route_config.py`

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_memo_route_config.py
from backend.llm.config import TASK_CONFIG
from backend.llm.tools import registry
from backend.llm.tools.route import ROUTE_TOOL_NAME


def test_memo_route_task_config():
    cfg = TASK_CONFIG["memo_route"]
    assert cfg["tool_choice"] == {"type": "function", "function": {"name": ROUTE_TOOL_NAME}}
    names = [t["function"]["name"] for t in cfg["tools"]]
    assert names == [ROUTE_TOOL_NAME]
    assert cfg["priority"] == 1
    assert cfg["max_tokens"] <= 64  # 二分输出极短


def test_memo_route_registered():
    # 注册仅对称用（Tier-1 forced 运行期绕过 registry），executor 必须 None
    assert registry.get_tool_schema(ROUTE_TOOL_NAME)["function"]["name"] == ROUTE_TOOL_NAME
    assert registry.get_executor(ROUTE_TOOL_NAME) is None
```

- [ ] **Step 2: 跑测试确认 fail**

Run: `uv run pytest backend/tests/test_memo_route_config.py -q`
Expected: FAIL（KeyError: 'memo_route' / KeyError route_memo 未注册）

- [ ] **Step 3: 实现 config.py**

`backend/llm/config.py` import 块（:28-29 之后）加：

```python
from backend.llm.tools.route import ROUTE_TOOL_NAME, build_route_memo_tool
```

`TASK_CONFIG` 里（紧挨 `note_struct` 条目，:152 之后）加：

```python
    # 入口调度器：forced 二分类 route_memo(kind: note|query)。route.py import-neutral，
    # eager 挂 build_route_memo_tool() 安全（无 np_note 依赖，无须 lazy）。
    "memo_route": {
        "max_tokens": 16,
        "temperature": 0.1,
        "priority": 1,
        "system": "判断这条 memo 是记录备注还是查询信息。",
        "tools": [build_route_memo_tool()],
        "tool_choice": {
            "type": "function",
            "function": {"name": ROUTE_TOOL_NAME},
        },
    },
```

- [ ] **Step 4: 实现 registry.py**

`backend/llm/tools/registry.py` `_bootstrap()` 里（:94 `register(NOTE_TOOL_NAME, ...)` 之后）加：

```python
    from backend.llm.tools.route import ROUTE_TOOL_NAME, build_route_memo_tool  # noqa: PLC0415

    register(ROUTE_TOOL_NAME, build_route_memo_tool(), executor=None)  # 入口调度器 forced 工具
```

- [ ] **Step 5: 跑测试确认 pass + 冷 import 环检测**

Run: `uv run pytest backend/tests/test_memo_route_config.py backend/tests/test_import_hygiene.py -q`
Expected: PASS（含 import 卫生闸门，确认 route.py eager import 不成环）

- [ ] **Step 6: commit**

```bash
git add backend/llm/config.py backend/llm/tools/registry.py backend/tests/test_memo_route_config.py
git commit -m "feat(dispatcher): memo_route task_type + registry 注册（块③ Task A2）"
```

---

### Task A3: classify_memo 分类器管线（fail-closed）

**Files:**
- Create: `backend/pipelines/memo_route.py`
- Test: `backend/tests/test_memo_route_classify.py`

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_memo_route_classify.py
import asyncio
import json
import pytest

from backend.pipelines.memo_route import classify_memo


class _StubService:
    def __init__(self, *, kind=None, exc=None):
        self._kind = kind
        self._exc = exc
        self.last_task_type = None

    async def infer_tool(self, messages, task_type, priority=None, timeout=None, tool_choice=None):
        self.last_task_type = task_type
        if self._exc is not None:
            raise self._exc
        return {"function": {"name": "route_memo", "arguments": json.dumps({"kind": self._kind})}}


def test_classify_query():
    svc = _StubService(kind="query")
    assert asyncio.run(classify_memo("第一场拍了多少条", svc)) == "query"
    assert svc.last_task_type == "memo_route"


def test_classify_note():
    assert asyncio.run(classify_memo("这条过了", _StubService(kind="note"))) == "note"


@pytest.mark.parametrize("exc", [asyncio.TimeoutError(), LookupError(), ValueError()])
def test_classify_fail_closed_to_note(exc):
    # 任何异常 → fail-closed note，绝不让分类器宕掉挡住备注提交
    assert asyncio.run(classify_memo("x", _StubService(exc=exc))) == "note"


def test_classify_bad_kind_fail_closed():
    # 模型吐了非法 kind → note
    assert asyncio.run(classify_memo("x", _StubService(kind="garbage"))) == "note"
```

- [ ] **Step 2: 跑测试确认 fail**

Run: `uv run pytest backend/tests/test_memo_route_classify.py -q`
Expected: FAIL（ModuleNotFoundError）

- [ ] **Step 3: 实现**

```python
# backend/pipelines/memo_route.py
"""入口调度器分类器（块③）：forced 二分类 memo → note | query。

classify_memo 对标 run_np_note 的 forced tool-call 路径，但只取 kind 字段。
设计纪律（spec §3.3）：任何失败 → fail-closed "note"——分类器宕了也绝不能挡掉备注提交。
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.llm.service import LLMService

logger = logging.getLogger(__name__)

_VALID_KINDS = ("note", "query")

_SYSTEM = (
    "你是场记输入分诊器。判断录音师这条输入是要「记录一条备注（note）」"
    "还是「查询场记信息（query）」。\n"
    "- query：想知道/查/问某个事实，如「第一场拍了多少条」「第72场在哪拍」「有几个角色」。\n"
    "- note：对某条素材的评价/好坏/问题/要保要过要废，如「这条过了」「收音有点小」「第三条留着」。"
)


async def classify_memo(
    text: str,
    service: "LLMService",
    *,
    timeout: float = 5.0,
) -> str:
    """forced 二分类 → "note" | "query"。任何异常/超时/畸形 → fail-closed "note"。"""
    messages = [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": text},
    ]
    try:
        tool_call = await service.infer_tool(
            messages,
            task_type="memo_route",
            priority=1,
            timeout=timeout,
        )
        kind = json.loads(tool_call["function"]["arguments"])["kind"]
    except Exception as exc:  # noqa: BLE001  fail-closed：分类器任何失败都退回 note
        logger.warning("memo 分类失败，fail-closed note: %r", exc)
        return "note"
    if kind not in _VALID_KINDS:
        logger.warning("memo 分类返回非法 kind=%r，fail-closed note", kind)
        return "note"
    return kind
```

- [ ] **Step 4: 跑测试确认 pass**

Run: `uv run pytest backend/tests/test_memo_route_classify.py -q`
Expected: PASS

- [ ] **Step 5: commit**

```bash
git add backend/pipelines/memo_route.py backend/tests/test_memo_route_classify.py
git commit -m "feat(dispatcher): classify_memo forced 二分类 + fail-closed note（块③ Task A3）"
```

---

### Task A4: 抽 run_qp_and_broadcast 共享 helper

**Files:**
- Modify: `backend/api/routes/query.py`（抽 helper + post_query 复用）
- Test: `backend/tests/test_query_route.py`（若已存在则补，否则新建）

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_query_route.py
import asyncio

from backend.api.routes.query import run_qp_and_broadcast


class _StubCM:
    def __init__(self):
        self.calls = []

    def broadcast(self, topic, payload):
        self.calls.append((topic, payload))


class _StubService:
    pass


def test_run_qp_and_broadcast_broadcasts(monkeypatch):
    async def _fake_query(*, text, dal, service, timeout=30.0):
        return "第一场一共拍了 3 条。"

    monkeypatch.setattr("backend.api.routes.query.run_qp_query", _fake_query)
    cm = _StubCM()
    answer = asyncio.run(
        run_qp_and_broadcast("第一场拍了多少条", "conn-7", dal=None, service=_StubService(), cm=cm)
    )
    assert answer == "第一场一共拍了 3 条。"
    assert cm.calls[0][0] == "qp.answer.conn-7"
    assert cm.calls[0][1].connection_id == "conn-7"
    assert cm.calls[0][1].answer_text == answer


def test_run_qp_and_broadcast_error_friendly(monkeypatch):
    async def _boom(*, text, dal, service, timeout=30.0):
        raise RuntimeError("db down")

    monkeypatch.setattr("backend.api.routes.query.run_qp_query", _boom)
    cm = _StubCM()
    answer = asyncio.run(run_qp_and_broadcast("x", "c1", dal=None, service=_StubService(), cm=cm))
    assert "抱歉" in answer  # 友好兜底，仍广播
    assert cm.calls[0][0] == "qp.answer.c1"
```

- [ ] **Step 2: 跑测试确认 fail**

Run: `uv run pytest backend/tests/test_query_route.py -q`
Expected: FAIL（ImportError: run_qp_and_broadcast）

- [ ] **Step 3: 实现（重构 query.py）**

`backend/api/routes/query.py` 把 post_query 的核心抽成 helper，post_query 改为复用。新 `query.py`：

```python
"""QP 直连入口（spec §10）：POST /api/v1/query + 共享 run_qp_and_broadcast。

run_qp_and_broadcast：跑 QP 循环 → 广播 qp.answer.{conn_id} → 返回答案。
post_query（直连 demo，同步返回）与入口调度器 query 分支（fire-and-forget）共用此 helper。
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from backend.api.auth import require_admin
from backend.core.events import QP_ANSWER, QpAnswerPayload
from backend.pipelines.qp_query import run_qp_query

if TYPE_CHECKING:
    from backend.db.dal import DAL
    from backend.llm.service import LLMService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["query"])


class QueryRequest(BaseModel):
    text: str
    conn_id: str


async def run_qp_and_broadcast(
    text: str,
    conn_id: str,
    *,
    dal: "DAL",
    service: "LLMService",
    cm,
) -> str:
    """跑 QP 两步走循环 → 广播 qp.answer.{conn_id} → 返回答案。

    run_qp_query 把 TimeoutError 等放行到这里，兜成友好自然语言、不抛穿（caller 可能是
    fire-and-forget task，没有人接异常）。CancelledError（BaseException）不在此捕获。
    """
    try:
        answer = await run_qp_query(text=text, dal=dal, service=service)
    except Exception as exc:  # noqa: BLE001
        logger.warning("qp query 失败 conn_id=%s: %r", conn_id, exc)
        answer = "抱歉，这次查询出错了，请换种说法再试一次。"
    cm.broadcast(
        f"{QP_ANSWER}.{conn_id}",
        QpAnswerPayload(connection_id=conn_id, answer_text=answer),
    )
    return answer


@router.post("/query")
async def post_query(
    body: QueryRequest,
    request: Request,
    _: None = Depends(require_admin),
) -> dict:
    """跑 QP 两步走循环 → 广播 qp.answer.{conn_id} + 同步返回答案。"""
    orchestrator = request.app.state.orchestrator
    service = getattr(request.app.state, "llm_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="LLM service 未就绪")
    answer = await run_qp_and_broadcast(
        body.text,
        body.conn_id,
        dal=orchestrator.dal,
        service=service,
        cm=request.app.state.connection_manager,
    )
    return {"status": "ok", "answer": answer}
```

- [ ] **Step 4: 跑测试确认 pass + 旧 query 测试不破**

Run: `uv run pytest backend/tests/test_query_route.py -q && uv run pytest -k "query" -q`
Expected: PASS（新 helper 测试 + 原 post_query 相关测试全绿）

- [ ] **Step 5: commit**

```bash
git add backend/api/routes/query.py backend/tests/test_query_route.py
git commit -m "refactor(qp): 抽 run_qp_and_broadcast 供 post_query + 调度器复用（块③ Task A4）"
```

---

### Task A5: NoteCreateBody conn_id + create_note 分流

**Files:**
- Modify: `backend/api/routes/takes.py`（NoteCreateBody :295-301；create_note :325-382；加 import + 模块级 fire-and-forget 工具）
- Test: `backend/tests/test_notes_dispatch.py`

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_notes_dispatch.py
import asyncio
import pytest
from fastapi.testclient import TestClient

# 复用既有 app fixture 习惯（参考 backend/tests/test_notes*.py 的 client fixture）。
# 这里给出分流断言要点；实现期对齐仓库现有 note 端点测试的 fixture 风格。

from backend.api.routes import takes as takes_mod


def test_query_branch_schedules_qp(monkeypatch, app_client_with_stub_llm):
    """conn_id + 分类=query → 调度 run_qp_and_broadcast、不进 NP、返回 kind=query。"""
    client, captured = app_client_with_stub_llm
    monkeypatch.setattr(
        "backend.api.routes.takes.classify_memo",
        _async_const("query"),
    )
    scheduled = {}
    monkeypatch.setattr(
        "backend.api.routes.takes._schedule_qp",
        lambda text, conn_id, **kw: scheduled.update(text=text, conn_id=conn_id),
    )
    r = client.post("/api/v1/notes", json={"text": "第一场拍了多少条", "conn_id": "c9"},
                    headers={"Authorization": "Bearer devtoken"})
    assert r.status_code == 202
    assert r.json()["kind"] == "query"
    assert scheduled == {"text": "第一场拍了多少条", "conn_id": "c9"}
    assert captured.np_called is False


def test_note_branch_unchanged(monkeypatch, app_client_with_stub_llm):
    """分类=note → 原样进 run_np_async。"""
    client, captured = app_client_with_stub_llm
    monkeypatch.setattr("backend.api.routes.takes.classify_memo", _async_const("note"))
    r = client.post("/api/v1/notes", json={"text": "这条过了", "conn_id": "c9"},
                    headers={"Authorization": "Bearer devtoken"})
    assert r.status_code == 202
    assert captured.np_called is True


def test_no_conn_id_stays_note(monkeypatch, app_client_with_stub_llm):
    """没 conn_id（query 无处广播）→ 不分类、直接 note。"""
    client, captured = app_client_with_stub_llm
    called = {"classify": False}
    monkeypatch.setattr("backend.api.routes.takes.classify_memo",
                        _mark_called(called, "classify", "query"))
    r = client.post("/api/v1/notes", json={"text": "第一场拍了多少条"},
                    headers={"Authorization": "Bearer devtoken"})
    assert r.status_code == 202
    assert called["classify"] is False   # 无 conn_id 不调分类
    assert captured.np_called is True


def _async_const(val):
    async def _f(text, service, **kw):
        return val
    return _f


def _mark_called(flag, key, val):
    async def _f(text, service, **kw):
        flag[key] = True
        return val
    return _f
```

> 实现注：`app_client_with_stub_llm` fixture 复用/扩展 `backend/tests/` 现有 note 端点 fixture（注入带 `llm_service`、`connection_manager`、`orchestrator` 的 app，`run_np_async` 打桩记 `np_called`，token=devtoken）。实现者对齐既有 `test_notes*.py` fixture，不新造一套。

- [ ] **Step 2: 跑测试确认 fail**

Run: `uv run pytest backend/tests/test_notes_dispatch.py -q`
Expected: FAIL（create_note 未分流 / `_schedule_qp` 不存在）

- [ ] **Step 3: 实现 takes.py**

import 区（:46 `from backend.pipelines.note_parse import ...` 附近）加：

```python
import asyncio

from backend.api.routes.query import run_qp_and_broadcast
from backend.pipelines.memo_route import classify_memo
```

`NoteCreateBody`（:295-301）加字段：

```python
class NoteCreateBody(BaseModel):
    """POST /notes 请求体。"""

    text: str
    ts: float | None = None
    # 前端生成的乐观 pending 去重键（crypto.randomUUID），原样回传到 note.processed。
    client_id: str | None = None
    # WS 连接标识：query 分支据此把答案广播到 qp.answer.{conn_id}（前端按前缀认领）。
    conn_id: str | None = None
```

模块级 fire-and-forget 工具（放 `create_note` 之前，`_MAX_VOICE_BYTES` 附近）：

```python
# query 分支 fire-and-forget task 持有集：防 asyncio.create_task 结果被 GC（Python 文档建议）。
_qp_tasks: set[asyncio.Task] = set()


def _qp_task_done(task: asyncio.Task) -> None:
    _qp_tasks.discard(task)
    if not task.cancelled() and task.exception() is not None:
        logger.warning("qp 调度 task 异常: %r", task.exception())


def _schedule_qp(text: str, conn_id: str, **kwargs) -> None:
    """调度 run_qp_and_broadcast 为 fire-and-forget task（kwargs: dal/service/cm）。"""
    task = asyncio.create_task(run_qp_and_broadcast(text, conn_id, **kwargs))
    _qp_tasks.add(task)
    task.add_done_callback(_qp_task_done)
```

`create_note`（:325-382）在 `parse_note` 成功（:342）之后、`run_np_async`（:345）之前插分类分流：

```python
    # 入口调度器（块③）：query → QP fire-and-forget 广播；note/任何失败 → 现有 NP。
    # 仅当有 conn_id（query 答案要广播到 qp.answer.{conn_id}）且 LLM 就绪、且非 @显式类别时分类。
    # classify 有意留在 202 关键路径（route 须在 202 时知道 kind 才能告诉前端 query/note，
    # 决定要不要撤掉乐观 pending）；其延迟由前端乐观 pending 提前到 await 前藏掉（见 Part B Task B2），
    # 故后端无须把 classify 挪后台、保持简单。
    service = getattr(request.app.state, "llm_service", None)
    if service is not None and body.conn_id and not body.text.lstrip().startswith("@"):
        kind = await classify_memo(note.raw_text, service)
        if kind == "query":
            _schedule_qp(
                note.raw_text,
                body.conn_id,
                dal=orchestrator.dal,
                service=service,
                cm=request.app.state.connection_manager,
            )
            return {"status": "processing", "kind": "query"}
```

（`@` 开头是显式 category 备注，必是 note，跳过分类省一次 LLM 调用；无 conn_id 的 query 无处广播，也退 note。`note` 分支及其 RuntimeError fallback :345-382 一字不动。）

- [ ] **Step 4: 跑测试确认 pass**

Run: `uv run pytest backend/tests/test_notes_dispatch.py -q`
Expected: PASS

- [ ] **Step 5: 全量回归（确认 note 端点 + QP + service 无回归）**

Run: `uv run pytest -q`
Expected: 在 main 基线（`776 passed, 12 skipped`）上 +新增测试全绿，无回归。

- [ ] **Step 6: commit**

```bash
git add backend/api/routes/takes.py backend/tests/test_notes_dispatch.py
git commit -m "feat(dispatcher): /notes 插 memo_route 分流（query→QP 广播，note 原样）（块③ Task A5）"
```

---

## Part B — 前端 QP 答案（块③ 前端，让用户真看到答案）

> UX 待定点：QP 答案在 memo 框**哪里显示**（浮层 toast / 框上方气泡）。本 Part 默认渲染成「memo 框上方一条临时气泡，几秒后淡出」，实现期出草样给 Lead 定位置再调。后端不依赖本 Part 即可测（gate 走 `POST /query`）。

> **投递机制已核（ws.py:139 + :22）**：`ConnectionManager.broadcast` 是 **send-to-all**——`_async_broadcast` `for ws in list(self._active)` 投给全部活跃连接，无客户端订阅协议。故 `qp.answer.{conn_id}` 这种动态 topic 到达所有浏览器，本 tab 按 `topic === qp.answer.${CONN_ID}` 认领，其余 tab 过滤丢弃。post_query 既有广播从没到过前端（前端无 handler），但机制是 send-to-all，无须后端加订阅步骤。

### Task B1: 客户端 CONN_ID + postNote 带 conn_id

**Files:**
- Create: `frontend/src/lib/connId.ts`
- Modify: `frontend/src/lib/api.ts`（`postNote` 加 connId）
- Modify: `frontend/src/components/admin/MemoInput.tsx`（提交带 CONN_ID）

- [ ] **Step 1: CONN_ID 模块**

```typescript
// frontend/src/lib/connId.ts
// 每个 tab 一个稳定连接标识：发 QP 查询时带上，后端把答案广播到 qp.answer.{CONN_ID}，
// 本 tab 据此认领自己的答案（其他 tab 收到也按前缀过滤掉）。import 时生成一次。
// crypto.randomUUID 仅安全源可用，局域网 HTTP 回退（同 MemoInput.newClientId）。
export const CONN_ID: string =
  crypto?.randomUUID?.() ?? `conn-${Date.now()}-${Math.random().toString(36).slice(2)}`
```

- [ ] **Step 2: api.ts postNote 加 conn_id**

```typescript
// frontend/src/lib/api.ts  postNote 改签名 + body
export function postNote(
  text: string,
  ts?: number,
  clientId?: string,
  connId?: string,
): Promise<NoteCreateResponse> {
  return request<NoteCreateResponse>(`/api/v1/notes`, {
    method: "POST",
    body: JSON.stringify({ text, ts: ts ?? undefined, client_id: clientId, conn_id: connId }),
  })
}
```

- [ ] **Step 3: MemoInput 提交带 CONN_ID**

`MemoInput.tsx`：import `CONN_ID`，`handleSubmit` 里 `postNote(trimmed, undefined, clientId)` → `postNote(trimmed, undefined, clientId, CONN_ID)`。

```typescript
import { CONN_ID } from "@/lib/connId"
// ...
const resp: NoteCreateResponse = await postNote(trimmed, undefined, clientId, CONN_ID)
```

> 注：`NoteCreateResponse` 现含 category/content（note 分支）；query 分支后端返 `{status:"processing", kind:"query"}`。乐观 pending 仅在非 query 时插——见 Task B2 处理 kind 分歧（query 不插 note pending，等 qp.answer）。

- [ ] **Step 4: 前端类型检查**

Run: `cd frontend && pnpm typecheck`
Expected: 通过（postNote 新参可选，调用点已更新）

- [ ] **Step 5: commit**

```bash
git add frontend/src/lib/connId.ts frontend/src/lib/api.ts frontend/src/components/admin/MemoInput.tsx
git commit -m "feat(dispatcher-fe): memo 提交带 CONN_ID（块③ 前端 Task B1）"
```

---

### Task B2: 收 qp.answer + 渲染答案

**Files:**
- Modify: `frontend/src/types/api.ts`（QpAnswerMsg 类型）
- Modify: `frontend/src/store/session.ts`（qpAnswer 状态 + setter）
- Modify: `frontend/src/hooks/useLiveConnection.ts`（`qp.answer.${CONN_ID}` 过滤）
- Modify: `frontend/src/components/admin/MemoInput.tsx`（query 提交不插 note pending；渲染答案气泡）

- [ ] **Step 1: 类型**

```typescript
// frontend/src/types/api.ts  追加
export interface QpAnswerMsg {
  connection_id: string
  answer_text: string
}
```

- [ ] **Step 2: session store 加 qpAnswer**

```typescript
// frontend/src/store/session.ts  state 加字段 + action
// state: qpAnswer: { text: string; ts: number } | null
// action: setQpAnswer(text: string) => set({ qpAnswer: { text, ts: Date.now() } })
//         clearQpAnswer() => set({ qpAnswer: null })
```

（对齐 store 现有 slice 写法：在 state 初值加 `qpAnswer: null`，actions 加 `setQpAnswer`/`clearQpAnswer`。）

- [ ] **Step 3: useLiveConnection 过滤 qp.answer**

`useLiveConnection.ts` `onMessage` 里（`note.failed` 分支 :119 之后）加：

```typescript
        if (topic === `qp.answer.${CONN_ID}`) {
          s.setQpAnswer((payload as QpAnswerMsg).answer_text)
          return
        }
```

import `CONN_ID` from `@/lib/connId`、`QpAnswerMsg` from `@/types/api`。

- [ ] **Step 4: MemoInput——乐观 pending 提前到 await 之前（藏掉 classify 延迟）+ query 命中撤掉 + 渲染答案气泡**

**为何这样改（advisor 实证）**：后端 classify 串在 `LLMService._lock` 上，最坏排在一条 4096-token L2 分析后面。当前 `MemoInput` 是 `await postNote` **之后**才 `addPendingNote`，于是每条普通备注都要等 classify 跑完才显示「处理中」——常见路径回归。修法：**乐观 pending 提前到 await 之前 + 提交不阻塞输入**（classify 延迟藏后台），await 返回若 `kind==="query"` 撤掉刚插的 note pending（它是查询不是备注），答案走 qp.answer 气泡。后端 A5 因此无须把 classify 挪后台（route 在 202 时仍需 kind 告诉前端 query/note）。

```typescript
import { CONN_ID } from "@/lib/connId"
// ...
const handleSubmit = () => {
  const trimmed = text.trim()
  if (!trimmed || recorder.recording) return
  const clientId = newClientId()
  const ts = Date.now() / 1000
  // 乐观 pending 先插（不 await）：提交即显「处理中」，不等后端 classify。category 占位，
  // note.processed 回灌时按 client_id 转成真类别（query 命中则下面撤掉）。
  addPendingNote({ client_id: clientId, kind: "text", ts, category: "note", content: trimmed, rawText: trimmed })
  setText("")
  onNoteAdded?.()
  postNote(trimmed, undefined, clientId, CONN_ID)
    .then((resp) => {
      if (resp.kind === "query") {
        // 实为查询：撤掉 note pending（复用 4.x 既有按 client_id 精确移除 action，见 noteFailed 邻近），
        // 答案靠 qp.answer 气泡回灌。
        useSessionStore.getState().<removePendingByClientId>(clientId)
      }
    })
    .catch(() => {
      useSessionStore.getState().noteFailed({ reason: "upload_failed", ts, client_id: clientId })
    })
}
```

渲染：从 `useSessionStore` 读 `qpAnswer`，在 MemoInput 容器上方显示一条临时气泡（几秒淡出 / 点击关闭），消失时调 `clearQpAnswer`。

> 实现注：(1) `<removePendingByClientId>` = store 现有按 client_id 精确移除 pending 的 action（4.x「精确移除卡处理中」已落，实现者在 `session.ts` 找准名字）。(2) `NoteCreateResponse` 放宽含可选 `kind?: "query"`（types/api.ts），免 as 断言。(3) 去掉对文本提交的 `sending` 锁（提交转后台、不阻塞输入）；`sending` 若仍被 mic 路径用则保留、只摘掉文本路径——对齐组件现有 sending/mic 交互，实现者按组件实情收敛。(4) query 命中到答案到达之间有数秒 QP 循环空窗，可选加一个轻量「查询中…」指示（UX polish，与气泡位置一起给 Lead 定）。

- [ ] **Step 5: 类型检查 + 前端测试**

Run: `cd frontend && pnpm typecheck && pnpm test`
Expected: 通过

- [ ] **Step 6: commit**

```bash
git add frontend/src/types/api.ts frontend/src/store/session.ts frontend/src/hooks/useLiveConnection.ts frontend/src/components/admin/MemoInput.tsx
git commit -m "feat(dispatcher-fe): 收 qp.answer 渲染答案气泡（块③ 前端 Task B2）"
```

---

### Task B3: 端到端手验（真模型）

- [x] **Step 1: 起后端（lazy 模型，预种库）** — 2026-06-06 手验：后端 :8000（GEMMA_MODEL_PATH=models/gemma-4-E4B-it-Q4_K_M.gguf，SOUNDSPEED_DB=data/soundspeed.db 仅 Scene_1/1 take，ASR/diar 关），前端 :5173。

```bash
ADMIN_TOKEN=devtoken SOUNDSPEED_DB=<seed.db> SOUNDSPEED_LIVE_ASR=0 SOUNDSPEED_DIARIZATION=0 \
PORT=8077 GEMMA_MODEL_PATH=<gguf> GEMMA_MMPROJ_PATH=<mmproj> uv run python -m backend.api
```

- [x] **Step 2: 前端连后端，memo 框打字验证** — 三条行为均通过（query 分流回灌气泡 / note 照旧乐观 pending / @keep 跳分类），Lead 肉眼确认。

- 打「第一场拍了多少条」→ 不插 note pending，几秒后气泡显示「第一场一共拍了 3 条。」（query 分支）。
- 打「这条过了」→ 照旧乐观 pending → note.processed 转实（note 分支）。
- 打「@keep 第三条好」→ 跳过分类直接 note。
Expected: 三条行为如上；后端日志见 memo_route 分类 + qp.answer 广播。

---

## Part C — 语音 spike（块①，调研，非 TDD）

> 交付物 = **一份结论**（写回 spec §4 + follow-up 计划），外加探针代码（不进生产）。先跑便宜的 binary-first probe。
>
> **Hygiene（advisor）**：spike 为验形态会临时改生产文件（`service.py` 的 `infer_voice_tool` tool_choice 形参、`config.py` 的 `memo_route_voice`、option-a 的 client.py handler 注入）。这些是**语音的活，不属文本 branch**。本 branch「文本先合」要干净——**C2/C3 的生产文件改动一律不 commit 进本 branch**（留工作区未提交 / 或开 throwaway 子分支跑），探针脚本放 `experiments/`（不进 git）。验通了的生产改动随**语音 follow-up branch** 正式提交。本 branch 只 commit C4 的结论回填（spec/memory 文档）。

### Task C1: 真语音 WAV 样本（硬前置）

- [ ] 录/取真语音 WAV：query 样本「第一场拍了多少条」「第七十二场在哪拍」；note 样本「这条过了」「收音有点小」「第三条留着」。各 2-3 条。取法：复用 Capture/enroll 后端现场麦录（16k mono PCM16 WAV），或请 Lead 录。放 `experiments/2026-06-06-voice-dispatch-spike/wav/`（experiments 不进 git）。

### Task C2: binary-first probe（便宜，先跑）

- [ ] **加 infer_voice_tool 的 tool_choice 覆盖**（spike 用，最小改）：`service.infer_voice_tool`（service.py:350）加可选 `tool_choice` 形参，透传到 `_submit`（已接受 tool_choice，:232-233）。镜像 `infer_tool`。
- [ ] **加 audio 二分 task_type**（spike 用）：config 加 `memo_route_voice`，`tools=[build_route_memo_tool()]`，forced route_memo。
- [ ] **探针脚本** `experiments/2026-06-06-voice-dispatch-spike/probe_binary.py`：对每条 WAV，组多模态 messages（text 上下文 + AUDIO_SENTINEL，仿 run_np_voice）→ `infer_voice_tool(task_type="memo_route_voice", audio=wav)` → 取 kind。统计 query/note 正确率。
- [ ] **跑 + 记结果**：正确率、失败模式。判据：forced audio 二分能否稳定分对 note/query。

### Task C3: option-a probe（贵，仅当 C2 通过且想要「省一跳」优雅版）

- [ ] **建 audio+tools handler 注入**（client.py，spec §4 form A 最硬的一层）：让带音频的请求也能渲染 FunctionGemma 工具声明（把工具声明注入多模态 chat format，而非走纯文本 native formatter）。
- [ ] **探针** `probe_option_a.py`：组合工具集（5 QP + structure_note = 6 工具），audio hop 上 `tool_choice="auto"`，看模型是否 auto 选对工具（query 样本选 QP 工具 / note 样本选 structure_note）。
- [ ] **跑 + 记结果**：6 工具 auto+audio 正确率 vs C2 的 binary-first。

### Task C4: 结论回填

- [ ] 把 C2/C3 实测（正确率、成本、可靠性）写回 `docs/specs/2026-06-06-np-qp-dispatcher.md` §4，给出 follow-up 语音 branch 的形态决策（option-a / binary-first / whisper 降级）。更新 memory `project_qp_tool_loop`。
- [ ] 探针代码留 `experiments/`（不进 git），生产改动（infer_voice_tool tool_choice / handler 注入）若转正随 follow-up branch 提交。

---

## Self-Review 检查（写计划者已核）

- **spec 覆盖**：块③ 文本调度器（A1 工具→A2 config→A3 分类器→A4 helper→A5 分流 + B 前端）✓；块① spike（C1 样本→C2 binary→C3 option-a→C4 结论）✓。INV2②/语音实现④ 明确拆 follow-up（不在本计划）✓。
- **类型/名字一致**：`ROUTE_TOOL_NAME`/`memo_route`/`classify_memo`/`run_qp_and_broadcast`/`conn_id`/`CONN_ID`/`QP_ANSWER` 跨任务统一（见共享契约）✓。
- **无占位**：后端步骤含完整代码；前端 store/气泡渲染给出改动点 + 默认方案（UX 位置实现期定）；spike 是调研步骤非 TDD（已标注）✓。
- **不破现有**：note 分支 :345-382 一字不动、只被 gate；post_query 重构后行为不变（A4 测旧 query 测试）；全量回归对齐 `776 passed`（A5 Step 5）✓。

---

## 执行交接

计划存 `docs/plans/2026-06-06-np-qp-dispatcher.md`。两种执行方式：

1. **Subagent-Driven（推荐）**——逐任务 fresh 子代理 + 两段 review（spec 合规 → 代码质量），快迭代。
2. **Inline Execution**——本 session 批量执行 + checkpoint。

A1→A5 有依赖链（A5 依赖 A2/A3/A4），顺序执行；B1→B2→B3 依赖 A；C 独立（可与 A/B 并）。
