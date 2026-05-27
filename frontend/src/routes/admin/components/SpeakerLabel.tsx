import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { SPEAKER_OPTIONS, SPEAKER_DOT, SPEAKER_TEXT } from "@/lib/constants"
import { cn } from "@/lib/utils"

export function SpeakerLabel({
  speaker,
  onChange,
  muted = false,
}: {
  speaker: string
  onChange: (speaker: string) => void
  muted?: boolean
}) {
  const dotColor = muted
    ? "bg-muted-foreground/40"
    : (SPEAKER_DOT[speaker] || "bg-muted-foreground")
  const textColor = muted
    ? "text-muted-foreground/60"
    : (SPEAKER_TEXT[speaker] || "text-muted-foreground")

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <span
          className={cn(
            "inline-flex items-center gap-1 cursor-pointer select-none rounded px-1 -ml-1 transition-colors",
            textColor,
            muted ? "hover:bg-muted/40" : "hover:bg-muted"
          )}
        >
          <span className={cn("size-1.5 rounded-full flex-shrink-0", dotColor)} />
          {speaker}：
        </span>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start">
        <DropdownMenuLabel>切换说话人</DropdownMenuLabel>
        {SPEAKER_OPTIONS.map((s) => (
          <DropdownMenuItem
            key={s}
            className={cn(s === speaker && "bg-accent")}
            onClick={() => onChange(s)}
          >
            <span className={cn("size-1.5 rounded-full mr-2", SPEAKER_DOT[s] || "bg-muted-foreground")} />
            {s}
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
