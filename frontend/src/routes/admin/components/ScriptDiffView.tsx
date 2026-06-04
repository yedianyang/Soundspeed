import type { LineMatch, ScriptDiff } from "@/types/api"

const DIFF_LABEL: Record<LineMatch["diff_type"], string> = {
  match: "匹配",
  missing: "漏词",
  substitution: "改词",
  insertion: "加词",
}

const DIFF_ORDER: LineMatch["diff_type"][] = [
  "match",
  "substitution",
  "missing",
  "insertion",
]

// L2 script_diff 显示。优先级：corrected_segments（原→改，主内容）> line_matches 计数摘要 >
// script_diff_summary（模型 prose，弱化）。降级：无 diff → 「L2 未完成 / 无剧本」；
// 无 corrected_segments 且无 line_matches → 「无偏差」。
export function ScriptDiffView({ diff }: { diff: ScriptDiff | null }) {
  if (!diff) {
    return <p className="text-sm text-muted-foreground/60">L2 未完成 / 无剧本</p>
  }

  // line_matches 理论上后端总会带（orchestrator 写 script_diff 时必填），但 v3 老库 / 降级路径
  // 可能存到只有 summary、缺 line_matches 的 script_diff。缺这层守卫时 .filter 会抛、整页白屏
  // （删除最新 take 把这种坏 diff 顶进 LLMFeedback / 展开卡片即触发）。?? [] 兜底，缺字段当无比对。
  const corrected = diff.corrected_segments ?? []
  const matches = diff.line_matches ?? []
  const detailMatches = matches.filter((m) => m.detail)

  if (corrected.length === 0 && matches.length === 0) {
    return <p className="text-sm text-muted-foreground/60">无偏差</p>
  }

  // line_matches 按 diff_type 计数。
  const counts = matches.reduce<Record<string, number>>((acc, m) => {
    acc[m.diff_type] = (acc[m.diff_type] ?? 0) + 1
    return acc
  }, {})

  return (
    <div className="space-y-3">
      {/* a. corrected_segments —— 主内容：原 → 改 */}
      {corrected.length > 0 && (
        <div className="space-y-2">
          {corrected.map((seg) => (
            <div key={seg.idx} className="space-y-0.5">
              <p className="flex gap-1.5 text-sm">
                <span className="text-[10px] font-mono text-muted-foreground/70 mt-0.5 flex-shrink-0">
                  原
                </span>
                <span className="text-muted-foreground line-through decoration-muted-foreground/40">
                  {seg.original}
                </span>
              </p>
              <p className="flex gap-1.5 text-sm">
                <span className="text-[10px] font-mono text-primary mt-0.5 flex-shrink-0">
                  改
                </span>
                <span className="text-foreground">{seg.corrected}</span>
              </p>
            </div>
          ))}
        </div>
      )}

      {/* b. line_matches —— 一行计数摘要 */}
      {matches.length > 0 && (
        <p className="text-xs text-muted-foreground">
          剧本比对 {matches.length} 行
          {DIFF_ORDER.map((d) => ` · ${DIFF_LABEL[d]} ${counts[d] ?? 0}`).join("")}
        </p>
      )}

      {/* 若有非空 detail 的 line_match，补充展示 */}
      {detailMatches.length > 0 && (
        <div className="space-y-1">
          {detailMatches.map((lm, i) => (
            <div key={i} className="flex items-baseline gap-2 text-xs">
              <span className="font-mono text-muted-foreground w-10 flex-shrink-0">
                {lm.line_no >= 0 ? `L${lm.line_no}` : "—"}
              </span>
              <span className="text-muted-foreground flex-shrink-0">
                {DIFF_LABEL[lm.diff_type]}
              </span>
              <span className="text-foreground">{lm.detail}</span>
            </div>
          ))}
        </div>
      )}

      {/* c. script_diff_summary —— 模型 prose，弱化 */}
      {diff.script_diff_summary && (
        <p className="text-[11px] text-muted-foreground/70 leading-relaxed">
          <span className="font-mono mr-1">L2 摘要</span>
          {diff.script_diff_summary}
        </p>
      )}
    </div>
  )
}
