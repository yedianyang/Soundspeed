import { beforeEach, describe, expect, it } from "vitest"

import { useSessionStore, type ToolCallEntry } from "@/store/session"
import type {
  AsrMsg,
  NoteFailedMsg,
  PendingNote,
  QaItem,
  ScriptDiff,
  TakeChangedMsg,
  TakeDTO,
  TranscriptSegmentDTO,
} from "@/types/api"

// 非 null 的 script_diff 哨兵：测试只关心 null vs 非 null 的身份，不关心内部形状。
const DIFF = { line_matches: [] } as unknown as ScriptDiff

// take.changed 只携带 5 字段（Pick），构造便捷工厂。
function takeChanged(
  over: Partial<TakeChangedMsg> & Pick<TakeChangedMsg, "take_id">,
): TakeChangedMsg {
  return {
    scene_id: 1,
    take_number: 1,
    status: "tbd",
    script_diff: null,
    ...over,
  }
}

// 一条完整 TakeDTO（seedTakes 用），便捷工厂。
function takeDTO(over: Partial<TakeDTO> & Pick<TakeDTO, "take_id">): TakeDTO {
  return {
    scene_id: 1,
    shot: null,
    take_number: 1,
    take_suffix: "",
    start_ts: 0,
    end_ts: null,
    status: "tbd",
    script_diff: null,
    notes: null,
    deleted_at: null,
    created_at: 0,
    updated_at: 0,
    ...over,
  }
}

function asr(over: Partial<AsrMsg>): AsrMsg {
  return {
    text: "",
    start_frame: 0,
    end_frame: 0,
    speaker: null,
    take_id: null,
    is_partial: false,
    ...over,
  }
}

function pendingNote(
  over: Partial<PendingNote> & Pick<PendingNote, "client_id">,
): PendingNote {
  return {
    kind: "text",
    ts: 0,
    category: "note",
    content: "",
    rawText: "",
    ...over,
  }
}

function noteFailedMsg(
  over: Partial<NoteFailedMsg> & Pick<NoteFailedMsg, "client_id">,
): NoteFailedMsg {
  return { reason: "timeout", ts: 0, ...over }
}

function transcriptSeg(
  over: Partial<TranscriptSegmentDTO> & Pick<TranscriptSegmentDTO, "ch">,
): TranscriptSegmentDTO {
  return {
    segment_id: 1,
    speaker: null,
    text: "",
    start_frame: 0,
    end_frame: 0,
    ...over,
  }
}

function toolCall(over: Partial<ToolCallEntry>): ToolCallEntry {
  return {
    task_type: "np",
    tool_id: null,
    tool_type: null,
    tool_name: "mark",
    arguments: "{}",
    finish_reason: null,
    model: null,
    prompt_tokens: null,
    completion_tokens: null,
    total_tokens: null,
    available_tools: [],
    tool_choice: null,
    ts: 0,
    ...over,
  }
}

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

// applyAsr：实时转录条目按声道维护，partial 替换末条、final 落定、跨-take 迟到帧守卫。
// 对应 live-transcript 工作线里 partial 丢字 / 乱序的高发区。
describe("applyAsr", () => {
  beforeEach(() => {
    useSessionStore.setState({
      segments: { ch1: [], ch2: [] },
      currentTakeId: null,
    })
  })

  it("final 推入新条目到对应声道（isPartial=false）", () => {
    useSessionStore.getState().applyAsr(1, true, asr({ text: "你好", end_frame: 10 }))
    const { ch1, ch2 } = useSessionStore.getState().segments
    expect(ch2).toHaveLength(0)
    expect(ch1).toHaveLength(1)
    expect(ch1[0]).toMatchObject({ text: "你好", isPartial: false })
  })

  it("partial 替换该声道末条 partial，不累积", () => {
    const s = useSessionStore.getState()
    s.applyAsr(1, false, asr({ text: "你", end_frame: 3 }))
    s.applyAsr(1, false, asr({ text: "你好", end_frame: 6 }))
    const { ch1 } = useSessionStore.getState().segments
    expect(ch1).toHaveLength(1)
    expect(ch1[0]).toMatchObject({ text: "你好", isPartial: true })
  })

  it("final 落定末条 partial（替换而非新增）", () => {
    const s = useSessionStore.getState()
    s.applyAsr(1, false, asr({ text: "你好世", end_frame: 6 }))
    s.applyAsr(1, true, asr({ text: "你好世界", end_frame: 9 }))
    const { ch1 } = useSessionStore.getState().segments
    expect(ch1).toHaveLength(1)
    expect(ch1[0]).toMatchObject({ text: "你好世界", isPartial: false })
  })

  it("final 跟在 final 之后 → push 新条目（不替换已落定的）", () => {
    const s = useSessionStore.getState()
    s.applyAsr(1, true, asr({ text: "第一句", end_frame: 5 }))
    s.applyAsr(1, true, asr({ text: "第二句", end_frame: 10 }))
    const { ch1 } = useSessionStore.getState().segments
    expect(ch1.map((x) => x.text)).toEqual(["第一句", "第二句"])
  })

  it("跨-take 迟到帧守卫：两侧 take_id 都非 null 且不等 → 丢弃（no-op）", () => {
    useSessionStore.setState({ currentTakeId: 7 })
    useSessionStore.getState().applyAsr(1, true, asr({ text: "迟到", take_id: 6 }))
    expect(useSessionStore.getState().segments.ch1).toHaveLength(0)
  })

  it("dev 注入器 take_id=null 绕过守卫，照常写入", () => {
    useSessionStore.setState({ currentTakeId: 7 })
    useSessionStore.getState().applyAsr(1, true, asr({ text: "注入", take_id: null }))
    expect(useSessionStore.getState().segments.ch1).toHaveLength(1)
  })

  it("take_id 与 currentTakeId 一致 → 正常写入", () => {
    useSessionStore.setState({ currentTakeId: 7 })
    useSessionStore.getState().applyAsr(2, true, asr({ text: "同 take", take_id: 7 }))
    expect(useSessionStore.getState().segments.ch2).toHaveLength(1)
  })
})

// applyTakeChanged：patch-merge 5 字段、script_diff 不向下降级、L2 到达档案未读+1、currentTakeId 单调顶。
describe("applyTakeChanged", () => {
  beforeEach(() => {
    useSessionStore.setState({
      takes: new Map(),
      currentTakeId: null,
      archiveUnread: 0,
    })
  })

  it("新 take：插入部分条目（take_suffix=''、end_ts=null 占位）", () => {
    useSessionStore.getState().applyTakeChanged(takeChanged({ take_id: 10, take_number: 3 }))
    const t = useSessionStore.getState().takes.get(10)
    expect(t).toMatchObject({ take_id: 10, take_number: 3, take_suffix: "", end_ts: null })
  })

  it("已存在 take：patch-merge 只覆盖 5 字段，保留 shot/notes 等", () => {
    useSessionStore.setState({
      takes: new Map([[10, takeDTO({ take_id: 10, shot: "3", notes: "保留我", take_number: 1 })]]),
    })
    useSessionStore.getState().applyTakeChanged(takeChanged({ take_id: 10, take_number: 2, status: "keep" }))
    const t = useSessionStore.getState().takes.get(10)!
    expect(t.take_number).toBe(2)
    expect(t.status).toBe("keep")
    expect(t.shot).toBe("3") // patch-merge 未覆盖
    expect(t.notes).toBe("保留我")
  })

  it("script_diff 不向下降级：已有非 null、incoming null → 保留旧", () => {
    useSessionStore.setState({
      takes: new Map([[10, takeDTO({ take_id: 10, script_diff: DIFF })]]),
    })
    useSessionStore.getState().applyTakeChanged(takeChanged({ take_id: 10, script_diff: null }))
    expect(useSessionStore.getState().takes.get(10)!.script_diff).toBe(DIFF)
  })

  it("L2 到达（已存在 take 的 script_diff null→非 null）→ archiveUnread+1", () => {
    useSessionStore.setState({
      takes: new Map([[10, takeDTO({ take_id: 10, script_diff: null })]]),
      archiveUnread: 0,
    })
    useSessionStore.getState().applyTakeChanged(takeChanged({ take_id: 10, script_diff: DIFF }))
    expect(useSessionStore.getState().archiveUnread).toBe(1)
  })

  it("已有 diff 再来一帧非 null → 不重复 +1（l2Arrived 仅认 null→非 null）", () => {
    useSessionStore.setState({
      takes: new Map([[10, takeDTO({ take_id: 10, script_diff: DIFF })]]),
      archiveUnread: 0,
    })
    useSessionStore.getState().applyTakeChanged(takeChanged({ take_id: 10, script_diff: DIFF }))
    expect(useSessionStore.getState().archiveUnread).toBe(0)
  })

  it("currentTakeId 单调顶：更高 take_id 顶上、更低不动", () => {
    const s = useSessionStore.getState()
    s.applyTakeChanged(takeChanged({ take_id: 5 }))
    expect(useSessionStore.getState().currentTakeId).toBe(5)
    s.applyTakeChanged(takeChanged({ take_id: 9 }))
    expect(useSessionStore.getState().currentTakeId).toBe(9)
    s.applyTakeChanged(takeChanged({ take_id: 3 })) // 低 id 帧抢先到也不回退
    expect(useSessionStore.getState().currentTakeId).toBe(9)
  })
})

// seedTakes：getTakes 全量覆盖，例外 script_diff 不向下降级到 null（P1-1 纵深防御）。
describe("seedTakes", () => {
  beforeEach(() => {
    useSessionStore.setState({ takes: new Map(), archiveUnread: 0 })
  })

  it("全量覆盖每个 take_id 条目", () => {
    useSessionStore.getState().seedTakes([
      takeDTO({ take_id: 1, take_number: 1 }),
      takeDTO({ take_id: 2, take_number: 2 }),
    ])
    expect(useSessionStore.getState().takes.size).toBe(2)
    expect(useSessionStore.getState().takes.get(2)!.take_number).toBe(2)
  })

  it("script_diff no-downgrade：store 已有非 null、seed 带 null → 保留旧", () => {
    useSessionStore.setState({
      takes: new Map([[1, takeDTO({ take_id: 1, script_diff: DIFF })]]),
    })
    useSessionStore.getState().seedTakes([takeDTO({ take_id: 1, script_diff: null })])
    expect(useSessionStore.getState().takes.get(1)!.script_diff).toBe(DIFF)
  })

  it("seed 带非 null → 用 seed 的", () => {
    useSessionStore.setState({
      takes: new Map([[1, takeDTO({ take_id: 1, script_diff: null })]]),
    })
    useSessionStore.getState().seedTakes([takeDTO({ take_id: 1, script_diff: DIFF })])
    expect(useSessionStore.getState().takes.get(1)!.script_diff).toBe(DIFF)
  })

  it("seedTakes 不 bump archiveUnread（非新事件）", () => {
    useSessionStore.getState().seedTakes([takeDTO({ take_id: 1, script_diff: DIFF })])
    expect(useSessionStore.getState().archiveUnread).toBe(0)
  })
})

// ─────────────── 第二批：store 迁移特征化测试 ───────────────

// removeTake：删除 + 解绑 currentTakeId 的四个分支。seedTakes 只增不删，删除必须走这条。
describe("removeTake", () => {
  beforeEach(() => {
    useSessionStore.setState({ takes: new Map(), currentTakeId: null })
  })

  it("删存在的 take、非 current → 从 Map 移除，currentTakeId 不动", () => {
    useSessionStore.setState({
      takes: new Map([[1, takeDTO({ take_id: 1 })]]),
      currentTakeId: 2,
    })
    useSessionStore.getState().removeTake(1)
    expect(useSessionStore.getState().takes.has(1)).toBe(false)
    expect(useSessionStore.getState().currentTakeId).toBe(2)
  })

  it("删的恰是 currentTakeId → 移除 + 解绑成 null", () => {
    useSessionStore.setState({
      takes: new Map([[1, takeDTO({ take_id: 1 })]]),
      currentTakeId: 1,
    })
    useSessionStore.getState().removeTake(1)
    expect(useSessionStore.getState().takes.has(1)).toBe(false)
    expect(useSessionStore.getState().currentTakeId).toBeNull()
  })

  it("删一个不在 Map 里、但等于 currentTakeId 的 id → 仍解绑", () => {
    useSessionStore.setState({ takes: new Map(), currentTakeId: 5 })
    useSessionStore.getState().removeTake(5)
    expect(useSessionStore.getState().currentTakeId).toBeNull()
  })

  it("删不存在、非 current → no-op", () => {
    useSessionStore.setState({
      takes: new Map([[1, takeDTO({ take_id: 1 })]]),
      currentTakeId: 1,
    })
    useSessionStore.getState().removeTake(99)
    expect(useSessionStore.getState().takes.has(1)).toBe(true)
    expect(useSessionStore.getState().currentTakeId).toBe(1)
  })
})

// applyBackfilledSegments：diarization 回填用权威 segments 替换 Live 框；守卫不覆盖新 take 的实时转录。
describe("applyBackfilledSegments", () => {
  beforeEach(() => {
    useSessionStore.setState({
      segments: { ch1: [], ch2: [] },
      currentTakeId: null,
      isRecording: false,
    })
  })

  it("按 ch 分组替换，isPartial 全置 false", () => {
    useSessionStore.setState({ currentTakeId: 1 })
    useSessionStore.getState().applyBackfilledSegments(1, [
      transcriptSeg({ ch: 1, text: "甲", speaker: "A" }),
      transcriptSeg({ ch: 2, text: "乙", speaker: "B" }),
      transcriptSeg({ ch: 1, text: "甲2", speaker: "A" }),
    ])
    const { ch1, ch2 } = useSessionStore.getState().segments
    expect(ch1.map((s) => s.text)).toEqual(["甲", "甲2"])
    expect(ch2.map((s) => s.text)).toEqual(["乙"])
    expect(ch1.every((s) => s.isPartial === false)).toBe(true)
    expect(ch1[0].speaker).toBe("A")
  })

  it("守卫：已开新 take 且在录（cur != takeId && isRecording）→ 跳过，不覆盖", () => {
    useSessionStore.setState({
      currentTakeId: 2,
      isRecording: true,
      segments: { ch1: [{ text: "新take转录", speaker: null, start_frame: 0, end_frame: 1, isPartial: true }], ch2: [] },
    })
    useSessionStore.getState().applyBackfilledSegments(1, [transcriptSeg({ ch: 1, text: "旧回填" })])
    // 未被覆盖
    expect(useSessionStore.getState().segments.ch1[0].text).toBe("新take转录")
  })

  it("不在录（isRecording=false）即使 take 不同也替换", () => {
    useSessionStore.setState({ currentTakeId: 2, isRecording: false })
    useSessionStore.getState().applyBackfilledSegments(1, [transcriptSeg({ ch: 1, text: "回填" })])
    expect(useSessionStore.getState().segments.ch1[0].text).toBe("回填")
  })

  it("cur == takeId → 替换（同一 take 的回填）", () => {
    useSessionStore.setState({ currentTakeId: 1, isRecording: true })
    useSessionStore.getState().applyBackfilledSegments(1, [transcriptSeg({ ch: 2, text: "同take回填" })])
    expect(useSessionStore.getState().segments.ch2[0].text).toBe("同take回填")
  })
})

// pending 生命周期：noteFailed / retryPending / dismissPending / removePending。
describe("pending 生命周期", () => {
  beforeEach(() => {
    useSessionStore.setState({ pendingNotes: [] })
  })

  it("noteFailed：按 client_id 标 failedReason", () => {
    useSessionStore.setState({ pendingNotes: [pendingNote({ client_id: "c1" })] })
    useSessionStore.getState().noteFailed(noteFailedMsg({ client_id: "c1", reason: "take_not_found" }))
    expect(useSessionStore.getState().pendingNotes[0].failedReason).toBe("take_not_found")
  })

  it("noteFailed client_id=null：不误标任何 pending", () => {
    useSessionStore.setState({ pendingNotes: [pendingNote({ client_id: "c1" })] })
    useSessionStore.getState().noteFailed(noteFailedMsg({ client_id: null }))
    expect(useSessionStore.getState().pendingNotes[0].failedReason).toBeUndefined()
  })

  it("retryPending：清 failedReason，打回处理中", () => {
    useSessionStore.setState({
      pendingNotes: [pendingNote({ client_id: "c1", failedReason: "timeout" })],
    })
    useSessionStore.getState().retryPending("c1")
    expect(useSessionStore.getState().pendingNotes[0].failedReason).toBeUndefined()
  })

  it("dismissPending：按 client_id 移除", () => {
    useSessionStore.setState({
      pendingNotes: [pendingNote({ client_id: "c1" }), pendingNote({ client_id: "c2" })],
    })
    useSessionStore.getState().dismissPending("c1")
    const ids = useSessionStore.getState().pendingNotes.map((p) => p.client_id)
    expect(ids).toEqual(["c2"])
  })

  it("removePending：按 client_id 移除（query 改判撤乐观 note）", () => {
    useSessionStore.setState({
      pendingNotes: [pendingNote({ client_id: "c1" }), pendingNote({ client_id: "c2" })],
    })
    useSessionStore.getState().removePending("c2")
    const ids = useSessionStore.getState().pendingNotes.map((p) => p.client_id)
    expect(ids).toEqual(["c1"])
  })
})

// appendToolCall：有界缓冲，最近 TOOL_CALLS_MAX(150) 条，超出从头丢。
describe("appendToolCall", () => {
  beforeEach(() => {
    useSessionStore.setState({ toolCalls: [] })
  })

  it("追加一条", () => {
    useSessionStore.getState().appendToolCall(toolCall({ tool_name: "mark", ts: 1 }))
    expect(useSessionStore.getState().toolCalls).toHaveLength(1)
    expect(useSessionStore.getState().toolCalls[0].tool_name).toBe("mark")
  })

  it("正好 150 条不丢", () => {
    const s = useSessionStore.getState()
    for (let i = 0; i < 150; i++) s.appendToolCall(toolCall({ ts: i }))
    expect(useSessionStore.getState().toolCalls).toHaveLength(150)
    expect(useSessionStore.getState().toolCalls[0].ts).toBe(0)
  })

  it("第 151 条进来 → 丢最老一条，长度保持 150", () => {
    const s = useSessionStore.getState()
    for (let i = 0; i < 151; i++) s.appendToolCall(toolCall({ ts: i }))
    const tc = useSessionStore.getState().toolCalls
    expect(tc).toHaveLength(150)
    expect(tc[0].ts).toBe(1) // ts=0 那条被丢
    expect(tc[tc.length - 1].ts).toBe(150)
  })
})
