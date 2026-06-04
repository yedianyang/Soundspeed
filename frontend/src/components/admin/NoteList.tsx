import { useEffect, useState } from "react"
import { Card } from "@/components/ui/card"
import { getTakeNotes } from "@/lib/api"
import { useSessionStore } from "@/store/session"
import type { NoteDTO, PendingNote } from "@/types/api"

const CATEGORY_COLORS: Record<string, string> = {
  keeper: "text-green-600",
  ng: "text-red-600",
  issue: "text-yellow-600",
  note: "text-muted-foreground",
}

function formatTime(ts: number): string {
  const d = new Date(ts * 1000)
  return d.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", second: "2-digit" })
}

interface NoteListProps {
  takeId: number | null
  refreshKey: number
}

export default function NoteList({ takeId, refreshKey }: NoteListProps) {
  const [notes, setNotes] = useState<NoteDTO[]>([])
  const pendingNotes = useSessionStore((s) => s.pendingNotes)
  // note.processed 落库后 store bump notesVersion → 触发 resolved refetch（衔接 pending 移除与 resolved 显示）。
  const notesVersion = useSessionStore((s) => s.notesVersion)

  useEffect(() => {
    if (takeId == null) {
      setNotes([])
      return
    }
    let cancelled = false
    getTakeNotes(takeId).then((res) => {
      if (!cancelled) setNotes(res.events)
    }).catch(() => {
      if (!cancelled) setNotes([])
    })
    return () => { cancelled = true }
  }, [takeId, refreshKey, notesVersion])

  // pending 是乐观本地态，独立于 take——没建 take（takeId==null）也要让场记看到「处理中」，否则
  // 提交后零反馈。resolved notes 已由上面 effect 在 takeId==null 时清空成 []，故只看两个数组长度：
  // 都空才不渲染（避免空浮层永久遮住 main 一条）。
  const hasNotes = pendingNotes.length > 0 || notes.length > 0
  if (!hasNotes) {
    return null
  }

  // 按时间倒序
  const sorted = [...notes].reverse()

  return (
    // pb-[35px] = 16(可见上间距) + 17(藏量) + 2(上移)；藏量 17 与 AdminHome 浮层
    // bottom-[calc(100%-26px)] 的 17 同源，改一处两处都要改（否则浮层底边与 pill 顶错位）。
    <Card
      size="sm"
      className="pointer-events-auto px-3 pt-4 pb-[35px] gap-1 max-h-[40vh] overflow-y-auto bg-background rounded-t-2xl rounded-b-none shadow-[0_-4px_16px_rgba(0,0,0,0.1)] ring-0"
    >
      {/* Pending notes（处理中） */}
      {pendingNotes.map((pn: PendingNote, i: number) => (
        <div key={`pending-${pn.ts}-${i}`} className="flex items-center gap-2 text-xs py-0.5 opacity-60">
          <span className="text-muted-foreground font-mono whitespace-nowrap">
            {formatTime(pn.ts)}
          </span>
          <span className={`font-semibold whitespace-nowrap ${CATEGORY_COLORS[pn.category] ?? "text-muted-foreground"}`}>
            @{pn.category}
          </span>
          {pn.content && (
            <span className="text-foreground break-all">{pn.content}</span>
          )}
          <span className="text-muted-foreground italic whitespace-nowrap">处理中...</span>
        </div>
      ))}
      {/* Resolved notes */}
      {sorted.map((n) => (
        <div key={n.event_id} className="flex items-center gap-2 text-xs py-0.5">
          <span className="text-muted-foreground font-mono whitespace-nowrap">
            {formatTime(n.ts)}
          </span>
          <span className={`font-semibold whitespace-nowrap ${CATEGORY_COLORS[n.category] ?? "text-muted-foreground"}`}>
            @{n.category}
          </span>
          {n.content && (
            <span className="text-foreground break-all">{n.content}</span>
          )}
        </div>
      ))}
    </Card>
  )
}
