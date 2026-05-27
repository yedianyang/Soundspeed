# Spec: 拍摄现场 LLM 用例与 UX 流程

版本：v1.2.1
日期：2026-05-27
状态：已同步 Notion「与AI头脑风暴的UX形态」v0.2

变更记录：
- v1.2.1（2026-05-27 Lead 复核修补）：VAD 节点声道命名 ch0/ch1 → ch1/ch2 对齐 architecture 声道约定；前端技术栈引用 §B8 → §9 前端结构。
- v1.2：同步 Notion v0.2 流程图；删独立 L1 Pipeline 节点（职责并入 L2）；补 speaker diarization 节点；新增 UI 结构图与交互子流程
- v1.1：补充 ASR 引擎选型结论（依据 framework benchmark）
- v1.0：初稿

---

## 背景

Soundspeed 的核心管道（音频采集 → ASR → 说话人分离 → SQLite）不需要 LLM 参与。
LLM 的价值在数据上方的智能层：理解录音师的口语化输入、语义检索、脚本对比、事后注释解析。
本 spec 定义这个智能层的完整用例、输入来源和 UX 流程。

---

## 用户角色

| 角色 | 设备 | 权限 |
|---|---|---|
| 录音师（主操作者） | 系统主机（Mac mini 等） | 读写：标注、查询、输入 note |
| 导演 / 场记（只读参考） | 手机 / iPad，局域网浏览器 | 只读：实时转录、Take 状态、自然语言查询 |

---

## 输入架构

系统维护两条独立音频输入通道：

**Ch1 — 对白通道**
Boom 或 Lav 信号，采集演员对白。经 ASR 转录后实时推送至前端显示，同时写入 Take 记录。

**Ch2 — 录音师备注通道**
录音师面向自己的专用麦克风，随时口述质量评估和备注。ASR 转录后由 LLM 解析为结构化字段，与当前 Take 时间戳绑定。

此外，录音师可通过前端进行：
- 触屏快速 mark（Keeper / NG / Hold）
- 文字输入（导演指令转述、候补 note）

---

## ASR 引擎选型

依据 `experiments/2026-05-20-whisper-framework-benchmark/`：同一 whisper-medium、
统一 8-bit 量化，在 M1 Max 上对比 Cactus / whisper.cpp / mlx-whisper。

| 框架 | 推理 RTF | 加载(s) | 中文输出 |
|---|---|---|---|
| whisper.cpp (q8_0) | 22.0x | 0.34 | 干净 |
| mlx-whisper (8-bit) | 34.5x | 2.25 | 干净 |
| cactus-whisper (INT8) | 8.3x | 0.52 | 「中」字输出成 `?`（bug） |

结论：

- whisper.cpp 最均衡——推理快、加载最快、输出干净。
- mlx-whisper 推理最快但加载慢，每会话一次加载成本。
- Cactus 三项垫底，且有「中」字 artifact、INT8 medium 冷加载观测到一次 5427s。
  移动引擎在 Mac 上吃不满 GPU。

**决定**：Mac 开发与 demo 阶段，ASR 层用 whisper.cpp。原 CLAUDE.md 架构设想
「Cactus 一套引擎同时跑 ASR + Gemma」，基准否决了这个方案在 Mac 上的可行性——
ASR 与 Gemma 推理引擎现已分离。未来若上手机 / 嵌入式，Cactus 的移动优化会改变
结论，需复测。

---

## 核心 LLM 用例

### 1. 口述备注结构化（Ch2）

录音师说：「这条二号最后一句漏词，收音干净，先 hold。」

LLM 输出：
```
performer_issue: [2]
issue_type: line_fluff
location: 结尾
audio_quality: clean
status: hold
```

比打字快，比规则分类器灵活，能处理口语简称和省略。

### 2. 候补 note（事后追加）

Take 结束后、场次收工后，录音师用文字输入：「第三条结尾比较好，可以用。」

LLM 解析：
- 默认绑定当前场景上下文（最近活跃场次）
- 识别 take 编号（「第三条」→ Take 3）
- 提取 note 内容，追加写入对应 Take 记录
- 跨场引用需显式带场次编号（例：「第五场第二条」）

### 3. 脚本偏差检测

前提：录音师提前加载剧本文本。

每条 Take 结束后，LLM 比对 Ch1 转录与剧本对应台词，输出偏差报告：
- 漏词：演员跳过的台词片段
- 改词：说法与剧本不符
- 加词：临场加入的台词

结果写入 Take 记录，在共享视图中对场记可见。

### 4. Take 对比摘要

录音师或导演发起查询：「这场哪条最稳？」

LLM 读取当前场次所有 Take 记录（状态、Ch2 备注、手动 mark），生成一句话摘要：
「Take 4 收音干净，Take 6 表演最完整，Take 8 结尾有底噪备注。」

摘要质量上限由 Ch2 备注密度决定，没有备注的字段不做推断。

### 5. 语义化查找

查询：「找那条二号演员卡壳的 take。」

LLM 解析意图，跨字段联合检索 SQLite，返回匹配 Take 列表。
可搜索字段：performer_issue、issue_type、audio_quality、note 文本、转录内容。

### 6. 剧本语义检索

查询：「从'我不想走'那里开始。」

LLM 在已加载剧本中定位该台词，返回对应场次 / 镜次编号。
不需要录音师翻纸质剧本，也无需精确关键词。

---

## UX 流程

### 单 Take 循环流程

```mermaid
flowchart LR
     subgraph AUDIO["Audio 输入层"]
         A1["Ch1 对白语音"]
         A2["Ch2 备注语音"]
     end
 
     subgraph VAD["Voice Activity Layer"]
         V1["VAD · ch1"]
         V2["VAD · ch2"]
     end
 
     subgraph PRE["ASR 层"]
         ASR1["whisper.cpp · Ch1"]
         ASR2["whisper.cpp · Ch2"]
     end
 
     subgraph DIAR["说话人分离层"]
         SD["Speaker Diarization · Ch1"]
     end
 
     subgraph LLMLAYER["LLM 处理层（Gemma 4 E4B 共享实例）"]
         BUFFER[("per-take ASR buffer (Orchestrator 内存)")]
         L2["L2 per-take 整合\nASR 清洗 / 剧本 diff / take 摘要\n（含原 L1 per-segment 清洗职责）"]
         BUFFER --> L2
     end
 
     TBOUND(("Take 边界信号 开始 / 结束 (手动按钮)"))
 
     subgraph STORE["存储层"]
         DB[("SQLite + FTS5 索引 (scripts 表)")]
     end
 
     UI["前端 / 共享视图"]
 
     A1 --> V1 --> ASR1
     A2 --> V2 --> ASR2
     ASR1 --> SD
     SD -->|"final segment + ch1"| BUFFER
     ASR2 -->|"final segment + ch2"| BUFFER
     ASR1 -.->|"partial"| UI
     ASR2 -.->|"partial"| UI
 
     TBOUND -.->|"开始 → INSERT take row 占位"| DB
     TBOUND -.->|"结束 → 触发 L2"| L2
 
     DB -->|"读 scripts (FTS5)"| L2
     L2 -->|"批写入：UPDATE take row\n(transcript + diff + 摘要)"| DB
     DB --> UI
```

### Note 输入流程

```mermaid
flowchart LR
    subgraph IN["Note input"]
        N1["录音师对麦输入"]
        N2["录音师打字文字输入"]
    end

    subgraph CTXSRC["上下文来源"]
        ORCH["Orchestrator 会话状态 current_scene_no（由 UI / slate / 剧本导入写入）"]
        QUERY[("SQLite 查询 latest_take_no = MAX(take_no) WHERE scene = current")]
    end

    CTX["inject prompt{current_scene_no, latest_take_no}"]

    NOTEPROC["Gemma 4 多模态 音频 / 文本 + 上下文 → JSON"]

    SQLITE[("SQLite takes.notes 字段")]
    UIERR["UI 弹错 / 提示重试"]

    ORCH --> CTX
    QUERY --> CTX
    CTX --> NOTEPROC

    N1 --> NOTEPROC
    N2 --> NOTEPROC
    NOTEPROC -->|"成功"| SQLITE
    NOTEPROC -.->|"失败"| UIERR
```

### Take 状态标注流程

```mermaid
flowchart LR
    M1["前端 UI 按钮点击 Keeper / NG / Hold"]

    DISPATCH{"Orchestrator 检查 active_take_id"}

    subgraph DBOP["DB 写入"]
        D1["UPDATE takes SET status = ? WHERE id = active_take_id"]
        D2["INSERT take_events (audit log)"]
    end

    UIERR["UI 弹错 （无 active take）"]

    DB[("SQLite")]
    UI_OUT["前端 / 共享视图"]

    M1 --> DISPATCH
    DISPATCH -->|"存在"| D1 & D2
    DISPATCH -.->|"无"| UIERR
    D1 & D2 --> DB
    DB --> UI_OUT
```

### 查询流程

```mermaid
flowchart LR
      subgraph IN["查询输入（多设备发起）"]
          Q1["UI 文本输入"]
          Q2["UI 语音输入"]
      end
  
      subgraph QPSESS["Query mini-session（per-query · max 3 轮 tool_call）"]
          SESS[("Session messages [system, user, tool_call, tool_result, ...]")]
          LLM["Gemma 4 E4B 多模态 意图解析 / tool_call / 答案整理"]
          SESS <--> LLM
      end
  
      subgraph TOOLS["Tools (read-only)"]
          T1["query_takes(filters)"]
          T2["get_scene_takes(scene_no)"]
          T3["search_script(query)"]
      end
  
      DB[("SQLite")]
      SDB[("剧本文本")]
      UI_OUT["前端答案输出 自然语言 + 结构化结果列表（录音师主机 / 共享视图 · 手机 · iPad）"]
  
      Q1 -->|"文本"| SESS
      Q2 -->|"音频"| SESS
      LLM -->|"tool_call ≤ 3"| T1 & T2 & T3
      T1 -.->|"SQL"| DB
      T2 -.->|"SQL"| DB
      T3 -.->|"FTS"| SDB
      T1 & T2 & T3 -->|"tool_result"| SESS
      LLM -->|"final answer"| UI_OUT
```

### 录音师（Admin）用户界面

```mermaid
flowchart TB
      subgraph RECUI["录音师 UI · 主操作者"]
          STATUS["状态栏\n当前场次 / take 编号 / 录制状态\n在线观察者: N 人"]
  
          subgraph TRANSCRIPT["实时转录面板（双通道）"]
              T1["Ch1 对白\npartial 灰 / final 黑"]
              T2["Ch2 备注\npartial 灰 / final 黑"]
          end
  
          subgraph TAKEOPS["Take 操作区"]
              TO1["开始 take / 结束 take（边界触发）"]
              TO2["mark: Keeper / NG / Hold"]
              TO3["Note 录音（按麦）"]
              TO4["Note 打字"]
          end
  
          subgraph SCENEMGR["场次管理"]
              SM1["切换 / 新建场次"]
              SM2["剧本导入：粘贴 / 拍照"]
          end
  
          subgraph TAKELIST["Take 列表（本场）"]
              TL1["take 编号 + status + 简要 note"]
              TL2["点击 → 展开编辑面板"]
          end
  
          subgraph EDIT["编辑面板"]
              E1["改字段：status / notes /\nperformer_issues / audio_quality"]
              E2["改 ASR 转录文本（人工纠错）"]
          end
  
          subgraph QUERY["查询入口（私有 session）"]
              Q1["文本输入"]
              Q2["语音输入"]
          end
  
          EXPORT["导出场记单 / PDF\n（仅录音师 UI）"]
      end
  
      BACKEND["后端 Orchestrator"]
  
      TO1 -->|"边界事件"| BACKEND
      TO2 -->|"mark 事件"| BACKEND
      TO3 & TO4 -->|"Note pipeline"| BACKEND
      SM1 -->|"切场 → 更新 SessionState"| BACKEND
      SM2 -->|"剧本 pipeline"| BACKEND
      E1 & E2 -->|"修改事件 + audit_log"| BACKEND
      Q1 & Q2 -->|"查询 pipeline（私有）"| BACKEND
      EXPORT -->|"导出请求"| BACKEND
  
      BACKEND -.->|"ASR partial 推送"| TRANSCRIPT
      BACKEND -.->|"DB 同步广播"| TAKELIST
      BACKEND -.->|"答案仅返回给发起者"| QUERY
      BACKEND -.->|"presence 在线观察者数"| STATUS
```

### 导演 / 场记（共享视图）用户界面

```mermaid
flowchart TB
      Y1["浏览器访问\n局域网 IP : 端口"]
      Y2["填写名字（必填）\n例：导演 A · 场记 B"]
      Y3["WebSocket 连接\n注册 {connection_id, name}"]
      SHARED["共享视图\n（只读 + 查询）"]
  
      Y1 --> Y2 --> Y3 --> SHARED
      subgraph SHAREDUI["共享视图 · 只读 + 查询"]
          STATUS2["状态栏\n当前场次 / take 编号 / 录制状态"]
  
          subgraph TRANSCRIPT2["实时转录显示"]
              T1["Ch1 对白流\npartial 灰 / final 黑\n（不显示 Ch2）"]
          end
  
          subgraph TAKELIST2["Take 状态列表"]
              TL1["take 编号 + status + 公开 note"]
              TL2["（只读，不可编辑）"]
          end
  
          subgraph QUERY2["查询入口（私有 session）"]
              Q1["文本输入"]
              Q2["语音输入"]
          end
  
          HISTORY["历史浏览\n过往场次 + take"]
      end
  
      BACKEND2["后端 Orchestrator\n/api/shared/*（read-only + query）"]
  
      BACKEND2 -.->|"ASR partial 推送"| TRANSCRIPT2
      BACKEND2 -.->|"DB 同步广播"| TAKELIST2
      BACKEND2 -.->|"DB 同步"| HISTORY
      Q1 & Q2 -->|"查询 pipeline（私有 session）"| BACKEND2
      BACKEND2 -.->|"答案仅返回给发起者"| QUERY2
```

---

## Take 记录数据结构（草案）

每条 Take 写入 SQLite 的字段：

| 字段 | 来源 | 说明 |
|---|---|---|
| scene | 上下文 | 场次编号 |
| shot | 上下文 | 镜次编号 |
| take_number | 自动递增 | 本场第几条 |
| start_ts / end_ts | 系统时间戳 | Take 起止时间 |
| transcript_ch1 | ASR + Diarization | Ch1 对白转录全文（含说话人标签） |
| status | 手动 mark / Ch2 解析 | Keeper / NG / Hold / TBD |
| performer_issues | Ch2 解析 | 涉及哪个演员、什么问题 |
| audio_quality | Ch2 解析 | clean / noisy / clipped 等 |
| script_diff | LLM | 与剧本的偏差（漏词/改词/加词） |
| notes | Ch2 原文 + 候补 note | 录音师全部文本备注 |

技术层字段（噪音检测、削波等）由信号处理层写入，不归 LLM。

---

## 不在本 spec 范围内

- 音频采集与 ASR 的具体实现（归 backend-asr）
- SQLite schema 详细定义（归 backend-agent）
- 前端 UI 设计与交互细节（前端技术栈见 `docs/specs/2026-05-26-system-architecture.md` §9 前端结构）
- BWF / iXML 元数据联动（P3 阶段）
- 导演音频直接采集（第三通道，设计成本较高，当前不纳入）

---

## 开放问题

1. Ch2 备注通道：用独立物理麦，还是录音机的通话键（PTT）？影响采集方式。
2. Take 边界检测：靠录音师手动触发，还是系统自动检测 Cut 信号？
3. 自然语言查询入口：录音师用键盘打字，还是 Ch2 也能发起查询？
