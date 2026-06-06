import { useEffect, useState } from "react"
import { Card } from "@/components/ui/card"
import { getTakeNotes, postNote, postVoiceNote } from "@/lib/api"
import { CONN_ID } from "@/lib/connId"
import { useSessionStore } from "@/store/session"
import type { NoteDTO, PendingNote } from "@/types/api"

const CATEGORY_COLORS: Record<string, string> = {
  keep: "text-green-600",
  pass: "text-green-600",
  ng: "text-red-600",
  issue: "text-yellow-600",
  note: "text-muted-foreground",
}

// note.failed reason → 场记可读文案（4.I）
const FAIL_REASON_TEXT: Record<string, string> = {
  take_not_found: "找不到对应素材",
  parse_error: "理解失败",
  timeout: "处理超时",
  asr_unclear: "没听清",
  model_unavailable: "模型未就绪", // mmproj 缺失/下载失败，语音模型未挂载（4.J）
  upload_failed: "提交失败", // 前端网络/上传层失败（请求没进后端），区别于后端 NP 的 timeout
}

function formatTime(ts: number): string {
  const d = new Date(ts * 1000)
  return d.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", second: "2-digit" })
}

interface NoteListProps {
  takeId: number | null
  refreshKey: number
}

export default function NoteList({ takeId, refreshKey }: NoteListProps) {
  const [notes, setNotes] = useState<NoteDTO[]>([])
  const pendingNotes = useSessionStore((s) => s.pendingNotes)
  // note.processed 落库后 store bump notesVersion → 触发 resolved refetch（衔接 pending 移除与 resolved 显示）。
  const notesVersion = useSessionStore((s) => s.notesVersion)
  const retryPending = useSessionStore((s) => s.retryPending)
  const noteFailed = useSessionStore((s) => s.noteFailed)

  // 4.I/4.L：失败 pending 重试——乐观打回「处理中」，用同 client_id 重投；网络层再失败则标回。
  // 语音条目（有 voiceBlob）重传录音 WAV，文本条目重投原文。
  const handleRetry = (pn: PendingNote) => {
    retryPending(pn.client_id)
    // 带 CONN_ID（对齐初次提交 MemoInput）：后端据此走 voice/text dispatch 判 note/query，
    // query 答案靠 qp.answer.{CONN_ID} 气泡回灌。漏带则 conn_id=None → 直落 NP-only 分支，
    // 被判 query 的条目重试后永远等不到答案、卡「处理中」。
    const resubmit = pn.voiceBlob
      ? postVoiceNote(pn.voiceBlob, pn.client_id, pn.ts, CONN_ID)
      : postNote(pn.rawText, undefined, pn.client_id, CONN_ID)
    resubmit.catch(() => {
      noteFailed({ reason: "upload_failed", ts: pn.ts, client_id: pn.client_id })
    })
  }

  useEffect(() => {
    if (takeId == null) {
      setNotes([])
      return
    }
    let cancelled = false
    getTakeNotes(takeId).then((res) => {
      if (!cancelled) setNotes(res.events)
    }).catch(() => {
      if (!cancelled) setNotes([])
    })
    return () => { cancelled = true }
  }, [takeId, refreshKey, notesVersion])

  // pending 是乐观本地态，独立于 take——没建 take（takeId==null）也要让场记看到「处理中」，否则
  // 提交后零反馈。resolved notes 已由上面 effect 在 takeId==null 时清空成 []，故只看两个数组长度：
  // 都空才不渲染（避免空浮层永久遮住 main 一条）。
  const hasNotes = pendingNotes.length > 0 || notes.length > 0
  if (!hasNotes) {
    return null
  }

  // 按时间倒序
  const sorted = [...notes].reverse()

  return (
    // pb-[35px] = 16(可见上间距) + 17(藏量) + 2(上移)；藏量 17 与 AdminHome 浮层
    // bottom-[calc(100%-26px)] 的 17 同源，改一处两处都要改（否则浮层底边与 pill 顶错位）。
    <Card
      size="sm"
      className="pointer-events-auto px-3 pt-4 pb-[35px] gap-1 max-h-[40vh] overflow-y-auto bg-background rounded-t-2xl rounded-b-none shadow-[0_-4px_16px_rgba(0,0,0,0.1)] ring-0"
    >
      {/* Pending notes（处理中 / 失败） */}
      {pendingNotes.map((pn: PendingNote) => {
        const failed = pn.failedReason != null
        return (
          <div
            key={`pending-${pn.client_id}`}
            className={`flex items-center gap-2 text-xs py-0.5 ${failed ? "" : "opacity-60"}`}
          >
            <span className="text-muted-foreground font-mono whitespace-nowrap">
              {formatTime(pn.ts)}
            </span>
            {/* 语音 pending 类别由模型判，未知 → 不渲染 @category（避免伪造 @note）；文本才显类别。 */}
            {pn.kind !== "voice" && (
              <span className={`font-semibold whitespace-nowrap ${CATEGORY_COLORS[pn.category] ?? "text-muted-foreground"}`}>
                @{pn.category}
              </span>
            )}
            {pn.content && (
              <span className={`break-all ${failed ? "text-red-600" : "text-foreground"}`}>
                {pn.content}
              </span>
            )}
            {failed ? (
              <>
                <span className="text-red-600 whitespace-nowrap">
                  {FAIL_REASON_TEXT[pn.failedReason!] ?? "处理失败"}
                </span>
                <button
                  type="button"
                  onClick={() => handleRetry(pn)}
                  className="text-red-600 underline whitespace-nowrap hover:text-red-700"
                >
                  重试
                </button>
              </>
            ) : (
              <span className="text-muted-foreground italic whitespace-nowrap">处理中...</span>
            )}
          </div>
        )
      })}
      {/* Resolved notes */}
      {sorted.map((n) => (
        <div key={n.event_id} className="flex items-center gap-2 text-xs py-0.5">
          <span className="text-muted-foreground font-mono whitespace-nowrap">
            {formatTime(n.ts)}
          </span>
          <span className={`font-semibold whitespace-nowrap ${CATEGORY_COLORS[n.category] ?? "text-muted-foreground"}`}>
            @{n.category}
          </span>
          {n.content && (
            <span className="text-foreground break-all">{n.content}</span>
          )}
        </div>
      ))}
    </Card>
  )
}
