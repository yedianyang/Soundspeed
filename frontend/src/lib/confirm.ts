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
  }
}
