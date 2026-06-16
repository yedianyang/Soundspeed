// 解析结果横幅（✅ 已导入 N 场 / ✗ 解析失败）的显示判定。
// latestUpload 来自常驻轮询、跨重启持久化；若仅凭它显示，冷启动会复活旧记录的横幅。
// 故额外按「本会话解析过的 upload_id」门控：sessionUploadId 是内存态，reload 即清零。
export function shouldShowParseResult(
  latest: { status: string; upload_id: number },
  sessionUploadId: number | null,
  dismissedId: number | null,
): boolean {
  if (latest.status !== "parsed" && latest.status !== "error") return false
  if (latest.upload_id !== sessionUploadId) return false
  return latest.upload_id !== dismissedId
}
