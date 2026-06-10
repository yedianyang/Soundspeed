import { beforeEach, describe, expect, it } from "vitest"

import { useSessionStore } from "@/store/session"
import type { AsrMsg, PendingNote, QaItem } from "@/types/api"

// qpAnswerArrived：QP 答案到达时把答案落进队列。store 是模块级单例，每个用例前重置相关切片。
describe("qpAnswerArrived", () => {
  beforeEach(() => {
    useSessionStore.setState({ qaItems: [], pendingNotes: [], archiveUnread: 0 })
  })

  it("命中预建的 processing qaItem（文本 query 路径）→ 置 done + answer", () => {
    const qa: QaItem = {
      client_id: "c-text",
      question: "第三场 NG 几条？",
      status: "processing",
      ts: 100,
    }
    useSessionStore.setState({ qaItems: [qa] })

    useSessionStore.getState().qpAnswerArrived("c-text", "NG 共 3 条。")

    const items = useSessionStore.getState().qaItems
    expect(items).toHaveLength(1)
    expect(items[0]).toMatchObject({
      client_id: "c-text",
      question: "第三场 NG 几条？",
      status: "done",
      answer: "NG 共 3 条。",
    })
    expect(useSessionStore.getState().archiveUnread).toBe(1)
  })

  it("无 qaItem 但有匹配的语音 pending → 撤 pending + 新增 done qaItem（fallback 问题文案）", () => {
    const pending: PendingNote = {
      client_id: "c-voice",
      kind: "voice",
      ts: 200,
      category: "note",
      content: "语音备注",
      rawText: "",
    }
    useSessionStore.setState({ pendingNotes: [pending] })

    useSessionStore.getState().qpAnswerArrived("c-voice", "这是语音查询的答案。")

    const state = useSessionStore.getState()
    // 语音 pending 被移除
    expect(state.pendingNotes.find((p) => p.client_id === "c-voice")).toBeUndefined()
    // 新增一条 done qaItem，问题用通用占位（语音 query 无问题原文）
    expect(state.qaItems).toHaveLength(1)
    expect(state.qaItems[0]).toMatchObject({
      client_id: "c-voice",
      question: "🎤 语音提问",
      status: "done",
      answer: "这是语音查询的答案。",
      ts: 200,
    })
    expect(state.archiveUnread).toBe(1)
  })

  it("既无 qaItem 也无 pending（陈旧/旧广播）→ no-op", () => {
    useSessionStore.getState().qpAnswerArrived("c-unknown", "孤儿答案")

    const state = useSessionStore.getState()
    expect(state.qaItems).toHaveLength(0)
    expect(state.pendingNotes).toHaveLength(0)
    expect(state.archiveUnread).toBe(0)
  })
})

// applyAsr 空文本 partial = 清除信号（流式 partial spec §3.6）：后端 turn 结束未出 final 时
// 发空 partial，前端据此移除该声道悬挂的 partial 行，避免幽灵斜体光标行。
describe("applyAsr 空 partial 清除", () => {
  beforeEach(() => {
    useSessionStore.setState({ segments: { ch1: [], ch2: [] }, currentTakeId: null })
  })

  const msg = (text: string): AsrMsg => ({
    text,
    speaker: null,
    start_frame: 0,
    end_frame: 0,
    take_id: null,
    is_partial: true,
  })

  it("空文本 partial → 移除该声道最后一条 partial（清掉悬挂行）", () => {
    useSessionStore.getState().applyAsr(1, false, msg("你好世界"))
    expect(useSessionStore.getState().segments.ch1).toHaveLength(1)
    useSessionStore.getState().applyAsr(1, false, msg(""))
    expect(useSessionStore.getState().segments.ch1).toHaveLength(0)
  })

  it("空文本 partial 但最后一条是 final → 不动（不能删掉已落定文本）", () => {
    useSessionStore.getState().applyAsr(1, true, msg("最终文本"))
    useSessionStore.getState().applyAsr(1, false, msg(""))
    const ch1 = useSessionStore.getState().segments.ch1
    expect(ch1).toHaveLength(1)
    expect(ch1[0].isPartial).toBe(false)
  })

  it("空文本 partial 在空声道 → no-op", () => {
    useSessionStore.getState().applyAsr(2, false, msg(""))
    expect(useSessionStore.getState().segments.ch2).toHaveLength(0)
  })

  it("非空 partial 仍替换最后一条 partial（原行为不变）", () => {
    useSessionStore.getState().applyAsr(1, false, msg("你"))
    useSessionStore.getState().applyAsr(1, false, msg("你好"))
    const ch1 = useSessionStore.getState().segments.ch1
    expect(ch1).toHaveLength(1)
    expect(ch1[0].text).toBe("你好")
  })
})
