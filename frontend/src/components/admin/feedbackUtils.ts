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
