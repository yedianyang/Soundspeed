import { useState, useRef, type ReactNode } from "react"
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
import type { Status, Take } from "@/types/take"
import { HISTORY_TAKES } from "@/data/mock"

function LongPressDropdown({
  trigger,
  children,
}: {
  trigger: ReactNode
  children: ReactNode
}) {
  const [open, setOpen] = useState(false)
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const [pressing, setPressing] = useState(false)

  const startPress = () => {
    setPressing(true)
    const id = setTimeout(() => {
      setPressing(false)
      setOpen(true)
    }, 1000)
    timerRef.current = id
  }

  const endPress = () => {
    if (timerRef.current) {
      clearTimeout(timerRef.current)
      timerRef.current = null
    }
    setPressing(false)
  }

  return (
    <DropdownMenu open={open} onOpenChange={setOpen}>
      <DropdownMenuTrigger asChild>
        <Button
          variant="ghost"
          size="sm"
          onPointerDown={(e) => {
            e.preventDefault()
            startPress()
          }}
          onPointerUp={endPress}
          onPointerLeave={endPress}
          className="relative overflow-hidden gap-0.5 h-7 px-1.5 rounded-full bg-background border border-border/60 shadow-sm active:scale-95 transition-transform select-none"
        >
          <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
            <div
              className="rounded-full bg-primary/15 transition-transform duration-1000 ease-linear"
              style={{
                width: '200%',
                height: '200%',
                transform: pressing ? 'scale(1)' : 'scale(0)',
                transformOrigin: 'center',
              }}
            />
          </div>
          <span className="relative z-10 font-mono text-[10px]">{trigger}</span>
          <ChevronDown className="relative z-10 size-3 text-muted-foreground" />
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
          className="w-full text-left rounded-4xl bg-muted/50 hover:bg-muted shadow-none ring-0 py-0 transition-colors"
        >
          <CardContent className="p-4 space-y-2">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-0.5 sm:gap-1 flex-wrap">
                <LongPressDropdown trigger={<>Scene {getScene(take)}</>}>
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
                </LongPressDropdown>

                <LongPressDropdown trigger={<>Shot {getShot(take)}</>}>
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
                </LongPressDropdown>

                <LongPressDropdown trigger={<>Take {getNo(take)}</>}>
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
                </LongPressDropdown>

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
