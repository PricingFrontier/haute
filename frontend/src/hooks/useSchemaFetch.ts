import { useState, useEffect, useCallback } from "react"
import { fetchSchema } from "../api/client"
import type { SchemaReader } from "../api/client"
import { apiErrorMessage } from "../api/errors"
import type { SchemaInfo } from "../panels/editors/_shared"
import { newest, signalPart, answer } from "../utils/answers"
import { useRequestKind } from "./useRequestKind"

/** One schema read: the file and reader settings it is for, and how it went. */
type SchemaRead =
  | { identity: string; status: "loading" }
  | { identity: string; status: "ready"; schema: SchemaInfo }
  | { identity: string; status: "error"; error: string }

function readIdentity(path: string, readerKey: string): string {
  return JSON.stringify([path, readerKey])
}

/**
 * Shared hook for fetching file schema (columns, preview, row count).
 *
 * Used by data-input and API-input editors to avoid duplicating
 * the same fetch-schema-on-mount + fetch-on-select pattern. With *reader*, the
 * file is read with those Data Input reader settings, and a change to them
 * fetches again.
 *
 * A schema belongs to the path and reader settings it was read with: the hook
 * gives a schema, an error or a load only for the current ones, so a schema
 * read for another file or other settings (an earlier one, or a late reply)
 * is never shown or applied, and a cleared path has none.
 */
export function useSchemaFetch(path?: string, reader?: SchemaReader) {
  const readerKey = reader ? JSON.stringify(reader) : ""
  const identity = path ? readIdentity(path, readerKey) : ""
  const [read, setRead] = useState<SchemaRead | null>(null)
  // The read last asked for: a reply to any other is stale, and so is one whose caller let go.
  const reads = useRequestKind()

  const fetchForPath = useCallback((fetchPath: string, signal?: AbortSignal) => {
    const asked = readIdentity(fetchPath, readerKey)
    setRead({ identity: asked, status: "loading" })
    const parts = signal ? [newest(reads), signalPart(signal)] as const : [newest(reads)] as const
    void answer(parts, (own) => fetchSchema(fetchPath, { signal: signal ?? own }, readerKey ? (JSON.parse(readerKey) as SchemaReader) : undefined), {
      ok: (data) => setRead({ identity: asked, status: "ready", schema: data }),
      failed: (err) => setRead({ identity: asked, status: "error", error: apiErrorMessage(err) }),
    })
  }, [readerKey, reads])

  // Fetch whenever the path or the reader settings change; abort on cleanup.
  useEffect(() => {
    if (!path) return

    const controller = new AbortController()
    // eslint-disable-next-line react-hooks/set-state-in-effect -- fetch-on-change: fetchForPath records the request it starts
    fetchForPath(path, controller.signal)

    return () => {
      controller.abort()
    }
  }, [path, fetchForPath])

  const current = identity !== "" && read?.identity === identity ? read : null
  return {
    schema: current?.status === "ready" ? current.schema : null,
    loading: identity !== "" && (current === null || current.status === "loading"),
    error: current?.status === "error" ? current.error : null,
    fetchForPath,
  }
}
