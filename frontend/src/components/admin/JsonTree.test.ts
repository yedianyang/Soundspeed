import { describe, it, expect } from "vitest"
import { tryParseJson } from "@/lib/jsonUtils"

describe("tryParseJson", () => {
  it("解析合法 JSON 对象，返回 ok=true 和 value", () => {
    const result = tryParseJson('{"foo": 1, "bar": "baz"}')
    expect(result.ok).toBe(true)
    if (result.ok) {
      expect(result.value).toEqual({ foo: 1, bar: "baz" })
    }
  })

  it("解析合法 JSON 数组，返回 ok=true 和 value", () => {
    const result = tryParseJson('[1, "two", true, null]')
    expect(result.ok).toBe(true)
    if (result.ok) {
      expect(result.value).toEqual([1, "two", true, null])
    }
  })

  it("解析嵌套对象，value 结构正确", () => {
    const raw = JSON.stringify({ a: { b: [1, 2, 3] } })
    const result = tryParseJson(raw)
    expect(result.ok).toBe(true)
    if (result.ok) {
      expect((result.value as Record<string, unknown>).a).toEqual({ b: [1, 2, 3] })
    }
  })

  it("解析 null 字面量，返回 ok=true，value=null", () => {
    const result = tryParseJson("null")
    expect(result.ok).toBe(true)
    if (result.ok) {
      expect(result.value).toBeNull()
    }
  })

  it("非法 JSON 字符串返回 ok=false（fallback 触发）", () => {
    const result = tryParseJson("not json at all")
    expect(result.ok).toBe(false)
  })

  it("空字符串返回 ok=false", () => {
    const result = tryParseJson("")
    expect(result.ok).toBe(false)
  })

  it("截断的 JSON 返回 ok=false", () => {
    const result = tryParseJson('{"key": "val"')
    expect(result.ok).toBe(false)
  })

  it("纯文本（非 JSON）返回 ok=false", () => {
    const result = tryParseJson("Scene 3 / Shot 2 / Take 1")
    expect(result.ok).toBe(false)
  })
})
