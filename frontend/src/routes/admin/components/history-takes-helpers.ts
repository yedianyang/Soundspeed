import type { TakeDTO } from "@/types/api"

/** 历史 take 列表排序:scene_id → shot(localeCompare)→ take_number。返回新数组,不原地改。 */
export function sortTakes(takes: TakeDTO[]): TakeDTO[] {
  return [...takes].sort(
    (a, b) =>
      a.scene_id - b.scene_id ||
      (a.shot ?? "").localeCompare(b.shot ?? "") ||
      a.take_number - b.take_number,
  )
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
