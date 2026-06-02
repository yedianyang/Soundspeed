import { useState, useRef } from "react"
import { Card, CardContent } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { miniPill, mutedCard } from "@/lib/styles"
import { pickActiveScene, useSceneScript, useScenes } from "@/lib/api"
import type { SceneDTO } from "@/types/api"
import {
  ChevronLeft,
  ChevronRight,
  Upload,
  Camera,
  RotateCcw,
  Check,
} from "lucide-react"

// ---- OCR 本地 mock（决策点 2：上传/拍照不接后端，仍走本地预览）----

type OcrLine =
  | { type: "action"; text: string }
  | { type: "dialogue"; speaker: string; text: string }

interface OcrScene {
  location: string
  time: string
  setting: string
  lines: OcrLine[]
}

const MOCK_OCR: OcrScene = {
  location: "室内",
  time: "夜",
  setting: "拍摄现场",
  lines: [
    { type: "action", text: "【OCR 识别结果，请核对】" },
    { type: "dialogue", speaker: "角色A", text: "这是从照片识别出的台词。" },
    { type: "dialogue", speaker: "角色B", text: "需要人工校对后确认。" },
  ],
}

// 归一化台词行（真库行与 OCR 行共用一种渲染样式）。speaker=null → 动作描述。
interface NormLine {
  speaker: string | null
  text: string
}

// ---- 组件 ----

export function ScriptPanel() {
  const { data: scenes } = useScenes()

  // viewIndex=null 表示「跟随当前场」（活跃场次随后端变化时自动跟）。
  const [viewIndex, setViewIndex] = useState<number | null>(null)
  const [jumpInput, setJumpInput] = useState(false)
  const [jumpValue, setJumpValue] = useState("")
  const jumpRef = useRef<HTMLInputElement>(null)
  const [ocrScene, setOcrScene] = useState<OcrScene | null>(null)

  const fileInputRef = useRef<HTMLInputElement>(null)
  const cameraInputRef = useRef<HTMLInputElement>(null)

  const isOcr = ocrScene !== null

  // 当前场 = is_active 那条（pickActiveScene 退回第一条）。每次渲染从最新 scenes 派生。
  const active = pickActiveScene(scenes)
  const currentIndex =
    scenes && active ? scenes.findIndex((s) => s.scene_id === active.scene_id) : 0
  // 有效查看索引：null → 当前场。
  const view = viewIndex ?? currentIndex
  const viewScene: SceneDTO | undefined = scenes?.[view]

  // OCR 模式不拉剧本（OCR 自带行）；真库模式按 scene_id 拉，hook 内部用 enabled 门控。
  const scriptSceneId = isOcr ? null : viewScene?.scene_id ?? null
  const {
    data: script,
    isLoading: scriptLoading,
    isError: scriptError,
  } = useSceneScript(scriptSceneId)

  // ---- 导航（都从有效 view 算，避免异步 stale）----
  const goPrev = () => {
    if (!scenes) return
    setViewIndex(Math.max(0, view - 1))
  }
  const goNext = () => {
    if (!scenes) return
    setViewIndex(Math.min(scenes.length - 1, view + 1))
  }
  const goCurrent = () => {
    setOcrScene(null)
    setViewIndex(null) // 回到「跟随当前场」
  }

  const startJump = () => {
    setJumpValue(viewScene?.scene_code ?? "")
    setJumpInput(true)
  }

  const commitJump = () => {
    const target = jumpValue.trim()
    if (!target || !scenes) {
      setJumpInput(false)
      return
    }
    const idx = scenes.findIndex((s) => s.scene_code === target)
    if (idx >= 0) {
      setViewIndex(idx)
      setOcrScene(null)
    }
    setJumpInput(false)
  }

  // ---- 上传 / OCR（保持本地 mock，不接后端）----
  const triggerUpload = () => fileInputRef.current?.click()
  const triggerCamera = () => cameraInputRef.current?.click()

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    // 任意文件都进 OCR 本地预览（决策点 2：导入/OCR 无后端端点）。
    setOcrScene(MOCK_OCR)
    e.target.value = ""
  }

  // ---- 渲染台词（归一化行）----
  const renderLines = (lines: NormLine[]) => (
    <div className="space-y-3">
      {lines.map((line, i) =>
        line.speaker == null ? (
          <p key={i} className="text-sm leading-relaxed text-foreground">
            {line.text}
          </p>
        ) : (
          <p key={i} className="text-sm leading-relaxed">
            <span className="text-primary font-medium">{line.speaker}：</span>
            {line.text}
          </p>
        )
      )}
    </div>
  )

  // 真库剧本 → 归一化行（后端已按 line_no 升序）。
  const scriptLines: NormLine[] =
    script?.lines.map((l) => ({ speaker: l.character, text: l.text })) ?? []
  // OCR mock → 归一化行。
  const ocrLines: NormLine[] =
    ocrScene?.lines.map((l) =>
      l.type === "dialogue" ? { speaker: l.speaker, text: l.text } : { speaker: null, text: l.text }
    ) ?? []

  const headerCode = viewScene?.scene_code

  return (
    <div className="py-4 space-y-3 h-full flex flex-col">
      {/* ========== 工具栏 ========== */}
      <div className="flex items-center justify-between gap-2">
        <span className="text-sm font-medium">剧本</span>
        <div className="flex items-center gap-1">
          <Button
            variant="ghost"
            size="icon-sm"
            title="上传剧本文件"
            onClick={triggerUpload}
          >
            <Upload className="size-4" />
          </Button>
          <Button
            variant="ghost"
            size="icon-sm"
            title="拍照识别"
            onClick={triggerCamera}
          >
            <Camera className="size-4" />
          </Button>
        </div>
      </div>

      {/* 隐藏的文件输入 */}
      <input
        ref={fileInputRef}
        type="file"
        accept=".txt,.doc,.docx,.pdf"
        className="hidden"
        onChange={handleFileChange}
      />
      <input
        ref={cameraInputRef}
        type="file"
        accept="image/*"
        capture="environment"
        className="hidden"
        onChange={handleFileChange}
      />

      {/* ========== 场次导航 ========== */}
      {!isOcr && (
        <div className="flex items-center justify-between">
          <Button variant="ghost" size="icon-sm" onClick={goPrev} disabled={view === 0}>
            <ChevronLeft className="size-4" />
          </Button>
          <div className="flex items-center gap-1.5 sm:gap-2">
            {jumpInput ? (
              <div className="flex items-center gap-1.5">
                <Input
                  ref={jumpRef}
                  value={jumpValue}
                  onChange={(e) => setJumpValue(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") commitJump()
                    if (e.key === "Escape") setJumpInput(false)
                  }}
                  className="h-6 w-20 text-center text-xs font-mono px-1 py-0"
                  autoFocus
                />
                <Button
                  variant="ghost"
                  size="icon-sm"
                  className="size-6 rounded-full bg-primary/10 hover:bg-primary/20 text-primary p-0"
                  onMouseDown={(e) => e.preventDefault()}
                  onClick={commitJump}
                >
                  <Check className="size-3.5" />
                </Button>
              </div>
            ) : (
              <span
                className="text-xs font-mono text-muted-foreground cursor-pointer select-none"
                onDoubleClick={startJump}
                title="双击输入场次号跳转"
              >
                SCENE {headerCode ?? "—"}
              </span>
            )}
            {view !== currentIndex && (
              <Button
                variant="ghost"
                size="icon-sm"
                className="size-6 rounded-full sm:h-6 sm:w-auto sm:px-2 sm:text-xs sm:gap-1"
                onClick={goCurrent}
                title="返回当前场"
              >
                <RotateCcw className="size-3" />
                <span className="hidden sm:inline">返回当前场</span>
              </Button>
            )}
            {view === currentIndex && (
              <span className={miniPill("primary", "text-[10px]")}>
                当前场
              </span>
            )}
          </div>
          <Button
            variant="ghost"
            size="icon-sm"
            onClick={goNext}
            disabled={!scenes || view >= scenes.length - 1}
          >
            <ChevronRight className="size-4" />
          </Button>
        </div>
      )}

      {/* OCR 场次头 */}
      {isOcr && (
        <div className="flex items-center justify-between">
          <span className="text-xs font-mono text-muted-foreground">OCR 识别</span>
          <Button variant="ghost" size="sm" className="h-6 text-xs gap-1" onClick={goCurrent}>
            <RotateCcw className="size-3" />
            返回剧本
          </Button>
        </div>
      )}

      {/* ========== 剧本内容 ========== */}
      <div className="flex-1 min-h-0 overflow-y-auto">
        <Card className={mutedCard}>
          <CardContent className="p-4 space-y-4">
            {isOcr && ocrScene ? (
              <>
                {/* OCR 场次头 */}
                <div className="flex items-center gap-2 text-xs font-mono text-muted-foreground">
                  <span className={miniPill("neutral")}>OCR</span>
                  <span>{ocrScene.location}</span>
                  <span>·</span>
                  <span>{ocrScene.time}</span>
                  <span>·</span>
                  <span>{ocrScene.setting}</span>
                </div>
                {renderLines(ocrLines)}
              </>
            ) : (
              <>
                {/* 真库场次头 */}
                <div className="flex flex-col gap-1.5">
                  <div className="flex flex-wrap items-center gap-1.5 text-xs font-mono text-muted-foreground">
                    <span className={miniPill("neutral")}>
                      SCENE {headerCode ?? "—"}
                    </span>
                    {[viewScene?.int_ext, viewScene?.time_of_day, viewScene?.location]
                      .map((v) => v?.trim())
                      .filter((v): v is string => !!v)
                      .map((v) => (
                        <span key={v} className={miniPill("primary")}>{v}</span>
                      ))}
                  </div>
                  {viewScene?.description && (
                    <p className="text-xs text-muted-foreground">{viewScene.description}</p>
                  )}
                </div>

                {/* 台词 / 加载 / 空 / 错误态 */}
                {!viewScene ? (
                  <p className="text-sm text-muted-foreground">
                    {scenes ? "无场次" : "加载中…"}
                  </p>
                ) : scriptLoading ? (
                  <p className="text-sm text-muted-foreground">加载中…</p>
                ) : scriptError ? (
                  <p className="text-sm text-destructive">剧本加载失败，请重试。</p>
                ) : scriptLines.length === 0 ? (
                  <p className="text-sm text-muted-foreground">该场暂无剧本</p>
                ) : (
                  renderLines(scriptLines)
                )}
              </>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
