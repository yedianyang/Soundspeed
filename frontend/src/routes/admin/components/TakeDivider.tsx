import { Separator } from "@/components/ui/separator"

// Live transcript 顶部的 take 分隔条。label 为 take 编号显示（含冲突后缀，如 "3+"）。
// time 可选（实时录制无时间戳）。
export function TakeDivider({ label, time }: { label: string; time?: string }) {
  return (
    <div className="flex items-center gap-3">
      <Separator className="flex-1" />
      <span className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">
        Take {label}{time ? ` · ${time}` : ""}
      </span>
      <Separator className="flex-1" />
    </div>
  )
}
