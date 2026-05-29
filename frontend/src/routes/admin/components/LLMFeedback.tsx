import { Badge } from "@/components/ui/badge"
import { Card, CardContent } from "@/components/ui/card"
import { mutedCard } from "@/lib/styles"
import type { LineMatch, TakeDTO } from "@/types/api"
import { useSessionStore } from "@/store/session"

const DIFF_LABEL: Record<LineMatch["diff_type"], string> = {
  match: "匹配",
  missing: "漏词",
  substitution: "改词",
  insertion: "加词",
}

export function LLMFeedback() {
  const takesMap = useSessionStore((s) => s.takes)

  // 最近被更新的、有 script_diff 的 take：按 updated_at 降序（L2 写库会刷新 updated_at），
  // updated_at 缺失（WS 部分条目 0）时退回 take_number 降序。
  const latest: TakeDTO | undefined = Array.from(takesMap.values())
    .filter((t) => t.script_diff != null)
    .sort(
      (a, b) => (b.updated_at ?? 0) - (a.updated_at ?? 0) || b.take_number - a.take_number
    )[0]

  if (!latest || !latest.script_diff) {
    return (
      <div className="py-4">
        <p className="text-xs text-muted-foreground text-center pt-2">
          take 结束后由 L2 推送
        </p>
      </div>
    )
  }

  const diff = latest.script_diff

  return (
    <div className="py-4 space-y-3">
      <Card className={mutedCard}>
        <CardContent className="p-4 space-y-2">
          <div className="flex items-center gap-2">
            <Badge variant="secondary" className="font-mono uppercase">
              summary
            </Badge>
            <span className="font-mono text-[10px] text-muted-foreground">
              Scene {latest.scene_id} · Take {latest.take_number}
            </span>
          </div>
          <p className="text-sm leading-relaxed text-foreground">
            {diff.script_diff_summary ?? "无偏差摘要（台词匹配 / 无剧本）"}
          </p>
        </CardContent>
      </Card>

      {diff.line_matches.length > 0 && (
        <Card className={mutedCard}>
          <CardContent className="p-4 space-y-2">
            <Badge variant="secondary" className="font-mono uppercase">
              diff
            </Badge>
            <div className="space-y-1">
              {diff.line_matches.map((lm, i) => (
                <div key={i} className="flex items-baseline gap-2 text-xs">
                  <span className="font-mono text-muted-foreground w-10 flex-shrink-0">
                    {lm.line_no >= 0 ? `L${lm.line_no}` : "—"}
                  </span>
                  <span className="text-muted-foreground flex-shrink-0">
                    {DIFF_LABEL[lm.diff_type]}
                  </span>
                  {lm.detail && <span className="text-foreground">{lm.detail}</span>}
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      )}

      <p className="text-xs text-muted-foreground text-center pt-2">
        每次 take 结束后由 L2 推送
      </p>
    </div>
  )
}
