import { postNote, postQuery } from "@/lib/api"
import { useSessionStore } from "@/store/session"
import type { NoteCreateResponse } from "@/types/api"

// client_id 只需全局唯一（pending 乐观去重/精确移除/标失败的键），不要求密码学强度。
// crypto.randomUUID 仅在安全源（HTTPS / localhost）可用，局域网 HTTP（iPad/手机经 LAN IP 访问）
// 下为 undefined，直接调用会抛 TypeError 让提交失败，故加回退。
export function newClientId(): string {
  return crypto?.randomUUID?.() ?? `nid-${Date.now()}-${Math.random().toString(36).slice(2)}`
}

// 跑一条 QP 查询：乐观插 processing → 同步返回填 done / 异常填 failed。
// MemoInput 的 ? 前缀路由、回执「↩ 其实是提问」改判共用——异常自吞成 failed 态（有专门渲染），
// 调用方不必 try/catch。
export async function runQuery(text: string): Promise<void> {
  const clientId = newClientId()
  const ts = Date.now() / 1000
  useSessionStore.getState().addQa({ client_id: clientId, question: text, status: "processing", ts })
  try {
    const r = await postQuery(text)
    useSessionStore.getState().resolveQa(clientId, r.answer)
  } catch (e) {
    useSessionStore.getState().failQa(clientId, e instanceof Error ? e.message : "查询失败")
  }
}

// 跑一条文本 note：await postNote 拿到 LLM 归置的 category/content 后乐观插 pending。
// QaRow「✎ 记为备注」改判共用。postNote 失败抛出（无 pending 可标），由调用方 catch 提示。
export async function runNote(text: string): Promise<void> {
  const clientId = newClientId()
  const resp: NoteCreateResponse = await postNote(text, undefined, clientId)
  useSessionStore.getState().addPendingNote({
    client_id: clientId,
    kind: "text",
    ts: Date.now() / 1000,
    category: resp.category,
    content: resp.content,
    rawText: text,
  })
}
