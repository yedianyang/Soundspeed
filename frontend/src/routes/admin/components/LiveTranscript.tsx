import { speakerColor } from "@/lib/constants"
import { cn } from "@/lib/utils"
import { useSessionStore, type LiveSeg } from "@/store/session"
import { TakeDivider } from "./TakeBlock"

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
  const currentTake = useSessionStore((s) => s.currentTake)

  const merged: RenderSeg[] = [
    ...segments.ch1.map((s, i) => ({ ...s, ch: 1 as const, idx: i })),
    ...segments.ch2.map((s, i) => ({ ...s, ch: 2 as const, idx: i })),
  ].sort((a, b) => a.start_frame - b.start_frame)

  const hasContent = merged.length > 0

  return (
    <div className="px-4 py-4 space-y-4">
      {currentTake.take_number != null && (
        <TakeDivider no={currentTake.take_number} />
      )}

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
          {currentTake.recording ? "等待转录…" : "未在录制"}
        </p>
      )}
    </div>
  )
}
