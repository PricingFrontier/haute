import type { NodeDataCache } from "../hooks/useNodeDataCache"

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

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  const units = ["KB", "MB", "GB", "TB"]
  let value = bytes / 1024
  let unit = 0
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024
    unit += 1
  }
  return `${value >= 10 ? Math.round(value) : value.toFixed(1)} ${units[unit]}`
}

/** The snapshot's size and retention, or null when there is nothing cached. */
export function dataCacheDetail(cache: NodeDataCache): string | null {
  if (cache.sizeBytes === null && cache.rowCount === null) return null
  const parts: string[] = []
  if (cache.rowCount !== null) parts.push(`${cache.rowCount.toLocaleString()} rows`)
  if (cache.sizeBytes !== null) parts.push(formatBytes(cache.sizeBytes))
  if (cache.retention) parts.push(cache.retention === "pinned" ? "pinned" : "automatic")
  return parts.join(" · ")
}
