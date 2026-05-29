import type { Status } from "@/types/take"

export const STATUS_DOT: Record<Status, string> = {
  keeper: "bg-green-500",
  ng: "bg-destructive",
  hold: "bg-primary",
  tbd: "bg-muted-foreground",
  recording: "bg-destructive animate-pulse",
}

export const STATUS_LABEL: Record<Status, string> = {
  keeper: "KEEP",
  ng: "NG",
  hold: "PASS",
  tbd: "TBD",
  recording: "REC",
}

export const MARK_ORDER: Status[] = ["ng", "keeper", "hold"]

export const SPEAKER_OPTIONS = ["SZA", "YY", "Unknown"]

export const SPEAKER_DOT: Record<string, string> = {
  SZA: "bg-primary",
  YY: "bg-secondary-foreground",
  Unknown: "bg-muted-foreground",
}

export const SPEAKER_TEXT: Record<string, string> = {
  SZA: "text-primary",
  YY: "text-secondary-foreground",
  Unknown: "text-muted-foreground",
}

// ---- 实时转录 speaker 分色 ----
// 真实 diarize 输出是 speaker_0 / speaker_1 / ...，上面的 SZA/YY 命名映射是 mock 期的，
// 对 live 数据兜不住。改用确定性哈希把任意 speaker 字符串映射到固定调色板，保证同一 speaker
// 每次渲染同色。null（ch2 设计上无 speaker）→ muted。

const SPEAKER_PALETTE = [
  "text-primary",
  "text-secondary-foreground",
  "text-green-600",
  "text-orange-600",
  "text-purple-600",
] as const

export function speakerColor(speaker: string | null): string {
  if (!speaker) return "text-muted-foreground"
  // 简单稳定哈希（djb2 变体），取模映射到调色板。
  let hash = 0
  for (let i = 0; i < speaker.length; i++) {
    hash = (hash * 31 + speaker.charCodeAt(i)) | 0
  }
  return SPEAKER_PALETTE[Math.abs(hash) % SPEAKER_PALETTE.length]
}
