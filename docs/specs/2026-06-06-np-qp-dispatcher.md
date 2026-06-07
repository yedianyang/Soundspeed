# NP/QP 自动调度器（入口层）设计

- 日期：2026-06-06
- 状态：设计稿（待评审）
- 分支：`worktree-feat+np-qp-dispatcher`（fresh 切自 origin/main `7be5303`）
- 关联：`docs/specs/2026-06-05-qp-tool-loop.md`（QP 内核 + 入口层 §3.1/§3.2.1，本稿是它入口层的落地）、`docs/2026-06-06-input-pipeline-architecture-map.md`（四业务线 × 单实例三模态）
- 核验来源：6-agent 只读 workflow（2026-06-06）对着活代码逐一核准六个改动落点，下文 file:line 均已实证。

---

## 0. 背景与范围

QP 内核（`run_tool_loop` + 5 工具 + 只读墙 + `POST /query`）已合并进 main（PR #39），零悬挂门。4.x NP 栈（文本 + 语音 note）也在 main（PR #29/#35）。现在补**入口层**：同一个文本框、同一个语音框进来，Gemma 自己判这条是 **note（记录）还是 query（查询）**，分流到 NP 或 QP。

这对应 QP spec §3.1（文本 option 1）+ §3.2.1（语音 option a），当时标「延后，等 4.x 合并」。4.x 已合，现在做。

### 0.1 一句话定位

调度器全部活在**入口层**。内核层（`run_tool_loop`、QP 工具、只读墙、`POST /query`）对 NP 零依赖，本设计**不破坏这条边界**——内核继续独立可跑可 demo，调度器是叠在上面的薄层 + 一处让内核 toolset-agnostic 的机械改动（INV2）。

### 0.2 范围（用户 2026-06-06 拍板「拆 follow-up，文本先合，spike 出结论再定语音」）

四块拆两批。**本 branch（文本先合）**：

- **块③ 文本调度器**——`route_memo` 二分类，把 memo 框的 QP 问题路由到 QP 并返回答案（用户要的能力）。不依赖 spike。
- **块① 语音 spike（当调研做完，出结论）**——用真语音 WAV 实证 binary-first / option-a 的可行性与形态，结论写回本 spec + follow-up，不在本 branch 建语音实现。

**follow-up branch（spike 出结论后）**：

- **块② INV2 参数化**——`run_tool_loop` toolset-agnostic。**唯一消费者是语音统一循环**（文本调度器不碰 `run_tool_loop`），故跟语音实现一起落地。
- **块④ 语音调度器**——形态由块① spike 结论定（option-a 或 binary-first）。

> 排序更正：原计划「全装一个 branch、spike 最先」。6-agent 实证显示语音比 §3.2.1 设想深好几层（新 handler / auto+audio 未接 / 6>5 / note 工具终结性 + `_finalize_np` 抽取），用户据更正信息改为**文本先合、语音拆 follow-up、spike 当调研定向**。INV2 因唯一消费者是语音，随块④ 迁去 follow-up。

### 0.3 明确延后（非本设计）

- 结构化输出 `data/refs`——v2。
- 场次别名表——v10 迁移，v2。
- thinking 开关——可选准确率增强，v2。

---

## 1. 架构分层（重申，守住边界）

```
┌───────────────────────── 入口层（本设计）─────────────────────────┐
│  memo 框（文字 / 语音，与 NP 共用）                                │
│   文字: POST /notes          语音: POST /notes/voice              │
│        │                          │                              │
│        ▼ 文本分类器                ▼ 语音调度器                    │
│   route_memo(kind)            （形态待 spike：option-a / binary） │
│   ┌────┴────┐                  ┌────┴────┐                       │
│ note      query              note      query                    │
│   │         │                  │         │                       │
│   ▼         ▼                  ▼         ▼                       │
│ 现有 NP   QP 循环            现有语音 NP  QP 循环                  │
└─────────────┼──────────────────────────┼────────────────────────┘
              │                          │
┌─────────────┼──────────────────────────┼──── 内核层（已合 main，不动）┐
│  POST /query ┘  run_tool_loop（INV2 后 toolset-agnostic）          │
│                 5 QP 工具 + 只读墙 → 自然语言文本                   │
└───────────────────────────────────────────────────────────────────┘
```

要点：内核零依赖不变。INV2 只把「循环用哪个 toolset」从硬编码常量变成形参，内核仍可单独跑（默认 `query_session`）。调度器是入口层的薄分流。

---

## 2. INV2：run_tool_loop 参数化（块②，机械）

### 2.1 现状（实证）

`run_tool_loop`（qp_query.py:67-124）把工具集硬钉模块常量 `_QP_TASK="query_session"`（:28），在两处用：step A `service.infer(..., task_type=_QP_TASK)`（:89）、step B `service.infer_tool(..., task_type=_QP_TASK)`（:96-99）。

`service.infer`/`infer_tool`（service.py:251/314）**已经**只认 `task_type` + 可选 `tool_choice` 覆盖，tools/tool_choice 全在 `_submit`（:192-249）里从 `TASK_CONFIG[task_type]` 取。**没有** tools/toolset kwarg——想换工具集，唯一杠杆是换 task_type 这个 key。

### 2.2 改动（精确）

`run_tool_loop` 加形参 `task_type: str = "query_session"`，替换 :89 与 :96 两处 `_QP_TASK`。`service.py`/`config.py` **零改动**。`run_qp_query`（:142）可不动（默认值兜住）。

要让一个**新** toolset 可用，只需在 `TASK_CONFIG` 加一条新 key（带 `tools` + `tool_choice="auto"`）并把 key 当 `task_type` 传进来。

### 2.3 gotchas（实证）

- **step A 必须 `tool_choice="auto"`**：循环靠 `_scrape_tool_name` 从 FunctionGemma auto content `<|tool_call>call:NAME` 抠名。新 task_type 若用 forced tool_choice，step A 行为就变了。
- **task_type 必须是 TASK_CONFIG 里的真实非保留 key**：`_submit`（service.py:212-213）校验，未知 raise ValueError，`_reserved=True`（如 agent_init）raise NotImplementedError。
- **priority 在调用点硬编码 =1**（:89/:99），不从 config 取。query_session 本就 priority 1，一致；若新 task_type 想要别的优先级得另议。
- **system prompt 不随 task_type 走**：config 的 `system` 只是参考模板，service 不注入；system 由组装 `messages` 的人拥有（`run_qp_query` 自己拼 `_QP_SYSTEM`）。调度器各分支自己拼 system。
- **测试**：`test_qp_loop.py` 不传 task_type 调 `run_tool_loop`（:77/92/111/129/146），默认值保绿。`test_qp_config.py` 钉 query_session 的 5 工具 + auto，别动 query_session 条目。

---

## 3. 文本调度器：route_memo（块③，独立交付）

### 3.1 插入点（实证）

`takes.py` `create_note`（:325-376），在 `parse_note` 成功（:342）之后、`orchestrator.run_np_async(...)`（:345）之前插。`note: NoteStruct` 已就绪，`note.raw_text` 是分类输入。`request.app.state.llm_service` + `orchestrator.dal` 可取（同 query.py:35-38）。

```
parse_note(body.text, ts) → note          # :339-342 不动
─── 插入：route_memo 分类 ───
  kind = classify(note.raw_text)           # forced infer_tool(task_type="memo_route")
  if kind == "query": → QP 分支（见 3.4）
  else（含任何分类失败）: ↓
orchestrator.run_np_async(...)             # :345-376 原样，只被 gate
```

### 3.2 新 forced 工具 + task_type（照搬 note_struct 三步）

1. **工具 builder**：新增 `ROUTE_TOOL_NAME="route_memo"` + `build_route_memo_tool()`，单枚举参 `kind: ["note","query"]`（扁平标量，required）。形如 tools/note.py:16-66。
2. **registry**：Tier-1 forced 工具运行期**绕过** registry（config 直接调 builder，service 透传）。route_memo executor=None，注册仅对称用、可选。分类结果由 Python 读 `tool_calls[0].function.arguments.kind` 分支，不走 `get_executor`。
3. **TASK_CONFIG**：加 `memo_route` 条目，仿 note_struct（config.py:152 / `_build_note_task_config` :32-51）：小 max_tokens（~64）、低温、priority 1、`tools=[build_route_memo_tool()]`、`tool_choice` forced 到 route_memo。builder 不 import pipeline 就可 eager（同 build_l2_tool）。

### 3.3 fail-closed 到 note（硬规则）

分类器任何异常/超时/参数畸形 → **默认 note**，原样进现有 NP。分类器宕了绝不能挡掉 note 提交。`parse_note` 已对未知 @category raise 400，这条独立。

### 3.4 query 分支 + conn_id 缺口决议

**缺口（实证）**：`NoteCreateBody`（takes.py:295-301）只有 text/ts/client_id，**没 conn_id**；但 QP 答案广播 `qp.answer.{conn_id}`（query.py:49-54）要 conn_id。且两分支响应形状不对称：note 是 `run_np_async`（sync fire-and-forget，202）；`run_qp_query`（:142）是 async 返回 str。

**决议**：给 `NoteCreateBody` 加可选 `conn_id: str | None`，query 分支也做成 **fire-and-forget + 202 + `qp.answer.{conn_id}` 广播**，与 note 分支对称（两支都 202，结果都走 WS：note→`note.processed`，query→`qp.answer`）。前端 memo 框本就持 WS 连接，提交时带 conn_id 即可。这把「异步广播 fire-and-forget」做成两分支统一契约，避免 /notes 一会儿 202 一会儿同步返回。query 分支新增一个 `run_qp_async`-类的 orchestrator 方法（schedule task → `run_qp_query` → 广播 `qp.answer.{conn_id}`），镜像 `run_np_async`。

---

## 4. 语音 spike：硬门 + 形态 A/B（块①，最先做）

### 4.0 块① 前置依赖：真语音 WAV 样本（先搞定，否则开跑即卡）

spike 要 4B 真听音频转写+路由，**必须有真语音 WAV 字节**。`run_np_voice` 的现有测试用 stub bytes，真语音样本仓库里大概率没有。块① 的**第一步**是录/取一小批真语音 WAV：至少 query 样本（「第一场拍了多少条」）+ note 样本（「这条过了」「收音有点小」）各几条。取法：复用现有 Capture / enroll 后端现场麦通路录，或请用户录几条。这是块① 的硬前置，不解决 spike 无从跑起。

### 4.1 为什么 spike 是真门，不是走过场（实证，三条机制级反证）

spec §3.2.1 设想「语音 memo → 一个多模态 auto-tool 调用搞定 ASR+分类+路由+回答」。核验发现这个形态**当前在机制上不成立**：

1. **「音频 + auto」选不了工具**：`client.py` audio 分支（:159-173）在 text+tools handler swap（:178-185）**之前** return。带音频时工具声明不渲染（多模态 handler 的 CHAT_FORMAT 无 FunctionGemma `<|tool>` 宏），且 auto 不上 grammar → 模型吐散文 → `_scrape_tool_name` 拿 None → 循环 hop 1 就终止。语音 NP 能跑只因它 **forced**（grammar 兜，不需渲染工具）。
2. **没有同时支持音频 + 工具渲染的 handler**：多模态 handler 不渲染工具；原生 FunctionGemma formatter（`_build_native_tool_handler`，client.py:123-152）是纯文本、无 mtmd 音频通路。要 audio+tools 并存得**新造**一个把工具声明注入多模态 chat format 的 handler/模板。这比「text-only swap」深一层。
3. **两步走跨不过音频跳**：step B 是独立纯文本推理，但参数（take_id/category/content 或查询意图）活在音频里、只在 hop 1 存在。纯文本 step B 没音频可抽。

加上 `infer_voice_tool`（service.py:350）**无 tool_choice 覆盖参**（只能 forced 静态 config 工具），以及合并工具集 **6 > FC 的 ≤5 线**——语音优雅版叠了「新 handler + 未接的 auto+audio 推理 + 超线工具数」三重未验风险。**这正是 spike 必须先跑的理由。**

### 4.2 两个形态，成本不对称 → 先跑便宜的 B

| 形态 | 机制 | spike 成本 | 风险 |
|------|------|-----------|------|
| **A：option-a（优雅版）** | 新建 audio+tools handler，渲染 6 工具，audio hop 上 auto 选工具（note→终结结构化，query→转文本续 QP 循环）。需验 auto pass 能否直接吐可用 tool 选择，或要不要二次 audio forced 取参 | **高**——A 的 spike 本身就要先建出块④ 最硬的那层（audio+tools handler 注入）才能跑，**不是探针，是「先把最难的活建出来再测」** | 6>5 路由、auto+audio 未接、4B 可靠性全未验 |
| **B：binary-first（稳）** | 一个 forced audio 调用做粗二分（route_memo over audio，或返 `{kind, transcript}`）→ note 走现有 `run_np_voice`（forced structure_note），query 走现有 5 工具 QP 文本循环（喂 transcript）。每次选 ≤5、复用两条已绿路径 | **低**——`infer_voice_tool` 加一行 tool_choice 透传（`_submit` 已接）+ 一个 audio 二分 task_type，复用两条已绿路径 | forced 二分 4B 可靠（语音 NP 已证）；主要不确定是 query 分支的 transcript 取得 |

**关键：B 不是 A 的降级功能，是同一需求的另一机制。** 用户要的是「语音也能自动分流 note/query」——B 完整满足这个需求，只是没 A 优雅。A 的「一次调用搞定」更省一跳，但代价是先建最硬的 handler 注入。

**跑法（先 B 后 A，便宜信号先行）**：
1. **先跑 B probe**（便宜）：B 通过 → 已经有一个可交付的语音调度器，块④ 可走 B。A 从「前置赌注」降级为「可测量的优雅升级」，有余力再做。
2. **B 通过后再评 A**：值不值得为「省一跳」建 audio+tools handler。A probe 跑通且收益明显 → 升级到 option-a；否则块④ 收在 B。
3. **B 都不稳**（forced audio 二分都不可靠，可能性低，语音 NP 已证 forced audio 可行）→ checkpoint 停下，语音降级「whisper 先转文本 → 文本调度器」，本 branch 语音块改延后（不污染已成的文本块 + INV2）。

**门判据**：用 §4.0 的真语音 WAV，各形态各样本跑 N 次，看分类/路由正确率 + 是否取到可用参数。B 的 spike 接法（tool_choice 透传）若过即转正进生产；A 的 handler 注入若过同样转正。

### 4.3 spike 结论（2026-06-06 实测，3 条真语音 WAV）

> **本节修正 §4.1/§4.2 对形态 A 的成本判断。** §4.1 说「音频+auto 选不了工具」——根因经一手源码定位属实，但根因本身暴露了一条 §4.2 没设想的轻绕路：**不建新 handler，直接把工具声明文本注入 system content**，auto 走纯生成（不靠 grammar/tools 渲染），输出用 `_scrape_tool_name` 抠。§4.2 表里「A=高成本/先建最硬的 audio+tools handler」的判断因此被修正——**形态 A 不必新建 handler，实测可行，且比 B 多省一跳。** 用户据结论（2026-06-06）拍板采形态 A。

#### 实测 setup

- 真语音 WAV 由用户录制后放入 `experiments/2026-06-06-voice-dispatch-spike/wav/`，经重采样转 16k mono PCM16（对齐生产 `blobToWav16kMono` 金标准）。样本：2 条 query（「第一场拍了多少条」「这个剧本一共有几场」）+ 1 条 note（「这条过了」）。
- 模型 gemma-4-E4B-it-Q4_K_M + mmproj-F16（llama_cpp 0.3.25）。
- 探针脚本均放 `experiments/2026-06-06-voice-dispatch-spike/`（已 gitignore，不进源码树）。

#### 三个探针结果

**C2 — probe_binary.py（forced audio 二分）**

单工具 forced `route_memo`（kind: note|query），grammar 兜，**不渲染 6 工具声明**。组多模态 messages（AUDIO_SENTINEL + 系统提示），`infer_voice_tool(task_type="memo_route_voice", tool_choice=forced_route_memo)` 走 grammar forced，验证 forced audio 二分通路坐实。

| 样本 | 内容 | forced 工具 | 结果 |
|------|------|------------|------|
| query-1 | 第一场拍了多少条 | route_memo | kind=query ✓ |
| query-2 | 这个剧本一共有几场 | route_memo | kind=query ✓ |
| note-1 | 这条过了 | route_memo | kind=note ✓ |

正确率 3/3。结论：forced audio 二分稳定可靠，grammar 兜住。**注意**：C2 走的是单二分工具，机制与 corrected-C3 的 6 工具文本注入不同——两条通路分别验证，共同支撑形态 A 的两跳。

**corrected-C3 — probe_c3_text_decl.py（工具声明文本注入 + 纯生成）**

用 `vocab_only=True` 加载 GGUF，通过 `Jinja2ChatFormatter` 渲染 6 工具 + dummy messages，从渲染结果正则提取全部 `<|tool>...<tool|>` 块（GGUF 内嵌 chat_template 的原生工具声明格式），拼接后注入 system content。Gemma4ChatHandler.CHAT_FORMAT 把 system 折进首个 user turn 前缀，模型在推理时「看见」工具声明。`infer_voice` 走 `task_type="voice_dispatch_free"`（无 tools/tool_choice/grammar 参数的专用配置），纯生成，输出用 `_scrape_tool_name` 正则抠工具名。

| 样本 | 内容 | 模型自发吐出 | 抠出工具 | 正确 |
|------|------|------------|---------|------|
| note-1 | 这条过了 | `<|tool_call>call:structure_note{...}<tool_call|>` | structure_note | ✓ |
| query-1 | 第一场拍了多少条 | `<|tool_call>call:count_takes{...}<tool_call|>` | count_takes | ✓ |
| query-2 | 这个剧本一共有几场 | `<|tool_call>call:query_database{"sql":"SELECT COUNT(*) FROM scenes..."}<tool_call|>` | query_database（自写 SQL）| ✓ |

正确率 3/3。关键发现：**模型在工具声明文本注入后自发吐出格式规范的 function-call 字符串**，`_scrape_tool_name` 可直接抠出工具名和参数 JSON。注意模型还自主写出了合理 SQL（query_database），说明 6 工具语义在 system 文本层面被充分理解。**这条路不需要新建任何 audio+tools handler，复用现有两步走零件。**

**端到端 — probe_qp_voice_e2e.py（全链路打通）**

2 条 query 语音走完 A→B→C→续跳全链路：hop A（`infer_voice` 文本注入 6 工具 + `_scrape_tool_name` 抠名）→ hop B（`infer_voice_tool` forced=抠到的工具名，audio 取参）→ `_run_executor`（真跑 SQLite）→ 续跳 `run_tool_loop`（文本，出自然语言答案）→ `schedule_qp_broadcast`（广播 `qp.answer.{conn_id}`）。

| 样本 | 最终答案 | 正确 |
|------|---------|------|
| 第一场拍了多少条 | 「第一场拍了 7 条」 | ✓ |
| 这个剧本一共有几场 | 「这个剧本一共有 1 场」 | ✓ |

2/2 真答案（对齐测试数据库内容）。**每块都是已验证原语的组合，没有新建任何 handler。**

#### §4.1 原判修正（一手源码，load-bearing）

§4.1 反证 #1 说「音频+auto 选不了工具」——根因经源码精确定位：

- `llama_chat_format.py` `Llava15ChatHandler.__call__` 渲染模板时**只传 messages、不传 tools**（:2912），工具声明从没进过模型上下文。
- auto（`tool_choice` 非 dict）**不建 grammar、不解析 tool_calls**（:3068-3121，只有 `tool_choice` 为 forced dict 才建 grammar 解析 tool_call）。
- 因此模型当然不吐 function-call 格式，`_scrape_tool_name` 拿 None，§4.1 结论本身正确。

**但这根因指向了 §4.2 没设想的绕路**：既然 handler 根本不传 tools，那就**绕过 handler**——把工具声明当纯文本折进 system content，走 `infer_voice` 纯生成（无 grammar），用正则抠输出。实测 Gemma-4-E4B 在这条路上自发吐出规范 function-call 格式（corrected-C3 3/3）。

所以：
- §4.1 反证 #1 根因正确，结论「优雅版在机制上不成立」需修正为「**原设想的 auto+audio handler 路不通，但存在等效的文本注入绕路**」。
- §4.2 形态 A 成本标「高——须先建 audio+tools handler」被修正为「**中——工具声明文本注入 + 抠输出，不建新 handler，复用现有两步走零件**」。
- §4.2 的「先跑便宜的 B」策略已完成使命（B 的 forced 通路由 C2 坐实）；A 经 corrected-C3 + e2e 验证可行且更优，形态决策收在 A。

#### 形态决策（用户 2026-06-06 拍板）

**采形态 A（audio-auto，单入口，无独立 ASR）**。

两步走机制（已全部用已验证原语拼成）：

```
语音 WAV
  │
  ▼ hop A：infer_voice（文本注入 6 工具声明 + 场次目录）+ _scrape_tool_name 抠名
  │
  ├─ structure_note ─▶ hop B：infer_voice_tool(forced=structure_note, audio) 取参
  │                    → _parse_tool_call → _persist_np_output（落 NP）
  │
  └─ QP 工具（count_takes / query_database / ...）
       ▼ hop B：infer_voice_tool(forced=工具名, audio) 取参
       ▼ hop C：_run_executor 真跑（SQLite / 内存）
       ▼ 续跳：run_tool_loop（纯文本，出自然语言答案，复用现有 5 工具循环）
       ▼ schedule_qp_broadcast → qp.answer.{conn_id}
```

hop A = corrected-C3（文本注入 + 纯生成 + 抠名）；hop B = C2（forced audio 取参）；hop C + 续跳 = 现有 `run_tool_loop`（**不改**，e2e 实证直接接上即可）。

hop A 抠到 None（模型未吐 function-call）的兜底：fail-closed 当 note 处理（hop B forced=structure_note，降级到语音 NP 已绿路径）。

音频只在 hop A/B eval。续跳是纯文本（messages 里有 tool_call trace + executor 结果，模型靠上下文收尾，无须重 eval 音频或转写）。e2e 实证可行。

#### 诚实边界

N 小（2 query / 1 note），是**可行性坐实 + 方向全对**，不是准确率基准。hop A 的 `_scrape_tool_name` 正则依赖模型自发吐出规范格式，在噪声语音或极端 prompt 下可能漂移。**上线前需更多样本测 hop A 抠名准确率**，尤其 query/note 边界样本（询问式备注、感叹式查询）。

#### 带进 follow-up 的实现要点

1. **INV2 是否必须**：e2e 实证 voice hop A 用 6 工具自定义入口（文本注入），路由到 query 后续跳直接复用现有 5 工具 `run_tool_loop` 即可跑通。故 INV2（`run_tool_loop` toolset-agnostic 化）是否必须待实现期定——**可能只需 voice 自己的 hop A + 现成 `run_tool_loop` 续跳，不改 `run_tool_loop`**。若确认不必改则 Part E 跳过。

2. **note 分支终结性**：hop A 抠到 `structure_note` → 走 note 分支（hop B forced structure_note → `_finalize_np`/`_persist_np_output` 落库）；抠到 QP 工具 → query 分支。两条分支清晰，不走统一 executor 循环（`structure_note` executor=None，不能进 `_run_executor`）。

3. **conn_id/qp.answer 广播**：query 分支答案复用块③已建的 `schedule_qp_broadcast`（`qp.answer.{conn_id}`），零改动。

4. **音频只在 hop A/B，续跳无需重 eval 音频**：`_worker` 仅 `payload.audio is not None` 才加 audio kwarg，纯文本跳自动恢复；hop 1 回喂前须从 messages 历史里丢掉 `AUDIO_SENTINEL` content part，否则后续纯文本跳 `load_image` raise（multimodal.py:59-62，spec §5.4 已记录）。

---

## 5. 语音调度器实现（块④，条件性，形态见 §4）

无论 A 还是 B，note 分支都要解决同一个结构问题：**note 工具是终结动作，不是 QP executor**。

### 5.1 note 工具终结性（实证）

`structure_note` 在 registry executor=None（registry.py:94，刻意）。若统一循环照搬 `run_tool_loop`，模型选 structure_note 会走 `_run_executor`（qp_query.py:50-64）返 `{"error":"...无 executor"}` 然后**空转**，不终止。所以统一循环必须**按工具名特判 note 家族**：选 structure_note → 终结 + `_parse_tool_call`（np_note.py，纯函数可复用）→ 落库；选 QP 工具 → 现有 execute-and-feed-back。

### 5.2 抽 _finalize_np 的落库副作用（实证，load-bearing）

`Orchestrator._finalize_np`（orchestrator.py:598-682）把两件事缠一起：(A) await runner + 映射 runner 域失败（NPParseError→parse_error / TimeoutError→timeout / ModelUnavailableError→model_unavailable）；(B) 落库副作用。统一循环要复用的是 (B)。

抽出独立单元 `_persist_np_output(output: NPOutput, *, ts, client_id, raw_text_override)`，拥有：
- `insert_note`（自带 sqlite3.IntegrityError→`note.failed("take_not_found")` 守卫，FK miss 是落库期失败）
- **无条件** publish `note.processed`（durable-once 不变量，Mark 失败不得回滚）
- 若 `output.category in _STATUS_CATEGORIES`（pass/ng/keep，orchestrator.py:45）→ `set_take_status` + `get_take` + publish `take.changed`，try/except 只 log、绝不回滚 note.processed。

`_finalize_np` 保留 (A)、调 `_persist_np_output`。统一循环 note 分支 `_parse_tool_call → _persist_np_output` 直接调。

**硬约束**：`_persist_np_output` 依赖 `self.dal` + `self.publish`（Orchestrator 绑定），而 `run_tool_loop` 是自由函数有 dal+service、**无 publish/session**。所以统一循环要么做成 Orchestrator 方法（持 self），要么把 publish-callable + np-context（`_build_np_input` 出的 NPInput，session/dal 耦合）显式注入 note 分支。「抽 _finalize_np 成 callable」不能默认 publish/session 跟着走，必须显式接线。

### 5.3 状态生命周期对账

`_emit_np_status_preamble`（:531）/`_np_done_callback`（:724）发 `llm.status`（downloading/loading/running/idle），task_type 都是 `note_struct`。统一循环跑自己的 task_type。状态怎么发要对账（note 分支重发 note_struct 状态，还是循环状态覆盖），别盲抄。

### 5.4 音频跳 + 后续文本跳（实证）

audio 只在 hop 1。`_worker`（service.py:417-426）仅当 `payload.audio is not None` 才加 audio kwarg，文本跳调用形状不变、auto 路径自动恢复（handler swap 重新可达）。但 hop 1 后**必须从 message 历史里丢掉 AUDIO_SENTINEL content part**，否则后续纯文本跳带着 image_url 但无 pending audio，`load_image` 会 raise（multimodal.py:59-62）。回喂仍用纯文本约定（不用 OpenAI assistant{tool_calls}，会撞 GGUF Jinja `raise_exception` 错，qp_query.py:114-118）。

---

## 6. 文件落点

| 文件 | 动作 | 块 | 说明 |
|------|------|----|------|
| `backend/pipelines/qp_query.py` | 改 | ② | `run_tool_loop` 加 `task_type="query_session"` 形参，替换 2 处 `_QP_TASK` |
| `backend/llm/tools/transcript.py`（或新 tools 文件） | 改/新建 | ③ | `ROUTE_TOOL_NAME` + `build_route_memo_tool()` |
| `backend/llm/config.py` | 改 | ③④ | 加 `memo_route`（forced 二分）；语音若走 option-a 加 `memo_unified`（6 工具 auto，note 工具须 lazy import 避环） |
| `backend/api/routes/takes.py` | 改 | ③ | `NoteCreateBody` 加 `conn_id`；`create_note` 插分类器 + query 分支 |
| `backend/core/orchestrator.py` | 改 | ③④⑤ | 抽 `_persist_np_output`；加 `run_qp_async`（query 文本分支 fire-and-forget 广播）；语音统一循环编排 |
| `backend/llm/client.py` | 改（条件） | ①④ | 仅 option-a：audio+tools handler（新模板注入工具声明到多模态 chat format） |
| `backend/llm/service.py` | 改（条件） | ①④ | 仅语音：`infer_voice_tool` 加 tool_choice 覆盖（镜像 infer_tool，`_submit` 已接，一行透传） |
| `backend/tests/...` | 新建 | 全 | 见 §7 |

---

## 7. 测试策略

- **L0**：`build_route_memo_tool()` 结构、参扁平、name 一致；`memo_route` config 形状。
- **L1**：`_persist_np_output` 抽取后行为不变（insert_note + note.processed 无条件 + pass/ng/keep 打 Mark + durable-once）；用 StubDAL + 捕获 publish 断言事件序。回归保证抽取零行为漂移（对比抽取前 `_finalize_np` 的事件序）。
- **L2 文本调度**：StubService forced 返 `{kind:"note"}`/`{kind:"query"}`，断言分流；分类失败 fail-closed 到 note；conn_id 透传到 qp.answer。
- **L2 INV2**：`run_tool_loop(task_type=...)` 透传到 infer/infer_tool（StubClient 断言 last task_type）；不传保默认。
- **L3 spike（写块④实现前先跑）**：真语音 WAV，A/B 两形态正确率实测，门判据见 §4.2。
- 每个 implementer 子代理首条 `First run the tests`，基线对齐 main `776 passed, 12 skipped`。

---

## 8. 风险

| 风险 | 级别 | 缓解 |
|------|------|------|
| 语音 option-a 三重未验（新 handler / auto+audio / 6>5） | 高 | spike 硬门 A/B；不过降 binary-first 或 whisper 转文本 |
| 抽 `_persist_np_output` 漂移行为（破 durable-once / Mark 顺序） | 中 | L1 回归对比事件序；保留无条件 note.processed + Mark 只 log |
| 自由循环接 publish/session 接错 | 中 | §5.2 显式接线决策；统一循环做成 Orchestrator 方法持 self |
| conn_id 缺口决议改前端契约 | 低 | NoteCreateBody 加可选 conn_id，前端 memo 框带上；缺省退化为无广播 |
| route_memo 4B 二分不准 | 低 | forced+grammar（Tier-1 最稳）；fail-closed 到 note |
| 文本块被语音 spike 阻塞 | 低 | 块③不依赖 spike，可先合 |

---

## 9. 开放问题 / 待评审

1. 语音形态 A vs B：**由 spike 实证定**（§4.2），非拍脑袋。**先跑便宜的 B 拿快信号**（B 的 spike 是一行透传 + 复用已绿路径），B 过即有可交付语音调度器；A（优雅版）的 spike 本身就是块④ 最硬的 handler 注入，故从「前置赌注」改为「B 之上可测量的优雅升级」。用户原意「优雅版」=A，仍是目标，只是不前置赌。
2. 统一循环接 publish/session：做成 Orchestrator 方法（持 self）还是注入 callable？倾向前者（§5.2）。
3. 块顺序（拆分后）：**本 branch** = 块③ 文本调度器 + 块① 语音 spike（调研），两者互不依赖可并。**follow-up** = 块② INV2 + 块④ 语音实现（INV2 是块④ 的硬前置，同 branch 落）。
4. query 文本分支 `run_qp_async` 广播 vs 同步返回：本稿选广播（§3.4 对称 202）。

---

## 11. 块④ 语音调度器合并进队列模型的 reconcile（2026-06-07）

语音调度器（块④，PR #48 `worktree-feat+voice-qp`）从 `bfbc8be`（#45）fork，早于 #49。#49 已把前端「就地反馈」从气泡模型换成**队列 + 档案模型**（`addQa` / `resolveQa` / `qpAnswerArrived` / `InlineFeedbackQueue` / `LLMArchiveSheet`）并合进 main。本节记录 #48 onto #49 的解冲突约定 + 语音 query 答案进队列的接缝。

### 11.1 解冲突规则

- **后端冲突以 #49（现 main）为准**：`voice_dispatch.py` / `voice_dispatch_helpers.py` 是净新文件；`takes.py` 的 `/notes/voice` 路由、`app.py` 的 `_broadcast_wrapper`（广播 `QpAnswerPayload(..., client_id=client_id)` 到 `qp.answer.{conn_id}`，不重跑 query）、`config.py` / `service.py` 取 #48 版自动合并，已验证正确。`api.ts` 的 4 参 `postVoiceNote(wav, clientId, ts, connId)` 同样保留（带 connId → 后端走 voice dispatch）。
- **前端冲突以队列模型为准**：哪边和队列模型一致就留哪边，#48 的气泡遗留（`setQpAnswer` / `removePending` 直接清 pending）一律丢弃。
- 四个冲突的具体解法：
  1. `core/events.py`（`QpAnswerPayload.client_id`）——字段两边一致，保留 #49 侧 docstring（文本 + 语音两条路径共享同一字段契约）。
  2. `types/api.ts`（`QpAnswerMsg`）——保留 #49 侧队列向注释，删 #48 气泡向注释。
  3. `components/admin/NoteList.tsx`——#49 已删此文件（被 `InlineFeedbackQueue` / `LLMArchiveSheet` 取代），保留删除。
  4. `hooks/useLiveConnection.ts`——保留 #49 队列结构，`qp.answer.{CONN_ID}` handler 由 `resolveQa` 改调新方法 `qpAnswerArrived`。

### 11.2 语音 query「答案到达时 promote 进队列」接缝（#47/#48 都没有）

文本 query 提交时 202 同步返回 `kind:"query"`，`MemoInput` 当场 `addQa` 一条 processing；答案到达走 `resolveQa` 落到那条。**语音 query 提交时 202 只返回 `kind:"dispatching"`**，那一刻不知 note/query，所以语音不能在提交时 `addQa`——只插一条 `kind:"voice"` 的 pending。语音 query 的答案只能在**答案到达时**才进队列。

新增 store 方法 `qpAnswerArrived(clientId, answerText)`（`store/session.ts`），`useLiveConnection` 的 `qp.answer.{CONN_ID}` handler 调它：

- 存在 client_id 匹配的 qaItem（文本 query 预建的 processing 条）→ 等价 `resolveQa`（置 `status:"done"` + answer，`archiveUnread + 1`）。
- 否则存在 client_id 匹配的 `kind==="voice"` pending（语音 query）→ `removePending(clientId)` + `addQa({ client_id, question, status:"done", answer, ts })`，`archiveUnread + 1`。
- 否则（无 qaItem 也无 pending：陈旧/旧广播）→ no-op。

### 11.3 MVP 局限（有意决定，非 bug）

`qp.answer` 只带 `answer_text` / `client_id`，不带问题原文。语音 pending 的 `rawText` 为空（语音 202 时正文未知），故语音 query 在档案里的「问题」只能显示通用占位「🎤 语音提问」。注意 `MemoInput` 给语音 pending 的 `content` 是乐观文案「语音备注」——那是为 note 渲染准备的，对 query 是错标，故 promote 时**只看 `rawText`（空）走 fallback**，不用 `content`。测试 `src/store/session.test.ts` 断言这条 fallback 文案。要带问题原文需后端在 `qp.answer` 多带一个字段，留作 follow-up。

### 11.4 测试基础设施（计划外补齐）

前端此前**无任何测试栈**（package.json 无 vitest、无 config、无既有 store 测试）。本次为 `qpAnswerArrived` 补红绿测试时一并 bootstrap：`vitest` devDep + `test` 脚本 + `vitest.config.ts`（node 环境、复用 `@` alias）+ `vitest.setup.ts`（node 25 自带的 localStorage 残缺，注入内存版给 store 的 `readToken`）。`src/store/session.test.ts` 三例：文本路径 processing qaItem → done；语音 pending → promote（fallback 问题）；无匹配 → no-op。

## 12. 变更记录

- v0.1（2026-06-06）：初稿。基于 6-agent 只读 workflow 实证六落点。确立四块 + 风险前置排序、INV2 精确 diff、文本 route_memo 插入点 + conn_id 决议 + fail-closed、语音 spike 硬门 A/B（option-a vs binary-first，附三条机制级反证说明优雅版比 §3.2.1 设想深）、note 工具终结性 + `_persist_np_output` 抽取 + 自由循环接线约束。
- v0.2（2026-06-07）：补 §11。块④ 语音调度器（#48）在 #49 队列模型之后合并的 reconcile——解冲突规则（后端 → #49、前端 → 队列模型）、四冲突具体解法、语音 query「答案到达时 promote 进队列」接缝（`qpAnswerArrived`）、MVP 局限（语音 query 档案问题用占位「🎤 语音提问」）、前端测试栈计划外 bootstrap。
