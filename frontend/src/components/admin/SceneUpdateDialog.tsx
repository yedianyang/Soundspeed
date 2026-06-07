import { useState } from "react"
import { useQueryClient } from "@tanstack/react-query"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import {
  ApiError,
  charactersQueryKey,
  parseSingleScene,
  scenesQueryKey,
  sceneScriptQueryKey,
  updateSceneScript,
} from "@/lib/api"
import type { ParseSingleResult } from "@/types/api"
import { Loader2 } from "lucide-react"

interface Props {
  open: boolean
  onOpenChange: (open: boolean) => void
  sceneId: number | null
  sceneCode: string | null
  onUpdated?: () => void
}

type Phase = "edit" | "parsing" | "preview" | "committing"

// 选中场更新：粘贴这一场的剧本文本 → 原生 FC 解析预览 → 确认 → 给该场追加新版本。
// 只动选中的那一场（versioned，不删旧版）。照片/OCR 走同一确认流（后续接 OCR 时复用）。
export default function SceneUpdateDialog({
  open,
  onOpenChange,
  sceneId,
  sceneCode,
  onUpdated,
}: Props) {
  const qc = useQueryClient()
  const [text, setText] = useState("")
  const [phase, setPhase] = useState<Phase>("edit")
  const [preview, setPreview] = useState<ParseSingleResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [skippedMsg, setSkippedMsg] = useState<string | null>(null)

  const reset = () => {
    setText("")
    setPhase("edit")
    setPreview(null)
    setError(null)
    setSkippedMsg(null)
  }

  const handleParse = async () => {
    const src = text.trim()
    if (!src) return
    setError(null)
    setPhase("parsing")
    try {
      const result = await parseSingleScene(src)
      setPreview(result)
      setPhase("preview")
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "解析失败，请重试")
      setPhase("edit")
    }
  }

  const handleCommit = async () => {
    if (sceneId == null || !preview) return
    setError(null)
    setPhase("committing")
    try {
      const res = await updateSceneScript(sceneId, text, preview.lines)
      qc.invalidateQueries({ queryKey: sceneScriptQueryKey(sceneId) })
      qc.invalidateQueries({ queryKey: scenesQueryKey() })
      qc.invalidateQueries({ queryKey: charactersQueryKey() })
      if (res.skipped) {
        // 内容与最新版相同：不当成功收尾，提示用户
        setSkippedMsg("内容与当前版本相同，未新建版本。")
        setPhase("preview")
        return
      }
      onUpdated?.()
      reset()
      onOpenChange(false)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "更新失败，请重试")
      setPhase("preview")
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        if (!o) reset()
        onOpenChange(o)
      }}
    >
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>更新本场（SCENE {sceneCode ?? "—"}）</DialogTitle>
          <DialogDescription>
            粘贴这一场的剧本文本，解析预览核对后确认。只更新这一场，追加为新版本（旧版本保留）。
          </DialogDescription>
        </DialogHeader>

        {phase === "edit" || phase === "parsing" ? (
          <div className="space-y-3">
            <Textarea
              value={text}
              onChange={(e) => setText(e.target.value)}
              placeholder="粘贴本场剧本文本（可含场头行，如「场3 内 咖啡馆 日」）…"
              className="min-h-40 text-sm"
              disabled={phase === "parsing"}
            />
            {error && <p className="text-xs text-destructive">{error}</p>}
            <div className="flex justify-end gap-2">
              <Button variant="ghost" size="sm" onClick={() => onOpenChange(false)}>
                取消
              </Button>
              <Button size="sm" onClick={handleParse} disabled={!text.trim() || phase === "parsing"}>
                {phase === "parsing" ? (
                  <>
                    <Loader2 className="size-3.5 animate-spin" /> 解析中…
                  </>
                ) : (
                  "解析预览"
                )}
              </Button>
            </div>
          </div>
        ) : (
          // preview / committing
          <div className="space-y-3">
            <div className="flex flex-wrap items-center gap-1.5 text-xs font-mono text-muted-foreground">
              <span>SCENE {preview?.scene_code ?? sceneCode ?? "—"}</span>
              {[preview?.int_ext, preview?.time_of_day, preview?.location]
                .filter((v): v is string => !!v)
                .map((v) => (
                  <span key={v} className="text-primary">
                    · {v}
                  </span>
                ))}
            </div>
            <div className="max-h-64 overflow-y-auto space-y-2 rounded-lg bg-muted/50 p-3">
              {(preview?.lines ?? []).length === 0 ? (
                <p className="text-sm text-muted-foreground">未解析出任何台词行。</p>
              ) : (
                preview?.lines.map((l, i) => (
                  <p key={i} className="text-sm leading-relaxed">
                    {l.character ? (
                      <span className="text-primary font-medium">{l.character}：</span>
                    ) : null}
                    <span className={l.character ? "" : "text-muted-foreground"}>{l.text}</span>
                  </p>
                ))
              )}
            </div>
            {skippedMsg && <p className="text-xs text-muted-foreground">{skippedMsg}</p>}
            {error && <p className="text-xs text-destructive">{error}</p>}
            <div className="flex justify-end gap-2">
              <Button
                variant="ghost"
                size="sm"
                onClick={() => {
                  setPhase("edit")
                  setSkippedMsg(null)
                }}
                disabled={phase === "committing"}
              >
                重新编辑
              </Button>
              <Button
                size="sm"
                onClick={handleCommit}
                disabled={phase === "committing" || (preview?.lines ?? []).length === 0}
              >
                {phase === "committing" ? (
                  <>
                    <Loader2 className="size-3.5 animate-spin" /> 更新中…
                  </>
                ) : (
                  "确认更新本场"
                )}
              </Button>
            </div>
          </div>
        )}
      </DialogContent>
    </Dialog>
  )
}
