import type { ReactNode } from "react"
import { Sparkles, X } from "lucide-react"
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetDescription,
} from "@/components/ui/sheet"
import { Button } from "@/components/ui/button"
import { useSessionStore } from "@/store/session"
import { ScriptDiffView } from "@/routes/admin/components/ScriptDiffView"

// 时间线一项：QP 问答 = 琥珀 ✦ 左条；L2 推送 = 中性左条。
// 沿用就地层配色语言（QP 暖色作 LLM 反馈基调、L2/note 中性），档案层与就地层一致。
function FeedItem({
  source,
  time,
  children,
}: {
  source: "QP" | "L2"
  time: string
  children: ReactNode
}) {
  const isQP = source === "QP"
  return (
    <div className={"pl-3 border-l-2 " + (isQP ? "border-amber-400/40" : "border-muted-foreground/20")}>
      <div className="flex items-center gap-2 mb-1.5">
        {isQP && <Sparkles className="size-3 text-amber-500/80" />}
        <span className="text-xs font-medium text-muted-foreground">{isQP ? "问答" : "L2 推送"}</span>
        <span className="text-[10px] font-mono text-muted-foreground/50">{time}</span>
      </div>
      {children}
    </div>
  )
}

const fmtTime = (ts: number) =>
  ts > 0 ? new Date(ts * 1000).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" }) : ""

// 留存层：QP 问答 + L2 推送的全历史时间线（底部 Sheet）。就地层短暂自清，档案层是权威留存。
// 数据从 store 派生：takes 里带 script_diff 的为 L2，qaItems 里 done 的为 QP，按时序混排。
export function LLMArchiveSheet({
  open,
  onOpenChange,
}: {
  open: boolean
  onOpenChange: (o: boolean) => void
}) {
  const takesMap = useSessionStore((s) => s.takes)
  const qaItems = useSessionStore((s) => s.qaItems)

  // L2：带 script_diff 的 take（按开拍时序）。start_ts/created_at 为 0 = 实时 L2 刚到、getTakes
  // 尚未回填真实时间 → 视为最新（排最底），refetch 后落到真实位置。
  const l2 = Array.from(takesMap.values())
    .filter((t) => t.script_diff != null)
    .map((t) => ({ kind: "L2" as const, sortTs: t.start_ts || t.created_at || Number.MAX_SAFE_INTEGER, t }))
  // QP：已落定的问答（processing/failed 不入档案）。
  const qa = qaItems
    .filter((q) => q.status === "done")
    .map((q) => ({ kind: "QP" as const, sortTs: q.ts, q }))
  const items = [...l2, ...qa].sort((a, b) => a.sortTs - b.sortTs)

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="bottom" className="h-[70vh] rounded-t-2xl p-0 gap-0 flex flex-col">
        <SheetHeader className="flex-shrink-0 px-4 pt-4 pb-3 border-b">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Sparkles className="size-4 text-amber-500" />
              <SheetTitle className="text-base">LLM 反馈</SheetTitle>
              <span className="text-xs text-muted-foreground">QP 问答 · L2 推送 · 全历史</span>
            </div>
            <Button
              variant="ghost"
              size="icon-sm"
              className="rounded-full"
              onClick={() => onOpenChange(false)}
            >
              <X className="size-4" />
            </Button>
          </div>
          <SheetDescription className="sr-only">LLM 问答与 L2 推送的全历史时间线</SheetDescription>
        </SheetHeader>
        <div className="flex-1 min-h-0 overflow-y-auto px-4 py-4 space-y-6">
          {items.length === 0 && (
            <p className="text-xs text-muted-foreground text-center pt-4">还没有问答或 L2 推送</p>
          )}
          {items.map((it) =>
            it.kind === "L2" ? (
              <FeedItem key={`l2-${it.t.take_id}`} source="L2" time={`Take ${it.t.take_number}`}>
                <ScriptDiffView diff={it.t.script_diff} />
              </FeedItem>
            ) : (
              <FeedItem key={`qa-${it.q.client_id}`} source="QP" time={fmtTime(it.q.ts)}>
                <div className="text-sm font-medium text-foreground mb-1">{it.q.question}</div>
                <div className="text-sm leading-relaxed text-foreground/80 whitespace-pre-line">
                  {it.q.answer}
                </div>
              </FeedItem>
            ),
          )}
        </div>
      </SheetContent>
    </Sheet>
  )
}
