import { create } from "zustand"
import { API_BASE, LS_API_BASE_KEY, LS_TOKEN_KEY } from "@/lib/config"
import type {
  AsrMsg,
  LlmState,
  TakeChangedMsg,
  TakeDTO,
} from "@/types/api"

export type ConnectionState = "connecting" | "open" | "closed" | "no-token"

// 当前录制 take 的实时转录条目（按声道维护）。
export interface LiveSeg {
  text: string
  speaker: string | null
  start_frame: number
  end_frame: number
  isPartial: boolean
}

export interface CurrentTake {
  take_id: number | null
  scene_id: number | null
  take_number: number | null
  shot: string | null
  recording: boolean
}

function readToken(): string | null {
  if (typeof localStorage === "undefined") return null
  const v = localStorage.getItem(LS_TOKEN_KEY)
  return v && v.trim() ? v : null
}

function readApiBase(): string {
  if (typeof localStorage === "undefined") return API_BASE
  return localStorage.getItem(LS_API_BASE_KEY) ?? API_BASE
}

interface SessionState {
  // 配置 / 鉴权
  apiBase: string
  token: string | null
  connection: ConnectionState

  // 当前录制 take 的实时转录（按 ch）。partial 替换该声道最后一条 partial，final 落定。
  segments: { ch1: LiveSeg[]; ch2: LiveSeg[] }

  // 当前 take。recording 本地（前端按 start/end）；take_id/take_number 从 take.changed 取。
  currentTake: CurrentTake

  // take 列表：Map<take_id, TakeDTO>。getTakes 全量覆盖（权威），take.changed patch-merge 5 字段。
  takes: Map<number, TakeDTO>

  llm: { state: LlmState }

  // ── actions ──
  setToken: (t: string | null) => void
  setConnection: (c: ConnectionState) => void
  applyAsr: (ch: 1 | 2, isFinal: boolean, p: AsrMsg) => void
  applyTakeChanged: (m: TakeChangedMsg) => void
  seedTakes: (list: TakeDTO[]) => void
  setLlm: (state: LlmState) => void
  startRecordingLocal: (sceneId: number, shot: string | null) => void
  stopRecordingLocal: () => void
}

const initialCurrentTake: CurrentTake = {
  take_id: null,
  scene_id: null,
  take_number: null,
  shot: null,
  recording: false,
}

export const useSessionStore = create<SessionState>((set) => ({
  apiBase: readApiBase(),
  token: readToken(),
  connection: readToken() ? "connecting" : "no-token",

  segments: { ch1: [], ch2: [] },
  currentTake: { ...initialCurrentTake },
  takes: new Map(),
  llm: { state: "idle" },

  setToken: (t) =>
    set(() => ({
      token: t && t.trim() ? t : null,
      connection: t && t.trim() ? "connecting" : "no-token",
    })),

  setConnection: (c) => set(() => ({ connection: c })),

  applyAsr: (ch, isFinal, p) =>
    set((state) => {
      const key = ch === 1 ? "ch1" : "ch2"
      const list = state.segments[key]
      const seg: LiveSeg = {
        text: p.text,
        speaker: p.speaker,
        start_frame: p.start_frame,
        end_frame: p.end_frame,
        isPartial: !isFinal,
      }
      const last = list[list.length - 1]
      // partial 替换该声道最后一条 partial；final 也优先落定最后一条 partial，否则 push。
      const next =
        last && last.isPartial
          ? [...list.slice(0, -1), seg]
          : [...list, seg]
      return { segments: { ...state.segments, [key]: next } }
    }),

  applyTakeChanged: (m) =>
    set((state) => {
      const takes = new Map(state.takes)
      const existing = takes.get(m.take_id)
      if (existing) {
        // patch-merge：只覆盖 take.changed 的 5 字段，保留 shot/start_ts/end_ts/notes 等。
        takes.set(m.take_id, { ...existing, ...m })
      } else {
        // 新 take：插入部分条目，其余字段等 getTakes/getTake 补齐。
        takes.set(m.take_id, {
          take_id: m.take_id,
          scene_id: m.scene_id,
          take_number: m.take_number,
          status: m.status,
          script_diff: m.script_diff,
          shot: null,
          start_ts: 0,
          end_ts: null,
          notes: null,
          created_at: 0,
          updated_at: 0,
        })
      }

      // 单操作员假设：startRecordingLocal 后第一条 take_id 未知的 take.changed 绑定 currentTake。
      let currentTake = state.currentTake
      if (currentTake.recording && currentTake.take_id === null) {
        currentTake = {
          ...currentTake,
          take_id: m.take_id,
          scene_id: m.scene_id,
          take_number: m.take_number,
        }
      }

      return { takes, currentTake }
    }),

  // getTakes 快照永远不旧于任何同字段 WS 消息（DB 先写后 publish），故全量覆盖每个 take_id 条目。
  seedTakes: (list) =>
    set((state) => {
      const takes = new Map(state.takes)
      for (const t of list) {
        takes.set(t.take_id, t)
      }
      return { takes }
    }),

  setLlm: (state) => set(() => ({ llm: { state } })),

  startRecordingLocal: (sceneId, shot) =>
    set(() => ({
      segments: { ch1: [], ch2: [] },
      currentTake: {
        take_id: null,
        scene_id: sceneId,
        take_number: null,
        shot,
        recording: true,
      },
    })),

  stopRecordingLocal: () =>
    set((state) => ({
      currentTake: { ...state.currentTake, recording: false },
    })),
}))
