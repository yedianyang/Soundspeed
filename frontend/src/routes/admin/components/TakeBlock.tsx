import { useState } from "react"
import { Separator } from "@/components/ui/separator"
import { STATUS_DOT } from "@/lib/constants"
import { cn } from "@/lib/utils"
import type { Take } from "@/types/take"
import { SpeakerLabel } from "./SpeakerLabel"

export function TakeBlock({
  take,
  partial,
  muted = false,
}: {
  take: Take
  partial?: string
  muted?: boolean
}) {
  const [overrides, setOverrides] = useState<Record<number, string>>({})

  const getSpeaker = (i: number) => overrides[i] ?? take.lines[i].speaker

  return (
    <div
      className={cn(
        "flex items-baseline gap-3 leading-relaxed",
        muted ? "text-muted-foreground" : "text-foreground"
      )}
    >
      <span
        className={cn(
          "text-[11px] font-mono uppercase tracking-wider w-10 flex-shrink-0",
          muted ? "text-muted-foreground/60" : "text-muted-foreground"
        )}
      >
        T{take.no}
      </span>
      <div className="flex-1 space-y-1.5">
        {take.lines.map((line, i) => (
          <p key={i} className="text-base">
            {take.status !== "recording" && (
              <SpeakerLabel
                speaker={getSpeaker(i)}
                onChange={(s) => setOverrides((prev) => ({ ...prev, [i]: s }))}
                muted={muted}
              />
            )}
            {line.text}
          </p>
        ))}
        {partial && (
          <p className="text-muted-foreground italic">
            {partial}
            <span className="inline-block w-0.5 h-4 bg-muted-foreground ml-0.5 align-middle animate-pulse" />
          </p>
        )}
      </div>
      {take.status !== "recording" && (
        <span
          className={cn(
            "size-2 rounded-full mt-2 flex-shrink-0",
            STATUS_DOT[take.status]
          )}
        />
      )}
    </div>
  )
}

export function TakeDivider({ no, time }: { no: number; time: string }) {
  return (
    <div className="flex items-center gap-3">
      <Separator className="flex-1" />
      <span className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">
        Take {no} · {time}
      </span>
      <Separator className="flex-1" />
    </div>
  )
}
