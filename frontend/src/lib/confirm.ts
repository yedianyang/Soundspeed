import type { NpExtractionDTO } from "@/types/api"

// ── 验证「次」输入：空串→[]（当前条，合法）；非空则要求逗号分隔的正整数（>0）。
// 返回 null=非法，数组=合法（含空数组）。export 供测试直接覆盖。
export function parseTakeOrdinals(raw: string): number[] | null {
  const trimmed = raw.trim()
  if (trimmed === "") return []
  const parts = trimmed.split(",").map((s) => s.trim())
  const nums = parts.map(Number)
  if (nums.some((n) => !Number.isInteger(n) || n <= 0)) return null
  return nums
}

// ── 构造确认卡提交的 extraction：场/镜数字化 + 次数组覆盖。
// 用户给了显式次号（parsedTakes 非空）→ deictic 置 "none"：
// 后端 resolve_targets 里 deictic 优先于 take_ordinals，不清会让用户填的编号被无视
// （extract_np schema 约定「用了编号=deictic none」，后端路由有同样归一兜底，这里保乐观 UI 一致）。
export function buildConfirmedExtraction(
  extraction: NpExtractionDTO,
  scene: string,
  shot: string,
  parsedTakes: number[],
): NpExtractionDTO {
  return {
    ...extraction,
    scene_ordinal: Number(scene),
    shot_ordinal: Number(shot),
    take_ordinals: parsedTakes,
    ...(parsedTakes.length > 0 ? { deictic: "none" as const } : {}),
  }
}
