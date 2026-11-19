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
import { apiErrorMessage } from "../../api/errors"
import { useRequestKind } from "../../hooks/useRequestKind"
import { answer, ask, newest, requestKind } from "../../utils/answers"

// Coalesce consumers that mount while the same request is still pending. A
// later mount starts a fresh request so backend capability changes are visible
// without requiring a frontend reload.
let inflight: Promise<IoCapabilitiesResponse> | null = null
/** The request held in `inflight`: each one sent takes a turn, and a reset ends it. */
const capabilitiesAsked = requestKind()

function loadIoCapabilities(): Promise<IoCapabilitiesResponse> {
  if (!inflight) {
    const request = fetchIoCapabilities()
    inflight = request
    const settle = () => {
      inflight = null
    }
    void answer([newest(capabilitiesAsked)], request, { ok: settle, failed: settle })
  }
  return inflight
}

/** Test hook: clear the module-level in-flight request between tests. */
export function resetIoCapabilitiesRequestForTests(): void {
  inflight = null
  capabilitiesAsked.bump()
}

/** Fetch the format capability payload for this mount. */
export function useIoCapabilities(): { capabilities: IoCapabilitiesResponse | null; error: string | null } {
  const [capabilities, setCapabilities] = useState<IoCapabilitiesResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const reads = useRequestKind()

  useEffect(() => {
    const asked = ask([newest(reads)])
    void asked.answer(loadIoCapabilities(), {
      ok: (result) => setCapabilities(result),
      failed: (e) => setError(apiErrorMessage(e, "Failed to load formats")),
    })
    return () => asked.release()
  }, [reads])

  return { capabilities, error }
}
