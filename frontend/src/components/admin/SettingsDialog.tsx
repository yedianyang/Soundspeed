import { useState } from "react"
import { useQueryClient } from "@tanstack/react-query"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import { Button } from "@/components/ui/button"
import { Separator } from "@/components/ui/separator"
import { miniPill } from "@/lib/styles"
import { API_BASE, LS_TOKEN_KEY } from "@/lib/config"
import { useSessionStore } from "@/store/session"
import {
  endTake,
  injectDebugAsr,
  injectDebugScript,
  pickActiveScene,
  startTake,
  useScenes,
  type DebugAsrSeg,
  type DebugScriptLine,
} from "@/lib/api"
import { Check, ChevronDown, ChevronRight } from "lucide-react"
import { Plus, Trash2, User, AudioLines, Link2, Server, FlaskConical } from "lucide-react"

const DEV = import.meta.env.DEV

const DEBUG_ASR_PLACEHOLDER = `{
  "speakers": ["SPEAKER_00", "SPEAKER_01"],
  "turns": [
    { "start": 2.88, "end": 5.63, "speaker": "SPEAKER_00", "text": "你昨天为什么没告诉我真相。" },
    { "start": 5.70, "end": 8.10, "speaker": "SPEAKER_01", "text": "因为我不想让你再卷进来。" }
  ]
}

// 也接受纯段数组：[{ "ch": 1, "speaker": "...", "text": "...", "is_partial": false }]`

// 容错解析，三种输入形状（按优先级）：
// 1. diarize 导出 {turns:[{speaker,text,start,end,...}]} → 每条 turn 映射为 ch1 final 段（单声道）。
// 2. 顶层数组 [{ch?=1, speaker?=null, text, is_partial?=false}]。
// 3. {segments:[...]} 包裹，同 (2)。
// 每项需非空 text，缺 text 的项跳过（不报错）。speaker 任意字符串（speakerColor 哈希分色）。
function parseDebugAsr(raw: string): { segs: DebugAsrSeg[]; error: string | null } {
  let data: unknown
  try {
    data = JSON.parse(raw)
  } catch {
    return { segs: [], error: "JSON 解析失败：检查格式（{turns:[...]} 或段数组）" }
  }

  // 形状 1：diarize {turns:[...]}。turns 是 final，单声道 = ch1；忽略 start/end，按数组顺序注入
  //（后端 /debug/asr 盖单调 server frame，顺序保留）。
  if (data && typeof data === "object" && Array.isArray((data as { turns?: unknown }).turns)) {
    const turns = (data as { turns: unknown[] }).turns
    const segs: DebugAsrSeg[] = []
    for (const t of turns) {
      if (!t || typeof t !== "object") continue
      const o = t as Record<string, unknown>
      if (typeof o.text !== "string" || !o.text.trim()) continue
      segs.push({
        ch: 1,
        text: o.text,
        speaker: typeof o.speaker === "string" ? o.speaker : null,
        is_partial: false,
      })
    }
    if (segs.length === 0) return { segs: [], error: "turns 里没有带 text 的有效条目" }
    return { segs, error: null }
  }

  // 形状 2 / 3：顶层数组 或 {segments:[...]}。
  const arr = Array.isArray(data)
    ? data
    : data && typeof data === "object" && Array.isArray((data as { segments?: unknown }).segments)
      ? (data as { segments: unknown[] }).segments
      : null
  if (!arr) return { segs: [], error: "需要 {turns:[...]}、段数组、或 {segments:[...]}" }
  if (arr.length === 0) return { segs: [], error: "段数组为空" }

  const segs: DebugAsrSeg[] = []
  for (const item of arr) {
    if (!item || typeof item !== "object") continue
    const o = item as Record<string, unknown>
    if (typeof o.text !== "string" || !o.text.trim()) continue
    segs.push({
      ch: o.ch === 2 ? 2 : 1,
      text: o.text,
      speaker: typeof o.speaker === "string" ? o.speaker : null,
      is_partial: o.is_partial === true,
    })
  }
  if (segs.length === 0) return { segs: [], error: "没有带 text 的有效段" }
  return { segs, error: null }
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms))

const DEBUG_SCRIPT_PLACEHOLDER = `SZA：你昨天为什么没告诉我真相。
YY：因为我不想让你再卷进来。
那你打算什么时候告诉我。

// 也接受 JSON：[{ "character": "SZA", "text": "..." }] 或 {lines:[...]}`

// 剧本文本解析，两种输入形状（按优先级）：
// 1. JSON 数组 [{character?, text}] 或 {lines:[...]}（容错，同 ASR parser）。
// 2. 纯文本，每行一句：按首个全角「：」或半角「:」切分 → {character: 冒号前, text: 冒号后}；
//    无冒号的行 → {character: null, text: 整行}。空行跳过。
// 每行需非空 text。
function parseDebugScript(raw: string): { lines: DebugScriptLine[]; error: string | null } {
  const trimmed = raw.trim()
  if (!trimmed) return { lines: [], error: "剧本为空" }

  // 先试 JSON（数组 或 {lines:[...]}）。失败则当纯文本。
  if (trimmed.startsWith("[") || trimmed.startsWith("{")) {
    let data: unknown = null
    try {
      data = JSON.parse(trimmed)
    } catch {
      // 解析失败 → data 保持 null，退回纯文本路径。
    }
    if (data) {
      const arr = Array.isArray(data)
        ? data
        : typeof data === "object" && Array.isArray((data as { lines?: unknown }).lines)
          ? (data as { lines: unknown[] }).lines
          : null
      if (arr) {
        const lines: DebugScriptLine[] = []
        for (const item of arr) {
          if (!item || typeof item !== "object") continue
          const o = item as Record<string, unknown>
          if (typeof o.text !== "string" || !o.text.trim()) continue
          lines.push({
            character: typeof o.character === "string" ? o.character : null,
            text: o.text.trim(),
          })
        }
        if (lines.length === 0) return { lines: [], error: "JSON 里没有带 text 的有效行" }
        return { lines, error: null }
      }
      // 是 JSON 但形状不对（既非数组也非 {lines:[]}）→ 报错，不静默当纯文本。
      return { lines: [], error: "JSON 需为数组或 {lines:[...]}" }
    }
  }

  // 纯文本：每行按首个 ：/ : 切分。
  const lines: DebugScriptLine[] = []
  for (const row of trimmed.split(/\r?\n/)) {
    const line = row.trim()
    if (!line) continue
    const m = /[：:]/.exec(line)
    if (m) {
      const character = line.slice(0, m.index).trim()
      const text = line.slice(m.index + 1).trim()
      if (!text) continue
      lines.push({ character: character || null, text })
    } else {
      lines.push({ character: null, text: line })
    }
  }
  if (lines.length === 0) return { lines: [], error: "没有有效台词行" }
  return { lines, error: null }
}

// ---- 数据模型 ----

interface Speaker {
  id: string // diarize 输出的 speaker_num，如 "speaker_0"
}

interface ActorBinding {
  id: string
  speakerId: string
  actorName: string
}

interface SettingsDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
}

// ---- 组件 ----

export default function SettingsDialog({ open, onOpenChange }: SettingsDialogProps) {
  // 说话人：由 diarize 系统识别
  const [speakers, setSpeakers] = useState<Speaker[]>([
    { id: "speaker_0" },
    { id: "speaker_1" },
    { id: "speaker_2" },
  ])

  // 演员列表
  const [actors, setActors] = useState<string[]>(["张三", "李四"])

  // 演员绑定：speaker num -> 演员名字
  const [bindings, setBindings] = useState<ActorBinding[]>([
    { id: "b1", speakerId: "speaker_0", actorName: "张三" },
  ])

  // 左侧选中的演员
  const [selectedActor, setSelectedActor] = useState<string>("张三")

  // 右侧多选中的说话人
  const [selectedSpeakers, setSelectedSpeakers] = useState<Set<string>>(new Set())

  // 新增演员输入
  const [newActorName, setNewActorName] = useState("")

  // 合并说话人
  const [mergeSource, setMergeSource] = useState("")
  const [mergeTarget, setMergeTarget] = useState("")

  // 服务器连接：admin token（localStorage 持久化 + 同步 store 触发重连）
  const storeToken = useSessionStore((s) => s.token)
  const connection = useSessionStore((s) => s.connection)
  const setToken = useSessionStore((s) => s.setToken)
  const [tokenInput, setTokenInput] = useState<string>(storeToken ?? "")
  const queryClient = useQueryClient()

  const handleSaveToken = () => {
    const v = tokenInput.trim()
    if (v) {
      localStorage.setItem(LS_TOKEN_KEY, v)
    } else {
      localStorage.removeItem(LS_TOKEN_KEY)
    }
    // 写 localStorage 不会通知 useLiveConnection；更新 store.token 才会翻转连接态并触发重连。
    setToken(v || null)
    // 首次填 token 时 scenes/takes 此前因 401 settle 在 error，key 没变不会自动 refetch；
    // 显式 invalidate 让它们带新 token 重取（否则 REC 一直停在「无活跃场次」直到硬刷新）。
    if (v) {
      queryClient.invalidateQueries({ queryKey: ["scenes"] })
      queryClient.invalidateQueries({ queryKey: ["takes"] })
    }
  }

  // ---- dev 测试面板：paste ASR JSON → 一键跑完整 take ----
  const { data: scenes } = useScenes()
  const activeScene = pickActiveScene(scenes)
  const startRecordingLocal = useSessionStore((s) => s.startRecordingLocal)
  const stopRecordingLocal = useSessionStore((s) => s.stopRecordingLocal)
  const [asrJson, setAsrJson] = useState("")
  const [running, setRunning] = useState(false)
  const [runStatus, setRunStatus] = useState<{ kind: "info" | "error" | "done"; msg: string } | null>(null)
  const [scriptText, setScriptText] = useState("")
  const [scriptStatus, setScriptStatus] = useState<{ kind: "info" | "error" | "done"; msg: string } | null>(null)

  const handleInjectScript = async () => {
    if (running) return
    const { lines, error } = parseDebugScript(scriptText)
    if (error) {
      setScriptStatus({ kind: "error", msg: error })
      return
    }
    if (!activeScene) {
      setScriptStatus({ kind: "error", msg: "无活跃场次，无法注入" })
      return
    }
    setRunning(true)
    try {
      // sceneId 省略 → 后端用活跃场次；这里显式传 activeScene 与 UI 显示一致。
      const res = await injectDebugScript(lines, activeScene.scene_id)
      setScriptStatus({
        kind: "done",
        msg: `注入 ${res.line_count} 行剧本到 Scene ${res.scene_id}`,
      })
    } catch (err) {
      console.error("inject script failed", err)
      setScriptStatus({ kind: "error", msg: "请求失败（看 console / 是否 SOUNDSPEED_DEV=1）" })
    } finally {
      setRunning(false)
    }
  }

  const handleRunFullTake = async () => {
    if (running) return
    const { segs, error } = parseDebugAsr(asrJson)
    if (error) {
      setRunStatus({ kind: "error", msg: error })
      return
    }
    if (!activeScene) {
      setRunStatus({ kind: "error", msg: "无活跃场次，无法开始" })
      return
    }
    setRunning(true)
    // 必须 startRecordingLocal：清 segments、置 recording=true / take_id=null，使 take.changed 绑定门
    // 重绑到新 take。否则 currentTake 停在上一次 REC 的 take_id，applyAsr 的跨-take 守卫会把本次注入帧
    // 全丢（transcript 空），且 take_number 不显示。recording=true 后无论后端是否在 /debug/asr 盖
    // take_id 都能正确绑定。
    startRecordingLocal(activeScene.scene_id, null)
    try {
      await startTake(activeScene.scene_id, null)
      setRunStatus({ kind: "info", msg: `注入 ${segs.length} 段…` })
      for (const seg of segs) {
        await injectDebugAsr(seg)
        // stagger，让 transcript 可见地滚动（partial 替换 / final 落定）。
        await sleep(150)
      }
      await endTake()
      setRunStatus({ kind: "done", msg: "完成 — 看 History / LLM 反馈 tab" })
      queryClient.invalidateQueries({ queryKey: ["takes"] })
    } catch (err) {
      console.error("run full take failed", err)
      setRunStatus({ kind: "error", msg: "请求失败（看 console / 是否 SOUNDSPEED_DEV=1）" })
    } finally {
      stopRecordingLocal()
      setRunning(false)
    }
  }

  const toggleSpeakerSelection = (speakerId: string) => {
    setSelectedSpeakers((prev) => {
      const next = new Set(prev)
      if (next.has(speakerId)) {
        next.delete(speakerId)
      } else {
        next.add(speakerId)
      }
      return next
    })
  }

  const handleBindSelected = () => {
    if (!selectedActor || selectedSpeakers.size === 0) return
    setBindings((prev) => {
      // 先移除这些 speaker 的旧绑定
      const filtered = prev.filter((b) => !selectedSpeakers.has(b.speakerId))
      // 添加新绑定
      const newBindings = Array.from(selectedSpeakers).map((speakerId) => ({
        id: crypto.randomUUID(),
        speakerId,
        actorName: selectedActor,
      }))
      return [...filtered, ...newBindings]
    })
    setSelectedSpeakers(new Set())
  }

  const handleAddActor = () => {
    if (!newActorName.trim()) return
    const name = newActorName.trim()
    if (actors.includes(name)) return
    setActors((prev) => [...prev, name])
    setNewActorName("")
    setSelectedActor(name)
  }

  const handleRemoveActor = (name: string) => {
    setActors((prev) => prev.filter((a) => a !== name))
    // 解绑该演员的所有 speaker
    setBindings((prev) => prev.filter((b) => b.actorName !== name))
    if (selectedActor === name) {
      setSelectedActor("")
    }
  }

  const handleRemoveSpeaker = (id: string) => {
    setSpeakers((prev) => prev.filter((s) => s.id !== id))
    setBindings((prev) => prev.filter((b) => b.speakerId !== id))
    setSelectedSpeakers((prev) => {
      const next = new Set(prev)
      next.delete(id)
      return next
    })
  }

  const handleMerge = () => {
    if (!mergeSource || !mergeTarget || mergeSource === mergeTarget) return
    setSpeakers((prev) => prev.filter((s) => s.id !== mergeSource))
    setBindings((prev) =>
      prev.map((b) =>
        b.speakerId === mergeSource ? { ...b, speakerId: mergeTarget } : b
      )
    )
    setSelectedSpeakers((prev) => {
      const next = new Set(prev)
      next.delete(mergeSource)
      return next
    })
    setMergeSource("")
    setMergeTarget("")
  }

  const getBoundActor = (speakerId: string) => {
    return bindings.find((b) => b.speakerId === speakerId)?.actorName
  }

  const getBoundSpeakers = (actorName: string) => {
    return bindings
      .filter((b) => b.actorName === actorName)
      .map((b) => b.speakerId)
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>设置</DialogTitle>
          <DialogDescription>
            配置音频输入、演员与说话人绑定
          </DialogDescription>
        </DialogHeader>

        <div className="grid gap-5 py-2 max-h-[70vh] overflow-y-auto pr-1">
          {/* ========== 服务器连接 ========== */}
          <div className="grid gap-2">
            <div className="flex items-center gap-2">
              <Server className="size-4 text-primary" />
              <span className="text-sm font-medium">服务器连接</span>
              <span
                className={`ml-auto ${miniPill(
                  connection === "open" ? "primary" : "neutral",
                  "text-[10px]"
                )}`}
              >
                {connection === "open"
                  ? "已连接"
                  : connection === "connecting"
                    ? "连接中…"
                    : connection === "no-token"
                      ? "未鉴权"
                      : "已断开"}
              </span>
            </div>
            <div className="grid gap-1">
              <span className="text-xs text-muted-foreground">API 地址</span>
              <span className="font-mono text-xs text-foreground break-all">{API_BASE}</span>
            </div>
            <div className="grid gap-1">
              <span className="text-xs text-muted-foreground">Admin Token</span>
              <div className="flex gap-2">
                <Input
                  type="password"
                  placeholder="粘贴后端启动时打印的 admin token"
                  value={tokenInput}
                  onChange={(e) => setTokenInput(e.target.value)}
                  onBlur={handleSaveToken}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") handleSaveToken()
                  }}
                  className="flex-1"
                />
                <Button variant="secondary" size="sm" onClick={handleSaveToken}>
                  保存
                </Button>
              </div>
              {connection === "no-token" && (
                <span className="text-xs text-destructive">
                  未填写 admin token，连接未鉴权。
                </span>
              )}
            </div>
          </div>

          <Separator />

          {/* ========== 开发 / 测试（仅 dev 构建） ========== */}
          {DEV && (
            <>
              <div className="grid gap-2">
                <div className="flex items-center gap-2">
                  <FlaskConical className="size-4 text-primary" />
                  <span className="text-sm font-medium">开发 / 测试</span>
                  <span className={`ml-auto ${miniPill("neutral", "text-[10px]")}`}>
                    {activeScene ? `场次 ${activeScene.scene_code}` : "无活跃场次"}
                  </span>
                </div>

                {/* ---- 剧本注入（先做，持久化） ---- */}
                <span className="text-xs font-medium text-foreground">
                  剧本（可选，注入后 L2 才能产出真实 diff）
                </span>
                <Textarea
                  value={scriptText}
                  onChange={(e) => setScriptText(e.target.value)}
                  placeholder={DEBUG_SCRIPT_PLACEHOLDER}
                  rows={5}
                  className="font-mono text-xs"
                  disabled={running}
                />
                <div className="flex items-center gap-2">
                  <Button
                    variant="secondary"
                    size="sm"
                    className="gap-1.5"
                    disabled={running || !activeScene || !scriptText.trim()}
                    onClick={handleInjectScript}
                  >
                    注入剧本到当前场次
                  </Button>
                  {scriptStatus && (
                    <span
                      className={
                        scriptStatus.kind === "error"
                          ? "text-xs text-destructive"
                          : scriptStatus.kind === "done"
                            ? "text-xs text-green-600"
                            : "text-xs text-muted-foreground"
                      }
                    >
                      {scriptStatus.msg}
                    </span>
                  )}
                </div>
                <span className="text-[10px] text-muted-foreground/70">
                  先注入剧本，再「一键跑完整 take」，L2 就会按剧本逐行比对（改词/漏词/加词）。
                  剧本会持久化，注入一次可跑多条 take。
                </span>

                <Separator className="my-1" />

                {/* ---- ASR 注入 + 一键跑完整 take ---- */}
                <span className="text-xs text-muted-foreground">
                  粘贴 ASR JSON，一键跑完整 take（start → 逐段注入 → end → L2）。
                </span>
                <Textarea
                  value={asrJson}
                  onChange={(e) => setAsrJson(e.target.value)}
                  placeholder={DEBUG_ASR_PLACEHOLDER}
                  rows={6}
                  className="font-mono text-xs"
                  disabled={running}
                />
                <div className="flex items-center gap-2">
                  <Button
                    variant="default"
                    size="sm"
                    className="gap-1.5"
                    disabled={running || !activeScene || !asrJson.trim()}
                    onClick={handleRunFullTake}
                  >
                    <FlaskConical className="size-4" />
                    {running ? "运行中…" : "一键跑完整 take"}
                  </Button>
                  {runStatus && (
                    <span
                      className={
                        runStatus.kind === "error"
                          ? "text-xs text-destructive"
                          : runStatus.kind === "done"
                            ? "text-xs text-green-600"
                            : "text-xs text-muted-foreground"
                      }
                    >
                      {runStatus.msg}
                    </span>
                  )}
                </div>
                <span className="text-[10px] text-muted-foreground/70">
                  L2 摘要需 Gemma 权重，否则降级（script_diff 为空）。
                </span>
              </div>

              <Separator />
            </>
          )}

          {/* ========== 音频输入 ========== */}
          <div className="grid gap-2">
            <span className="text-sm font-medium">音频输入设备</span>
            <Select defaultValue="default">
              <SelectTrigger className="w-full">
                <SelectValue placeholder="选择输入设备" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="default">系统默认</SelectItem>
                <SelectItem value="mic1">内置麦克风</SelectItem>
                <SelectItem value="usb">USB 音频接口 (Zoom H6)</SelectItem>
                <SelectItem value="bluetooth">蓝牙输入</SelectItem>
              </SelectContent>
            </Select>
          </div>

          <Separator />

          {/* ========== 演员 / 说话人 双栏 ========== */}
          <div className="grid grid-cols-1 sm:grid-cols-[1fr_1.5fr] gap-4">
            {/* ---- 左栏：演员 ---- */}
            <div className="grid gap-3 content-start">
              <span className="text-sm font-medium">演员</span>

              <div className="grid gap-2">
                {actors.map((actor) => {
                  const bound = getBoundSpeakers(actor)
                  const isSelected = selectedActor === actor
                  return (
                    <button
                      key={actor}
                      onClick={() => setSelectedActor(actor)}
                      className={`
                        flex flex-col gap-1 rounded-2xl px-3 py-2 text-left transition-colors
                        ${isSelected
                          ? "bg-primary/10 ring-1 ring-primary/30"
                          : "bg-muted/50 hover:bg-muted"
                        }
                      `}
                    >
                      <div className="flex items-center gap-2">
                        <User className="size-4 text-primary flex-shrink-0" />
                        <span className="text-sm font-medium">{actor}</span>
                        <Button
                          variant="ghost"
                          size="icon-sm"
                          className="ml-auto text-muted-foreground hover:text-destructive flex-shrink-0 -mr-1"
                          onClick={(e) => {
                            e.stopPropagation()
                            handleRemoveActor(actor)
                          }}
                        >
                          <Trash2 className="size-3.5" />
                        </Button>
                      </div>
                      {bound.length > 0 && (
                        <div className="flex flex-wrap gap-1 pl-6">
                          {bound.map((sid) => (
                            <span
                              key={sid}
                              className={miniPill("neutral", "text-[10px]")}
                            >
                              {sid}
                            </span>
                          ))}
                        </div>
                      )}
                    </button>
                  )
                })}
              </div>

              {/* 添加演员 */}
              <div className="flex gap-2">
                <Input
                  placeholder="新演员姓名"
                  value={newActorName}
                  onChange={(e) => setNewActorName(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") handleAddActor()
                  }}
                  className="flex-1"
                />
                <Button variant="secondary" size="icon-sm" onClick={handleAddActor}>
                  <Plus className="size-4" />
                </Button>
              </div>
            </div>

            {/* ---- 右栏：已识别说话人（可多选） ---- */}
            <div className="grid gap-3 content-start">
              <div className="flex items-center justify-between">
                <span className="text-sm font-medium">已识别说话人</span>
                <span className="text-xs text-muted-foreground">
                  勾选后点击绑定
                </span>
              </div>

              {/* 未绑定说话人 — 始终展开 */}
              <div className="grid gap-2">
                {speakers.filter((s) => !getBoundActor(s.id)).map((speaker) => {
                  const isChecked = selectedSpeakers.has(speaker.id)
                  return (
                    <div
                      key={speaker.id}
                      className={`
                        flex items-center gap-2 rounded-2xl px-3 py-2 transition-colors
                        ${isChecked ? "bg-primary/5 ring-1 ring-primary/20" : "bg-muted/50"}
                      `}
                    >
                      <div
                        className={`
                          size-5 rounded-full border-2 flex items-center justify-center flex-shrink-0 cursor-pointer
                          transition-colors
                          ${isChecked
                            ? "bg-primary border-primary"
                            : "border-muted-foreground/30 bg-transparent"
                          }
                        `}
                        onClick={() => toggleSpeakerSelection(speaker.id)}
                      >
                        {isChecked && <Check className="size-3 text-primary-foreground" />}
                      </div>
                      <AudioLines className="size-4 text-primary flex-shrink-0" />
                      <span className="text-sm font-medium flex-1">{speaker.id}</span>
                      <Button
                        variant="ghost"
                        size="icon-sm"
                        className="text-muted-foreground hover:text-destructive flex-shrink-0 -mr-1"
                        onClick={() => handleRemoveSpeaker(speaker.id)}
                      >
                        <Trash2 className="size-3.5" />
                      </Button>
                    </div>
                  )
                })}
              </div>

              {/* 已绑定说话人 — 可折叠 */}
              {speakers.some((s) => getBoundActor(s.id)) && (
                <BoundSpeakersCollapse
                  speakers={speakers}
                  getBoundActor={getBoundActor}
                  selectedSpeakers={selectedSpeakers}
                  toggleSpeakerSelection={toggleSpeakerSelection}
                  handleRemoveSpeaker={handleRemoveSpeaker}
                />
              )}

              {/* 绑定按钮 */}
              <Button
                variant="default"
                size="sm"
                className="w-full gap-1.5"
                disabled={!selectedActor || selectedSpeakers.size === 0}
                onClick={handleBindSelected}
              >
                <Link2 className="size-4" />
                绑定到 {selectedActor || "…"}
                {selectedSpeakers.size > 0 && ` (${selectedSpeakers.size})`}
              </Button>

              {/* 合并说话人 */}
              {speakers.length >= 2 && (
                <div className="grid gap-2 pt-1">
                  <span className="text-xs text-muted-foreground">合并说话人</span>
                  <div className="flex items-center gap-2">
                    <Select value={mergeSource} onValueChange={setMergeSource}>
                      <SelectTrigger className="flex-1">
                        <SelectValue placeholder="源" />
                      </SelectTrigger>
                      <SelectContent>
                        {speakers.map((s) => (
                          <SelectItem key={s.id} value={s.id}>{s.id}</SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                    <span className="text-xs text-muted-foreground flex-shrink-0">→</span>
                    <Select value={mergeTarget} onValueChange={setMergeTarget}>
                      <SelectTrigger className="flex-1">
                        <SelectValue placeholder="目标" />
                      </SelectTrigger>
                      <SelectContent>
                        {speakers.map((s) => (
                          <SelectItem key={s.id} value={s.id}>{s.id}</SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                    <Button
                      variant="outline"
                      size="sm"
                      className="flex-shrink-0"
                      disabled={!mergeSource || !mergeTarget || mergeSource === mergeTarget}
                      onClick={handleMerge}
                    >
                      合并
                    </Button>
                  </div>
                </div>
              )}
            </div>
          </div>

          <Separator />

          {/* ========== 界面语言 ========== */}
          <div className="grid gap-2">
            <span className="text-sm font-medium">界面语言</span>
            <Select defaultValue="zh">
              <SelectTrigger className="w-full">
                <SelectValue placeholder="选择语言" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="zh">简体中文</SelectItem>
                <SelectItem value="en">English</SelectItem>
              </SelectContent>
            </Select>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  )
}

// ---- 已绑定说话人折叠组件 ----

interface BoundSpeakersCollapseProps {
  speakers: Speaker[]
  getBoundActor: (speakerId: string) => string | undefined
  selectedSpeakers: Set<string>
  toggleSpeakerSelection: (speakerId: string) => void
  handleRemoveSpeaker: (id: string) => void
}

function BoundSpeakersCollapse({
  speakers,
  getBoundActor,
  selectedSpeakers,
  toggleSpeakerSelection,
  handleRemoveSpeaker,
}: BoundSpeakersCollapseProps) {
  const [collapsed, setCollapsed] = useState(true)
  const boundSpeakers = speakers.filter((s) => getBoundActor(s.id))

  return (
    <div className="grid gap-2">
      <button
        onClick={() => setCollapsed((prev) => !prev)}
        className="flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground transition-colors"
      >
        {collapsed ? <ChevronRight className="size-3.5" /> : <ChevronDown className="size-3.5" />}
        <span>已绑定 ({boundSpeakers.length})</span>
      </button>

      {!collapsed && (
        <div className="grid gap-2">
          {boundSpeakers.map((speaker) => {
            const boundActor = getBoundActor(speaker.id)
            const isChecked = selectedSpeakers.has(speaker.id)
            return (
              <div
                key={speaker.id}
                className={`
                  flex items-center gap-2 rounded-2xl px-3 py-2 transition-colors
                  ${isChecked ? "bg-primary/5 ring-1 ring-primary/20" : "bg-muted/50"}
                `}
              >
                <div
                  className={`
                    size-5 rounded-full border-2 flex items-center justify-center flex-shrink-0 cursor-pointer
                    transition-colors
                    ${isChecked
                      ? "bg-primary border-primary"
                      : "border-muted-foreground/30 bg-transparent"
                    }
                  `}
                  onClick={() => toggleSpeakerSelection(speaker.id)}
                >
                  {isChecked && <Check className="size-3 text-primary-foreground" />}
                </div>
                <AudioLines className="size-4 text-primary flex-shrink-0" />
                <span className="text-sm font-medium flex-1">{speaker.id}</span>
                {boundActor && (
                  <span className={miniPill("primary", "text-[10px] flex-shrink-0")}>
                    {boundActor}
                  </span>
                )}
                <Button
                  variant="ghost"
                  size="icon-sm"
                  className="text-muted-foreground hover:text-destructive flex-shrink-0 -mr-1"
                  onClick={() => handleRemoveSpeaker(speaker.id)}
                >
                  <Trash2 className="size-3.5" />
                </Button>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
