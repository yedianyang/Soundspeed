import { useQuery } from "@tanstack/react-query"
import { API_BASE } from "@/lib/config"
import { useSessionStore } from "@/store/session"
import type { SceneDTO, ScriptDTO, TakeDTO, TakeDetailDTO } from "@/types/api"

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

export function startTake(sceneId: number, shot?: string | null): Promise<void> {
  return request<void>(`/api/v1/take/start`, {
    method: "POST",
    body: JSON.stringify({ scene_id: sceneId, shot: shot ?? null }),
  })
}

export function endTake(): Promise<void> {
  return request<void>(`/api/v1/take/end`, {
    method: "POST",
    body: JSON.stringify({}),
  })
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
