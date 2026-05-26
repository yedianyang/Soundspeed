import { useState } from "react"
import {
  Mic,
  Plus,
  Trash2,
  ChevronDown,
} from "lucide-react"
import { Button } from "@/components/ui/button"
import { Separator } from "@/components/ui/separator"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { cn } from "@/lib/utils"

type Status = "keeper" | "ng" | "hold" | "recording"

const STATUS_DOT: Record<Status, string> = {
  keeper: "bg-emerald-500",
  ng: "bg-destructive",
  hold: "bg-primary",
  recording: "bg-red-500 animate-pulse",
}

const STATUS_LABEL: Record<Status, string> = {
  keeper: "KEEP",
  ng: "NG",
  hold: "PASS",
  recording: "REC",
}

const MARK_ORDER: Status[] = ["ng", "keeper", "hold"]

export default function BottomControlBar() {
  const [isRecording, setIsRecording] = useState(true)
  const [mark, setMark] = useState<string>("ng")

  const currentMark = (MARK_ORDER.includes(mark as Status) ? mark : "") as Status
  const nextMark = MARK_ORDER[(MARK_ORDER.indexOf(currentMark) + 1) % MARK_ORDER.length]

  return (
    <div className="flex-shrink-0 border-t bg-background">
      {/* Memo input */}
      <div className="px-3 sm:px-5 pt-2 pb-1.5">
        <div className="flex items-center gap-2 h-11 px-4 rounded-4xl bg-muted/60 focus-within:bg-muted transition-colors">
          <input
            placeholder="Typing memo · 例：第三条结尾好，可以用"
            className="flex-1 bg-transparent text-sm focus:outline-none placeholder:text-muted-foreground/70"
          />
          <Button
            variant="ghost"
            size="icon-sm"
            className="rounded-full text-muted-foreground hover:text-foreground"
            title="按麦录音 memo"
          >
            <Mic className="size-4" />
          </Button>
        </div>
      </div>

      {/* Controls: left stack + right REC */}
      <div className="px-3 sm:px-5 pb-2 mt-1 flex items-start justify-between gap-3">
        <div className="flex flex-col gap-2">
          {/* Row 1: Scene / Shot / Take / Mark */}
          <div className="flex items-center gap-2 flex-wrap">
            <SceneShotTakeButton label="Scene" value="3" />
            <SceneShotTakeButton label="Shot" value="2" />
            <SceneShotTakeButton label="Take" value="5" highlight />
            <button
              onClick={() => setMark(nextMark)}
              className="inline-flex items-center gap-1.5 h-9 px-3 rounded-full bg-background shadow-sm active:scale-95 transition-transform"
            >
              <span className={cn("size-1.5 rounded-full", STATUS_DOT[currentMark] || "bg-muted-foreground")} />
              <span className="text-sm font-medium text-foreground">{STATUS_LABEL[currentMark]}</span>
            </button>
          </div>

          {/* Row 2: Next take + Delete */}
          <div className="flex items-center gap-3">
            <button className="inline-flex items-center gap-1.5 h-10 px-5 rounded-full bg-muted/60 active:bg-muted/80 active:scale-95 transition-all text-foreground text-sm font-medium">
              <Plus className="size-4" />
              Next take
            </button>
            <button
              className="inline-flex items-center justify-center h-10 w-10 text-destructive active:scale-95 transition-transform"
              title="Delete last"
            >
              <Trash2 className="size-5" />
            </button>
          </div>
        </div>

        {/* REC button */}
        <button
          onClick={() => setIsRecording(!isRecording)}
          className={cn(
            "size-20 rounded-full flex items-center justify-center text-white shadow-lg transition-all active:scale-95 flex-shrink-0",
            isRecording
              ? "bg-red-600 ring-4 ring-red-500/20"
              : "bg-red-500 ring-2 ring-red-500/10"
          )}
          title={isRecording ? "停止录制" : "开始录制"}
        >
          {isRecording ? (
            <span className="text-xs font-mono tracking-wider font-semibold">0:42</span>
          ) : (
            <span className="text-xs font-mono tracking-wider font-semibold">REC</span>
          )}
        </button>
      </div>

      {/* Log */}
      <div className="px-3 sm:px-5 pb-1.5 pt-0.5 border-t">
        <div className="flex items-center gap-2 text-[11px] font-mono text-muted-foreground overflow-x-auto whitespace-nowrap py-1">
          <span className="size-1.5 rounded-full bg-emerald-500 flex-shrink-0" />
          <span>ASR ch1 latency 0.6s</span>
          <Separator orientation="vertical" className="h-3" />
          <span>LLM queue: 0</span>
          <Separator orientation="vertical" className="h-3" />
          <span>ch1 −12 dB</span>
          <Separator orientation="vertical" className="h-3" />
          <span>ch2 −48 dB</span>
          <Separator orientation="vertical" className="h-3" />
          <span>conn 3 observers</span>
        </div>
      </div>
    </div>
  )
}

function SceneShotTakeButton({
  label,
  value,
  highlight,
}: {
  label: string
  value: string
  highlight?: boolean
}) {
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          className={cn(
            "inline-flex items-center gap-1 h-9 px-2.5 rounded-full text-xs active:scale-95 transition-transform",
            highlight ? "bg-background shadow-sm" : "bg-muted/60"
          )}
        >
          <span className="text-[10px] uppercase tracking-wider text-muted-foreground">
            {label}
          </span>
          <span className="font-semibold text-sm text-foreground">{value}</span>
          <ChevronDown className="size-3 text-muted-foreground" />
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="w-44">
        <DropdownMenuLabel>{label}</DropdownMenuLabel>
        <DropdownMenuItem>
          <span className="font-mono w-8">1</span>
          <span className="text-muted-foreground text-xs">4 takes</span>
        </DropdownMenuItem>
        <DropdownMenuItem className="bg-accent">
          <span className="font-mono w-8">{value}</span>
          <span className="font-semibold text-xs">当前</span>
        </DropdownMenuItem>
        <DropdownMenuItem>
          <span className="font-mono w-8">4</span>
          <span className="text-muted-foreground text-xs">空</span>
        </DropdownMenuItem>
        <DropdownMenuSeparator />
        <DropdownMenuItem>
          <Plus className="size-3.5 mr-2" />
          新建 {label}
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
