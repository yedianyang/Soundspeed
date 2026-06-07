import { beforeEach, describe, expect, it } from "vitest"
import { useSessionStore } from "@/store/session"
import type { NoteAppliedMsg, NoteClarifyMsg, PendingNote } from "@/types/api"

const pending = (client_id: string): PendingNote => ({
  client_id, kind: "text", ts: 1, category: "note", content: "c", rawText: "原话",
})

beforeEach(() => {
  useSessionStore.setState({
    pendingNotes: [], feedReceipts: [], clarifyItems: [], notesVersion: 0,
  } as never)
})

describe("noteApplied", () => {
  it("移除对应 pending + 推一条带 changes 的 receipt + bump notesVersion", () => {
    useSessionStore.setState({ pendingNotes: [pending("cid")] } as never)
    const m: NoteAppliedMsg = {
      client_id: "cid",
      changes: [{ op: "mark", take_id: 3, scene_code: "1", shot: "", take_number: 3, take_suffix: "", status: "keep" }],
      ts: 9,
    }
    useSessionStore.getState().noteApplied(m)
    const s = useSessionStore.getState()
    expect(s.pendingNotes).toHaveLength(0)
    expect(s.feedReceipts).toHaveLength(1)
    expect(s.feedReceipts[0].changes[0].status).toBe("keep")
    expect(s.feedReceipts[0].rawText).toBe("原话")
    expect(s.notesVersion).toBe(1)
  })

  it("纯 mark（无 note）也清 pending（否则 pending 挂死）", () => {
    useSessionStore.setState({ pendingNotes: [pending("cid")] } as never)
    useSessionStore.getState().noteApplied({
      client_id: "cid",
      changes: [{ op: "mark", take_id: 3, scene_code: "1", shot: "", take_number: 3, take_suffix: "", status: "pass" }],
      ts: 9,
    })
    expect(useSessionStore.getState().pendingNotes).toHaveLength(0)
  })

  it("client_id 为 null 时不动 pending，仅 bump version", () => {
    useSessionStore.setState({ pendingNotes: [pending("cid")] } as never)
    useSessionStore.getState().noteApplied({ client_id: null, changes: [], ts: 9 })
    expect(useSessionStore.getState().pendingNotes).toHaveLength(1)
    expect(useSessionStore.getState().notesVersion).toBe(1)
  })
})

describe("noteClarify", () => {
  it("移除对应 pending + 推一条 clarify item", () => {
    useSessionStore.setState({ pendingNotes: [pending("cid")] } as never)
    const m: NoteClarifyMsg = {
      client_id: "cid",
      message: "第1条有多条",
      candidates: [{ take_id: 1, scene_code: "1", shot: "", take_number: 1, take_suffix: "", status: "tbd" }],
      ts: 9,
    }
    useSessionStore.getState().noteClarify(m)
    const s = useSessionStore.getState()
    expect(s.pendingNotes).toHaveLength(0)
    expect(s.clarifyItems).toHaveLength(1)
    expect(s.clarifyItems[0].message).toBe("第1条有多条")
  })
})

describe("dismissClarify", () => {
  it("按 client_id 移除 clarify item", () => {
    useSessionStore.setState({
      clarifyItems: [{ client_id: "cid", message: "m", candidates: [], ts: 1 }],
    } as never)
    useSessionStore.getState().dismissClarify("cid")
    expect(useSessionStore.getState().clarifyItems).toHaveLength(0)
  })
})
