import { useEffect } from "react"
import { useQueryClient } from "@tanstack/react-query"
import { LiveSocket } from "@/lib/ws"
import { useSessionStore } from "@/store/session"
import type { AsrMsg, LlmStatusMsg, TakeChangedMsg } from "@/types/api"

// ch 编码在 topic 后缀（asr.partial.ch1 / asr.final.ch2），不在 payload 里。
function parseAsrTopic(topic: string): { ch: 1 | 2; isFinal: boolean } | null {
  const m = /^asr\.(partial|final)\.ch([12])$/.exec(topic)
  if (!m) return null
  return { isFinal: m[1] === "final", ch: Number(m[2]) as 1 | 2 }
}

// 单 WS 连接，按 topic 分发进 store；token 变化（SettingsDialog 保存）时重连。
export function useLiveConnection(): void {
  const token = useSessionStore((s) => s.token)
  const queryClient = useQueryClient()

  useEffect(() => {
    const store = useSessionStore.getState()

    if (!token) {
      store.setConnection("no-token")
      return
    }

    store.setConnection("connecting")

    const socket = new LiveSocket(token, {
      onOpen: () => useSessionStore.getState().setConnection("open"),
      onClose: () => useSessionStore.getState().setConnection("closed"),
      onReconnect: () => {
        // 断线期间错过的 take.changed（尤其 L2 那条）靠重取 getTakes 对齐（spec §3.3）。
        queryClient.invalidateQueries({ queryKey: ["takes"] })
      },
      onMessage: (topic, payload) => {
        const s = useSessionStore.getState()
        const asr = parseAsrTopic(topic)
        if (asr) {
          s.applyAsr(asr.ch, asr.isFinal, payload as AsrMsg)
          return
        }
        if (topic === "take.changed") {
          s.applyTakeChanged(payload as TakeChangedMsg)
          return
        }
        if (topic === "llm.status") {
          s.setLlm((payload as LlmStatusMsg).state)
          return
        }
      },
    })

    socket.connect()
    return () => socket.close()
  }, [token, queryClient])
}
