import { useRef, useState } from "react"
import { Mic, ArrowUp } from "lucide-react"
import { Input } from "@/components/ui/input"
import { Button } from "@/components/ui/button"
import { postNote, postVoiceNote } from "@/lib/api"
import { useVoiceRecorder } from "@/hooks/useVoiceRecorder"
import { useSessionStore } from "@/store/session"
import type { NoteCreateResponse } from "@/types/api"

interface MemoInputProps {
  onNoteAdded?: () => void
}

// 底部栏的打字 memo 输入（场记真实输入口）。接 POST /notes；类别走 @语法（如「@keeper 第三条好」），
// 不打前缀默认 note。提交后乐观插入 pending note（队列由上方 NoteList 显示「处理中」），
// WS note.processed 落定后转实。麦克风按钮按住录音（4.L）→ 16k WAV → POST /notes/voice → Gemma 原生音频归置。
export default function MemoInput({ onNoteAdded }: MemoInputProps) {
  const [text, setText] = useState("")
  const [sending, setSending] = useState(false)
  const addPendingNote = useSessionStore((s) => s.addPendingNote)
  const recorder = useVoiceRecorder()
  // 当前手势是否在录音（守卫 start 的 async 间隙 + 避免 up/leave 重复触发）。
  const gestureRef = useRef(false)

  const handleSubmit = async () => {
    const trimmed = text.trim()
    if (!trimmed || sending) return
    setSending(true)
    const clientId = crypto.randomUUID()
    try {
      const resp: NoteCreateResponse = await postNote(trimmed, undefined, clientId)
      addPendingNote({
        client_id: clientId,
        kind: "text",
        ts: Date.now() / 1000,
        category: resp.category,
        content: resp.content,
        rawText: trimmed, // 失败重试据此重投同文本
      })
      setText("")
      onNoteAdded?.()
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "提交失败"
      alert(msg)
    } finally {
      setSending(false)
    }
  }

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault()
      handleSubmit()
    }
  }

  // ── 语音 note：按住麦克风录音（对讲机式）──

  const uploadVoice = async (wav: Blob) => {
    const clientId = crypto.randomUUID()
    const ts = Date.now() / 1000
    // 乐观 pending：语音的类别/正文 202 时未知（由模型从音频判）。kind=voice → 渲染只显 🎤 不显
    // @category（避免伪造类别）；voiceBlob 供失败重试重传。
    addPendingNote({
      client_id: clientId,
      kind: "voice",
      ts,
      category: "note",
      content: "🎤 语音备注",
      rawText: "",
      voiceBlob: wav,
    })
    onNoteAdded?.()
    try {
      await postVoiceNote(wav, clientId, ts)
    } catch {
      // 网络层失败 → 标失败态（用 client_id 精确定位），不卡处理中。
      useSessionStore.getState().noteFailed({ reason: "timeout", ts, client_id: clientId })
    }
  }

  const handleMicDown = async (e: React.PointerEvent) => {
    e.preventDefault()
    if (gestureRef.current || sending) return
    gestureRef.current = true
    try {
      await recorder.start()
    } catch {
      gestureRef.current = false
      alert("无法访问麦克风（检查浏览器权限 / 局域网需 HTTPS）")
    }
  }

  const handleMicUp = async () => {
    if (!gestureRef.current) return
    gestureRef.current = false
    const wav = await recorder.stop() // null=太短（误触），静默丢弃
    if (wav) await uploadVoice(wav)
  }

  const handleMicCancel = () => {
    if (!gestureRef.current) return // 上滑离开按钮 / pointercancel → 取消不提交
    gestureRef.current = false
    recorder.cancel()
  }

  return (
    <div className="flex items-center gap-2 h-11 px-4 rounded-4xl bg-muted">
      <Input
        placeholder={
          recorder.recording
            ? "录音中…松开发送，上滑取消"
            : "Typing memo · 例：第三条结尾好，可以用（@keeper / @ng / @issue）"
        }
        className="flex-1 bg-transparent border-0 ring-0 rounded-none text-sm focus:outline-none placeholder:text-muted-foreground/70 focus-visible:ring-0"
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={handleKeyDown}
        disabled={sending || recorder.recording}
      />
      <Button
        variant="ghost"
        size="icon-sm"
        className="rounded-full text-muted-foreground hover:text-foreground disabled:opacity-40"
        onClick={handleSubmit}
        disabled={sending || recorder.recording || !text.trim()}
        title="提交备注"
      >
        <ArrowUp className="size-4" />
      </Button>
      <Button
        variant="ghost"
        size="icon-sm"
        className={
          "rounded-full select-none touch-none " +
          (recorder.recording
            ? "text-destructive bg-destructive/10"
            : "text-muted-foreground hover:text-foreground")
        }
        onPointerDown={handleMicDown}
        onPointerUp={handleMicUp}
        onPointerLeave={handleMicCancel}
        onPointerCancel={handleMicCancel}
        title="按住说话（松开发送，上滑取消）"
      >
        <Mic className={"size-4" + (recorder.recording ? " animate-pulse" : "")} />
      </Button>
    </div>
  )
}
