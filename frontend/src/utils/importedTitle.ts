import type { InputCacheSnapshotResponse } from "../api/types"
import { formatRelativeTime } from "./formatTime"

/** When the node's data was last imported, from its snapshot status. */
export function importedTitle(status: InputCacheSnapshotResponse, now: Date): string {
  if (status.tables) {
    // A structured Quote Input: its tables are imported together, but a failed
    // import can leave some of them unpublished.
    const imported = status.tables.flatMap((table) => (table.generation ? [table.generation] : []))
    if (imported.length === 0) return "Not imported yet"
    const newest = Math.max(...imported.map((generation) => generation.created_at))
    const when = formatRelativeTime(newest, now)
    return imported.length < status.tables.length
      ? `Partly imported (${imported.length} of ${status.tables.length} tables), ${when}`
      : `Imported ${when}`
  }
  if (!status.generation) return "Not imported yet"
  return `Imported ${formatRelativeTime(status.generation.created_at, now)}`
}
