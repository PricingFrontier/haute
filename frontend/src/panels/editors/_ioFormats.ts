/**
 * Capability-payload plumbing for the dataInput / dataOutput editors.
 *
 * GET /api/io-capabilities is the single source of format knowledge (io-nodes
 * review IO12): format options, mode options, argument-name lists and
 * missing-engine flags all derive from it — neither this module nor the
 * editors hard-code a format name.
 */
import { useEffect, useState } from "react"
import { fetchIoCapabilities } from "../../api/client"
import type { IoCapabilitiesResponse } from "../../api/types"

// Coalesce consumers that mount while the same request is still pending. A
// later mount starts a fresh request so backend capability changes are visible
// without requiring a frontend reload.
let inflight: Promise<IoCapabilitiesResponse> | null = null

function loadIoCapabilities(): Promise<IoCapabilitiesResponse> {
  if (!inflight) {
    const request = fetchIoCapabilities()
    inflight = request
    request.then(
      () => {
        if (inflight === request) inflight = null
      },
      () => {
        if (inflight === request) inflight = null
      },
    )
  }
  return inflight
}

/** Test hook: clear the module-level in-flight request between tests. */
export function resetIoCapabilitiesCacheForTests(): void {
  inflight = null
}

/** Fetch the format capability payload for this mount. */
export function useIoCapabilities(): { capabilities: IoCapabilitiesResponse | null; error: string | null } {
  const [capabilities, setCapabilities] = useState<IoCapabilitiesResponse | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    loadIoCapabilities()
      .then((result) => {
        if (!cancelled) setCapabilities(result)
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(e instanceof Error ? e.message : "Failed to load formats")
      })
    return () => { cancelled = true }
  }, [])

  return { capabilities, error }
}
