import { describe, expect, it } from "vitest"

import {
  DEFAULT_FILENAME_FORMAT,
  formatFileName,
  type FileNameFormat,
} from "@/lib/filename-format"

// formatFileName：把一条 take 的场/镜/次按格式拼成文件名串。
// 这里测的是「编码契约」——scene_code 抽数字、缺段跳过、补零——正是 #63（Scene_1 vs 1）那类
// 编码 bug 的落点。特征化测试：锁住当前正确行为，改动编码逻辑若意外破坏立即变红。
describe("formatFileName", () => {
  it("默认格式 Sc1_S1_T001：scene_code 抽数字、take 补零到 3 位", () => {
    const name = formatFileName(
      { scene_code: "Scene_1", shot: "1", take_number: 1 },
      DEFAULT_FILENAME_FORMAT,
    )
    expect(name).toBe("Sc1_S1_T001")
  })

  it("#63 核心：scene_code 只取数字部分，不把 'Scene_' 前缀带进来", () => {
    const fmt = DEFAULT_FILENAME_FORMAT
    // "Scene_1" → 数字 1；"3A" → 3；纯 "1" → 1（抽不到额外数字就用它）
    expect(formatFileName({ scene_code: "Scene_1", take_number: 1 }, fmt)).toBe("Sc1_T001")
    expect(formatFileName({ scene_code: "3A", take_number: 1 }, fmt)).toBe("Sc3_T001")
    expect(formatFileName({ scene_code: "1", take_number: 1 }, fmt)).toBe("Sc1_T001")
  })

  it("抽不到数字的 scene_code 原样保留（digitsOf 兜底）", () => {
    expect(
      formatFileName({ scene_code: "外景", take_number: 1 }, DEFAULT_FILENAME_FORMAT),
    ).toBe("Sc外景_T001")
  })

  it("缺失段跳过、不留空分隔：无 shot → Sc1_T001", () => {
    const name = formatFileName(
      { scene_code: "Scene_1", shot: null, take_number: 1 },
      DEFAULT_FILENAME_FORMAT,
    )
    expect(name).toBe("Sc1_T001")
  })

  it("空串 scene_code（falsy）整段跳过，不产生前导分隔符", () => {
    const name = formatFileName(
      { scene_code: "", shot: "2", take_number: 5 },
      DEFAULT_FILENAME_FORMAT,
    )
    expect(name).toBe("S2_T005")
  })

  it("take_number 为 0 仍渲染（!= null 守卫，不被 falsy 吞掉）", () => {
    const name = formatFileName(
      { scene_code: "1", take_number: 0 },
      DEFAULT_FILENAME_FORMAT,
    )
    expect(name).toBe("Sc1_T000")
  })

  it("pad=0 不补零；自定义前缀与分隔符如实拼接", () => {
    const fmt: FileNameFormat = {
      scene: { prefix: "Scene", pad: 0 },
      shot: { prefix: "Shot", pad: 0 },
      take: { prefix: "Take", pad: 0 },
      sep: " · ",
    }
    expect(
      formatFileName({ scene_code: "12", shot: "3", take_number: 7 }, fmt),
    ).toBe("Scene12 · Shot3 · Take7")
  })

  it("三段全缺 → 空串", () => {
    expect(formatFileName({}, DEFAULT_FILENAME_FORMAT)).toBe("")
  })
})
