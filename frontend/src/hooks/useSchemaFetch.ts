import { useState, useEffect, useCallback, useRef } from "react"
import { fetchSchema } from "../api/client"
import type { SchemaInfo } from "../panels/editors/_shared"

/**
 * Shared hook for fetching file schema (columns, preview, row count).
 *
 * Used by DataSourceEditor and ApiInputEditor to avoid duplicating
 * the same fetch-schema-on-mount + fetch-on-select pattern.
 */
export function useSchemaFetch(initialPath?: string) {
  const [schema, setSchema] = useState<SchemaInfo>(null)
  const [loading, setLoading] = useState(!!initialPath)
  const [error, setError] = useState<string | null>(null)
  const abortRef = useRef<AbortController | null>(null)

  const fetchForPath = useCallback((path: string, signal?: AbortSignal) => {
    setLoading(true)
    setError(null)
    fetchSchema(path, { signal })
      .then((data) => {
        if (signal?.aborted) return
        setSchema(data)
        setLoading(false)
      })
      .catch((err: unknown) => {
        if (signal?.aborted || (err instanceof DOMException && err.name === "AbortError")) return
        setSchema(null)
        setError(err instanceof Error ? err.message : String(err))
        setLoading(false)
      })
  }, [])

  // Auto-fetch on mount when path exists; abort on cleanup
  useEffect(() => {
    if (!initialPath) return

    const controller = new AbortController()
    abortRef.current = controller

    // eslint-disable-next-line react-hooks/set-state-in-effect -- fetch-on-mount: fetchForPath sets state asynchronously via .then()
    fetchForPath(initialPath, controller.signal)

    return () => {
      controller.abort()
    }
  }, [initialPath, fetchForPath])

  return { schema, setSchema, loading, error, fetchForPath }
}
