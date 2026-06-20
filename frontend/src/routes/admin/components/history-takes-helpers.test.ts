import { describe, expect, it } from "vitest"
import type { TakeDTO } from "@/types/api"
import { sortTakes, historyListState } from "./history-takes-helpers"

function take(p: Partial<TakeDTO>): TakeDTO {
  return { scene_id: 0, shot: null, take_number: 1, ...p } as TakeDTO
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
