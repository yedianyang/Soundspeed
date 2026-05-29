import { useState, useRef, type ReactNode, type PointerEvent } from "react"
import { ChevronDown, ChevronRight, ChevronUp } from "lucide-react"
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
import { STATUS_DOT, STATUS_LABEL, MARK_ORDER } from "@/lib/constants"
import { cn } from "@/lib/utils"
import { mutedCard } from "@/lib/styles"
import type { Status, Take } from "@/types/take"
import { HISTORY_TAKES } from "@/data/mock"

function TapDropdown({
  trigger,
  children,
}: {
  trigger: ReactNode
  children: ReactNode
}) {
  const [open, setOpen] = useState(false)
  const startRef = useRef<{ x: number; y: number } | null>(null)

  // 单击弹菜单，与底部控制条统一；密集列表里滚动时不误触：
  // pointerdown 记起点，pointerup 时若位移超阈值判为滚动、不弹。比 1s 长按更快，又挡住滑动误触。
  const handlePointerDown = (e: PointerEvent<HTMLButtonElement>) => {
    e.preventDefault() // 阻止 Radix Trigger 默认 pointerdown 立即打开，改由手势判定
    startRef.current = { x: e.clientX, y: e.clientY }
  }
  const handlePointerUp = (e: PointerEvent<HTMLButtonElement>) => {
    const start = startRef.current
    startRef.current = null
    if (!start) return
    const moved =
      Math.abs(e.clientX - start.x) > 10 || Math.abs(e.clientY - start.y) > 10
    if (!moved) setOpen(true)
  }

  return (
    <DropdownMenu open={open} onOpenChange={setOpen}>
      <DropdownMenuTrigger asChild>
        <Button
          variant="ghost"
          size="sm"
          onPointerDown={handlePointerDown}
          onPointerUp={handlePointerUp}
          className="gap-0.5 h-7 px-1.5 rounded-full bg-background border border-border/60 shadow-sm active:scale-95 transition-transform select-none"
        >
          <span className="font-mono text-[10px]">{trigger}</span>
          <ChevronDown className="size-3 text-muted-foreground" />
        </Button>
      </DropdownMenuTrigger>
      {children}
    </DropdownMenu>
  )
}

function StatusBadge({
  status,
  onChange,
}: {
  status: Status
  onChange: (status: Status) => void
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
        <DropdownMenuLabel>修改状态</DropdownMenuLabel>
        {(MARK_ORDER as Status[]).map((s) => (
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

export function HistoryTakes() {
  const [overrides, setOverrides] = useState<Record<string, Status>>({})
  const [sceneOverrides, setSceneOverrides] = useState<Record<string, number>>({})
  const [shotOverrides, setShotOverrides] = useState<Record<string, number>>({})
  const [noOverrides, setNoOverrides] = useState<Record<string, number>>({})
  const [expanded, setExpanded] = useState<Set<string>>(new Set())

  const getStatus = (take: Take) => overrides[take.id] ?? take.status
  const getScene = (take: Take) => sceneOverrides[take.id] ?? take.scene
  const getShot = (take: Take) => shotOverrides[take.id] ?? take.shot
  const getNo = (take: Take) => noOverrides[take.id] ?? take.no

  const toggleExpand = (id: string) => {
    setExpanded((prev) => {
      const next = new Set(prev)
      if (next.has(id)) {
        next.delete(id)
      } else {
        next.add(id)
      }
      return next
    })
  }

  return (
    <div className="py-4 space-y-2.5">
      {HISTORY_TAKES.map((take) => (
        <Card
          key={take.id}
          className={cn(mutedCard, "w-full text-left hover:bg-muted transition-colors")}
        >
          <CardContent className="p-4 space-y-2">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-0.5 sm:gap-1 flex-wrap">
                <TapDropdown trigger={<>Scene {getScene(take)}</>}>
                  <DropdownMenuContent align="start">
                    <DropdownMenuLabel>修改 Scene</DropdownMenuLabel>
                    {[1, 2, 3, 4].map((n) => (
                      <DropdownMenuItem
                        key={n}
                        className={cn(n === getScene(take) && "bg-accent")}
                        onClick={() => setSceneOverrides((prev) => ({ ...prev, [take.id]: n }))}
                      >
                        Scene {n}
                      </DropdownMenuItem>
                    ))}
                  </DropdownMenuContent>
                </TapDropdown>

                <TapDropdown trigger={<>Shot {getShot(take)}</>}>
                  <DropdownMenuContent align="start">
                    <DropdownMenuLabel>修改 Shot</DropdownMenuLabel>
                    {[1, 2, 3, 4].map((n) => (
                      <DropdownMenuItem
                        key={n}
                        className={cn(n === getShot(take) && "bg-accent")}
                        onClick={() => setShotOverrides((prev) => ({ ...prev, [take.id]: n }))}
                      >
                        Shot {n}
                      </DropdownMenuItem>
                    ))}
                  </DropdownMenuContent>
                </TapDropdown>

                <TapDropdown trigger={<>Take {getNo(take)}</>}>
                  <DropdownMenuContent align="start">
                    <DropdownMenuLabel>修改 Take</DropdownMenuLabel>
                    {[1, 2, 3, 4, 5].map((n) => (
                      <DropdownMenuItem
                        key={n}
                        className={cn(n === getNo(take) && "bg-accent")}
                        onClick={() => setNoOverrides((prev) => ({ ...prev, [take.id]: n }))}
                      >
                        Take {n}
                      </DropdownMenuItem>
                    ))}
                  </DropdownMenuContent>
                </TapDropdown>

                <StatusBadge
                  status={getStatus(take)}
                  onChange={(s) => setOverrides((prev) => ({ ...prev, [take.id]: s }))}
                />
              </div>
              <div className="flex items-center gap-1">
                <span className="text-[10px] font-mono text-muted-foreground">14:30</span>
                <Button
                  variant="ghost"
                  size="icon-sm"
                  className="size-6 rounded-full"
                  onClick={() => toggleExpand(take.id)}
                >
                  {expanded.has(take.id) ? (
                    <ChevronUp className="size-3.5" />
                  ) : (
                    <ChevronRight className="size-3.5" />
                  )}
                </Button>
              </div>
            </div>
            {expanded.has(take.id) ? (
              <div className="space-y-2">
                {take.lines.map((line, i) => (
                  <p key={i} className="text-sm">
                    <span className="text-primary font-medium">{line.speaker}：</span>
                    {line.text}
                  </p>
                ))}
              </div>
            ) : (
              <p className="text-sm text-muted-foreground line-clamp-2">
                {take.lines.map((l) => l.text).join("  ")}
              </p>
            )}
            {take.note && (
              <div className="flex items-center gap-2">
                <div className="flex-1 h-px bg-border" />
                <span className="text-[10px] text-muted-foreground whitespace-nowrap">
                  Note
                </span>
                <div className="flex-1 h-px bg-border" />
              </div>
            )}
            {take.note && (
              <p className="text-xs text-muted-foreground">
                {take.note}
              </p>
            )}
          </CardContent>
        </Card>
      ))}
    </div>
  )
}
