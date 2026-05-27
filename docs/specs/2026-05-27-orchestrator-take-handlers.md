# Spec: Orchestrator take handler + L2 触发（ticket 1.H）

版本：v0.1
日期：2026-05-27
状态：Q1–Q6 已 Lead 拍板，可供 backend agent 实施

对接 ticket：1.H · feat: core — take.end → 触发 L2 → 写 takes 表

依赖 spec（按权威级别）：
1. `orchestrator-session-state v0.4`（`docs/specs/2026-05-27-orchestrator-session-state.md`）
2. `l2-pipeline v0.1`（`docs/specs/2026-05-27-l2-pipeline.md`）
3. `sqlite-schema v0.3.2`（`docs/specs/2026-05-27-sqlite-schema.md`）
4. `llm-service v0.1`（`docs/specs/2026-05-27-llm-service.md`）
5. `development-plan v0.2`（`docs/specs/2026-05-27-development-plan.md`）
6. `system-architecture v0.1`（`docs/specs/2026-05-26-system-architecture.md`）
7. `onset-llm-ux v1.2.1`（`docs/specs/2026-05-22-onset-llm-ux.md`）

---

## §1 范围与目标

**架构约定**：Orchestrator 假定调用方在 asyncio 事件循环内。take.end handler 用 `asyncio.get_running_loop()` 获取 loop；在非 async 上下文调用 publish 时该调用会抛 RuntimeError，由 Orchestrator 的 exception isolation 捕获记日志，L2 不触发。不提供降级路径（不调 `asyncio.run()`），明确错误优于静默失败。（Q6 拍板）

### 1.1 1.H 做什么

**Stage 1（factory 重构）**：Orchestrator 构造期依赖注入重构——引入 `Dependencies` 容器，使 LLMService 与 L2 runner 能优雅地注入，同时保持 1.E 测试覆盖零改动。

**Stage 2（take handler 实现）**：注册 `take.start` / `take.end` 两个内置 handler。`take.start` 建 DAL take 行、更新 SessionState；`take.end` 结束 take、fire-and-forget 触发 L2、写 takes.script_diff、写 take_line_matches、publish `take.changed`。

**Stage 3（集成测试）**：端到端验证「activate_scene 设置 → take.start → asr.final × N → take.end → L2 → 写库 → take.changed 发布」全链路。测试文件 `backend/tests/test_orchestrator_l2.py`。

### 1.2 1.H 不做什么

- 不实现 L1 / L3 / SP / NP / QP Pipeline（独立 ticket）
- 不实现 ASR 真实集成（使用 mock `asr.final.ch1` event）
- 不实现 WebSocket 推送 `take.changed`（由 1.I 在 Orchestrator.subscribe 注册转发）
- 不实现前端 Take Detail 接入（1.L ticket）
- 不新增 `SCENE_ACTIVATE` 事件常量或 handler（见 §5.1 测试策略说明）

---

## §2 与上下游 spec 的关系

本 spec 不重复上游 spec 已有内容，只填实施空白。

| 上游 spec | 本 spec 直接复用 | 本 spec 填的空白 |
|---|---|---|
| `orchestrator-session-state v0.4` | pub/sub 同步架构；`Handler = Callable[[object], None]`；handler 异常隔离（记日志 + 继续）；1.E 内置 handler 行为；`SessionState.take_start / take_end` 方法签名 | take.start / take.end handler 实现细节；Dependencies 容器结构；异步 L2 触发方案 |
| `l2-pipeline v0.1` | `L2Input / L2Output / LineMatch` dataclass；`run_l2_take(input_data, llm_service)` 签名；`L2ParseError` 异常；insertion line_no=-1 跳过写 take_line_matches（§4 D5）；previous_notes 来源是 `takes.script_diff["script_diff_summary"]`（§10 Q5）；transcript 2500 字符截断由 pipeline 内部处理（caller 无需预截断） | previous_notes 提取算法（1.H caller 的组装逻辑）；previous_notes 3 条上限 / 200 字符截断（caller 侧处理） |
| `sqlite-schema v0.3.2` | `start_take / end_take / insert_take_line_match / list_take_line_matches` 签名；takes.script_diff 存 JSON dict；`insert_take_line_match(take_id, line_id, diff_type, payload)` | `update_take_l2_output` / `list_script_lines` / `insert_take_line_matches` 三个 DAL 方法在 1.H scope 内新增（见 §3.3） |
| `llm-service v0.1` | `get_service()` 工厂；`StubClient` 测试 fixture；`_reset_service()` 用法 | 1.H 如何将 LLMService 实例注入 Orchestrator（通过 Dependencies 容器） |
| `onset-llm-ux v1.2.1` | `take.changed` 事件的前端消费方式；`status='tbd'` 作为 L2 完成前的默认值 | `take.changed` payload 结构（§4.2 / §4.5） |

---

## §3 Stage 1：Orchestrator factory 重构

### 3.1 现状问题（1.E 挖的坑）

读 `backend/core/orchestrator.py`（67 行），现状如下：

**DAL 注入方式**：`__init__(self, dal: DAL, session: SessionState | None = None)`，DAL 以构造参数方式注入，没有全局单例。注入方式本身是正确的。

**handler 注册形式**：`_register_builtin_handlers` 内用 lambda 调 `self._on_asr_final`，handler 是私有方法而非独立 class。

**问题所在**：当 1.H 要同时注入 `LLMService` 和 `run_l2_take`（L2 runner）时，`__init__` 参数列表会膨胀到 `(dal, session, llm_service, l2_runner)`，且各 handler 方法体内会散落对 `self.dal`、`self.llm_service`、`self.l2_runner` 的直接引用——这就是「DAL 依赖扩散」：依赖未被收束，handler 与具体依赖实例紧耦合，无法独立测试单个 handler，也无法不带 LLMService 初始化纯 DAL-only 的 Orchestrator。

**1.E 现有接口**：
```python
Orchestrator(dal: DAL, session: SessionState | None = None)
```
必须保持兼容，因为 `test_orchestrator.py` 有 17 个用例用此签名，且不在 1.H 改动范围内。

### 3.2 重构方案

#### 3.2a 最小重构（1.H 内实施）

⚠ **本 spec 决策**：采用最小重构方案。引入 `Dependencies` dataclass 收束依赖，handler 继续保持 method 形式（不拆 class），1.E 测试零改动。

```python
# backend/core/orchestrator.py

from __future__ import annotations
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.llm.service import LLMService
    from backend.pipelines.l2_take import L2Output


@dataclass
class Dependencies:
    """Orchestrator 依赖容器。
    
    llm_service 和 l2_runner 可为 None（纯 DAL-only 场景，如 1.E 测试）。
    """
    dal: "DAL"
    llm_service: "LLMService | None" = None
    l2_runner: "object | None" = None  # Callable，实际是 run_l2_take


class Orchestrator:
    def __init__(
        self,
        dal: "DAL",
        session: "SessionState | None" = None,
        *,
        llm_service: "LLMService | None" = None,
        l2_runner: "object | None" = None,
    ) -> None:
        """1.E 兼容签名 + 1.H 新增 keyword-only 可选参数。"""
        self._deps = Dependencies(dal=dal, llm_service=llm_service, l2_runner=l2_runner)
        self.session: SessionState = session if session is not None else SessionState()
        self._handlers: dict[str, list[Handler]] = {}
        self._register_builtin_handlers()

    @property
    def dal(self) -> "DAL":
        """兼容现有测试对 orch.dal 的直接访问。"""
        return self._deps.dal
```

**兼容性保证**：
- 现有 `Orchestrator(dal, session)` 调用：正常工作，`llm_service` / `l2_runner` 默认 None。
- 现有 `orch.dal` 直接属性访问：通过 property 兼容（test_orchestrator.py 无直接访问 orch.dal，仅间接通过 DAL 方法验证写库结果，但保留 property 以防 1.I 层代码访问）。
- 所有 1.E 用例：零改动，即刻通过。

**1.H 新增创建方式**：

```python
# 模块级工厂函数（非类方法，避免与 __init__ 形成两条入口造成困惑）
def create_orchestrator(
    dal: "DAL",
    session: "SessionState | None" = None,
    *,
    llm_service: "LLMService | None" = None,
) -> Orchestrator:
    """1.H 推荐的带 LLMService 的 Orchestrator 创建入口。"""
    from backend.pipelines.l2_take import run_l2_take
    return Orchestrator(dal, session, llm_service=llm_service, l2_runner=run_l2_take)
```

⚠ **本 spec 决策**：用模块级函数 `create_orchestrator` 而非类工厂方法（`Orchestrator.from_config`）。理由：类工厂方法和 `__init__` 共存会形成两条合法入口，代码读者需要判断哪条是「正路」；模块级函数更显眼，IDE 自动补全提示更直接；且与 `get_service()` 工厂函数风格一致（llm-service v0.1 §4.1）。

⚠ **本 spec 决策**：旧 `Orchestrator(dal, session)` API 不做 deprecation，继续有效。理由：1.E 测试不动是硬约束，deprecation 会导致 warning log 污染测试输出，得不偿失；等彻底重构时（3.2b）一并处理。

#### 3.2b handler class 拆分（已关闭，不在 1.H 实施）

⚠ **Lead 拍板（Q2）**：handler 不拆 class，保持 `_on_asr_final` / `_on_take_start` / `_on_take_end` 私有方法风格。待 handler 总数 ≥ 5 时再评估提取 class 是否有组织收益，届时由专属 refactor ticket 处理。

### 3.3 DAL 接口扩展（⚠ 1.H scope 内实施，Lead 已拍板 Q1 + Q3）

**背景**：`take.end` handler 先调 `dal.end_take(...)` 写结束时间戳（script_diff=None），L2 后台任务完成后再写 script_diff。但现有 `dal.end_take` 是「一锅出」——同时写 end_ts / status / script_diff / notes，无法支持两步写入。`_build_script_lines` 需要按 script_id 查台词行，但 DAL 缺对应读方法。

以下三个方法均在 1.H scope 内补充，实施时与 `dal.py` 同步完成。sqlite-schema v0.3.2 §6 接口清单须同步更新。

#### 3.3.1 `update_take_l2_output`

```python
def update_take_l2_output(
    self,
    take_id: int,
    script_diff: dict | None,
) -> None:
    """L2 异步完成后回写 script_diff，不覆盖 end_ts / status / notes。

    script_diff 传 dict，DAL 内部 json.dumps 存库；None 时写 SQL NULL。
    对应 SQL：UPDATE takes SET script_diff = ? WHERE take_id = ?
    """
```

**测试要求**：
- `update_take_l2_output(take_id, {"script_diff_summary": "ok", "line_matches": []})` → `get_take(take_id).script_diff` 等于传入 dict。
- `update_take_l2_output(take_id, None)` → `get_take(take_id).script_diff is None`。
- 传入不存在的 take_id 不抛错（UPDATE 0 rows，静默忽略；调用方负责保证 take_id 有效）。

**sqlite-schema 参考**：`takes` 表 `script_diff TEXT`（JSON 透明序列化，ref sqlite-schema v0.3.2 §3）。

#### 3.3.2 `insert_take_line_matches`（批量写入）

```python
def insert_take_line_matches(
    self,
    take_id: int,
    matches: list[dict],
) -> None:
    """批量写入 take_line_matches，单条失败记 WARNING 继续（逐条 try/except）。

    每个 match dict 含：
      - line_id: int        # script_lines.line_id（已由 caller 查出）
      - diff_type: str      # 'match' | 'substitution' | 'insertion' | 'deletion'
      - payload: dict       # 透传给 insert_take_line_match 的 payload 参数

    line_no == -1 的 insertion 行在 caller 侧跳过，不传入本方法（ref l2-pipeline D5）。
    """
```

**与现有 `insert_take_line_match` 的关系**：`insert_take_line_match(take_id, line_id, diff_type, payload)` 是单条写，本方法是 handler 内批量调用的封装，底层调单条方法。亦可在 `_write_line_matches` helper 内直接循环调单条方法而不新建 DAL 方法——由 backend agent 按代码整洁度自行决定，spec 不强制要求新建 `insert_take_line_matches`，允许 handler 内自行循环调 `insert_take_line_match`。

**测试要求**：
- 写入 2 条 line_matches → `dal.list_take_line_matches(take_id)` 返回 2 条。
- 某条 line_id 不存在（FK 违约）→ 该条跳过，其余条正常写入，记 WARNING 日志。

#### 3.3.3 `list_script_lines`

```python
def list_script_lines(
    self,
    script_id: int,
) -> list[dict]:
    """返回 script_id 下全部台词行，按 line_no 升序。

    每条 dict 含：
      line_no: int
      line_id: int      # script_lines 主键，insert_take_line_match 需要此值
      character: str | None
      text: str

    对应 SQL：
      SELECT line_no, line_id, character, text
      FROM script_lines
      WHERE script_id = ?
      ORDER BY line_no ASC
    """
```

**测试要求**：
- 插入 3 行 → `list_script_lines(script_id)` 返回 3 条，`line_no` 升序。
- 不存在的 script_id → 返回空列表，不抛错。

---

## §4 Stage 2：take.start / take.end handler

### 4.1 take.start handler

**输入事件**：`TAKE_START`，payload：`TakeStartPayload(scene_id, shot, start_ts)`（ref orchestrator-session-state v0.4 §2.2）。

**take_number 计算**：payload 不携带 take_number，handler 内通过 `dal.list_takes(scene_id=payload.scene_id)` 取已有 take 列表，`take_number = len(takes) + 1`。理由：DAL 是真相来源，内存计数器在重启后会丢失。

**行为序列**（同步）：

1. 调 `dal.set_active_scene(payload.scene_id)` 切换活跃场次（ref sqlite-schema §2.1 is_active 更新由 take.start 触发）。
2. 计算 take_number（见上）。
3. 调 `dal.start_take(scene_id=payload.scene_id, take_number=take_number, start_ts=payload.start_ts, shot=payload.shot)` 返回 `take_id`。
4. 调 `self.session.take_start(take_id=take_id, take_number=take_number, start_ts=payload.start_ts, shot=payload.shot)`。
5. 调 `self.publish(TAKE_CHANGED, TakeChangedPayload(take_id=take_id, status='tbd', take_number=take_number, scene_id=payload.scene_id, script_diff=None))`。

⚠ **本 spec 决策**：`take.start` 时 publish `take.changed` 通知前端 take 已开始。前端可据此更新状态栏（onset-llm-ux v1.2.1 §take.changed 用法）。

### 4.2 take.end handler

**输入事件**：`TAKE_END`，payload：`TakeEndPayload(end_ts)`（ref orchestrator-session-state v0.4 §2.2）。

**`session.take_id` 为 None 时的保护**：

```python
take_id = self.session.take_id
if take_id is None:
    logger.error("take.end received but session.take_id is None, skipping")
    return
```

**同步部分（take.end handler 主体，在 publish 调用栈内完成）**：

1. 取 `take_id = self.session.take_id`（None 保护见上）。
2. 调 `dal.end_take(take_id, end_ts=payload.end_ts, status='tbd', script_diff=None, notes=None)` 写结束时间戳。
3. 调 `self.session.take_end()`，`take_active` 设 False，`take_id` 保留。
4. 调 `self.publish(TAKE_CHANGED, TakeChangedPayload(..., status='tbd'))` 发第一次 `take.changed`（通知前端 take 已结束，L2 尚未完成）。
5. 若 `self._deps.llm_service is None` 或 `self._deps.l2_runner is None`：记 warning 日志，流程结束（纯 DAL-only 模式，1.E 测试场景）。
6. 否则：`asyncio.get_running_loop().create_task(_run_l2_background(take_id, scene_id, take_number))` 发起 fire-and-forget 后台任务。

**后台 L2 任务（`_run_l2_background`，定义为 Orchestrator 的 async 私有方法或内部 coroutine）**：

```python
async def _run_l2_background(
    self,
    take_id: int,
    scene_id: int,
    take_number: int,
) -> None:
    """后台 L2 执行，take.end 后 fire-and-forget 触发。"""
    try:
        # 1. 拉 ch1 全量 segment，组装 L2Input
        segments = self._deps.dal.list_segments(take_id, ch=1)
        transcript_segments = [
            {
                "speaker": s.speaker,
                "text": s.text,
                "start_frame": s.start_frame,
                "end_frame": s.end_frame,
            }
            for s in segments
        ]

        # 2. 拉当前场次最新 script_lines
        script_lines = self._build_script_lines(scene_id)

        # 3. 组装 previous_notes（见 §4.3）
        previous_notes = self._build_previous_notes(scene_id, exclude_take_id=take_id)

        # 4. 调 L2 Pipeline
        input_data = L2Input(
            take_id=take_id,
            scene_id=scene_id,
            take_number=take_number,
            transcript_segments=transcript_segments,
            script_lines=script_lines,
            previous_notes=previous_notes,
        )
        l2_output: L2Output = await self._deps.l2_runner(input_data, self._deps.llm_service)

        # 5. 写 takes.script_diff
        script_diff_dict = {
            "script_diff_summary": l2_output.script_diff_summary,
            "line_matches": [
                {"line_no": m.line_no, "diff_type": m.diff_type, "detail": m.detail}
                for m in l2_output.line_matches
            ],
        }
        self._deps.dal.update_take_l2_output(take_id, script_diff=script_diff_dict)

        # 6. 写 take_line_matches（line_no=-1 的 insertion 跳过，ref l2-pipeline D5）
        self._write_line_matches(take_id, scene_id, l2_output.line_matches)

    except (L2ParseError, asyncio.TimeoutError) as exc:
        logger.error("L2 failed for take_id=%d: %s", take_id, exc)
        # L2 失败：script_diff 保持 NULL，不写 take_line_matches

    except Exception as exc:
        logger.exception("unexpected error in _run_l2_background for take_id=%d", take_id, exc_info=exc)

    finally:
        # 无论成功或失败，都 publish 第二次 take.changed（让前端知道 L2 处理完毕）
        self.publish(TAKE_CHANGED, TakeChangedPayload(take_id=take_id, ...))
```

### 4.3 previous_notes 组装算法

ref l2-pipeline v0.1 §10 Q5：`previous_notes` 来源是同场次历史 take 的 `takes.script_diff["script_diff_summary"]`，而非完整 JSON。

组装步骤（实现在 `Orchestrator._build_previous_notes(scene_id, exclude_take_id)`）：

1. 调 `dal.list_takes(scene_id=scene_id)` 按 take_number 升序获取全部 take。
2. 排除 `take_id == exclude_take_id`（当前 take，其 script_diff 尚未写入）。
3. 过滤 `take.script_diff is not None`。
4. 从每个 take 的 `script_diff` dict 中取 `script_diff_summary`（str | None），跳过 None。
5. 取最后 3 条（`[-3:]`，最近的历史 take）。
6. 每条截断至 200 字符：`note[:200]`。
7. 返回 `list[str]`，可为空列表。

### 4.4 script_lines 查询

ref l2-pipeline v0.1 §3：`script_lines` 是「当前场次剧本行」，由 1.H caller 决定范围。

⚠ **本 spec 决策**：1.H 取当前场次**最新版本**全部台词行（无过滤），组装前检查总字符数上限 1000 字符；超限时截断（取前 N 行，从行号小的开始保留）。

实现（`Orchestrator._build_script_lines(scene_id)`）：
1. 调 `dal.get_latest_script(scene_id)` 得 `script_id`；若为 None 返回空列表（剧本未上传，L2 仍运行，输出空 line_matches）。
2. 调 `dal.list_script_lines(script_id)` → `list[dict]`（⚠ Lead 已拍板 Q3，此方法 1.H scope 内新增，签名见 §3.3.3）。
3. 每条 dict 含 `line_no / line_id / character / text`，直接传入 `L2Input.script_lines`。

### 4.5 take.changed 事件与 payload

1.E `events.py` 没有 `TAKE_CHANGED` 常量，1.H 需要新增。

⚠ **Lead 拍板（Q4）**：`TakeChangedPayload` 字段确定如下：

```python
# events.py 追加
TAKE_CHANGED = "take.changed"

@dataclass(frozen=True)
class TakeChangedPayload:
    """take.changed 的 payload，cover take.start / take.end / L2 完成三个时机。"""
    take_id: int
    scene_id: int
    take_number: int
    status: str          # 'tbd' | 'keeper' | 'ng' | 'hold'
    script_diff: dict | None  # L2 完成前为 None
```

字段说明：
- `take_id`、`scene_id`、`take_number`：下游（1.I WebSocket 推送、前端列表渲染）定位 take 需要的最小三元组。
- `status`：L2 完成前始终为 `'tbd'`，后续 NP / QP Pipeline 写入实际 status 后再发一次 `take.changed`（独立 ticket）。
- `script_diff`：L2 未完成时为 None；L2 成功时为含 `script_diff_summary` + `line_matches` 的 dict；L2 失败时仍为 None。

⚠ **本 spec 决策**：`take.changed` publish 发生两次：(1) take.end handler 同步阶段发第一次（`script_diff=None, status='tbd'`）；(2) L2 后台任务结束后发第二次（成功时携带 `script_diff`，失败时 `script_diff=None`）。前端根据两次更新均可渲染，第一次让 take 列表立即出现新条目，第二次携带 diff 报告。

take.start handler 同样 publish 一次 `take.changed`（§4.1 第 5 步），字段与 take.end 第一次相同（`script_diff=None, status='tbd'`），通知前端 take 已创建。

### 4.6 异步触发 L2（决策）

⚠ **本 spec 决策**：采用选项 B —— take.end handler 是同步函数，内部用 `asyncio.get_running_loop().create_task(...)` fire-and-forget。

选项评估：
- **选项 A**（整个 publish 链路改 async）：破坏 1.E 同步架构，17 条测试全部需要改造，代价不可接受。
- **选项 B（推荐）**：handler 同步返回，L2 task 在后台 event loop 上运行。1.E 测试不动；1.H 集成测试用 `pytest-asyncio` + `await asyncio.sleep(0)` 或 `loop.run_until_complete` 等待 task 完成。
- **选项 C**（同步内 `loop.run_until_complete`）：在 FastAPI async 上下文中调用会抛 `RuntimeError: This event loop is already running`，直接排除。

**选项 B 的前提条件**：take.end handler 被调用时必须有 running event loop。FastAPI 路由是 async 函数，1.I 调 `orchestrator.publish(TAKE_END, ...)` 时天然在 event loop 上；测试用 `pytest-asyncio`。如果在同步上下文（如某个非 async 的测试）中调 publish，`get_running_loop()` 会抛 `RuntimeError`——handler 会被 Orchestrator 的 exception isolation 捕获记日志，L2 不触发但不崩溃。

**background task 异常抓取**：`create_task` 返回的 `asyncio.Task` 需要挂 `add_done_callback` 打印异常，否则 asyncio 会在 task GC 时打 warning（`Task exception was never retrieved`）：

```python
task = loop.create_task(self._run_l2_background(take_id, scene_id, take_number))
task.add_done_callback(_log_task_exception)

def _log_task_exception(task: asyncio.Task) -> None:
    if not task.cancelled() and task.exception() is not None:
        logger.error("L2 background task raised exception", exc_info=task.exception())
```

实际上，`_run_l2_background` 内已对 L2 异常做 try/except，只有真正意外的 Exception 会漏到 task 层，`add_done_callback` 是二道防线。

⚠ **Lead 拍板（Q6）**：`asyncio.get_running_loop()` 在无 running loop 时抛 `RuntimeError`，由 Orchestrator exception isolation 捕获记日志，L2 不触发。不提供 `asyncio.run()` 降级。明确错误优于静默失败。Orchestrator 约定必须在 asyncio 事件循环内运行（FastAPI async route / pytest-asyncio 均满足此前提）。

### 4.7 错误处理

| 场景 | 处理方式 |
|---|---|
| `session.take_id is None` 时收到 `TAKE_END` | 记 ERROR 日志 + 早退，不写库，不 publish take.changed |
| `take.start` 时 `dal.start_take` 抛 `IntegrityError`（重复 take_number）| handler 内不捕获，由 Orchestrator.publish 的 exception isolation 记日志 + 继续其他 handler |
| `dal.end_take` 抛错 | 同上，exception isolation 处理，session.take_end() 仍调（确保 take_active=False，避免 ASR 继续写库） |
| L2 抛 `L2ParseError` | `_run_l2_background` 内 catch，记 ERROR，script_diff 保持 NULL，仍 publish 第二次 take.changed（status='tbd'）|
| L2 抛 `asyncio.TimeoutError` | 同上 |
| `dal.update_take_l2_output` 抛错 | `_run_l2_background` 的 `except Exception` 捕获，记 exception，仍在 finally publish take.changed |
| `dal.insert_take_line_match` 单条失败 | 记 WARNING，跳过该条，继续写其余 line_matches |

ref orchestrator-session-state v0.4 §4.1：handler exception isolation 风格——任一 handler 异常记日志 + 继续其余 handler，不向 publish 调用方传播。

---

## §5 Stage 3：集成测试

### 5.1 activate_scene 处理策略

`events.py` 中无 `SCENE_ACTIVATE` 事件常量（1.E spec §1.2 明确此为后续 ticket）。集成测试不通过 publish 事件来激活场次，而是：

- 直接调 `dal.create_scene(...)` 建场次行。
- 直接调 `session.activate_scene(scene_id)` 更新 SessionState（`session.scene_id = scene_id`）。
- `dal.set_active_scene(scene_id)` 由 take.start handler 内部调用（无需测试单独触发）。

这样绕过了 activate_scene 事件，聚焦在 1.H 的实际 scope（take handler + L2 触发），不越界到 1.I / 其他 ticket 的事件。

### 5.2 端到端集成测试流程

测试文件：`backend/tests/test_orchestrator_l2.py`

```
1. 建真实 DAL（tmp_path sqlite 文件）
2. 建真实 SessionState
3. 建 StubLLMService（StubClient 返回固定 L2 JSON）
4. dal.create_scene("Scene_3A") → scene_id
5. session.activate_scene(scene_id)（直接设 session.scene_id，绕过事件）
6. 用 create_orchestrator(dal, session, llm_service=stub_service) 建 Orchestrator
7. 注册 spy handler 监听 TAKE_CHANGED：captured_payloads = []
8. 发 TAKE_START 事件 → assert takes 表有新行（status='tbd'，end_ts=None）
9. 发 asr.final.ch1 × 2 → assert transcript_segments 有 2 条
10. 发 TAKE_END → assert takes.end_ts 非 None
   → assert spy_handler 收到第一次 take.changed（script_diff=None）
11. await asyncio.sleep(0.1)（等 L2 background task 完成）
   → assert spy_handler 收到第二次 take.changed（script_diff 非 None）
   → assert takes.script_diff 非 NULL（dal.get_take(take_id).script_diff 非 None）
   → assert take_line_matches 有记录（dal.list_take_line_matches(take_id) 非空）
```

**StubClient L2 JSON 示例**：

```json
{
    "script_diff_summary": "台词匹配，第1行完全正确。",
    "line_matches": [
        {"line_no": 1, "diff_type": "match", "detail": null}
    ]
}
```

需要预先在 script_lines 表写一行台词（`dal.insert_script` + `dal.insert_script_line`）以使 `list_take_line_matches` 有非空结果。

### 5.3 previous_notes 集成测试

验证 previous_notes 跨 take 传递：

1. 先跑 take 1（L2 写入 script_diff_summary）。
2. 跑 take 2，take.end 后 `_build_previous_notes` 应取到 take 1 的 summary。
3. 验证 L2 被调时的 prompt 包含历史偏差摘要（通过 mock `l2_runner` 或 SpyClient 捕获 messages）。

### 5.4 load_script 集成测试备忘

SP Pipeline ticket 未开始，1.H 集成测试里 script_lines 通过直接调 `dal.insert_script_line` 写入（fixture 注入，不走 SP Pipeline）。真正的 load_script 端到端测试（`SCRIPT_UPLOAD` 事件 → SP Pipeline → script_lines 写库）由 SP Pipeline ticket 负责。本 spec 不预设 SP Pipeline 接口，fixture 结构按 sqlite-schema v0.3.2 §6 ScriptLine dataclass 写。

---

## §6 测试设计

### 6.1 Stage 1 兼容性测试

- **无改动**：`backend/tests/test_orchestrator.py` 全部 17 条测试继续 pass，这是 Stage 1 重构的验收标准。
- 重构后立即跑全量 `pytest backend -q`，确认基线不退化。

### 6.2 Stage 2 take handler 单测

测试文件：`backend/tests/test_orchestrator_l2.py`（新建）

| 用例名 | 验证行为 |
|---|---|
| `test_take_start_creates_take_row` | publish TAKE_START → dal.get_take 有新行，status='tbd'，end_ts=None |
| `test_take_start_updates_session_state` | publish TAKE_START → session.take_active=True，session.take_id 非 None |
| `test_take_start_publishes_take_changed` | spy handler 收到 TAKE_CHANGED，take_id 与新建 take 一致 |
| `test_take_start_sets_active_scene` | publish TAKE_START → dal.get_active_scene_id() == payload.scene_id |
| `test_take_end_sets_end_ts` | publish TAKE_START 再 TAKE_END → dal.get_take.end_ts 非 None |
| `test_take_end_updates_session_take_active` | TAKE_END → session.take_active=False，session.take_id 保留 |
| `test_take_end_publishes_take_changed_immediately` | TAKE_END 同步阶段 spy 收到第一次 take.changed（script_diff=None） |
| `test_take_end_without_take_id_skips` | session.take_id=None 时 publish TAKE_END → 不写库，不 publish，记 ERROR 日志 |
| `test_take_end_without_llm_service_skips_l2` | llm_service=None 时 take.end handler → end_ts 写库，第一次 take.changed publish，但无第二次（不触发 L2） |
| `test_previous_notes_assembly` | 建 3 个已有 take（各有 script_diff），组装后 previous_notes 取最近 3 条，每条不超 200 字符 |
| `test_previous_notes_skips_null_script_diff` | 部分 take 无 script_diff 时，这些 take 跳过，不计入 previous_notes |

### 6.3 Stage 3 集成测试

| 用例名 | 验证行为 |
|---|---|
| `test_e2e_take_full_pipeline` | 全链路（§5.2 步骤），takes.script_diff 非 NULL，take_line_matches 有记录，第二次 take.changed 收到 |
| `test_e2e_l2_parse_error_still_publishes_take_changed` | StubClient 返回非法 JSON → L2ParseError → script_diff=NULL → 仍 publish 第二次 take.changed |
| `test_e2e_previous_notes_passed_to_l2` | take 2 时 previous_notes 含 take 1 的 summary（SpyClient 捕获 messages 验证 prompt 内容） |
| `test_e2e_asr_segments_missing_still_runs_l2` | 无 ch1 segments → L2 以空 transcript 运行，输出 line_matches=[]，写库成功 |

---

## §7 验收（development-plan §1.H 4 条）

对应 development-plan v0.2 §1.H 子任务：

| 验收条件 | 对应测试用例 |
|---|---|
| `take.end` → L2 → 写 takes.script_diff | `test_e2e_take_full_pipeline`（dal.get_take.script_diff 非 None） |
| publish `take.changed`（两次）| `test_take_end_publishes_take_changed_immediately` + `test_e2e_take_full_pipeline`（spy 收到第二次） |
| previous_notes 从历史 take 正确组装 | `test_previous_notes_assembly` + `test_e2e_previous_notes_passed_to_l2` |
| L2 失败时 `take.changed` 仍 publish，status='tbd' | `test_e2e_l2_parse_error_still_publishes_take_changed` |

---

## §8 不在本 ticket 范围

以下内容明确不在 1.H 实施，留后续 ticket：

- **L1 / L3 / SP / NP / QP Pipeline 实现**：独立 ticket。
- **ASR 真实集成**：1.H 测试用 `publish(ASR_FINAL_CH1, ...)` 模拟，不启动真实 whisper.cpp。
- **WebSocket 推送 take.changed**：1.I 在 Orchestrator.subscribe 注册转发 handler。
- **前端 Take Detail 接入**：1.L ticket。
- **handler class 拆分（3.2b）**：Lead 已拍板不在 1.H 实施，待 handler 总数 ≥ 5 时再评估（见 §3.2b）。
- **`SCENE_ACTIVATE` 事件**：属于 1.I 或独立 ticket，1.H 测试直接操作 session + dal 绕过事件。
- **manual.mark handler**：1.H 不实现，events.py 已有常量，handler 由专属 ticket 实现。

---

## §9 1.E 挖坑落地说明

orchestrator-session-state v0.4 §1.2 明确：「`take.start` / `take.end` handler 移出 1.E，留 1.H 一并实现」，「SessionState 提供 `take_start` / `take_end` 方法供 1.H handler 调，1.E 不调」。

本 spec §3-§4 定义的 Stage 1 factory 重构 + Stage 2 take handler 实现，就是 1.E 留下的「Orchestrator 扩展」坑的落地方案。1.H 完成后，Orchestrator 具备：
- 1.E 实现的：asr.final.ch1 / ch2 → insert_segment
- 1.H 实现的：take.start → start_take + session.take_start + take.changed；take.end → end_take + session.take_end + L2 触发 + take.changed（两次）

下游 1.I ticket 在此基础上 subscribe take.changed 等事件，通过 WebSocket 推送给前端，不需要再动 Orchestrator 核心逻辑。

---

## §10 待澄清问题

Q1（DAL `update_take_l2_output`）、Q2（handler class 拆分）、Q3（DAL `list_script_lines`）、Q4（TakeChangedPayload 字段）、Q6（asyncio 上下文保证）均已 Lead 拍板，决策内容已合并进正文各对应节。

**Q5（1.G 遗留 bug，已挂起）**：`_truncate_segments` 实现在单 segment text 字符数本身超 2500 时可能返回空列表（无任何 segment 能 fit）。Lead 拍板：此 bug 不在 1.H scope 内修复，等待 1.G v0.2 spec 发起补丁修。1.H 不修。
