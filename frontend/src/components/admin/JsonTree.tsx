/**
 * JsonTree —— 递归 JSON 树，用原生 <details>/<summary> 实现逐节点折叠。
 * 入参 raw 是原始 JSON 字符串。parse 失败 fallback 到 <pre> 原串。
 */

import React, { useState } from "react"
import { tryParseJson } from "@/lib/jsonUtils"

// ── 类型 ────────────────────────────────────────────────────────────────────

type JsonValue =
  | string
  | number
  | boolean
  | null
  | JsonValue[]
  | { [k: string]: JsonValue }

// ── 递归节点 ────────────────────────────────────────────────────────────────

const INDENT = 12 // px per depth level

interface JsonNodeProps {
  value: JsonValue
  keyName?: string
  depth: number
}

// 对象/数组节点 —— 用受控 <details> 折叠。单拆出来让 useState 无条件调用
//（满足 rules-of-hooks；JsonNode 里按 value 类型分支会让 hook 变条件调用）。
function JsonBranch({ value, keyName, depth }: { value: JsonValue[] | { [k: string]: JsonValue }; keyName?: string; depth: number }) {
  const pl = depth * INDENT
  const isArray = Array.isArray(value)
  const entries = isArray
    ? (value as JsonValue[]).map((v, i) => [String(i), v] as [string, JsonValue])
    : Object.entries(value as Record<string, JsonValue>)
  const count = entries.length
  // 长数组（>20）默认收起，其余默认展开
  const defaultOpen = !isArray || count <= 20
  const [open, setOpen] = useState(defaultOpen)

  const summary = isArray ? `[${count}]` : `{${count}}`

  return (
    <details
      open={open}
      onToggle={(e) => setOpen((e.currentTarget as HTMLDetailsElement).open)}
      style={{ paddingLeft: pl }}
      className="my-[1px]"
    >
      <summary className="cursor-pointer select-none list-none font-mono text-[10px] text-muted-foreground hover:text-foreground flex items-baseline gap-[2px]">
        {/* 折叠三角（手动渲染，去掉原生 marker，随开合切换） */}
        <span className="inline-block w-[10px] shrink-0 text-muted-foreground/60">
          {open ? "▾" : "▸"}
        </span>
        {keyName !== undefined && (
          <span className="text-foreground/80">{keyName}:&nbsp;</span>
        )}
        <span className="text-muted-foreground/60">{summary}</span>
      </summary>

      <div className="font-mono text-[10px]">
        {entries.map(([k, v]) => (
          <JsonNode key={k} value={v} keyName={isArray ? undefined : k} depth={depth + 1} />
        ))}
      </div>
    </details>
  )
}

function JsonNode({ value, keyName, depth }: JsonNodeProps) {
  const pl = depth * INDENT

  // 对象或数组 —— 委托给受控折叠组件
  if (value !== null && typeof value === "object") {
    return <JsonBranch value={value} keyName={keyName} depth={depth} />
  }

  // 原始值 —— 一行
  let valueEl: React.ReactNode

  if (value === null) {
    valueEl = (
      <span className="text-muted-foreground/50 italic">null</span>
    )
  } else if (typeof value === "boolean") {
    valueEl = (
      <span className="text-amber-500/80 dark:text-amber-400/80">{String(value)}</span>
    )
  } else if (typeof value === "number") {
    valueEl = (
      <span className="text-sky-600/90 dark:text-sky-400/80">{value}</span>
    )
  } else {
    // string
    valueEl = (
      <span className="text-emerald-700/80 dark:text-emerald-400/70">&quot;{value}&quot;</span>
    )
  }

  return (
    <div
      style={{ paddingLeft: pl + INDENT }}
      className="font-mono text-[10px] leading-[1.6] text-foreground/90"
    >
      {keyName !== undefined && (
        <span className="text-foreground/70">{keyName}:&nbsp;</span>
      )}
      {valueEl}
    </div>
  )
}

// ── 公开组件 ────────────────────────────────────────────────────────────────

interface JsonTreeProps {
  raw: string
}

export function JsonTree({ raw }: JsonTreeProps) {
  const result = tryParseJson(raw)

  if (!result.ok) {
    return (
      <pre className="whitespace-pre-wrap break-all font-mono text-[10px] text-foreground/80">
        {raw}
      </pre>
    )
  }

  return (
    <div className="json-tree font-mono text-[10px] leading-[1.6]">
      <JsonNode value={result.value} depth={0} />
    </div>
  )
}
