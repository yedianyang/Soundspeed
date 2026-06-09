import { describe, expect, it } from "vitest"

import { formatElapsed, formatTakeLabel } from "@/lib/utils"

// formatTakeLabel：take_number 拼冲突后缀 take_suffix。显示编码契约，#63 同类。
describe("formatTakeLabel", () => {
  it("无后缀：suffix='' → '3'", () => {
    expect(formatTakeLabel({ take_number: 3, take_suffix: "" })).toBe("3")
  })

  it("有后缀：suffix='+' → '3+'，'++' → '3++'", () => {
    expect(formatTakeLabel({ take_number: 3, take_suffix: "+" })).toBe("3+")
    expect(formatTakeLabel({ take_number: 3, take_suffix: "++" })).toBe("3++")
  })

  it("suffix 缺省 / null → 只显示数字（store 部分字段 take 也能拼）", () => {
    expect(formatTakeLabel({ take_number: 5 })).toBe("5")
    expect(formatTakeLabel({ take_number: 5, take_suffix: null })).toBe("5")
  })

  it("take_number 为 null → 占位 '—'", () => {
    expect(formatTakeLabel({ take_number: null })).toBe("—")
    expect(formatTakeLabel({ take_number: null, take_suffix: "+" })).toBe("—")
  })

  it("take_number 为 0 仍渲染（!= null，不被 falsy 吞掉）", () => {
    expect(formatTakeLabel({ take_number: 0 })).toBe("0")
  })
})

// formatElapsed：录制时长 mm:ss / h:mm:ss。
describe("formatElapsed", () => {
  it("不足 1 小时：mm:ss，秒补零", () => {
    expect(formatElapsed(0)).toBe("0:00")
    expect(formatElapsed(5)).toBe("0:05")
    expect(formatElapsed(75)).toBe("1:15")
  })

  it("满 1 小时：h:mm:ss，分秒都补零", () => {
    expect(formatElapsed(3600)).toBe("1:00:00")
    expect(formatElapsed(3661)).toBe("1:01:01")
  })
})
