import { useState } from "react"
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
import { Button } from "@/components/ui/button"
import { Separator } from "@/components/ui/separator"
import { miniPill } from "@/lib/styles"
import { API_BASE, LS_TOKEN_KEY } from "@/lib/config"
import { useSessionStore } from "@/store/session"
import { Check, ChevronDown, ChevronRight } from "lucide-react"
import { Plus, Trash2, User, AudioLines, Link2, Server } from "lucide-react"

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

  const handleSaveToken = () => {
    const v = tokenInput.trim()
    if (v) {
      localStorage.setItem(LS_TOKEN_KEY, v)
    } else {
      localStorage.removeItem(LS_TOKEN_KEY)
    }
    // 写 localStorage 不会通知 useLiveConnection；更新 store.token 才会翻转连接态并触发重连。
    setToken(v || null)
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
