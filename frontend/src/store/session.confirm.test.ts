import { beforeEach, describe, expect, it } from "vitest"
import { useSessionStore } from "@/store/session"
import type { NoteAppliedMsg, NoteConfirmMsg, NpExtractionDTO, PendingNote } from "@/types/api"

// ── fixtures ──

const extraction: NpExtractionDTO = {
  scene_ordinal: 1,
  shot_ordinal: 2,
  take_ordinals: [3],
  deictic: "current",
  mark: "ng",
  note_text: "灯光偏暗",
  note_category: "issue",
}

const confirmMsg = (clientId: string | null): NoteConfirmMsg => ({
  client_id: clientId,
  extraction,
  disagreement: ["scene_ordinal"],
  options: { scenes: ["1", "2"], shots: ["A", "B"], take_numbers: [1, 2, 3] },
  ts: 100,
})

const pending = (client_id: string): PendingNote => ({
  client_id,
  kind: "text",
  ts: 99,
  category: "issue",
  content: "灯光偏暗",
  rawText: "刚才灯光偏暗",
})

beforeEach(() => {
  useSessionStore.setState({
    pendingNotes: [],
    feedReceipts: [],
    clarifyItems: [],
    confirmItems: [],
    notesVersion: 0,
  } as never)
})

// ── noteConfirm ──

describe("noteConfirm", () => {
  it("移除对应 pending + confirmItems +1 + 字段透传", () => {
    useSessionStore.setState({ pendingNotes: [pending("cid")] } as never)
    useSessionStore.getState().noteConfirm(confirmMsg("cid"))
    const s = useSessionStore.getState()
    expect(s.pendingNotes).toHaveLength(0)
    expect(s.confirmItems).toHaveLength(1)
    const item = s.confirmItems[0]
    expect(item.client_id).toBe("cid")
    expect(item.extraction.mark).toBe("ng")
    expect(item.disagreement).toEqual(["scene_ordinal"])
    expect(item.options.scenes).toEqual(["1", "2"])
    expect(item.ts).toBe(100)
    expect(item.rawTextSummary).toBe("刚才灯光偏暗")
  })

  it("client_id 不匹配时 pending 不动，仍 push confirmItem", () => {
    useSessionStore.setState({ pendingNotes: [pending("other")] } as never)
    useSessionStore.getState().noteConfirm(confirmMsg("cid"))
    const s = useSessionStore.getState()
    expect(s.pendingNotes).toHaveLength(1)
    expect(s.confirmItems).toHaveLength(1)
  })

  it("client_id 为 null 时静默丢弃：不动 pending、不 push（对齐 noteClarify）", () => {
    useSessionStore.setState({ pendingNotes: [pending("cid")] } as never)
    useSessionStore.getState().noteConfirm(confirmMsg(null))
    const s = useSessionStore.getState()
    expect(s.pendingNotes).toHaveLength(1)
    expect(s.confirmItems).toHaveLength(0)
  })
})

// ── dismissConfirm ──

describe("dismissConfirm", () => {
  it("按 client_id 移除 confirmItem（纯前端，不触碰 pending/网络）", () => {
    useSessionStore.getState().noteConfirm(confirmMsg("cid"))
    useSessionStore.getState().dismissConfirm("cid")
    expect(useSessionStore.getState().confirmItems).toHaveLength(0)
  })

  it("多条共存时只删目标", () => {
    useSessionStore.getState().noteConfirm(confirmMsg("c1"))
    useSessionStore.getState().noteConfirm(confirmMsg("c2"))
    useSessionStore.getState().dismissConfirm("c1")
    const s = useSessionStore.getState()
    expect(s.confirmItems).toHaveLength(1)
    expect(s.confirmItems[0].client_id).toBe("c2")
  })
})

// ── submitConfirm ──

describe("submitConfirm", () => {
  it("移除 confirmItem + 用同一 client_id 回插 pending（processing 态）", () => {
    // Arrange：pending 经 noteConfirm 已移出，confirmItem 在场
    useSessionStore.setState({ pendingNotes: [pending("cid")] } as never)
    useSessionStore.getState().noteConfirm(confirmMsg("cid"))

    const editedExtraction: NpExtractionDTO = { ...extraction, mark: "pass" }
    useSessionStore.getState().submitConfirm("cid", editedExtraction)

    const s = useSessionStore.getState()
    expect(s.confirmItems).toHaveLength(0)
    expect(s.pendingNotes).toHaveLength(1)
    const reinserted = s.pendingNotes[0]
    expect(reinserted.client_id).toBe("cid")
    expect(reinserted.kind).toBe("text")
    expect(reinserted.category).toBe("issue")
    expect(reinserted.content).toBe("灯光偏暗")
    // rawText 取 rawTextSummary（原始输入）
    expect(reinserted.rawText).toBe("刚才灯光偏暗")
    // 无 failedReason → 处理中态
    expect(reinserted.failedReason).toBeUndefined()
  })

  it("submitConfirm 对不存在的 client_id 是 no-op", () => {
    useSessionStore.getState().noteConfirm(confirmMsg("cid"))
    useSessionStore.getState().submitConfirm("other", extraction)
    const s = useSessionStore.getState()
    expect(s.confirmItems).toHaveLength(1) // 未被移除
    expect(s.pendingNotes).toHaveLength(0)
  })
})

// ── 闭环：submitConfirm → noteApplied 消费回插的 pending ──

describe("闭环：submitConfirm 后 note.applied 到达（同 client_id）", () => {
  it("noteApplied 按 client_id 消费回插的 pending，推 receipt，notesVersion +1", () => {
    useSessionStore.setState({ pendingNotes: [pending("cid")] } as never)
    // 1. confirm 到达，pending 移出，confirm item 插入
    useSessionStore.getState().noteConfirm(confirmMsg("cid"))
    // 2. 用户提交，confirm item 移出，pending 回插（同 client_id）
    useSessionStore.getState().submitConfirm("cid", extraction)
    expect(useSessionStore.getState().pendingNotes).toHaveLength(1)
    // 3. 后端处理完，note.applied 到达（同 client_id）
    const applied: NoteAppliedMsg = {
      client_id: "cid",
      changes: [
        {
          op: "mark",
          take_id: 5,
          scene_code: "1",
          shot: "A",
          take_number: 3,
          take_suffix: "",
          status: "ng",
        },
      ],
      ts: 200,
    }
    useSessionStore.getState().noteApplied(applied)
    const s = useSessionStore.getState()
    expect(s.pendingNotes).toHaveLength(0)
    expect(s.feedReceipts).toHaveLength(1)
    expect(s.feedReceipts[0].client_id).toBe("cid")
    expect(s.notesVersion).toBe(1)
  })
})
