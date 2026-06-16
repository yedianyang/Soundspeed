import { describe, expect, it } from "vitest"
import { shouldShowParseResult } from "./script-panel-banner"

// 结果横幅（✅ 已导入 N 场 / ✗ 解析失败）应只在「本会话刚解析」时显示。
// 持久化的 parsed 上传记录在冷启动时不得复活这条横幅。
describe("shouldShowParseResult", () => {
  it("hides the result banner on cold start (persisted parsed record, no session parse)", () => {
    // 库里有一条历史 parsed 记录，本会话没解析过 → sessionUploadId 为 null。
    expect(
      shouldShowParseResult({ status: "parsed", upload_id: 7 }, null, null),
    ).toBe(false)
  })

  it("shows the result banner after a parse in the current session", () => {
    expect(
      shouldShowParseResult({ status: "parsed", upload_id: 7 }, 7, null),
    ).toBe(true)
  })

  it("shows the error banner after a failed parse in the current session", () => {
    expect(
      shouldShowParseResult({ status: "error", upload_id: 7 }, 7, null),
    ).toBe(true)
  })

  it("hides the error banner on cold start", () => {
    expect(
      shouldShowParseResult({ status: "error", upload_id: 7 }, null, null),
    ).toBe(false)
  })

  it("hides the banner once dismissed in the current session", () => {
    expect(
      shouldShowParseResult({ status: "parsed", upload_id: 7 }, 7, 7),
    ).toBe(false)
  })

  it("does not show for an in-progress or not-yet-parsed upload", () => {
    expect(
      shouldShowParseResult({ status: "uploaded", upload_id: 7 }, 7, null),
    ).toBe(false)
    expect(
      shouldShowParseResult({ status: "parsing", upload_id: 7 }, 7, null),
    ).toBe(false)
  })
})
