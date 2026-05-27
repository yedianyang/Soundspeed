import { Card, CardContent } from "@/components/ui/card"

export function ScriptPanel() {
  return (
    <div className="py-4 space-y-3">
      <Card className="rounded-3xl bg-muted/50 shadow-none ring-0 py-0">
        <CardContent className="p-4 space-y-4">
          <div className="flex items-center gap-2 text-xs font-mono text-muted-foreground">
            <span className="px-2 py-0.5 rounded-full bg-background">SCENE 3</span>
            <span>室内</span>
            <span>·</span>
            <span>夜</span>
            <span>·</span>
            <span>客厅</span>
          </div>
          <p className="text-sm leading-relaxed text-foreground">
            SZA 坐在沙发上，YY 站在窗边。
          </p>
          <div className="space-y-2">
            <p className="text-sm leading-relaxed">
              <span className="text-primary font-medium">SZA：</span>
              你昨天为什么没有告诉我真相。
            </p>
            <p className="text-sm leading-relaxed">
              <span className="text-secondary-foreground font-medium">YY：</span>
              因为我不想让你再卷进来。
            </p>
            <p className="text-sm leading-relaxed">
              <span className="text-primary font-medium">SZA：</span>
              那你打算什么时候告诉我。
            </p>
          </div>
        </CardContent>
      </Card>
      <p className="text-xs text-muted-foreground text-center pt-2">
        剧本由制片部门上传，拍摄前锁定
      </p>
    </div>
  )
}
