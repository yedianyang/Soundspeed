# Soundspeed 技术报告

> **Gemma 4 开发者大赛（GDG Shanghai · 2026）**
> 面向电影同期录音部门的**本地离线 AI 场记助手**——用**一个 Gemma 4 E4B** 同时承担文本理解、剧本照片识别与语音查询，配合**贯穿全栈的原生函数调用**，在一台 M4 Mac Mini 上完成"录音 → 理解 → 结构化场记"的全离线闭环。

---

## 一、提交信息

| 项 | 内容 |
| --- | --- |
| 项目名称 | **Soundspeed**（同期录音 AI 场记助手） |
| 参赛赛道 | **赛道 B · Multimodal（多模态）** |
| 队伍名称 | 【待填】 |
| 团队成员 | 【待填：姓名 / 分工】 |
| 代码仓库 | https://github.com/yedianyang/Soundspeed |
| 在线 Demo | 【待填：在线演示链接】 |
| 演示视频 | 【待填：≤5 分钟视频链接】 |
| 核心模型 | **Gemma 4 E4B-it**（`Q4_K_M` GGUF）+ `mmproj-F16`（含 vision + audio 双投影器） |
| 运行环境 | llama-cpp-python，M4 Mac Mini，全程本地离线（跨 macOS / Windows 双平台） |
| 开源协议 | Apache-2.0 |

---

## 二、问题与真实影响

**同期录音（production sound）现场的场记工作，至今高度依赖人工纸笔。** 每条镜头（take）拍摄时，场记要同时盯住：这条说了什么、和剧本有没有出入、哪句台词被改/被漏/被加、录音质量如何、导演有没有口头备注、是哪个演员说的。一条戏几十上百条 take，信息量大、节奏快，人工记录既慢又易漏，而这些"片场元数据"直接决定后期剪辑与导演决策的效率。

**Soundspeed 把这套流程自动化，并坚持"全部在片场本地、离线完成"**：

- **录音师 / 场记** 在录制时实时拿到逐字转写、实录↔剧本逐行对照、说话人归属、口头备注的结构化归档；
- 收工即可导出**场记单（CSV）**，无需事后誊写；
- 影视项目的剧本与录音是**高度保密资产**，云端方案存在合规与泄密风险——Soundspeed 不依赖任何云 API，数据不出本机，天然适配片场保密要求。

受众清晰（同期录音部门）、需求真实（替代纸笔场记）、可扩展（中小成本剧组无需昂贵后期人力即可获得专业级场记）。

---

## 三、为什么选择 Gemma 4 E4B

Soundspeed 的硬约束是：**片场无稳定公网、数据必须本地、只有一台小型本地设备（M4 Mac Mini）**。在此约束下逐项权衡四个规格：

| 规格 | 是否选用 | 理由 |
| --- | --- | --- |
| **E4B（端侧）** | ✅ **选用** | `Q4_K_M` 量化后在一台 M4 Mac Mini（Apple Silicon 统一内存 + Metal）上即可全离线流畅运行；在中文台词理解、原生函数调用稳定性、视觉 OCR 质量上明显优于 E2B，是"能落到片场本地设备 + 质量够用"的最优平衡点。 |
| E2B（端侧） | ✗ | 资源更省，但长 take 的函数调用结构化、密集中文剧本 OCR 稳定性不足，质量风险偏高。 |
| 26B MoE | ✗ | 吞吐与质量更高，但体量/算力超出 M4 Mac Mini 这类端侧设备，更适合云端——与"离线 + 隐私"诉求冲突。 |
| 31B Dense | ✗ | 同上，定位云端高负载，不满足端侧离线落地。 |

更关键的是，**E4B 一个模型就原生覆盖了我们需要的全部能力**——文本推理、视觉、音频、原生函数调用。这正是把"片场只部署一个模型实例，却能读图、能听懂语音、能调工具"变为现实的前提。选 E4B 不是妥协，而是"端侧落地 × 原生多模态 × 函数调用"的交集。

> 模型坐标：`unsloth/gemma-4-E4B-it-GGUF` 的 `gemma-4-E4B-it-Q4_K_M.gguf` + `mmproj-F16.gguf`（一份 F16 投影器同时含 `gemma4v` 视觉与 `gemma4a` 音频两路）。首次运行自动从 HuggingFace 拉取；`mmproj` 缺失/离线时自动降级为纯文本（视觉/语音暂不可用），联网后首条多模态请求会补拉。

---

## 四、系统架构总览

### 4.1 核心理念：Gemma 4 是"理解层"，专用模型是"捕获层"

Soundspeed **刻意不做"模型大杂烩串联"**，而是清晰分工：

- **捕获层（高保真、确定性）**：录音的**逐字转写由 Whisper（`pywhispercpp`）**完成，**说话人 / 声纹识别由 Pyannote（`pyannote.audio` 4.0）**完成。它们把声音"原样"变成带时间戳的文本与说话人标签。**Gemma 4 不参与转写与声纹识别。**
- **理解层（语义、结构化、跨模态）**：**同一个 Gemma 4 E4B 实例**承担所有"需要理解"的任务——实录↔剧本比对、备注结构化、自然语言查询、剧本照片 OCR，以及**直接听懂用户口述的查询/备注**。文本、图像、语音指令都经由同一个 `MultimodalGemma4Handler` 进入这一个模型。

这一分工既发挥了 Whisper/Pyannote 在逐字精度与声纹上的专业优势，又用单个 Gemma 4 统一了所有语义与跨模态环节，避免为每种语义任务各塞一个模型。

### 4.2 事件驱动编排（Orchestrator）

```
麦克风音频 ──► Whisper ASR ──► 逐字转写片段（ch1/ch2，带 start/end frame）
                                   │
                          Orchestrator.publish("asr.final.*")  →  写入 transcript_segments
                                   │
                       take.end 事件 ──────────────┐
                                   │（先同步出态、再异步补 script_diff，前端立刻有反馈）
                 ┌─────────────────┴───────┐   ┌───┴────────────────────────┐
                 ▼                          ▼   ▼                            ▼
        L2（实录↔剧本对照）        NP（备注结构化）      Pyannote 分离回填（复用 ASR 文本不重跑）
                 └──────────┬───────────────┘
                            ▼
              LLMService（单例·优先级队列·单 worker·全局锁串行）── 同一个 Gemma 4 E4B
              infer() / infer_tool() / infer_voice() / infer_voice_tool()
                            │
                  WebSocket 广播 + 写库（SQLite）
```

- **单实例 + 优先级队列**：一份 Gemma 权重被剧本解析、L2、备注结构化、查询、OCR、语音调度六类任务争用；用 `asyncio.PriorityQueue` + 单 worker + 全局锁把所有推理**串行化**，优先级 **QP 用户查询(1) > L2/NP 后台分析(2) > 剧本解析(3)**，保证人在交互时优先响应。
- **运行档位（`SOUNDSPEED_PROFILE`）**：`import` 档让 Gemma 充分用满本机算力做解析/视觉；`record` 档把算力让给 Whisper + Pyannote、Gemma 退 CPU。底层有 GPU/CPU 自适应回落（独显环境按可用显存判定、统一内存环境可手动切档），让"重 LLM"与"重 ASR"两阶段都能在单机跑顺。
- **运行时调优**：`n_ctx=8192`（容纳 5 分钟以上长 take + 上百行剧本），开启 Flash Attention 降低长上下文的内存开销与延迟。

---

## 五、Gemma 4 能力的深度运用

### 5.1 单实例三模态 + 运行时热切 chat handler（多模态与函数调用共存的关键工程）

我们不为"听/看/读"各开一个模型，而是让**同一个 Gemma 4 实例**服务三种输入：

- 给 client 注入 `mmproj` 后挂上 `MultimodalGemma4Handler`，文本、图像、语音共用同一份权重与 KV；
- 但这个多模态 handler 的对话模板**不渲染工具声明**——也就是说"文本 + 工具"的请求里模型看不到工具。为此，client 在遇到"文本 + tools"请求时，**临时把 chat handler 换成由 GGUF 内嵌模板构造的原生 FunctionGemma formatter（会渲染工具），推理后再还原**，靠全局锁串行保证无竞态。

这一步"按需热切 handler"正是把"一个实例既能多模态、又能原生函数调用"真正落地的接缝——也是赛道 B"深度整合、超越简单串联"的具体体现。

### 5.2 原生函数调用（Native Function Calling）—— 全栈贯穿

函数调用是 Soundspeed 把"模型输出"变成"可落库结构化数据"的核心机制，按任务形态分了三种策略：

| 管线 | 工具 | 模式 | 作用 |
| --- | --- | --- | --- |
| **L2 实录↔剧本分析** | `report_script_analysis` | forced（具名 tool_choice） | 逐行匹配（漏说/替换/新增）+ 修正片段 |
| **NP 备注结构化** | `structure_note` | forced | 把口头/文字备注归到正确 take 并分类 |
| **Memo 路由** | `route_memo` | forced（16-token 二分类） | 输入分流到 note / query 分支 |
| **单场剧本解析** | `report_parsed_lines` | forced（grammar 路径） | 单场更新时把文本结构化为逐行 |
| **QP 会话查询** | `count_takes` / `get_scene_info` / `list_characters` / `search_script_lines` / `query_database` | `auto`（多跳自主路由） | 模型自主选工具、执行、回喂、续跳作答 |

- **四个推理入口的正交分解**：`infer`（文本→内容）、`infer_tool`（文本→工具调用）、`infer_voice`（音频→内容）、`infer_voice_tool`（音频→工具调用），由 `want_tool_call × audio` 两个正交维度叉乘而成，共用同一条入队/调度路径——干净且无重复实现。
- **forced vs auto 两种调度**：结构化落库（L2/NP/路由/单场解析）用 forced，由 grammar 在采样层物理保证输出合法 JSON；多工具问答（QP）用 auto 让模型自主路由。
- **工具 schema 与下游校验同源**：L2 的 `diff_type`、备注的 `category` 等枚举抽到中性叶子模块（`l2_constants` 等），grammar 约束的合法值与 pipeline 校验取同一真源，杜绝"schema 与校验漂移"。

### 5.3 grammar 的成本权衡 → 冷热路径分流

我们发现 grammar（GBNF 约束采样）在 Gemma 约 25 万词表上每 token 的 CPU 开销显著、吞吐大幅下降（内部测得约 **5.6×** 量级）。据此做冷热分流：

- **整本剧本解析（热路径）刻意不用 grammar**：用纯代码正则按场头切分（`split_scenes_by_slugline`），再让 Gemma 逐行自由吐 `[说话人, 台词]`，配多层容错解析兜底（解析失败退化到冒号启发式，台词原文永不丢）。"能用代码就别用模型、模型只做必须的语义判断"。
- **grammar / 强制 FC 只留给低频路径**：单场更新、路由、QP 取参。

### 5.4 视觉：照片直接更新剧本

- **逐页 OCR**：剧本照片经同一个 Gemma 4 视觉投影器转写（`IMAGE_TOKENS=1120` 高分辨率档，专为密集中文小字调高）。**逐页单图**喂入（不一次喂多图），根治小模型看多图时整段循环复读。
- **OCR 越界续写的多层兜底**：小模型常不在回合边界停、越界吐 `<|turn|>` 等标记再乱写/复读。对此做了三道闸——生成期 `stop` 列表遇回合标记即停 + `repeat_penalty`；事后 `_strip_special_tokens` 截掉特殊标记后的续写；`_dedup_repeated_lines` 折叠连续/近窗重复行。
- **结构化走无 grammar 快路径**：OCR 文本偏长，强制 grammar FC 会超时（实测多页超时），故照片路径用无 grammar 的 `parse_scene_block` 结构化（与 §5.3 的冷热分流一致）。
- **增量合并（不再整段覆盖）**：端点 `POST /api/v1/scenes/{scene_id}/script/diff` 用标准库 `difflib.SequenceMatcher` 把 OCR 结果与该场旧版逐行对齐（确定性、不调 LLM）：**未变留旧 / 改动取新 / 新增加入 / 旧有新无则保留旧**。最后一条尤为关键——小模型 OCR 难免漏字漏行，"保留旧"保证增补绝不会把原内容删没；提交的 `raw_text` 由合并结果重建，保证落库 `raw_text↔lines` 一致与幂等。用户在彩色逐行对照里复核后再确认。

### 5.5 语音查询：原生音频输入（真正的多模态体现）

项目里**真正调用 Gemma 4 原生多模态**的地方，是**"标记 / 查询"输入支持直接语音**：场记/录音师按住说一句话（如"第三场拍了几条？"），**原始 WAV 不经转写，直接进入 Gemma 4**，由模型直接"听懂"口语意图。

- **音频哨兵通道**：WAV 字节不放进对话消息，消息里只放一个占位 `AUDIO_SENTINEL`；真字节在串行锁下暂存单槽位，handler 命中哨兵时取回——借 `mtmd` 对音频/图像通用的媒体标记，把音频塞进既有图像通道，让一份实例无缝多服务一种模态。
- **两跳调度**：hop A 用自由生成"听懂意图"——工具声明以 GGUF 原生 `<|tool>…<tool|>` 格式（用 `vocab_only` 秒级加载提取、`lru_cache` 缓存）注入 system，让模型自发吐出该调哪个工具；hop B 用强制 tool_choice 在同一段音频上取结构化参数 → 执行查询/落库备注 → 广播答案。
- **边界说明**：录音的逐字转写（Whisper）与说话人/声纹识别（Pyannote）**不经过 Gemma**；Gemma 4 的音频能力专门用于"直接听懂用户口述的查询/备注"。

至此，**文本（推理与结构化）、图像（剧本 OCR）、语音（口述查询）三类输入都由同一个 Gemma 4 E4B 原生处理**。

### 5.6 QP：本地小模型上的稳健 agentic tool-loop

QP（场记查询）是"模型自主多跳工具调用"，针对 4B 级小模型函数调用易抖动做了专门工程：

- **两步走解码**：每一跳先用 `auto` 让模型吐出工具名（正则从原生 `<|tool_call>call:NAME` 里抠，名字铁稳在参数之前），再用 forced + grammar 取**干净 JSON 参数**——彻底不在脆弱的自由文本里解析嵌套参数。最多 5 跳。
- **避开模板坑**：回喂刻意用"`assistant` 原文 + `user` 纯文本"而非 OpenAI 的 `role=tool`——后者会触发该 GGUF Jinja 模板的 `raise_exception undefined`。
- **`query_database` 只读 SQL"万能笔" + 沙箱**：除 4 个结构化工具外，第 5 个工具允许模型**自己写一条只读 SELECT** 兜底长尾问题；执行走独立 `mode=ro` 连接 + `set_authorizer` 动作级放行（仅 SELECT/READ/FUNCTION，拒 ATTACH/写/`load_extension`）+ 单句守卫 + 行数封顶 + 超时中断——把"让 LLM 直接执行 SQL"这一高危能力收进多层纵深防御的沙箱。
- **错误回喂自纠**：工具执行的任何异常都被包成 `{"error": ...}` 回喂，模型下一跳自我纠正，而非整轮失败。

### 5.7 可观测性：函数调用实时遥测

`LLMService` 暴露一个无侵入的 tool-call tap：每次函数调用成功后，把工具名、参数、可用工具、`finish_reason`、token 用量经 WebSocket 实时广播到管理面板的开发者日志。评委能**实时看到模型在调哪个工具、用了多少 token**——既利于调试，也是现场演示的加分项。

---

## 六、工程化设计（节选）

- **异步分发的硬约束**：`take.end` 触发 L2/NP 是 fire-and-forget（`asyncio.create_task`），并采用"先同步出态、后异步补 `script_diff`"的两段式广播，让前端立刻有反馈。相关端点**必须 `async def`**——否则被丢进线程池就没有 running loop，L2 会静默不触发；这个真实踩过的坑被固化进模块契约。
- **并发模型取舍**：DAL 用单个 `check_same_thread=False` 共享连接 + WAL + `BEGIN IMMEDIATE`；据此**所有路由强制 async**，把 DB 访问钉在事件循环单线程串行，避免与 L2 异步任务并发操作同一连接（而非引入连接池/锁）。
- **数据层的一致性设计**：剧本**版本追加、读取只取最新版**（历史可追溯）；无号场用源文本内容指纹做稳定 ID，根治"同一剧本重传累积重复场"；take 号位冲突在单事务内原子解析（软删号可复用、被编辑 take 顺位加后缀）；备注用 `take_events` 事件溯源 + 派生聚合，多写者（用户 Mark / L2 / memo / NP）共写一行不同列时用 `COALESCE` 实现"部分更新不互相覆盖"。
- **检索基座**：台词检索用 FTS5 `trigram` tokenizer（中文无需分词即可子串匹配）+ BM25 相关性排序，外部内容表 + 触发器保证与源表强一致。
- **分离两阶段、复用 ASR 文本不重跑**：实时 ASR 与离线 diarization 解耦；回填只把说话人标签 UPDATE 回既有转录段（不动文本、不重跑 Whisper），并把"本 take 在场演员数"作为先验喂给 Pyannote（防单麦相似音色塌成一人）；enroll 与 diarize 共用同一 pipeline、同一 embedding 空间才能跨 take 比对。
- **健壮性**：推理超时即取消、worker 跳过已取消任务省算力；对"配了强制 tool_choice 却误调 `infer`"给显式护栏（不静默返回 None）；路由分类、语音分类都 **fail-closed**（分类器宕了也不挡备注提交）；模型后端抽象成 `Protocol` + `StubClient`，让全链路在无 GPU/无模型的 CI 下可测；进程重启时自动复位残留的"解析中"上传、恢复活跃场上下文。

---

## 七、前端工程

- **实录↔剧本序列对齐并置**：浏览器侧做 merge-diff——以实录（按真实录制时序）为主线，按 `segment_ids` 把每条实录段映射到剧本行，对上的并列、漏说的就地插入、实录独有的单列。L2 给的位置下标易失，后端**回带稳定的 DB `segment_id`**，让前端把实录侧重接到最新可编辑的转录段；场记纠正说话人即时同步到对照视图。
- **选中场增量更新对话框**：文本/照片双入口，解析后与最新版逐行对齐、彩色角标预览（未变/改/新增/保留旧），确认提交合并结果，版本追加、旧版保留。
- **语音输入接线**：浏览器侧"按住说话"统一编码成 16k 单声道 WAV（消平台差异），直传后端交 Gemma 4 多模态。
- **实时一致性**：乐观更新 + WebSocket 按 `CONN_ID`/`client_id` 定向认领回包；断线指数退避重连后用 refetch 对齐错过的关键事件，`script_diff` 防降级。

---

## 八、赛道选择与覆盖

**主选赛道 B · Multimodal**：项目原生使用 Gemma 4 的文本 + 视觉 + 语音三类输入，且都接入原生函数调用，并为"单实例多模态 + 函数调用共存"做了底层 handler 热切设计——是真实业务闭环而非简单 demo。

同时具备其他赛道的硬实力：

- **赛道 A（Agent）**：QP 是完整的"模型自主多跳工具调用"（两步走解码 + 错误回喂自纠 + 只读 SQL 万能笔），语音查询是"听懂意图 → 选工具 → 取参 → 作答"的两跳 agent 形态。
- **本地离线部署**：整套系统在单台 M4 Mac Mini 上全离线运行，无任何云依赖；跨 macOS / Windows 双平台。
- **AI for Social Good**：让中小成本剧组以极低硬件成本获得专业级场记能力，降低影视生产门槛。

---

## 九、功能完备性

**已端到端跑通：**

- 实时双声道 ASR（Whisper + VAD）、说话人分离（Pyannote）与即时回填同步
- **L2** 实录↔剧本逐行序列对齐并置 + 替换/漏说/新增判定（强制函数调用）
- **NP** 文字/语音备注结构化归档；**QP** 自然语言查询多跳作答，**支持语音输入**
- **SP** 剧本上传解析 / 单场更新 / **照片视觉 OCR + 增量合并**（版本化，旧版保留）
- 管理面板（History 并置视图、剧本面板、场次导航/更新、函数调用实时日志）、**场记单 CSV 导出**
- SQLite 持久化、FastAPI REST + WebSocket、React 前端

**已实现、仍在打磨（诚实披露）：**

- 语音**查询**分支的"生产无转写续跳"形态已落地，模型收尾稳定性仍在真机验证中（语音**备注**分支已稳定）；语音查询暂未注入场次目录上下文（文本查询有），解析准确性上两者尚不完全对等。

**预留未实现：** 通用 Agent 管线（`agent_init`）目前为占位、调用即报 `NotImplementedError`，不计入演示范围。

---

## 十、创新点

1. **单实例三模态 + 原生函数调用共存**：一份 Gemma 4 E4B 权重同时读图、听语音、做结构化推理，并为多模态 handler 与函数调用 formatter 的不兼容做了"按需热切 handler"的底层缝合——不是接了三个能力，而是让它们在一个实例里共存。
2. **实录↔剧本的"merge-diff 并置"**：以实录为时间轴基线把剧本逐行交织对齐，漏说/插入就地标注；并把 LLM 给的易失位置下标收口成稳定 DB 主键回带前端，说话人纠正即时同步。
3. **照片→剧本的"安全增量合并"**：生成期+后处理多层兜底让小模型 OCR 输出可用，再用 `difflib` + "旧有新无保留旧"的确定性合并，把不可避免的 OCR 误差转化为"用户可复核、绝不删没原文"的安全增补。
4. **本地小模型上的稳健 agentic 查询**：两步走解码 + 只读 SQL 沙箱万能笔 + 错误回喂自纠，把 4B 级模型的函数调用做到生产可用。

---

## 十一、工程质量与可演示性

- **测试**：后端 **1023 个用例通过、12 跳过（共约 1035 项，68 个测试文件）**，覆盖 ASR、DAL、各管线、orchestrator、LLM service、多模态 handler、函数调用、语音调度、API；模型后端用 `StubClient` 替身，全链路无需 GPU/真实权重即可测。前端 `tsc` 类型零错误。
- **工程规范**：TDD 红绿、feature 分支 → PR → review 合并、契约边界与设计文档化（`docs/specs/`）。
- **可观测/可演示**：函数调用实时遥测面板（工具名/参数/token 用量）、模型 `downloading → loading → running` 状态机前端可见。
- **可复现**：仓库含 `requirements.txt` / `pyproject.toml` 锁定依赖（环境搭建步骤见 `CONTRIBUTING.md` 与 `docs/`）；模型权重首次运行自动从 HuggingFace 拉取。
- **技术栈**：FastAPI（Python 3.12）+ React 19 / Vite / TypeScript + SQLite；llama-cpp-python / pywhispercpp / pyannote.audio / silero-vad。

---

## 十二、合规性

- **核心模型**：**Gemma 4 E4B**，权重来自 HuggingFace `unsloth/gemma-4-E4B-it-GGUF`，遵循 Google Gemma 使用条款。
- **训练数据披露**：本项目**不做任何微调、不引入外部训练数据**，全部能力来自 Gemma 4 的**纯推理 + 提示工程 + 原生函数调用**；不存在自有训练集。
- **数据隐私**：**全程本地离线**，录音、剧本、备注等敏感数据不出本机、不上传任何云服务，符合影视生产保密与数据隐私要求。
- **第三方组件**：Whisper（whisper.cpp，MIT）、Pyannote（需用户自备 HF token，遵循其模型许可）、silero-vad —— 均为开源、按各自许可使用；本项目以 Apache-2.0 开源。

---

## 附录：快速复现

1. 准备 Python 3.12 + `venv`，按 `requirements.txt` / `pyproject.toml` 安装依赖（搭建细节见 `CONTRIBUTING.md` / `docs/`）。
2. 首次运行自动从 HuggingFace 下载 `gemma-4-E4B-it-Q4_K_M.gguf` 与 `mmproj-F16.gguf`（`mmproj` 缺失会降级纯文本，联网后首条多模态请求补拉）。
3. 起后端（`SOUNDSPEED_PROFILE=import` 试视觉/解析；`record` 试实时录制），起前端 `npm run dev`，浏览器进 `/admin`。

> 文中所有技术细节均可在仓库对应代码与 `docs/specs/` 设计文档中查证。
