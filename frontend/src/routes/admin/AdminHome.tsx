import { useEffect, useRef, useState, type ReactNode, type TouchEvent } from "react"
import {
  ChevronDown,
  Eye,
  Folder,
  Settings,
  Upload,
} from "lucide-react"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Card } from "@/components/ui/card"
import { Separator } from "@/components/ui/separator"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { cn } from "@/lib/utils"
import BottomControlBar from "@/components/admin/BottomControlBar"

type Status = "keeper" | "ng" | "hold" | "recording"

interface Line {
  speaker: string
  text: string
}

interface Take {
  id: string
  scene: number
  shot: number
  no: number
  status: Status
  lines: Line[]
}

const HISTORY_TAKES: Take[] = [
  {
    id: "t1",
    scene: 3,
    shot: 2,
    no: 1,
    status: "keeper",
    lines: [
      { speaker: "SZA", text: "你昨天为什么没有告诉我真相。" },
      { speaker: "YY", text: "因为我不想让你再卷进来。" },
    ],
  },
  {
    id: "t2",
    scene: 3,
    shot: 2,
    no: 2,
    status: "ng",
    lines: [
      { speaker: "SZA", text: "你昨天为什么没告诉我真相？" },
      { speaker: "YY", text: "因为我..." },
    ],
  },
  {
    id: "t3",
    scene: 3,
    shot: 2,
    no: 3,
    status: "hold",
    lines: [
      { speaker: "SZA", text: "你昨天为什么没有告诉我真相。" },
      { speaker: "YY", text: "因为我不想让你再卷进来。" },
    ],
  },
  {
    id: "t4",
    scene: 3,
    shot: 2,
    no: 4,
    status: "keeper",
    lines: [
      { speaker: "SZA", text: "你昨天为什么没有告诉我真相。" },
      { speaker: "YY", text: "因为我不想让你再卷进来。" },
      { speaker: "SZA", text: "那你打算什么时候告诉我。" },
    ],
  },
]

const CURRENT_TAKE: Take = {
  id: "t5",
  scene: 3,
  shot: 2,
  no: 5,
  status: "recording",
  lines: [{ speaker: "SZA", text: "你昨天为什么没有告诉我真相。" }],
}

const CURRENT_PARTIAL = "因为我担心你会"

const INPUT_DEVICE = "MacBook Microphone"
const INPUT_CHANNELS = 2

const LLM_STATES = [
  { key: "idle", detail: "Idle", tone: "ok" as const },
  { key: "l1", detail: "L1", tone: "warn" as const },
  { key: "l2", detail: "L2", tone: "warn" as const },
  { key: "l3", detail: "L3", tone: "warn" as const },
  { key: "voice", detail: "Voice", tone: "warn" as const },
  { key: "photo", detail: "Photo", tone: "warn" as const },
  { key: "script", detail: "Script", tone: "warn" as const },
]

const LLM_FEEDBACK = [
  { kind: "summary", text: "T4 表演完整，台词与剧本一致。本场建议 keeper。" },
  { kind: "diff", text: "L102 改词：『再卷进来』→『卷进来』" },
  { kind: "note", text: "Ch2 提示：T2 二号最后一句漏词，先 hold。" },
]

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

const MOBILE_TABS = ["live", "script", "history", "llm"] as const

export default function AdminHome() {
  const [mobileTab, setMobileTab] = useState("live")
  const [sideTab, setSideTab] = useState("script")
  const [llmIndex, setLlmIndex] = useState(0)
  const goMobileTab = (next: string) => {
    setMobileTab(next)
  }

  // ---- mobile swipe ----
  const touchStart = useRef<{ x: number; y: number } | null>(null)
  const handleTouchStart = (e: TouchEvent<HTMLDivElement>) => {
    const t = e.touches[0]
    touchStart.current = { x: t.clientX, y: t.clientY }
  }
  const handleTouchEnd = (e: TouchEvent<HTMLDivElement>) => {
    if (!touchStart.current) return
    const start = touchStart.current
    touchStart.current = null
    const t = e.changedTouches[0]
    const dx = start.x - t.clientX
    const dy = start.y - t.clientY
    const minSwipe = 56 // px
    // 只处理水平滑动，且水平位移大于垂直位移（避免与垂直滚动冲突）
    if (Math.abs(dx) < minSwipe || Math.abs(dy) > Math.abs(dx)) return

    const idx = MOBILE_TABS.indexOf(mobileTab as typeof MOBILE_TABS[number])
    if (dx > 0 && idx < MOBILE_TABS.length - 1) {
      setMobileTab(MOBILE_TABS[idx + 1])
    } else if (dx < 0 && idx > 0) {
      setMobileTab(MOBILE_TABS[idx - 1])
    }
  }

  const mobileIdx = MOBILE_TABS.indexOf(mobileTab as typeof MOBILE_TABS[number])

  return (
    <div className="h-dvh w-screen flex flex-col bg-muted/50 text-foreground overflow-hidden">
      {/* ============ Header ============ */}
      <header className="flex-shrink-0 bg-background">
        {/* line 1: 状态条 + 观察者 */}
        <div className="px-3 sm:px-5 h-11 flex items-center justify-between gap-2 border-b">
          <div className="flex items-center gap-2 min-w-0">
            <Button variant="ghost" size="icon-sm" className="rounded-full text-muted-foreground flex-shrink-0" title="导入已录制文件">
              <Folder className="size-4" />
            </Button>
            <StatusChip label="Input" tone="ok" detail={INPUT_DEVICE}>
              {Array.from({ length: INPUT_CHANNELS }, (_, i) => (
                <LevelMeter key={i} count={5} color={i === 0 ? "bg-emerald-500" : "bg-primary"} />
              ))}
            </StatusChip>
            <StatusChip
              label="LLM"
              tone={LLM_STATES[llmIndex].tone}
              detail={LLM_STATES[llmIndex].detail}
              onClick={() => setLlmIndex((i) => (i + 1) % LLM_STATES.length)}
            />
          </div>

          <div className="flex items-center gap-1 flex-shrink-0">
            <Button variant="ghost" size="sm" className="gap-1.5 text-muted-foreground">
              <Eye />
              <span className="font-mono text-xs">3</span>
            </Button>
            <Button variant="ghost" size="icon-sm" className="rounded-full text-muted-foreground" title="导出">
              <Upload className="size-4" />
            </Button>
            <Button variant="ghost" size="icon-sm" className="text-muted-foreground">
              <Settings />
            </Button>
          </div>
        </div>

      </header>

      {/* ============ Main ============ */}
      <main className="flex-1 min-h-0 p-2 sm:p-3 flex flex-col md:flex-row gap-2 sm:gap-3">
        {/* ---- Mobile：单 Card 内 Tabs 切换 ---- */}
        <Card size="sm" className="md:hidden flex-1 min-h-0 p-0 gap-0 overflow-hidden">
          <Tabs value={mobileTab} onValueChange={goMobileTab} className="flex-1 min-h-0 flex flex-col p-3 pb-0 gap-3">
            <TabsList className="w-full flex-shrink-0">
              <TabsTrigger value="live">Live</TabsTrigger>
              <TabsTrigger value="script">剧本</TabsTrigger>
              <TabsTrigger value="history">History</TabsTrigger>
              <TabsTrigger value="llm">LLM 反馈</TabsTrigger>
            </TabsList>
            <div
              className="flex-1 min-h-0 overflow-hidden touch-pan-y"
              onTouchStart={handleTouchStart}
              onTouchEnd={handleTouchEnd}
            >
              <div
                className="flex h-full transition-transform duration-300 ease-out will-change-transform"
                style={{ transform: `translateX(-${mobileIdx * 100}%)` }}
              >
                <div className="w-full h-full flex-shrink-0 overflow-y-auto px-3 pb-3">
                  <LiveTranscript />
                </div>
                <div className="w-full h-full flex-shrink-0 overflow-y-auto px-3 pb-3">
                  <ScriptPanel />
                </div>
                <div className="w-full h-full flex-shrink-0 overflow-y-auto px-3 pb-3">
                  <HistoryTakes />
                </div>
                <div className="w-full h-full flex-shrink-0 overflow-y-auto px-3 pb-3">
                  <LLMFeedback />
                </div>
              </div>
            </div>
          </Tabs>
        </Card>

        {/* ---- Desktop：左 transcript Card ---- */}
        <Card size="sm" className="hidden md:flex flex-1 min-h-0 p-0 gap-0 overflow-hidden">
          <div className="flex-1 min-h-0 overflow-y-auto">
            <LiveTranscript />
          </div>
        </Card>

        {/* ---- Desktop：右 tabs Card ---- */}
        <Card size="sm" className="hidden md:flex w-[420px] flex-col p-0 gap-0 overflow-hidden">
          <Tabs value={sideTab} onValueChange={setSideTab} className="flex-1 min-h-0 flex flex-col p-3 pb-0 gap-3">
            <TabsList className="w-full flex-shrink-0">
              <TabsTrigger value="script">剧本</TabsTrigger>
              <TabsTrigger value="history">History</TabsTrigger>
              <TabsTrigger value="llm">LLM 反馈</TabsTrigger>
            </TabsList>
            <div className="flex-1 min-h-0 overflow-y-auto -mx-3 px-3 pb-3">
              {sideTab === "script" && <ScriptPanel />}
              {sideTab === "history" && <HistoryTakes />}
              {sideTab === "llm" && <LLMFeedback />}
            </div>
          </Tabs>
        </Card>
      </main>

      {/* ============ Bottom ============ */}
      <BottomControlBar />
    </div>
  )
}

/* ============ 子组件 ============ */

function StatusChip({
  label,
  tone,
  detail,
  onClick,
  children,
}: {
  label: string
  tone: "ok" | "warn" | "err"
  detail?: string
  onClick?: () => void
  children?: ReactNode
}) {
  const dotColor =
    tone === "ok" ? "bg-emerald-500" : tone === "warn" ? "bg-primary" : "bg-destructive"
  const className = cn(
    "flex items-center gap-1.5 h-9 px-4 rounded-full bg-muted/70 whitespace-nowrap sm:min-w-[5.5rem]",
    onClick && "cursor-pointer active:scale-95 transition-transform focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
  )
  const content = (
    <>
      <span className={cn("size-1.5 rounded-full flex-shrink-0", dotColor)} />
      <span className="hidden sm:inline text-xs font-medium text-foreground">{label}</span>
      {detail && (
        <span className="text-[10px] font-mono text-muted-foreground">
          {detail}
        </span>
      )}
      {children}
    </>
  )

  if (onClick) {
    return (
      <button type="button" className={className} onClick={onClick} aria-label={`${label} ${detail ?? ""}`.trim()}>
        {content}
      </button>
    )
  }
  return <div className={className}>{content}</div>
}

function LevelMeter({
  count = 5,
  color = "bg-emerald-500",
}: {
  count?: number
  color?: string
}) {
  const [heights, setHeights] = useState<number[]>(() =>
    Array.from({ length: count }, () => Math.random())
  )

  useEffect(() => {
    const id = setInterval(() => {
      setHeights(Array.from({ length: count }, () => Math.random()))
    }, 80)
    return () => clearInterval(id)
  }, [count])

  return (
    <div className="flex items-center gap-[1.5px] h-4">
      {heights.map((h, i) => (
        <span
          key={i}
          className={cn("w-[2px] rounded-full transition-all duration-75", color)}
          style={{ height: `${3 + h * 10}px` }}
        />
      ))}
    </div>
  )
}

function LiveTranscript() {
  return (
    <div className="px-3 sm:px-5 lg:px-8 py-3 lg:py-4 space-y-4 max-w-3xl mx-auto">
      {HISTORY_TAKES.slice(-1).map((take) => (
        <TakeBlock key={take.id} take={take} muted />
      ))}

      <div className="flex items-center gap-3">
        <Separator className="flex-1" />
        <span className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">
          Take {CURRENT_TAKE.no} · 14:31:24
        </span>
        <Separator className="flex-1" />
      </div>

      <TakeBlock take={CURRENT_TAKE} partial={CURRENT_PARTIAL} />
    </div>
  )
}

const SPEAKER_OPTIONS = ["SZA", "YY", "Unknown"]

const SPEAKER_DOT: Record<string, string> = {
  SZA: "bg-primary",
  YY: "bg-secondary-foreground",
  Unknown: "bg-muted-foreground",
}

const SPEAKER_TEXT: Record<string, string> = {
  SZA: "text-primary",
  YY: "text-secondary-foreground",
  Unknown: "text-muted-foreground",
}

function SpeakerLabel({
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

function TakeBlock({
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
            {/* ASR 流式阶段不显示说话人，LLM 处理完成后才分配并显示 speaker */}
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

function ScriptPanel() {
  return (
    <div className="py-4 space-y-3">
      <div className="rounded-3xl bg-muted/50 p-4 space-y-4">
        <div className="flex items-center gap-2 text-xs font-mono text-muted-foreground">
          <span className="px-2 py-0.5 rounded-full bg-background">SCENE 3</span>
          <span>室内</span>
          <span>·</span>
          <span>夜</span>
          <span>·</span>
          <span>客厅</span>
        </div>
        <p className="text-sm leading-relaxed text-foreground">
          SZA 坐在沙发上，YY 站在窗边。
        </p>
        <div className="space-y-2">
          <p className="text-sm leading-relaxed">
            <span className="text-primary font-medium">SZA：</span>
            你昨天为什么没有告诉我真相。
          </p>
          <p className="text-sm leading-relaxed">
            <span className="text-secondary-foreground font-medium">YY：</span>
            因为我不想让你再卷进来。
          </p>
          <p className="text-sm leading-relaxed">
            <span className="text-primary font-medium">SZA：</span>
            那你打算什么时候告诉我。
          </p>
        </div>
      </div>
      <p className="text-xs text-muted-foreground text-center pt-2">
        剧本由制片部门上传，拍摄前锁定
      </p>
    </div>
  )
}

function LLMFeedback() {
  return (
    <div className="py-4 space-y-3">
      {LLM_FEEDBACK.map((item, i) => (
        <div key={i} className="rounded-3xl bg-muted/50 p-4 space-y-2">
          <Badge variant="secondary" className="font-mono uppercase">
            {item.kind}
          </Badge>
          <p className="text-sm leading-relaxed text-foreground">{item.text}</p>
        </div>
      ))}
      <p className="text-xs text-muted-foreground text-center pt-2">
        每次 take 结束后由 L2 / NP / SP Pipeline 推送
      </p>
    </div>
  )
}

function LongPressDropdown({
  trigger,
  children,
}: {
  trigger: ReactNode
  children: ReactNode
}) {
  const [open, setOpen] = useState(false)
  const timerRef = useRef<NodeJS.Timeout | null>(null)
  const [pressing, setPressing] = useState(false)

  const startPress = () => {
    setPressing(true)
    timerRef.current = setTimeout(() => {
      setPressing(false)
      setOpen(true)
    }, 1000)
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
        <button
          type="button"
          onPointerDown={(e) => {
            e.preventDefault()
            startPress()
          }}
          onPointerUp={endPress}
          onPointerLeave={endPress}
          className="relative overflow-hidden inline-flex items-center justify-center gap-0.5 h-7 px-1.5 rounded-full bg-background border border-border/60 shadow-sm active:scale-95 transition-transform select-none"
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
        </button>
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
        {(["keeper", "ng", "hold"] as Status[]).map((s) => (
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

function HistoryTakes() {
  const [overrides, setOverrides] = useState<Record<string, Status>>({})
  const [sceneOverrides, setSceneOverrides] = useState<Record<string, number>>({})
  const [shotOverrides, setShotOverrides] = useState<Record<string, number>>({})
  const [noOverrides, setNoOverrides] = useState<Record<string, number>>({})

  const getStatus = (take: Take) => overrides[take.id] ?? take.status
  const getScene = (take: Take) => sceneOverrides[take.id] ?? take.scene
  const getShot = (take: Take) => shotOverrides[take.id] ?? take.shot
  const getNo = (take: Take) => noOverrides[take.id] ?? take.no

  return (
    <div className="py-4 space-y-2.5">
      {HISTORY_TAKES.map((take) => (
        <div
          key={take.id}
          className="w-full text-left rounded-3xl bg-muted/50 hover:bg-muted p-4 transition-colors space-y-2"
        >
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-1 flex-wrap">
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
            <span className="text-[10px] font-mono text-muted-foreground">14:30</span>
          </div>
          <p className="text-sm text-muted-foreground line-clamp-2">
            {take.lines.map((l) => l.text).join("  ")}
          </p>
        </div>
      ))}
    </div>
  )
}
