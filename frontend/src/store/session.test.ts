import { beforeEach, describe, expect, it } from "vitest"

import { useSessionStore } from "@/store/session"
import type {
  AsrMsg,
  PendingNote,
  QaItem,
  ScriptDiff,
  TakeChangedMsg,
  TakeDTO,
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
