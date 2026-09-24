import { useEffect, useState } from "react"

import { fetchCacheUsage } from "../api/client"
import type { CacheUsageResponse } from "../api/types"
import { formatByteSize } from "../utils/formatBytes"

export interface CacheStoreSizeProps {
  /** Read the size again whenever this changes; `null` keeps the last reading. */
  refreshKey: unknown
}

/**
 * How much the project's snapshot store holds, for the preview status bar.
 *
 * Read when a preview settles rather than on a timer, because previews are
 * what add captures to the store. A failed read shows nothing: the number
 * is for information, and the cached-data inventory is where to act on it.
 */
export default function CacheStoreSize({ refreshKey }: CacheStoreSizeProps) {
  const [usage, setUsage] = useState<CacheUsageResponse | null>(null)

  useEffect(() => {
    if (refreshKey === null) return
    const controller = new AbortController()
    fetchCacheUsage({ signal: controller.signal }).then(
      (next) => setUsage(next),
      () => {
        if (!controller.signal.aborted) setUsage(null)
      },
    )
    return () => controller.abort()
  }, [refreshKey])

  if (usage === null) return null
  return (
    <span
      data-testid="cache-store-size"
      className="text-[11px] whitespace-nowrap"
      style={{ color: "var(--text-muted)" }}
      title={
        `Cached data: ${formatByteSize(usage.total_bytes)}. Automatic captures hold ` +
        `${formatByteSize(usage.automatic_bytes)} of their ${formatByteSize(usage.automatic_budget_bytes)} ` +
        "budget; the least recently used go first. Input snapshots and explicit builds " +
        "stay until you clear them in Pipeline settings."
      }
    >
      {formatByteSize(usage.total_bytes)} cached
    </span>
  )
}
