import { useState } from "react"
import { Button } from "@/components/ui/button"
import { Card } from "@/components/ui/card"
import { postNote } from "@/lib/api"

const CATEGORIES = [
  { key: "keeper", label: "Keeper", color: "bg-green-600" },
  { key: "ng", label: "NG", color: "bg-red-600" },
  { key: "issue", label: "Issue", color: "bg-yellow-600" },
  { key: "note", label: "Note", color: "bg-gray-600" },
]

interface NoteMemoProps {
  onNoteAdded: () => void // 通知父组件刷新 note 列表
}

export default function NoteMemo({ onNoteAdded }: NoteMemoProps) {
  const [text, setText] = useState("")
  const [sending, setSending] = useState(false)

  const handleSubmit = async () => {
    const trimmed = text.trim()
    if (!trimmed || sending) return
    setSending(true)
    try {
      await postNote(trimmed)
      setText("")
      onNoteAdded()
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "提交失败"
      alert(msg)
    } finally {
      setSending(false)
    }
  }

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault()
      handleSubmit()
    }
  }

  const insertCategory = (cat: string) => {
    setText((prev) => `@${cat} ${prev}`)
  }

  return (
    <Card size="sm" className="p-3 gap-2">
      <div className="flex items-center gap-1 flex-wrap">
        {CATEGORIES.map((c) => (
          <Button
            key={c.key}
            variant="outline"
            size="sm"
            className="h-7 px-2 text-xs gap-1"
            onClick={() => insertCategory(c.key)}
          >
            <span className={`size-2 rounded-full ${c.color}`} />
            {c.label}
          </Button>
        ))}
      </div>
      <div className="flex gap-2">
        <textarea
          className="flex-1 min-h-[60px] rounded-md border border-input bg-background px-3 py-2 text-sm resize-none focus:outline-none focus:ring-2 focus:ring-ring"
          placeholder="输入备注..."
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={handleKeyDown}
          disabled={sending}
          rows={2}
        />
        <Button
          size="sm"
          className="self-end h-9"
          onClick={handleSubmit}
          disabled={sending || !text.trim()}
        >
          {sending ? "..." : "提交"}
        </Button>
      </div>
    </Card>
  )
}
