import type { InputCacheSnapshotResponse } from "../api/types"
import { formatRelativeTime } from "./formatTime"

/** When a Data Input's data was last imported, from its snapshot status. */
export function importedTitle(status: InputCacheSnapshotResponse, now: Date): string {
  if (!status.generation) return "Not imported yet"
  return `Imported ${formatRelativeTime(status.generation.created_at, now)}`
}
