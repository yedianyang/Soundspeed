# QP (Query Pipeline) Tool-Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 QP 查询管线落成仓库第一个 Tier 2 多工具 agentic 循环——Gemma 自选工具、看结果、必要时再查，最后合成自然语言回答；v1 走 `POST /api/v1/query` 直连入口，独立可 demo，不依赖 4.x。

**Architecture:** 内核 + 入口两层（本计划只做内核 v1）。内核 = 5 个工具（4 个策展工具 + 1 个 `query_database` 万能笔）+ 统一两步走循环（auto 跳抠工具名、forced 跳 grammar 出干净 JSON 参数，≤5 跳）+ 全只读 DB 访问（临时 `mode=ro` 连接，不碰共享 `self._conn`）+ 万能笔安全墙（authorizer/行数封顶/超时）。循环编排住在 `backend/pipelines/qp_query.py`，反复调 `service.infer`(auto)/`infer_tool`(forced)；service 只加一个按调用覆盖 `tool_choice` 的小钩子。

**Tech Stack:** Python 3.12、FastAPI、sqlite3（FTS5 + authorizer）、llama-cpp-python（Gemma 4 E4B GGUF，FunctionGemma 工具格式）、pytest / pytest-asyncio。

**Spec:** `docs/specs/2026-06-05-qp-tool-loop.md`（v0.2，已评审 + advisor review）。本计划逐节落地该 spec 的 §4–§12。

---

## 关键设计决定（spec 留给实现期具体化的点）

实现这些时按本节，不要自行另解：

1. **executor 签名具体化为 `executor(args: dict, dal: DAL) -> dict`。** spec §5.3 写的是 `executor(args)->dict`，但 §6 要 executor 拿 DAL。全局 registry 在 import 期注册，无法闭包绑 per-request/per-test 的 DAL。把 DAL **显式作第二参数传入**，registry 保持无状态、并发/测试安全。循环 step C 用 `registry.get_executor(name)(args, dal)`，并包在 `asyncio.to_thread` 里跑（见 Task 9），既不阻塞事件循环又匹配 spec「executor 在 to_thread worker 附近执行」的线程语义。

2. **所有 QP 读走临时 `mode=ro` 连接（D-QP-12），一律不碰共享 `self._conn`。** 包括场次目录、策展工具、万能笔。新增 `DAL._readonly_conn()` 临时连接 helper，所有 QP 读方法走它。

3. **scene 去重 bug（§7.2）本计划不碰写路径。** QP v1 只在读侧 `resolve_scene_id` 双向 `normalize_scene_code` 后精确匹配，**不改** `get_or_create_scene`（其写路径归属 2.x/3.x，待 Lead 定）。`normalize_scene_code` 作模块级纯函数加在 `dal.py`。

4. **`qp.answer.{conn_id}` 是广播 topic 串，不是定向路由。** 复用现有 `ConnectionManager.broadcast(topic, payload)`（广播给全部连接），客户端按 `qp.answer.<自己的 conn_id>` 前缀过滤——对齐现有「连上即收全部 topic」语义。route 直接调 `request.app.state.connection_manager.broadcast(...)`，不经 orchestrator.publish。payload 用 frozen dataclass（broadcast 内部 `asdict`）。

5. **路由注册在 `create_app`（`backend/api/app.py`）**，紧挨 `takes_router`/`ws_router`——它要 `app.state.orchestrator.dal` + `connection_manager` + `llm_service`，TestClient 直接可验。

6. **【已被 Task 7.5 probe 证伪 → 已解】多跳 tool-response 回喂渲染。** ✅实测（commit `84428fa`）：OpenAI 风格 `tool_calls`/`role=tool` 多跳回喂**撞 Jinja `UndefinedError: 'raise_exception' is undefined`**（状态相关、不稳），FunctionGemma `<|tool_response>` 标记格式可工作但不如纯文本稳。**定案：Task 9 step C 用纯文本回喂**——`assistant`=auto 步原始 content（模型自吐 `<|tool_call>`）+ `user`=`f"工具 {name} 返回：{json}"`，实测 3 次确定性稳定渲染 + 自然语言收尾。单测用 StubClient 不受此影响（断言改 role=user「工具…返回…」）。

7. **【已被 Task 7.5 probe 证实】auto 跳返回 content 字符串而非结构化 tool_calls。** ✅实测：auto 跳返回 `finish_reason=stop` + content 串 `<|tool_call>call:NAME{...}`（`tool_calls=None`），`service.infer` 正常返回、不撞护栏，正则抠名成功。**故 Task 8 只需加 `tool_choice` 覆盖（不需要 `infer_message` 变体），Task 9 step A 原样、forced 第二跳保留**（分诊 A 未触发）。

8. **config.py 照 eager 写法加（D-QP-09）——原「合并 4.x 须重贴 lazy」的硬提醒已作废。** 本分支已 rebase 到含 4.x 的 main，实测：4.x **只**把 `note_struct` 一条抽成 `_build_note_task_config()` lazy（因 `build_note_tool` 运行期 import `np_note` 取 enum 会成环）；`query_session`/`l2_take` 仍是 eager dict 字面量。QP 的 `transcript.py` 只 import 中性叶子（DAL 仅 `TYPE_CHECKING`），import-neutral，所以 `query_session` 直接 eager 挂 `build_qp_tools()` 即可，**无须重贴到任何 lazy builder**。memory `project_qp_tool_loop` 那条「合并 4.x 必重贴」提醒同步作废（已更新）。

---

## File Structure

**新建：**

| 文件 | 责任 |
|------|------|
| `backend/llm/tools/transcript.py` | 5 个 `build_*_tool()` schema（FC spec §3.3 预留的 QP 工具家）+ `build_qp_tools()` 返回 list + 5 个 `*_executor(args, dal)` executor。schema 与 executor 同模块共置；只 import 中性叶子（DAL 仅 `TYPE_CHECKING`），避开 `config→tools→pipeline→config` 循环。 |
| `backend/pipelines/qp_query.py` | `_scrape_tool_name`、`_build_scene_catalog`、`_build_qp_system_prompt`、`run_tool_loop`、`run_qp_query`。循环编排内聚于此。 |
| `backend/api/routes/query.py` | `POST /api/v1/query`（请求体带 `conn_id`）直连入口，跑 `run_qp_query` → 广播 `qp.answer.{conn_id}`。 |
| `backend/tests/test_qp_dal_readonly.py` | DAL 只读 foundation + 策展读方法 + 万能笔安全墙单测（Task 1/2/3）。 |
| `backend/tests/test_qp_tools.py` | 工具 schema L0 + executor L1 单测（Task 4/5）。 |
| `backend/tests/test_qp_registry.py` | registry 注册 QP 工具 + executor 单测（Task 6）。 |
| `backend/tests/test_qp_config.py` | `query_session` 挂 tools + `tool_choice="auto"` 单测（Task 7）。 |
| `backend/tests/test_qp_probe.py` | 真模型 probe（`@pytest.mark.smoke`，Task 7.5）：建循环前钉死假设 6/7。 |
| `backend/tests/test_qp_loop.py` | 两步走循环 L2 单测（StubService，Task 9）。 |
| `backend/tests/test_qp_route.py` | `POST /query` route 单测（TestClient + StubService，Task 10）。 |
| `backend/tests/test_qp_smoke.py` | L3 真模型 spike（`@pytest.mark.smoke`，Task 11）。 |

**修改：**

| 文件 | 改动 |
|------|------|
| `backend/db/dal.py` | 存 `self._db_path`；加 `_readonly_conn()`、模块级 `normalize_scene_code`、`list_scenes_readonly`、`resolve_scene_id`、`count_takes`、`get_scene_info`、`list_characters`、`search_script_lines`、`query_readonly`（含 authorizer/封顶/超时常量）。 |
| `backend/llm/tools/registry.py` | `_bootstrap()` 追加注册 5 个 QP 工具，传真实 executor。 |
| `backend/llm/config.py` | `query_session` 加 `tools=build_qp_tools()` + `tool_choice="auto"`。 |
| `backend/llm/service.py` | `infer`/`infer_tool`/`_submit` 加可选 `tool_choice` 覆盖参数，并进 gen_kwargs。 |
| `backend/core/events.py` | 加 `QP_ANSWER = "qp.answer"` topic 常量 + `QpAnswerPayload` frozen dataclass。 |
| `backend/api/app.py` | `include_router(query_router)`。 |

**基线先跑（每个执行 session 第一步，CLAUDE.md 约定）：**

```bash
uv run pytest backend/tests/ -q
```
Expected: 全绿（记录基线条数）。任一红的先停下查清，不要在红基线上开工。

---

## Task 1: DAL 只读 foundation（`_db_path` + `_readonly_conn` + `normalize_scene_code` + `list_scenes_readonly` + `resolve_scene_id`）

**Files:**
- Modify: `backend/db/dal.py`（`__init__` 约 219-231 行；新增模块级函数 + 方法）
- Test: `backend/tests/test_qp_dal_readonly.py`（新建）

- [ ] **Step 1: 写失败测试**

新建 `backend/tests/test_qp_dal_readonly.py`：

```python
"""QP 只读路径单测：normalize_scene_code / resolve_scene_id / _readonly_conn。

所有 QP 读走临时 mode=ro 连接（D-QP-12），不碰共享 self._conn。
"""
from __future__ import annotations

import pytest

from backend.db.dal import DAL, normalize_scene_code


@pytest.fixture
def dal(tmp_path) -> DAL:
    d = DAL(tmp_path / "qp.db")
    yield d
    d.close()


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Scene_3A", "3A"),
        ("scene 3a", "3A"),
        ("Scene3", "3"),       # 前缀后无分隔符、紧跟数字
        ("Sc_72", "72"),
        ("S72", "72"),
        ("场3", "3"),
        ("  72 ", "72"),
        ("3", "3"),
        ("sce3", "SCE3"),      # sc 不啃 sce（后跟 'e' 非数字，不剥）
        ("", ""),
    ],
)
def test_normalize_scene_code(raw: str, expected: str) -> None:
    assert normalize_scene_code(raw) == expected


def test_resolve_scene_id_matches_via_normalize(dal: DAL) -> None:
    sid = dal.create_scene("Scene_72")
    # 口语变体都能对到同一 scene_id
    assert dal.resolve_scene_id("72") == sid
    assert dal.resolve_scene_id("S72") == sid
    assert dal.resolve_scene_id("scene 72") == sid


def test_resolve_scene_id_symmetric_normalize(dal: DAL) -> None:
    # 库里存无前缀，带前缀变体也能查回（双向 normalize 对称）
    sid = dal.create_scene("72")
    assert dal.resolve_scene_id("S72") == sid
    assert dal.resolve_scene_id("Scene_72") == sid


def test_resolve_scene_id_missing_returns_none(dal: DAL) -> None:
    dal.create_scene("Scene_1")
    # 不同数字 = 不同场，不模糊替换（spec §7.5）
    assert dal.resolve_scene_id("2") is None
    assert dal.resolve_scene_id("") is None


def test_readonly_conn_blocks_writes(dal: DAL) -> None:
    import sqlite3

    dal.create_scene("Scene_1")
    with dal._readonly_conn() as conn:
        # 读没问题
        rows = conn.execute("SELECT scene_code FROM scenes;").fetchall()
        assert rows[0]["scene_code"] == "Scene_1"
        # 写被 mode=ro 拦死
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("INSERT INTO scenes (scene_code) VALUES ('x');")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest backend/tests/test_qp_dal_readonly.py -q`
Expected: FAIL —— `ImportError: cannot import name 'normalize_scene_code'` / `AttributeError: '_readonly_conn'`。

- [ ] **Step 3: 实现**

在 `backend/db/dal.py` 顶部 import 区（已有 `import sqlite3`、`from contextlib import contextmanager`、`from typing import Iterator`）下方、`class DAL` 之前，加模块级函数与常量：

```python
import re

# QP 场次编号归一：剥 Scene/场/Sc/S 前缀 + 分隔符，保留数字+后缀，统一大写。
# (?=\d) 前瞻——前缀只在其后（跨可选分隔符）紧跟数字时才剥，避免 `sc` 误啃 `sce3`→`E3`。
_SCENE_PREFIX_RE = re.compile(r"^(?:scene|场|sc|s)[\s_\-]*(?=\d)", re.IGNORECASE)


def normalize_scene_code(raw: str) -> str:
    """归一场次编号用于读侧匹配（spec §7.2）。

    trim → 剥前缀（Scene/场/Sc/S + 分隔符，仅当其后紧跟数字）→ 大写。
    例：'Scene_3A'→'3A'，'Scene3'→'3'，'s72'→'72'，'场3'→'3'，'3'→'3'，''→''。
    读、写两侧都过一遍再精确比对，覆盖「同号不同前缀」的常见变体；
    前缀后不跟数字的（如 'sce3'→'SCE3'）原样大写返回，不误剥、不破坏特殊编号。
    """
    s = raw.strip()
    if not s:
        return ""
    s = _SCENE_PREFIX_RE.sub("", s)
    return s.strip().upper()
```

在 `DAL.__init__` 里存 db_path（紧跟 `apply_migrations(db_path)` 之后、`self._conn = ...` 之前）：

```python
        self._db_path = db_path  # QP 只读连接用（_readonly_conn），不复用共享 self._conn
```

在 `_write_tx` 之后（资源管理区附近）加只读连接 helper + 三个 QP 读方法：

```python
    # ── QP 只读路径（D-QP-12：临时 mode=ro 连接，不碰共享 self._conn）─────────────

    @contextmanager
    def _readonly_conn(self) -> Iterator[sqlite3.Connection]:
        """每次开一个临时 mode=ro 连接，用完 finally close。

        executor 在事件循环外（to_thread worker）跑，复用共享 self._conn 是跨线程
        并发隐患（D-QP-12）。所有 QP 读（场次目录/策展工具/万能笔）一律走本 helper。
        """
        conn = sqlite3.connect(f"file:{self._db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        # 只设 busy_timeout。故意不调 _configure_connection / 不设 PRAGMA journal_mode=WAL——
        # 在 mode=ro 连接上执行 WAL 切换会抛 OperationalError: attempt to write a readonly
        # database（WAL 要写 -wal/-shm）。ro 连接读已落盘 WAL 内容无需切 journal_mode。
        conn.execute("PRAGMA busy_timeout = 5000;")
        try:
            yield conn
        finally:
            conn.close()

    def list_scenes_readonly(self) -> list[dict]:
        """QP 场次目录用：按创建序返回全部场次（只读连接）。

        只返 QP 场次目录所需最小列集合（scene_id/scene_code/location/int_ext/
        time_of_day/shoot_date），不是 list_scenes 的全列只读版。
        """
        with self._readonly_conn() as conn:
            rows = conn.execute(
                "SELECT scene_id, scene_code, location, int_ext, time_of_day, shoot_date "
                "FROM scenes ORDER BY created_at, scene_id;"
            ).fetchall()
        return [dict(r) for r in rows]

    def resolve_scene_id(self, scene_ref: str) -> int | None:
        """口语/变体场次引用 → 真实 scene_id：两侧 normalize 后精确匹配。

        找不到返回 None（调用方据此老实说没有，禁止模糊替换，spec §7.3）。
        数字不同 = 不同场，不跨号匹配（spec §7.5）。
        """
        target = normalize_scene_code(scene_ref)
        if not target:
            return None
        # 全表扫：场次量级 O(10^1)，不加 index 可接受，别误当遗漏优化去改。
        for s in self.list_scenes_readonly():
            if normalize_scene_code(s["scene_code"]) == target:
                return int(s["scene_id"])
        return None
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest backend/tests/test_qp_dal_readonly.py -q`
Expected: PASS（全部 normalize 参数化 + resolve + readonly 用例）。

> WAL + mode=ro 提醒：若 `_readonly_conn` 读报 `sqlite3.OperationalError: unable to open database file`，是 WAL 共享内存问题——`mode=ro` 连 WAL 库需 `-shm`/`-wal` 可访问。同进程里 `self._conn` 一直开着应已规避；万一撞上，别在 foundation 任务上耗时间猜这个 opaque 错，先认它是 WAL+ro 的已知坑。

- [ ] **Step 5: Commit**

```bash
git add backend/db/dal.py backend/tests/test_qp_dal_readonly.py
git commit -m "feat(qp): DAL 只读 foundation（_readonly_conn + normalize/resolve scene）"
```

---

## Task 2: DAL 策展读方法（`count_takes` / `get_scene_info` / `list_characters` / `search_script_lines`）

**Files:**
- Modify: `backend/db/dal.py`（QP 只读区追加 4 个方法）
- Test: `backend/tests/test_qp_dal_readonly.py`（追加）

- [ ] **Step 1: 写失败测试**

在 `backend/tests/test_qp_dal_readonly.py` 末尾追加：

```python
def _seed_scene_with_script(dal: DAL) -> int:
    """建一个带剧本（2 角色 + 1 舞台指示）的场次，返回 scene_id。"""
    sid = dal.get_or_create_scene(
        "Scene_5",
        int_ext="室内",
        time_of_day="日",
        location="咖啡馆",
    )[0]
    script_id = dal.insert_script(sid, "raw")
    dal.insert_script_line(script_id, 1, "李雷", "你好，韩梅梅。")
    dal.insert_script_line(script_id, 2, "韩梅梅", "好久不见。")
    dal.insert_script_line(script_id, 3, "李雷", "最近怎么样？")
    dal.insert_script_line(script_id, 4, None, "（两人握手）")  # 舞台指示，character=NULL
    return sid


def test_count_takes_filters_soft_deleted(dal: DAL) -> None:
    sid = dal.create_scene("Scene_5")
    t1, _ = dal.start_take(sid, "", 1000.0)
    dal.start_take(sid, "", 1001.0)
    dal.delete_take(t1)  # 软删
    assert dal.count_takes(sid) == 1  # 软删的不计


def test_count_takes_status_filter(dal: DAL) -> None:
    sid = dal.create_scene("Scene_5")
    t1, _ = dal.start_take(sid, "", 1000.0)
    dal.start_take(sid, "", 1001.0)
    dal.set_take_status(t1, "keep")  # v9 正名：keeper→keep, hold→pass（schema CHECK pass/ng/keep/tbd）
    assert dal.count_takes(sid, status="keep") == 1
    assert dal.count_takes(sid, status="tbd") == 1


def test_get_scene_info(dal: DAL) -> None:
    sid = _seed_scene_with_script(dal)
    info = dal.get_scene_info(sid)
    assert info["scene_code"] == "Scene_5"
    assert info["location"] == "咖啡馆"
    assert info["int_ext"] == "室内"
    assert info["time_of_day"] == "日"
    assert info["character_count"] == 2  # 李雷/韩梅梅，舞台指示 NULL 不计


def test_get_scene_info_missing(dal: DAL) -> None:
    assert dal.get_scene_info(99999) is None


def test_list_characters_dedup_excludes_stage_dirs(dal: DAL) -> None:
    sid = _seed_scene_with_script(dal)
    chars = dal.list_characters(sid)
    assert sorted(chars) == ["李雷", "韩梅梅"]  # 去重 + 舞台指示(NULL) 不出现


def test_search_script_lines_fts(dal: DAL) -> None:
    sid = _seed_scene_with_script(dal)
    hits = dal.search_script_lines("好久不见", scene_id=sid)
    assert any("好久不见" in h["text"] for h in hits)
    assert all({"line_no", "character", "text"} <= set(h) for h in hits)


def test_scene_info_and_characters_use_latest_version(dal: DAL) -> None:
    # 多版本剧本：只反映最新版（v2），不跨版本 union（3.x 重导入场景）
    sid = dal.create_scene("Scene_9")
    s1 = dal.insert_script(sid, "v1")  # version 自动 = 1
    dal.insert_script_line(s1, 1, "甲", "旧台词")
    dal.insert_script_line(s1, 2, "乙", "旧台词2")
    s2 = dal.insert_script(sid, "v2")  # version 自动 = 2（最新）
    dal.insert_script_line(s2, 1, "甲", "新台词")
    dal.insert_script_line(s2, 2, "丙", "新台词2")
    assert dal.get_scene_info(sid)["character_count"] == 2  # 甲/丙，不是 union 的 3
    assert sorted(dal.list_characters(sid)) == ["丙", "甲"]


def test_count_takes_status_no_match_returns_zero(dal: DAL) -> None:
    sid = dal.create_scene("Scene_9")
    dal.start_take(sid, "", 1000.0)
    assert dal.count_takes(sid, status="ng") == 0  # 无匹配 status 返回 0


def test_search_script_lines_no_scene_filter(dal: DAL) -> None:
    _seed_scene_with_script(dal)
    hits = dal.search_script_lines("好久不见")  # 不带 scene_id：全剧本检索分支
    assert any("好久不见" in h["text"] for h in hits)


def test_list_characters_empty_when_no_script(dal: DAL) -> None:
    sid = dal.create_scene("Scene_9")  # 场次存在但无剧本
    assert dal.list_characters(sid) == []
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest backend/tests/test_qp_dal_readonly.py -q -k "count_takes or scene_info or characters or search_script"`
Expected: FAIL —— `AttributeError: 'DAL' object has no attribute 'count_takes'` 等。

- [ ] **Step 3: 实现**

在 `backend/db/dal.py` 的 QP 只读区（Task 1 新增方法之后）追加：

```python
    def count_takes(self, scene_id: int, status: str | None = None) -> int:
        """统计某场次的有效 take 数（软删过滤；可选 status 过滤）。"""
        sql = "SELECT COUNT(*) AS n FROM takes WHERE scene_id = ? AND deleted_at IS NULL"
        params: tuple = (scene_id,)
        if status is not None:
            sql += " AND status = ?"
            params = (scene_id, status)
        with self._readonly_conn() as conn:
            row = conn.execute(sql + ";", params).fetchone()
        return int(row["n"])

    def get_scene_info(self, scene_id: int) -> dict | None:
        """返回场次信息 + 最新剧本的角色数。无此场次返回 None。

        character_count 只统计**最新版本**剧本（对齐 get_latest_script），
        不跨版本 union——3.x 重导入会有多版本，union 对「这场几个角色」无意义。
        """
        with self._readonly_conn() as conn:
            row = conn.execute(
                "SELECT scene_id, scene_code, location, int_ext, time_of_day, shoot_date "
                "FROM scenes WHERE scene_id = ?;",
                (scene_id,),
            ).fetchone()
            if row is None:
                return None
            char_row = conn.execute(
                "SELECT COUNT(DISTINCT sl.character) AS n "
                "FROM script_lines sl "
                "WHERE sl.script_id = ("
                "  SELECT script_id FROM scripts WHERE scene_id = ? ORDER BY version DESC LIMIT 1"
                ") AND sl.character IS NOT NULL;",
                (scene_id,),
            ).fetchone()
        info = dict(row)
        info["character_count"] = int(char_row["n"])
        return info

    def list_characters(self, scene_id: int) -> list[str]:
        """返回场次**最新版本**剧本里去重后的角色清单（舞台指示 character=NULL 不计）。

        场次不存在 / 无剧本时返回 []（缺场由 executor 的 resolve_scene_id 先把关，Task 5）。
        只取最新版本，不跨版本 union（同 get_scene_info）。
        """
        with self._readonly_conn() as conn:
            rows = conn.execute(
                "SELECT DISTINCT sl.character AS c "
                "FROM script_lines sl "
                "WHERE sl.script_id = ("
                "  SELECT script_id FROM scripts WHERE scene_id = ? ORDER BY version DESC LIMIT 1"
                ") AND sl.character IS NOT NULL "
                "ORDER BY sl.character;",
                (scene_id,),
            ).fetchall()
        return [r["c"] for r in rows]

    def search_script_lines(self, query: str, scene_id: int | None = None) -> list[dict]:
        """FTS5 MATCH 检索台词（BM25 排序，只读连接）。返回 line_no/character/text dict 列表。"""
        base = (
            "SELECT sl.line_no AS line_no, sl.character AS character, sl.text AS text "
            "FROM script_lines_fts fts "
            "JOIN script_lines sl ON sl.line_id = fts.rowid "
        )
        with self._readonly_conn() as conn:
            if scene_id is not None:
                rows = conn.execute(
                    base
                    + "JOIN scripts s ON s.script_id = sl.script_id "
                    "WHERE fts.text MATCH ? AND s.scene_id = ? "
                    "ORDER BY bm25(script_lines_fts);",
                    (query, scene_id),
                ).fetchall()
            else:
                rows = conn.execute(
                    base + "WHERE fts.text MATCH ? ORDER BY bm25(script_lines_fts);",
                    (query,),
                ).fetchall()
        return [dict(r) for r in rows]
```

> 注：`set_take_status(take_id, status)` 已确认存在于 `backend/db/dal.py:1002`（Mark 用），测试直接调即可，不要新增写方法。

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest backend/tests/test_qp_dal_readonly.py -q`
Expected: PASS（Task 1 + Task 2 全部用例）。

- [ ] **Step 5: Commit**

```bash
git add backend/db/dal.py backend/tests/test_qp_dal_readonly.py
git commit -m "feat(qp): DAL 策展读方法（count_takes/get_scene_info/list_characters/search_script_lines）"
```

---

## Task 3: DAL `query_readonly` 万能笔安全墙（D-QP-04）

**Files:**
- Modify: `backend/db/dal.py`（加 authorizer 常量 + `query_readonly`）
- Test: `backend/tests/test_qp_dal_readonly.py`（追加安全墙专测）

- [ ] **Step 1: 写失败测试**

在 `backend/tests/test_qp_dal_readonly.py` 末尾追加：

```python
def test_query_readonly_allows_select(dal: DAL) -> None:
    dal.create_scene("Scene_1")
    res = dal.query_readonly("SELECT scene_code FROM scenes;")
    assert res["row_count"] == 1
    assert res["rows"][0]["scene_code"] == "Scene_1"
    assert res["truncated"] is False


def test_query_readonly_allows_cte(dal: DAL) -> None:
    dal.create_scene("Scene_1")
    res = dal.query_readonly(
        "WITH x AS (SELECT scene_code FROM scenes) SELECT * FROM x;"
    )
    assert "error" not in res
    assert res["row_count"] == 1


def test_query_readonly_blocks_write(dal: DAL) -> None:
    res = dal.query_readonly("INSERT INTO scenes (scene_code) VALUES ('x');")
    assert "error" in res


def test_query_readonly_blocks_attach(dal: DAL) -> None:
    # mode=ro 拦不住 ATTACH，必须靠 authorizer（spec §6.2 ✅实测）
    res = dal.query_readonly("ATTACH DATABASE ':memory:' AS evil;")
    assert "error" in res


def test_query_readonly_blocks_pragma(dal: DAL) -> None:
    # 任意 PRAGMA（非 data_version）仍被 DENY——证明 PRAGMA 是 scoped 不是全开
    res = dal.query_readonly("PRAGMA table_info(scenes);")
    assert "error" in res


def test_query_readonly_allows_pragma_data_version(dal: DAL) -> None:
    # MATCH 内部需要 data_version，scoped 放行（§6.2 实证修正）
    res = dal.query_readonly("PRAGMA data_version;")
    assert "error" not in res


def test_query_readonly_blocks_multi_statement(dal: DAL) -> None:
    res = dal.query_readonly("SELECT 1; SELECT 2;")
    assert "error" in res  # 单游标只执行一条，多句 raise Warning


def test_query_readonly_allows_fts_match(dal: DAL) -> None:
    # FTS MATCH 可用——authorizer 放行所有表 READ（含影子表）+ scoped data_version。
    # 影子表按设计可读（§6.2 实证修正：无法按表名区分 MATCH 内部读 vs 直接读，且影子读无安全价值）。
    _seed_scene_with_script(dal)
    res = dal.query_readonly(
        "SELECT text FROM script_lines_fts WHERE text MATCH '好久不见';"
    )
    assert "error" not in res
    assert res["row_count"] >= 1


def test_query_readonly_truncates_rows(dal: DAL) -> None:
    sid = dal.create_scene("Scene_1")
    for i in range(5):
        dal.start_take(sid, "", 1000.0 + i)
    res = dal.query_readonly("SELECT take_id FROM takes;", max_rows=3)
    assert res["row_count"] == 3
    assert res["truncated"] is True


def test_query_readonly_blocks_load_extension(dal: DAL) -> None:
    # RCE 向量：authorizer 层独立 DENY load_extension（纵深防御，锁定行为）
    res = dal.query_readonly("SELECT load_extension('/tmp/evil.so');")
    assert "error" in res


def test_query_readonly_timeout(dal: DAL) -> None:
    # DoS 防线：progress_handler 超时中断（无限 RECURSIVE + 小 timeout）
    res = dal.query_readonly(
        "WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM c) "
        "SELECT count(*) FROM c;",
        timeout_s=0.1,
    )
    assert "error" in res
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest backend/tests/test_qp_dal_readonly.py -q -k query_readonly`
Expected: FAIL —— `AttributeError: 'DAL' object has no attribute 'query_readonly'`。

- [ ] **Step 3: 实现**

在 `backend/db/dal.py` 模块级常量区（`normalize_scene_code` 附近）加：

```python
# 万能笔 authorizer：只放行 SELECT 系动作，其余 DENY（挡 ATTACH/写/临时表/任意 PRAGMA）。
# SQLITE_READ 放行所有表（含 FTS 影子表）——见下方 authorizer 注释与 spec §6.2 实证决策。
_QP_ALLOWED_ACTIONS = frozenset(
    {
        sqlite3.SQLITE_SELECT,
        sqlite3.SQLITE_READ,
        sqlite3.SQLITE_FUNCTION,
        sqlite3.SQLITE_RECURSIVE,
    }
)
```

> **§6.2 实证修正（Task 3 实现期发现）**：原 spec/计划想「authorizer 按表名挡 FTS 影子表、但放行 `script_lines_fts` 让万能笔可 MATCH」。✅实测 SQLite 3.53.1：FTS5 MATCH **内部**会读 `script_lines_fts_config`/`_idx` 影子表 + 触发 `PRAGMA data_version`，这些 `SQLITE_READ` 与「直接读影子表」是**同类事件、无法按表名区分**——按表名挡影子表会同时挡死 MATCH。决策（Lead 拍板，可逆）：**放行所有表的 READ（含影子表）+ 只 scoped 放行 `PRAGMA data_version`**。安全不变量不受影响（无写 / 无 ATTACH / 无任意 PRAGMA / 不跨库；影子表是 `script_lines` 派生的索引 blob，漏不出额外信息）。挡影子表原是「防模型查到 garbage」的可用性护栏，安全上无价值且 env-fragile，去掉。`_QP_FTS_SHADOW_TABLES` 常量随之删除。

在 QP 只读区（Task 2 方法之后）加 `query_readonly`：

```python
    def query_readonly(
        self,
        sql: str,
        params: tuple = (),
        *,
        max_rows: int = 300,
        timeout_s: float = 3.0,
    ) -> dict:
        """万能笔：执行模型现写的只读 SQL（spec §6 安全墙）。

        临时只读连接 + authorizer 只放行 SELECT 系 action + scoped PRAGMA（仅 data_version）
        + 单句守卫 + 行数封顶（fetchmany max_rows+1 截断）+ progress_handler 计算超时。
        错误不抛穿：包成 {"error": ...} 让循环下一跳自纠。
        成功返回 {"columns": [...], "rows": [...], "row_count": n, "truncated": bool}。
        """
        import time

        def _authorizer(
            action: int, arg1: object, arg2: object, db_name: object, trigger: object
        ) -> int:
            # PRAGMA 只放行 MATCH 内部需要的 data_version；其余 PRAGMA（table_info/
            # writable_schema…）一律 DENY。必须先于 allowed-actions 判断（SQLITE_PRAGMA
            # 不在 _QP_ALLOWED_ACTIONS 里，靠这个分支单独 scoped 放行）。
            if action == sqlite3.SQLITE_PRAGMA:
                return sqlite3.SQLITE_OK if arg1 == "data_version" else sqlite3.SQLITE_DENY
            # FUNCTION 整体放行（bm25 等 FTS/标量函数需要），但独立堵死 load_extension——
            # 它是 RCE 向量；即便当前 _readonly_conn 未 enable_load_extension，也在
            # authorizer 层堵死，防未来连接配置变动打开缺口（纵深防御）。
            if action == sqlite3.SQLITE_FUNCTION:
                return sqlite3.SQLITE_DENY if arg2 == "load_extension" else sqlite3.SQLITE_OK
            # SELECT/READ/RECURSIVE 放行（READ 含所有表，FTS 影子表按设计可读，
            # 见 §6.2 实证修正）。其余（写/ATTACH/临时表…）DENY——真正的安全边界。
            # accepted risk：zeroblob/randomblob 单次大分配的内存 DoS 不堵（单用户 demo、有界）。
            if action in _QP_ALLOWED_ACTIONS:
                return sqlite3.SQLITE_OK
            return sqlite3.SQLITE_DENY

        with self._readonly_conn() as conn:
            deadline = time.monotonic() + timeout_s
            conn.set_authorizer(_authorizer)
            conn.set_progress_handler(
                lambda: 1 if time.monotonic() > deadline else 0, 1000
            )
            try:
                cur = conn.execute(sql, params)
                fetched = cur.fetchmany(max_rows + 1)
                truncated = len(fetched) > max_rows
                fetched = fetched[:max_rows]
                columns = [d[0] for d in cur.description] if cur.description else []
                return {
                    "columns": columns,
                    "rows": [dict(r) for r in fetched],
                    "row_count": len(fetched),
                    "truncated": truncated,
                }
            except sqlite3.Warning as exc:
                return {"error": f"只能执行单条 SQL 语句：{exc}"}
            except sqlite3.OperationalError as exc:
                return {"error": f"查询失败或超时：{exc}"}
            except sqlite3.DatabaseError as exc:
                return {"error": f"数据库拒绝该查询（仅允许只读 SELECT）：{exc}"}
            finally:
                conn.set_authorizer(None)
                conn.set_progress_handler(None, 1000)
```

> except 顺序要紧：`sqlite3.Warning`（多句）独立于 `DatabaseError`；`OperationalError`（超时 interrupted）是 `DatabaseError` 子类，须排在 `DatabaseError` 前；authorizer DENY 抛的 `DatabaseError: not authorized` 落最后一条。

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest backend/tests/test_qp_dal_readonly.py -q`
Expected: PASS（含 9 条安全墙断言：select/cte/写/attach/pragma/多句/影子表/fts-match/截断）。

> FTS authorizer（已实证定案，Task 3 实现期）：原预判「authorizer 只对 SQL 显式引用的表触发、不对 FTS 内部游标读触发」**被证伪**——SQLite 3.53.1 下 MATCH 内部确实读 `_config`/`_idx` 影子表（`SQLITE_READ`）+ 触发 `PRAGMA data_version`，与直接读影子表无法区分。故按表名挡影子表会挡死 MATCH。**定案**：authorizer 放行所有表 READ（含影子表）+ scoped 放行 `PRAGMA data_version`，靠 action 级（只 SELECT 系）+ mode=ro + 单句 + 封顶 + 超时守住边界（见上方 §6.2 实证修正块）。不改 schema。

- [ ] **Step 5: Commit**

```bash
git add backend/db/dal.py backend/tests/test_qp_dal_readonly.py
git commit -m "feat(qp): query_readonly 万能笔安全墙（authorizer/封顶/超时）"
```

---

## Task 4: QP 工具 schema（`build_*_tool` + `build_qp_tools`）

**Files:**
- Create: `backend/llm/tools/transcript.py`
- Test: `backend/tests/test_qp_tools.py`（新建）

- [ ] **Step 1: 写失败测试**

新建 `backend/tests/test_qp_tools.py`：

```python
"""QP 工具 schema（L0）+ executor（L1）单测。"""
from __future__ import annotations

import pytest

from backend.llm.tools.transcript import (
    build_count_takes_tool,
    build_get_scene_info_tool,
    build_list_characters_tool,
    build_qp_tools,
    build_query_database_tool,
    build_search_script_lines_tool,
)

_BUILDERS = [
    build_count_takes_tool,
    build_get_scene_info_tool,
    build_list_characters_tool,
    build_search_script_lines_tool,
    build_query_database_tool,
]


def _is_flat_scalar(prop: dict) -> bool:
    return prop.get("type") in {"string", "integer", "boolean", "number"}


@pytest.mark.parametrize("builder", _BUILDERS)
def test_tool_is_openai_style(builder) -> None:
    schema = builder()
    assert schema["type"] == "function"
    fn = schema["function"]
    assert isinstance(fn["name"], str) and fn["name"]
    assert isinstance(fn["description"], str) and fn["description"]
    assert fn["parameters"]["type"] == "object"


@pytest.mark.parametrize("builder", _BUILDERS)
def test_tool_params_all_flat_scalar(builder) -> None:
    # spec §4：所有参数必须扁平标量，不许嵌套数组/对象（auto 跳解析对嵌套截断崩溃）
    props = builder()["function"]["parameters"]["properties"]
    assert props, "工具至少要有一个参数"
    for name, prop in props.items():
        assert _is_flat_scalar(prop), f"参数 {name} 非扁平标量: {prop}"


def test_build_qp_tools_returns_five_named() -> None:
    tools = build_qp_tools()
    names = [t["function"]["name"] for t in tools]
    assert names == [
        "count_takes",
        "get_scene_info",
        "list_characters",
        "search_script_lines",
        "query_database",
    ]


def test_query_database_has_single_sql_param() -> None:
    props = build_query_database_tool()["function"]["parameters"]["properties"]
    assert list(props) == ["sql"]
    assert props["sql"]["type"] == "string"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest backend/tests/test_qp_tools.py -q`
Expected: FAIL —— `ModuleNotFoundError: No module named 'backend.llm.tools.transcript'`。

- [ ] **Step 3: 实现**

新建 `backend/llm/tools/transcript.py`（本步只写 schema 部分，executor 在 Task 5 加进同文件）：

```python
"""QP 工具家（FC spec §3.3 预留）：5 个 build_*_tool() schema + build_qp_tools()。

executor（Task 5）也住本模块，与 schema 共置。本模块只 import 中性叶子，
DAL 仅 TYPE_CHECKING——避开 config→tools→pipeline→config 循环（D-QP-09）。
所有参数扁平标量（spec §4）：auto 跳的 FunctionGemma 字符串解析对扁平标量稳，
对嵌套数组截断崩溃。
"""
from __future__ import annotations


def build_count_takes_tool() -> dict:
    return {
        "type": "function",
        "function": {
            "name": "count_takes",
            "description": "统计某场次已拍摄的 take 条数（已排除软删，可选按状态过滤）。问「第N场拍了多少条」用它。",
            "parameters": {
                "type": "object",
                "properties": {
                    "scene_ref": {
                        "type": "string",
                        "description": "场次引用，如 '第3场' / '3' / 'Scene_3'",
                    },
                    "status": {
                        "type": "string",
                        "enum": ["keep", "ng", "pass", "tbd"],
                        "description": "可选状态过滤：keep/ng/pass/tbd，不填则统计全部",
                    },
                },
                "required": ["scene_ref"],
            },
        },
    }


def build_get_scene_info_tool() -> dict:
    return {
        "type": "function",
        "function": {
            "name": "get_scene_info",
            "description": "返回某场次的地点/内外景/时间/拍摄日期/角色数。问「第N场在哪拍」用它。",
            "parameters": {
                "type": "object",
                "properties": {
                    "scene_ref": {
                        "type": "string",
                        "description": "场次引用，如 '第72场' / '72' / 'Scene_72'",
                    },
                },
                "required": ["scene_ref"],
            },
        },
    }


def build_list_characters_tool() -> dict:
    return {
        "type": "function",
        "function": {
            "name": "list_characters",
            "description": "返回某场次剧本里出现的角色清单。问「这场有几个角色/都有谁」用它。",
            "parameters": {
                "type": "object",
                "properties": {
                    "scene_ref": {
                        "type": "string",
                        "description": "场次引用，如 '第3场' / '3' / 'Scene_3'",
                    },
                },
                "required": ["scene_ref"],
            },
        },
    }


def build_search_script_lines_tool() -> dict:
    return {
        "type": "function",
        "function": {
            "name": "search_script_lines",
            "description": "按关键词全文检索剧本台词，返回匹配行。问「哪句台词提到X / 某句台词在第几行」用它。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "要检索的台词关键词",
                    },
                    "scene_ref": {
                        "type": "string",
                        "description": "可选场次引用，限定检索范围；不填则全剧本检索",
                    },
                },
                "required": ["query"],
            },
        },
    }


def build_query_database_tool() -> dict:
    return {
        "type": "function",
        "function": {
            "name": "query_database",
            "description": (
                "万能笔：当上面四个工具都覆盖不了时，写一条只读 SQL 直接查数据库。"
                "只允许 SELECT。主要表：scenes(scene_id,scene_code,location,int_ext,time_of_day,shoot_date)、"
                "takes(take_id,scene_id,shot,take_number,status,deleted_at)、"
                "script_lines(line_no,character,text,script_id)、scripts(script_id,scene_id)。"
                "软删行 deleted_at IS NOT NULL，统计 take 记得加 deleted_at IS NULL。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "sql": {
                        "type": "string",
                        "description": "一条只读 SELECT 语句（单句，不要分号分隔多句）",
                    },
                },
                "required": ["sql"],
            },
        },
    }


def build_qp_tools() -> list[dict]:
    """返回 QP 全部 5 个工具 schema（顺序固定，供 config.query_session 与测试用）。"""
    return [
        build_count_takes_tool(),
        build_get_scene_info_tool(),
        build_list_characters_tool(),
        build_search_script_lines_tool(),
        build_query_database_tool(),
    ]
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest backend/tests/test_qp_tools.py -q`
Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add backend/llm/tools/transcript.py backend/tests/test_qp_tools.py
git commit -m "feat(qp): 5 个工具 schema（扁平标量参数 + build_qp_tools）"
```

---

## Task 5: QP executor（`*_executor(args, dal)`）

**Files:**
- Modify: `backend/llm/tools/transcript.py`（追加 5 个 executor）
- Test: `backend/tests/test_qp_tools.py`（追加 L1）

- [ ] **Step 1: 写失败测试**

在 `backend/tests/test_qp_tools.py` 末尾追加：

```python
from backend.db.dal import DAL
from backend.llm.tools.transcript import (
    count_takes_executor,
    get_scene_info_executor,
    list_characters_executor,
    query_database_executor,
    search_script_lines_executor,
)


@pytest.fixture
def seeded_dal(tmp_path) -> DAL:
    d = DAL(tmp_path / "qp_exec.db")
    sid = d.get_or_create_scene("Scene_7", int_ext="室外", time_of_day="夜", location="天台")[0]
    d.start_take(sid, "", 1000.0)
    d.start_take(sid, "", 1001.0)
    script_id = d.insert_script(sid, "raw")
    d.insert_script_line(script_id, 1, "阿强", "我们走吧。")
    d.insert_script_line(script_id, 2, "小美", "再等等。")
    yield d
    d.close()


def test_count_takes_executor(seeded_dal: DAL) -> None:
    res = count_takes_executor({"scene_ref": "7"}, seeded_dal)
    assert res["count"] == 2


def test_count_takes_executor_missing_scene(seeded_dal: DAL) -> None:
    res = count_takes_executor({"scene_ref": "999"}, seeded_dal)
    assert "error" in res  # 找不到老实说没有（spec §7.3）


def test_get_scene_info_executor(seeded_dal: DAL) -> None:
    res = get_scene_info_executor({"scene_ref": "Scene_7"}, seeded_dal)
    assert res["location"] == "天台"
    assert res["character_count"] == 2


def test_list_characters_executor(seeded_dal: DAL) -> None:
    res = list_characters_executor({"scene_ref": "7"}, seeded_dal)
    assert sorted(res["characters"]) == ["小美", "阿强"]


def test_search_script_lines_executor(seeded_dal: DAL) -> None:
    res = search_script_lines_executor({"query": "我们走吧"}, seeded_dal)
    assert res["count"] >= 1
    assert any("走吧" in m["text"] for m in res["matches"])


def test_query_database_executor(seeded_dal: DAL) -> None:
    res = query_database_executor({"sql": "SELECT COUNT(*) AS n FROM scenes;"}, seeded_dal)
    assert res["rows"][0]["n"] == 1


def test_query_database_executor_blocks_write(seeded_dal: DAL) -> None:
    res = query_database_executor({"sql": "DELETE FROM scenes;"}, seeded_dal)
    assert "error" in res
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest backend/tests/test_qp_tools.py -q -k executor`
Expected: FAIL —— `ImportError: cannot import name 'count_takes_executor'`。

- [ ] **Step 3: 实现**

在 `backend/llm/tools/transcript.py` 顶部加 TYPE_CHECKING import（不在运行期 import DAL，避免拖重依赖 / 循环）：

```python
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.db.dal import DAL
```

在文件末尾（`build_qp_tools` 之后）追加 5 个 executor。executor 签名统一 `(args: dict, dal: "DAL") -> dict`（见「关键设计决定 1」）：

```python
# ---------------------------------------------------------------------------
# executor（spec §5.3：executor(args, dal) -> dict；DAL 读全走只读连接，D-QP-12）
# ---------------------------------------------------------------------------

_SCENE_NOT_FOUND = "找不到场次 {ref!r}，数据库里没有这一场（不要用相近场次顶替）。"


# 取参一律 str(...) 强转：4B FunctionGemma 常吐整数场次号 {scene_ref: 7}，
# 不转会在 normalize_scene_code(7).strip() 处抛 AttributeError 穿出循环（spec §5.3）。
def count_takes_executor(args: dict, dal: "DAL") -> dict:
    ref = str(args.get("scene_ref") or "")
    status = args.get("status")
    scene_id = dal.resolve_scene_id(ref)
    if scene_id is None:
        return {"error": _SCENE_NOT_FOUND.format(ref=ref)}
    result = {"scene_ref": ref, "count": dal.count_takes(scene_id, status=status)}
    if status is not None:  # 不带 status 时不塞 null，回喂给模型更干净
        result["status"] = status
    return result


def get_scene_info_executor(args: dict, dal: "DAL") -> dict:
    ref = str(args.get("scene_ref") or "")
    scene_id = dal.resolve_scene_id(ref)
    if scene_id is None:
        return {"error": _SCENE_NOT_FOUND.format(ref=ref)}
    info = dal.get_scene_info(scene_id)
    if info is None:  # 保守防御（resolve 命中后理论不会 None），无害保留
        return {"error": _SCENE_NOT_FOUND.format(ref=ref)}
    return info


def list_characters_executor(args: dict, dal: "DAL") -> dict:
    ref = str(args.get("scene_ref") or "")
    scene_id = dal.resolve_scene_id(ref)
    if scene_id is None:
        return {"error": _SCENE_NOT_FOUND.format(ref=ref)}
    chars = dal.list_characters(scene_id)
    return {"scene_ref": ref, "characters": chars, "count": len(chars)}


def search_script_lines_executor(args: dict, dal: "DAL") -> dict:
    query = str(args.get("query") or "")
    if not query.strip():
        return {"error": "query 不能为空"}
    ref = args.get("scene_ref")
    scene_id = None
    if ref:
        scene_id = dal.resolve_scene_id(str(ref))
        if scene_id is None:
            return {"error": _SCENE_NOT_FOUND.format(ref=ref)}
    try:
        matches = dal.search_script_lines(query, scene_id=scene_id)
    except Exception as exc:  # FTS5 语法错误（空/保留字）等，包成 error 让模型自纠，不抛穿
        return {"error": f"检索失败：{exc}"}
    return {"query": query, "matches": matches, "count": len(matches)}


def query_database_executor(args: dict, dal: "DAL") -> dict:
    sql = str(args.get("sql") or "")
    if not sql.strip():
        return {"error": "sql 为空"}
    return dal.query_readonly(sql)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest backend/tests/test_qp_tools.py -q`
Expected: PASS（schema L0 + executor L1 全部）。

- [ ] **Step 5: Commit**

```bash
git add backend/llm/tools/transcript.py backend/tests/test_qp_tools.py
git commit -m "feat(qp): 5 个 executor（DAL 只读读取 + 找不到说没有 + 万能笔代理）"
```

---

## Task 6: registry `_bootstrap` 注册 QP 工具（传真实 executor）

**Files:**
- Modify: `backend/llm/tools/registry.py`（`_bootstrap`）
- Test: `backend/tests/test_qp_registry.py`（新建）

- [ ] **Step 1: 写失败测试**

新建 `backend/tests/test_qp_registry.py`：

```python
"""QP 工具注册：schema + 真实 executor（executor!=None 首个真实消费者）。"""
from __future__ import annotations

from backend.db.dal import DAL
from backend.llm.tools import registry

_QP_NAMES = ["count_takes", "get_scene_info", "list_characters", "search_script_lines", "query_database"]


def test_qp_tools_registered_with_schema() -> None:
    for name in _QP_NAMES:
        schema = registry.get_tool_schema(name)
        assert schema["function"]["name"] == name


def test_qp_tools_have_real_executor() -> None:
    for name in _QP_NAMES:
        assert registry.get_executor(name) is not None, f"{name} executor 不能为 None（QP Tier 2）"


def test_qp_executor_callable_end_to_end(tmp_path) -> None:
    dal = DAL(tmp_path / "reg.db")
    dal.create_scene("Scene_1")
    executor = registry.get_executor("count_takes")
    res = executor({"scene_ref": "1"}, dal)
    assert res["count"] == 0
    dal.close()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest backend/tests/test_qp_registry.py -q`
Expected: FAIL —— `KeyError: 工具 'count_takes' 未注册`。

- [ ] **Step 3: 实现**

改 `backend/llm/tools/registry.py` 的 `_bootstrap()`。**4.x 已在 `_bootstrap` 里注册了 `report_script_analysis` + `structure_note`（note 工具，executor=None）——务必保留这两条**，在其后追加 QP 5 条（先 Read 当前 `_bootstrap` 确认实际文本，用 Edit 追加，别整体覆盖把 `structure_note` 删了）。追加后形如：

```python
def _bootstrap() -> None:
    """在 module 导入时注册所有工具。"""
    from backend.llm.tools.note import NOTE_TOOL_NAME, build_note_tool  # noqa: PLC0415
    from backend.llm.tools.script import build_l2_tool  # noqa: PLC0415

    register("report_script_analysis", build_l2_tool(), executor=None)
    register(NOTE_TOOL_NAME, build_note_tool(), executor=None)  # 4.x note 工具，保留勿删

    # QP 工具家（Tier 2，executor!=None 首个真实消费者；note/script 仍是 None）
    from backend.llm.tools.transcript import (  # noqa: PLC0415
        build_count_takes_tool,
        build_get_scene_info_tool,
        build_list_characters_tool,
        build_query_database_tool,
        build_search_script_lines_tool,
        count_takes_executor,
        get_scene_info_executor,
        list_characters_executor,
        query_database_executor,
        search_script_lines_executor,
    )

    register("count_takes", build_count_takes_tool(), executor=count_takes_executor)
    register("get_scene_info", build_get_scene_info_tool(), executor=get_scene_info_executor)
    register("list_characters", build_list_characters_tool(), executor=list_characters_executor)
    register("search_script_lines", build_search_script_lines_tool(), executor=search_script_lines_executor)
    register("query_database", build_query_database_tool(), executor=query_database_executor)
```

> 同时把模块顶部 docstring 里「当前无生产消费者」一句改为「QP 是 executor 槽首个真实消费者」（spec §0.1），保持注释与现实一致。

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest backend/tests/test_qp_registry.py -q`
Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add backend/llm/tools/registry.py backend/tests/test_qp_registry.py
git commit -m "feat(qp): registry 注册 5 个 QP 工具 + 真实 executor"
```

---

## Task 7: config.py 给 `query_session` 挂 tools + `tool_choice="auto"`

**Files:**
- Modify: `backend/llm/config.py`（`query_session` 条目）
- Test: `backend/tests/test_qp_config.py`（新建）

- [ ] **Step 1: 写失败测试**

新建 `backend/tests/test_qp_config.py`：

```python
"""query_session 挂 QP tools + tool_choice=auto（D-QP-09 eager）。"""
from __future__ import annotations

from backend.llm.config import TASK_CONFIG


def test_query_session_has_qp_tools() -> None:
    cfg = TASK_CONFIG["query_session"]
    names = [t["function"]["name"] for t in cfg["tools"]]
    assert names == [
        "count_takes",
        "get_scene_info",
        "list_characters",
        "search_script_lines",
        "query_database",
    ]


def test_query_session_tool_choice_auto() -> None:
    # auto 跳：模型自选工具（不是 forced）
    assert TASK_CONFIG["query_session"]["tool_choice"] == "auto"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest backend/tests/test_qp_config.py -q`
Expected: FAIL —— `KeyError: 'tools'`。

- [ ] **Step 3: 实现**

改 `backend/llm/config.py`：顶部 import 区在 `from backend.llm.tools.script import build_l2_tool` 下加：

```python
from backend.llm.tools.transcript import build_qp_tools
```

把 `query_session` 条目改为（保留现有 max_tokens/temperature/priority，补 tools + tool_choice；system 暂留现有，真正的极简 system prompt 由 pipeline 组装，见 Task 9 §5.5）：

```python
    "query_session": {
        "max_tokens": 1024,
        "temperature": 0.3,
        "priority": 1,
        "system": "你是一个场记查询助手，帮助导演和录音师快速查找场记信息。",
        # QP Tier 2 多工具 auto 路由（D-QP-09）。已 rebase 到含 4.x 的 main：
        # query_session/l2_take 仍 eager（只有 note_struct 因 np_note 依赖才 lazy），
        # transcript.py import-neutral，eager 挂 build_qp_tools() 安全，无须 lazy。
        "tools": build_qp_tools(),
        "tool_choice": "auto",
    },
```

> `tools`/`tool_choice` 不在 `service._META_KEYS`（`{"priority","_reserved","system"}`），service 会自动透传给 `create_chat_completion`——与 `l2_take` 一致，无需改 service 的透传逻辑。

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest backend/tests/test_qp_config.py backend/tests/test_llm_service.py backend/tests/test_import_hygiene.py -q`
Expected: PASS（含既有 service 测试不回归 + `test_import_hygiene.py` 子进程冷 import `backend.llm.config` 通过——验证新增的 eager `import build_qp_tools` 没把 transcript 拉成 import 环）。

- [ ] **Step 5: Commit**

```bash
git add backend/llm/config.py backend/tests/test_qp_config.py
git commit -m "feat(qp): query_session 挂 5 工具 + tool_choice=auto（eager，照 l2_take 写法）"
```

---

## Task 7.5: 真模型 probe —— 在建循环前钉死假设 6/7（gate）

> **为什么在这**：Task 8/9 整个建在两条未验证假设上——(7) auto 跳返回可抠名的 content 串、(6) 多跳 `role=tool` 回喂能被 Jinja 模板渲染。StubService 单测把假设当契约编码、**验证不了真模型行为**。这个 probe 只需 Task 7（config 有 tools+auto），不需要循环、不需要 Task 8，用最小代价在动 service/loop 之前拿到真数据。若假设被推翻，改的是 Task 8 的 service 形态（可能要 `infer_message` 变体而非只加 `tool_choice` 覆盖）和 Task 9 的 step A——现在知道比 Task 11 才知道省一轮返工。
>
> 需本地有 GGUF。`@pytest.mark.smoke` + 模块级 `skipif(not GEMMA_MODEL_PATH)`（对齐既有 L2 smoke）——**未设 `GEMMA_MODEL_PATH` 则整模块 skip**，全量套件默认不跑真模型。跑法：`GEMMA_MODEL_PATH=<gguf 路径> uv run pytest backend/tests/test_qp_probe.py -q -s`（**不是** `--smoke` flag，项目无此 flag）。
>
> **✅ 实证结论（已跑，commit `84428fa`）——本 task 已完成，结论已落进 Task 8/9 代码：**
> - **假设 7 成立**：auto 跳直连 client 返回 `finish_reason=stop` + content 串 `<|tool_call>call:count_takes{...}`（`tool_calls=None`）。`service.infer` 正常返回该串、**不撞护栏**，`_scrape_tool_name` 抠到工具名。→ Task 8 按原计划（只加 `tool_choice` 覆盖，**不需要 `infer_message` 变体**）；Task 9 step A 原样。分诊 A 未触发。
> - **假设 6 不成立 → 已解（分诊 B）**：OpenAI 风格 `assistant{content:None,tool_calls}` + `role=tool` 多跳回喂撞 Jinja `UndefinedError: 'raise_exception' is undefined`（状态相关、不稳）。**Task 9 step C 改用纯文本回喂**：`assistant`=auto 步原始 content（模型自吐 `<|tool_call>`）+ `user`=`f"工具 {name} 返回：{json}"`。实测 3 次确定性稳定渲染 + 自然语言收尾。
> - **副发现**：连续多次加载模型后 llama.cpp Metal 退出期有 `GGML_ASSERT` teardown 崩溃（已知上游 bug，结果在崩溃前产出、不影响断言）。Task 11 e2e 注意。
>
> 下面 Step 1-3 是当初的探查脚本设计，**实际提交的 probe（`backend/tests/test_qp_probe.py`）已据上述结论改写成断言版**（auto 返 content / service 不抛 / 纯文本回喂渲染 / openai 格式留痕），与下文略有出入，以提交版为准。

**Files:**
- Create: `backend/tests/test_qp_probe.py`

- [ ] **Step 1: 写 probe（assumption 7：auto 返回可抠名 content，不触发护栏）**

新建 `backend/tests/test_qp_probe.py`：

```python
"""QP 建循环前的真模型 probe（@pytest.mark.smoke）。钉死假设 6/7，结论回填 spec §5.1。

forced 路径已由既有 L2 工作证明（finish_reason=tool_calls + 干净 JSON），本文件不再重复。
"""
from __future__ import annotations

import pytest

from backend.llm.service import LLMService, _reset_service


@pytest.fixture
def real_service():
    _reset_service()
    svc = LLMService()
    yield svc


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_probe_auto_returns_scrapeable_content(real_service) -> None:
    """假设 7：auto 跳 service.infer 返回可抠名的 FunctionGemma content 串，
    且不触发 service 护栏（content is None + finish_reason=tool_calls）。
    """
    from backend.pipelines.qp_query import _scrape_tool_name

    messages = [
        {"role": "system", "content": "你是场记查询助手，有工具就调工具查。"},
        {"role": "user", "content": "第一场拍了多少条？"},
    ]
    # 若真模型 auto 模式吐 content=None + finish_reason=tool_calls，infer 会抛 LookupError——
    # 那就是假设 7 被推翻（见 Step 3 分诊 A）。这里不 try：让它抛，把真相暴露出来。
    text = await real_service.infer(messages, task_type="query_session", timeout=120.0)
    print("\n[probe auto content]\n", repr(text))
    name = _scrape_tool_name(text)
    # 理想：抠到某个 QP 工具名（多半是 count_takes）。即便抠不到，能看到 content 形态也是收获。
    assert isinstance(text, str)
    assert name in {"count_takes", "get_scene_info", "list_characters", "search_script_lines", "query_database", None}
    await real_service.aclose()
```

- [ ] **Step 2: 写 probe（assumption 6：多跳 role=tool 回喂能渲染）**

在同文件追加：

```python
@pytest.mark.smoke
@pytest.mark.asyncio
async def test_probe_multihop_tool_response_renders(real_service) -> None:
    """假设 6：手搭一段「assistant tool_call + role=tool 响应」历史，再 infer 一跳，
    断言模板不报错、且回的是自然语言收尾（不再无脑反复调工具）。
    """
    messages = [
        {"role": "system", "content": "你是场记查询助手。查到结果后用一句话直接回答。"},
        {"role": "user", "content": "第一场拍了多少条？"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "qp_call_0",
                    "type": "function",
                    "function": {"name": "count_takes", "arguments": '{"scene_ref": "1"}'},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "qp_call_0", "name": "count_takes",
         "content": '{"scene_ref": "1", "count": 3}'},
    ]
    text = await real_service.infer(messages, task_type="query_session", timeout=120.0)
    print("\n[probe multi-hop reply]\n", repr(text))
    assert isinstance(text, str) and text.strip()  # 没炸模板、给了回复就算过
    await real_service.aclose()
```

- [ ] **Step 3: 跑 probe + 据结论决定 Task 8/9 形态**

Run: `GEMMA_MODEL_PATH=<gguf 路径> uv run pytest backend/tests/test_qp_probe.py -q -s`

- **两条都过 + auto content 能抠到工具名** → 假设 6/7 成立，Task 8/9 按原计划写。把结论回填 spec §5.1 的 ✅实测。
- **auto probe 抛 `LookupError`（content=None + finish_reason=tool_calls）**（分诊 A，推翻假设 7）：
  → 真模型 auto 模式吐结构化 `tool_calls`。**调整 Task 8**：除 `tool_choice` 覆盖外，再加 `infer_message()` 返回整条 `message`（含 content 或 tool_calls）。**调整 Task 9 step A**：用 `infer_message`，有 `tool_calls` 就直接取 `message["tool_calls"][0]["function"]`（name + arguments），**跳过 step B**（auto 已给干净结构化参数，forced 第二跳冗余）；无 tool_calls 才按 content 抠名收尾。先回填 spec §5.1 再动代码。
- **multi-hop probe 报模板错 / 答非所问**（分诊 B，推翻假设 6）：
  → **调整 Task 9 step C 回喂格式**：不追加 OpenAI 风格 `role=tool`，改手拼 FunctionGemma 串塞 `role=user`：
  `{"role": "user", "content": f"<|tool_response>response:{name}{json.dumps(result, ensure_ascii=False)}<tool_response|>"}`，
  assistant tool_call 那条同样手拼 `<|tool_call>` 或省去。先回填 spec §5.1 再动代码。
- **顺带（advisor 提示）**：把 auto probe 打印出的 content 形态看一眼——QP 参数全扁平标量，FunctionGemma 截断只发生在嵌套数组，**auto 模式很可能直接返回干净的结构化 `tool_calls`**。若是，分诊 A 的「跳过 step B」路径能把每跳两次模型调用砍成一次。用 print 的实测数据决定，别凭猜。

- [ ] **Step 4: Commit**

```bash
git add backend/tests/test_qp_probe.py
git commit -m "test(qp): 真模型 probe 钉死 auto 抠名 + 多跳回喂渲染（建循环前 gate）"
```

> 本 probe 是 gate：**结论确定后再进 Task 8**。若触发分诊 A/B，把对 Task 8/9 的调整连同 spec §5.1 回填一并处理——下面 Task 8/9 的代码是「假设成立」分支，按 probe 结论可能微调。

---

## Task 8: service.py 加按调用覆盖 `tool_choice`（forced 跳用）

**Files:**
- Modify: `backend/llm/service.py`（`_submit` / `infer` / `infer_tool`）
- Test: `backend/tests/test_llm_service.py`（追加）

- [ ] **Step 1: 写失败测试**

在 `backend/tests/test_llm_service.py` 末尾追加（沿用该文件既有的 stub/单例 reset 风格；下面给出自带 stub 的独立用例）：

```python
import pytest

from backend.llm.config import TASK_CONFIG
from backend.llm.service import LLMService, _reset_service


class _RecordingClient:
    """记录最后一次 create_chat_completion 的 kwargs，返回固定 tool_calls。"""

    def __init__(self) -> None:
        self.last_kwargs: dict = {}

    def create_chat_completion(self, messages, **kwargs):
        self.last_kwargs = kwargs
        return {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "c0",
                                "type": "function",
                                "function": {"name": "count_takes", "arguments": "{}"},
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ]
        }


@pytest.mark.asyncio
async def test_infer_tool_tool_choice_override_forwarded() -> None:
    _reset_service()
    svc = LLMService()
    client = _RecordingClient()
    svc._client = client  # 注入，跳过真实加载

    forced = {"type": "function", "function": {"name": "count_takes"}}
    await svc.infer_tool(
        [{"role": "user", "content": "x"}],
        task_type="query_session",
        tool_choice=forced,
    )
    # 覆盖值透传给 client，盖掉 config 的 "auto"
    assert client.last_kwargs.get("tool_choice") == forced
    await svc.aclose()


@pytest.mark.asyncio
async def test_infer_tool_choice_defaults_to_config() -> None:
    _reset_service()
    svc = LLMService()
    client = _RecordingClient()
    svc._client = client

    await svc.infer_tool(
        [{"role": "user", "content": "x"}],
        task_type="query_session",
    )
    # 不传 override → 用 config 的 "auto"（默认行为不变，回归保护）
    assert client.last_kwargs.get("tool_choice") == TASK_CONFIG["query_session"]["tool_choice"]
    await svc.aclose()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest backend/tests/test_llm_service.py -q -k tool_choice`
Expected: FAIL —— `TypeError: infer_tool() got an unexpected keyword argument 'tool_choice'`。

- [ ] **Step 3: 实现**

改 `backend/llm/service.py`：

> ⚠️ 4.x（语音 NP）已给 `_submit` 加了 `audio: bytes | None = None` 参数（在 `want_tool_call` 之后），并新增 `infer_voice`/`infer_voice_tool` 两个入口。**做 Edit 时 old_string 一律取文件当前真实文本（含 `audio`），别照下面片段把 audio 删掉**；新参数 `tool_choice` 排在 `audio` 之后。`infer_voice`/`infer_voice_tool` 保持原样不动（QP 不走语音）。`_META_KEYS` 仍是 `{priority,_reserved,system}`、worker 护栏未变——都不要碰。

`_submit` 签名加 `tool_choice` 参数（排在 4.x 的 `audio` 之后），并在组装 gen_kwargs 后覆盖：

```python
    async def _submit(
        self,
        messages: list[dict],
        task_type: str,
        priority: int | None,
        timeout: float | None,
        want_tool_call: bool,
        audio: bytes | None = None,         # ← 4.x 已有参数，保留勿删
        tool_choice: str | dict | None = None,
    ) -> asyncio.Future:
```

在 `gen_kwargs = {k: v for k, v in cfg.items() if k not in _META_KEYS}` 之后加：

```python
        # 按调用覆盖 tool_choice（QP forced 跳动态强制某工具名，spec §5.4）。
        # None = 不覆盖，沿用 TASK_CONFIG 的静态 tool_choice（默认行为不变）。
        if tool_choice is not None:
            gen_kwargs["tool_choice"] = tool_choice
```

`infer` 与 `infer_tool` 各加 `tool_choice: str | dict | None = None` 参数并透传给 `_submit`：

```python
    async def infer(
        self,
        messages: list[dict],
        task_type: str,
        priority: int | None = None,
        timeout: float | None = 30.0,
        tool_choice: str | dict | None = None,
    ) -> str:
        ...
        fut = await self._submit(
            messages, task_type, priority, timeout, want_tool_call=False, audio=None, tool_choice=tool_choice
        )
        return await asyncio.wait_for(fut, timeout=timeout)

    async def infer_tool(
        self,
        messages: list[dict],
        task_type: str,
        priority: int | None = None,
        timeout: float | None = 30.0,
        tool_choice: str | dict | None = None,
    ) -> dict:
        ...
        fut = await self._submit(
            messages, task_type, priority, timeout, want_tool_call=True, tool_choice=tool_choice
        )
        return await asyncio.wait_for(fut, timeout=timeout)
```

> 同步更新两个方法的 docstring，补一行 `tool_choice: 可选，按调用覆盖 TASK_CONFIG 的 tool_choice；None 沿用配置`。

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest backend/tests/test_llm_service.py -q`
Expected: PASS（新增 2 条 + 既有全部不回归——`l2_take` 的 forced 路径默认 tool_choice 不变）。

- [ ] **Step 5: Commit**

```bash
git add backend/llm/service.py backend/tests/test_llm_service.py
git commit -m "feat(qp): service.infer/infer_tool 加按调用 tool_choice 覆盖（forced 跳用）"
```

---

## Task 9: qp_query.py 两步走循环（核心）

**Files:**
- Create: `backend/pipelines/qp_query.py`
- Test: `backend/tests/test_qp_loop.py`（新建）

- [ ] **Step 1: 写失败测试**

新建 `backend/tests/test_qp_loop.py`：

```python
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
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest backend/tests/test_qp_loop.py -q`
Expected: FAIL —— `ModuleNotFoundError: No module named 'backend.pipelines.qp_query'`。

- [ ] **Step 3: 实现**

新建 `backend/pipelines/qp_query.py`：

```python
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
) -> str:
    """两步走循环：≤max_hops 跳，返回最终自然语言文本。messages 原地追加每跳的 tool 往返。"""
    for hop in range(max_hops):
        # step A — auto 跳：抠工具名。
        # ✅ Task 7.5 probe 已实证「假设 7」成立：service.infer 在 auto 跳返回 FunctionGemma
        #   content 串（finish_reason=stop，不撞 service 护栏），happy path 即此分支。
        #   （故不需要 infer_message 变体，分诊 A 未触发。）
        text = await service.infer(messages, task_type=_QP_TASK, priority=1, timeout=timeout)
        name = _scrape_tool_name(text)
        if name is None:
            return text  # 模型给的是自然语言最终答案，终止

        # step B — forced 跳：grammar 出干净 JSON 参数。
        # 异常契约：TimeoutError/CancelledError 放行给 caller（route 兜底返回友好错误，对齐
        # l2_take）；其余取参失败（模型没走 FC 的 LookupError / arguments 缺失或非法）包成 error
        # 回喂、不抛穿，让模型下一跳自纠。
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
            raise
        except (LookupError, KeyError, json.JSONDecodeError, TypeError) as exc:
            logger.warning("qp forced 跳取参失败 name=%s: %r", name, exc)
            result = {"error": f"工具 {name} 调用失败：参数无法解析，请换种方式或直接回答。"}
        else:
            # step C — 执行（executor 在 to_thread worker 跑，只读连接跨线程安全）
            result = await asyncio.to_thread(_run_executor, name, args, dal)

        # 回喂格式（Task 7.5 probe 实证定案）：**不**用 OpenAI 风格 assistant{tool_calls}+role=tool——
        # 那会撞这个 GGUF 的 Jinja 模板 `UndefinedError: 'raise_exception' is undefined`（状态相关、不稳）。
        # 改纯文本：assistant 喂 auto 步原始 content（模型自吐的 <|tool_call>...，特殊 token 模型认得），
        # tool 结果用纯文本 user 消息。实测 3 次确定性稳定渲染 + 自然语言收尾、不再无脑调工具。
        messages.append({"role": "assistant", "content": text})
        messages.append(
            {"role": "user", "content": f"工具 {name} 返回：{json.dumps(result, ensure_ascii=False)}"}
        )

    return _FALLBACK_TEXT  # 兜底：正常 1~2 跳收尾，靠 system 防 run-on（spec §5.2）


def _build_scene_catalog(dal: "DAL") -> str:
    """场次目录注入（spec §7.1）：编号 + scene_code + slugline + 顺序号，注入 user/context。"""
    scenes = dal.list_scenes_readonly()
    if not scenes:
        return "（当前项目还没有任何场次记录。）"
    lines = ["当前项目场次目录（顺序号. 编号 ｜ 内外景 地点 时间）："]
    for pos, s in enumerate(scenes, start=1):
        slug = " ".join(
            v for v in (s.get("int_ext"), s.get("location"), s.get("time_of_day")) if v
        )
        lines.append(f"{pos}. {s['scene_code']} ｜ {slug or '（无 slugline）'}")
    lines.append(f"（共 {len(scenes)} 场）")
    return "\n".join(lines)


async def run_qp_query(
    *,
    text: str,
    dal: "DAL",
    service: "LLMService",
    timeout: float = 30.0,
) -> str:
    """QP 入口：拼场次目录 + 极简 system → 跑两步走循环 → 返回自然语言答案。"""
    messages = [
        {"role": "system", "content": _QP_SYSTEM},
        {"role": "user", "content": f"{_build_scene_catalog(dal)}\n\n用户提问：{text}"},
    ]
    return await run_tool_loop(messages, service=service, dal=dal, timeout=timeout)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest backend/tests/test_qp_loop.py -q`
Expected: PASS（抠名 + 单跳收尾 + 5 跳兜底 + error 回喂）。

- [ ] **Step 5: Commit**

```bash
git add backend/pipelines/qp_query.py backend/tests/test_qp_loop.py
git commit -m "feat(qp): 两步走 tool-loop（auto 抠名 + forced 取参 + executor 回喂，≤5 跳）"
```

---

## Task 10: `POST /api/v1/query` route + 广播 `qp.answer.{conn_id}`

**Files:**
- Create: `backend/api/routes/query.py`
- Modify: `backend/core/events.py`（加 `QP_ANSWER` + `QpAnswerPayload`）
- Modify: `backend/api/app.py`（include query_router）
- Test: `backend/tests/test_qp_route.py`（新建）

- [ ] **Step 1: 写失败测试**

新建 `backend/tests/test_qp_route.py`：

```python
"""POST /api/v1/query：跑 QP → 广播 qp.answer.{conn_id} + 同步返回答案。"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.core.orchestrator import create_orchestrator
from backend.db.dal import DAL


class _FakeService:
    """跳过真实模型：run_qp_query 用不到它（route 经 monkeypatch 短路）。"""


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", "devtoken")
    dal = DAL(tmp_path / "route.db")
    dal.create_scene("Scene_1")
    orch = create_orchestrator(dal)
    app = create_app(orch, llm_service=_FakeService())

    # 短路 run_qp_query，避免真模型；断言 route 把答案广播 + 返回
    async def _fake_run_qp_query(*, text, dal, service, timeout=30.0):
        return f"答复：{text}"

    monkeypatch.setattr("backend.api.routes.query.run_qp_query", _fake_run_qp_query)

    with TestClient(app) as c:
        c._dal = dal
        yield c
    dal.close()


def test_post_query_returns_answer(client) -> None:
    resp = client.post(
        "/api/v1/query",
        json={"text": "第一场拍了多少条", "conn_id": "abc"},
        headers={"Authorization": "Bearer devtoken"},
    )
    assert resp.status_code == 200
    assert resp.json()["answer"] == "答复：第一场拍了多少条"


def test_post_query_broadcasts_qp_answer(client, monkeypatch) -> None:
    captured = {}

    def _capture(topic, payload):
        captured["topic"] = topic
        captured["payload"] = payload

    monkeypatch.setattr(
        client.app.state.connection_manager, "broadcast", _capture
    )
    client.post(
        "/api/v1/query",
        json={"text": "hi", "conn_id": "abc"},
        headers={"Authorization": "Bearer devtoken"},
    )
    assert captured["topic"] == "qp.answer.abc"  # topic 带 conn_id，客户端按前缀过滤


def test_post_query_requires_auth(client) -> None:
    resp = client.post("/api/v1/query", json={"text": "x", "conn_id": "abc"})
    assert resp.status_code in (401, 403)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest backend/tests/test_qp_route.py -q`
Expected: FAIL —— 404（路由未注册）/ 或 `ModuleNotFoundError: backend.api.routes.query`。

- [ ] **Step 3: 实现**

(a) `backend/core/events.py` 加 topic 常量（在 `SCENE_CHANGED = "scene.changed"` 附近）与 payload（frozen dataclass，与 `QueryRequestPayload` 配对）：

```python
QP_ANSWER = "qp.answer"  # 实际广播 topic 为 f"{QP_ANSWER}.{conn_id}"，客户端按前缀过滤
```

```python
@dataclass(frozen=True)
class QpAnswerPayload:
    """qp.answer.{conn_id} 的 payload（QP 完成后广播，客户端按 conn_id 认领）。"""

    connection_id: str
    answer_text: str
```

(b) 新建 `backend/api/routes/query.py`：

```python
"""QP 直连入口（spec §10）：POST /api/v1/query。

请求体带发起方 conn_id；QP 完成后把答案广播到 topic qp.answer.{conn_id}
（复用现有 ConnectionManager 广播 seam，客户端按 conn_id 前缀认领，spec §9）。
v1 同时同步返回答案，便于 demo / 测试（不依赖 WS 客户端）。
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from backend.api.auth import require_admin
from backend.core.events import QP_ANSWER, QpAnswerPayload
from backend.pipelines.qp_query import run_qp_query

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["query"])


class QueryRequest(BaseModel):
    text: str
    conn_id: str


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

    # run_tool_loop 把 TimeoutError 等放行给这里（异常契约见 qp_query.run_tool_loop docstring）。
    # 兜成友好自然语言答案、不让 route 500——demo/前端拿到的始终是一句话。CancelledError
    # （取消，BaseException 非 Exception）不在此捕获，照常向上传播。
    try:
        answer = await run_qp_query(text=body.text, dal=orchestrator.dal, service=service)
    except Exception as exc:  # noqa: BLE001
        logger.warning("qp query 失败 conn_id=%s: %r", body.conn_id, exc)
        answer = "抱歉，这次查询出错了，请换种说法再试一次。"

    cm = request.app.state.connection_manager
    cm.broadcast(
        f"{QP_ANSWER}.{body.conn_id}",
        QpAnswerPayload(connection_id=body.conn_id, answer_text=answer),
    )
    return {"status": "ok", "answer": answer}
```

(c) `backend/api/app.py`：顶部 import 区加 `from backend.api.routes.query import router as query_router`，在 `app.include_router(takes_router)` 之后加 `app.include_router(query_router)`。

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest backend/tests/test_qp_route.py -q`
Expected: PASS（返回答案 + 广播 topic 带 conn_id + 鉴权）。

- [ ] **Step 5: Commit**

```bash
git add backend/api/routes/query.py backend/core/events.py backend/api/app.py backend/tests/test_qp_route.py
git commit -m "feat(qp): POST /query 直连入口 + 广播 qp.answer.{conn_id}"
```

---

## Task 11: L3 真模型 e2e 验收（hero 问题端到端）

> ✅ **已完成（commit `159d4f3`）+ 挖出并修了一个根因级共享层 bug**：e2e 初跑 auto 路径不调工具（瞎答）。一路隔离（排除 4B 随机/prompt/线程/参数）定位根因——4.x 多模态单实例给 Llama 装的 `MultimodalGemma4Handler.CHAT_FORMAT` **不渲染 FunctionGemma 工具声明**，带 tools 的文本请求模型看不到工具（L2/NP forced 靠 grammar 兜没暴露，QP auto 暴露）。修复在 `GemmaClient`（commit `44c5da2`）：text+tools 临时换 GGUF 原生 FunctionGemma Jinja formatter，音频/图像仍走多模态 handler。修后 e2e 3 hero 问题（拍了多少条→3 / 在哪拍→客厅 / 几个角色→2）全过、L2/NP forced FC smoke 零回归。**教训**（advisor 纠偏）：probe(raw client) 成功 vs e2e(service) 失败时别混淆 path 与 prompt 两变量——是 path（client 用 GGUF 原生模板 vs service 用多模态 handler），不是 prompt/4B。reviewed Task 9 的原 prompt+目录 handler 修好后照样工作，无需改。详见 memory `project_qp_tool_loop`。

> spec §12 L3。底层假设 6/7 已由 **Task 7.5 probe** 在建循环前钉死；本 task 是**全链路 e2e 验收**——真权重跑完整 `run_qp_query`（场次目录 + 两步走循环 + executor 只读查询），确认 hero 问题答得对。需本地有 GGUF。`@pytest.mark.smoke` + 模块级 `skipif(not GEMMA_MODEL_PATH)`（对齐既有 L2 smoke / probe）——未设 `GEMMA_MODEL_PATH` 则 skip。跑法：`GEMMA_MODEL_PATH=<gguf 路径> uv run pytest backend/tests/test_qp_smoke.py -q -s`（**不是** `--smoke` flag）。注意 llama.cpp Metal 退出期 teardown 崩溃（已知 bug，结果在崩溃前产出）；e2e 测试内**复用单一 client/service**、少 reset，减少多次加载。

**Files:**
- Create: `backend/tests/test_qp_smoke.py`

- [ ] **Step 1: 写 smoke 测试**

新建 `backend/tests/test_qp_smoke.py`：

```python
"""QP L3 真模型 e2e（@pytest.mark.smoke + skipif(not GEMMA_MODEL_PATH)，未设则 skip）。

验证两条 spec ✅实测假设在多跳循环下仍成立：
  (7) auto 跳返回可抠名的 FunctionGemma content 串（非 content=None + finish_reason=tool_calls）。
  (6) 多跳 tool-response 回喂后，第二跳能正常收尾（Jinja 模板能渲染 role=tool 历史）。
"""
from __future__ import annotations

import pytest

from backend.db.dal import DAL
from backend.llm.service import LLMService, _reset_service
from backend.pipelines.qp_query import run_qp_query


@pytest.fixture
def real_service():
    _reset_service()
    svc = LLMService()
    yield svc


def _seed(tmp_path) -> DAL:
    dal = DAL(tmp_path / "qp_smoke.db")
    sid = dal.get_or_create_scene("Scene_1", int_ext="室内", time_of_day="日", location="客厅")[0]
    dal.start_take(sid, "", 1000.0)
    dal.start_take(sid, "", 1001.0)
    dal.start_take(sid, "", 1002.0)
    return dal


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_qp_count_takes_end_to_end(tmp_path, real_service) -> None:
    """hero 问题：第一场拍了多少条 → 期望答案含 '3'。"""
    dal = _seed(tmp_path)
    try:
        answer = await run_qp_query(
            text="第一场一共拍了多少条？", dal=dal, service=real_service, timeout=120.0
        )
        assert answer and "3" in answer  # 多跳循环跑通 + 数对
    finally:
        await real_service.aclose()
        dal.close()


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_qp_scene_info_end_to_end(tmp_path, real_service) -> None:
    """第二个 hero 问题：第一场在哪拍 → 期望答案含 '客厅'。"""
    dal = _seed(tmp_path)
    try:
        answer = await run_qp_query(
            text="第一场在哪拍的？", dal=dal, service=real_service, timeout=120.0
        )
        assert answer and "客厅" in answer
    finally:
        await real_service.aclose()
        dal.close()
```

- [ ] **Step 2: 跑 smoke（需真模型）**

Run: `GEMMA_MODEL_PATH=<gguf 路径> uv run pytest backend/tests/test_qp_smoke.py -q -s`
Expected: PASS —— 两个 hero 问题答案分别含 `3` / `客厅`，证明 auto 抠名 + forced 取参 + 多跳回喂渲染端到端成立。

- [ ] **Step 3: 若 e2e 失败，分诊（不要硬改测试让它绿）**

> Task 7.5 已过的前提下，假设 6/7 不该再炸。先看 e2e 特有环节：场次目录拼得对不对（`_build_scene_catalog`）、极简 system 是否够模型选对工具、executor 查询返回是否符合预期。若仍命中下面两个底层症状，说明 7.5 漏过、按其分诊修并回填 spec §5.1。

- **症状 A：step A 抛 `LookupError: content 为 None 且 finish_reason='tool_calls'`**（service 护栏拦截）。
  → 真模型 auto 模式吐了结构化 tool_calls 而非 content 串（推翻假设 7）。
  修法：给 service 加一个 `infer_message()` 变体返回整条 `message`（含 content 或 tool_calls），
  `run_tool_loop` step A 改用它：有 `tool_calls` 直接取 `tool_calls[0].function.name` 当 name、
  跳过 step B 直接用其 arguments；否则按 content 抠名。**先在 spec §5.1 标注实测结论再动代码。**

- **症状 B：多跳时第二跳模型答非所问 / 报模板错**（推翻假设 6：Jinja 渲染 role=tool 历史失败）。
  → fallback：`run_tool_loop` step C 不再追加 OpenAI 风格 `role=tool` 消息，改为把工具结果手拼成
  FunctionGemma 串塞进 `role=user`：
  `messages.append({"role": "user", "content": f"<|tool_response>response:{name}{json.dumps(result, ensure_ascii=False)}<tool_response|>"})`，
  并去掉 assistant tool_calls 那条（或同样手拼 `<|tool_call>`）。**先在 spec §5.1 标注实测结论再动代码。**

- **两种症状都把实测结论回填 spec §5.1 / §12 的 ✅实测 标注**，让设计与现实对齐（项目纪律）。

- [ ] **Step 4: Commit**

```bash
git add backend/tests/test_qp_smoke.py
git commit -m "test(qp): L3 真模型 spike（auto/forced + 多跳回喂端到端）"
```

> 若 Step 3 触发了 service / loop 改动，连同 spec 回填一起补一个 `fix(qp): ...` commit。

---

## 收尾：全量回归 + 文档

- [ ] 跑全量：`uv run pytest backend/tests/ -q` —— 全绿（基线 + QP 新增）。
- [ ] 跑 lint：`uv run ruff check backend/` —— 无新增告警。
- [ ] 手测 demo（可选，需真模型 + dev server）：
  ```bash
  SOUNDSPEED_DEV=1 uv run python -m backend.api
  # 另开终端：
  curl -s -X POST localhost:8000/api/v1/query \
    -H "Authorization: Bearer devtoken" -H "Content-Type: application/json" \
    -d '{"text":"第一场拍了多少条","conn_id":"manual-test"}'
  ```
  Expected: 返回 `{"status":"ok","answer":"..."}`，answer 是自然语言条数。
- [ ] 更新 memory `project_qp_tool_loop`：已 rebase 到含 4.x 的 main（config 仍 eager，「合并 4.x 重贴 lazy」提醒已作废）、内核 v1 落地状态、smoke 结论（假设 6/7）、延后的场次 alias 表迁移号 v9→v10。

---

## Self-Review（spec 覆盖核对）

| spec 节 | 落点 | 覆盖 |
|--------|------|------|
| §4 工具集（5 工具，扁平标量） | Task 4 schema + Task 5 executor | ✅ |
| §4.1 注册机制（照搬 L2/NP） | Task 6 registry `_bootstrap` 传真实 executor | ✅ |
| §5.1 两步走机制 | Task 7.5 真模型 probe 钉死假设 6/7 → Task 9 `run_tool_loop`（auto 抠名 + forced 取参 + 回喂） | ✅ |
| §5.2 终止与跳数（≤5 + run-on 防护） | Task 9（5 跳兜底 + 极简 system 钉「查一次就回答」） | ✅ |
| §5.3 executor 契约 | Task 5（`(args, dal)->dict` + 错误包裹）+ Task 9（to_thread） | ✅（签名具体化，见关键决定 1） |
| §5.4 service 最小改动 | Task 8（tool_choice 覆盖） | ✅ |
| §5.5 上下文注入与 prompt 预算 | Task 9（极简 system + 场次目录注入 user，few-shot 延后） | ✅ |
| §6 万能笔安全墙（ro/authorizer/封顶/超时/FTS 收口） | Task 3 `query_readonly` + Task 1 `_readonly_conn`（D-QP-12 全读 ro） | ✅ |
| §7 场次解析（normalize + 目录注入 + 找不到说没有） | Task 1 `normalize_scene_code`/`resolve_scene_id` + Task 9 目录 + Task 5 找不到 error | ✅（写路径 dedup bug 延后，见关键决定 3） |
| §8 thinking 开关 | 延后（spec 明确非本设计交付） | ✅ 不做 |
| §9 输出契约（WS qp.answer.{conn_id} + conn_id 管线） | Task 10 route 广播 + `QpAnswerPayload` | ✅ |
| §10 文件落点 | File Structure 表逐项对应 | ✅ |
| §12 测试金字塔（L0/L1/安全墙/L2/L3） | Task 4(L0)/5(L1)/3(安全墙)/9(L2)/7.5(probe gate)/11(L3 e2e) | ✅ |
| §3 入口层（分类器） | 延后（依赖 4.x，spec 明确非本设计交付） | ✅ 不做 |

**Placeholder 扫描：** 无 TBD/TODO 占位（config 的现有 `TODO(1.G)` system 注释保留原样，不属本计划新增）。每个 code step 都给了完整代码。

**类型一致性：** executor 签名全程 `(args, dal)->dict`；工具名 5 个在 Task 4/5/6/7 一致（`count_takes`/`get_scene_info`/`list_characters`/`search_script_lines`/`query_database`）；`run_qp_query`/`run_tool_loop` 关键字参数（`service`/`dal`/`timeout`）在 Task 9/10/11 一致；`tool_choice` 覆盖参数在 Task 8/9 一致；广播 topic `qp.answer.{conn_id}` 在 Task 10 route 与测试一致。
