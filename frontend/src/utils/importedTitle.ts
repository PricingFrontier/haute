import type { InputCacheSnapshotResponse } from "../api/types"
import { relativeTime } from "./relativeTime"

/** When a Data Input's data was last imported, from its snapshot status. */
export function importedTitle(status: InputCacheSnapshotResponse, now: Date): string {
  if (!status.generation) return "Not imported yet"
  return `Imported ${relativeTime(new Date(status.generation.created_at * 1000), now)}`
}
