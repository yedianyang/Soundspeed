import { useEffect, useRef, useState, type TouchEvent } from "react"
import {
  Eye,
  Folder,
  Settings,
  Upload,
  X,
} from "lucide-react"
import { Button } from "@/components/ui/button"
import { Card } from "@/components/ui/card"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import BottomControlBar from "@/components/admin/BottomControlBar"
import { INPUT_DEVICE, INPUT_CHANNELS } from "@/data/mock"
import { MARK_ORDER } from "@/lib/constants"
import { cn } from "@/lib/utils"
import type { Status } from "@/types/take"
import type { LlmState } from "@/types/api"
import { endTake, pickActiveScene, startTake, useScenes, useTakes } from "@/lib/api"
import { useLiveConnection } from "@/hooks/useLiveConnection"
import { useSessionStore } from "@/store/session"
import { StatusChip, LevelMeter } from "./components/StatusChip"
import { LiveTranscript } from "./components/LiveTranscript"
import { ScriptPanel } from "./components/ScriptPanel"
import { LLMFeedback } from "./components/LLMFeedback"
import { HistoryTakes } from "./components/HistoryTakes"
import SettingsDialog from "@/components/admin/SettingsDialog"

const MOBILE_TABS = ["live", "script", "history", "llm"] as const

// llm.status → header LLM chip 展示（spec §3.5）。
const LLM_CHIP: Record<LlmState, { tone: "ok" | "warn"; detail: string }> = {
  idle: { tone: "ok", detail: "Idle" },
  loading: { tone: "warn", detail: "Loading…" },
  running: { tone: "warn", detail: "L2" },
  downloading: { tone: "warn", detail: "Downloading…" },
}

export default function AdminHome() {
  const [mobileTab, setMobileTab] = useState("live")
  const [sideTab, setSideTab] = useState("script")
  const [settingsOpen, setSettingsOpen] = useState(false)

  // ---- WS 连接（admin-scoped，挂一次）----
  useLiveConnection()

  // ---- scene / take / llm（来自后端 / store）----
  const { data: scenes } = useScenes()
  const activeScene = pickActiveScene(scenes)

  // takes 列表 + seedTakes 桥接挂在 AdminHome（始终挂载），不在 HistoryTakes（桌面端条件挂载）。
  // 否则未打开 History 时 LLMFeedback 读空 Map，且重连时无活跃 observer → invalidate 不 refetch，
  // §3.3 恢复链断开。react-query v5 无 onSuccess，用 effect 桥接。
  const { data: takesData } = useTakes()
  const seedTakes = useSessionStore((s) => s.seedTakes)
  useEffect(() => {
    if (takesData) seedTakes(takesData)
  }, [takesData, seedTakes])

  const currentTake = useSessionStore((s) => s.currentTake)
  const startRecordingLocal = useSessionStore((s) => s.startRecordingLocal)
  const stopRecordingLocal = useSessionStore((s) => s.stopRecordingLocal)
  const llmState = useSessionStore((s) => s.llm.state)

  // ---- recording state (lifted from BottomControlBar) ----
  const [isRecording, setIsRecording] = useState(false)
  const [mark, setMark] = useState<Status>("ng")
  const [elapsed, setElapsed] = useState(0)
  const [recError, setRecError] = useState<string | null>(null)
  const elapsedRef = useRef(elapsed)

  useEffect(() => {
    elapsedRef.current = elapsed
  }, [elapsed])

  useEffect(() => {
    if (!isRecording) return
    const start = Date.now() - elapsedRef.current * 1000
    const id = setInterval(() => {
      setElapsed(Math.floor((Date.now() - start) / 1000))
    }, 250)
    return () => clearInterval(id)
  }, [isRecording])

  const handleToggleRecording = async () => {
    if (isRecording) {
      // 先停本地（UX 立即响应），再通知后端；失败仅记日志，不回滚（take 已开始）。
      setIsRecording(false)
      stopRecordingLocal()
      try {
        await endTake()
      } catch (err) {
        console.error("endTake failed", err)
      }
      return
    }

    // 开始：必须有活跃场次。
    if (!activeScene) {
      setRecError("无活跃场次")
      return
    }
    setRecError(null)
    // 先翻转本地态（recording=true, take_id=null），再 POST：后端在返回 HTTP 响应前就 publish
    // take.changed，WS 帧往往早于 await 解析到达。若等 await 后再置 recording，绑定门会错过这帧、
    // take_id 永不绑定。失败时回滚。
    startRecordingLocal(activeScene.scene_id, null)
    setElapsed(0)
    setIsRecording(true)
    try {
      await startTake(activeScene.scene_id, null)
    } catch (err) {
      console.error("startTake failed", err)
      stopRecordingLocal()
      setIsRecording(false)
      setRecError("开始录制失败")
    }
  }

  const handleCycleMark = () => {
    setMark((prev) => MARK_ORDER[(MARK_ORDER.indexOf(prev) + 1) % MARK_ORDER.length])
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
    const minSwipe = 56
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
        <div className="px-4 h-11 flex items-center justify-between gap-2 border-b">
          <div className="flex items-center gap-2 min-w-0">
            <Button variant="ghost" size="icon-sm" className="rounded-full text-muted-foreground flex-shrink-0" title="导入已录制文件">
              <Folder className="size-4" />
            </Button>
            <StatusChip label="Input" tone="ok" detail={INPUT_DEVICE} className="min-w-0">
              <div className="flex items-center gap-1.5 flex-shrink-0">
                {Array.from({ length: INPUT_CHANNELS }, (_, i) => (
                  <LevelMeter
                    key={i}
                    count={5}
                    color={i === 0 ? "bg-green-500" : "bg-primary"}
                    className={i > 0 ? "hidden sm:flex" : undefined}
                  />
                ))}
              </div>
            </StatusChip>
            <StatusChip
              label="LLM"
              tone={LLM_CHIP[llmState].tone}
              detail={LLM_CHIP[llmState].detail}
              className="flex-shrink-0"
            />
            {/* ---- 状态栏：当前场次 / take / 录制态（真实数据）---- */}
            <div className="hidden sm:flex items-center gap-1.5 h-9 px-3 rounded-full bg-muted/70 flex-shrink-0 font-mono text-[10px] text-muted-foreground">
              <span
                className={cn(
                  "size-1.5 rounded-full flex-shrink-0",
                  isRecording ? "bg-destructive animate-pulse" : "bg-muted-foreground/40"
                )}
              />
              <span className="text-foreground">
                {activeScene ? activeScene.scene_code : "—"}
              </span>
              {currentTake.take_number != null && (
                <span>· T{currentTake.take_number}</span>
              )}
              {currentTake.shot && <span>· {currentTake.shot}</span>}
            </div>
          </div>

          <div className="flex items-center gap-1 flex-shrink-0">
            <Button variant="ghost" size="sm" className="gap-1.5 text-muted-foreground">
              <Eye />
              <span className="font-mono text-xs">3</span>
            </Button>
            <Button variant="ghost" size="icon-sm" className="rounded-full text-muted-foreground" title="导出">
              <Upload className="size-4" />
            </Button>
            <Button
              variant="ghost"
              size="icon-sm"
              className="rounded-full text-muted-foreground"
              title={settingsOpen ? "关闭设置" : "打开设置"}
              onClick={() => setSettingsOpen((prev) => !prev)}
            >
              {settingsOpen ? <X className="size-4" /> : <Settings className="size-4" />}
            </Button>
          </div>
        </div>
      </header>

      {/* ============ Main ============ */}
      <main className="flex-1 min-h-0 p-4 flex flex-col md:flex-row gap-3">
        {/* ---- Mobile：单 Card 内 Tabs 切换 ---- */}
        <Card size="sm" className="md:hidden flex-1 min-h-0 p-0 gap-0 overflow-hidden">
          <Tabs value={mobileTab} onValueChange={setMobileTab} className="flex-1 min-h-0 flex flex-col p-3 pb-0 gap-3">
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
        <Card size="sm" className="hidden md:flex flex-[2] min-h-0 p-0 gap-0 overflow-hidden flex-col">
          <div className="flex-shrink-0 p-3 pb-0">
            <Tabs value="live" className="items-center">
              <TabsList>
                <TabsTrigger value="live" className="min-w-[9rem]">Live</TabsTrigger>
              </TabsList>
            </Tabs>
          </div>
          <div className="flex-1 min-h-0 overflow-y-auto">
            <LiveTranscript />
          </div>
        </Card>

        {/* ---- Desktop：右 tabs Card ---- */}
        <Card size="sm" className="hidden md:flex flex-[3] flex-col p-0 gap-0 overflow-hidden">
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
      <SettingsDialog open={settingsOpen} onOpenChange={setSettingsOpen} />

      <BottomControlBar
        isRecording={isRecording}
        onToggleRecording={handleToggleRecording}
        mark={mark}
        onCycleMark={handleCycleMark}
        elapsed={elapsed}
        recDisabled={!activeScene && !isRecording}
        recHint={recError ?? (!activeScene ? "无活跃场次" : null)}
      />
    </div>
  )
}
