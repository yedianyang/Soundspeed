import { useState } from "react"
import {
  Check,
  Plus,
  Trash2,
  Undo2,
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
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { STATUS_DOT, STATUS_LABEL } from "@/lib/constants"
import { stageButton, recordingDisabled } from "@/lib/styles"
import { cn, formatElapsed } from "@/lib/utils"
import type { Status } from "@/types/take"
import type { SceneDTO, LlmState } from "@/types/api"
import TakeSpeakerSelect from "@/components/admin/TakeSpeakerSelect"
import MemoInput from "@/components/admin/MemoInput"

interface BottomControlBarProps {
  isRecording: boolean
  onToggleRecording: () => void
  mark: Status
  onCycleMark: () => void
  elapsed: number
  recDisabled?: boolean
  recHint?: string | null
  // ── 1.x：本 take 在场演员选择 ──
  speakerIds: number[]
  onSpeakerIdsChange: (ids: number[]) => void

  // ── scene / take 接线（2.C / 2.D / §16 工作槽）──
  scenes: SceneDTO[]
  activeScene: SceneDTO | undefined
  // ── 工作槽 workSlot（待录描述符）：底部 Shot / Take badge 读它，独立于具体 take ──
  slotShot: string | null // 空 shot 显示 "—"
  slotTakeLabel: string // 工作槽 take_number 显示（无 suffix）；无 workSlot 时为 "—"
  slotTakeNumber: number | null
  // Mark 的作用对象（currentTakeRecord）。null → Mark 禁用。
  currentTakeId: number | null
  // Delete（事件 7）的作用对象 = workSlot 组最新 live take 是否存在。false（空组）→ 删除禁用。
  canDeleteSlot: boolean
  onSelectScene: (sceneId: number) => void
  onCreateScene: () => void
  // 改 Shot（事件 6）：free-text，只更新 workSlot，不发 PATCH。
  onChangeShot: (shot: string | null) => void
  // 改 Take：手动指定待录号，只更新 workSlot，不发 PATCH（下一次 REC 作为显式号传后端）。
  onChangeTake: (takeNumber: number) => void
  onNextTake: () => void
  nextTakeBusy?: boolean
  onDeleteTake: () => void
  // 删除撤销（2.D point 4）
  canUndo: boolean
  onUndoDelete: () => void
  undoBusy?: boolean
  // 各操作 inflight 时禁用，避免重复触发。
  sceneBusy?: boolean
  takeBusy?: boolean

  // 打字 memo 提交后回调（触发 NoteList 刷新已落库 notes）。
  onNoteAdded?: () => void

  // ── P5：LLM 反馈档案一级入口（QP 问答 + L2 推送全历史）。未读点驱动来自 store.archiveUnread。──
  onOpenArchive: () => void
  archiveUnread: number
  // LLM 运行态（与 header LLM chip 同源）：非 idle = 正在跑 → 入口左点呈处理态（amber + 脉冲）。
  llmState: LlmState
}

export default function BottomControlBar({
  isRecording,
  onToggleRecording,
  mark,
  onCycleMark,
  elapsed,
  recDisabled = false,
  recHint = null,
  speakerIds,
  onSpeakerIdsChange,
  scenes,
  activeScene,
  slotShot,
  slotTakeLabel,
  slotTakeNumber,
  currentTakeId,
  canDeleteSlot,
  onSelectScene,
  onCreateScene,
  onChangeShot,
  onChangeTake,
  onNextTake,
  nextTakeBusy = false,
  onDeleteTake,
  canUndo,
  onUndoDelete,
  undoBusy = false,
  sceneBusy = false,
  takeBusy = false,
  onNoteAdded,
  onOpenArchive,
  archiveUnread,
  llmState,
}: BottomControlBarProps) {
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [shotDraft, setShotDraft] = useState("")
  const [takeDraft, setTakeDraft] = useState("")

  // currentTakeId 为 null（本会话尚无 take）→ 作用于「当前 take」的控件（Mark / Delete）禁用。
  const noTake = currentTakeId == null

  // 改 Shot（事件 6）：只更新 workSlot，不发 PATCH。空输入归一为 null（→ 空 shot）。
  const commitShot = () => {
    const v = shotDraft.trim()
    onChangeShot(v ? v : null)
  }

  // 改 Take：解析为正整数才提交（take 号从 1 起，挡掉空/0/负/非数字）。
  const commitTake = () => {
    const n = Number.parseInt(takeDraft.trim(), 10)
    if (Number.isFinite(n) && n >= 1) onChangeTake(n)
  }

  // 录制中（E）：Scene / Shot / Next Take / Delete / 撤销 全禁，只有 Mark 可点。
  // 因录制而禁用的控件加淡红遮罩（recordingDisabled），区别于普通灰色 disabled（opacity-50）。
  const sceneDisabled = isRecording || sceneBusy
  // Shot 现在改 workSlot（无需 take），故只受录制锁约束，不再受 noTake 限制。
  const shotDisabled = isRecording
  // Take 与 Shot 对称：手动改待录号也只受录制锁约束。
  const takeDisabled = isRecording
  const nextDisabled = !activeScene || takeBusy || nextTakeBusy || isRecording
  // Mark 作用于 currentTakeRecord，受 noTake 约束。Delete 作用于 workSlot 组最新 live take，
  // 空组（canDeleteSlot=false）禁用。
  const deleteDisabled = !canDeleteSlot || isRecording || takeBusy
  const undoDisabled = !canUndo || isRecording || undoBusy

  // 控件 className 里的「禁用态外观」：录制锁 → 淡红遮罩；否则普通灰。
  const disabledTone = (recLocked: boolean, otherwiseDisabled: boolean) =>
    isRecording && recLocked
      ? recordingDisabled
      : otherwiseDisabled
        ? "opacity-50"
        : undefined

  return (
    <div className="flex-shrink-0 border-t bg-background">
      {/* Memo input（真实打字 memo 输入口；类别走 @语法，Mic 预留语音入口）*/}
      <div className="relative z-30 px-4 pt-2 pb-1.5">
        <MemoInput onNoteAdded={onNoteAdded} />
      </div>

      {/* Controls: left stack + right REC (absolute) */}
      <div className="px-4 pb-2 mt-1 relative">
        <div className="flex flex-col gap-2 pr-24">
          {/* Row 1: Scene / Shot / Take / Mark。窄屏可换行（Scene 持最长内容，给更大比例）。 */}
          <div className="flex items-center gap-2 flex-wrap">
            {/* Scene：选已有场 → activate；新建 → 弹窗。录制中禁切场（呼应后端 409）。
                Scene 持最长内容（scene_code），单独放宽：窄屏占双倍比例、宽屏按内容撑开不截断。 */}
            <DropdownMenu>
              <DropdownMenuTrigger asChild disabled={sceneDisabled}>
                <Button
                  variant="ghost"
                  size="default"
                  className={cn(
                    stageButton,
                    "flex-[2] sm:w-auto sm:min-w-[7rem]",
                    disabledTone(true, sceneDisabled),
                  )}
                  title={isRecording ? "录制中不可切场" : "切换 / 新建场次"}
                >
                  <span className="text-[10px] uppercase tracking-wider text-muted-foreground">
                    Scene
                  </span>
                  <span className="font-semibold text-sm text-foreground whitespace-nowrap">
                    {activeScene ? activeScene.scene_code : "—"}
                  </span>
                  <ChevronDown className="size-3 text-muted-foreground" />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="start" className="w-56">
                <DropdownMenuLabel>切换场次</DropdownMenuLabel>
                {scenes.length === 0 && (
                  <DropdownMenuItem disabled>
                    <span className="text-muted-foreground text-xs">暂无场次</span>
                  </DropdownMenuItem>
                )}
                {scenes.map((s) => {
                  const isActive = s.scene_id === activeScene?.scene_id
                  return (
                    <DropdownMenuItem
                      key={s.scene_id}
                      className={cn(isActive && "bg-accent")}
                      onClick={() => {
                        if (!isActive) onSelectScene(s.scene_id)
                      }}
                    >
                      <span className="font-mono text-xs flex-1 truncate">
                        {s.scene_code}
                      </span>
                      {isActive && <Check className="size-3.5" />}
                    </DropdownMenuItem>
                  )
                })}
                <DropdownMenuSeparator />
                <DropdownMenuItem onClick={onCreateScene}>
                  <Plus className="size-3.5 mr-2" />
                  新建 Scene
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>

            {/* Shot（事件 6）：free-text，提交后只改 workSlot，不发 PATCH（改 shot = 换镜，非改历史）。 */}
            <DropdownMenu
              onOpenChange={(open) => {
                if (open) setShotDraft(slotShot ?? "")
              }}
            >
              <DropdownMenuTrigger asChild disabled={shotDisabled}>
                <Button
                  variant="ghost"
                  size="default"
                  className={cn(stageButton, disabledTone(true, shotDisabled))}
                  title={isRecording ? "录制中不可换镜" : "换镜（改待录 Shot）"}
                >
                  <span className="text-[10px] uppercase tracking-wider text-muted-foreground">
                    Shot
                  </span>
                  <span className="font-semibold text-sm text-foreground truncate">
                    {slotShot ?? "—"}
                  </span>
                  <ChevronDown className="size-3 text-muted-foreground" />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="start" className="w-48 p-2">
                <DropdownMenuLabel className="px-1">Shot</DropdownMenuLabel>
                <form
                  onSubmit={(e) => {
                    e.preventDefault()
                    commitShot()
                  }}
                  className="flex items-center gap-1.5 px-1"
                >
                  <Input
                    autoFocus
                    value={shotDraft}
                    onChange={(e) => setShotDraft(e.target.value)}
                    placeholder="例：2A"
                    className="h-8 text-sm"
                  />
                  <Button type="submit" size="icon-sm" className="rounded-full">
                    <Check className="size-3.5" />
                  </Button>
                </form>
              </DropdownMenuContent>
            </DropdownMenu>

            {/* Take（与 Shot 同构的下拉数字弹窗）：读 workSlot.take_number 显示已录最新（空组为 1）。
                平时 REC 由后端按 Shot 自动计次；用户可手动改待录号，下一次 REC 作为显式号传后端
                （撞 live 号后端落后缀）。 */}
            <DropdownMenu
              onOpenChange={(open) => {
                if (open) setTakeDraft(slotTakeNumber != null ? String(slotTakeNumber) : "")
              }}
            >
              <DropdownMenuTrigger asChild disabled={takeDisabled}>
                <Button
                  variant="ghost"
                  size="default"
                  className={cn(stageButton, disabledTone(true, takeDisabled))}
                  title={isRecording ? "录制中不可改号" : "改待录 Take 号"}
                >
                  <span className="text-[10px] uppercase tracking-wider text-muted-foreground">
                    Take
                  </span>
                  <span className="font-semibold text-sm text-foreground">
                    {slotTakeLabel}
                  </span>
                  <ChevronDown className="size-3 text-muted-foreground" />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="start" className="w-48 p-2">
                <DropdownMenuLabel className="px-1">Take</DropdownMenuLabel>
                <form
                  onSubmit={(e) => {
                    e.preventDefault()
                    commitTake()
                  }}
                  className="flex items-center gap-1.5 px-1"
                >
                  <Input
                    autoFocus
                    type="number"
                    inputMode="numeric"
                    min={1}
                    step={1}
                    value={takeDraft}
                    onChange={(e) => setTakeDraft(e.target.value)}
                    placeholder="例：5"
                    className="h-8 text-sm"
                  />
                  <Button type="submit" size="icon-sm" className="rounded-full">
                    <Check className="size-3.5" />
                  </Button>
                </form>
              </DropdownMenuContent>
            </DropdownMenu>

            {/* Mark：改当前 take 的 status（循环 MARK_ORDER）。无 take → 禁用。 */}
            <Button
              variant="ghost"
              size="default"
              onClick={onCycleMark}
              disabled={noTake || takeBusy}
              className={cn(
                "flex-none sm:w-24 gap-1.5 h-9 px-3 rounded-full bg-background border border-border/60 shadow-sm active:scale-95 transition-transform",
                (noTake || takeBusy) && "opacity-50"
              )}
              title={noTake ? "本会话尚无 take" : "切换当前 take 状态"}
            >
              <span className={cn("size-1.5 rounded-full", STATUS_DOT[mark] || "bg-muted-foreground")} />
              <span className="text-sm font-medium text-foreground">{STATUS_LABEL[mark]}</span>
            </Button>
          </div>

          {/* Row 2: 本 take 在场演员 + Next take + Delete + Undo。Next Take 不自动开录（建空块）。录制中全禁。 */}
          <div className="flex items-center gap-3">
            <TakeSpeakerSelect
              value={speakerIds}
              onChange={onSpeakerIdsChange}
              disabled={isRecording}
            />
            {/* 窄屏（竖屏 <lg）缩成 + 圆按钮省空间；宽屏（横屏）显「Next take」全文。 */}
            <Button
              variant="ghost"
              onClick={onNextTake}
              disabled={nextDisabled}
              className={cn(
                "gap-1.5 h-10 px-3 lg:px-5 rounded-full bg-muted/60 hover:bg-muted/80 active:bg-muted/80 active:scale-95 transition-all text-foreground text-sm font-medium",
                disabledTone(true, nextDisabled)
              )}
              title={isRecording ? "录制中不可起新 take" : "起下一条空 take"}
            >
              <Plus className="size-4" />
              <span className="hidden lg:inline">Next take</span>
            </Button>
            <Button
              variant="ghost"
              size="icon"
              onClick={() => setConfirmDelete(true)}
              disabled={deleteDisabled}
              className={cn(
                "h-10 w-10 text-destructive hover:text-destructive active:scale-95 transition-transform rounded-full",
                disabledTone(true, deleteDisabled)
              )}
              title={
                isRecording
                  ? "录制中不可删除"
                  : !canDeleteSlot
                    ? "该 Shot 组尚无可删 take"
                    : "删除该 Shot 组最新一条 take"
              }
            >
              <Trash2 className="size-5" />
            </Button>
            {/* 撤销删除：弹撤销栈顶 take_id → restore。栈空 / 录制中禁用。 */}
            <Button
              variant="ghost"
              size="icon"
              onClick={onUndoDelete}
              disabled={undoDisabled}
              className={cn(
                "h-10 w-10 text-muted-foreground hover:text-foreground active:scale-95 transition-transform rounded-full",
                disabledTone(true, undoDisabled)
              )}
              title={
                isRecording
                  ? "录制中不可撤销"
                  : !canUndo
                    ? "无可撤销的删除"
                    : "撤销最近一次删除"
              }
            >
              <Undo2 className="size-5" />
            </Button>

            {/* LLM 历史一级入口：Mark(TBD) 同款视觉（白底 + 边框 + 阴影 + 左状态点）。
                左点与 header LLM chip 同步：处理中（非 idle）= amber + 脉冲，呈现正在处理；
                idle 时有未读 = amber，无未读 = 绿（同 header idle）。紧跟撤销按钮右侧。 */}
            <Button
              variant="ghost"
              size="default"
              onClick={onOpenArchive}
              className="flex-none gap-1.5 h-9 px-3 rounded-full bg-background border border-border/60 shadow-sm active:scale-95 transition-transform"
              title="LLM 反馈历史：QP 问答 + L2 推送全历史"
            >
              <span
                className={cn(
                  "size-1.5 rounded-full",
                  llmState !== "idle"
                    ? "bg-primary animate-pulse"
                    : archiveUnread > 0
                      ? "bg-primary"
                      : "bg-green-500",
                )}
              />
              <span className="text-sm font-medium text-foreground">LLM 历史</span>
            </Button>
          </div>
        </div>

        {/* REC button */}
        <Button
          variant="ghost"
          onClick={onToggleRecording}
          disabled={recDisabled}
          className={cn(
            "absolute right-4 bottom-2 size-20 rounded-full text-white shadow-lg transition-all active:scale-95 border-0",
            isRecording
              ? "bg-red-600 hover:bg-red-600 ring-4 ring-red-500/20"
              : "bg-red-500 hover:bg-red-500 ring-2 ring-red-500/10",
            recDisabled && "opacity-50 cursor-not-allowed"
          )}
          title={recDisabled ? recHint ?? "无法录制" : isRecording ? "停止录制" : "开始录制"}
        >
          <span className="text-xs font-mono tracking-wider font-semibold">
            {isRecording ? formatElapsed(elapsed) : "Capture"}
          </span>
        </Button>

        {recHint && !isRecording && (
          <span className="absolute right-4 bottom-24 text-[10px] font-mono text-destructive whitespace-nowrap">
            {recHint}
          </span>
        )}
      </div>

      {/* Log */}
      <div className="px-4 pb-1.5 pt-0.5 border-t">
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

      {/* 删除二次确认 */}
      <Dialog open={confirmDelete} onOpenChange={setConfirmDelete}>
        <DialogContent showCloseButton={false}>
          <DialogHeader>
            <DialogTitle>删除当前 take？</DialogTitle>
            <DialogDescription>
              将删除最新一条 take{slotTakeNumber != null ? ` T${slotTakeLabel}` : ""}
              {slotShot ? ` · ${slotShot}` : ""}。可用撤销按钮恢复。
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setConfirmDelete(false)}>
              取消
            </Button>
            <Button
              variant="destructive"
              onClick={() => {
                setConfirmDelete(false)
                onDeleteTake()
              }}
            >
              删除
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
