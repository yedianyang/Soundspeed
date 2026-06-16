/**
 * JSON 解析工具 —— 可纯测逻辑，独立于 React 组件。
 */

type JsonValue =
  | string
  | number
  | boolean
  | null
  | JsonValue[]
  | { [k: string]: JsonValue }

/**
 * 尝试解析 JSON 字符串。
 * 返回 { ok: true, value } 或 { ok: false }。
 */
export function tryParseJson(raw: string): { ok: true; value: JsonValue } | { ok: false } {
  try {
    return { ok: true, value: JSON.parse(raw) as JsonValue }
  } catch {
    return { ok: false }
  }
}
