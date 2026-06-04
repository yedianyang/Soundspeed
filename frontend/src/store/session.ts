import { create } from "zustand"
import { LS_TOKEN_KEY } from "@/lib/config"
import type {
  AsrMsg,
  LlmState,
  TakeChangedMsg,
  TakeDTO,
  TakeProcessingMsg,
  TakeProcessingPhase,
  TranscriptSegmentDTO,
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
  const stored =
    typeof localStorage !== "undefined" ? localStorage.getItem(LS_TOKEN_KEY) : null
  if (stored && stored.trim()) return stored
  // dev 自动填：localhost 无需手填 token。后端 DEV 用固定 "dev"，故默认 VITE_ADMIN_TOKEN ?? "dev"。
  // 生产构建（import.meta.env.DEV=false）不自动填，仍需手填 token——不是鉴权绕过，只是已知 dev 默认。
  if (import.meta.env.DEV) {
    return (import.meta.env.VITE_ADMIN_TOKEN as string | undefined) ?? "dev"
  }
  return null
}

interface SessionState {
  // 鉴权（API base 取自 config.ts 的 API_BASE，不可编辑，故不入 store）
  token: string | null
  connection: ConnectionState

  // 当前录制 take 的实时转录（按 ch）。partial 替换该声道最后一条 partial，final 落定。
  segments: { ch1: LiveSeg[]; ch2: LiveSeg[] }

  // 当前 take。recording 本地（前端按 start/end）；take_id/take_number 从 take.changed 取。
  currentTake: CurrentTake

  // take 列表：Map<take_id, TakeDTO>。getTakes 全量覆盖（权威），take.changed patch-merge 5 字段。
  takes: Map<number, TakeDTO>

  llm: { state: LlmState }

  // take.end 后处理状态条（diarization + Gemma）；done 清空，error 保留到下次录制。null=不显示。
  processing: { phase: TakeProcessingPhase; detail: string | null } | null

  // ── actions ──
  setToken: (t: string | null) => void
  setConnection: (c: ConnectionState) => void
  applyAsr: (ch: 1 | 2, isFinal: boolean, p: AsrMsg) => void
  applyBackfilledSegments: (takeId: number, segments: TranscriptSegmentDTO[]) => void
  applyTakeChanged: (m: TakeChangedMsg) => void
  seedTakes: (list: TakeDTO[]) => void
  setLlm: (state: LlmState) => void
  setTakeProcessing: (m: TakeProcessingMsg) => void
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
  token: readToken(),
  connection: readToken() ? "connecting" : "no-token",

  segments: { ch1: [], ch2: [] },
  currentTake: { ...initialCurrentTake },
  takes: new Map(),
  llm: { state: "idle" },
  processing: null,

  setToken: (t) =>
    set(() => ({
      token: t && t.trim() ? t : null,
      connection: t && t.trim() ? "connecting" : "no-token",
    })),

  setConnection: (c) => set(() => ({ connection: c })),

  applyAsr: (ch, isFinal, p) =>
    set((state) => {
      // 丢弃来自其他 take 的迟到帧（跨 take 泄漏）。两侧 != null 守卫：dev 注入器（take_id=null）
      // 与 currentTake 绑定前的窗口仍正常工作。
      if (
        p.take_id != null &&
        state.currentTake.take_id != null &&
        p.take_id !== state.currentTake.take_id
      ) {
        return {}
      }
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

  // diarization 回填完成：用权威 segments（带 speaker）替换 Live 框里只有 ASR 文本的内容。
  // 守卫：仅当回填的 take 仍是当前/最后绑定的 take 时替换；若已开新 take（take_id 不同且
  // 在录），跳过以免覆盖新 take 的实时转录。
  applyBackfilledSegments: (takeId, segments) =>
    set((state) => {
      const cur = state.currentTake.take_id
      if (cur != null && cur !== takeId && state.currentTake.recording) {
        return {}
      }
      const toSeg = (d: TranscriptSegmentDTO): LiveSeg => ({
        text: d.text,
        speaker: d.speaker,
        start_frame: d.start_frame,
        end_frame: d.end_frame,
        isPartial: false,
      })
      return {
        segments: {
          ch1: segments.filter((s) => s.ch === 1).map(toSeg),
          ch2: segments.filter((s) => s.ch === 2).map(toSeg),
        },
      }
    }),

  applyTakeChanged: (m) =>
    set((state) => {
      const takes = new Map(state.takes)
      const existing = takes.get(m.take_id)
      if (existing) {
        // patch-merge：只覆盖 take.changed 的 5 字段，保留 shot/start_ts/end_ts/notes 等。
        // script_diff 同 seedTakes：不向下降级到 null（与 P1-1 对称的纵深防御）。单条有序 WS 上
        // 发布序为 start(null)→end(null)→L2(non-null)，本不会产生 null-after-non-null，但对齐
        // 防御形状，杜绝该类隐患。
        takes.set(m.take_id, {
          ...existing,
          ...m,
          script_diff: m.script_diff ?? existing.script_diff ?? null,
        })
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

      // currentTake 绑定。take.start 与 take.end 都发 status=tbd / script_diff=null 的 take.changed，
      // 二者不可区分；用 take_id 单调递增（autoincrement）兜底：recording 期间只要来的是 start/end 帧
      // （script_diff===null）且 take_id 比当前更大（或尚未绑定）就（重）绑定。可重绑 → 若低 id 帧抢先
      // 到达，后续更高 id 帧会自我纠正到最新 take。
      let currentTake = state.currentTake
      if (
        currentTake.recording &&
        m.script_diff === null &&
        (currentTake.take_id === null || m.take_id > currentTake.take_id)
      ) {
        currentTake = {
          ...currentTake,
          take_id: m.take_id,
          scene_id: m.scene_id,
          take_number: m.take_number,
        }
      }

      return { takes, currentTake }
    }),

  // getTakes 全量覆盖每个 take_id 条目（getTakes 权威）。例外：script_diff 不向下降级到 null——
  // getTakes 快照读可能早于某条 L2 DB 写，而那条的 WS 帧已把 store 的 script_diff 填好；若 seed
  // 直接覆盖会把刚到的 L2 摘要抹回 null。故 script_diff 取 incoming ?? existing ?? null。
  seedTakes: (list) =>
    set((state) => {
      const takes = new Map(state.takes)
      for (const t of list) {
        const existing = takes.get(t.take_id)
        takes.set(t.take_id, {
          ...t,
          script_diff: t.script_diff ?? existing?.script_diff ?? null,
        })
      }
      return { takes }
    }),

  setLlm: (state) => set(() => ({ llm: { state } })),

  // take.end 后处理状态条：done 清空；diarizing/summarizing/error 显示。
  setTakeProcessing: (m) =>
    set(() => ({
      processing: m.phase === "done" ? null : { phase: m.phase, detail: m.detail },
    })),

  startRecordingLocal: (sceneId, shot) =>
    set(() => ({
      segments: { ch1: [], ch2: [] },
      processing: null, // 新录制开始，清掉上一条 take 的处理状态/错误
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
