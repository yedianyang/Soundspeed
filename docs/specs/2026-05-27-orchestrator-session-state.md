
# Spec: Orchestrator + SessionState + 事件骨架 + ASR 订阅接口

版本：v0.4（codex P1/P3 review 收敛，待用户审批）
日期：2026-05-27
状态：草稿

变更记录：
- v0.4（2026-05-27 codex P1/P3 review 收敛）：(a) [P1] `asr.final.ch1/ch2` handler 不再无条件忽略 payload.take_id —— 改为「payload.take_id 非 null 时优先用、null 时回退 session.take_id；payload.take_id 与 session.take_id 都非 null 且不匹配时仍按 payload.take_id 写库 + 记 warning 日志」。目的是把跨 take 边界迟到的 ASR final segment（如 take 5 末尾「Cut」字幕在 take 6 已开始后才到达）正确归属到原 take，而不是丢失或污染下一 take 数据。take_active=False 时仍跳过（彻底没开始 take 时不建孤儿数据）。(b) [P3] 删 `:memory:` 测试备选 —— 当前 DAL 实现中 apply_migrations 用一条临时连接、DAL.__init__ 再开新连接，`Path(":memory:")` 会让 migrations 写到的临时表在新连接里不可见。spec 只留 `tmp_path` 文件路径方案。
- v0.3（2026-05-27 用户澄清两路语义后修订）：把 ch1 / ch2 两路在数据层对称化 —— ch2（录音师口头备注通道）改为按段入库写 `transcript_segments`（`ch=2`、`speaker=None`），不再用内存 `ch2_buffer` 暂存。下游 1.H NP Pipeline 输入改为 `dal.list_segments(take_id, ch=2)`。SessionState 删 `ch2_buffer` 字段及 `append_ch2` / `consume_ch2_buffer` 方法。本次修订覆盖 system-architecture v0.1 §4 中「SessionState 含 ch2_buffer」与事件路由表「asr.final.ch2 追加 ch2_buffer」两处设计；上游 spec 同步升版列入 Lead 下次协调。修订动机：前端时间轴混排展示需要 ch2 历史可查、take 跨重启不丢、导出场记单分栏，内存 buffer 无法满足。
- v0.2（2026-05-27 codex/lead 评审收敛）：(a) 收紧 1.E 范围 —— take.start / take.end handler 移出 1.E，留 1.H 一并实现（dev-plan §6 ticket 1.E 子任务原文未列 take handler，本 spec v0.1 越界）。SessionState 状态机方法定义保留，1.E 不调。(b) TakeStartPayload 加 scene_id + start_ts 字段：补齐 contract C3，避免下游 1.I/1.H 实现时缺字段。(c) AsrPartialPayload / AsrFinalPayload 加 is_partial 字段，回归 dev-plan §8 C1 原定 schema，不单边修订上游 contract。
- v0.1：初稿
owner：境熙
对应 ticket：1.E（dev-plan v0.2 §6）

依赖 spec（按权威级别）：
1. system-architecture v0.1（`docs/specs/2026-05-26-system-architecture.md`，§4 Orchestrator + SessionState）
2. development-plan v0.2（`docs/specs/2026-05-27-development-plan.md`，§6 ticket 1.E + §8 contract C1/C3）
3. sqlite-schema v0.3.2（`docs/specs/2026-05-27-sqlite-schema.md`，DAL 接口 = contract C2）
4. onset-llm-ux v1.1（`docs/specs/2026-05-22-onset-llm-ux.md`）

覆盖范围：1.E 实现的接口形状与行为。定义 `backend/core/events.py` 的事件类型常量与 payload schema，`backend/core/session.py` 的 SessionState 数据类与状态机操作，`backend/core/orchestrator.py` 的 pub/sub 接口（contract C1 + C3）与初版事件路由（含 `asr.final.ch1` → DAL insert_segment 的 handler）。本 spec 不实现 L2 Pipeline 触发（留 1.H）、不实现 FastAPI 端点（留 1.I）、不实现 ASR publish 侧（留 1.C）。

---

## 1. 1.E 范围与边界

### 1.1 范围内

- `backend/core/events.py`：事件类型常量 + payload dataclass（contract C1 + C3 全部事件 schema 由 1.E 作为定义方定清；下游 ticket 引用本文件）。
- `backend/core/session.py`：`SessionState` 数据类（在 system-architecture §4 字段基础上删 `ch2_buffer`，因 ch2 改为按段入库不再需要内存缓存），以及操作该状态的方法定义（take_start / take_end / activate_scene / load_script / register_observer / unregister_observer）。1.E 内无方法被本 ticket 的 handler 调用，全部供下游 ticket 调用。
- `backend/core/orchestrator.py`：
  - pub/sub 接口（contract C3 + contract C1 Python 端）：`subscribe(event_type, handler)` / `publish(event_type, payload)`。
  - 内置 handler（构造时自动注册）：
    - `asr.final.ch1` → 调 `dal.insert_segment(take_id, ch=1, speaker, text, start_frame, end_frame)`，仅当 `session.take_active == True`；take 未开始时跳过，记 debug 日志。
    - `asr.final.ch2` → 调 `dal.insert_segment(take_id=session.take_id, ch=2, speaker=None, text=payload.text, start_frame=payload.start_frame, end_frame=payload.end_frame)`，仅当 `session.take_active == True`；take 未开始时跳过，记 debug 日志。覆盖 system-architecture v0.1 §4 路由表中 ch2 不入库的旧设计，使 ch1 / ch2 两路在数据层对称（前端可按 start_frame 排序混排展示）。
- `backend/tests/test_orchestrator.py`：覆盖 §7 列出的测试入口。

### 1.2 范围外（明确不做，避免越界）

- **`take.start` handler / `take.end` handler**：1.E 只在 `events.py` 定义 `TakeStartPayload` / `TakeEndPayload`（contract C3 给经纬定义），不注册 handler。take handler 的完整行为（建 DAL take 行 + 切 `scenes.is_active` + 更新 SessionState + 触发 L2 + 写 takes 结束信息）跟 L2 Pipeline 设计耦合，整体留 1.H 一并实现。SessionState 提供 `take_start` / `take_end` 方法供 1.H handler 调，1.E 不调。
- `manual.mark` / `query.request` / `script.upload` 三个事件类型：1.E 在 `events.py` 定义常量与 payload schema，但不注册 handler，留下游 ticket（1.H 接 `manual.mark`、QP/SP Pipeline ticket 接 `query.request` / `script.upload`）。
- `asr.partial.ch1` / `asr.partial.ch2`：1.E 不内置 handler，仅在 `events.py` 定义事件常量与 payload 类型，等 1.I WS 接入时由 1.I 自行 `subscribe`。
- WS topic 转发（`asr.*` / `take.changed` / `presence` / `qp.answer.*`）：留 1.I。Orchestrator 仅 publish 内部事件，转 WS 是 1.I 注册外部订阅者的事。
- ASR publish 侧（`backend/asr/publisher.py`）：留 1.C。1.E 只定义被订阅的事件 schema 形状（contract C1 的 Python 端形状）。
- 多 session 并发：MVP 只有单 session 单实例，Orchestrator 持有一个 SessionState。多 session 留未来扩展。

### 1.3 不引入的依赖

- 不引入 asyncio / 协程：pub/sub 同步实现（详见 §4.1 决策依据）。
- 不引入第三方 pub/sub 库（pyee / blinker 等）：手写一个 dict[event_type, list[handler]] 字典足够。

---

## 2. backend/core/events.py

### 2.1 事件类型常量

事件类型用模块级字符串常量定义，命名形如 `ASR_FINAL_CH1 = "asr.final.ch1"`。字符串值与 contract C1 / WS topic 命名完全一致，便于 1.I 把内部事件直接映射到 WS topic。

```python
# ASR 事件（contract C1）
ASR_PARTIAL_CH1 = "asr.partial.ch1"
ASR_PARTIAL_CH2 = "asr.partial.ch2"
ASR_FINAL_CH1 = "asr.final.ch1"
ASR_FINAL_CH2 = "asr.final.ch2"

# Take 事件（contract C3：FastAPI 调用 publish）
TAKE_START = "take.start"
TAKE_END = "take.end"

# 其他事件（本 ticket 只定义常量，不注册 handler）
MANUAL_MARK = "manual.mark"
QUERY_REQUEST = "query.request"
SCRIPT_UPLOAD = "script.upload"
```

### 2.2 Payload dataclass

事件 payload 用 `@dataclass(frozen=True)` 定义，frozen 防止 handler 间互相篡改 payload。所有字段类型用 PEP 604 联合语法（`X | None`）。

| 事件 | Payload dataclass | 字段 |
|---|---|---|
| `asr.partial.ch1` / `asr.partial.ch2` | `AsrPartialPayload` | `text: str`、`start_frame: int`、`end_frame: int`、`speaker: str \| None`、`take_id: int \| None`、`is_partial: bool` |
| `asr.final.ch1` / `asr.final.ch2` | `AsrFinalPayload` | `text: str`、`start_frame: int`、`end_frame: int`、`speaker: str \| None`、`take_id: int \| None`、`is_partial: bool` |
| `take.start` | `TakeStartPayload` | `scene_id: int`、`shot: str \| None`、`start_ts: float` |
| `take.end` | `TakeEndPayload` | `end_ts: float` |
| `manual.mark` | `ManualMarkPayload` | `mark_type: str`、`note: str \| None`、`ts: float` |
| `query.request` | `QueryRequestPayload` | `connection_id: str`、`query: str` |
| `script.upload` | `ScriptUploadPayload` | `scene_id: int`、`raw_text: str` |

设计要点：
- `take_id` 字段在 ASR payload 中可空（v0.4 修订）：handler 内采用「payload.take_id 优先 + session.take_id 回退」策略 —— `payload.take_id` 非 null 时直接用它写库（即使 ≠ `session.take_id`，因为这通常意味着 ASR final 跨越了 take 边界、应归属原 take）；`payload.take_id` 为 null 时回退用 `session.take_id`。两者都非 null 且不匹配时记 warning 日志（debug 用）。take_active=False 时整体跳过（没活跃 take 不建孤儿 segment）。详见 §4.2 handler 表。
- `speaker` 字段统一可空：diarization 失败 / 未启用时为 None。schema 已允许 NULL（sqlite-schema v0.3.2）。
- `is_partial` 字段保留在 payload 内（dev-plan §8 contract C1 原定）：事件类型已能区分 partial/final，但前端 WS 客户端按单字段判断更省事，对 contract 兼容性也更稳。Publisher 按事件类型填值（partial 事件填 `True`，final 事件填 `False`）。
- `TakeStartPayload.start_ts` 由 1.I 端点处理层在收到 `POST /api/v1/take/start` 时填 `time.time()`；Orchestrator handler（1.H 实现）直接消费，不再取时间源。
- `TakeStartPayload.scene_id` 由 1.I 端点从 admin 前端请求 body 透传；1.H take.start handler 用 `payload.scene_id` 作为 take 归属场次，handler 内调 `dal.set_active_scene(payload.scene_id)` 同步 `scenes.is_active`（sqlite-schema §2.1 字段说明：is_active 由 Orchestrator 在 take.start 时更新）。
- Payload 与 DAL 数据类（`TranscriptSegment` 等）刻意分开：DAL 数据类是「库里有什么」，事件 payload 是「在线上传什么」，两者不应耦合。

---

## 3. backend/core/session.py

### 3.1 SessionState 数据类

在 system-architecture v0.1 §4 表的 9 个字段基础上删 `ch2_buffer`（v0.3 修订：ch2 改为按段入库，不再需要内存缓存），其余 8 个字段保留。**`take_start_ts` 字段同时记录到 SessionState 和 DAL.takes.start_ts**：DAL 是持久化真相，SessionState 是运行时缓存，两者初始一致。

```python
@dataclass
class SessionState:
    scene_id: int | None = None
    shot: str | None = None
    take_id: int | None = None
    take_number: int = 0
    take_active: bool = False
    take_start_ts: float | None = None
    script_loaded: bool = False
    active_connections: set[str] = field(default_factory=set)
```

### 3.2 状态机方法

| 方法 | 行为 | 调用方 |
|---|---|---|
| `take_start(take_id, take_number, start_ts, shot)` | 设 `take_id` / `take_number` / `take_start_ts`；`shot` 可为 None；`take_active=True` | Orchestrator `take.start` handler（1.H 实现） |
| `take_end()` | `take_active=False`；不清空 `take_id`（1.H 写 takes.end_ts 还要用） | Orchestrator `take.end` handler（1.H 实现） |
| `activate_scene(scene_id)` | 设 `scene_id`；不写 DAL（DAL 在 take.start 时连带处理） | 后续 ticket |
| `load_script()` | 设 `script_loaded=True` | 后续 ticket |
| `register_observer(connection_id)` / `unregister_observer(connection_id)` | 加/删 `active_connections` | 后续 ticket（1.I 调） |

设计要点：
- SessionState 不持有 DAL 引用：所有 DAL 写由 Orchestrator handler 调，SessionState 只管运行时缓存。
- 不做线程锁：MVP 假设 publish 串行（pub/sub 同步），无并发写 SessionState 风险。多线程进入后再加 `threading.Lock`。

---

## 4. backend/core/orchestrator.py

### 4.1 pub/sub 接口（contract C1 + C3）

```python
class Orchestrator:
    def __init__(self, dal: DAL, session: SessionState | None = None) -> None: ...
    def subscribe(self, event_type: str, handler: Handler) -> None: ...
    def publish(self, event_type: str, payload: object) -> None: ...
```

`Handler` 类型别名：`Callable[[object], None]`。payload 类型在 events.py 已定，handler 内部 cast / isinstance 判断。

**为什么同步而非 async**：

- Orchestrator 仅做事件分发与 DAL 写，无长 IO，sync 完全够用。
- FastAPI async handler 调 sync `publish` 没问题；ASR 线程调 sync `publish` 也没问题（DAL 自带 sqlite 连接，busy_timeout=5000 兜底并发）。
- L2/NP Pipeline（1.G/1.H）是 LLM 调用，确实异步，但走 LLMService.infer 的 async 接口，Orchestrator 内 publish `take.end` 不会卡。
- 引入 asyncio 反而要决定 event loop 归属、跨线程 publish 怎么传、handler 同步异步如何混用，复杂度 > 收益。
- 若后续需要 async handler，wrapper 加 `asyncio.create_task(coro)` 即可，不破坏当前 sync 接口形状。

**publish 行为**：

- 遍历该 event_type 注册的 handler 列表，逐个同步调用。
- 任一 handler 抛异常：**记日志（stdlib `logging`）+ 继续调用剩余 handler**，避免一个 handler bug 中断整条事件链。异常不向 publish 调用方传播。
- 未注册的 event_type publish：no-op，不抛错（前端 / 上游可能比 handler 注册早，不应崩）。
- 同一 event_type 注册多个 handler 时按 subscribe 顺序调用（dict[str, list[handler]] 列表追加，FIFO）。
- subscribe 不去重：同一 handler 重复 subscribe 同一 event_type 会被调多次（调用方自管，简化实现）。

### 4.2 内置 handler 注册

Orchestrator 构造时自动注册以下内置 handler（用户无需手动 subscribe，对外暴露 subscribe 是给 1.I/1.C 等下游订阅扩展）：

| event_type | handler 行为 |
|---|---|
| `asr.final.ch1` | 若 `session.take_active=False`：跳过，记 debug 日志。<br>否则确定 `target_take_id`：<br>· `payload.take_id` 非 null → 用 `payload.take_id`<br>· `payload.take_id` 为 null → 用 `session.take_id`<br>若 `payload.take_id` 与 `session.take_id` 都非 null 且不匹配：仍按 `payload.take_id` 写库 + 记 warning 日志（跨 take 边界迟到 segment）。<br>调 `dal.insert_segment(take_id=target_take_id, ch=1, speaker=payload.speaker, text=payload.text, start_frame=payload.start_frame, end_frame=payload.end_frame)`。 |
| `asr.final.ch2` | 同 ch1 的 take_id 选择逻辑。<br>调 `dal.insert_segment(take_id=target_take_id, ch=2, speaker=None, text=payload.text, start_frame=payload.start_frame, end_frame=payload.end_frame)`。ch2 不带 speaker（diarization 只跑 ch1）；前端按 start_frame 与 ch1 段混排展示。 |

**`asr.partial.*` 不注册内置 handler**：避免空 handler 占位污染日志。1.I 接入 WS 时自行 subscribe。

### 4.3 DAL 注入

- Orchestrator 构造 `__init__(self, dal: DAL, session: SessionState | None = None)`，必传 DAL。
- 测试一律用真 DAL + pytest `tmp_path` 夹具（`DAL(tmp_path / "test.db")`），与 test_dal.py 风格一致。**不要用 `DAL(Path(":memory:"))`**：当前 DAL 实现里 `apply_migrations` 用一条临时 sqlite 连接，DAL.__init__ 再开新连接，`:memory:` 数据库在两条连接间不共享，会导致 migrations 创建的表在 DAL 实例上不可见（v0.4 修订，codex P3 评审收敛）。

---

## 5. 文件清单与不引入新依赖

新建：
- `backend/core/__init__.py`（空）
- `backend/core/events.py`（事件常量 + payload dataclass）
- `backend/core/session.py`（SessionState + 状态机方法）
- `backend/core/orchestrator.py`（Orchestrator 类 + 内置 handler）
- `backend/tests/test_orchestrator.py`（测试入口见 §7）

不修改：
- `backend/db/dal.py`：保持 1.D 接口不变，1.E 只调用不修改。

引入：
- 仅 stdlib：`dataclasses` / `typing` / `collections.abc` / `logging`。无新第三方依赖。

---

## 6. 跨平台 / 跨模块兼容

- 不涉及音频设备、文件路径、子进程：跨平台问题无新增。
- DAL 已 1.D 跨平台测试通过，1.E 复用 DAL，不直接碰 sqlite。
- `time.time()` 取 `take_start_ts` 默认值（仅当 publish 未传 start_ts 时，handler 不主动取——start_ts 由调用方提供，避免 Orchestrator 引入隐式时间源）。

---

## 7. 测试入口（test_orchestrator.py）

### 7.1 pub/sub 基础

| 测试名 | 验证行为 |
|---|---|
| `test_subscribe_and_publish_calls_handler` | subscribe 后 publish 同事件，handler 被调一次，payload 透传 |
| `test_publish_unregistered_event_is_noop` | 未 subscribe 的 event_type publish 不抛错 |
| `test_multiple_handlers_called_in_subscribe_order` | 同 event_type 注册两个 handler，按 subscribe 顺序调 |
| `test_handler_exception_does_not_block_others` | 第一个 handler 抛异常，第二个仍被调，publish 调用方不见异常 |

### 7.2 内置 handler（asr.final.ch1 / asr.final.ch2）

| 测试名 | 验证行为 |
|---|---|
| `test_asr_final_ch1_writes_segment_when_take_active` | session.take_active=True + take_id 已设，publish `asr.final.ch1`（payload.take_id 与 session 同步）后 `dal.list_segments(take_id, ch=1)` 返回新片段、speaker 字段透传自 payload |
| `test_asr_final_ch1_skipped_when_take_inactive` | session.take_active=False 时 publish `asr.final.ch1` 不写库 |
| `test_asr_final_ch1_falls_back_to_session_take_id_when_payload_null` | payload.take_id=None，handler 用 session.take_id 写库 |
| `test_asr_final_ch1_uses_payload_take_id_when_provided` | payload.take_id=5、session.take_id=5，handler 用 payload.take_id 写库 |
| `test_asr_final_ch1_writes_to_payload_take_id_on_mismatch` | payload.take_id=5、session.take_id=6（迟到段跨边界场景），handler 按 payload.take_id=5 写库，且日志含 warning |
| `test_asr_final_ch2_writes_segment_when_take_active` | session.take_active=True 时 publish `asr.final.ch2` 后 `dal.list_segments(take_id, ch=2)` 返回新片段，speaker 列为 None |
| `test_asr_final_ch2_skipped_when_take_inactive` | session.take_active=False 时 publish `asr.final.ch2` 不写库 |
| `test_asr_final_ch1_and_ch2_share_timeline` | 同一 take 内交替 publish ch1 / ch2，list_segments(take_id) ORDER BY start_frame ASC 时两路按时间戳交错排列 |

### 7.3 SessionState 行为

| 测试名 | 验证行为 |
|---|---|
| `test_session_take_start_sets_fields` | take_start 调用后 take_id / take_number / take_start_ts / shot 写入，take_active=True |
| `test_session_take_end_keeps_take_id` | take_end 调用后 take_active=False、take_id 不清空（1.H 还要用） |
| `test_session_register_unregister_observer` | active_connections set 增删行为 |

SessionState 的 take_start / take_end 方法虽不被 1.E 的内置 handler 调用，但作为 contract 仍需测试覆盖其字段更新行为，供 1.H handler 实现时直接复用。

### 7.4 测试夹具策略

- 用 `tmp_path` + `DAL(Path(tmp_path / "test.db"))` 真 sqlite，与 test_dal.py 一致。
- conftest.py 不新增 fixture（现有 fixture 都是音频相关，与 1.E 无关）。orchestrator 测试在 test_orchestrator.py 内局部 fixture。

---

## 8. 与下游 ticket 的对接

| 下游 ticket | 1.E 提供的接口 | 1.E 不实现的部分 |
|---|---|---|
| 1.C（ASR publisher） | events.py 的 ASR_FINAL_CH1/CH2 常量 + AsrFinalPayload | publisher 怎么构造 payload（1.C 自己定） |
| 1.I（FastAPI + WS） | Orchestrator.publish（contract C3）+ Orchestrator.subscribe（让 1.I 订阅 asr.* / take.changed 转 WS） | take.changed 事件本 ticket 不 publish（1.H 才会 publish）、WS 转发 |
| 1.H（take.start / take.end handler + L2 + NP） | events.py 中 TakeStartPayload / TakeEndPayload + Orchestrator.subscribe + SessionState.take_start / take_end 方法 + transcript_segments 表 ch1 / ch2 数据可用 | take.start handler 行为（建 DAL take 行 + 切 is_active + 更新 SessionState）、take.end handler 行为（拉 ch1 → 触发 L2、拉 ch2 → 触发 NP，写 takes 结束信息 + publish take.changed）、L2 / NP Pipeline 实现。ch2 Pipeline 输入来自 `dal.list_segments(take_id, ch=2)`，不再走 SessionState |
| 1.F（LLMService） | 无直接对接 | — |

---

## 9. 开放问题

1. **`asr.partial.*` 是否在 1.E 内置 no-op handler 占位**：当前选不占位（§4.2 末段）。若 1.I 测试中发现「没有任何 handler 时 partial 事件 publish 触发 unregistered no-op 路径，日志噪音」再调整。
2. **publish 的线程安全**：MVP 单线程模型成立的前提是「ASR 也在主线程 publish」。1.C 接入时若 ASR 走子线程 publish，需要决定加锁与否（dict[event_type, list[handler]] 读多写少，handler list 在 subscribe 期可能并发改写）。**评审决议时拍**。
3. **`take.end` payload 是否要带 `take_id`**：当前不带（用 SessionState.take_id）。但若多 take 并发（未来扩展），就需要 payload 带 take_id 明确路由。MVP 单 session 单 take 不需要。
4. **上游 system-architecture spec 同步**：本 spec v0.3 覆盖了 system-architecture v0.1 §4 的两处设计（SessionState 字段表 ch2_buffer / 事件路由表 asr.final.ch2 追加 buffer）。Lead 后续协调升 system-architecture v0.2，删 ch2_buffer 字段并改 ch2 路由说明。1.E 开发不等上游升版。

---

## 10. TODO（开发期）

- [ ] Lead 评审本 spec → 用户审批 → 进开发
- [ ] 按 §7 写 test_orchestrator.py（TDD 红）
- [ ] 实现 events.py / session.py / orchestrator.py（TDD 绿）
- [ ] pytest + ruff + mypy 全过
- [ ] codex review + lead review
- [ ] simplify（如有可简化）
- [ ] 更新 dev-plan §6 ticket 1.E 状态 → Test
