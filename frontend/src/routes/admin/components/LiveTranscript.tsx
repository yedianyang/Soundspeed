import { AlertCircle, Loader2 } from "lucide-react"
import { speakerColor } from "@/lib/constants"
import { cn, formatTakeLabel } from "@/lib/utils"
import { useSessionStore, type LiveSeg } from "@/store/session"
import { TakeDivider } from "./TakeDivider"

// take.end 后处理状态条：分离说话人 / 生成摘要(Gemma) / 出错。
function ProcessingBanner() {
  const processing = useSessionStore((s) => s.processing)
  if (!processing) return null

  if (processing.phase === "error") {
    return (
      <div className="flex items-start gap-2 rounded-xl bg-destructive/10 px-3 py-2 text-sm text-destructive">
        <AlertCircle className="size-4 mt-0.5 flex-shrink-0" />
        <span>{processing.detail ?? "后处理出错"}</span>
      </div>
    )
  }

  const label =
    processing.phase === "diarizing"
      ? "正在分离说话人…"
      : "正在生成场记摘要（Gemma）…"
  return (
    <div className="flex items-center gap-2 rounded-xl bg-muted/70 px-3 py-2 text-sm text-muted-foreground">
      <Loader2 className="size-4 flex-shrink-0 animate-spin" />
      <span>{label}</span>
    </div>
  )
}

// 渲染用条目：合并两声道后按 start_frame 升序。
// key 用「声道内位置」idx：每个声道 append-only + 仅替换末尾 partial，索引不会平移/收缩，
// 故 ch-idx 唯一且稳定——interleave 插入中段不影响（key 与合并后位置无关），
// partial→final 同 key 原地更新（去光标 + italic→黑，不 remount）。
interface RenderSeg extends LiveSeg {
  ch: 1 | 2
  idx: number
}

export function LiveTranscript() {
  const segments = useSessionStore((s) => s.segments)
  // 当前 take 从 currentTakeId + takes Map 派生（编号 + 后缀），录制态读 isRecording。
  const currentTakeId = useSessionStore((s) => s.currentTakeId)
  const isRecording = useSessionStore((s) => s.isRecording)
  const currentTake = useSessionStore((s) =>
    currentTakeId != null ? s.takes.get(currentTakeId) : undefined,
  )

  const merged: RenderSeg[] = [
    ...segments.ch1.map((s, i) => ({ ...s, ch: 1 as const, idx: i })),
    ...segments.ch2.map((s, i) => ({ ...s, ch: 2 as const, idx: i })),
  ].sort((a, b) => a.start_frame - b.start_frame)

  const hasContent = merged.length > 0

  return (
    <div className="px-4 py-4 space-y-4">
      {currentTake?.take_number != null && (
        <TakeDivider label={formatTakeLabel(currentTake)} />
      )}

      <ProcessingBanner />

      {hasContent ? (
        <div className="space-y-1.5 leading-relaxed">
          {merged.map((seg) => (
            <p
              key={`${seg.ch}-${seg.idx}`}
              className={cn(
                "text-sm",
                seg.isPartial ? "text-muted-foreground italic" : "text-foreground"
              )}
            >
              {seg.speaker && (
                <span className={cn("font-medium mr-1", speakerColor(seg.speaker))}>
                  {seg.speaker}：
                </span>
              )}
              {seg.text}
              {seg.isPartial && (
                <span className="inline-block w-0.5 h-4 bg-muted-foreground ml-0.5 align-middle animate-pulse" />
              )}
            </p>
          ))}
        </div>
      ) : (
        <p className="text-sm text-muted-foreground/60 text-center py-8">
          {isRecording ? "等待转录…" : "未在录制"}
        </p>
      )}
    </div>
  )
}
