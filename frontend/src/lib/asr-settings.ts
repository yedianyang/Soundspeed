// 设置页「转录引擎与语言」双 dropdown 的派生视图(纯函数,便于测试)。
// 语言列表随引擎联动;录制中禁止切引擎(不可热切,同一 take 不混两引擎文本风格)。

export interface AsrEngineInfo {
  id: string
  label: string
  languages: string[]
  installed: boolean
}

const FALLBACK_LANGUAGES = ["zh", "en", "auto"]

export interface AsrSelectModel {
  engineOptions: AsrEngineInfo[]
  languageOptions: string[]
  engineDisabled: boolean
}

export function asrSelectModel(args: {
  engines: AsrEngineInfo[]
  engine: string | null
  isRecording: boolean
}): AsrSelectModel {
  const current = args.engines.find((e) => e.id === (args.engine ?? "whisper"))
  return {
    engineOptions: args.engines,
    languageOptions: current?.languages ?? FALLBACK_LANGUAGES,
    engineDisabled: args.isRecording,
  }
}
