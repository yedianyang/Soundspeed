# Spec: LLM Service 设计方案

版本：v1.0
日期：2026-05-25
状态：定稿，进入开发

---

## 背景

Soundspeed 当前有 6 个 LLM 调用点：

- **L1 Pipeline**：per-segment 清洗/解析（实时）
- **L2 Pipeline**：per-take 整合/摘要（批处理）
- **SP Pipeline**：剧本结构化（一次性）
- **NP Pipeline**：Note 处理（秒级）
- **QP Pipeline**：Query mini-session（用户等待）
- **Agent Pipeline**：场景初始化 Agent（多轮 tool_use 循环）

所有调用点共享同一个 Gemma 4 E4B 模型实例（Mac mini 本地部署）。如果各自独立加载模型，内存直接爆炸；如果并发推理，本地 GPU/ANE 会冲突或 OOM。

本 Spec 定义统一的 LLM Service 层，解决**单实例共享**与**并发冲突**问题。

---

## 设计目标

1. **单实例**：全系统只加载一次 Gemma 4 E4B
2. **无冲突**：同一时间只有一个推理任务在执行
3. **可插队**：实时任务（L1/QP）不被批处理任务（Agent/SP）阻塞
4. **可扩展**：新 Pipeline 接入成本低，只需定义 task_type + priority

---

## 架构总览

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         Soundspeed LLM Service                          │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  调用方 (6 个 Pipeline)                                                  │
│  ┌─────────┐  ┌─────────┐  ┌─────────┐  ┌─────────┐  ┌─────────┐     │
│  │  L1     │  │  L2     │  │   SP    │  │   NP    │  │   QP    │     │
│  │ P1-实时 │  │ P2-批量 │  │ P3-一次 │  │ P2-秒级 │  │ P1-查询 │     │
│  └────┬────┘  └────┬────┘  └────┬────┘  └────┬────┘  └────┬────┘     │
│       │            │            │            │            │            │
│  ┌─────────┐       │            │            │            │            │
│  │ Agent   │───────┴────────────┴────────────┴────────────┘            │
│  │ P3-循环 │                                                             │
│  └────┬────┘                                                             │
│       │                                                                 │
│       ▼                                                                 │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │              infer(task_type, prompt, priority)                  │   │
│  │                        统一入口                                   │   │
│  └──────────────────────────────┬──────────────────────────────────┘   │
│                                 │                                       │
│                                 ▼                                       │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │                     PriorityQueue 优先级队列                      │   │
│  │                                                                 │   │
│  │   Head ──→ ┌─────────┐   ┌─────────┐   ┌─────────┐   ┌────────┐  │   │
│  │            │ P1: L1  │ → │ P1: QP  │ → │ P2: NP  │ → │P3:Agt │  │   │
│  │            │  实时   │   │  查询   │   │  Note   │   │ Agent │  │   │
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
│  │                     Gemma 4 E4B                                  │   │
│  │              仅加载一次 · 单实例 · 内存 ~3-4GB                     │   │
│  │                                                                 │   │
│  │   推理执行: asyncio.to_thread(model.generate, prompt)            │   │
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

本地 Gemma 推理后端（llama.cpp / mlx-lm）**不支持并发**。使用 `asyncio.Lock()` 强制串行：

```python
self._lock = asyncio.Lock()

async def infer(self, prompt, task_type, priority):
    # 入队等待...
    async with self._lock:
        result = await asyncio.to_thread(self.model.generate, prompt)
        return result
```

**关键点**：`generate()` 是同步阻塞的 CPU/GPU 计算，必须用 `asyncio.to_thread()` 包裹，避免阻塞整个事件循环（影响 WebSocket 推送等）。

### 决策 3：优先级队列

不同 Pipeline 的实时性要求不同：

| 优先级 | Pipeline | 原因 |
|--------|----------|------|
| **P1** | L1, QP | 实时流式 / 用户在等 |
| **P2** | L2, NP | 批处理 / 秒级容忍 |
| **P3** | SP, Agent | 一次性 / 多轮循环 |

队列按 `(priority, timestamp)` 排序，同优先级 FIFO。

### 决策 4：Agent 多轮循环 · 主动释放锁

场景初始化 Agent 需要连续调用 LLM 5 次（parse → create → analyze → summarize → recommend）。**禁止一次性在锁内跑完全部 5 轮**。

正确做法：每轮作为**独立请求**重新入队：

```python
# Agent Pipeline
for step in ["parse", "create", "analyze", "summarize", "recommend"]:
    prompt = build_prompt(step, context)
    result = await llm.infer(prompt, task_type="agent_init", priority=3)
    context = process_result(result)
    # ← 锁在这里释放，L1 (P1) 可以插队
```

---

## 接口定义

### Python API

```python
class LLMService:
    async def infer(
        self,
        prompt: str,
        task_type: str,
        priority: int = 2,
        timeout: float = 30.0,
    ) -> str:
        """
        统一推理入口。

        Args:
            prompt: 完整 prompt（system + user 已拼接）
            task_type: 任务类型，用于映射生成参数
            priority: 1=实时, 2=普通, 3=批处理
            timeout: 最大等待时间（含排队 + 推理）

        Returns:
            LLM 生成的文本
        """
```

### Task Config 映射

```python
TASK_CONFIG = {
    "l1_clean": {
        "max_tokens": 150,
        "temperature": 0.1,
        "system": "你是一个录音转录清洗助手...",
    },
    "l2_summarize": {
        "max_tokens": 512,
        "temperature": 0.2,
        "system": "整合 take 信息，生成剧本 diff 和摘要...",
    },
    "script_parse": {
        "max_tokens": 2048,
        "temperature": 0.1,
        "system": "将剧本解析为结构化 JSON...",
    },
    "note_struct": {
        "max_tokens": 512,
        "temperature": 0.2,
        "system": "将录音师备注解析为结构化字段...",
    },
    "query_session": {
        "max_tokens": 1024,
        "temperature": 0.3,
        "system": "你是一个场记查询助手...",
    },
    "agent_init": {
        "max_tokens": 1024,
        "temperature": 0.3,
        "system": "你是场景初始化 Agent，可用工具：...",
    },
}
```

---

## 与现有架构的集成

### 文件位置

```
backend/
├── llm/
│   ├── __init__.py
│   ├── service.py          # LLMService 单例 + 队列 + 锁
│   ├── config.py           # TASK_CONFIG 映射
│   └── client.py           # 底层模型加载 (Gemma / llama.cpp / Ollama)
├── orchestrator.py         # 现有：事件路由
└── pipelines/
    ├── l1_segment.py       # 调用 llm.infer("l1_clean", ..., priority=1)
    ├── l2_take.py          # 调用 llm.infer("l2_summarize", ..., priority=2)
    ├── script_parse.py     # 调用 llm.infer("script_parse", ..., priority=3)
    ├── note_process.py     # 调用 llm.infer("note_struct", ..., priority=2)
    ├── query_session.py    # 调用 llm.infer("query_session", ..., priority=1)
    └── agent_init.py       # 5 轮循环，每轮 priority=3
```

### 集成点

1. **Orchestrator 现有事件路由不变**
2. **各 Pipeline 把直接调用 LLM 改为调用 `LLMService().infer()`**
3. **Agent Pipeline 内部自己管理多轮循环，每轮独立入队**

---

## 关键风险与对策

| 风险 | 影响 | 对策 |
|------|------|------|
| Gemma 推理慢（>1s/token） | L1 实时性崩 | 实测后若太慢，L1 降级为规则清洗，不走红线 |
| asyncio.to_thread 线程池满 | 系统假死 | 线程池设上限（`max_workers=2`），超限时排队 |
| Agent 5 轮总时间 >30s | 前端超时 | Agent 每轮返回 partial progress，前端显示进度条 |
| 多 worker 加载多份模型 | OOM | Hackathon 阶段单 worker；生产环境走 Ollama |

---

## 开放问题

1. **Gemma 具体推理后端**：llama-cpp-python / Ollama / mlx-lm？影响 Lock 是否必须
2. **L1 清洗是否真需要 LLM**：若 4B 模型推理太慢，L1 可能改回规则清洗
3. **Agent tool_use 格式**：Gemma 是否原生支持 function calling？不支持则改用 ReAct prompt 模式

---

## 验收标准

- [ ] `LLMService()` 多次实例化返回同一对象
- [ ] 并发调用 6 个 Pipeline，内存中只有一个 Gemma 进程
- [ ] L1 (P1) 请求可在 Agent (P3) 多轮之间插队
- [ ] 推理期间 WebSocket 推送不卡顿（事件循环未被阻塞）
