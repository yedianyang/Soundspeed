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

/**
 * 底栏 Scene / Shot / Take 三个下拉按钮的统一样式（A2）。
 * 收敛到更克制的那套（muted/60 底 + border/60），消除原先 Take 单独用 bg-background shadow-sm
 * 造成的视觉不一致。尺寸/间距/圆角一致，调用处只追加 disabled 态类。
 */
export const stageButton =
  "flex-1 sm:flex-none sm:w-24 min-w-0 gap-1 h-9 px-2.5 rounded-full text-xs border border-border/60 bg-muted/60 active:scale-95 transition-transform"

/**
 * 「录音中锁住」遮罩（E）：区别于普通灰色 disabled。
 * 淡红色调 + 微红边框，让人一眼看出是录制锁定而非功能损坏。与普通 opacity-50 互斥使用。
 */
export const recordingDisabled =
  "opacity-100 bg-destructive/10 border-destructive/30 text-destructive/70 cursor-not-allowed"
