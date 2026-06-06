# NP/QP 共享输入 × 反馈呈现 —— 前端设计

> 日期 2026-06-06 ｜ 分支 `worktree-frontend-design`（基于 main `7be5303`）
> 范围：**纯前端**。后端 `route_memo` 调度器当契约（已有独立 spec `5814e06` + plan，本文不实现）。
> 状态：brainstorm 收敛后的设计定稿，待用户复审 → 转 writing-plans。

---

## 1. 背景与问题

NP（录音师备注）与 QP（自然语言查询）将**共用底栏同一个文本框 + 同一个语音框**。后端 `route_memo` 自动判这条走 note 还是 query（隐式调度，用户发送时不知道走哪条线）。当前前端的两个痛点：

1. **NP 不流畅**：发 note 后进一个常驻持久队列（`NoteList.tsx`），盯着「处理中…」等结构化落定，把「fire-and-forget 的写」做成了「要等回执的读」。
2. **QP 前端未接线**：`/api/v1/query` 后端活着，但 `api.ts` 无 `postQuery`，答案没有落点。若沿用 L2 那个埋在侧 tab 的「LLM 反馈」面，QP 答案会落进用户没在看的 tab，flow 断掉。

根因：NP（写/低注意力/持久）与 QP（读/高注意力/短暂）在每条轴上相反，却共用一个输入口；而「LLM 反馈」一词把 L2/SP/NP/QP 四种正交的东西糊在一起。

## 2. 目标 / 非目标

**目标**
- 共享输入口的反馈，对 note 轻、对 query 答案就地可读、对历史可回溯。
- 把「LLM 反馈」抽成独立于业务 tab 的一级留存层。
- 视觉轻量，与现有 shadcn（`radix-luma` / taupe / lucide）一致。

**非目标（本轮明确不做）**
- 后端 `route_memo` 实现（另有 workstream）。
- 语音 query 调度（卡在多模态 handler 注入工具声明的收敛点，`client.py:176-177`）—— 语音本轮只走 NP。
- QP 结构化输出 / thinking / alias 表。
- SP 导入反馈纳入本面（SP 是导入期，归导入向导，非现场实时）。

## 3. 硬约束（实现纪律）

1. **只动 NP/QP 输入+反馈这块的视觉**。业务三 tab（Live / 剧本 / History）、底栏控制（REC / scene·shot·take / Mark / 演员选择）、设备/录入等，**视觉一律不改**；只做必要的**结构挪位**（见 §6）。
2. **分点推进**：按 §10 的点逐个落地，**改一点 → 起 dev server 截图视觉验证 → 再下一点**，不一次性大改。
3. **组件只用现有 shadcn**（`@/components/ui/*`）：`Sheet` `Card` `Button` `Input` `Badge` `ToggleGroup` `ScrollArea` + lucide 图标 + `cn()`。不引第三方、不发明新组件类型。

## 4. 心智模型：两层 + 一个共享输入口

```
┌─ 业务区（不动）─ Live │ 剧本 │ History ───────────────┐
│                                                        │
│   （LLM 反馈已从这组 tab 移走）                          │
└────────────────────────────────────────────────────────┘
┌─ 会话区（底栏，本设计的全部范围）──────────────────────┐
│  ▎当下层：就地队列（输入框正上方，短暂）                 │
│     · 你发的每条 → 处理中行 → 结果就地呈现 → 自己瘦下去   │
│  ┌────────────────────────────────────────────────┐    │
│  │ [ 输入框：memo / 提问共用 ]      ↑发送   🎙语音   │    │
│  └────────────────────────────────────────────────┘    │
│  ● SCENE/S/T        ✦ LLM 反馈(未读点)        REC       │
│                         └─ 点开 ▶ 留存层                 │
└────────────────────────────────────────────────────────┘
       ▲ 留存层：LLM 反馈档案（底部全高 Sheet，持久）
         QP 问答 + L2 推送 全历史时间线
```

- **当下层**＝「我刚发的，马上要看」。短暂、就地、自我清理。
- **留存层**＝「回看所有问答 + L2 推送」。独立于业务 tab 的一级入口，持久。
- **共享输入口**＝一个框收 memo + 提问，后端按契约回 `note | query` 判定。

## 5. 交互规格

### 5.1 输入与调度契约（前端假设，后端另实现）

- 文本发往**单一入口**（落点 `POST /notes`，与 dispatcher spec 一致）。后端 `route_memo` 返回判定 `kind: "note" | "query"`，前端**据此回溯地决定渲染**（用户发送时未知）。
- **语音本轮恒为 note**（语音 query 调度卡在多模态收敛点，§2 非目标）：语音入口照旧 `POST /notes/voice`，前端不期待 `kind=query`，UI 不为语音预留 query 分支。
- 响应/WS 形状需带 `kind` 与 `client_id`（沿用现有乐观去重机制）：
  - `kind=note` → 走现有 `note.processed` / `structure_note`，得 `category` + `content`。
  - `kind=query` → 带 `answer`（可多行文本）。
- **契约未就绪期**：前端按此契约实现，可用 stub/mock 数据驱动各状态做视觉验证；端到端真跑等后端 `route_memo`。QP 也可临时显式直连 `/api/v1/query` 验证答案渲染（仅开发期）。

### 5.2 当下层：就地队列（采用 B2 —— 单行可展开）

**单条生命周期**
| 阶段 | note | query |
|---|---|---|
| 发出（乐观） | 「正在记录…」行 | 「正在查询…」行 |
| 落定 | 回执：`已记录 Sc/S/T · pass`，**3s 后自动消失** | 单行答案（长答案截断 + 点开就地展开），**lingers** |
| 清理 | 3s 自动 | 开始打下一条 / 点「全部收起」/ 手动关 |

**堆叠（≥2 条）**
- 多条按时间堆叠，**最新贴输入框**（最可能在等的离手最近）。
- 顶部极淡一行 `N 条 · 全部收起`。
- 整区 **封顶 40vh，超出内部滚动**，永不整屏盖住业务面。
- note 回执 3s 自走，不长期占席；query 答案靠「下一次输入 / 全部收起」清。

**为何 B2 而非卡片（B1）**：单条时卡片好看，但并发队列下卡片堆成墙；单行堆叠在 3 条时仍紧凑（已 mock 实证）。长答案用「点开就地展开」补回可读性 —— 列表用 B2、详情就地展开。

### 5.3 误判兜底（route_memo 判错的就地纠偏）

- **query 被判成 note（最危险：问题变日志没人答）**：该条回执用**琥珀左条**显式标注 `已记录 note 「原文」`，右侧一键 **`↩ 其实是提问`** → 把同一句改走 query。
- **note 被判成 query（较轻）**：答案行末尾挂淡链接 **`✎ 记为备注`** → 归档成 note，不必重打。
- 颜色即信号：误判用琥珀，正常答案用 primary 细条。

### 5.4 留存层：LLM 反馈档案（Sheet）

- 入口：底栏一级 `✦ LLM 反馈` 按钮（ghost，带未读小点），独立于业务 tab。
- 形态：`Sheet side="bottom"`，全高 `h-[70vh]`，业务面在后变暗但**不动**。手机/桌面同一套形态。
- 内容：QP 问答 + L2 推送 的**时间线全历史**；顶部可选筛选 `全部 / 问答 / L2`（`ToggleGroup`）。
- **补 a11y**：`SheetContent` 加 `SheetDescription` 或 `aria-describedby`，消除现有 `DialogContent` 缺 description 的 console warning。

### 5.5 L2 推送落点

- L2 是系统主动推（take 结束），不是用户发的，**不挤进当下层队列**。
- L2 到达 → `LLM 反馈` 入口亮**未读点** + 进留存层时间线。
- 可选（标记为后续，不在首版）：take 结束时也在当下层飘一条就地 L2 卡。

### 5.6 note 双写

- 当下层 3s 回执只是即时确认；note 本体照旧落进该 take 的 note 列表（业务数据，在 History/take 详情可查）。**回执飘走 ≠ 数据没存。**

## 6. 信息架构与布局改动（对照现有代码）

| 现状 | 改动 | 落点 |
|---|---|---|
| 侧 tab 组 `剧本 / History / LLM 反馈` | **移除 LLM 反馈 tab** → `剧本 / History` | `AdminHome.tsx:693-697`（desktop）、`:644-649`/`:668-670`（mobile swipe tab） |
| 底栏 NoteList 常驻持久浮层 | **换成就地队列组件**（B2，短暂自清理） | `AdminHome.tsx:717-725` 浮层容器 + `NoteList.tsx` 重写为 `InlineFeedbackQueue` |
| `LLMFeedback.tsx`（L2-only，tab 内容） | **重构为档案时间线**（QP+L2），移进底部 Sheet | `LLMFeedback.tsx` → `LLMArchiveSheet`（含 `ConversationTimeline`） |
| 底栏无 LLM 入口 | **加一级 `✦ LLM 反馈` 入口 + 未读点** | `BottomControlBar.tsx`（scene/take 行右侧，REC 左邻） |
| `MemoInput.tsx` 仅发 `/notes` | 保留输入形态；接调度契约（按 §5.1，端到端待后端） | `MemoInput.tsx` |

> 业务三 tab 与底栏控制其余部分**视觉不动**（硬约束 §3.1）。

## 7. 视觉语言（轻）

统一一套，当下层与留存层同语：
- **细色条代替图标/头像轨**：2px 左 border —— 问答/答案 `border-primary/25~30`，L2 `border-muted-foreground/20`，误判 `border-amber-400/70`。
- **去框去填充去套卡**：答案/条目即文字，不包 `Card`、不套内层灰底块、不用实心 Badge（DIFF → 淡 mono 标签）。
- **底栏**：硬 `border-t` 实底 → `bg-background/95` + `backdrop-blur-sm` + 柔顶阴影（浮起感）。
- **颜色仅在必要处**：区分来源（primary/灰）、警示（琥珀）、diff（淡红删/淡绿增）。
- **入口** ghost 无边 + 小未读点。文字降一档（`foreground/80~90`、`muted-foreground` 系）。

> 注：实现后视觉收敛为「统一 `primary`(amber) 主题色 + 淡背景块状态区分」，详见 memory `feedback_frontend_feed_color`，本节描述为设计期初版。

### 7.1 header 电平表刻度（实现留档）

`LiveLevelMeter`（header Input chip 麦克风监看，`StatusChip.tsx`）用 **dB 对数刻度**——线性下人声小信号（RMS 0.05–0.3）亮格太少不明显。映射 `20·log10(level)` 落 `[FLOOR_DB, 0]` 归一化 ×count，`FLOOR_DB = -60`：

| level (RMS) | 线性亮格 | 对数亮格（FLOOR=-60dB） |
|---|---|---|
| 0.02 | 0 | 3 |
| 0.05 | 0 | 4 |
| 0.1 | 1 | 5 |
| 0.3 | 2 | 6 |
| 1.0 | 7 | 7 |
| 0（静音） | 0 | 0 |

`FLOOR_DB` 可调灵敏度：底越高（如 -45）越钝（轻环境噪声不亮）、越低越灵敏。

## 8. 数据流与状态

- 复用现有乐观机制：`pendingNotes` + `client_id` 去重 + `notesVersion` bump（`store/session.ts:60/64/98-101`）。扩展：
  - 队列条目模型加 `kind: note|query`、`status`、query 的 `answer`、note 的 `slot/category`。
  - 新增 query pending → resolved 流转（对称 note 的 `note.processed`）。
  - 新增 `archive`（留存层时间线）state：累积 QP 问答 + L2 推送；未读计数驱动入口红点。
- L2 仍走 `takes` Map 的 `script_diff`（`take.changed` WS，`session.ts:184-206`）；档案时间线从 `takes` 派生 L2 条目 + 从 archive 取 QP 条目，按时间合并。
- `api.ts`：加 `postQuery`（开发期可直连 `/api/v1/query`）；统一入口随后端 `route_memo` 接入。

## 9. 错误处理

- 网络/上传层失败：沿用 `noteFailed({reason:"upload_failed"})` + 就地「重试」（`NoteList.tsx:45-53` 现有逻辑迁入新队列组件）。
- 后端 NP/QP timeout、`asr_unclear`、`model_unavailable`：复用 `FAIL_REASON_TEXT` 文案映射，渲染在就地行。
- 误判：见 §5.3，是「可恢复」而非「错误」，不走失败态。

## 10. 实现分点（逐点推进，每点改完截图视觉验证）

> 每点为一个独立 commit；完成即起 dev server 截图比对 mock，确认后再下一点。

- **P1 业务 tab 瘦身**：从 desktop + mobile tab 组移除 `LLM 反馈`，仅留 `剧本 / History`。验证：tab 组无 LLM、业务面其余不变。
- **P2 就地队列组件**：`NoteList` → `InlineFeedbackQueue`（B2 单行、轻视觉、3s 回执、堆叠封顶 40vh、最新贴底）。先用现有 note 流跑通（query 用 stub）。验证：发 note 见就地回执 3s 自走；多条堆叠紧凑。
- **P3 query 渲染 + 调度契约接入**：输入接 `kind` 判定；query 答案单行 + 点开就地展开；`postQuery` 开发期直连验证答案渲染。验证：提问见「正在查询…」→ 单行答案 → 展开。
- **P4 误判兜底**：query→note 琥珀条 + `↩ 其实是提问`；note→query `✎ 记为备注`。验证：两种误判态就地纠偏可点。
- **P5 留存层入口 + 档案 Sheet**：底栏 `✦ LLM 反馈` 一级入口 + 未读点；`Sheet` 全高档案时间线（QP+L2，轻视觉，筛选，补 `SheetDescription`）。验证：点开见合并时间线；新 L2/QP 亮未读点。
- **P6 收尾**：移除 `/mock*` 路由与 `Mock*` 文件；过 `pnpm build`（tsc）+ lint；横竖屏（iPad/手机）回归截图。

## 11. 依赖与开放问题

- **依赖**：端到端需后端 `route_memo` 单入口返回 `kind`（dispatcher workstream `5814e06`）。本轮前端按契约实现，stub 驱动视觉验证。
- **开放**：(a) L2 是否也在当下层飘就地卡（§5.5，默认否）；(b) 档案筛选是否首版就要；(c) query 答案的「展开」是否需要富文本（take 列表/链接），首版纯文本。

## 12. 验证方式

- 每点：Playwright 截图（iPad 横屏 1194×834 + 手机竖屏 390×844）比对本设计 mock。
- 收尾：`pnpm build` + `pnpm lint` 通过；无新增 console error（含 a11y warning 已消）。
- 参考 mock（评审后删）：`/mock-b?v=2&state=queue|misroute|long|note`，`localhost:5173` 或 `192.168.0.190:5173`。
