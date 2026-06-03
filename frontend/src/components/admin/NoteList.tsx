import { useEffect, useState } from "react"
import { Card } from "@/components/ui/card"
import { getTakeNotes } from "@/lib/api"
import type { NoteDTO } from "@/types/api"

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
  refreshKey: number // 每次递增触重新 fetch
}

export default function NoteList({ takeId, refreshKey }: NoteListProps) {
  const [notes, setNotes] = useState<NoteDTO[]>([])

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
  }, [takeId, refreshKey])

  if (takeId == null || notes.length === 0) {
    return (
      <Card size="sm" className="p-3 gap-1 text-xs text-muted-foreground">
        暂无备注
      </Card>
    )
  }

  // 按时间倒序
  const sorted = [...notes].reverse()

  return (
    <Card size="sm" className="p-3 gap-1 max-h-[200px] overflow-y-auto">
      {sorted.map((n) => (
        <div key={n.event_id} className="flex items-start gap-2 text-xs py-0.5">
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
