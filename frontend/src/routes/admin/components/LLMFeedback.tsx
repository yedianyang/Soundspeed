import { Badge } from "@/components/ui/badge"
import { Card, CardContent } from "@/components/ui/card"
import { LLM_FEEDBACK } from "@/data/mock"
import { mutedCard } from "@/lib/styles"

export function LLMFeedback() {
  return (
    <div className="py-4 space-y-3">
      {LLM_FEEDBACK.map((item, i) => (
        <Card key={i} className={mutedCard}>
          <CardContent className="p-4 space-y-2">
            <Badge variant="secondary" className="font-mono uppercase">
              {item.kind}
            </Badge>
            <p className="text-sm leading-relaxed text-foreground">{item.text}</p>
          </CardContent>
        </Card>
      ))}
      <p className="text-xs text-muted-foreground text-center pt-2">
        每次 take 结束后由 L2 / NP / SP Pipeline 推送
      </p>
    </div>
  )
}
