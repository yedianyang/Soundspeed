import { HISTORY_TAKES, CURRENT_TAKE, CURRENT_PARTIAL } from "@/data/mock"
import { TakeBlock, TakeDivider } from "./TakeBlock"

export function LiveTranscript() {
  return (
    <div className="px-3 sm:px-5 py-4 space-y-4">
      {HISTORY_TAKES.slice(-2).map((take) => (
        <TakeBlock key={take.id} take={take} muted />
      ))}

      <TakeDivider no={CURRENT_TAKE.no} time="14:31:24" />

      <TakeBlock take={CURRENT_TAKE} partial={CURRENT_PARTIAL} />
    </div>
  )
}
