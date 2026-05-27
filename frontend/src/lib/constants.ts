import type { Status } from "@/types/take"

export const STATUS_DOT: Record<Status, string> = {
  keeper: "bg-emerald-500",
  ng: "bg-destructive",
  hold: "bg-primary",
  recording: "bg-red-500 animate-pulse",
}

export const STATUS_LABEL: Record<Status, string> = {
  keeper: "KEEP",
  ng: "NG",
  hold: "PASS",
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
