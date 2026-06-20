import { describe, expect, it } from "vitest"
import type { TakeDTO } from "@/types/api"
import { sortTakes, historyListState, buildHistoryRows } from "./history-takes-helpers"

function take(p: Partial<TakeDTO>): TakeDTO {
  return { scene_id: 0, shot: null, take_number: 1, status: "tbd", start_ts: 0, ...p } as TakeDTO
}

describe("sortTakes", () => {
  it("按 scene_id → shot → take_number 升序,且不原地改入参", () => {
    const input = [
      take({ scene_id: 2, shot: "A", take_number: 1 }),
      take({ scene_id: 1, shot: "B", take_number: 2 }),
      take({ scene_id: 1, shot: "A", take_number: 3 }),
      take({ scene_id: 1, shot: "A", take_number: 1 }),
    ]
    const out = sortTakes(input)
    expect(out.map((t) => [t.scene_id, t.shot, t.take_number])).toEqual([
      [1, "A", 1], [1, "A", 3], [1, "B", 2], [2, "A", 1],
    ])
    expect(input[0].scene_id).toBe(2) // 入参未被原地排序
  })

  it("shot 为 null 用空串参与比较,不抛错", () => {
    const out = sortTakes([
      take({ scene_id: 1, shot: null, take_number: 2 }),
      take({ scene_id: 1, shot: "A", take_number: 1 }),
    ])
    expect(out.map((t) => t.shot)).toEqual([null, "A"])
  })
})

describe("historyListState", () => {
  it("401 冷启(无数据 + isError)→ error,不再伪装成 empty", () => {
    expect(historyListState(false, true, 0)).toBe("error")
  })
  it("加载中无数据 → loading", () => {
    expect(historyListState(true, false, 0)).toBe("loading")
  })
  it("加载完无数据无错 → empty", () => {
    expect(historyListState(false, false, 0)).toBe("empty")
  })
  it("有数据 → list", () => {
    expect(historyListState(false, false, 5)).toBe("list")
  })
  it("有数据时瞬时 error 不打断列表", () => {
    expect(historyListState(false, true, 5)).toBe("list")
  })
  it("有数据时后台 loading 不打断列表", () => {
    expect(historyListState(true, false, 5)).toBe("list")
  })
})

describe("buildHistoryRows", () => {
  it("空 expandedScenes = 全折叠,每 scene 只出 scene 行含 take 数与 4 态计数", () => {
    const rows = buildHistoryRows(
      [
        take({ take_id: 1, scene_id: 1, shot: null, take_number: 1, status: "keep" }),
        take({ take_id: 2, scene_id: 1, shot: null, take_number: 2, status: "ng" }),
      ],
      new Set(),
    )
    expect(rows.map((r) => r.kind)).toEqual(["scene"])
    const scene = rows[0]
    expect(scene).toMatchObject({ kind: "scene", sceneId: 1, takeCount: 2, collapsed: true })
    expect(scene.kind === "scene" && scene.counts).toMatchObject({ keep: 1, ng: 1, pass: 0, tbd: 0 })
  })

  it("展开的 scene(在 expandedScenes 里)出 take 行,collapsed=false", () => {
    const rows = buildHistoryRows(
      [take({ take_id: 1, scene_id: 1, shot: null, take_number: 1 })],
      new Set([1]),
    )
    expect(rows.map((r) => r.kind)).toEqual(["scene", "take"])
    expect(rows[0].kind === "scene" && rows[0].collapsed).toBe(false)
  })

  it("展开态:有非空 shot 才插 shot 行;null-shot 的 take 直接出", () => {
    const rows = buildHistoryRows(
      [
        take({ take_id: 1, scene_id: 1, shot: null, take_number: 1 }),
        take({ take_id: 2, scene_id: 1, shot: "A", take_number: 1 }),
        take({ take_id: 3, scene_id: 1, shot: "A", take_number: 2 }),
      ],
      new Set([1]),
    )
    // sortTakes 把 null(→"")排在 "A" 前;null-shot take 先出(无 shot 头),再 shot A 头 + 两条
    expect(rows.map((r) => r.kind)).toEqual(["scene", "take", "shot", "take", "take"])
    expect(rows.find((r) => r.kind === "shot")).toMatchObject({ kind: "shot", sceneId: 1, shot: "A" })
  })

  it("展开态:全 null-shot 的场平铺,不出任何 shot 行", () => {
    const rows = buildHistoryRows(
      [
        take({ take_id: 1, scene_id: 2, shot: null, take_number: 1 }),
        take({ take_id: 2, scene_id: 2, shot: null, take_number: 2 }),
      ],
      new Set([2]),
    )
    expect(rows.map((r) => r.kind)).toEqual(["scene", "take", "take"])
  })

  it("多 scene 按 scene_id 升序分段;key 唯一", () => {
    const rows = buildHistoryRows(
      [
        take({ take_id: 9, scene_id: 2, shot: null, take_number: 1 }),
        take({ take_id: 1, scene_id: 1, shot: null, take_number: 1 }),
      ],
      new Set([1, 2]),
    )
    const sceneRows = rows.filter((r) => r.kind === "scene")
    expect(sceneRows.map((r) => r.kind === "scene" && r.sceneId)).toEqual([1, 2])
    const keys = rows.map((r) => r.key)
    expect(new Set(keys).size).toBe(keys.length)
  })
})
