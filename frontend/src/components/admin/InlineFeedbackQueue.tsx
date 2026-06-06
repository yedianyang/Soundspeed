import { useEffect, useState } from "react"
import { Loader2, Clock, CornerUpLeft, ChevronDown, Sparkles } from "lucide-react"
import { useSessionStore } from "@/store/session"
import { postNote, postVoiceNote } from "@/lib/api"
import type { PendingNote, FeedReceipt, QaItem } from "@/types/api"

// note.failed reason → 场记可读文案（4.I），沿用旧 NoteList 映射。
const FAIL_REASON_TEXT: Record<string, string> = {
  take_not_found: "找不到对应素材",
  parse_error: "理解失败",
  timeout: "处理超时",
  asr_unclear: "没听清",
  model_unavailable: "模型未就绪",
  upload_failed: "提交失败",
}

// note 回执：done 态，挂载即排 3s 自走（query 项 P3 lingers，不在此）。轻视觉，无框纯文字。
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

// pending note：处理中（转圈）/ 失败（琥珀左条 + 重试）。
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
  // 失败 = 琥珀实心块（重），与「正在记录…」的中性轻行、答案的琥珀左条区分开。
  return (
    <div className="flex items-center gap-2 text-sm px-2.5 py-1.5 rounded-lg bg-amber-100 text-amber-900 border border-amber-300/60">
      <span>{FAIL_REASON_TEXT[pn.failedReason!] ?? "处理失败"}</span>
      <button onClick={() => onRetry(pn)} className="flex items-center gap-1 text-xs text-amber-700 hover:underline">
        <CornerUpLeft className="size-3" /> 重试
      </button>
    </div>
  )
}

// QP 问答行（B2 单行）：查询中转圈 / 失败琥珀左条 / 完成单行；长答案截断点开就地展开。
// query 项不像 note 回执 3s 自走——lingers 供场记看完，由档案/收起手动清。
function QaRow({ q }: { q: QaItem }) {
  const [expanded, setExpanded] = useState(false)
  if (q.status === "processing") {
    return (
      <div className="flex items-center gap-2 text-sm text-muted-foreground/80 px-1 py-1">
        <Loader2 className="size-3.5 animate-spin opacity-60" />
        <span>正在查询…</span>
      </div>
    )
  }
  // 警告/失败 = 琥珀实心块（重），与正常答案的「琥珀左条」靠轻重区分，不靠色相撞。
  if (q.status === "failed") {
    return (
      <div className="flex items-center gap-2 text-sm px-2.5 py-1.5 rounded-lg bg-amber-100 text-amber-900 border border-amber-300/60">
        查询失败：{q.failedReason ?? "未知"}
      </div>
    )
  }
  const text = q.answer ?? ""
  const isLong = text.includes("\n") || text.length > 36
  const head = isLong ? text.split("\n")[0].slice(0, 36) + "…" : text
  // 正常答案 = 琥珀左条 + ✦（轻，常态暖色，LLM 反馈基调）；note 回执维持中性灰以作区分。
  return (
    <button
      onClick={() => isLong && setExpanded((e) => !e)}
      className="w-full flex items-start gap-2.5 text-sm text-left pl-2.5 pr-1 py-1 border-l-2 border-amber-400/50 hover:border-amber-400/80"
    >
      <Sparkles className="size-3.5 flex-shrink-0 mt-0.5 text-amber-500/80" />
      <span className="flex-1 min-w-0 text-foreground/90">
        {expanded ? (
          <span className="leading-relaxed whitespace-pre-line">{text}</span>
        ) : (
          <span className="truncate block">{head}</span>
        )}
      </span>
      {isLong && (
        <ChevronDown
          className={
            "size-4 flex-shrink-0 mt-0.5 text-muted-foreground/50 transition-transform " +
            (expanded ? "rotate-180" : "")
          }
        />
      )}
    </button>
  )
}

// 就地反馈队列：替代旧常驻 NoteList。短暂、就地、自清理。
// note（pending + 回执）+ query（QaRow）按 ts 混排，最新贴底。
export default function InlineFeedbackQueue() {
  const pendingNotes = useSessionStore((s) => s.pendingNotes)
  const feedReceipts = useSessionStore((s) => s.feedReceipts)
  const qaItems = useSessionStore((s) => s.qaItems)
  const dismissReceipt = useSessionStore((s) => s.dismissReceipt)
  const retryPending = useSessionStore((s) => s.retryPending)
  const noteFailed = useSessionStore((s) => s.noteFailed)

  // 失败重试：乐观打回处理中，用同 client_id 重投（语音重传 WAV，文本重投原文）。
  const handleRetry = (pn: PendingNote) => {
    retryPending(pn.client_id)
    const resubmit = pn.voiceBlob
      ? postVoiceNote(pn.voiceBlob, pn.client_id, pn.ts)
      : postNote(pn.rawText, undefined, pn.client_id)
    resubmit.catch(() => noteFailed({ reason: "upload_failed", ts: pn.ts, client_id: pn.client_id }))
  }

  // 合并按 ts，最新贴底（输入框侧）。
  const rows = [
    ...pendingNotes.map((pn) => ({
      ts: pn.ts,
      node: <PendingRow key={`p-${pn.client_id}`} pn={pn} onRetry={handleRetry} />,
    })),
    ...feedReceipts.map((r) => ({
      ts: r.ts,
      node: <ReceiptRow key={`r-${r.client_id}`} r={r} onDismiss={dismissReceipt} />,
    })),
    ...qaItems
      .filter((q) => !q.inlineDismissed)
      .map((q) => ({ ts: q.ts, node: <QaRow key={`q-${q.client_id}`} q={q} /> })),
  ].sort((a, b) => a.ts - b.ts)

  if (rows.length === 0) return null
  return (
    // pb-[32px]：浮层底边下沉 26px 藏进输入框 pill（AdminHome bottom-[calc(100%-26px)]），
    // 留足底距让最后一行浮在 pill 之上不被挡（沿用旧 NoteList 的补偿思路）。
    <div className="pb-[32px] space-y-0.5 max-h-[40vh] overflow-y-auto pointer-events-auto rounded-t-2xl bg-background/95 backdrop-blur-sm px-3 pt-2 shadow-[0_-12px_28px_-20px_rgba(0,0,0,0.18)]">
      {rows.length > 1 && (
        <div className="px-1 pb-0.5 text-[10px] text-muted-foreground/50">{rows.length} 条</div>
      )}
      {rows.map((r) => r.node)}
    </div>
  )
}
