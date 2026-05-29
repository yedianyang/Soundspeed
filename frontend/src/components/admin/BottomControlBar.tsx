import {
  Mic,
  Plus,
  Trash2,
  ChevronDown,
} from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { STATUS_DOT, STATUS_LABEL } from "@/lib/constants"
import { cn, formatElapsed } from "@/lib/utils"
import type { Status } from "@/types/take"

interface BottomControlBarProps {
  isRecording: boolean
  onToggleRecording: () => void
  mark: Status
  onCycleMark: () => void
  elapsed: number
}

export default function BottomControlBar({
  isRecording,
  onToggleRecording,
  mark,
  onCycleMark,
  elapsed,
}: BottomControlBarProps) {
  return (
    <div className="flex-shrink-0 border-t bg-background">
      {/* Memo input */}
      <div className="px-3 sm:px-5 pt-2 pb-1.5">
        <div className="flex items-center gap-2 h-11 px-4 rounded-4xl bg-muted/60 focus-within:bg-muted transition-colors">
          <Input
            placeholder="Typing memo · 例：第三条结尾好，可以用"
            className="flex-1 bg-transparent border-0 ring-0 rounded-none text-sm focus:outline-none placeholder:text-muted-foreground/70 focus-visible:ring-0"
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

      {/* Controls: left stack + right REC (absolute) */}
      <div className="px-3 sm:px-5 pb-2 mt-1 relative">
        <div className="flex flex-col gap-2 pr-24 sm:pr-28">
          {/* Row 1: Scene / Shot / Take / Mark */}
          <div className="flex items-center gap-1.5 sm:gap-2">
            <SceneShotTakeButton label="Scene" value="3" />
            <SceneShotTakeButton label="Shot" value="2" />
            <SceneShotTakeButton label="Take" value="6" highlight />
            <Button
              variant="ghost"
              size="default"
              onClick={onCycleMark}
              className="flex-1 sm:flex-none min-w-0 gap-1 sm:gap-1.5 h-9 px-2.5 sm:px-3 rounded-full bg-background border border-border/60 shadow-sm active:scale-95 transition-transform"
            >
              <span className={cn("size-1.5 rounded-full", STATUS_DOT[mark] || "bg-muted-foreground")} />
              <span className="text-sm font-medium text-foreground">{STATUS_LABEL[mark]}</span>
            </Button>
          </div>

          {/* Row 2: Next take + Delete */}
          <div className="flex items-center gap-3">
            <Button
              variant="ghost"
              className="gap-1.5 h-10 px-5 rounded-full bg-muted/60 hover:bg-muted/80 active:bg-muted/80 active:scale-95 transition-all text-foreground text-sm font-medium"
            >
              <Plus className="size-4" />
              Next take
            </Button>
            <Button
              variant="ghost"
              size="icon"
              className="h-10 w-10 text-destructive hover:text-destructive active:scale-95 transition-transform"
              title="Delete last"
            >
              <Trash2 className="size-5" />
            </Button>
          </div>
        </div>

        {/* REC button */}
        <Button
          variant="ghost"
          onClick={onToggleRecording}
          className={cn(
            "absolute right-3 sm:right-5 bottom-2 size-20 rounded-full text-white shadow-lg transition-all active:scale-95 border-0",
            isRecording
              ? "bg-red-600 hover:bg-red-600 ring-4 ring-red-500/20"
              : "bg-red-500 hover:bg-red-500 ring-2 ring-red-500/10"
          )}
          title={isRecording ? "停止录制" : "开始录制"}
        >
          <span className="text-xs font-mono tracking-wider font-semibold">
            {isRecording ? formatElapsed(elapsed) : "REC"}
          </span>
        </Button>
      </div>

      {/* Log */}
      <div className="px-3 sm:px-5 pb-1.5 pt-0.5 border-t">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2 text-[11px] font-mono text-muted-foreground whitespace-nowrap py-1">
            <span className="size-1.5 rounded-full bg-green-500 flex-shrink-0" />
            <span>debug log</span>
          </div>
          <span className="text-[10px] font-mono text-muted-foreground/50">
            powered by Gemma 4
          </span>
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
        <Button
          variant="ghost"
          size="default"
          className={cn(
            "flex-1 sm:flex-none min-w-0 gap-1 h-9 px-2 sm:px-2.5 rounded-full text-xs border border-border/60 active:scale-95 transition-transform",
            highlight ? "bg-background shadow-sm" : "bg-muted/60"
          )}
        >
          <span className="text-[10px] uppercase tracking-wider text-muted-foreground">
            {label}
          </span>
          <span className="font-semibold text-sm text-foreground">{value}</span>
          <ChevronDown className="size-2.5 sm:size-3 text-muted-foreground" />
        </Button>
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
