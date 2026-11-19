import { useEffect, useState } from "react"

import { fetchCacheUsage } from "../api/client"
import type { CacheUsageResponse } from "../api/types"
import { useRequestKind } from "../hooks/useRequestKind"
import { ask, newest } from "../utils/answers"
import { formatByteSize } from "../utils/formatBytes"

export interface CacheStoreSizeProps {
  /** Read the size again whenever this changes; `null` keeps the last reading. */
  refreshKey: unknown
}

/**
 * How much the project's snapshot store holds, beside the cached-data
 * inventory in Pipeline settings.
 *
 * Read with each inventory read rather than on a timer. A failed read shows
 * nothing: the number is for information, and the inventory is where to act
 * on it.
 */
export default function CacheStoreSize({ refreshKey }: CacheStoreSizeProps) {
  const [usage, setUsage] = useState<CacheUsageResponse | null>(null)
  const reads = useRequestKind()

  useEffect(() => {
    if (refreshKey === null) return
    const asked = ask([newest(reads)])
    void asked.answer((signal) => fetchCacheUsage({ signal }), {
      ok: (next) => setUsage(next),
      failed: () => setUsage(null),
    })
    return () => asked.release()
  }, [reads, refreshKey])

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
        "stay until you clear them below."
      }
    >
      {formatByteSize(usage.total_bytes)} cached
    </span>
  )
}
