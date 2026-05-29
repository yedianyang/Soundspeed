import { useState, useRef } from "react"
import { Card, CardContent } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  ChevronLeft,
  ChevronRight,
  Upload,
  Camera,
  RotateCcw,
  Check,
} from "lucide-react"

// ---- 数据模型 ----

interface Scene {
  id: string
  location: string
  time: string
  setting: string
  lines: ScriptLine[]
}

type ScriptLine =
  | { type: "action"; text: string }
  | { type: "dialogue"; speaker: string; text: string }

// ---- Mock 数据 ----

const MOCK_SCENES: Scene[] = [
  {
    id: "1",
    location: "室外",
    time: "日",
    setting: "街道",
    lines: [
      { type: "action", text: "清晨，薄雾笼罩着老城区。" },
      { type: "dialogue", speaker: "张三", text: "今天天气不错。" },
      { type: "dialogue", speaker: "李四", text: "是啊，适合出门走走。" },
    ],
  },
  {
    id: "2",
    location: "室内",
    time: "日",
    setting: "咖啡馆",
    lines: [
      { type: "action", text: "两人走进街角的咖啡馆，找了个靠窗的位置坐下。" },
      { type: "dialogue", speaker: "张三", text: "你昨天为什么没有告诉我真相。" },
      { type: "dialogue", speaker: "李四", text: "因为我不想让你再卷进来。" },
      { type: "dialogue", speaker: "张三", text: "那你打算什么时候告诉我。" },
    ],
  },
  {
    id: "3",
    location: "室内",
    time: "夜",
    setting: "客厅",
    lines: [
      { type: "action", text: "夜晚，客厅只亮着一盏落地灯。" },
      { type: "dialogue", speaker: "SZA", text: "你昨天为什么没有告诉我真相。" },
      { type: "dialogue", speaker: "YY", text: "因为我不想让你再卷进来。" },
      { type: "dialogue", speaker: "SZA", text: "那你打算什么时候告诉我。" },
      { type: "dialogue", speaker: "YY", text: "等这一切结束吧。" },
    ],
  },
  {
    id: "4",
    location: "室外",
    time: "夜",
    setting: "天台",
    lines: [
      { type: "action", text: "天台，城市灯火在脚下铺开。" },
      { type: "dialogue", speaker: "张三", text: "我们不能再这样下去了。" },
      { type: "dialogue", speaker: "李四", text: "我知道。" },
    ],
  },
]

const MOCK_OCR: Scene = {
  id: "OCR",
  location: "室内",
  time: "夜",
  setting: "拍摄现场",
  lines: [
    { type: "action", text: "【OCR 识别结果，请核对】" },
    { type: "dialogue", speaker: "角色A", text: "这是从照片识别出的台词。" },
    { type: "dialogue", speaker: "角色B", text: "需要人工校对后确认。" },
  ],
}

// ---- 组件 ----

export function ScriptPanel() {
  const [scenes, setScenes] = useState<Scene[]>(MOCK_SCENES)
  const [currentIndex] = useState(1)
  const [viewIndex, setViewIndex] = useState(1)
  const [jumpInput, setJumpInput] = useState(false)
  const [jumpValue, setJumpValue] = useState("")
  const jumpRef = useRef<HTMLInputElement>(null)
  const [ocrScene, setOcrScene] = useState<Scene | null>(null)

  const fileInputRef = useRef<HTMLInputElement>(null)
  const cameraInputRef = useRef<HTMLInputElement>(null)

  const viewScene = ocrScene ?? scenes[viewIndex]
  const isOcr = ocrScene !== null

  // ---- 导航 ----
  const goPrev = () => setViewIndex((i) => Math.max(0, i - 1))
  const goNext = () => setViewIndex((i) => Math.min(scenes.length - 1, i + 1))
  const goCurrent = () => {
    setOcrScene(null)
    setViewIndex(currentIndex)
  }

  const startJump = () => {
    setJumpValue(String(viewScene?.id ?? ""))
    setJumpInput(true)
  }

  const commitJump = () => {
    const target = jumpValue.trim()
    if (!target) {
      setJumpInput(false)
      return
    }
    const idx = scenes.findIndex((s) => s.id === target)
    if (idx >= 0) {
      setViewIndex(idx)
      setOcrScene(null)
    }
    setJumpInput(false)
  }

  // ---- 上传 / OCR ----
  const triggerUpload = () => fileInputRef.current?.click()
  const triggerCamera = () => cameraInputRef.current?.click()

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    if (file.type.startsWith("image/")) {
      setOcrScene(MOCK_OCR)
    } else {
      setOcrScene(null)
      setScenes(MOCK_SCENES)
      setViewIndex(0)
    }
    e.target.value = ""
  }

  // ---- 渲染台词 ----
  const renderLines = (scene: Scene) => (
    <div className="space-y-3">
      {scene.lines.map((line, i) =>
        line.type === "action" ? (
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
          <Button variant="ghost" size="icon-sm" onClick={goPrev} disabled={viewIndex === 0}>
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
                SCENE {viewScene?.id}
              </span>
            )}
            {viewIndex !== currentIndex && (
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
            {viewIndex === currentIndex && (
              <span className="text-[10px] bg-primary/10 text-primary px-1.5 py-0.5 rounded-full">
                当前场
              </span>
            )}
          </div>
          <Button
            variant="ghost"
            size="icon-sm"
            onClick={goNext}
            disabled={viewIndex === scenes.length - 1}
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
        <Card className="rounded-4xl bg-muted/50 shadow-none ring-0 py-0">
          <CardContent className="p-4 space-y-4">
            {/* 场次头 */}
            <div className="flex items-center gap-2 text-xs font-mono text-muted-foreground">
              <span className="px-2 py-0.5 rounded-full bg-background">
                SCENE {viewScene?.id}
              </span>
              <span>{viewScene?.location}</span>
              <span>·</span>
              <span>{viewScene?.time}</span>
              <span>·</span>
              <span>{viewScene?.setting}</span>
            </div>

            {/* 台词 */}
            {viewScene && renderLines(viewScene)}
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
