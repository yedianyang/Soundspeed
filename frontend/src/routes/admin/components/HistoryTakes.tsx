import { useState } from "react"
import { ChevronRight, ChevronUp } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Card, CardContent } from "@/components/ui/card"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { STATUS_DOT, STATUS_LABEL, MARK_ORDER, speakerColor } from "@/lib/constants"
import { cn } from "@/lib/utils"
import { mutedCard } from "@/lib/styles"
import type { TakeDTO, TakeStatus, LineMatch } from "@/types/api"
import { useTake } from "@/lib/api"
import { useSessionStore } from "@/store/session"

const DIFF_LABEL: Record<LineMatch["diff_type"], string> = {
  match: "匹配",
  missing: "漏词",
  substitution: "改词",
  insertion: "加词",
}

function StatusBadge({
  status,
  onChange,
}: {
  status: TakeStatus
  onChange: (status: TakeStatus) => void
}) {
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Badge variant="secondary" className="gap-1 cursor-pointer">
          <span className={cn("size-1.5 rounded-full", STATUS_DOT[status])} />
          {STATUS_LABEL[status]}
        </Badge>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start">
        <DropdownMenuLabel>修改状态（本地）</DropdownMenuLabel>
        {(MARK_ORDER as TakeStatus[]).map((s) => (
          <DropdownMenuItem
            key={s}
            className={cn(s === status && "bg-accent")}
            onClick={() => onChange(s)}
          >
            <span className={cn("size-1.5 rounded-full mr-2", STATUS_DOT[s])} />
            {STATUS_LABEL[s]}
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

// 展开后的详情：拉 getTake → segments + L2 摘要 + line_matches。
function TakeDetail({ takeId }: { takeId: number }) {
  const { data, isLoading, isError } = useTake(takeId, true)

  if (isLoading) {
    return <p className="text-sm text-muted-foreground">加载中…</p>
  }
  if (isError || !data) {
    return <p className="text-sm text-destructive">详情加载失败</p>
  }

  const diff = data.script_diff

  return (
    <div className="space-y-3">
      {/* transcript segments */}
      {data.segments.length > 0 ? (
        <div className="space-y-1.5">
          {data.segments.map((seg) => (
            <p key={seg.segment_id} className="text-sm">
              {seg.speaker && (
                <span className={cn("font-medium mr-1", speakerColor(seg.speaker))}>
                  {seg.speaker}：
                </span>
              )}
              {seg.text}
            </p>
          ))}
        </div>
      ) : (
        <p className="text-sm text-muted-foreground/60">无转录片段</p>
      )}

      {/* L2 摘要 + line_matches */}
      <div className="flex items-center gap-2">
        <div className="flex-1 h-px bg-border" />
        <span className="text-[10px] text-muted-foreground whitespace-nowrap">L2</span>
        <div className="flex-1 h-px bg-border" />
      </div>
      {diff ? (
        <div className="space-y-2">
          {diff.script_diff_summary ? (
            <p className="text-sm text-foreground">{diff.script_diff_summary}</p>
          ) : (
            <p className="text-sm text-muted-foreground/60">无偏差摘要</p>
          )}
          {diff.line_matches.length > 0 && (
            <div className="space-y-1">
              {diff.line_matches.map((lm, i) => (
                <div key={i} className="flex items-baseline gap-2 text-xs">
                  <span className="font-mono text-muted-foreground w-10 flex-shrink-0">
                    {lm.line_no >= 0 ? `L${lm.line_no}` : "—"}
                  </span>
                  <span className="text-muted-foreground flex-shrink-0">
                    {DIFF_LABEL[lm.diff_type]}
                  </span>
                  {lm.detail && <span className="text-foreground">{lm.detail}</span>}
                </div>
              ))}
            </div>
          )}
        </div>
      ) : (
        <p className="text-sm text-muted-foreground/60">L2 未完成 / 无剧本</p>
      )}
    </div>
  )
}

export function HistoryTakes() {
  // takes 由 AdminHome 的 useTakes + seedTakes 桥接填充（始终挂载），此处只读 store。
  const takesMap = useSessionStore((s) => s.takes)

  // 本地展示态（scope guard：状态 override 不持久化，无端点）。
  const [statusOverrides, setStatusOverrides] = useState<Record<number, TakeStatus>>({})
  const [expanded, setExpanded] = useState<Set<number>>(new Set())

  const takes: TakeDTO[] = Array.from(takesMap.values()).sort(
    (a, b) => a.scene_id - b.scene_id || a.take_number - b.take_number
  )

  const getStatus = (t: TakeDTO) => statusOverrides[t.take_id] ?? t.status

  const toggleExpand = (id: number) => {
    setExpanded((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  if (takes.length === 0) {
    return (
      <p className="py-8 text-sm text-muted-foreground/60 text-center">
        暂无 take，开始录制后出现
      </p>
    )
  }

  return (
    <div className="py-4 space-y-2.5">
      {takes.map((take) => {
        const isExpanded = expanded.has(take.take_id)
        const summary = take.script_diff?.script_diff_summary
        return (
          <Card
            key={take.take_id}
            className={cn(mutedCard, "w-full text-left hover:bg-muted transition-colors")}
          >
            <CardContent className="p-4 space-y-2">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-1.5 flex-wrap">
                  <span className="font-mono text-xs text-muted-foreground">
                    Scene {take.scene_id} · Take {take.take_number}
                  </span>
                  {take.shot && (
                    <span className="font-mono text-[10px] text-muted-foreground/70">
                      {take.shot}
                    </span>
                  )}
                  <StatusBadge
                    status={getStatus(take)}
                    onChange={(s) =>
                      setStatusOverrides((prev) => ({ ...prev, [take.take_id]: s }))
                    }
                  />
                </div>
                <Button
                  variant="ghost"
                  size="icon-sm"
                  className="size-6 rounded-full"
                  onClick={() => toggleExpand(take.take_id)}
                >
                  {isExpanded ? (
                    <ChevronUp className="size-3.5" />
                  ) : (
                    <ChevronRight className="size-3.5" />
                  )}
                </Button>
              </div>

              {isExpanded ? (
                <TakeDetail takeId={take.take_id} />
              ) : (
                <p className="text-sm text-muted-foreground line-clamp-2">
                  {summary ?? "（展开查看转录）"}
                </p>
              )}
            </CardContent>
          </Card>
        )
      })}
    </div>
  )
}
