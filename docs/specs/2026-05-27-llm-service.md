# Spec: LLMService 实施细化（ticket 1.F）

版本：v0.1
日期：2026-05-27
状态：草稿，待 Lead 评审

对接 ticket：1.F · feat: llm — LLMService 单例 + PQueue + Lock + Gemma client

---

## §1 范围与目标

本 spec 是对 `llm-service-design v1.1` 的实施细化。目标是填平「架构设计已定但实施时必须拍板的细节」，避免 1.F 开发过程中反复回头查多份文档拼答案。

**本 ticket（1.F）做什么：**

- `backend/llm/service.py`：LLMService 单例 + asyncio.PriorityQueue + asyncio.Lock + worker task
- `backend/llm/config.py`：TASK_CONFIG 映射（5 个 task_type，不含 `l1_clean`）
- `backend/llm/client.py`：LLMClient 协议 + GemmaClient 实现（llama-cpp-python 封装）
- `backend/tests/test_llm_service.py`：单元测试 + smoke 测试（带 skip marker）

**本 ticket 不做什么：**

- 不接 Pipeline 调用：1.G（L2）及后续 ticket 才接入
- 不接 Orchestrator：Orchestrator 通过 Pipeline 间接使用，不直接调 LLMService
- 不实现 `agent_init` Pipeline：`_reserved=True`，MVP 阶段 `infer` 入口直接 `NotImplementedError`
- 不做启动预加载：模型加载在首次 `infer` 调用时触发（lazy init），不在 FastAPI 启动时
- 不做监控 / 指标采集：RSS 监控、tps 监控留后续 ticket
- 不做热加载 / 量化档切换：量化档锁定 Q4_K_M（0.C 决议），切换留运维 ticket

---

## §2 与上下游 spec 的关系

本 spec 不重复上游 spec 已经写清的内容，只补实施空白。

| 上游 spec | 来自该 spec 的约定 | 本 spec 不重复的内容 |
|---|---|---|
| `llm-service-design v1.1`（`docs/specs/2026-05-25-llm-service-design.md`） | 架构总览、4 项设计决策、`infer` 基础签名、TASK_CONFIG 结构、验收 6 条（其中 4 条 v1.1 正文，2 条 v1.1 变更记录补充）、风险表、开放问题 1-4 | 不重写架构图、决策叙述、风险分析 |
| `llm-backend-selection v0.3`（`docs/specs/2026-05-27-llm-backend-selection.md`） | 后端框架（llama-cpp-python 0.3.x）、量化档（Q4_K_M）、配置参数（n_ctx=4096 / n_gpu_layers=-1 / seed=42）、benchmark 数据 | 不重复 benchmark 数字和选型推导 |
| `system-architecture v0.1`（`docs/specs/2026-05-26-system-architecture.md`）§6 | `infer` 签名（messages 列表格式、4 参数）、B1/B2/B4 三项决议、Pipeline-LLMService 调用关系 | 不重复 Orchestrator 结构和前后端协议 |
| `development-plan v0.2`（`docs/specs/2026-05-27-development-plan.md`）§1.F | 子任务清单、涉及文件列表、依赖关系（0.C / 0.D done 才解锁） | 不重复子任务描述 |

**本 spec 只填以下内容**：异常清单（§3）、队列元组与 worker 状态机（§4）、TASK_CONFIG 字段完整定义（§5）、LLMClient 协议签名（§6）、测试用例清单与 fixtures（§7）。

---

## §3 API 契约细化

### 签名

```python
async def infer(
    self,
    messages: list[dict],
    task_type: str,
    priority: int = 2,
    timeout: float | None = 30.0,
) -> str:
```

与 `llm-service-design v1.1` 签名的差异：v1.1 写的是 `timeout: float = 30.0`，本 spec 改为 `float | None`。`None` 表示「不超时，等到推理完成为止」，用于内部批处理场景。⚠ 本 spec 决策（v1.1 未明确 None 语义）。此差异须回写 v1.1，见 §10 Q1。

### 入参约束

- `messages`：非空列表，每个元素须有 `"role"` 和 `"content"` 键。MVP 阶段 `content` 只接受 `str`，传入非 str content 块不校验（交给 client 层透传，按 B4 决议）。
- `task_type`：必须是 `TASK_CONFIG` 的合法 key。不在 TASK_CONFIG 中则 `ValueError`。
- `priority`：1-3 整数，不在范围内则 `ValueError`。
- `timeout`：正浮点数或 `None`。传 0 或负数则 `ValueError`。

### 返回

LLM 生成的文本字符串，对应 `choices[0]["message"]["content"]`（client 层返回 dict，service 层 unwrap）。

### 异常清单

| 异常 | 触发条件 | 说明 |
|---|---|---|
| `ValueError` | `task_type` 不在 TASK_CONFIG，或 `priority` / `timeout` 参数非法 | 在入口立即抛，不入队 |
| `NotImplementedError` | `TASK_CONFIG[task_type].get("_reserved")` 为 `True`（当前为 `agent_init`） | 在入口立即抛，不入队。⚠ 本 spec 决策：在入队前拦截，不浪费队列槽位 |
| `asyncio.TimeoutError` | 排队 + 推理总耗时超过 `timeout` | `asyncio.wait_for` 包裹从入队到拿到结果的全过程（含等待锁）。`timeout=None` 时不抛 |
| `RuntimeError` | worker task 内部异常（client.create_chat_completion 崩溃）| worker 捕获异常，通过 `Future.set_exception` 回传给调用方协程，worker 本身不退出（见 §4 worker 异常处理） |
| `LookupError` | client 返回的 dict 缺少 `choices[0]["message"]["content"]` 字段 | service 层 unwrap 时抛，调用方可重试 |

---

## §4 内部结构

### 4.1 单例：模块级 instance + `get_service()` 工厂

v1.1 用 `__new__` 实现类单例。本 spec 改为模块级变量 + 工厂函数，原因：便于测试时通过 `_reset_service()` 清空 instance，不需要魔改类的 `_instance` 属性。⚠ 本 spec 决策。

```python
# backend/llm/service.py

_service: LLMService | None = None

def get_service() -> LLMService:
    global _service
    if _service is None:
        _service = LLMService()
    return _service

def _reset_service() -> None:
    """仅供测试使用，生产代码不调用。"""
    global _service
    _service = None
```

Pipeline 调用方统一用 `get_service().infer(...)` 而非 `LLMService().infer(...)`。

### 4.2 asyncio.PriorityQueue：元组结构与稳定性

队列元素是四元组：

```python
(priority: int, counter: int, fut: asyncio.Future[str], payload: _InferPayload)
```

- `priority`：1-3，数字越小优先级越高（Python heapq 最小堆语义）。
- `counter`：单调递增整数，由 `itertools.count()` 产生，初始化在 `LLMService.__init__` 中。相同 priority 时保证 FIFO，避免 `asyncio.Future` 对象因不可比较而抛 `TypeError`。⚠ 本 spec 决策（v1.1 写了「timestamp」，本 spec 改用 counter，理由：counter 在同一进程内严格单调，不受系统时钟回拨影响）。
- `fut`：调用方等待的 `asyncio.Future`，worker 完成后 `fut.set_result(text)` 或 `fut.set_exception(exc)`。
- `payload`：含 `messages / task_type / gen_kwargs` 的轻量 dataclass，不含 priority（priority 已提取到元组头）。

`_InferPayload` 定义：

```python
from dataclasses import dataclass

@dataclass
class _InferPayload:
    messages: list[dict]
    task_type: str
    gen_kwargs: dict  # 从 TASK_CONFIG 提取的生成参数
```

队列使用 `asyncio.PriorityQueue`（unbounded）。⚠ 本 spec 决策：不设队列上限，MVP 阶段单 worker 进程，背压由上游限流（Pipeline 调用频率天然有界），无需 bounded queue 保护。若未来多 Pipeline 高频并发，再引入 `maxsize` 和 `LLMQueueFullError`。

### 4.3 asyncio.Lock：串行化 client 调用

```python
self._lock = asyncio.Lock()
```

worker 在 `async with self._lock:` 内执行 `await asyncio.to_thread(self._client.create_chat_completion, ...)` 并 unwrap 结果。Lock 保证同一时刻只有一个推理任务到达 client 层（llama-cpp-python 不支持并发推理，见 llm-service-design v1.1 决策 2）。

### 4.4 asyncio.to_thread 包裹同步调用

```python
result_dict = await asyncio.to_thread(
    self._client.create_chat_completion,
    messages=payload.messages,
    **payload.gen_kwargs,
)
text = result_dict["choices"][0]["message"]["content"]
```

`create_chat_completion` 是同步阻塞 CPU/GPU 计算，必须在线程池内跑，否则阻塞整个 asyncio 事件循环。线程池默认 `ThreadPoolExecutor`，不额外限制 `max_workers`（推理任务串行，Lock 已保证同时只有一个线程在 client 层，池满不是瓶颈）。

### 4.5 worker task：启动与关闭

**启动时机（lazy）**：模型加载和 worker task 均延迟到首次 `infer` 调用时触发。`get_service()` 只创建 `LLMService` 对象；`GemmaClient` 实例化（即 5.3 GB 模型加载）和 worker 启动均不在 `__init__` 或 import 阶段发生。⚠ 本 spec 决策：避免 import 或应用启动时触发模型加载（测试隔离 + 进程冷启动不阻塞）。

**worker 只启动一次**：`infer` 入口检查 `self._worker_task is None`。asyncio 事件循环是单线程的，`infer` 入口在同一 event loop 里是顺序执行的，不会出现两个协程同时通过 `is None` 判断的竞态。显式注释说明此假设（不跨线程调用 `infer`）。⚠ 本 spec 决策。

Worker 是一个长运行 `asyncio.Task`，循环从队列取元素执行推理：

```python
async def _worker(self) -> None:
    while True:
        priority, counter, fut, payload = await self._queue.get()
        if fut.cancelled():
            self._queue.task_done()
            continue
        try:
            async with self._lock:
                result_dict = await asyncio.to_thread(
                    self._client.create_chat_completion,
                    messages=payload.messages,
                    **payload.gen_kwargs,
                )
            text = result_dict["choices"][0]["message"]["content"]
            fut.set_result(text)
        except Exception as exc:
            fut.set_exception(exc)
        finally:
            self._queue.task_done()
```

**关闭**：调用 `LLMService.aclose()` 时 `cancel()` worker task，等待 task 结束。已在队列里但未处理的 Future 一律 `set_exception(asyncio.CancelledError)`。若 worker 从未启动（lazy init 且 `infer` 从未被调用），`aclose()` 是 no-op（`self._worker_task is None` 则直接返回）。FastAPI lifespan 负责在应用关闭时调用 `aclose()`（超出 1.F 范围，由 1.F 提供接口，lifespan 集成在后续 ticket 完成）。

**worker 异常处理**：worker 捕获所有异常并通过 `fut.set_exception(exc)` 回传，worker 本身不退出（while True 继续）。只有 `asyncio.CancelledError` 允许穿透（触发 task 退出）。

**超时处理**：`infer` 方法内用 `asyncio.wait_for(fut, timeout=timeout)` 包裹等待。超时后 `fut.cancel()`；worker 循环下一轮检测到 `fut.cancelled()` 则跳过该任务。⚠ 本 spec 决策：超时抛 `asyncio.TimeoutError`，不返回部分结果——LLM 推理若被中断无法恢复到有意义的截断点，返回部分文本会让 Pipeline 解析失败，不如让调用方重试。

---

## §5 TASK_CONFIG 字段定义

`config.py` 中的 TASK_CONFIG 复用 `llm-service-design v1.1` 的定义（ref §「Task Config 映射」节），以下列出字段语义，不重复具体数值。

| 字段 | 类型 | 说明 |
|---|---|---|
| `max_tokens` | `int` | 最大生成 token 数，传入 `create_chat_completion` 的 `max_tokens` 参数 |
| `temperature` | `float` | 采样温度，0.0-1.0 |
| `top_p` | `float`（可选，默认 1.0） | nucleus sampling 截断阈值，v1.1 未列出但 client 接受 |
| `priority` | `int` | 推荐优先级，`infer` 调用方若不传 `priority` 参数则从此处取默认值 |
| `system` | `str` | system prompt 模板，Pipeline 可在运行时用 `str.format(**context)` 插值 |
| `_reserved` | `bool`（可选） | 存在且为 `True` 时，`infer` 入口立即抛 `NotImplementedError` |

`service.py` 的 `infer` 从 TASK_CONFIG 提取生成参数时，过滤掉 `priority` 和 `_reserved` 和 `system` 键（system prompt 已由调用方拼入 messages，不重复传 gen_kwargs）。⚠ 本 spec 决策：system prompt 由 Pipeline 在构建 messages 时插入（`{"role": "system", "content": ...}`），TASK_CONFIG 的 `system` 字段只作为模板参考，service 层不自动注入 system message，以保持 messages 的完整控制权在 Pipeline 侧。

---

## §6 client.py 抽象

### LLMClient 协议

```python
from typing import Protocol, runtime_checkable

@runtime_checkable
class LLMClient(Protocol):
    def create_chat_completion(
        self,
        messages: list[dict],
        **kwargs,
    ) -> dict:
        """
        同步执行 chat completion，返回 OpenAI 风格 dict。
        约定：返回 dict 中 choices[0]["message"]["content"] 为生成文本。
        """
        ...
```

与 `llm-service-design v1.1`「接口约束」节对齐：返回 `dict`（OpenAI 风格），不返回 `str`。`str` 是 service 层 unwrap 后的产物，client 层不 unwrap。见 §10 Q2（task 描述里写的 `complete(...) -> str` 与此不一致）。

### GemmaClient（生产实现）

```python
class GemmaClient:
    """llama-cpp-python 封装，实现 LLMClient 协议。"""

    def __init__(self, model_path: str, **llama_kwargs):
        from llama_cpp import Llama
        self._llm = Llama(model_path=model_path, **llama_kwargs)

    def create_chat_completion(self, messages: list[dict], **kwargs) -> dict:
        return self._llm.create_chat_completion(messages=messages, **kwargs)
```

加载参数（来自 0.C 选型 ref §11.3）：`n_ctx=4096`、`n_gpu_layers=-1`、`seed=42`、`verbose=False`。`model_path` 从环境变量 `GEMMA_MODEL_PATH` 读取，默认值 `models/gemma-4-E4B-it-Q4_K_M.gguf`。

`GemmaClient` 不暴露 `self._llm`（原生 `Llama` 对象），调用方只能通过 `create_chat_completion` 接口，保证后续替换为 Ollama 或 vLLM 时只改 `client.py`。

### StubClient（测试用）

```python
class StubClient:
    """确定性 stub，不加载模型，供单元测试 fixture 使用。"""

    def __init__(self, response: str = "stub response", delay: float = 0.0):
        self._response = response
        self._delay = delay

    def create_chat_completion(self, messages: list[dict], **kwargs) -> dict:
        if self._delay:
            import time
            time.sleep(self._delay)
        return {
            "choices": [{"message": {"content": self._response}}]
        }
```

测试 fixture 将 `StubClient` 注入 `LLMService._client`，绕开 GemmaClient 的 import 和模型文件依赖。

---

## §7 测试设计

### Fixtures

```python
@pytest.fixture
def service():
    """每个测试用例得到新鲜的 LLMService 实例（StubClient 注入）。"""
    _reset_service()
    svc = get_service()
    svc._client = StubClient(response="ok")
    yield svc
    _reset_service()

@pytest.fixture
def slow_service():
    """StubClient 延迟 0.5s，用于测试超时和锁串行。"""
    _reset_service()
    svc = get_service()
    svc._client = StubClient(response="ok", delay=0.5)
    yield svc
    _reset_service()
```

### 单元测试用例清单

以下用例全部用 StubClient，不加载真实模型，`pytest` 默认运行。

| 用例 | 测试内容 |
|---|---|
| `test_singleton` | 连续调用 `get_service()` 两次返回同一对象（`is` 判断） |
| `test_infer_returns_string` | 正常调用 `infer` 返回 str，内容与 StubClient 响应一致 |
| `test_unknown_task_type_raises` | `task_type` 不在 TASK_CONFIG 时 `infer` 抛 `ValueError` |
| `test_reserved_task_type_raises` | `task_type="agent_init"` 时 `infer` 抛 `NotImplementedError` |
| `test_invalid_priority_raises` | `priority=0` 或 `priority=4` 时抛 `ValueError` |
| `test_invalid_timeout_raises` | `timeout=0` 或 `timeout=-1.0` 时抛 `ValueError` |
| `test_priority_order` | 并发提交 P3 / P1 / P2 三个任务，StubClient delay 须 > 单次入队耗时（建议 0.05s），确保第一个任务执行期间后两个已完成入队，worker 第二次从队列取时必然看到 P1 在头。验证完成顺序为 P1 → P2 → P3 |
| `test_fifo_within_same_priority` | 相同 priority 下多个任务按入队顺序完成（FIFO） |
| `test_lock_serialization` | 并发 3 个 infer，用 StubClient delay 验证实际执行是串行的（总耗时 ≈ N × delay，非并行的 max(delay)） |
| `test_timeout_raises` | `timeout=0.1s`，StubClient delay 0.5s，期望抛 `asyncio.TimeoutError` |
| `test_timeout_none_no_raise` | `timeout=None`，StubClient delay 0.3s，正常返回 |
| `test_client_exception_propagates` | StubClient 抛 `RuntimeError`，`infer` 调用方收到同一 `RuntimeError` |
| `test_task_config_applied` | mock `create_chat_completion`，验证 gen_kwargs 包含 TASK_CONFIG 的 `max_tokens` / `temperature` |
| `test_system_prompt_not_in_gen_kwargs` | mock 验证 gen_kwargs 中不含 `system` / `priority` / `_reserved` 键 |
| `test_event_loop_not_blocked` | 并发跑 `infer`（StubClient delay 0.2s）和 `asyncio.sleep(0.05)`，验证 sleep 在 infer 运行期间正常完成（探针不被推迟超过 10ms） |

### 验收测试（4 条，ref llm-service-design v1.1 §验收标准）

| 验收条 | 对应用例 |
|---|---|
| `LLMService()` 多次实例化返回同一对象 | `test_singleton` |
| 并发调用 4 个 Pipeline，内存中只有一个 Gemma 进程 | smoke test（见下）|
| QP (P1) 可在 SP (P3) / Agent (P3) 多轮之间插队 | `test_priority_order` |
| 推理期间 WebSocket 推送不卡顿（事件循环不阻塞） | `test_event_loop_not_blocked` |

「内存中只有一个 Gemma 进程」验证属于进程级检查，在 smoke test 里跑（单元测试无法验证 GemmaClient 是否真被实例化一次）。

### Smoke 测试（`@pytest.mark.smoke`，默认 skip）

```python
@pytest.mark.smoke
async def test_real_gemma_infer():
    """真实 GemmaClient，需要模型文件在 GEMMA_MODEL_PATH。"""
    svc = get_service()  # GemmaClient 路径，不注入 stub
    result = await svc.infer(
        messages=[{"role": "user", "content": "ping"}],
        task_type="query_session",
    )
    assert isinstance(result, str)
    assert len(result) > 0
```

`conftest.py` 配置：`--smoke` flag 激活；未传 flag 时自动 skip。模型文件缺失时 skip（不 fail）。

---

## §8 验收

以下验收直接对齐 `llm-service-design v1.1` 的验收标准，无改动，在此列出作为 1.F 完成判据：

- `LLMService()` 多次实例化返回同一对象
- 并发调用 4 个 Pipeline（L2 / SP / NP / QP），内存中只有一个 Gemma 进程
- QP (P1) 请求可在 SP (P3) / 预留 Agent (P3) 多轮之间插队
- 推理期间 WebSocket 推送不卡顿（事件循环未被阻塞）
- `infer(messages=[...])` 接口接受标准 chat 消息列表，底层 Gemma chat 模板由 client.py 自动套用
- `agent_init` task_type 调用 raise `NotImplementedError`（MVP 不实现，接口预留）

---

## §9 不在本 ticket 范围

以下内容明确不在 1.F 实施范围，留后续 ticket：

- **FastAPI lifespan 集成**：`LLMService.aclose()` 接口由 1.F 提供，但在 FastAPI lifespan 中调用留 API 层 ticket
- **Pipeline 集成**：1.G（L2）起才有 Pipeline 调用 `get_service().infer(...)`
- **Orchestrator 直接使用 LLMService**：Orchestrator 通过 Pipeline 间接调用，不直连
- **多模态 content 块（image / audio）**：接口透传但 MVP 不验证，见 llm-service-design v1.1 开放问题 3
- **监控与指标**：RSS 监控、队列深度指标、tps 记录留运维 ticket
- **热加载 / 量化档切换**：量化档锁 Q4_K_M（0.C 决议），不在本 ticket 实现切换逻辑
- **Windows 验证（0.C.1）**：llama-cpp-python Windows wheel 验证未完成，待 0.C.1 ticket
- **`infer` 签名回写 llm-service-design v1.1**：timeout 类型差异（§10 Q1）解决后由 docs agent 更新

---

## §10 待澄清问题

**Q1**：`timeout` 参数类型：llm-service-design v1.1 写 `float = 30.0`，本 spec 改为 `float | None = 30.0`，需 Lead 确认后回写 v1.1 签名。

**Q2**：`LLMClient` 协议的方法名与返回类型：task 1.F 描述里写 `complete(messages, **kwargs) -> str`，llm-service-design v1.1 写的是 `create_chat_completion(...) -> dict`。本 spec 采用 v1.1 的 dict 签名（与 llama-cpp-python 原生接口名一致，减少适配层）。若 Lead 倾向暴露 `complete -> str`，需明确 unwrap 在 client 层还是 service 层，以及 Protocol 签名随之更新。

**Q3（已决，待 Lead 确认）**：模型加载时机——本 spec 已在 §4.5 钉定 lazy 策略：`GemmaClient` 实例化和 worker 启动均延迟到首次 `infer` 调用，`get_service()` / `LLMService.__init__` 不触发加载。理由：测试隔离 + 避免 import 或进程启动时意外加载 5.3 GB 模型。若 Lead 倾向 eager 加载（FastAPI lifespan 显式 warmup），需修改 §4.5 并提供 lifespan 调用点说明。

**Q4**：`top_p` 字段是否进 TASK_CONFIG？llm-service-design v1.1 的 TASK_CONFIG 示例未列 `top_p`，但 llama-cpp-python `create_chat_completion` 支持该参数。本 spec §5 标为可选字段（默认 1.0），不主动加进 v1.1 的配置，1.F 实现时按现有字段走。若后续质量调优需要，再加入 TASK_CONFIG 并更新 spec。
