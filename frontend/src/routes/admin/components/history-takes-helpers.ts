import type { TakeDTO, TakeStatus } from "@/types/api"

/** start_ts 最大那条 take 的 scene_id(忽略 start_ts<=0 占位);空/全占位返回 null。 */
export function latestSceneId(takes: TakeDTO[]): number | null {
  let best: TakeDTO | null = null
  for (const t of takes) {
    if (t.start_ts > 0 && (best === null || t.start_ts > best.start_ts)) best = t
  }
  return best ? best.scene_id : null
}

/** 历史 take 列表排序:scene_id → shot(localeCompare)→ take_number。返回新数组,不原地改。 */
export function sortTakes(takes: TakeDTO[]): TakeDTO[] {
  return [...takes].sort(
    (a, b) =>
      a.scene_id - b.scene_id ||
      (a.shot ?? "").localeCompare(b.shot ?? "") ||
      a.take_number - b.take_number,
  )
}

export type StatusCounts = Record<TakeStatus, number>

export type HistoryRow =
  | { kind: "scene"; key: string; sceneId: number; takeCount: number; counts: StatusCounts; collapsed: boolean }
  | { kind: "shot"; key: string; sceneId: number; shot: string }
  | { kind: "take"; key: string; take: TakeDTO }

function countStatuses(takes: TakeDTO[]): StatusCounts {
  const counts: StatusCounts = { keep: 0, ng: 0, pass: 0, tbd: 0 }
  for (const t of takes) counts[t.status] = (counts[t.status] ?? 0) + 1
  return counts
}

/** 扁平异构行(scene 头 / shot 头 / take 卡)。空 expandedScenes = 全折叠;collapsed = !expandedScenes.has(sceneId)。 */
export function buildHistoryRows(takes: TakeDTO[], expandedScenes: Set<number>): HistoryRow[] {
  const sorted = sortTakes(takes)
  const rows: HistoryRow[] = []
  let i = 0
  while (i < sorted.length) {
    const sceneId = sorted[i].scene_id
    const sceneTakes: TakeDTO[] = []
    while (i < sorted.length && sorted[i].scene_id === sceneId) {
      sceneTakes.push(sorted[i])
      i++
    }
    const collapsed = !expandedScenes.has(sceneId)
    rows.push({
      kind: "scene",
      key: `scene-${sceneId}`,
      sceneId,
      takeCount: sceneTakes.length,
      counts: countStatuses(sceneTakes),
      collapsed,
    })
    if (collapsed) continue
    const hasNamedShot = sceneTakes.some((t) => t.shot != null && t.shot !== "")
    let lastShot: string | null = null
    for (const take of sceneTakes) {
      const shot = take.shot && take.shot !== "" ? take.shot : null
      if (hasNamedShot && shot !== null && shot !== lastShot) {
        rows.push({ kind: "shot", key: `shot-${sceneId}-${shot}`, sceneId, shot })
        lastShot = shot
      }
      rows.push({ kind: "take", key: `take-${take.take_id}`, take })
    }
  }
  return rows
}

export type HistoryListState = "loading" | "error" | "empty" | "list"

/**
 * history 列表渲染态。有数据优先:takeCount>0 恒 list(瞬时 loading/error 不打断);
 * 无数据时 error>loading>empty —— 401 冷启(isError + 空)显 error,不再伪装成「暂无 take」。
 */
export function historyListState(
  isLoading: boolean,
  isError: boolean,
  takeCount: number,
): HistoryListState {
  if (takeCount > 0) return "list"
  if (isError) return "error"
  if (isLoading) return "loading"
  return "empty"
}

/** Unix 秒 → 本地 "MM-DD HH:mm";占位(<=0)返回 "—"。 */
export function formatTakeTimestamp(startTs: number): string {
  if (startTs <= 0) return "—"
  const d = new Date(startTs * 1000)
  const p = (n: number) => String(n).padStart(2, "0")
  return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}

/** active 空集=不筛返回全部;否则只留 status 命中的。返回新数组。 */
export function filterTakesByStatus(takes: TakeDTO[], active: Set<TakeStatus>): TakeDTO[] {
  if (active.size === 0) return [...takes]
  return takes.filter((t) => active.has(t.status))
}
