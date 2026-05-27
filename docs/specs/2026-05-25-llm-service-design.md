# Spec: LLM Service 设计方案

版本：v1.1
日期：2026-05-25（v1.1 修订 2026-05-27）
状态：定稿，进入开发

变更记录：
- v1.1（2026-05-27）：落地 system-architecture v0.1 §6 三处修订与 0.C spike 选型。B1：删除 L1 Pipeline 与 `TASK_CONFIG["l1_clean"]`，L1 职责并入 L2。B2：`agent_init` 标 `_reserved: True`，MVP 不实现，接口预留。B4：`infer(prompt: str)` → `infer(messages: list[dict])`，工具调用走标准 messages 协议（多模态 content 块待后续启用）。新增「底层模型加载」节，钉定 llama-cpp-python + Gemma 4 E4B Q4_K_M + Metal（依据 0.C spike，见 `docs/specs/2026-05-27-llm-backend-selection.md`）。Lead 评审修订：A2/A3/A4/A5/A6 措辞与契约细化，A9 Gemma chat 模板兼容性提级到关键风险，TASK_CONFIG 各项加 priority 字段。
- v1.0（2026-05-25）：初稿定稿。PriorityQueue + asyncio.Lock + 单例 + asyncio.to_thread 串行架构。

依赖 spec：
- system-architecture v0.1（`docs/specs/2026-05-26-system-architecture.md`）§5/§6
- onset-llm-ux v1.1（`docs/specs/2026-05-22-onset-llm-ux.md`）
- llm-backend-selection v0.3（`docs/specs/2026-05-27-llm-backend-selection.md`，0.C spike 结论）

---

## 背景

Soundspeed 当前有 5 个 LLM 调用点 + 1 个预留：

- **L2 Pipeline**：per-take 整合/摘要（批处理，含原 L1 清洗职责）
- **SP Pipeline**：剧本结构化（一次性）
- **NP Pipeline**：Note 处理（秒级）
- **QP Pipeline**：Query mini-session（用户等待）
- **Agent Pipeline**：场景初始化 Agent（MVP 不实现，接口预留）

所有调用点共享同一个 Gemma 4 E4B 模型实例（Mac mini 16 GB 本地部署）。如果各自独立加载模型，内存直接爆炸；如果并发推理，本地 GPU/ANE 会冲突或 OOM。

本 Spec 定义统一的 LLM Service 层，解决**单实例共享**与**并发冲突**问题。

---

## 设计目标

1. **单实例**：全系统只加载一次 Gemma 4 E4B
2. **无冲突**：同一时间只有一个推理任务在执行
3. **可插队**：用户态任务（QP）不被批处理任务（SP / 预留 Agent）阻塞
4. **可扩展**：新 Pipeline 接入成本低，只需定义 task_type + priority
5. **原生多模态**：接口支持直接传 audio / image content 块（B4 决议）

---

## 架构总览

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         Soundspeed LLM Service                          │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  调用方（4 个 Pipeline + 1 预留）                                        │
│  ┌─────────┐  ┌─────────┐  ┌─────────┐  ┌─────────┐  ┌─────────┐     │
│  │  L2     │  │   SP    │  │   NP    │  │   QP    │  │ Agent   │     │
│  │ P2-批量 │  │ P3-一次 │  │ P2-秒级 │  │ P1-查询 │  │（预留） │     │
│  └────┬────┘  └────┬────┘  └────┬────┘  └────┬────┘  └────┬────┘     │
│       │            │            │            │            │            │
│       └────────────┴────────────┼────────────┴────────────┘            │
│                                 ▼                                       │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │           infer(messages, task_type, priority)                   │   │
│  │                       统一入口                                    │   │
│  └──────────────────────────────┬──────────────────────────────────┘   │
│                                 │                                       │
│                                 ▼                                       │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │                     PriorityQueue 优先级队列                      │   │
│  │                                                                 │   │
│  │   Head ──→ ┌─────────┐   ┌─────────┐   ┌─────────┐   ┌────────┐  │   │
│  │            │ P1: QP  │ → │ P2: L2  │ → │ P2: NP  │ → │ P3: SP │  │   │
│  │            │  查询   │   │  整合   │   │  Note   │   │ 剧本   │  │   │
│  │            └─────────┘   └─────────┘   └─────────┘   └────────┘  │   │
│  │                                                                 │   │
│  └──────────────────────────────┬──────────────────────────────────┘   │
│                                 │                                       │
│                                 ▼                                       │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │                     asyncio.Lock()                               │   │
│  │                   串行推理保护锁 (同一时间只有一个)                  │   │
│  └──────────────────────────────┬──────────────────────────────────┘   │
│                                 │                                       │
│                                 ▼                                       │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │                Gemma 4 E4B · llama-cpp-python · Metal            │   │
│  │      仅加载一次 · 单实例 · Q4_K_M · RSS ~5.3 GB · n_ctx=4096      │   │
│  │                                                                 │   │
│  │   推理执行: asyncio.to_thread(llm.create_chat_completion, ...)   │   │
│  │   (在线程池中跑，不阻塞主事件循环)                                 │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                                 │                                       │
│                                 ▼                                       │
│                          返回结果给调用方                                │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 核心设计决策

### 决策 1：单例模式

全系统通过 `LLMService()` 单例访问，模型在首次初始化时加载：

```python
class LLMService:
    _instance = None
    _initialized = False

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
```

**约束**：Hackathon 阶段后端开单 worker。若未来开多 worker（uvicorn `--workers N`），每个进程会独立加载一份模型。生产环境解决路径：模型服务化（Ollama / vLLM）。

### 决策 2：串行推理锁

llama-cpp-python（详见「底层模型加载」节，依据 0.C spike）**不支持并发推理**。使用 `asyncio.Lock()` 强制串行：

```python
self._lock = asyncio.Lock()

async def infer(self, messages, task_type, priority):
    # 入队等待...
    async with self._lock:
        result = await asyncio.to_thread(
            self._llm.create_chat_completion,
            messages=messages,
            **self._gen_kwargs(task_type),
        )
        return result["choices"][0]["message"]["content"]
```

**关键点**：`create_chat_completion()` 是同步阻塞的 CPU/GPU 计算，必须用 `asyncio.to_thread()` 包裹，避免阻塞整个事件循环（影响 WebSocket 推送等）。0.C spike 在 M1 Max 上实证：6 路 mock 并发下 P1 任务可在 P3 任务之间插队，事件循环不被阻塞（**N=1 硬件、mock prompt，1.F 真实负载下需复验**）。

### 决策 3：优先级队列

不同 Pipeline 的实时性要求不同：

| 优先级 | Pipeline | 原因 |
|--------|----------|------|
| **P1** | QP | 用户在等查询响应 |
| **P2** | L2, NP | 批处理 / 秒级容忍 |
| **P3** | SP, （预留）Agent | 一次性 / 多轮循环 |

队列按 `(priority, timestamp)` 排序，同优先级 FIFO。

### 决策 4：Agent 多轮循环 · 主动释放锁（预留）

Agent Pipeline MVP 不实现（B2 决议），TASK_CONFIG 仍保留条目以稳定接口。落地时遵循下列约束：

场景初始化 Agent 需要连续调用 LLM 5 次（parse → create → analyze → summarize → recommend）。**禁止一次性在锁内跑完全部 5 轮**。

正确做法：每轮作为**独立请求**重新入队：

```python
# Agent Pipeline（预留实现示意）
for step in ["parse", "create", "analyze", "summarize", "recommend"]:
    messages = build_messages(step, context)
    result = await llm.infer(messages, task_type="agent_init", priority=3)
    context = process_result(result)
    # ← 锁在这里释放，QP (P1) 可以插队
```

---

## 接口定义

### Python API

```python
class LLMService:
    async def infer(
        self,
        messages: list[dict],
        task_type: str,
        priority: int = 2,
        timeout: float = 30.0,
    ) -> str:
        """
        统一推理入口。

        Args:
            messages: 标准 chat 消息列表，
                      [{"role": "system|user|assistant|tool",
                        "content": str}]。
                      MVP 阶段 content 只支持 str。多模态 content 块（image / audio）
                      待启用，schema 与 dispatcher 行为见开放问题 3。
            task_type: 任务类型，用于映射生成参数（见 TASK_CONFIG）。
                      推荐 priority 由 task_type 决定（见决策 3 优先级表），
                      调用方原则上不应改写。
            priority: 1=用户态, 2=普通, 3=批处理 / 预留循环。
                      与 task_type 的对应关系是 honor system，由调用方保证。
            timeout: 最大等待时间（含排队 + 推理）。

        Returns:
            LLM 生成的文本（assistant message 的 content）。

        Raises:
            NotImplementedError: 当 task_type 在 TASK_CONFIG 中标 _reserved=True
                （当前为 agent_init）时抛出。调用方若想提前判断，可读
                `TASK_CONFIG[task_type].get("_reserved", False)`。
        """
```

**Why messages 而非 prompt**（B4 决议）：

- 工具调用 / function calling 与 messages role 协议绑定，str prompt 无法自然表达 tool_use 往返。
- 原生多模态（audio / image）走 content block 标准，不再让调用方拼字符串。
- chat 模板由底层 backend 套用（llama-cpp-python 的 `create_chat_completion` 内置 Gemma chat 模板），调用方不关心 `<start_of_turn>` 这种 token。

### Task Config 映射

```python
# 推荐 priority 由 task_type 决定，调用方原则上不应覆盖：
#   l2_take=2, script_parse=3, note_struct=2, query_session=1, agent_init=3
TASK_CONFIG = {
    "l2_take": {
        "max_tokens": 512,
        "temperature": 0.2,
        "priority": 2,
        "system": "整合 take 信息，生成剧本 diff 和摘要...",
    },
    "script_parse": {
        "max_tokens": 2048,
        "temperature": 0.1,
        "priority": 3,
        "system": "将剧本解析为结构化 JSON...",
    },
    "note_struct": {
        "max_tokens": 512,
        "temperature": 0.2,
        "priority": 2,
        "system": "将录音师备注解析为结构化字段...",
    },
    "query_session": {
        "max_tokens": 1024,
        "temperature": 0.3,
        "priority": 1,
        "system": "你是一个场记查询助手...",
    },
    "agent_init": {
        "_reserved": True,
        "max_tokens": 1024,
        "temperature": 0.3,
        "priority": 3,
        "system": "你是场景初始化 Agent，可用工具：...",
    },
}
```

`infer(messages, task_type, priority=2)` 的 `priority` 参数允许调用方临时提级（如紧急查询），但默认应取 `TASK_CONFIG[task_type]["priority"]`。

`_reserved: True`（B2 决议）：MVP 不实现，dispatcher 收到该 task_type 直接 raise `NotImplementedError`，但配置项保留，方便 Pipeline 后续解锁时不改 TASK_CONFIG schema。

---

## 底层模型加载

模型加载层在 `backend/llm/client.py`，封装 llama-cpp-python 调用细节，对 `service.py` 暴露同步 `create_chat_completion(messages, **kwargs)` 接口。

### 后端 + 量化档（0.C spike 定）

| 项 | 值 | 依据 |
|---|---|---|
| 推理框架 | `llama-cpp-python` 0.3.x prebuilt wheel | macOS arm64 wheel 自带 Metal，无需重编译；Windows 有 CPU/CUDA wheel。wheel 同时附带多模态支持库 libmtmd，但 MVP 不启用，等开放问题 3 解锁 |
| 模型 | `unsloth/gemma-4-E4B-it-GGUF`，`gemma-4-E4B-it-Q4_K_M.gguf` | 文件 4.6 GB，加载后 RSS ~5.3 GB |
| 后端 | Metal（macOS）/ CUDA 或 CPU（Windows，待 0.C.1 验证） | `n_gpu_layers=-1` 全卸载 |
| 上下文 | `n_ctx=4096` 起步 | 6 个 task_type 在该上限内安全，长剧本场景未来可调到 8192 |
| 随机性 | `seed=42`（默认）/ 由调用方覆盖 | |

完整 benchmark 数据与 Q4/Q6 对照见 `docs/specs/2026-05-27-llm-backend-selection.md` §11。

### 16 GB Mac mini 预算

Mac mini 16 GB 是目标部署设备。粗略预算：

- macOS 系统：~4 GB
- 后端 + ASR small + 音频缓冲 + 杂项：~2 GB
- 安全余量：~0.5 GB
- **LLM 实际可用预算**：~10 GB

Q4_K_M 5.3 GB → 余 ~4.7 GB，含 KV cache 与 n_ctx 扩展余地。Q6_K 7.3 GB 临界，不选。完整推导见 0.C spec §11.2。

### 接口约束

`client.py` 不暴露 llama-cpp-python 原生 `Llama` 对象，只提供包装的 `create_chat_completion(messages: list[dict], **kwargs) -> dict`。原因：后续若需替换为 Ollama 或 vLLM 兜底，只改 `client.py` 一文，不影响 `service.py` 与 Pipeline。

---

## 与现有架构的集成

### 文件位置

```
backend/
├── llm/
│   ├── __init__.py
│   ├── service.py          # LLMService 单例 + 队列 + 锁 + dispatcher
│   ├── config.py           # TASK_CONFIG 映射
│   └── client.py           # 底层模型加载（llama-cpp-python 封装）
├── core/
│   ├── orchestrator.py     # 事件路由
│   └── session_state.py
└── pipelines/
    ├── l2_take.py          # llm.infer(messages, task_type="l2_take", priority=2)
    ├── script_parse.py     # llm.infer(messages, task_type="script_parse", priority=3)
    ├── note_process.py     # llm.infer(messages, task_type="note_struct", priority=2)
    ├── query_session.py    # llm.infer(messages, task_type="query_session", priority=1)
    └── agent_init.py       # 预留，5 轮循环，每轮 priority=3（MVP 不实现）
```

**B1 决议**：不建 `l1_segment.py`。原 L1 per-segment 清洗职责并入 L2 在 take 结束后统一处理。实时段 ASR 文本直接 publish，不走 LLM。

### 集成点

1. **Orchestrator 事件路由不变**
2. **各 Pipeline 把直接调用 LLM 改为调用 `LLMService().infer(messages, ...)`**
3. **Agent Pipeline 落地时自管理多轮循环，每轮独立入队**

---

## 关键风险与对策

| 风险 | 等级 | 影响 | 对策 |
|------|------|------|------|
| **Gemma 4 chat 模板与 OpenAI tools 协议兼容性未验证** | **关键未验证假设** | 若 llama-cpp-python `create_chat_completion` 对 Gemma 4 不能正确套 chat 模板或不支持 `tools` 参数，整个 messages 协议（B4 决议）失效，需退回 ReAct prompt 模式重做接口层 | 1.F 起步第一周即写最小冒烟测试（`messages=[{"role":"user","content":"ping"}]` + 一个 tool schema），跑通再展开 |
| QP 推理慢（>2s） | 一般 | 用户体感卡 | 0.C spike 实测 query_session 长度 RSS ~5.3 GB / decode 53 tps，~1s 内出结果；若 1.F 实测退化，prompt 精简 + max_tokens 收紧 |
| asyncio.to_thread 线程池满 | 一般 | 系统假死 | 线程池设上限（`max_workers=2`），超限时排队 |
| Agent 5 轮总时间 >30s | 预留场景 | 前端超时 | Agent 落地时每轮返回 partial progress，前端显示进度条 |
| 多 worker 加载多份模型 | 部署 | OOM | Hackathon 阶段单 worker；生产环境走 Ollama |
| 16 GB Mac mini 内存撑满 | 一般 | swap / 死锁 | 量化档锁 Q4_K_M（0.C 决议），n_ctx 不轻易扩；多模态推理后续按需启用，监控 RSS |

---

## 开放问题

1. **量化档复审**：Q4_K_M 在函数调用 / 结构化 JSON schema 合规率是否够用？1.F 实测后若退化，升 Q5_K_M（未测，预计 RSS ~6.3 GB，仍在 16 GB 预算内）。
2. **n_ctx 上限**：SP 长剧本是否需要 8192+？KV cache 增长按层估算，需在 1.F 真实剧本数据下测一次内存峰值。
3. **多模态启用时机**：onset-llm-ux v1.1 拍照剧本流程在第二阶段，是否需要原生 image content 块？mmproj 文件（unsloth repo 已有 mmproj-F16.gguf）待启用时再补加载逻辑。
4. **Gemma 函数调用格式**：Gemma 4 是否原生支持 OpenAI 风格 `tools` 协议？llama-cpp-python `create_chat_completion` 的 `tools` 参数支持度待 1.F 落地时实测。不支持则 Agent Pipeline 走 ReAct prompt 模式。

---

## 验收标准

- [ ] `LLMService()` 多次实例化返回同一对象
- [ ] 并发调用 4 个 Pipeline（L2 / SP / NP / QP），内存中只有一个 Gemma 进程
- [ ] QP (P1) 请求可在 SP (P3) / 预留 Agent (P3) 多轮之间插队
- [ ] 推理期间 WebSocket 推送不卡顿（事件循环未被阻塞，0.C spike 已证）
- [ ] `infer(messages=[...])` 接口接受标准 chat 消息列表，底层 Gemma chat 模板由 client.py 自动套用
- [ ] `agent_init` task_type 调用 raise `NotImplementedError`（MVP 不实现，接口预留）
