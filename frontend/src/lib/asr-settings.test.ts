import { describe, expect, it } from "vitest"

import { asrSelectModel, type AsrEngineInfo } from "@/lib/asr-settings"

const ENGINES: AsrEngineInfo[] = [
  { id: "whisper", label: "whisper.cpp", languages: ["zh", "en", "auto"], installed: true },
  { id: "funasr", label: "FunASR", languages: ["zh"], installed: true },
]

// asrSelectModel:双 dropdown 的派生视图——语言列表随引擎联动、录制中禁用引擎切换。
describe("asrSelectModel", () => {
  it("whisper:语言列表 zh/en/auto", () => {
    const m = asrSelectModel({ engines: ENGINES, engine: "whisper", isRecording: false })
    expect(m.languageOptions).toEqual(["zh", "en", "auto"])
    expect(m.engineDisabled).toBe(false)
  })

  it("funasr:语言列表只剩 zh", () => {
    const m = asrSelectModel({ engines: ENGINES, engine: "funasr", isRecording: false })
    expect(m.languageOptions).toEqual(["zh"])
  })

  it("录制中:引擎下拉禁用", () => {
    const m = asrSelectModel({ engines: ENGINES, engine: "whisper", isRecording: true })
    expect(m.engineDisabled).toBe(true)
  })

  it("engine 为 null(未启用)→ 回退 whisper 的语言列表", () => {
    const m = asrSelectModel({ engines: ENGINES, engine: null, isRecording: false })
    expect(m.languageOptions).toEqual(["zh", "en", "auto"])
  })

  it("engines 为空(旧后端)→ 语言回退 zh/en/auto,引擎选项为空", () => {
    const m = asrSelectModel({ engines: [], engine: null, isRecording: false })
    expect(m.engineOptions).toEqual([])
    expect(m.languageOptions).toEqual(["zh", "en", "auto"])
  })

  it("engine 指向不存在的 id → 语言回退 zh/en/auto", () => {
    const m = asrSelectModel({ engines: [ENGINES[0]], engine: "funasr", isRecording: false })
    expect(m.languageOptions).toEqual(["zh", "en", "auto"])
  })
})
