import { describe, expect, it } from "vitest"
import { parseTakeOrdinals, buildConfirmedExtraction } from "@/lib/confirm"
import type { NpExtractionDTO } from "@/types/api"

// ── fixtures ──

const baseExtraction: NpExtractionDTO = {
  scene_ordinal: 1,
  shot_ordinal: 2,
  take_ordinals: [],
  deictic: "prev",
  mark: "ng",
  note_text: "灯光偏暗",
  note_category: "issue",
}

// ── parseTakeOrdinals（迁移保护，自 InlineFeedbackQueue 搬出）──

describe("parseTakeOrdinals", () => {
  it("空串→[]（当前条，合法）", () => {
    expect(parseTakeOrdinals("")).toEqual([])
  })

  it('"2,3"→[2,3]', () => {
    expect(parseTakeOrdinals("2,3")).toEqual([2, 3])
  })

  it('非法输入（"0"/"a"/"1,x"）→null', () => {
    expect(parseTakeOrdinals("0")).toBeNull()
    expect(parseTakeOrdinals("a")).toBeNull()
    expect(parseTakeOrdinals("1,x")).toBeNull()
  })
})

// ── buildConfirmedExtraction ──

describe("buildConfirmedExtraction", () => {
  it("用户给了显式次号时清 deictic：deictic=prev + parsedTakes=[3] → deictic=none", () => {
    const out = buildConfirmedExtraction(baseExtraction, "1", "2", [3])
    expect(out.deictic).toBe("none")
    expect(out.take_ordinals).toEqual([3])
  })

  it("没填次号（parsedTakes=[]）不乱清：deictic=current 保持", () => {
    const out = buildConfirmedExtraction({ ...baseExtraction, deictic: "current" }, "1", "2", [])
    expect(out.deictic).toBe("current")
    expect(out.take_ordinals).toEqual([])
  })

  it("场/镜字符串数字化：scene=7 / shot=2", () => {
    const out = buildConfirmedExtraction(baseExtraction, "7", "2", [])
    expect(out.scene_ordinal).toBe(7)
    expect(out.shot_ordinal).toBe(2)
  })
})
