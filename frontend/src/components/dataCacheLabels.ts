import type { NodeDataCache } from "../hooks/useNodeDataCache"
import { formatByteSize } from "../utils/formatBytes"

export function dataCacheStatusText(cache: NodeDataCache): string {
  if (cache.readsDirectly) return "Reads Parquet directly"
  switch (cache.availability) {
    case "current":
      return "Cached"
    case "partial":
      return "Cached for some columns"
    case "stale":
      return "Cache stale"
    case "building":
      return cache.message || "Caching"
    case "corrupt":
      return "Cache unreadable"
    case "checking":
      return "Checking cache"
    default:
      return "Not cached"
  }
}

/** The snapshot's size and retention, or null when there is nothing cached. */
export function dataCacheDetail(cache: NodeDataCache): string | null {
  if (cache.sizeBytes === null && cache.rowCount === null) return null
  const parts: string[] = []
  if (cache.rowCount !== null) parts.push(`${cache.rowCount.toLocaleString()} rows`)
  if (cache.sizeBytes !== null) parts.push(formatByteSize(cache.sizeBytes))
  if (cache.retention) parts.push(cache.retention === "pinned" ? "pinned" : "automatic")
  return parts.join(" · ")
}
