import { useEffect, useState, type ReactNode } from "react"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"

export function StatusChip({
  label,
  tone,
  detail,
  className,
  onClick,
  children,
}: {
  label: string
  tone: "ok" | "warn" | "err"
  detail?: string
  className?: string
  onClick?: () => void
  children?: ReactNode
}) {
  const dotColor =
    tone === "ok" ? "bg-green-500" : tone === "warn" ? "bg-primary" : "bg-destructive"
  const cls = cn(
    "flex items-center gap-1.5 h-9 px-4 rounded-full bg-muted/70 min-w-0 sm:min-w-[5.5rem]",
    onClick && "cursor-pointer active:scale-95 transition-transform focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
    className
  )
  const content = (
    <>
      <span className={cn("size-1.5 rounded-full flex-shrink-0", dotColor)} />
      <span className="hidden sm:inline text-xs font-medium text-foreground flex-shrink-0">{label}</span>
      {detail && (
        <span className="text-[10px] font-mono text-muted-foreground truncate min-w-0">{detail}</span>
      )}
      {children}
    </>
  )

  if (onClick) {
    return (
      <Button variant="ghost" className={cls} onClick={onClick} aria-label={`${label} ${detail ?? ""}`.trim()}>
        {content}
      </Button>
    )
  }
  return <div className={cls}>{content}</div>
}

export function LevelMeter({
  count = 5,
  color = "bg-green-500",
  className,
}: {
  count?: number
  color?: string
  className?: string
}) {
  const [heights, setHeights] = useState<number[]>(() =>
    Array.from({ length: count }, () => Math.random())
  )

  useEffect(() => {
    const id = setInterval(() => {
      setHeights(Array.from({ length: count }, () => Math.random()))
    }, 80)
    return () => clearInterval(id)
  }, [count])

  return (
    <div className={cn("flex items-center gap-[1.5px] h-4 flex-shrink-0", className)}>
      {heights.map((h, i) => (
        <span
          key={i}
          className={cn("w-[2px] rounded-full transition-all duration-75", color)}
          style={{ height: `${3 + h * 10}px` }}
        />
      ))}
    </div>
  )
}
