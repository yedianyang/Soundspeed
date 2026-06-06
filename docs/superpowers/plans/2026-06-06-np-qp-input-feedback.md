# NP/QP 共享输入 × 反馈呈现 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: 用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务执行。步骤用 checkbox（`- [ ]`）跟踪。
>
> **本仓前端无单测框架**（`package.json` 仅 dev/build/lint/preview）。按用户方法论：**每个任务改完 → 起 dev server → Playwright 截图比对设计 mock → 确认 → commit**。收尾任务跑 `pnpm build`（tsc）+ `pnpm lint`。

**Goal:** 把 NP/QP 共享输入的反馈重做成「当下层就地队列（B2 单行）+ 留存层 LLM 反馈档案 Sheet」两层，轻视觉，纯前端。

**Architecture:** 复用现有 note 乐观 pending + WS 机制（`pendingNotes`/`note.processed`/`note.failed`），additive 加 note 回执（`feedReceipts`，3s 自走）+ query 项（`qaItems`，QP 同步 `/api/v1/query`）+ 档案未读计数。`NoteList` → `InlineFeedbackQueue`；`LLMFeedback` tab → 底部 `Sheet` 档案。业务三 tab 与底栏控制视觉不动。

**Tech Stack:** React + TS + zustand（`store/session.ts`）+ react-router + shadcn（`@/components/ui/*`）+ lucide + Tailwind。

**硬约束（每个任务都遵守）:** ① 只动 NP/QP 输入+反馈视觉，业务三 tab / 底栏控制其余视觉不改；② 分点推进，改完即截图验证；③ 组件只用现有 shadcn，不引第三方、不发明组件。

**参考 mock（已验证，评审用，P6 删）:** `src/routes/mock/MockFormB.tsx`、`MockLLMSheet.tsx`（`ConversationTimeline`）。真实组件从这些 mock 的视觉代码改写 + 接 store。

**验证基线命令（每个任务复用）:**
```bash
# dev server（已可能在跑；没跑则起）
cd frontend && pnpm dev   # → http://localhost:5173 ，局域网 192.168.0.190:5173
```
截图用 Playwright：iPad 横屏 `1194×834`、手机竖屏 `390×844`，比对 mock 对应状态。

---

## Task 1（P1）：业务 tab 瘦身 —— 移除 LLM 反馈 tab

**Files:**
- Modify: `src/routes/admin/AdminHome.tsx:45`（MOBILE_TABS）、`:644-649`（mobile TabsList）、`:665-671`（mobile LLM 面板）、`:693-697`（desktop TabsList）、`:698-702`（desktop 面板渲染）

- [ ] **Step 1: 删 mobile tab 数组里的 "llm"**

`src/routes/admin/AdminHome.tsx:45`
```tsx
const MOBILE_TABS = ["live", "script", "history"] as const
```

- [ ] **Step 2: 删 mobile TabsList 的 LLM 触发 + 对应 swipe 面板**

`:644-649` 的 `TabsList` 删掉这一行：
```tsx
<TabsTrigger value="llm">LLM 反馈</TabsTrigger>
```
`:665-671` swipe 轨道里删掉 LLMFeedback 那个 `<div>…<LLMFeedback /></div>`（最后一个面板）。

- [ ] **Step 3: 删 desktop 右侧 TabsList 的 LLM 触发 + 条件渲染**

`:693-697` 删 `<TabsTrigger value="llm">LLM 反馈</TabsTrigger>`；`:698-702` 删 `{sideTab === "llm" && <LLMFeedback />}`。`sideTab` 初值 `"script"` 不变（已合法）。

- [ ] **Step 4: 暂留 LLMFeedback import**（Task 5 复用其 `ScriptDiffView`，先不删 import 以免 P1 报未用）

若 tsc `noUnusedLocals` 报 `LLMFeedback` 未用，临时在文件顶部加 `void LLMFeedback`，Task 5 接回后删除。或直接保留：本任务只验证视觉，dev server 不 typecheck。

- [ ] **Step 5: 视觉验证**

起 dev server，Playwright 截图 `localhost:5173/admin`（iPad 横屏 + 手机竖屏）。
预期：右侧/移动 tab 组只剩 `剧本 / History`，无 LLM 反馈；Live/剧本/History 内容与样式不变；底栏不变。

- [ ] **Step 6: Commit**

```bash
git add frontend/src/routes/admin/AdminHome.tsx
git commit -m "feat(p1): 业务 tab 组移除 LLM 反馈 tab"
```

---

## Task 2（P2）：就地队列组件 —— note 路径（B2 单行 + 3s 回执 + 堆叠）

**Files:**
- Create: `src/components/admin/InlineFeedbackQueue.tsx`
- Modify: `src/types/api.ts`（加 `FeedReceipt`）、`src/store/session.ts`（加 `feedReceipts` + actions，noteProcessed 推回执）、`src/hooks/useLiveConnection.ts`（note.processed 已调 noteProcessed，无需改）、`src/routes/admin/AdminHome.tsx:723-724`（浮层容器换组件）

- [ ] **Step 1: types 加 FeedReceipt**

`src/types/api.ts`（NoteFailedMsg 之后追加）：
```ts
// 就地队列的 note 回执（done 态，3s 自走）。由 note.processed 派生。
export interface FeedReceipt {
  client_id: string
  category: string // keep/pass/ng/issue/note
  content: string
  ts: number
}
```

- [ ] **Step 2: store 加 feedReceipts 状态 + actions，noteProcessed 推回执**

`src/store/session.ts`：state 接口加（在 `notesVersion: number` 附近）：
```ts
  feedReceipts: FeedReceipt[]
```
actions 接口加：
```ts
  dismissReceipt: (clientId: string) => void
```
初值（`notesVersion: 0,` 附近）：`feedReceipts: [],`
导入处加 `FeedReceipt`。
改 `noteProcessed`（在移除 pending 的同时推一条回执；保留 notesVersion 兼容他处）：
```ts
  noteProcessed: (m) =>
    set((s) => ({
      pendingNotes: m.client_id
        ? s.pendingNotes.filter((p) => p.client_id !== m.client_id)
        : s.pendingNotes,
      notesVersion: s.notesVersion + 1,
      feedReceipts: m.client_id
        ? [...s.feedReceipts, { client_id: m.client_id, category: m.category, content: m.content, ts: m.ts }]
        : s.feedReceipts,
    })),
```
加 `dismissReceipt`（组件 3s 后调）：
```ts
  dismissReceipt: (clientId) =>
    set((s) => ({ feedReceipts: s.feedReceipts.filter((r) => r.client_id !== clientId) })),
```

- [ ] **Step 3: 写 InlineFeedbackQueue（note 部分；query 部分 Task 3 补）**

`src/components/admin/InlineFeedbackQueue.tsx`（视觉照搬 MockFormB 轻版 B2；先只渲染 pendingNotes + feedReceipts）：
```tsx
import { useEffect } from "react"
import { Loader2, Clock, CornerUpLeft } from "lucide-react"
import { useSessionStore } from "@/store/session"
import { postNote, postVoiceNote } from "@/lib/api"
import type { PendingNote, FeedReceipt } from "@/types/api"

const FAIL_REASON_TEXT: Record<string, string> = {
  take_not_found: "找不到对应素材", parse_error: "理解失败", timeout: "处理超时",
  asr_unclear: "没听清", model_unavailable: "模型未就绪", upload_failed: "提交失败",
}

// note 回执 3s 自走（query 项 Task 3 lingers，不在此）。
function ReceiptRow({ r, onDismiss }: { r: FeedReceipt; onDismiss: (id: string) => void }) {
  useEffect(() => {
    const t = setTimeout(() => onDismiss(r.client_id), 3000)
    return () => clearTimeout(t)
  }, [r.client_id, onDismiss])
  return (
    <div className="flex items-center gap-2 text-xs px-1 py-1 text-muted-foreground">
      <Clock className="size-3 text-muted-foreground/60" />
      <span>已记录</span>
      <span className="font-medium text-green-600/90">{r.category}</span>
      <span className="truncate text-foreground/70">{r.content}</span>
    </div>
  )
}

function PendingRow({ pn, onRetry }: { pn: PendingNote; onRetry: (pn: PendingNote) => void }) {
  const failed = pn.failedReason != null
  if (!failed) {
    return (
      <div className="flex items-center gap-2 text-sm text-muted-foreground/80 px-1 py-1">
        <Loader2 className="size-3.5 animate-spin opacity-60" />
        <span>正在记录…</span>
      </div>
    )
  }
  return (
    <div className="flex items-center gap-2 text-sm px-2.5 py-1 border-l-2 border-amber-400/70">
      <span className="text-red-600">{FAIL_REASON_TEXT[pn.failedReason!] ?? "处理失败"}</span>
      <button onClick={() => onRetry(pn)} className="flex items-center gap-1 text-xs text-amber-700 hover:underline">
        <CornerUpLeft className="size-3" /> 重试
      </button>
    </div>
  )
}

export default function InlineFeedbackQueue() {
  const pendingNotes = useSessionStore((s) => s.pendingNotes)
  const feedReceipts = useSessionStore((s) => s.feedReceipts)
  const dismissReceipt = useSessionStore((s) => s.dismissReceipt)
  const retryPending = useSessionStore((s) => s.retryPending)
  const noteFailed = useSessionStore((s) => s.noteFailed)

  const handleRetry = (pn: PendingNote) => {
    retryPending(pn.client_id)
    const resubmit = pn.voiceBlob
      ? postVoiceNote(pn.voiceBlob, pn.client_id, pn.ts)
      : postNote(pn.rawText, undefined, pn.client_id)
    resubmit.catch(() => noteFailed({ reason: "upload_failed", ts: pn.ts, client_id: pn.client_id }))
  }

  // 合并按 ts，最新贴底（输入框侧）
  const rows = [
    ...pendingNotes.map((pn) => ({ ts: pn.ts, node: <PendingRow key={`p-${pn.client_id}`} pn={pn} onRetry={handleRetry} /> })),
    ...feedReceipts.map((r) => ({ ts: r.ts, node: <ReceiptRow key={`r-${r.client_id}`} r={r} onDismiss={dismissReceipt} /> })),
  ].sort((a, b) => a.ts - b.ts)

  if (rows.length === 0) return null
  return (
    <div className="pb-1 space-y-0.5 max-h-[40vh] overflow-y-auto pointer-events-auto">
      {rows.length > 1 && (
        <div className="px-1 pb-0.5 text-[10px] text-muted-foreground/50">{rows.length} 条</div>
      )}
      {rows.map((r) => r.node)}
    </div>
  )
}
```

- [ ] **Step 4: AdminHome 浮层容器换成新组件**

`src/routes/admin/AdminHome.tsx`：import 把 `NoteList` 换成 `InlineFeedbackQueue`；浮层容器（`:723-725`）替换为：
```tsx
        <div className="pointer-events-none absolute inset-x-0 bottom-[calc(100%-26px)] z-20 px-4">
          <InlineFeedbackQueue />
        </div>
```
（NoteList 旧 props `takeId`/`refreshKey` 不再需要；保留 `noteRefresh` 状态不影响，可后续清理。）

- [ ] **Step 5: 视觉验证**

`localhost:5173/admin`：在底栏输入框打一条 memo 发送（dev 后端起着则真跑；没后端则用 Playwright 直接 `addPendingNote` 注入或临时挂 stub）。
预期：发出见「正在记录…」单行；落定见「已记录 <类别> <内容>」3s 后消失，不遮业务面；视觉与 mock `?v=2&state=note` 一致（无框、纯文字、细行）。多条堆叠紧凑、最新贴底、封顶 40vh。

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/admin/InlineFeedbackQueue.tsx frontend/src/store/session.ts frontend/src/types/api.ts frontend/src/routes/admin/AdminHome.tsx
git commit -m "feat(p2): 就地反馈队列（note 路径，B2 单行+3s 回执，替换常驻 NoteList）"
```

---

## Task 3（P3）：query 渲染 + 调度契约接入

**Files:**
- Modify: `src/types/api.ts`（加 `QueryResponse` / `QaItem`）、`src/lib/api.ts`（加 `postQuery`）、`src/store/session.ts`（加 `qaItems` + actions）、`src/components/admin/InlineFeedbackQueue.tsx`（渲染 query 行）、`src/components/admin/MemoInput.tsx`（dispatch）

- [ ] **Step 1: types 加 QueryResponse / QaItem**

`src/types/api.ts`：
```ts
// POST /api/v1/query 返回（QP tool-loop 同步答案；dev 期直连）
export interface QueryResponse { answer: string }

// 就地队列 + 档案共用的问答项。done 持久供档案；inlineDismissed 控制是否还在就地层显示。
export interface QaItem {
  client_id: string
  question: string
  status: "processing" | "done" | "failed"
  answer?: string
  failedReason?: string
  ts: number
  inlineDismissed?: boolean
}
```

- [ ] **Step 2: api 加 postQuery**

`src/lib/api.ts`（Note API 区附近）：
```ts
// QP：自然语言查询（tool-loop 同步返回答案）。dev 期由前端显式触发（route_memo 落地后改单入口判定）。
export function postQuery(text: string): Promise<QueryResponse> {
  return request<QueryResponse>(`/api/v1/query`, {
    method: "POST",
    body: JSON.stringify({ text }),
  })
}
```
导入 `QueryResponse`。（注：`/api/v1/query` 需 require_admin，`request` 已带 token。）

- [ ] **Step 3: store 加 qaItems + actions**

`src/store/session.ts`：state 加 `qaItems: QaItem[]`；actions 加：
```ts
  addQa: (q: QaItem) => void
  resolveQa: (clientId: string, answer: string) => void
  failQa: (clientId: string, reason: string) => void
  dismissQaInline: (clientId: string) => void
```
初值 `qaItems: [],`；导入 `QaItem`。实现：
```ts
  addQa: (q) => set((s) => ({ qaItems: [...s.qaItems, q] })),
  resolveQa: (clientId, answer) =>
    set((s) => ({ qaItems: s.qaItems.map((q) => q.client_id === clientId ? { ...q, status: "done", answer } : q) })),
  failQa: (clientId, reason) =>
    set((s) => ({ qaItems: s.qaItems.map((q) => q.client_id === clientId ? { ...q, status: "failed", failedReason: reason } : q) })),
  dismissQaInline: (clientId) =>
    set((s) => ({ qaItems: s.qaItems.map((q) => q.client_id === clientId ? { ...q, inlineDismissed: true } : q) })),
```

- [ ] **Step 4: InlineFeedbackQueue 渲染 query 行**

在 `InlineFeedbackQueue.tsx` 加 query 行组件（B2 单行，长答案点开就地展开；primary 细色条）：
```tsx
import { useState } from "react"
import { ChevronDown } from "lucide-react"
import type { QaItem } from "@/types/api"

function QaRow({ q }: { q: QaItem }) {
  const [expanded, setExpanded] = useState(false)
  if (q.status === "processing")
    return (
      <div className="flex items-center gap-2 text-sm text-muted-foreground/80 px-1 py-1">
        <Loader2 className="size-3.5 animate-spin opacity-60" /><span>正在查询…</span>
      </div>
    )
  if (q.status === "failed")
    return (
      <div className="flex items-center gap-2 text-sm px-2.5 py-1 border-l-2 border-amber-400/70 text-red-600">
        查询失败：{q.failedReason ?? "未知"}
      </div>
    )
  const text = q.answer ?? ""
  const isLong = text.includes("\n") || text.length > 36
  const head = isLong ? text.split("\n")[0].slice(0, 36) + "…" : text
  return (
    <button onClick={() => isLong && setExpanded((e) => !e)}
      className="w-full flex items-start gap-2.5 text-sm text-left pl-2.5 pr-1 py-1 border-l-2 border-primary/25 hover:border-primary/50">
      <span className="flex-1 min-w-0 text-foreground/90">
        {expanded ? <span className="leading-relaxed whitespace-pre-line">{text}</span>
                  : <span className="truncate block">{head}</span>}
      </span>
      {isLong && <ChevronDown className={"size-4 mt-0.5 text-muted-foreground/50 transition-transform " + (expanded ? "rotate-180" : "")} />}
    </button>
  )
}
```
在 `InlineFeedbackQueue` 的 `rows` 合并里加（读 `qaItems`，过滤 `!inlineDismissed`）：
```tsx
  const qaItems = useSessionStore((s) => s.qaItems)
  // …rows 数组追加：
    ...qaItems.filter((q) => !q.inlineDismissed).map((q) => ({ ts: q.ts, node: <QaRow key={`q-${q.client_id}`} q={q} /> })),
```

- [ ] **Step 5: MemoInput dispatch（dev 期显式 "?" 前缀路由）**

`src/components/admin/MemoInput.tsx` 的 `handleSubmit`：在 `postNote` 前加路由（**临时 dev 脚手架**，route_memo 落地后换成单入口按返回 kind 分流）：
```tsx
    // ⚠ dev 期显式路由：以 "?" 或 "？" 开头 → QP 查询；否则 → NP 备注。route_memo 后端就绪后移除。
    if (/^[?？]/.test(trimmed)) {
      const clientId = newClientId()
      const ts = Date.now() / 1000
      const question = trimmed.replace(/^[?？]\s*/, "")
      const { addQa, resolveQa, failQa } = useSessionStore.getState()
      addQa({ client_id: clientId, question, status: "processing", ts })
      setText(""); onNoteAdded?.()
      try { const r = await postQuery(question); resolveQa(clientId, r.answer) }
      catch (e) { failQa(clientId, e instanceof Error ? e.message : "查询失败") }
      finally { setSending(false) }
      return
    }
```
导入 `postQuery`、`useSessionStore`（已导入）。占位提示更新为 `Typing memo / 提问 · 第三场 NG 几条？（? 开头=提问）`。

- [ ] **Step 6: 视觉验证**

`localhost:5173/admin`：输入 `? 第三场 NG 几条？` 发送（dev 后端 QP 起着则真答；否则 Playwright stub `addQa`+`resolveQa` 注入长/短答案）。
预期：见「正在查询…」→ 短答案单行 / 长答案截断+点开就地展开；视觉同 mock `?v=2&state=short|long`；与 note 回执混排时按 ts、最新贴底。

- [ ] **Step 7: Commit**

```bash
git add frontend/src/types/api.ts frontend/src/lib/api.ts frontend/src/store/session.ts frontend/src/components/admin/InlineFeedbackQueue.tsx frontend/src/components/admin/MemoInput.tsx
git commit -m "feat(p3): QP 查询就地渲染 + dev 期 ? 前缀调度（route_memo 契约占位）"
```

---

## Task 4（P4）：误判兜底（就地纠偏）

**Files:**
- Modify: `src/components/admin/InlineFeedbackQueue.tsx`（note 回执加「其实是提问」、qa 答案加「记为备注」）、`src/store/session.ts`（加 `reclassify` 辅助：可选）

- [ ] **Step 1: note 回执加「↩ 其实是提问」（query→note 误判）**

改 `ReceiptRow`：加琥珀左条 + 一键改判（把同一句 content/rawText 改走 query）。回执需带 `rawText` 以便重发——`FeedReceipt` 加 `rawText: string`（Step 2 改 types + noteProcessed 推回执处补 rawText；rawText 从对应 pending 取，故在 noteProcessed 里需查 pending：改为在移除前读取）。
`session.ts` noteProcessed 调整为先取 rawText：
```ts
  noteProcessed: (m) =>
    set((s) => {
      const matched = m.client_id ? s.pendingNotes.find((p) => p.client_id === m.client_id) : undefined
      return {
        pendingNotes: m.client_id ? s.pendingNotes.filter((p) => p.client_id !== m.client_id) : s.pendingNotes,
        notesVersion: s.notesVersion + 1,
        feedReceipts: m.client_id
          ? [...s.feedReceipts, { client_id: m.client_id, category: m.category, content: m.content, rawText: matched?.rawText ?? m.content, ts: m.ts }]
          : s.feedReceipts,
      }
    }),
```
`FeedReceipt` 加 `rawText: string`。
`ReceiptRow` 改为：
```tsx
function ReceiptRow({ r, onDismiss, onReclassify }: { r: FeedReceipt; onDismiss: (id: string) => void; onReclassify: (text: string) => void }) {
  useEffect(() => { const t = setTimeout(() => onDismiss(r.client_id), 3000); return () => clearTimeout(t) }, [r.client_id, onDismiss])
  return (
    <div className="flex items-center gap-2 text-xs px-1 py-1">
      <Clock className="size-3 text-muted-foreground/60" />
      <span className="text-muted-foreground">已记录</span>
      <span className="font-medium text-green-600/90">{r.category}</span>
      <span className="flex-1 min-w-0 truncate text-foreground/70">{r.content}</span>
      <button onClick={() => onReclassify(r.rawText)} className="flex items-center gap-1 text-amber-700 hover:underline flex-shrink-0">
        <CornerUpLeft className="size-3" /> 其实是提问
      </button>
    </div>
  )
}
```
`onReclassify(text)` 在 `InlineFeedbackQueue` 实现：调 query 路径（同 MemoInput 的 addQa+postQuery）。抽一个 `runQuery(text)` 工具函数复用（见 Step 3）。

- [ ] **Step 2: qa 答案加「✎ 记为备注」（note→query 误判）**

`QaRow` 的 done 分支末尾加：
```tsx
      <button onClick={(e) => { e.stopPropagation(); onAsNote(q.question) }}
        className="ml-2 inline-flex items-center gap-1 text-xs text-muted-foreground/70 hover:text-foreground flex-shrink-0">
        <Pencil className="size-3" /> 记为备注
      </button>
```
`QaRow` 加 prop `onAsNote: (text: string) => void`；导入 `Pencil`。`onAsNote` 调 `postNote(text,…)` + `addPendingNote`（复用 MemoInput 同逻辑）。

- [ ] **Step 3: 抽 runQuery/runNote 工具，避免与 MemoInput 重复（DRY）**

新建 `src/lib/feed-actions.ts`：
```ts
import { postNote, postQuery } from "@/lib/api"
import { useSessionStore } from "@/store/session"

export function newClientId(): string {
  return crypto?.randomUUID?.() ?? `nid-${Date.now()}-${Math.random().toString(36).slice(2)}`
}

export async function runQuery(text: string) {
  const s = useSessionStore.getState()
  const clientId = newClientId(); const ts = Date.now() / 1000
  s.addQa({ client_id: clientId, question: text, status: "processing", ts })
  try { const r = await postQuery(text); useSessionStore.getState().resolveQa(clientId, r.answer) }
  catch (e) { useSessionStore.getState().failQa(clientId, e instanceof Error ? e.message : "查询失败") }
}

export async function runNote(text: string) {
  const s = useSessionStore.getState()
  const clientId = newClientId()
  const resp = await postNote(text, undefined, clientId)
  s.addPendingNote({ client_id: clientId, kind: "text", ts: Date.now() / 1000, category: resp.category, content: resp.content, rawText: text })
}
```
MemoInput（Task 3 Step 5）与 InlineFeedbackQueue 改为调用 `runQuery`/`runNote`，删去各自重复实现（`newClientId` 也迁此，删 MemoInput 内副本）。

- [ ] **Step 4: 视觉验证**

`localhost:5173/admin`：① 发一条 note，回执出现 → 见「↩ 其实是提问」，点击 → 起一条 query；② `?` 提问得答案 → 见「✎ 记为备注」，点击 → 起一条 note 回执。视觉同 mock `?v=2&state=misroute`（琥珀左条、淡链接）。

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/admin/InlineFeedbackQueue.tsx frontend/src/components/admin/MemoInput.tsx frontend/src/lib/feed-actions.ts frontend/src/store/session.ts frontend/src/types/api.ts
git commit -m "feat(p4): 误判兜底（query→note『其实是提问』/ note→query『记为备注』）"
```

---

## Task 5（P5）：留存层 —— LLM 反馈一级入口 + 档案 Sheet

**Files:**
- Create: `src/components/admin/LLMArchiveSheet.tsx`（Sheet + 时间线）
- Modify: `src/store/session.ts`（加 `archiveUnread` + `markArchiveRead` + L2 到达自增）、`src/components/admin/BottomControlBar.tsx`（加一级入口按钮）、`src/routes/admin/AdminHome.tsx`（挂 Sheet + 状态）、删 mobile/desktop 残留 LLMFeedback import

- [ ] **Step 1: store 加未读计数**

`src/store/session.ts`：state 加 `archiveUnread: number`（初值 0）；actions 加 `markArchiveRead: () => void` = `set({ archiveUnread: 0 })`、`bumpArchiveUnread: () => void` = `set((s) => ({ archiveUnread: s.archiveUnread + 1 }))`。
在 `take.changed`/seedTakes 写入 `script_diff` 非 null 的新条目时调 `bumpArchiveUnread`（L2 推送到达）；qa `resolveQa` 时也 `bumpArchiveUnread`（QP 新答案）。最简实现：在 `resolveQa` 内 `archiveUnread: s.archiveUnread + 1`；L2 在 `setTake`/take.changed 分支检测 `script_diff` 由 null→非 null 时 +1。

- [ ] **Step 2: 写 LLMArchiveSheet（时间线从 takes 的 script_diff + qaItems 合并）**

`src/components/admin/LLMArchiveSheet.tsx`（视觉照搬 MockLLMSheet 轻版 `ConversationTimeline` + `FeedItem`，数据接真实 store）：
```tsx
import { Sparkles, X } from "lucide-react"
import { Sheet, SheetContent, SheetHeader, SheetTitle, SheetDescription } from "@/components/ui/sheet"
import { Button } from "@/components/ui/button"
import { useSessionStore } from "@/store/session"
import { ScriptDiffView } from "@/routes/admin/components/ScriptDiffView"
import type { TakeDTO, QaItem } from "@/types/api"

function FeedItem({ source, time, children }: { source: "QP" | "L2"; time: string; children: React.ReactNode }) {
  const isQP = source === "QP"
  return (
    <div className={"pl-3 border-l-2 " + (isQP ? "border-primary/30" : "border-muted-foreground/20")}>
      <div className="flex items-center gap-2 mb-1.5">
        <span className="text-xs font-medium text-muted-foreground">{isQP ? "问答" : "L2 推送"}</span>
        <span className="text-[10px] font-mono text-muted-foreground/50">{time}</span>
      </div>
      {children}
    </div>
  )
}
const fmt = (ts: number) => new Date(ts * 1000).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" })

export function LLMArchiveSheet({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
  const takesMap = useSessionStore((s) => s.takes)
  const qaItems = useSessionStore((s) => s.qaItems)
  const l2 = Array.from(takesMap.values()).filter((t: TakeDTO) => t.script_diff != null)
    .map((t) => ({ kind: "L2" as const, ts: t.take_id, t }))
  const qa = qaItems.filter((q: QaItem) => q.status === "done")
    .map((q) => ({ kind: "QP" as const, ts: q.ts, q }))
  const items = [...l2, ...qa].sort((a, b) => a.ts - b.ts)
  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="bottom" className="h-[70vh] rounded-t-2xl p-0 gap-0 flex flex-col">
        <SheetHeader className="flex-shrink-0 px-4 pt-4 pb-3 border-b">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Sparkles className="size-4 text-primary" />
              <SheetTitle className="text-base">LLM 反馈</SheetTitle>
              <span className="text-xs text-muted-foreground">QP 问答 · L2 推送 · 全历史</span>
            </div>
            <Button variant="ghost" size="icon-sm" className="rounded-full" onClick={() => onOpenChange(false)}><X className="size-4" /></Button>
          </div>
          <SheetDescription className="sr-only">LLM 问答与 L2 推送的全历史时间线</SheetDescription>
        </SheetHeader>
        <div className="flex-1 min-h-0 overflow-y-auto px-4 py-4 space-y-6">
          {items.length === 0 && <p className="text-xs text-muted-foreground text-center pt-4">还没有问答或 L2 推送</p>}
          {items.map((it) => it.kind === "L2" ? (
            <FeedItem key={`l2-${it.t.take_id}`} source="L2" time={`Take ${it.t.take_number}`}>
              <ScriptDiffView diff={it.t.script_diff!} />
            </FeedItem>
          ) : (
            <FeedItem key={`qa-${it.q.client_id}`} source="QP" time={fmt(it.q.ts)}>
              <div className="text-sm font-medium text-foreground mb-1">{it.q.question}</div>
              <div className="text-sm leading-relaxed text-foreground/80 whitespace-pre-line">{it.q.answer}</div>
            </FeedItem>
          ))}
        </div>
      </SheetContent>
    </Sheet>
  )
}
```
（注：`ScriptDiffView` 现有；若其默认样式偏重，仅在本 Sheet 内传轻量包裹，不改 ScriptDiffView 本体——它也用于别处。）

- [ ] **Step 3: BottomControlBar 加一级入口按钮**

`src/components/admin/BottomControlBar.tsx`：props 加 `onOpenArchive: () => void`、`archiveUnread: number`。在 scene/take 行（`:420` 那个 `justify-between` 行）REC 左邻插入（轻：ghost + 未读点）：
```tsx
<Button size="sm" variant="ghost" onClick={onOpenArchive}
  className="relative rounded-full h-8 px-2.5 text-xs gap-1.5 text-muted-foreground hover:text-foreground">
  <Sparkles className="size-3.5 text-primary/80" />
  LLM 反馈
  {archiveUnread > 0 && <span className="absolute top-1 right-1.5 size-1.5 rounded-full bg-primary" />}
</Button>
```
导入 `Sparkles`。

- [ ] **Step 4: AdminHome 挂 Sheet + 接线**

`src/routes/admin/AdminHome.tsx`：加 `const [archiveOpen, setArchiveOpen] = useState(false)`；import `LLMArchiveSheet`、`markArchiveRead`、`archiveUnread`（from store）。BottomControlBar 传 `onOpenArchive={() => { setArchiveOpen(true); markArchiveRead() }}`、`archiveUnread={archiveUnread}`。在底部 dock 末尾挂 `<LLMArchiveSheet open={archiveOpen} onOpenChange={setArchiveOpen} />`。删除 `LLMFeedback` import（已不在 tab 用）。

- [ ] **Step 5: 视觉验证**

`localhost:5173/admin`：① 底栏 REC 左见「✦ LLM 反馈」ghost 按钮；L2 到达 / `?` 提问得答案后，按钮亮未读点。② 点开 → 全高 Sheet，QP 问答 + L2 推送时间线（细色条、纯文字、淡 DIFF），业务面变暗不动；点开即清未读点。视觉同 mock 档案轻版。无 console a11y warning。iPad + 手机各截一张。

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/admin/LLMArchiveSheet.tsx frontend/src/components/admin/BottomControlBar.tsx frontend/src/routes/admin/AdminHome.tsx frontend/src/store/session.ts
git commit -m "feat(p5): LLM 反馈一级入口 + 独立档案 Sheet（QP 问答 + L2 推送时间线）"
```

---

## Task 6（P6）：收尾 —— 删 mock、build/lint、横竖屏回归

**Files:**
- Delete: `src/routes/mock/MockFormB.tsx`、`src/routes/mock/MockLLMSheet.tsx`
- Modify: `src/App.tsx`（删 `/mock`、`/mock-b` 路由 + import）、清理 `AdminHome.tsx` 残留（`noteRefresh`/`NoteList` 死代码、未用 import）

- [ ] **Step 1: 删 mock 文件与路由**

```bash
rm frontend/src/routes/mock/MockFormB.tsx frontend/src/routes/mock/MockLLMSheet.tsx
```
`src/App.tsx`：删 `MockLLMSheet`/`MockFormB` import 与两条 `<Route path="/mock…">`。

- [ ] **Step 2: 清死代码**

`AdminHome.tsx`：删不再使用的 `noteRefresh` state、`NoteList` import、`onNoteAdded={() => setNoteRefresh…}` 改为 `onNoteAdded={undefined}` 或删该 prop 链（确认 MemoInput 的 `onNoteAdded?` 可选，传不传都行）。删任何因 tab 删除而未用的 import。

- [ ] **Step 3: build + lint**

```bash
cd frontend && pnpm build 2>&1 | tail -20
pnpm lint 2>&1 | tail -20
```
预期：`tsc -b && vite build` 成功，无类型错误；lint 无新增 error。修任何未用变量/类型不一致。

- [ ] **Step 4: 横竖屏回归截图**

Playwright 截 `localhost:5173/admin`：iPad 横屏 1194×834、iPad 竖屏 834×1194、手机竖屏 390×844。三态各发一条 note + 一条 `?` query + 点开档案。预期：业务三 tab/底栏控制视觉与 main 一致（只多了就地队列与 LLM 入口）；就地层/档案轻视觉到位；无溢出/错位。

- [ ] **Step 5: Commit**

```bash
git add -A frontend/src
git commit -m "chore(p6): 删一次性 mock + 清死代码 + build/lint 过 + 横竖屏回归"
```

---

## 自审记录

- **Spec 覆盖**：§5.2 就地队列→T2/T3；§5.3 误判→T4；§5.4 档案→T5；§5.5 L2 落点→T5 Step1（不挤队列、只亮未读）；§5.6 note 双写→note 仍走 `/notes` 落 take 列表（未改后端，天然双写），就地只加回执；§6 布局改动→T1/T2/T4/T5 逐条；§7 轻视觉→各组件照搬 mock 轻版 class；§10 P1–P6→T1–T6 一一对应。
- **占位扫描**：dispatch 真入口（route_memo）显式标为 dev 期 `?` 前缀脚手架（T3 Step5），非占位而是有意的契约边界；其余步骤均有完整代码。
- **类型一致**：`FeedReceipt`(含 rawText, T4 补)、`QaItem`、`QueryResponse` 跨任务一致；store action 名 `dismissReceipt/addQa/resolveQa/failQa/dismissQaInline/markArchiveRead/bumpArchiveUnread` 全程一致；`runQuery/runNote/newClientId`（feed-actions.ts）T4 引入后 MemoInput/InlineFeedbackQueue 共用。
- **已知风险**：T2/T3 视觉验证依赖 dev 后端起着才能真跑；无后端时用 Playwright 注入 store（`useSessionStore.getState().addQa/addPendingNote`）驱动状态截图，不阻塞视觉验证。
