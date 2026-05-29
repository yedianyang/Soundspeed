import { useEffect, useState, type ReactNode } from "react"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"

export function StatusChip({
  label,
  tone,
  detail,
  detailClassName,
  onClick,
  children,
}: {
  label: string
  tone: "ok" | "warn" | "err"
  detail?: string
  detailClassName?: string
  onClick?: () => void
  children?: ReactNode
}) {
  const dotColor =
    tone === "ok" ? "bg-green-500" : tone === "warn" ? "bg-primary" : "bg-destructive"
  const className = cn(
    "flex items-center gap-1.5 h-9 px-4 rounded-full bg-muted/70 whitespace-nowrap sm:min-w-[5.5rem]",
    onClick && "cursor-pointer active:scale-95 transition-transform focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
  )
  const content = (
    <>
      <span className={cn("size-1.5 rounded-full flex-shrink-0", dotColor)} />
      <span className="hidden sm:inline text-xs font-medium text-foreground">{label}</span>
      {detail && (
        <span className={cn("text-[10px] font-mono text-muted-foreground", detailClassName)}>{detail}</span>
      )}
      {children}
    </>
  )

  if (onClick) {
    return (
      <Button variant="ghost" className={className} onClick={onClick} aria-label={`${label} ${detail ?? ""}`.trim()}>
        {content}
      </Button>
    )
  }
  return <div className={className}>{content}</div>
}

export function LevelMeter({
  count = 5,
  color = "bg-green-500",
}: {
  count?: number
  color?: string
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
    <div className="flex items-center gap-[1.5px] h-4">
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
