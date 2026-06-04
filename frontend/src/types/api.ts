// 后端 boundary 类型（snake_case）。派生自 backend/db/dal.py（Take / TranscriptSegment）
// 与 backend/core/events.py（WS payload）。应用内部只在此层用 snake_case，其余读自 typed store。

export type TakeStatus = "keeper" | "ng" | "hold" | "tbd"

// ── Scene DTO（GET /api/v1/scenes，spec v0.3 §2.5）──

export interface SceneDTO {
  scene_id: number
  scene_code: string
  description: string | null
  shoot_date: string | null
  is_active: number // SQLite 0 | 1，非 bool；pickActiveScene 的 truthy 检查照常工作
  created_at: number
  int_ext: string | null // slugline 内外景：室内 / 室外（v2，可能 null）
  time_of_day: string | null // slugline 时间：日 / 夜 …（v2，可能 null）
  location: string | null // slugline 地点：街道 / 咖啡馆 …（v2，可能 null）
}

// ── Scene 写操作（2.C）──

// POST /api/v1/scenes body。scene_code 必填，其余可选 slugline / 元数据。
export interface CreateSceneBody {
  scene_code: string
  description?: string
  shoot_date?: string
  int_ext?: string
  time_of_day?: string
  location?: string
}

// POST /api/v1/scenes 响应。created 区分新建 / 复用（两者都 200）。
export interface CreateSceneResult {
  scene_id: number
  scene_code: string
  created: boolean
  is_active: number
}

// POST /api/v1/scenes/{scene_id}/activate 响应。
export interface ActivateSceneResult {
  scene_id: number
  scene_code: string
}

// PATCH /api/v1/takes/{id} body，全可选。status ∈ keeper/ng/hold/tbd。
export interface PatchTakeBody {
  status?: TakeStatus
  scene_id?: number
  shot?: string | null
  take_number?: number
  notes?: string | null
}

// ── Script DTO（GET /api/v1/scenes/{scene_id}/script，spec 2026-06-01 §2.3）──

export interface ScriptLineDTO {
  line_no: number
  character: string | null // null → 动作描述（无说话人）；有值 → 台词说话人
  text: string
}

export interface ScriptDTO {
  script_id: number
  version: number
  lines: ScriptLineDTO[]
}

// ── L2 输出：takes.script_diff JSON 顶层形状（docs/specs/2026-05-27-l2-pipeline.md §）──

export interface LineMatch {
  line_no: number // insertion 时为 -1
  diff_type: "match" | "missing" | "substitution" | "insertion"
  detail: string | null
}

// L2 修正输出：原始转录 → 对齐剧本后的文本。diff 显示的主内容。
export interface CorrectedSegment {
  idx: number
  original: string
  corrected: string
}

export interface ScriptDiff {
  script_diff_summary: string | null // 无剧本场景可为 null
  line_matches: LineMatch[]
  corrected_segments?: CorrectedSegment[]
}

// ── Take DTO（dal.Take 投影）──
// performer_issues / audio_quality 属 NP Pipeline 输出，1.J–1.L 不暴露，有意省略（spec §2.1）。

export interface TakeDTO {
  take_id: number
  scene_id: number
  shot: string | null
  take_number: number
  take_suffix: string // 冲突后缀：'' / '+' / '++'…，显示时拼成 `Take 3+`（formatTakeLabel）
  start_ts: number
  end_ts: number | null
  status: TakeStatus
  script_diff: ScriptDiff | null
  notes: string | null
  deleted_at: number | null // 软删时间戳，null 表示未删除；restore 后回 null
  created_at: number
  updated_at: number
}

// 转录片段（dal.TranscriptSegment 投影）。start_frame / end_frame 单位毫秒。
export interface TranscriptSegmentDTO {
  segment_id: number
  ch: 1 | 2
  speaker: string | null
  text: string
  start_frame: number
  end_frame: number
}

// GET /api/v1/takes/{id} 返回：TakeDTO + segments。
export interface TakeDetailDTO extends TakeDTO {
  segments: TranscriptSegmentDTO[]
}

// ── WS 信封 + payload（events.py）──

export interface WsEnvelope {
  topic: string
  payload: unknown
}

// asr.partial / asr.final 的 payload。⚠ ch 不在 payload 里，编码在 topic 后缀（asr.partial.ch1）。
export interface AsrMsg {
  text: string
  start_frame: number
  end_frame: number
  speaker: string | null
  take_id: number | null
  is_partial: boolean
}

// take.changed 只携带这 5 个字段（spec §3.4 patch-merge 源，刻意 Pick 在类型层锁死契约）。
export type TakeChangedMsg = Pick<
  TakeDTO,
  "take_id" | "scene_id" | "take_number" | "status" | "script_diff"
>

// take.deleted（2.C）：删某条 take 后广播，让历史列表移除该条。
export interface TakeDeletedMsg {
  take_id: number
  scene_id: number
}

// scene.changed（2.C）：建/切场后广播，让场次列表 + 活跃场显示刷新。
export interface SceneChangedMsg {
  scene_id: number
  scene_code: string
  is_active: number // SQLite 0 | 1，与 SceneDTO.is_active 对齐
}

export type LlmState = "idle" | "loading" | "running" | "downloading"

export interface LlmStatusMsg {
  state: LlmState
  task_type: string | null
  take_id: number | null
}

// ── Note DTO（4.C POST /notes + GET /takes/{id}/notes）──

export interface NoteDTO {
  event_id: number
  take_id: number
  scene_code: string | null
  take_number: number | null
  category: string // "note" | "issue" | "keeper" | "ng" | "hold"
  content: string
  raw_text: string
  ts: number
}

export interface NoteListResponse {
  take_id: number
  notes_aggregated: string | null
  events: NoteDTO[]
}

// POST /notes 202 响应（NP Pipeline 非阻塞归置）
export interface NoteCreateResponse {
  status: "processing"
  category: string
  content: string
}

// 前端 pending note（已提交、等待 LLM 归置）
export interface PendingNote {
  ts: number
  category: string
  content: string
}

// WS note.processed payload
export interface NoteProcessedMsg {
  event_id: number
  take_id: number
  category: string
  content: string
  ts: number
}
