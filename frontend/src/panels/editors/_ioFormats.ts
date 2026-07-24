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

// Module-level cache: the payload is static per backend process, so one
// fetch serves every editor mount for the session.
let cachedCapabilities: IoCapabilitiesResponse | null = null
let inflight: Promise<IoCapabilitiesResponse> | null = null

function loadIoCapabilities(): Promise<IoCapabilitiesResponse> {
  if (cachedCapabilities) return Promise.resolve(cachedCapabilities)
  if (!inflight) {
    inflight = fetchIoCapabilities()
      .then((res) => {
        cachedCapabilities = res
        return res
      })
      .catch((err: unknown) => {
        inflight = null // allow a retry on the next editor mount
        throw err
      })
  }
  return inflight
}

/** Test hook: clear the module-level capability cache between tests. */
export function resetIoCapabilitiesCacheForTests(): void {
  cachedCapabilities = null
  inflight = null
}

/** Fetch (once per session) the format capability payload. */
export function useIoCapabilities(): { capabilities: IoCapabilitiesResponse | null; error: string | null } {
  const [capabilities, setCapabilities] = useState<IoCapabilitiesResponse | null>(cachedCapabilities)
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
