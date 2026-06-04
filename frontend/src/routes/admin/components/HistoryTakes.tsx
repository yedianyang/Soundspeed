import { useMemo, useState } from "react"
import type { UseMutationResult } from "@tanstack/react-query"
import { useQueryClient } from "@tanstack/react-query"
import { Check, ChevronRight, ChevronUp } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Input } from "@/components/ui/input"
import { Card, CardContent } from "@/components/ui/card"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { STATUS_DOT, STATUS_LABEL, MARK_ORDER } from "@/lib/constants"
import { cn, formatTakeLabel } from "@/lib/utils"
import { mutedCard } from "@/lib/styles"
import type {
  PatchTakeBody,
  SceneDTO,
  TakeDTO,
  TakeStatus,
  TranscriptSegmentDTO,
} from "@/types/api"
import {
  useTake,
  correctSegmentSpeaker,
  takeQueryKey,
  usePatchTake,
  useScenes,
} from "@/lib/api"
import { useSessionStore } from "@/store/session"
import { ScriptDiffView } from "./ScriptDiffView"
import { SpeakerLabel } from "./SpeakerLabel"

type PatchTakeMutation = UseMutationResult<
  TakeDTO,
  Error,
  { takeId: number; body: PatchTakeBody },
  unknown
>

// 三个可编辑 badge 共用的提交器：mutateAsync + 局部错误态，失败给轻提示不白屏。
// 成功后 usePatchTake 已 invalidate takes → refetch → seed 桥接刷新卡片。
function usePatchEditor(takeId: number, patchTake: PatchTakeMutation) {
  const [open, setOpen] = useState(false)
  const [error, setError] = useState(false)
  const submit = async (body: PatchTakeBody) => {
    setError(false)
    try {
      await patchTake.mutateAsync({ takeId, body })
      setOpen(false)
    } catch (err) {
      // 失败（如目标 scene 不存在 → 404）：保持编辑器开启并标红提示，UI 以 refetch 为准。
      console.error("修改 take 失败", err)
      setError(true)
    }
  }
  return { open, setOpen, error, setError, submit }
}

// Scene badge：下拉选另一个已有场 → PATCH {scene_id}（把这条 take 移到别的场）。
function SceneBadge({
  take,
  scenes,
  patchTake,
}: {
  take: TakeDTO
  scenes: SceneDTO[]
  patchTake: PatchTakeMutation
}) {
  const { open, setOpen, error, submit } = usePatchEditor(take.take_id, patchTake)
  const current = scenes.find((s) => s.scene_id === take.scene_id)
  const label = current ? current.scene_code : `#${take.scene_id}`
  return (
    <div className="inline-flex flex-col">
      <DropdownMenu open={open} onOpenChange={setOpen}>
        <DropdownMenuTrigger asChild>
          <Badge variant="secondary" className="gap-1 cursor-pointer font-mono">
            Scene {label}
          </Badge>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start" className="w-56">
          <DropdownMenuLabel>移动到场次</DropdownMenuLabel>
          {scenes.length === 0 && (
            <DropdownMenuItem disabled>
              <span className="text-muted-foreground text-xs">暂无场次</span>
            </DropdownMenuItem>
          )}
          {scenes.map((s) => {
            const isCurrent = s.scene_id === take.scene_id
            return (
              <DropdownMenuItem
                key={s.scene_id}
                className={cn(isCurrent && "bg-accent")}
                onClick={() => {
                  if (isCurrent) {
                    setOpen(false)
                    return
                  }
                  void submit({ scene_id: s.scene_id })
                }}
              >
                <span className="font-mono text-xs flex-1 truncate">
                  {s.scene_code}
                </span>
                {isCurrent && <Check className="size-3.5" />}
              </DropdownMenuItem>
            )
          })}
        </DropdownMenuContent>
      </DropdownMenu>
      {error && (
        <span className="text-[10px] text-destructive">移动失败，请重试</span>
      )}
    </div>
  )
}

// Take 编号 badge：点开输入框改数字 → PATCH {take_number}。撞号后端自动加后缀（200），refetch 后显示如 3+。
function TakeNumberBadge({
  take,
  patchTake,
}: {
  take: TakeDTO
  patchTake: PatchTakeMutation
}) {
  const { open, setOpen, error, submit } = usePatchEditor(take.take_id, patchTake)
  const [draft, setDraft] = useState("")
  const commit = () => {
    const n = Number.parseInt(draft, 10)
    if (Number.isFinite(n) && n > 0 && n !== take.take_number) {
      void submit({ take_number: n })
    } else {
      setOpen(false)
    }
  }
  return (
    <div className="inline-flex flex-col">
      <DropdownMenu
        open={open}
        onOpenChange={(o) => {
          if (o) setDraft(String(take.take_number))
          setOpen(o)
        }}
      >
        <DropdownMenuTrigger asChild>
          <Badge variant="secondary" className="gap-1 cursor-pointer font-mono">
            Take {formatTakeLabel(take)}
          </Badge>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start" className="w-48 p-2">
          <DropdownMenuLabel className="px-1">Take 编号</DropdownMenuLabel>
          <form
            onSubmit={(e) => {
              e.preventDefault()
              commit()
            }}
            className="flex items-center gap-1.5 px-1"
          >
            <Input
              autoFocus
              type="number"
              min={1}
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              className="h-8 text-sm"
            />
            <Button type="submit" size="icon-sm" className="rounded-full">
              <Check className="size-3.5" />
            </Button>
          </form>
        </DropdownMenuContent>
      </DropdownMenu>
      {error && (
        <span className="text-[10px] text-destructive">改编号失败，请重试</span>
      )}
    </div>
  )
}

// Shot badge：点开输入框改 → PATCH {shot}（空串 = 清空 → null）。无 shot 时显示「+ Shot」占位。
function ShotBadge({
  take,
  patchTake,
}: {
  take: TakeDTO
  patchTake: PatchTakeMutation
}) {
  const { open, setOpen, error, submit } = usePatchEditor(take.take_id, patchTake)
  const [draft, setDraft] = useState("")
  const commit = () => {
    const v = draft.trim()
    const next = v ? v : null
    if (next !== (take.shot ?? null)) {
      void submit({ shot: next })
    } else {
      setOpen(false)
    }
  }
  return (
    <div className="inline-flex flex-col">
      <DropdownMenu
        open={open}
        onOpenChange={(o) => {
          if (o) setDraft(take.shot ?? "")
          setOpen(o)
        }}
      >
        <DropdownMenuTrigger asChild>
          <Badge
            variant="secondary"
            className={cn(
              "gap-1 cursor-pointer font-mono",
              !take.shot && "text-muted-foreground/70",
            )}
          >
            {take.shot ? `Shot ${take.shot}` : "+ Shot"}
          </Badge>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start" className="w-48 p-2">
          <DropdownMenuLabel className="px-1">Shot</DropdownMenuLabel>
          <form
            onSubmit={(e) => {
              e.preventDefault()
              commit()
            }}
            className="flex items-center gap-1.5 px-1"
          >
            <Input
              autoFocus
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              placeholder="例：2A（留空清除）"
              className="h-8 text-sm"
            />
            <Button type="submit" size="icon-sm" className="rounded-full">
              <Check className="size-3.5" />
            </Button>
          </form>
        </DropdownMenuContent>
      </DropdownMenu>
      {error && (
        <span className="text-[10px] text-destructive">改 Shot 失败，请重试</span>
      )}
    </div>
  )
}

function StatusBadge({
  status,
  pending,
  onChange,
}: {
  status: TakeStatus
  pending?: boolean
  onChange: (status: TakeStatus) => void
}) {
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild disabled={pending}>
        <Badge
          variant="secondary"
          className={cn("gap-1 cursor-pointer", pending && "opacity-50")}
        >
          <span className={cn("size-1.5 rounded-full", STATUS_DOT[status])} />
          {STATUS_LABEL[status]}
        </Badge>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start">
        <DropdownMenuLabel>修改状态</DropdownMenuLabel>
        {(MARK_ORDER as TakeStatus[]).map((s) => (
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

// 展开后的详情：拉 getTake → segments（speaker 可纠正）+ L2 摘要 + line_matches。
function TakeDetail({
  takeId,
  corrected,
  onCorrected,
}: {
  takeId: number
  corrected: boolean
  onCorrected: () => void
}) {
  const { data, isLoading, isError } = useTake(takeId, true)
  const queryClient = useQueryClient()
  // 纠正失败的 segment（轻量错误反馈，无 toast 库）。
  const [failedSegId, setFailedSegId] = useState<number | null>(null)

  // 候选 = 本 take 出现过的 distinct speaker（含当前条自身）+ null（未知）。
  const candidates = useMemo<(string | null)[]>(() => {
    const ids = new Set<string>()
    for (const seg of data?.segments ?? []) {
      if (seg.speaker) ids.add(seg.speaker)
    }
    return [...ids, null]
  }, [data])

  if (isLoading) {
    return <p className="text-sm text-muted-foreground">加载中…</p>
  }
  if (isError || !data) {
    return <p className="text-sm text-destructive">详情加载失败</p>
  }

  const diff = data.script_diff
  // 按通道分组：ch1 = 主对话（speaker 可纠正）；ch2 = 备注（语音备注，与将来 typing memo 同层级）。
  const ch1Segs = data.segments.filter((s) => s.ch === 1)
  const memoItems = data.segments.filter((s) => s.ch === 2)

  async function handleCorrect(seg: TranscriptSegmentDTO, next: string | null) {
    // 点当前值是 no-op：不发 PATCH、不标记已纠正。
    if (next === seg.speaker) return
    try {
      await correctSegmentSpeaker(takeId, seg.segment_id, next)
      await queryClient.invalidateQueries({ queryKey: takeQueryKey(takeId) })
      setFailedSegId(null)
      onCorrected()
    } catch (err) {
      // 失败：不改本地 UI（以 refetch 为准），标记该条以显示错误提示。
      console.error("纠正说话人失败", err)
      setFailedSegId(seg.segment_id)
    }
  }

  return (
    <div className="space-y-3">
      {/* ch1 主对话转录：speaker 可点纠正。key 绑 speaker 强制 SpeakerLabel 重挂载，
          绕过 Radix asChild trigger 复用 DOM、重渲染却不重绘文本的问题（数据/重渲染本身正常，见排查记录）。 */}
      {ch1Segs.length > 0 ? (
        <div className="space-y-1.5">
          {ch1Segs.map((seg) => (
            <div key={seg.segment_id}>
              <p className="text-sm">
                <SpeakerLabel
                  key={String(seg.speaker)}
                  speaker={seg.speaker}
                  options={candidates}
                  onChange={(next) => handleCorrect(seg, next)}
                />{" "}
                {seg.text}
              </p>
              {failedSegId === seg.segment_id && (
                <p className="text-xs text-destructive">说话人纠正失败，请重试</p>
              )}
            </div>
          ))}
        </div>
      ) : (
        <p className="text-sm text-muted-foreground/60">无转录片段</p>
      )}

      {/* L2 diff */}
      <div className="flex items-center gap-2">
        <div className="flex-1 h-px bg-border" />
        <span className="text-[10px] text-muted-foreground whitespace-nowrap">L2</span>
        <div className="flex-1 h-px bg-border" />
      </div>
      {corrected && (
        <p className="text-[11px] text-muted-foreground/80">说话人已纠正，剧本分析未更新</p>
      )}
      <ScriptDiffView diff={diff} />

      {/* 备注区：ch2 语音备注（speaker 恒空、不可点）。通用结构——将来 typing memo（手输备注）
          接入同一区、同层级（本次只预留，不实现输入/存储）。无 ch2 + 无 memo → 整块不渲染。 */}
      {memoItems.length > 0 && (
        <div className="space-y-1.5 pt-1">
          <div className="flex items-center gap-2">
            <div className="flex-1 h-px bg-border" />
            <span className="text-[10px] text-muted-foreground whitespace-nowrap">备注</span>
            <div className="flex-1 h-px bg-border" />
          </div>
          {memoItems.map((m) => (
            <p key={m.segment_id} className="text-xs text-muted-foreground">
              {m.text}
            </p>
          ))}
        </div>
      )}
    </div>
  )
}

export function HistoryTakes() {
  // takes 由 AdminHome 的 useTakes + seedTakes 桥接填充（始终挂载），此处只读 store。
  const takesMap = useSessionStore((s) => s.takes)

  // 状态改动走 PATCH /takes/{id}（2.C），成功后 invalidate→refetch→seed 桥接刷新，不再本地 override。
  const patchTake = usePatchTake()
  // Scene badge 的下拉场列表 + scene_id→scene_code 显示映射。
  const { data: scenes = [] } = useScenes()
  const [expanded, setExpanded] = useState<Set<number>>(new Set())
  // 本会话纠正过的 take（放父组件，避免 TakeDetail 折叠 unmount 丢失提示态）。
  const [correctedTakes, setCorrectedTakes] = useState<Set<number>>(new Set())

  const takes: TakeDTO[] = Array.from(takesMap.values()).sort(
    (a, b) =>
      a.scene_id - b.scene_id ||
      (a.shot ?? "").localeCompare(b.shot ?? "") ||
      a.take_number - b.take_number
  )

  const handleChangeStatus = (take: TakeDTO, status: TakeStatus) => {
    if (status === take.status) return
    patchTake.mutate({ takeId: take.take_id, body: { status } })
  }

  const toggleExpand = (id: number) => {
    setExpanded((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  if (takes.length === 0) {
    return (
      <p className="py-8 text-sm text-muted-foreground/60 text-center">
        暂无 take，开始录制后出现
      </p>
    )
  }

  return (
    <div className="py-4 space-y-2.5">
      {takes.map((take) => {
        const isExpanded = expanded.has(take.take_id)
        const summary = take.script_diff?.script_diff_summary
        return (
          <Card
            key={take.take_id}
            className={cn(mutedCard, "w-full text-left hover:bg-muted transition-colors")}
          >
            <CardContent className="p-4 space-y-2">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-1.5 flex-wrap">
                  <SceneBadge take={take} scenes={scenes} patchTake={patchTake} />
                  <ShotBadge take={take} patchTake={patchTake} />
                  <TakeNumberBadge take={take} patchTake={patchTake} />
                  <StatusBadge
                    status={take.status}
                    pending={
                      patchTake.isPending &&
                      patchTake.variables?.takeId === take.take_id
                    }
                    onChange={(s) => handleChangeStatus(take, s)}
                  />
                </div>
                <Button
                  variant="ghost"
                  size="icon-sm"
                  className="size-6 rounded-full"
                  onClick={() => toggleExpand(take.take_id)}
                >
                  {isExpanded ? (
                    <ChevronUp className="size-3.5" />
                  ) : (
                    <ChevronRight className="size-3.5" />
                  )}
                </Button>
              </div>

              {isExpanded ? (
                <TakeDetail
                  takeId={take.take_id}
                  corrected={correctedTakes.has(take.take_id)}
                  onCorrected={() =>
                    setCorrectedTakes((prev) => new Set(prev).add(take.take_id))
                  }
                />
              ) : (
                <p className="text-sm text-muted-foreground line-clamp-2">
                  {summary ?? "（展开查看转录）"}
                </p>
              )}
            </CardContent>
          </Card>
        )
      })}
    </div>
  )
}
