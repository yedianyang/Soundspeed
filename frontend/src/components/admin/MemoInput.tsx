import { useState } from "react"
import { Mic, ArrowUp } from "lucide-react"
import { Input } from "@/components/ui/input"
import { Button } from "@/components/ui/button"
import { postNote } from "@/lib/api"
import { useSessionStore } from "@/store/session"
import type { NoteCreateResponse } from "@/types/api"

interface MemoInputProps {
  onNoteAdded?: () => void
}

// 底部栏的打字 memo 输入（场记真实输入口）。接 POST /notes；类别走 @语法（如「@keeper 第三条好」），
// 不打前缀默认 note。提交后乐观插入 pending note（队列由上方 NoteList 显示「处理中」），
// WS note.processed 落定后转实。Mic 按钮预留语音备注入口（ch2，4.E/4.F），暂不接线。
export default function MemoInput({ onNoteAdded }: MemoInputProps) {
  const [text, setText] = useState("")
  const [sending, setSending] = useState(false)
  const addPendingNote = useSessionStore((s) => s.addPendingNote)

  const handleSubmit = async () => {
    const trimmed = text.trim()
    if (!trimmed || sending) return
    setSending(true)
    const clientId = crypto.randomUUID()
    try {
      const resp: NoteCreateResponse = await postNote(trimmed, undefined, clientId)
      addPendingNote({
        client_id: clientId,
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

  return (
    <div className="flex items-center gap-2 h-11 px-4 rounded-4xl bg-muted">
      <Input
        placeholder="Typing memo · 例：第三条结尾好，可以用（@keeper / @ng / @issue）"
        className="flex-1 bg-transparent border-0 ring-0 rounded-none text-sm focus:outline-none placeholder:text-muted-foreground/70 focus-visible:ring-0"
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={handleKeyDown}
        disabled={sending}
      />
      <Button
        variant="ghost"
        size="icon-sm"
        className="rounded-full text-muted-foreground hover:text-foreground disabled:opacity-40"
        onClick={handleSubmit}
        disabled={sending || !text.trim()}
        title="提交备注"
      >
        <ArrowUp className="size-4" />
      </Button>
      <Button
        variant="ghost"
        size="icon-sm"
        className="rounded-full text-muted-foreground hover:text-foreground"
        title="按麦录音 memo（语音备注 ch2，4.E/4.F，暂未接线）"
      >
        <Mic className="size-4" />
      </Button>
    </div>
  )
}
