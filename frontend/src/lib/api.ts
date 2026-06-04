import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { API_BASE } from "@/lib/config"
import { useSessionStore } from "@/store/session"
import type {
  ActivateSceneResult,
  CreateSceneBody,
  CreateSceneResult,
  PatchTakeBody,
  SceneDTO,
  ScriptDTO,
  TakeDTO,
  TakeDetailDTO,
  TranscriptSegmentDTO,
} from "@/types/api"

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.name = "ApiError"
    this.status = status
  }
}

// token 在调用时读取（SettingsDialog 保存后会变），不在模块加载时固化。
async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const token = useSessionStore.getState().token
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(init?.headers as Record<string, string> | undefined),
  }
  if (token) headers.Authorization = `Bearer ${token}`

  const res = await fetch(`${API_BASE}${path}`, { ...init, headers })
  if (!res.ok) {
    throw new ApiError(res.status, `${init?.method ?? "GET"} ${path} → ${res.status}`)
  }
  if (res.status === 204) return undefined as T
  const text = await res.text()
  return (text ? JSON.parse(text) : undefined) as T
}

// ── REST ──

export async function getScenes(): Promise<SceneDTO[]> {
  const data = await request<{ scenes: SceneDTO[] }>(`/api/v1/scenes`)
  return data.scenes
}

// 取活跃场次（is_active），无则退回第一条，全空返回 undefined。
export function pickActiveScene(scenes: SceneDTO[] | undefined): SceneDTO | undefined {
  if (!scenes || scenes.length === 0) return undefined
  return scenes.find((s) => s.is_active) ?? scenes[0]
}

// 取某场最新剧本（GET /scenes/{id}/script）。无剧本 → 后端返回 {script:null}，这里返回 null。
export async function getSceneScript(sceneId: number): Promise<ScriptDTO | null> {
  const data = await request<{ script: ScriptDTO | null }>(
    `/api/v1/scenes/${sceneId}/script`,
  )
  return data.script
}

export async function getTakes(sceneId?: number): Promise<TakeDTO[]> {
  const qs = sceneId !== undefined ? `?scene_id=${sceneId}` : ""
  const data = await request<{ takes: TakeDTO[] }>(`/api/v1/takes${qs}`)
  return data.takes
}

export function getTake(id: number): Promise<TakeDetailDTO> {
  return request<TakeDetailDTO>(`/api/v1/takes/${id}`)
}

// takeNumber：用户在底部 Take 弹窗手动指定的待录号。省略/undefined → 后端按 (scene,shot) 自动 MAX+1。
export function startTake(
  sceneId: number,
  shot?: string | null,
  takeNumber?: number | null,
): Promise<void> {
  return request<void>(`/api/v1/take/start`, {
    method: "POST",
    body: JSON.stringify({
      scene_id: sceneId,
      shot: shot ?? null,
      take_number: takeNumber ?? null,
    }),
  })
}

export function endTake(): Promise<void> {
  return request<void>(`/api/v1/take/end`, {
    method: "POST",
    body: JSON.stringify({}),
  })
}

// ── Scene / Take 写操作（2.C）──
// 失败抛 ApiError（带 .status）：409 take_in_progress / take_number_conflict / scene_not_active，
// 404 不存在。调用处按 status catch。

// 建场或复用同 scene_code 的场（都 200，created 区分）。
export function createScene(body: CreateSceneBody): Promise<CreateSceneResult> {
  return request<CreateSceneResult>(`/api/v1/scenes`, {
    method: "POST",
    body: JSON.stringify(body),
  })
}

// 把某场置为活跃。录制中 → 409 take_in_progress；不存在 → 404。
export function activateScene(sceneId: number): Promise<ActivateSceneResult> {
  return request<ActivateSceneResult>(`/api/v1/scenes/${sceneId}/activate`, {
    method: "POST",
    body: JSON.stringify({}),
  })
}

// 改某条 take（status/shot/scene_id/take_number/notes 全可选）。录制中改 scene_id/take_number → 409。
// 撞号后端自动加后缀返回 200（带新 take_suffix），不再 409 take_number_conflict。
// 目标场不存在 / take 不存在 → 404。
export function patchTake(takeId: number, body: PatchTakeBody): Promise<TakeDTO> {
  return request<TakeDTO>(`/api/v1/takes/${takeId}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  })
}

// 软删某条 take。204 无 body；录制中 → 409；不存在 → 404。可经 restoreTake 撤销。
export function deleteTake(takeId: number): Promise<void> {
  return request<void>(`/api/v1/takes/${takeId}`, { method: "DELETE" })
}

// 撤销软删，返回恢复后的 TakeDTO。未软删 / 不存在 → 404。
// ⚠ 若该 take 的三元（scene_id, take_number, take_suffix）已被新 take 占用，后端 UNIQUE 约束会报 500，
// 调用处需 catch 并优雅提示（见 useRestoreTake / handleUndoDelete）。
export function restoreTake(takeId: number): Promise<TakeDTO> {
  return request<TakeDTO>(`/api/v1/takes/${takeId}/restore`, {
    method: "POST",
    body: JSON.stringify({}),
  })
}

// 纠正某条 segment 的 speaker 归属（PATCH，落库）。speaker=null 表示置「未知」。
// null 必须显式带进 body（不能省略字段），后端用它区分「置未知」与「漏传」。
export function correctSegmentSpeaker(
  takeId: number,
  segmentId: number,
  speaker: string | null,
): Promise<TranscriptSegmentDTO> {
  return request<TranscriptSegmentDTO>(
    `/api/v1/takes/${takeId}/segments/${segmentId}`,
    { method: "PATCH", body: JSON.stringify({ speaker }) },
  )
}

// dev-only 合成 ASR 注入（后端仅 SOUNDSPEED_DEV=1 挂载 /api/v1/debug/asr）。
// 服务端补 start/end_frame，前端只发 ch/text/speaker/is_partial。
export interface DebugAsrSeg {
  ch: 1 | 2
  text: string
  speaker: string | null
  is_partial: boolean
}

export function injectDebugAsr(seg: DebugAsrSeg): Promise<void> {
  return request<void>(`/api/v1/debug/asr`, {
    method: "POST",
    body: JSON.stringify(seg),
  })
}

// dev-only 剧本注入（后端仅 SOUNDSPEED_DEV=1 挂载 /api/v1/debug/script）。
// 注入后持久化到场次，L2 才能按剧本逐行比对产出真实 diff。sceneId 省略时后端用活跃场次。
export interface DebugScriptLine {
  character: string | null
  text: string
}

export interface DebugScriptResult {
  script_id: number
  scene_id: number
  line_count: number
}

// 可选 slugline heading，随注入剧本一起写到场次（后端部分更新，只写非空字段）。
export interface DebugScriptHeading {
  int_ext?: string
  time_of_day?: string
  location?: string
}

// dev-only 全量清库（后端仅 SOUNDSPEED_DEV=1 挂载 /api/v1/debug/reset-db）。
// 清空所有业务表，后端用 seed_dev_scene 重播种一个 active 空场，返回 {status, reseeded}。
// 危险操作不可恢复，调用处需二次确认。成功后建议整页 reload 派生干净状态。
export interface ResetDbResult {
  status: string
  reseeded: boolean
}

export function resetDb(): Promise<ResetDbResult> {
  return request<ResetDbResult>(`/api/v1/debug/reset-db`, {
    method: "POST",
  })
}

export function injectDebugScript(
  lines: DebugScriptLine[],
  sceneId?: number,
  heading?: DebugScriptHeading,
): Promise<DebugScriptResult> {
  const body: Record<string, unknown> = { lines }
  if (sceneId !== undefined) body.scene_id = sceneId
  // 后端已忽略空串（DAL 归一为不更新）；这里仍过滤空字段，避免发送无意义的空值。
  if (heading) {
    for (const key of ["int_ext", "time_of_day", "location"] as const) {
      const v = heading[key]?.trim()
      if (v) body[key] = v
    }
  }
  return request<DebugScriptResult>(`/api/v1/debug/script`, {
    method: "POST",
    body: JSON.stringify(body),
  })
}

// ── react-query 查询键 + hooks ──

export const scenesQueryKey = () => ["scenes"] as const

export const takesQueryKey = (sceneId?: number) =>
  ["takes", sceneId ?? null] as const

export const takeQueryKey = (id: number) => ["take", id] as const

export const sceneScriptQueryKey = (sceneId: number | null | undefined) =>
  ["scene-script", sceneId ?? null] as const

export function useScenes() {
  return useQuery({
    queryKey: scenesQueryKey(),
    queryFn: getScenes,
  })
}

export function useSceneScript(sceneId: number | null | undefined) {
  return useQuery({
    queryKey: sceneScriptQueryKey(sceneId),
    queryFn: () => getSceneScript(sceneId as number),
    enabled: sceneId != null,
  })
}

export function useTakes(sceneId?: number) {
  return useQuery({
    queryKey: takesQueryKey(sceneId),
    queryFn: () => getTakes(sceneId),
  })
}

export function useTake(id: number, enabled: boolean) {
  return useQuery({
    queryKey: takeQueryKey(id),
    queryFn: () => getTake(id),
    enabled,
  })
}

// ── mutation hooks（2.C）──
// 后端是否在这些写操作上 publish WS 未明（只 take.deleted / scene.changed 是新增明确事件）。
// 故每个 mutation 成功后自助 invalidate，不依赖 WS 回灌。用 invalidate→refetch（而非乐观更新），
// 避免与 WS patch-merge 双写。

export function useCreateScene() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: createScene,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: scenesQueryKey() })
    },
  })
}

export function useActivateScene() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (sceneId: number) => activateScene(sceneId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: scenesQueryKey() })
    },
  })
}

export function usePatchTake() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ takeId, body }: { takeId: number; body: PatchTakeBody }) =>
      patchTake(takeId, body),
    onSuccess: () => {
      // 改 scene_id/take_number 可能挪场，takes 与 scenes 都可能受影响。
      queryClient.invalidateQueries({ queryKey: ["takes"] })
      queryClient.invalidateQueries({ queryKey: scenesQueryKey() })
    },
  })
}

export function useDeleteTake() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (takeId: number) => deleteTake(takeId),
    onSuccess: (_data, takeId) => {
      // seedTakes 只增不删，invalidate→refetch 不会把已删条目从 store Map 抹掉。
      // 必须显式 removeTake，否则 HistoryTakes 仍显示该条（与 take.deleted WS 处理对称）。
      useSessionStore.getState().removeTake(takeId)
      queryClient.invalidateQueries({ queryKey: ["takes"] })
    },
  })
}

export function useRestoreTake() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (takeId: number) => restoreTake(takeId),
    onSuccess: () => {
      // 恢复后 take 重新出现：refetch 把它 seed 回 store Map。
      queryClient.invalidateQueries({ queryKey: ["takes"] })
    },
  })
}

// startTake / endTake 自助 invalidate ["takes"]：take.changed（5 字段 Pick）不带 end_ts，
// 只有 refetch 能把真实 end_ts / shot / start_ts 填进 store Map。REC 状态机判「take 是否已结束」
// 读 end_ts，故必须 await 这个 mutation（含 onSuccess 的 invalidate）后再做下一步决策。
export function useStartTake() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({
      sceneId,
      shot,
      takeNumber,
    }: {
      sceneId: number
      shot?: string | null
      takeNumber?: number | null
    }) => startTake(sceneId, shot, takeNumber),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ["takes"] }),
  })
}

export function useEndTake() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: () => endTake(),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ["takes"] }),
  })
}
