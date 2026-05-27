# Spec: Soundspeed MVP 开发计划

版本：v0.2
日期：2026-05-27
状态：定稿，进入开发

变更记录：
- v0.2（定稿 2026-05-27）：按「经纬整条音频生产链 + 境熙 L2 基座/Orchestrator」重排第一阶段；新增 speaker diarization spike + 实现 ticket；TranscriptSegment 与 DAL 接口加 speaker 字段；第二阶段及以后留白等第一阶段跑通后再拆。境熙起手 ticket 锁 0.E（schema spec），先解锁 1.D 实现路径；0.C/0.D/0.F 在 0.E 提交后再启。新增 P3 优先级语义（设计预留 / 未来可能性 / 不进 MVP），同步 Notion 任务清单 database。
- v0.1：初稿

依赖 spec：
- system-architecture v0.1（`docs/specs/2026-05-26-system-architecture.md`）
- onset-llm-ux v1.1（`docs/specs/2026-05-22-onset-llm-ux.md`）
- llm-service-design v1.0（`docs/specs/2026-05-25-llm-service-design.md`）
- audio-input-layer v0.3（`docs/specs/2026-05-20-audio-input-layer.md`）

覆盖范围：把 system-architecture v0.1 的待实现项拆为可执行 ticket，标 owner、依赖、验收。同时定义双人协作的工作流、ticket 在 Notion 上的存放方式、跨人对接点（contract boundary）。本 doc 是「开发指令书」，所有 ticket 在此先有定义、再到 Notion 落库。

---

## 1. 背景与目标

system-architecture v0.1 把后端模块边界、ownership、Pipeline 清单、9 表范围、REST/WS 端点表都钉好了。但还没回答：

- 「谁先动谁后动？」
- 「两个人怎么分工不撞车？」
- 「每个模块的实现要拆到多细？」
- 「前端 mock 怎么逐步替换成真接口？」

本 doc 回答这四个问题。

MVP 第一阶段目标：跑通 `mic → ASR + speaker diarization → Orchestrator → L2 → DB → 前端`。admin UI 能录一条 take，看到双声道带 speaker 标签的实时转录，take 结束后看到 L2 整合摘要写回前端 take 列表。

---

## 2. 双人协作约定

### 分工方式：workflow 切片，不是模块切片

不按 `asr / llm / db / api / frontend` 拆 owner，而是按一条端到端 workflow 切给一个人，每人对自己的 workflow 端到端负责（含前端接入）。

**第一阶段**：

- **经纬 own 音频生产链**：音频采集 → VAD → whisper.cpp 双路转录 → speaker diarization（ch1 内多演员区分） → 结构化 `TranscriptSegment`（含 speaker 字段）→ publish 给 Orchestrator → admin transcript 面板接入。同时 own FastAPI 启动 + WS 连接管理 + take.start/end 端点（前端要用）。
- **境熙 own L2 基座 + Orchestrator 调度**：DAL + schema + Orchestrator + SessionState + 事件骨架 + LLMService 单例 + L2 Pipeline + take.end → L2 → 写 takes 表 + admin take 控制按钮 + take 列表/详情前端接入。

「共享基建」概念取消，所有模块按 workflow 归属：经纬 own backend/audio + backend/asr + backend/api；境熙 own backend/db + backend/core + backend/llm + backend/pipelines。前端按界面元素归属，跟着对应 workflow 走。

**第二阶段及以后**：Note 输入流程 / 拍照剧本流程 / QP / 共享视图 / 导出 / Agent 预留接口，owner 待第一阶段跑通后再拆。

### 跨人对接点 = contract boundary

第一阶段中跨人的接口必须先在 doc 里钉死，先有 contract，后有实现。完整清单见第 8 节。简要列：

- ASR 事件 schema 含 `speaker` 字段（经纬 publish，境熙订阅，1.E Orchestrator 定 pub/sub 接口形状）
- DAL `transcript_segments` 接口含 `speaker` 字段（境熙提供，经纬调用）
- LLMService.infer 签名（境熙定，所有 Pipeline 用）
- Orchestrator 事件 publish 接口（境熙定，经纬的 API 层调用，把 take.start/end REST → 事件）
- WS topic `take.changed` payload（境熙 publish，经纬的前端订阅）

### 前端「立即接入」原则

前端 admin UI mock 已经在主分支（见 `docs/frontend-admin-ui-handoff.md`），不是装饰品。每个后端 ticket 完成定义为：**后端代码 + 测试通过 + 前端对应面板接入真后端 + 浏览器手动验证 happy path 跑通**。

这意味着每个 ticket 的子任务清单里必须包含「接前端」步骤。不接前端 ≠ 完成。

### TDD 与 commit

完整规则见 CLAUDE.md「开发流程」节，本 doc 不重复。要点：

- 每个 ticket 先写测试 commit（红），再写实现 commit（绿）。
- commit 前缀与 ticket 类型对齐：`feat / fix / spike / docs / chore`。
- 跨模块 / 改契约 / 新模块的 ticket，开工前 owner 起一份简短 spec 到 `docs/specs/`，Lead 评审后才动手。本 doc 已覆盖阶段 0-1 的契约，单 ticket 内的 spec 视情况补。

### 跨平台兼容性（Windows + macOS 一致）

Soundspeed 必须在 Windows 和 macOS 上行为一致。所有 ticket 的实现与选型都要满足下列约束：

- **所有 spike（0.A / 0.C / 0.G）**：选型方案必须在两个平台都跑通且行为一致。任一平台不通则方案否决，选兜底。实验 README 必须分别记录两个平台的运行结果。
- **依赖选型偏好**：优先选纯 Python / pip 可装 / 自带 wheel 的方案。涉及 native 编译（whisper.cpp / pyannote 的 torch 依赖）的，验证两个平台 wheel 都可用，否则文档化构建步骤。
- **路径处理**：一律用 `pathlib.Path`，禁止硬编码 `/` 或 `\` 分隔符。
- **subprocess 调用**：禁止假设 POSIX 行为，shell 命令必须 cross-platform，必要时分平台 dispatch。
- **音频设备**：device source 在 Windows 用 WASAPI / DirectSound，macOS 用 CoreAudio，封装层统一抽象。
- **模型权重路径**：`models/` 下用相对路径，下载脚本提供 PowerShell + Bash 两版。
- **CI / 验收**：1.M milestone 必须在两个平台各跑一次完整 happy path 才能签收。

某个 ticket 跨平台行为不一致 → 在 ticket 评论里标 `[CROSS-PLATFORM]` 升给 Lead，不能合并。

---

## 3. Ticket 与 Notion 工作流

### 存放位置

所有 ticket 平铺在 Notion 「任务清单」database（`collection://366cd7bb-f433-8047-8498-000b3b240dcc`），不分子 page、不建新 db。

### 命名格式

```
<阶段编号> <类型>: <模块> — <一句话动作>
```

类型枚举：`feat / fix / spike / docs / chore`，与 commit 前缀对齐。

例：

- `0.A spike: asr — whisper.cpp Python 集成`
- `1.D feat: db — schema.sql + DAL + migrations`
- `0.D docs: 升 llm-service-design 到 v1.1`

阶段编号进标题方便排序与互相引用。Owner / 依赖进正文第一段（见下方模板）。

### Notion 字段映射

| 字段 | 取值 | 含义 |
|---|---|---|
| Name | 见命名格式 | ticket 标题 |
| Owner | multi_select：境熙 / 经纬 | 谁负责。跨人 ticket 两个都勾，正文写明各自分工 |
| 优先级 | P0 / P1 / P2 / P3 | P0 = 阶段 0 + 阶段 1；P1 = 第二阶段；P2 = 第三阶段及以后；P3 = 设计预留 / 未来可能性 / 不进 MVP |
| 状态 | Backlog / Todo / Test / Done | Backlog 未排上 → Todo 本周做 → Test 开发完待联调/审查 → Done 合到 main |
| Date | 可选 | 计划完成日期 |

### Ticket 正文模板

```
**阶段**：0.A
**Owner**：经纬
**依赖**：无
**涉及文件**：
- `experiments/2026-05-27-whisper-cpp-bindings/`
- `docs/specs/2026-05-27-asr-service.md`（产出）

**验收标准**：
- 三种绑定方式（pywhispercpp / whisper-cpp-python / subprocess）在当前环境跑通至少一种
- 选型结论写到 ASR Service spec 草稿
- 实验 README 留痕，结论同步到 docs/

**子任务**：
- [ ] pywhispercpp 装 + 跑 hello world
- [ ] whisper-cpp-python 装 + 跑 hello world
- [ ] subprocess 直接调 whisper.cpp 二进制
- [ ] 对比加载时间、调用开销、异常处理
- [ ] 写实验 README + 结论留痕到 docs/

**对接点**：产出物 → 0.B（ASR Service spec）
```

### Backlog → Done 状态流

1. **Backlog**：在 doc 里有，但本周不打算做。
2. **Todo**：本周/下周要做，owner 自己拉到这。
3. **Test**：开发完成，测试本地全过，等联调或 quality agent 审查。
4. **Done**：合并到 main，关闭。

quality agent 不写测试，但在 Test → Done 之前做 code review + 跑全量 `pytest`。

---

## 4. 阶段拆分总览

```
阶段 0：解锁前置（spec / spike / docs 同步）
        必须在阶段 1 前完成。

阶段 1：第一阶段 —— 跑通 audio → ASR + diarization → L2 → DB → 前端
        经纬：音频采集 + VAD + whisper.cpp 双路 + speaker diarization +
              结构化输出 publish + FastAPI 启动 + take 端点 + admin
              transcript 面板。
        境熙：DAL + Orchestrator + SessionState + LLMService 单例 +
              L2 Pipeline + take.end 路由 + admin take 控制/列表/详情。
        Milestone：admin UI 能录一条 take，看到双声道带 speaker 标签的
        实时转录，take 结束 ≤10s 后 take 列表出现 L2 摘要。

第二阶段及以后：留待第一阶段跑通后再拆
        包含：Note 输入 / 拍照剧本 / QP / 共享视图 / 导出 /
              Agent 预留接口（B2 决议，MVP 不实现）。
```

每个阶段的 ticket 详细清单见第 5-6 节。第二阶段说明见第 7 节。

---

## 5. 阶段 0：解锁前置

| 编号 | 类型 | 标题 | Owner | 依赖 | 产出 |
|---|---|---|---|---|---|
| 0.A | spike | asr — whisper.cpp Python 集成 | 经纬 | — | 实验 README + 选型结论 |
| 0.B | spec | asr — ASR Service 独立 spec（含 speaker 字段） | 经纬 | 0.A, 0.G | `docs/specs/<date>-asr-service.md` |
| 0.C | spike | llm — Gemma 推理后端选型 | 境熙 | — | 实验 README + 选型结论 |
| 0.D | docs | 升 llm-service-design 到 v1.1 | 境熙 | — | spec 更新（B1/B2/B4 三处修订） |
| 0.E | spec | db — 9 表 schema 独立 spec（transcript_segments 含 speaker） | 境熙 | — | `docs/specs/<date>-sqlite-schema.md` |
| 0.F | docs | 同步上游文档 + Notion 流程图 | 境熙（可委托 Claude docs agent） | — | 4 处上游 doc + Notion 前五流总图修订 |
| 0.G | spike | asr — speaker diarization 选型 | 经纬 | — | pyannote / WhisperX / NeMo 对比 + 推荐 |

### 0.A · spike: asr — whisper.cpp Python 集成

**Owner**：经纬。**依赖**：无。**产出**：实验 README，选型结论留痕到 `docs/`。

子任务：

- [ ] 在 `experiments/2026-05-27-whisper-cpp-bindings/` 建实验目录
- [ ] pywhispercpp 装 + 跑 hello world（中文音频 10s 测试）
- [ ] whisper-cpp-python 装 + 跑 hello world
- [ ] subprocess 直接调 whisper.cpp 二进制（兜底方案）
- [ ] 对比加载时间、调用开销、异常处理、流式 vs 一次性
- [ ] 写实验 README，给出选型推荐 + 理由

### 0.B · spec: asr — ASR Service 独立 spec

**Owner**：经纬。**依赖**：0.A、0.G。**产出**：`docs/specs/<today>-asr-service.md`。

子任务：

- [ ] `TranscriptSegment` 字段最终确认（含 `speaker` 字段，在 system-architecture v0.1 第 7 节草案基础上）
- [ ] `transcribe(segment) → TranscriptSegment` 签名确认
- [ ] `transcribe_raw(audio, sr) → str` 签名确认（dispatcher 退路）
- [ ] VAD 模型选型（webrtcvad / silero / 其他）
- [ ] speaker diarization 集成方式（依赖 0.G 选型）
- [ ] ASR 事件 publish schema（partial.ch1 / partial.ch2 / final.ch1 / final.ch2 含 speaker）—— **对应 contract C1**（见第 8 节）
- [ ] spec 提交 Lead 评审

### 0.C · spike: llm — Gemma 推理后端选型

**Owner**：境熙。**依赖**：无。**产出**：实验 README，选型结论同步到 0.D。

子任务：

- [ ] 在 `experiments/2026-05-27-gemma-backend-bench/` 建实验目录
- [ ] llama-cpp-python 装 + Gemma 4 E4B 推理 hello world
- [ ] mlx-vlm 装 + Gemma 4 E4B 推理 hello world
- [ ] Ollama 装 + Gemma 4 E4B 推理 hello world
- [ ] 对比：加载时间、推理 RTF、多模态（audio / image）原生支持、asyncio.to_thread 集成难度
- [ ] 写实验 README，给出选型推荐 + 理由

### 0.D · docs: 升 llm-service-design 到 v1.1

**Owner**：境熙。**依赖**：无（但建议在 0.C 之后顺手做，可补底层 backend 选型）。**产出**：`docs/specs/2026-05-25-llm-service-design.md` v1.1。

子任务：

- [ ] B1：删 `TASK_CONFIG["l1_clean"]` 条目
- [ ] B2：`agent_init` 标 `_reserved: True`
- [ ] B4：`infer(prompt: str)` → `infer(messages: list[dict])`，更新接口段落
- [ ] 补「底层模型加载」小节，引用 0.C 选型结论
- [ ] 升版号 v1.1，补变更记录

### 0.E · spec: db — 9 表 schema 独立 spec

**Owner**：境熙。**依赖**：无。**产出**：`docs/specs/<today>-sqlite-schema.md`。

子任务：

- [ ] 9 张表字段细则（基于 system-architecture v0.1 第 8 节范围）
- [ ] `transcript_segments` 表加 `speaker` 字段（来自 ASR diarization 输出）
- [ ] 主键 / 外键 / 索引 / 约束
- [ ] FTS5 配置（`script_lines` 表）
- [ ] 迁移脚本骨架（v1 = 初始化）
- [ ] 草拟 DAL 接口签名（不实现，定接口形状）—— **对应 contract C2**（见第 8 节）

### 0.F · docs: 同步上游文档 + Notion 流程图

**Owner**：境熙（可委托 Claude docs agent）。**依赖**：无。**产出**：4 处 doc + 1 张 Notion 图修订。

子任务：

- [ ] `CLAUDE.md` 架构图：Cactus ASR → whisper.cpp + speaker diarization；删「take 边界检测」节点；加 Orchestrator 层
- [ ] `CLAUDE.md` 四个模块契约第 1、2 条：Cactus → whisper.cpp + diarization；ownership 表加 `backend/core/`
- [ ] `docs/structure.md`：Cactus → whisper.cpp；`models/` 行更新权重（含 diarization 模型）
- [ ] `docs/specs/2026-05-20-audio-input-layer.md`：「直接下游」改 ASR 引擎名；cactus-asr-probe 实验加「历史参考」注
- [ ] `docs/specs/2026-05-22-onset-llm-ux.md`：UX 流程图删独立 L1 节点；补 speaker diarization 节点
- [ ] Notion「与AI头脑风暴的UX形态」末张「6'· 前五流总图」：删 L1 + Agent 节点，加 speaker diarization 步骤

### 0.G · spike: asr — speaker diarization 选型

**Owner**：经纬。**依赖**：无。**产出**：实验 README，选型推荐写入 0.B spec。

子任务：

- [ ] 在 `experiments/2026-05-27-speaker-diarization/` 建实验目录
- [ ] 准备测试音频：2-3 个 speaker 的对白片段（30 秒 + 2 分钟两组）
- [ ] pyannote.audio 装 + 跑 hello world（HF token 准备）
- [ ] WhisperX 装 + 跑 hello world（whisper + diarization 一体）
- [ ] NeMo speaker diarization 装 + 跑 hello world（兜底）
- [ ] 对比：加载时间、推理 RTF、speaker 标签准确率（人耳手测）、与 whisper.cpp 集成难度
- [ ] 写实验 README，给出选型推荐 + 理由
- [ ] 结论同步到 0.B 的 ASR Service spec

---

## 6. 阶段 1：第一阶段实现

按 owner 切两条并行 lane，最后通过 1.M milestone 联调。

### 经纬侧 · 音频生产链 + API 启动 + admin 实时面板

| 编号 | 类型 | 标题 | Owner | 依赖 |
|---|---|---|---|---|
| 1.A | feat | asr — ASRService 双路转录（VAD + whisper.cpp） | 经纬 | 0.B |
| 1.B | feat | asr — speaker diarization 接入（ch1 多 speaker 标注） | 经纬 | 0.G, 1.A |
| 1.C | feat | asr — 结构化输出 + publish 到 Orchestrator | 经纬 | 1.B, 1.E |
| 1.I | feat | api — FastAPI 启动 + 鉴权 + WS + take.start/end 端点 + transcript topic | 经纬 | 1.E |
| 1.J | feat | frontend — admin transcript 面板接入（双声道 + speaker 标签） | 经纬 | 1.I, 1.C |

### 境熙侧 · L2 基座 + Orchestrator 调度 + admin take 操作面板

| 编号 | 类型 | 标题 | Owner | 依赖 |
|---|---|---|---|---|
| 1.D | feat | db — schema.sql + DAL + migrations（含 transcript_segments.speaker） | 境熙 | 0.E |
| 1.E | feat | core — Orchestrator + SessionState + 事件骨架 + ASR 事件订阅接口 | 境熙 | 1.D |
| 1.F | feat | llm — LLMService 单例 + PQueue + Lock + Gemma client | 境熙 | 0.C, 0.D |
| 1.G | feat | pipelines — L2 Pipeline 实现 | 境熙 | 1.F, 1.E |
| 1.H | feat | core — take.end → 拉 ch1 全量 segment + speaker → 触发 L2 → 写 takes 表 | 境熙 | 1.G, 1.D |
| 1.K | feat | frontend — admin take 控制按钮（开始/结束）+ 状态栏 | 境熙 | 1.I |
| 1.L | feat | frontend — admin take 列表 + take 详情接入（L2 摘要显示） | 境熙 | 1.H |

### Milestone

| 编号 | 类型 | 标题 | Owner | 依赖 |
|---|---|---|---|---|
| 1.M | chore | milestone — 第一阶段端到端联调 | 双方 | 1.A-1.L |

---

### 1.A · feat: asr — ASRService 双路转录（VAD + whisper.cpp）

**Owner**：经纬。**依赖**：0.B。**涉及文件**：`backend/asr/service.py`、`backend/asr/vad.py`、`backend/tests/test_asr_service.py`。

子任务：

- [ ] 写 `test_asr_service.py`（VAD 切段 + 双路并行 + 输出字段正确性）—— **TDD 红**
- [ ] 写 `vad.py`：VAD 切段，输出 `SpeechSegment`（带 pre/post roll）
- [ ] 写 `service.py`：双 whisper.cpp 实例（ch1 / ch2 各一）+ `transcribe()` + `transcribe_raw()`
- [ ] `pytest backend/tests/test_asr_service.py` 全绿
- [ ] ruff + mypy 全过
- [ ] 与音频采集层 smoke test：把 `audio-input-layer` 的 `FileSource` 接进来跑一段音频，能输出转录

### 1.B · feat: asr — speaker diarization 接入（ch1 多 speaker 标注）

**Owner**：经纬。**依赖**：0.G、1.A。**涉及文件**：`backend/asr/diarization.py`、`backend/tests/test_diarization.py`。

子任务：

- [ ] 写 `test_diarization.py`（用 fixture 多 speaker 音频，验证 speaker 标签输出）—— **TDD 红**
- [ ] 写 `diarization.py`：按 0.G 选型集成 diarization 模型（pyannote / WhisperX / NeMo）
- [ ] 集成到 1.A 的 ch1 链路：每个 `TranscriptSegment` 带 `speaker` 字段
- [ ] `pytest` 全绿，ruff + mypy 全过
- [ ] smoke test：跑 1 分钟 2-speaker 对话音频，speaker 标签准确率手测达标

### 1.C · feat: asr — 结构化输出 + publish 到 Orchestrator

**Owner**：经纬。**依赖**：1.B、1.E。**涉及文件**：`backend/asr/publisher.py`、`backend/tests/test_asr_publisher.py`。

子任务：

- [ ] 写 `test_asr_publisher.py`（订阅 Orchestrator event bus，验证 partial / final 事件 schema 含 speaker）—— **TDD 红**
- [ ] 写 `publisher.py`：把 1.A + 1.B 的输出转 `TranscriptSegment(ch, speaker, text, start_frame, end_frame, is_partial)`，按 contract C1 publish 到 Orchestrator
- [ ] Orchestrator 侧验证收到事件 + 调 DAL `insert_segment`（contract C2）
- [ ] `pytest` 全绿，ruff + mypy 全过

### 1.I · feat: api — FastAPI 启动 + 鉴权 + WS + take 端点 + transcript topic

**Owner**：经纬。**依赖**：1.E。**涉及文件**：`backend/api/app.py`、`backend/api/auth.py`、`backend/api/ws.py`、`backend/api/routes/takes.py`、`backend/tests/test_api.py`。

子任务：

- [ ] 写 `test_api.py`（健康检查 + ADMIN_TOKEN 鉴权 + WS 握手 + take.start/end 端点）—— **TDD 红**
- [ ] 写 `app.py`：FastAPI 实例 + CORS + 健康检查 `/healthz`
- [ ] 写 `auth.py`：ADMIN_TOKEN 中间件（从 `.env` 读，启动时控制台打印）
- [ ] 写 `ws.py`：`/ws` 端点 + 连接注册 + topic 订阅
- [ ] 写 `routes/takes.py`：`POST /api/v1/take/start` + `POST /api/v1/take/end`，调 Orchestrator 事件接口
- [ ] WS topic `asr.partial.ch{1,2}` / `asr.final.ch{1,2}` 在 Orchestrator publish ASR 事件时转发到 WS（订阅 Orchestrator event bus）
- [ ] `pytest` 全绿，ruff + mypy 全过

### 1.J · feat: frontend — admin transcript 面板接入

**Owner**：经纬。**依赖**：1.I、1.C。**涉及文件**：`frontend/src/routes/admin/` transcript 相关组件。

子任务：

- [ ] 替换 mock：transcript 面板订阅 WS `asr.partial.ch1/ch2` + `asr.final.ch1/ch2`
- [ ] partial 灰、final 黑显示
- [ ] speaker 标签渲染（按 ch1 内 speaker_id 分色显示）
- [ ] 浏览器手动跑：能录一段音频，双声道滚屏正确，speaker 标签可见
- [ ] commit message 末尾标 `[手动测试]`

### 1.D · feat: db — schema.sql + DAL + migrations

**Owner**：境熙。**依赖**：0.E。**涉及文件**：`backend/db/schema.sql`、`backend/db/dal.py`、`backend/db/migrations/v1_init.sql`、`backend/tests/test_dal.py`。**[已建 Notion ticket]**

子任务：

- [ ] 写 `test_dal.py`（每张表 CRUD + FTS5 MATCH + 边界）—— **TDD 红**
- [ ] 写 `schema.sql`（按 0.E spec，transcript_segments 含 speaker 字段）
- [ ] 写 `migrations/v1_init.sql` + migration runner
- [ ] 写 `dal.py`（每张表的 read / write 方法，含 `insert_segment(take_id, ch, speaker, text, start_frame, end_frame)`）
- [ ] `pytest backend/tests/test_dal.py` 全绿
- [ ] ruff + mypy 全过

### 1.E · feat: core — Orchestrator + SessionState + 事件骨架 + ASR 事件订阅接口

**Owner**：境熙。**依赖**：1.D。**涉及文件**：`backend/core/orchestrator.py`、`backend/core/session.py`、`backend/core/events.py`、`backend/tests/test_orchestrator.py`。

子任务：

- [ ] 写 `test_orchestrator.py`（事件路由 + SessionState 状态机 + pub/sub 订阅接口）—— **TDD 红**
- [ ] 写 `events.py`：事件类型定义（`asr.partial` / `asr.final` 含 speaker / `take.start` / `take.end` / `manual.mark` / `query.request` / `script.upload`）
- [ ] 写 `session.py`：`SessionState` 数据类（字段见 system-architecture v0.1 第 4 节）
- [ ] 写 `orchestrator.py`：事件路由骨架 + pub/sub 接口（暴露 `subscribe(event_type, handler)` 给经纬的 API/ASR 用）
- [ ] ASR 事件订阅 handler：收到 `asr.final` 调 DAL `insert_segment`（contract C2）
- [ ] `pytest` 全绿，ruff + mypy 全过

### 1.F · feat: llm — LLMService 单例 + PQueue + Lock + Gemma client

**Owner**：境熙。**依赖**：0.C、0.D。**涉及文件**：`backend/llm/service.py`、`backend/llm/config.py`、`backend/llm/client.py`、`backend/tests/test_llm_service.py`。

子任务：

- [ ] 写 `test_llm_service.py`（单例 + PriorityQueue 顺序 + Lock 串行化 + llm-service-design v1.1 验收 4 条）—— **TDD 红**
- [ ] 写 `client.py`：按 0.C 选型加载 Gemma 4 E4B
- [ ] 写 `config.py`：`TASK_CONFIG`（按 0.D v1.1，不含 `l1_clean`）
- [ ] 写 `service.py`：单例 + `infer(messages, task_type, priority, timeout)` + PriorityQueue + asyncio.Lock + asyncio.to_thread 包裹
- [ ] `pytest` 全绿，ruff + mypy 全过
- [ ] 联通性 smoke test：Pipeline stub 调一次 `infer`，拿到 Gemma 返回

### 1.G · feat: pipelines — L2 Pipeline 实现

**Owner**：境熙。**依赖**：1.F、1.E。**涉及文件**：`backend/pipelines/l2_take.py`、`backend/tests/test_l2_pipeline.py`。

子任务：

- [ ] 写 `test_l2_pipeline.py`（输入 fixture ch1 transcript segments + speaker + 上下文，验证输出 status / script_diff / notes 字段）—— **TDD 红**
- [ ] 写 `l2_take.py`：prompt builder（含 speaker 信息）+ 调 `LLMService.infer(messages, "l2_take", priority=2)` + 结构化解析
- [ ] `pytest` 全绿，ruff + mypy 全过

### 1.H · feat: core — take.end → 触发 L2 → 写 takes 表

**Owner**：境熙。**依赖**：1.G、1.D。**涉及文件**：`backend/core/orchestrator.py`（扩展）、`backend/tests/test_orchestrator_l2.py`。

子任务：

- [ ] 写 `test_orchestrator_l2.py`（mock LLMService，模拟 `take.end`，验证 L2 被调 + 结果写 takes 表 + publish `take.changed`）—— **TDD 红**
- [ ] Orchestrator 扩展 `take.end` handler：拉 ch1 全量 segment + speaker → 调 L2 → 写 takes → publish `take.changed`（contract C5）
- [ ] `pytest` 全绿，ruff + mypy 全过

### 1.K · feat: frontend — admin take 控制按钮 + 状态栏

**Owner**：境熙。**依赖**：1.I。**涉及文件**：`frontend/src/routes/admin/` take 控制相关组件、状态栏组件。

子任务：

- [ ] 替换 mock：take 开始 / 结束按钮调 REST `POST /api/v1/take/start` `/take/end`
- [ ] 状态栏显示当前场次 / take 编号 / 录制状态
- [ ] 浏览器手动跑：按开始 → 状态栏更新 → 按结束 → 状态回到 idle
- [ ] commit message 末尾标 `[手动测试]`

### 1.L · feat: frontend — admin take 列表 + take 详情接入

**Owner**：境熙。**依赖**：1.H。**涉及文件**：`frontend/src/routes/admin/` take 列表 / 详情相关组件。

子任务：

- [ ] 替换 mock：take 列表用 REST `GET /api/v1/takes`，订阅 WS `take.changed` 增量刷新
- [ ] take 详情用 REST `GET /api/v1/takes/{id}`，显示 L2 摘要 + script_diff
- [ ] 浏览器手动跑：take 结束后看到列表新增一条，≤10s 内 L2 摘要填进去
- [ ] commit message 末尾标 `[手动测试]`

### 1.M · chore: milestone — 第一阶段端到端联调

**Owner**：双方。**依赖**：1.A-1.L 全部完成。

验收：

- [ ] 在 admin UI 录一条 take：开始 → 多人说话 → 结束
- [ ] 双声道实时转录滚屏，partial 灰、final 黑
- [ ] ch1 内 speaker 标签按演员区分显示，准确率手测达标
- [ ] take 结束 ≤10s 内 take 列表更新，L2 摘要 + speaker 维度信息可见
- [ ] 没有 console error，没有 WS 断连
- [ ] 跑全量 `pytest` 全绿
- [ ] 打 tag `mvp-stage1`，写 milestone 日志到 `docs/`

---

## 7. 第二阶段及以后（留白）

第一阶段 1.M milestone 跑通后再拆。预期范围：

- **第二阶段**：Note 输入流程（按麦录音 + 打字，经纬 own）+ 拍照剧本流程（粘贴 / 拍照导入，境熙 own）。两条 workflow 独立并行。
- **第三阶段**：QP Pipeline + `/query` 端点 + WS `qp.answer.{conn_id}` topic + admin UI 查询入口 + `/view` 路由 + presence + 共享视图 transcript / take 列表 + 导出场记单（`/export/takes` JSON / PDF）。
- **第四阶段及以后**：Agent Pipeline 预留接口（B2 决议，MVP 不实现）+ quality agent 全量集成测试 + code review。

owner、ticket 拆分、contract boundary 在第一阶段 1.M 后开新一轮 planning 会议讨论。

---

## 8. 跨人对接清单（Contract Boundary）

第一阶段中跨人接口必须先有 contract、后有实现。每条 contract 标定义方 / 消费方 / 对接 ticket。

| ID | 接口 | 定义方 | 消费方 | 定义 ticket | 消费 ticket |
|---|---|---|---|---|---|
| C1 | ASR 事件 schema（`asr.partial.ch{1,2}` / `asr.final.ch{1,2}`，payload：`{text, speaker, start_frame, end_frame, take_id?, is_partial}`） | 0.B（经纬定 schema）+ 1.E（境熙定 pub/sub 接口形状） | Orchestrator（1.E） / api WS（1.I） | 0.B + 1.E | 1.C |
| C2 | DAL `transcript_segments` 接口（`insert_segment(take_id, ch, speaker, text, start_frame, end_frame) -> int`） | 0.E + 1.D（境熙） | Orchestrator ASR 事件 handler（1.E） / 间接被 1.C 调链触发 | 0.E + 1.D | 1.E |
| C3 | Orchestrator 事件 publish 接口（`publish(event_type, payload)`） | 1.E（境熙） | 经纬的 API 层 take.start/end handler（1.I） | 1.E | 1.I |
| C4 | LLMService.infer 签名（`infer(messages, task_type, priority, timeout) -> str`） | 1.F（境熙） | 所有 Pipeline（1.G 起） | 1.F | 1.G + 第二阶段所有 Pipeline |
| C5 | WS topic `take.changed` payload（`{take_id, take_number, status, scene_id}`） | 1.H（境熙 publish 到 Orchestrator event bus，1.I 经纬转 WS） | 经纬前端 take 列表（1.L 也消费） | 1.H + 1.I | 1.L |

任一 contract 改动 = 同步更新本表 + 通知消费方对应 ticket owner。

---

## 9. 经纬可独立领走的 Ticket 清单

按时间顺序，经纬打开 doc 第一眼可挑的活：

| 阶段 | Ticket | 前置 | 估时（粗） |
|---|---|---|---|
| 阶段 0 | 0.A `spike: asr — whisper.cpp Python 集成` | 无 | 半天 |
| 阶段 0 | 0.G `spike: asr — speaker diarization 选型` | 无 | 半天 |
| 阶段 0 | 0.B `spec: asr — ASR Service 独立 spec` | 0.A + 0.G | 半天 |
| 阶段 1 | 1.A `feat: asr — ASRService 双路转录` | 0.B | 1 天 |
| 阶段 1 | 1.B `feat: asr — speaker diarization 接入` | 0.G + 1.A | 1 天 |
| 阶段 1 | 1.C `feat: asr — 结构化输出 + publish` | 1.B + 1.E | 半天 |
| 阶段 1 | 1.I `feat: api — FastAPI 启动 + take 端点 + WS` | 1.E | 1 天 |
| 阶段 1 | 1.J `feat: frontend — admin transcript 面板接入` | 1.I + 1.C | 半天 |

经纬合计 ≈ 5 天工作量。任何 ticket 阻塞超过半天 → 在 ticket 评论里 `[BLOCKED]` 升给 Lead。

---

## 10. 风险与开放问题

1. **阶段 0 不收敛 → 全线阻塞**：0.A / 0.C / 0.G 三个 spike 必须时盒，定 1 天上限，超时降级（whisper.cpp 选 subprocess 兜底；Gemma 选 Ollama 兜底；diarization 选 WhisperX 一体方案兜底）。**注意**：每个 spike 必须在 Windows + macOS 两个平台都验证通过，详见 §2 跨平台兼容性约定。
2. **speaker diarization 准确率风险**：hackathon 阶段没时间精调 diarization 模型，可能出现 speaker 标签跳变。若准确率不达标，1.B 降级为「ch 维度准确即可，ch1 内 speaker 留空」。L2 prompt 容错处理。
3. **B 端开放问题**（声道命名 / ch2 取用策略 / take 边界自动 vs 手动）：见 system-architecture v0.1 第 15 节，需 Lead 在阶段 1 启动前拍。
4. **跨人 ticket 的 Owner 字段**：multi_select，跨人 ticket（如 1.M 联调 milestone）两个 owner 都勾，正文写明各自分工。
5. **第二阶段拆分滞后**：第二阶段 ticket 未在本 doc 拆出，避免基座抖动导致返工。1.M milestone 后必须立即开新一轮 planning，不要拖。

---

## 11. TODO（落地用）

- [x] 本 doc v0.2 提交 Lead 评审（境熙），评审通过后状态 → 定稿（2026-05-27 定稿）
- [ ] 在 Notion 「任务清单」db 批量建剩余 18 张 ticket（境熙）
- [ ] system-architecture v0.1 第 14 节里和本 doc 重复的 TODO 同步勾掉（境熙）
- [ ] 通知经纬上游：speaker diarization 是新增需求，需要 0.G spike 先行；TranscriptSegment 加 `speaker` 字段（境熙在 1.D + 0.E 时同步设计）
