import { describe, it, expect } from "vitest"
import { clearPerfBufferIfLarge } from "./dev-perf-guard"

// 伪造 Performance：只记录 measure 条数与 clear 调用，避免碰真实浏览器 API。
function fakePerf(measureCount: number) {
  const calls = { clearMeasures: 0, clearMarks: 0 }
  const perf = {
    getEntriesByType: (t: string) =>
      t === "measure" ? new Array(measureCount).fill(0) : [],
    clearMeasures: () => {
      calls.clearMeasures++
    },
    clearMarks: () => {
      calls.clearMarks++
    },
  } as unknown as Performance
  return { perf, calls }
}

describe("clearPerfBufferIfLarge", () => {
  it("超过阈值就清空 measure/mark 并返回 true", () => {
    const { perf, calls } = fakePerf(10_001)
    expect(clearPerfBufferIfLarge(perf, 10_000)).toBe(true)
    expect(calls.clearMeasures).toBe(1)
    expect(calls.clearMarks).toBe(1)
  })

  it("等于阈值不清（边界）", () => {
    const { perf, calls } = fakePerf(10_000)
    expect(clearPerfBufferIfLarge(perf, 10_000)).toBe(false)
    expect(calls.clearMeasures).toBe(0)
    expect(calls.clearMarks).toBe(0)
  })

  it("低于阈值不清、返回 false", () => {
    const { perf, calls } = fakePerf(5)
    expect(clearPerfBufferIfLarge(perf, 10_000)).toBe(false)
    expect(calls.clearMeasures).toBe(0)
  })
})
