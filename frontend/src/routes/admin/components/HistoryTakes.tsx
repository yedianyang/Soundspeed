import { useMemo, useState } from "react"
import { useQueryClient } from "@tanstack/react-query"
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
import type { TakeDTO, TakeStatus, TranscriptSegmentDTO } from "@/types/api"
import { useTake, correctSegmentSpeaker, takeQueryKey } from "@/lib/api"
import { useSessionStore } from "@/store/session"
import { ScriptDiffView } from "./ScriptDiffView"
import { SpeakerLabel } from "./SpeakerLabel"

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

// 展开后的详情：拉 getTake → segments（speaker 可纠正）+ L2 摘要 + line_matches。
function TakeDetail({
  takeId,
  corrected,
  onCorrected,
}: {
  takeId: number
  corrected: boolean
  onCorrected: () => void
}) {
  const { data, isLoading, isError } = useTake(takeId, true)
  const queryClient = useQueryClient()
  // 纠正失败的 segment（轻量错误反馈，无 toast 库）。
  const [failedSegId, setFailedSegId] = useState<number | null>(null)

  // 候选 = 本 take 出现过的 distinct speaker（含当前条自身）+ null（未知）。
  const candidates = useMemo<(string | null)[]>(() => {
    const ids = new Set<string>()
    for (const seg of data?.segments ?? []) {
      if (seg.speaker) ids.add(seg.speaker)
    }
    return [...ids, null]
  }, [data])

  if (isLoading) {
    return <p className="text-sm text-muted-foreground">加载中…</p>
  }
  if (isError || !data) {
    return <p className="text-sm text-destructive">详情加载失败</p>
  }

  const diff = data.script_diff

  async function handleCorrect(seg: TranscriptSegmentDTO, next: string | null) {
    // 点当前值是 no-op：不发 PATCH、不标记已纠正。
    if (next === seg.speaker) return
    try {
      await correctSegmentSpeaker(takeId, seg.segment_id, next)
      await queryClient.invalidateQueries({ queryKey: takeQueryKey(takeId) })
      setFailedSegId(null)
      onCorrected()
    } catch (err) {
      // 失败：不改本地 UI（以 refetch 为准），标记该条以显示错误提示。
      console.error("纠正说话人失败", err)
      setFailedSegId(seg.segment_id)
    }
  }

  return (
    <div className="space-y-3">
      {/* transcript segments：ch1 speaker 可点纠正；ch2 speaker 恒 null，沿用纯文本分支即不显示 label（达成 ch2 不可改，无需 disabled） */}
      {data.segments.length > 0 ? (
        <div className="space-y-1.5">
          {data.segments.map((seg) => (
            <div key={seg.segment_id}>
              <p className="text-sm">
                {seg.ch === 1 ? (
                  <SpeakerLabel
                    speaker={seg.speaker}
                    options={candidates}
                    onChange={(next) => handleCorrect(seg, next)}
                  />
                ) : (
                  seg.speaker && (
                    <span className={cn("font-medium mr-1", speakerColor(seg.speaker))}>
                      {seg.speaker}：
                    </span>
                  )
                )}
                {seg.ch === 1 ? " " : null}
                {seg.text}
              </p>
              {failedSegId === seg.segment_id && (
                <p className="text-xs text-destructive">说话人纠正失败，请重试</p>
              )}
            </div>
          ))}
        </div>
      ) : (
        <p className="text-sm text-muted-foreground/60">无转录片段</p>
      )}

      {/* L2 diff */}
      <div className="flex items-center gap-2">
        <div className="flex-1 h-px bg-border" />
        <span className="text-[10px] text-muted-foreground whitespace-nowrap">L2</span>
        <div className="flex-1 h-px bg-border" />
      </div>
      {corrected && (
        <p className="text-[11px] text-muted-foreground/80">说话人已纠正，剧本分析未更新</p>
      )}
      <ScriptDiffView diff={diff} />
    </div>
  )
}

export function HistoryTakes() {
  // takes 由 AdminHome 的 useTakes + seedTakes 桥接填充（始终挂载），此处只读 store。
  const takesMap = useSessionStore((s) => s.takes)

  // 本地展示态（scope guard：状态 override 不持久化，无端点）。
  const [statusOverrides, setStatusOverrides] = useState<Record<number, TakeStatus>>({})
  const [expanded, setExpanded] = useState<Set<number>>(new Set())
  // 本会话纠正过的 take（放父组件，避免 TakeDetail 折叠 unmount 丢失提示态）。
  const [correctedTakes, setCorrectedTakes] = useState<Set<number>>(new Set())

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
                <TakeDetail
                  takeId={take.take_id}
                  corrected={correctedTakes.has(take.take_id)}
                  onCorrected={() =>
                    setCorrectedTakes((prev) => new Set(prev).add(take.take_id))
                  }
                />
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
