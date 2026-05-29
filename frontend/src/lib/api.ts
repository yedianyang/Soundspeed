import { useQuery } from "@tanstack/react-query"
import { API_BASE } from "@/lib/config"
import { useSessionStore } from "@/store/session"
import type { SceneDTO, TakeDTO, TakeDetailDTO } from "@/types/api"

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

// ── react-query 查询键 + hooks ──

export const scenesQueryKey = () => ["scenes"] as const

export const takesQueryKey = (sceneId?: number) =>
  ["takes", sceneId ?? null] as const

export const takeQueryKey = (id: number) => ["take", id] as const

export function useScenes() {
  return useQuery({
    queryKey: scenesQueryKey(),
    queryFn: getScenes,
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
