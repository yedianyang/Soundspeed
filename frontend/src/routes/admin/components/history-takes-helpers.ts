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
