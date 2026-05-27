# Orchestrator 对接说明（经纬专用）

更新时间：2026-05-27

---

## 1. 本文档目的

1.E（Orchestrator + SessionState + 事件骨架）已合并到 main，对应 PR #4，merge commit `2421a9b`。你现在可以开干 1.C（ASR publisher）和 1.I（FastAPI + WS）。

这份文档告诉你：Orchestrator 怎么构造、有哪些事件可以 publish / subscribe、payload 字段是什么、内置 handler 替你干了什么、你不该干什么，以及测试怎么写。

---

## 2. 模块位置与 import

```python
from backend.core.orchestrator import Orchestrator
from backend.core.session import SessionState
from backend.core.events import (
    ASR_PARTIAL_CH1, ASR_PARTIAL_CH2,
    ASR_FINAL_CH1, ASR_FINAL_CH2,
    TAKE_START, TAKE_END,
    MANUAL_MARK, QUERY_REQUEST, SCRIPT_UPLOAD,
    AsrPartialPayload, AsrFinalPayload,
    TakeStartPayload, TakeEndPayload,
    ManualMarkPayload, QueryRequestPayload, ScriptUploadPayload,
)
from backend.db.dal import DAL
```

源文件：

- `backend/core/events.py` — 事件类型常量 + payload dataclass
- `backend/core/session.py` — SessionState 数据类 + 状态机方法
- `backend/core/orchestrator.py` — Orchestrator 类 + 内置 handler

---

## 3. Orchestrator 构造与生命周期

```python
from pathlib import Path
from backend.db.dal import DAL
from backend.core.session import SessionState
from backend.core.orchestrator import Orchestrator

dal = DAL(Path("data/soundspeed.db"))
session = SessionState()
orch = Orchestrator(dal, session=session)
```

构造时 Orchestrator 会**自动注册**两个内置 handler：

- `asr.final.ch1` → 写 `transcript_segments`（ch=1，保留 speaker）
- `asr.final.ch2` → 写 `transcript_segments`（ch=2，speaker 强制 None）

你不需要手动 subscribe 这两个。你需要 subscribe 的是 `asr.partial.*` 转 WS（1.I 负责）。

`session` 参数可省略，Orchestrator 会自己 new 一个空 SessionState。但 1.I 里最好显式传，因为你需要在多处访问同一个 `session` 实例。

---

## 4. pub/sub API 形状

```python
# 注册 handler
orch.subscribe(event_type: str, handler: Callable[[object], None]) -> None

# 发布事件
orch.publish(event_type: str, payload: object) -> None
```

行为约定：

- 同步、FIFO。publish 完所有 handler 才返回。
- 任一 handler 抛异常：记 ERROR 日志，继续调后续 handler，不向 publish 调用方传播。
- 未注册的 event_type publish 是 no-op，不抛错。
- 同一 event_type 可 subscribe 多个 handler，按注册顺序调用。
- subscribe 不去重（同一 handler 重复 subscribe 会被调多次，自管）。

---

## 5. 9 个事件类型与 payload 字段

### ASR 事件（contract C1）

| 事件类型常量 | 字符串值 | Payload | 谁 publish | 谁 subscribe |
|---|---|---|---|---|
| `ASR_PARTIAL_CH1` | `"asr.partial.ch1"` | `AsrPartialPayload` | 1.C ASR publisher | 1.I（转 WS） |
| `ASR_PARTIAL_CH2` | `"asr.partial.ch2"` | `AsrPartialPayload` | 1.C ASR publisher | 1.I（转 WS） |
| `ASR_FINAL_CH1` | `"asr.final.ch1"` | `AsrFinalPayload` | 1.C ASR publisher | 内置 handler（写库）+ 1.I（转 WS） |
| `ASR_FINAL_CH2` | `"asr.final.ch2"` | `AsrFinalPayload` | 1.C ASR publisher | 内置 handler（写库）+ 1.I（转 WS） |

`AsrPartialPayload` 与 `AsrFinalPayload` 字段完全相同：

| 字段 | 类型 | 说明 |
|---|---|---|
| `text` | `str` | 转录文本 |
| `start_frame` | `int` | 片段起始帧（相对录音文件开头） |
| `end_frame` | `int` | 片段结束帧 |
| `speaker` | `str \| None` | diarization 输出说话人标签，ch2 传 None |
| `take_id` | `int \| None` | 当前 take 的 DB id，不知道就传 None |
| `is_partial` | `bool` | partial 事件填 True，final 事件填 False |

### Take 事件（contract C3）

| 事件类型常量 | 字符串值 | Payload | 谁 publish | 谁 subscribe |
|---|---|---|---|---|
| `TAKE_START` | `"take.start"` | `TakeStartPayload` | 1.I FastAPI 端点 | 1.H（未实现，占位） |
| `TAKE_END` | `"take.end"` | `TakeEndPayload` | 1.I FastAPI 端点 | 1.H（未实现，占位） |

`TakeStartPayload` 字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| `scene_id` | `int` | 当前 scene 的 DB id，从请求 body 透传 |
| `shot` | `str \| None` | shot 标识，可选 |
| `start_ts` | `float` | `time.time()`，由 1.I 端点在收到请求时填，不要让 Orchestrator 取 |

`TakeEndPayload` 字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| `end_ts` | `float` | `time.time()`，由 1.I 端点填 |

### 其他事件（1.E 只定义 schema，handler 留后续 ticket）

| 事件类型常量 | 字符串值 | Payload | 当前 handler |
|---|---|---|---|
| `MANUAL_MARK` | `"manual.mark"` | `ManualMarkPayload` | 无，留 1.H |
| `QUERY_REQUEST` | `"query.request"` | `QueryRequestPayload` | 无，留 QP Pipeline |
| `SCRIPT_UPLOAD` | `"script.upload"` | `ScriptUploadPayload` | 无，留 SP Pipeline |

---

## 6. 关于 ASR payload 的 `take_id` 字段

这个字段可空，背后有逻辑。内置 handler 的 take_id 选择策略：

1. `payload.take_id` 非 None → 直接用它写库
2. `payload.take_id` 为 None → 回退用 `session.take_id`
3. 两者都非 None 但不匹配 → 按 `payload.take_id` 写库 + 记 WARNING 日志（跨 take 边界迟到 segment 场景）
4. `take_active=False` → 整体跳过，不写库

**给经纬的建议**：1.C ASR publisher 能推断出当前 take（从 `session.take_active` + `session.take_id` 读，或从 1.I 端点发出的 TAKE_START 事件推断）就填上，填 None 也不会出错，handler 会用 session 兜底。跨 take 边界的迟到 final 段最好填 `payload.take_id` 准确值，让 Orchestrator 能把它归回原 take。

---

## 7. 1.C ASR publisher 怎么写

最简形态：

```python
import time
from backend.core.orchestrator import Orchestrator
from backend.core.events import (
    ASR_PARTIAL_CH1, ASR_FINAL_CH1,
    ASR_PARTIAL_CH2, ASR_FINAL_CH2,
    AsrPartialPayload, AsrFinalPayload,
)


class AsrPublisher:
    def __init__(self, orch: Orchestrator) -> None:
        self.orch = orch

    def on_whisper_partial_ch1(self, text: str, start_frame: int, end_frame: int) -> None:
        payload = AsrPartialPayload(
            text=text,
            start_frame=start_frame,
            end_frame=end_frame,
            speaker=None,       # partial 阶段 diarization 还没出结果
            take_id=None,       # partial 不写库，take_id 无所谓
            is_partial=True,
        )
        self.orch.publish(ASR_PARTIAL_CH1, payload)

    def on_whisper_final_ch1(
        self,
        text: str,
        start_frame: int,
        end_frame: int,
        speaker: str | None,
    ) -> None:
        payload = AsrFinalPayload(
            text=text,
            start_frame=start_frame,
            end_frame=end_frame,
            speaker=speaker,        # diarization 输出，可空
            take_id=self.orch.session.take_id,  # 从 session 读，也可传 None
            is_partial=False,
        )
        self.orch.publish(ASR_FINAL_CH1, payload)

    def on_whisper_final_ch2(self, text: str, start_frame: int, end_frame: int) -> None:
        payload = AsrFinalPayload(
            text=text,
            start_frame=start_frame,
            end_frame=end_frame,
            speaker=None,           # ch2 不做 diarization，固定 None
            take_id=self.orch.session.take_id,
            is_partial=False,
        )
        self.orch.publish(ASR_FINAL_CH2, payload)
```

ch2 的 `speaker` 固定传 None，内置 handler 也会强制存 None，传进去没有副作用，但语义上就别填了。

内置 handler 会自动写库，1.C **不需要自己调 DAL.insert_segment**。

---

## 8. 1.I FastAPI 端点怎么 publish take.start / take.end

```python
import time
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from backend.core.orchestrator import Orchestrator
from backend.core.events import TAKE_START, TAKE_END, TakeStartPayload, TakeEndPayload

router = APIRouter()


class TakeStartRequest(BaseModel):
    scene_id: int
    shot: str | None = None


class TakeEndRequest(BaseModel):
    pass


def get_orch() -> Orchestrator:
    # 注入单例，具体实现看你 app 的依赖注入方式
    ...


@router.post("/api/v1/take/start")
async def take_start(
    req: TakeStartRequest,
    orch: Orchestrator = Depends(get_orch),
) -> dict:
    payload = TakeStartPayload(
        scene_id=req.scene_id,
        shot=req.shot,
        start_ts=time.time(),       # 时间源在这里，不在 Orchestrator 内部
    )
    orch.publish(TAKE_START, payload)
    return {"ok": True}


@router.post("/api/v1/take/end")
async def take_end(
    orch: Orchestrator = Depends(get_orch),
) -> dict:
    payload = TakeEndPayload(end_ts=time.time())
    orch.publish(TAKE_END, payload)
    return {"ok": True}
```

注意：1.E 本身**没有注册 TAKE_START / TAKE_END handler**，留给 1.H 实现。你把端点收到的请求 publish 出去就够了，现在 publish 进去相当于发出去没人接，等 1.H 合并后 handler 才会生效。

---

## 9. 1.I WS 转发怎么 subscribe

```python
from fastapi import FastAPI
from backend.core.events import (
    ASR_PARTIAL_CH1, ASR_PARTIAL_CH2,
    ASR_FINAL_CH1, ASR_FINAL_CH2,
)

app = FastAPI()


@app.on_event("startup")
async def register_ws_forwarders() -> None:
    orch = get_orch_singleton()  # 你的单例获取方式

    def make_forwarder(topic: str):
        def forward(payload: object) -> None:
            # payload 是 frozen dataclass，dataclasses.asdict 可以直接序列化
            import dataclasses
            ws_manager.broadcast(topic=topic, data=dataclasses.asdict(payload))  # type: ignore[arg-type]
        return forward

    orch.subscribe(ASR_PARTIAL_CH1, make_forwarder("asr.partial.ch1"))
    orch.subscribe(ASR_PARTIAL_CH2, make_forwarder("asr.partial.ch2"))
    orch.subscribe(ASR_FINAL_CH1, make_forwarder("asr.final.ch1"))
    orch.subscribe(ASR_FINAL_CH2, make_forwarder("asr.final.ch2"))
```

`asr.partial.*` 在 1.E 没有任何内置 handler，1.I subscribe 是第一个也是唯一的 handler，直接转 WS 即可。

`asr.final.*` 已有内置 handler（写库），1.I 在此基础上再 subscribe 一个转 WS 的，两个 handler 都会跑，互不干扰。subscribe 顺序不影响写库逻辑（内置 handler 是构造时注册的，先注册先跑）。

---

## 10. SessionState 哪些字段你能读，不能写什么

可以读：

```python
session.take_id       # 当前 take 的 DB id，1.C 推断 payload.take_id 时用
session.take_active   # 当前是否在录 take
session.scene_id      # 当前活跃 scene id
```

**不要直接写 SessionState 字段**。所有写操作必须走对应 handler：take_start / take_end / activate_scene 的 handler 由 1.H 实现，你的 1.C / 1.I 只管 publish 事件，不要绕过 Orchestrator 手动改 session 字段。

---

## 11. 几个坑要避开

**不要绕过 Orchestrator 直接调 dal.insert_segment。** 所有 ASR 段落入库只能走 `orch.publish(ASR_FINAL_CH*)` 这条路，内置 handler 会处理 `take_active` 守门和 `take_id` 选择逻辑。你绕过去，这些守门就消失了，脏数据会悄悄进库。

**不要在 take_active=False 时期望 ASR final 写库。** 内置 handler 遇到 `take_active=False` 会直接跳过，记一条 debug 日志就没了。这是设计行为，不是 bug。take 没开始就不应该有 segment。

**isinstance 检查。** 内置 handler 内部 `assert isinstance(payload, AsrFinalPayload)`，类型传错会被 `publish` 的 try/except 吞掉，变成一条 ERROR 日志，不抛给你。1.C 务必传正确的 payload 类型。

**ch2 的 speaker 不要填有效值。** 即使你传了非 None 的 speaker，内置 handler 也会强制存 None（`force_speaker_none=True`，这是 v0.3 决定，ch2 是录音师备注通道，不做 diarization）。填了也不会报错，但值被丢弃，容易产生误解。

**pub/sub 是同步的。** FastAPI async handler 内调 sync `orch.publish` 没问题（会阻塞当前协程，但写库够快，不是问题）。ASR 子线程调 sync publish 也没问题（DAL 有 `busy_timeout=5000` 兜底）。如果 ASR 在高频子线程 publish 导致竞态，告诉境熙再加锁，MVP 阶段先跑通。

**不要用 `DAL(Path(":memory:"))`** 写测试。当前 DAL 实现里 `apply_migrations` 用临时连接，`:memory:` 在两条连接间不共享，migrations 建的表在 DAL 实例上不可见。用 `tmp_path` 文件路径，见第 12 节。

---

## 12. 测试参考

复用 `backend/tests/conftest.py` 里的 `tmp_dal` fixture（先看一眼 conftest 确认 fixture 名）。Orchestrator 测试可以在你自己的 `backend/tests/test_asr_publisher.py` 里局部建 fixture：

```python
import time
from pathlib import Path
import pytest
from backend.db.dal import DAL
from backend.core.session import SessionState
from backend.core.orchestrator import Orchestrator


@pytest.fixture
def tmp_orch(tmp_path):
    dal = DAL(tmp_path / "test.db")
    session = SessionState()
    return Orchestrator(dal, session), dal, session


def test_publisher_emits_final(tmp_orch):
    orch, dal, session = tmp_orch

    # 模拟 take 已开始
    scene_id = dal.create_scene(name="test-scene")
    take_id = dal.start_take(scene_id=scene_id, take_number=1, start_ts=time.time())
    session.take_start(take_id=take_id, take_number=1, start_ts=time.time(), shot=None)

    # 模拟 ASR publisher publish final
    from backend.core.events import ASR_FINAL_CH1, AsrFinalPayload
    payload = AsrFinalPayload(
        text="台词内容",
        start_frame=0,
        end_frame=16000,
        speaker="演员A",
        take_id=take_id,
        is_partial=False,
    )
    orch.publish(ASR_FINAL_CH1, payload)

    segments = dal.list_segments(take_id, ch=1)
    assert len(segments) == 1
    assert segments[0].text == "台词内容"
    assert segments[0].speaker == "演员A"


def test_asr_final_skipped_when_take_inactive(tmp_orch):
    orch, dal, session = tmp_orch
    # session.take_active 默认 False，不 start take

    from backend.core.events import ASR_FINAL_CH1, AsrFinalPayload
    payload = AsrFinalPayload(
        text="不该入库",
        start_frame=0,
        end_frame=16000,
        speaker=None,
        take_id=None,
        is_partial=False,
    )
    orch.publish(ASR_FINAL_CH1, payload)

    # 没有 take，也就没有 segments 可查，直接断言 take 没建
    assert session.take_active is False
    assert session.take_id is None
```

DAL 的具体方法名（`create_scene` / `start_take` / `list_segments` 等）以 `backend/db/dal.py` 实际接口为准，先查一眼再写测试。

---

## 13. spec 参考

- `docs/specs/2026-05-27-orchestrator-session-state.md`（1.E 实施 spec v0.4，权威来源）
- `docs/specs/2026-05-27-development-plan.md` §6 ticket 1.C / 1.I + §8 contract C1 / C3
- `docs/specs/2026-05-26-system-architecture.md` §10 REST + WS 端点表

注意：system-architecture v0.1 §4 有两处过时内容（SessionState.ch2_buffer 字段 / asr.final.ch2 不入库的旧路由），已被 1.E spec v0.3 覆盖，待 Lead 协调升 v0.2。看事件路由和 SessionState 字段以 1.E spec v0.4 的 §1.1 / §4.2 为准，不要看 system-arch §4 的旧表。

---

## 14. 有问题找谁

境熙 own 1.E（Orchestrator）、1.F（LLMService）、1.G（L2 Pipeline）、1.H（take handler + NP Pipeline）。

contract 字段不清楚、handler 行为有疑问、想改 events.py 里的 payload schema，先和境熙对齐一下。改契约必须同步更新 `docs/specs/2026-05-27-orchestrator-session-state.md`，不能只改代码。

---

pub/sub 本身没什么玄学，你 publish 进去，内置 handler 帮你写库，你 subscribe 出来转 WS。卡住了先看日志，`logging.DEBUG` 级别会有 handler 跳过的记录。
