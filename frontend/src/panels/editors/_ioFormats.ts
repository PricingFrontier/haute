/**
 * Capability-payload plumbing for the dataInput / dataOutput editors.
 *
 * GET /api/formats is the single source of format knowledge (io-nodes
 * review IO12): format options, mode options, argument-name lists and
 * missing-engine flags all derive from it — neither this module nor the
 * editors hard-code a format name.
 */
import { useEffect, useState } from "react"
import { fetchIoFormats } from "../../api/client"
import type { IoFormatCapability } from "../../api/types"

export type IoSide = "input" | "output"

/** Which capability fields drive each editor side. */
export const IO_SIDE_SPECS: Record<IoSide, {
  availableKey: "read_available" | "write_available"
  enginesMissingKey: "read_engines_missing" | "write_engines_missing"
  modesKey: "input_modes" | "output_modes"
  argumentsKey: "input_arguments" | "output_arguments"
  /** Config key for the database-format target field, and its label. */
  databaseTargetKey: "query" | "table"
  databaseTargetLabel: string
}> = {
  input: {
    availableKey: "read_available",
    enginesMissingKey: "read_engines_missing",
    modesKey: "input_modes",
    argumentsKey: "input_arguments",
    databaseTargetKey: "query",
    databaseTargetLabel: "Query",
  },
  output: {
    availableKey: "write_available",
    enginesMissingKey: "write_engines_missing",
    modesKey: "output_modes",
    argumentsKey: "output_arguments",
    databaseTargetKey: "table",
    databaseTargetLabel: "Table",
  },
}

// Module-level cache: the payload is static per backend process, so one
// fetch serves every editor mount for the session.
let cachedFormats: IoFormatCapability[] | null = null
let inflight: Promise<IoFormatCapability[]> | null = null

function loadIoFormats(): Promise<IoFormatCapability[]> {
  if (cachedFormats) return Promise.resolve(cachedFormats)
  if (!inflight) {
    inflight = fetchIoFormats()
      .then((res) => {
        cachedFormats = res.formats
        return res.formats
      })
      .catch((err: unknown) => {
        inflight = null // allow a retry on the next editor mount
        throw err
      })
  }
  return inflight
}

/** Test hook: clear the module-level capability cache between tests. */
export function resetIoFormatsCacheForTests(): void {
  cachedFormats = null
  inflight = null
}

/** Fetch (once per session) the format capability payload. */
export function useIoFormats(): { formats: IoFormatCapability[] | null; error: string | null } {
  const [formats, setFormats] = useState<IoFormatCapability[] | null>(cachedFormats)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    loadIoFormats()
      .then((f) => {
        if (!cancelled) setFormats(f)
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(e instanceof Error ? e.message : "Failed to load formats")
      })
    return () => { cancelled = true }
  }, [])

  return { formats, error }
}
