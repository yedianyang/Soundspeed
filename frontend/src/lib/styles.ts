import { cn } from "@/lib/utils"

// ---- 业务层共享视觉 token ----
// 收敛散落在各业务组件里手写的重复样式串，统一形状/内边距/圆角，减少漂移。
// 文字大小、额外定位类由调用处按上下文传入，避免强制统一造成不和谐。

/**
 * mini-pill：场次号、状态标记、绑定计数等小标签。
 * 统一内边距与圆角（消除 px-1.5/px-2 漂移）；tone 决定底色；text 大小由调用处控制。
 */
export const pillTone = {
  primary: "bg-primary/10 text-primary",
  neutral: "bg-background",
} as const

export function miniPill(tone: keyof typeof pillTone = "neutral", className?: string) {
  return cn("px-1.5 py-0.5 rounded-full", pillTone[tone], className)
}

/**
 * muted 内容卡片：HistoryTakes / LLMFeedback / ScriptPanel 复用的弱化卡片样式。
 * 纯样式去重，视觉与原 inline 串保持一致。
 */
export const mutedCard = "rounded-4xl bg-muted/50 shadow-none ring-0 py-0"
