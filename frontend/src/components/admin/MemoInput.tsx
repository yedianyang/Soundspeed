import { useEffect, useRef, useState } from "react"
import { Mic, ArrowUp, X } from "lucide-react"
import { Input } from "@/components/ui/input"
import { Button } from "@/components/ui/button"
import { postNote, postVoiceNote } from "@/lib/api"
import { CONN_ID } from "@/lib/connId"
import { useVoiceRecorder } from "@/hooks/useVoiceRecorder"
import { useSessionStore } from "@/store/session"

// 答案气泡自动淡出毫秒数（点击 X 可提前关闭）。
const QP_ANSWER_TTL_MS = 12_000

interface MemoInputProps {
  onNoteAdded?: () => void
}

// client_id 只需全局唯一（pending 乐观去重/精确移除/标失败的键），不要求密码学强度。
// crypto.randomUUID 仅在安全源（HTTPS / localhost）可用，局域网 HTTP（iPad/手机经 LAN IP 访问，
// 见 spec §3.5）下为 undefined，直接调用会抛 TypeError 让 note 提交失败，故加回退。
function newClientId(): string {
  return crypto?.randomUUID?.() ?? `nid-${Date.now()}-${Math.random().toString(36).slice(2)}`
}

// 底部栏的打字 memo 输入（场记真实输入口）。接 POST /notes；类别走 @语法（如「@keep 第三条好」），
// 不打前缀默认 note。提交后乐观插入 pending note（队列由上方 NoteList 显示「处理中」），
// WS note.processed 落定后转实。麦克风按钮按住录音（4.L）→ 16k WAV → POST /notes/voice → Gemma 原生音频归置。
export default function MemoInput({ onNoteAdded }: MemoInputProps) {
  const [text, setText] = useState("")
  const addPendingNote = useSessionStore((s) => s.addPendingNote)
  const qpAnswer = useSessionStore((s) => s.qpAnswer)
  const clearQpAnswer = useSessionStore((s) => s.clearQpAnswer)
  const recorder = useVoiceRecorder()
  // 当前手势是否在录音（守卫 start 的 async 间隙 + 避免 up/leave 重复触发）。
  const gestureRef = useRef(false)

  // 答案气泡到达后定时淡出（TTL 内来新答案则重置计时——setQpAnswer 刷新 ts）。
  useEffect(() => {
    if (!qpAnswer) return
    const timer = setTimeout(() => clearQpAnswer(), QP_ANSWER_TTL_MS)
    return () => clearTimeout(timer)
  }, [qpAnswer, clearQpAnswer])

  // 文本提交：乐观 pending 提前到 await 之前（提交即显「处理中」，藏掉后端 classify 串行延迟），
  // 提交转后台不阻塞输入。后端判这条其实是查询（kind=query）→ 撤掉刚插的 note pending，
  // 答案靠 qp.answer 气泡回灌；普通备注照旧等 note.processed WS 落定转实。
  const handleSubmit = () => {
    const trimmed = text.trim()
    if (!trimmed || recorder.recording) return
    const clientId = newClientId()
    const ts = Date.now() / 1000
    addPendingNote({
      client_id: clientId,
      kind: "text",
      ts,
      category: "note", // 占位；note.processed 回灌时按 client_id 转成真类别（query 命中则撤掉）
      content: trimmed,
      rawText: trimmed, // 失败重试据此重投同文本
    })
    setText("")
    onNoteAdded?.()
    postNote(trimmed, undefined, clientId, CONN_ID)
      .then((resp) => {
        if (resp.kind === "query") {
          // 实为查询：撤掉乐观插的 note pending（它不会落库、无 note.processed 回灌），答案走气泡。
          useSessionStore.getState().removePending(clientId)
        }
      })
      .catch(() => {
        // 网络/上传层失败 → 标失败态（用 client_id 精确定位），不卡处理中。
        useSessionStore.getState().noteFailed({ reason: "upload_failed", ts, client_id: clientId })
      })
  }

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault()
      handleSubmit()
    }
  }

  // ── 语音 note：按住麦克风录音（对讲机式）──

  const uploadVoice = async (wav: Blob) => {
    const clientId = newClientId()
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
      // 网络层失败 → 标失败态（用 client_id 精确定位），不卡处理中。upload_failed 区别于后端 NP timeout。
      useSessionStore.getState().noteFailed({ reason: "upload_failed", ts, client_id: clientId })
    }
  }

  const handleMicDown = async (e: React.PointerEvent) => {
    e.preventDefault()
    if (gestureRef.current) return
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
    <div className="relative">
      {/* QP 答案气泡：默认位置在 memo 输入框正上方，TTL 后淡出 / 点 X 关闭。
          UX 位置（浮层 toast vs 框上方气泡）待 Lead 定，先做能用的草样。 */}
      {qpAnswer && (
        <div className="absolute bottom-full left-0 right-0 mb-2 flex justify-start">
          <div className="flex items-start gap-2 max-w-full rounded-2xl bg-primary text-primary-foreground px-3.5 py-2 text-sm shadow-lg">
            <span className="whitespace-pre-wrap break-words">{qpAnswer.text}</span>
            <button
              type="button"
              onClick={clearQpAnswer}
              className="mt-0.5 shrink-0 opacity-70 hover:opacity-100"
              title="关闭"
            >
              <X className="size-3.5" />
            </button>
          </div>
        </div>
      )}
      <div className="flex items-center gap-2 h-11 px-4 rounded-4xl bg-muted">
        <Input
          placeholder={
            recorder.recording
              ? "录音中…松开发送，上滑取消"
              : "Typing memo · 例：第三条结尾好，可以用（@keep / @pass / @ng）"
          }
          className="flex-1 bg-transparent border-0 ring-0 rounded-none text-sm focus:outline-none placeholder:text-muted-foreground/70 focus-visible:ring-0"
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={handleKeyDown}
          disabled={recorder.recording}
        />
        <Button
          variant="ghost"
          size="icon-sm"
          className="rounded-full text-muted-foreground hover:text-foreground disabled:opacity-40"
          onClick={handleSubmit}
          disabled={recorder.recording || !text.trim()}
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
    </div>
  )
}
