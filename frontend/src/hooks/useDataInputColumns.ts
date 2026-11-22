import { useRequestScope, useResultKey } from "../panels/useGraph"
import { useCallback, useState, useEffect, useRef, useMemo } from "react"
import { previewNode } from "../api/client"
import useGraphStore, { computeStructuralFingerprint } from "../stores/useGraphStore"
import useNodeResultsStore from "../stores/useNodeResultsStore"
import useSettingsStore from "../stores/useSettingsStore"
import useToastStore from "../stores/useToastStore"
import { canvasTarget, prepareNodeRequest } from "../utils/requestGateway"
import { columnFingerprint } from "../utils/columnFingerprint"
import { ask, newest } from "../utils/answers"
import { useRequestKind } from "./useRequestKind"
import type { SimpleNode, SimpleEdge } from "../panels/editors/_shared"
import { apiErrorMessage } from "../api/errors"

type DataInputColumn = { name: string; dtype: string }

type UseDataInputColumnsOptions = {
  enabled?: boolean
  fallbackColumns?: DataInputColumn[]
  /** The input's output read, when it has named outputs (a collapsed submodel's `out__<port>`). */
  handle?: string | null
}

const EMPTY_COLUMNS: DataInputColumn[] = []

/**
 * Fetches and caches columns from a data input node.
 * Shows cached columns immediately (no loading flash), re-fetches if stale.
 * Reads the active source from useSettingsStore so source-switch nodes
 * resolve through the same path as the normal preview.
 * Uses AbortController to cancel stale requests on rapid input switching.
 * Cache is keyed by (the input's result key, source) so switching sources
 * doesn't serve stale columns from a different data path, and an input inside
 * a submodel keeps each occurrence's columns apart.
 */
export function useDataInputColumns(
  dataInput: string,
  allNodes: SimpleNode[],
  edges: SimpleEdge[],
  submodels?: Record<string, unknown>,
  preamble?: string,
  options: UseDataInputColumnsOptions = {},
): DataInputColumn[] {
  const requestScope = useRequestScope()
  const setColumnsCache = useNodeResultsStore((s) => s.setColumns)
  const activeSource = useSettingsStore((s) => s.activeSource)
  const structuralVersion = useGraphStore((s) => s.structuralVersion)
  const enabled = options.enabled ?? true
  const fallbackColumns = options.fallbackColumns ?? EMPTY_COLUMNS
  const handle = options.handle ?? null
  const fallbackColumnsFingerprint = useMemo(
    () => columnFingerprint(fallbackColumns),
    [fallbackColumns],
  )

  // Source-aware cache key: "<result key>:source", the input's result key (with the
  // output read, for an input of several) taken here and used after the fetch. An
  // empty id is no input.
  const resultKey = useResultKey(dataInput || null)
  const inputKey = resultKey && handle ? `${resultKey}#${handle}` : resultKey
  const cacheKey = inputKey ? `${inputKey}:${activeSource}` : ""

  // Split into two leaf selectors so Zustand's Object.is check works
  // (avoids creating a new object on every store update)
  const cachedColumns = useNodeResultsStore((s) =>
    cacheKey ? s.columnCache[cacheKey]?.columns ?? null : null,
  )
  const isCacheFresh = useNodeResultsStore((s) => {
    if (!cacheKey) return false
    const entry = s.columnCache[cacheKey]
    return entry ? entry.structuralVersion === structuralVersion : false
  })

  const addToast = useToastStore((s) => s.addToast)

  const globalConstants = useGraphStore((s) => s.globalConstants)
  const graphFingerprint = useMemo(
    () => computeStructuralFingerprint(allNodes, edges, preamble, globalConstants),
    [allNodes, edges, preamble, globalConstants],
  )

  // Keep fresh refs for allNodes/edges so the effect body reads current data
  const allNodesRef = useRef(allNodes)
  const edgesRef = useRef(edges)
  const fallbackColumnsRef = useRef(fallbackColumns)
  useEffect(() => { allNodesRef.current = allNodes }, [allNodes])
  useEffect(() => { edgesRef.current = edges }, [edges])
  useEffect(() => { fallbackColumnsRef.current = fallbackColumns }, [fallbackColumns])

  const initialColumns = enabled ? (cachedColumns ?? fallbackColumns) : fallbackColumns
  // The columns shown belong to one input: after a switch, until the effects
  // below catch up, the new input's cached (else fallback) columns are shown,
  // never the previous input's.
  const [shown, setShown] = useState<{ key: string; columns: DataInputColumn[] }>(
    { key: cacheKey, columns: initialColumns },
  )
  const setDataInputColumns = useCallback(
    (columns: DataInputColumn[]) => setShown({ key: cacheKey, columns }),
    [cacheKey],
  )

  // Read cache state via refs so the fetch effect doesn't re-fire when the
  // store is updated with fresh columns (which would cause an infinite loop:
  // effect fires → setColumnsCache → cachedColumns changes → effect re-fires).
  const columnReads = useRequestKind()
  const cachedColumnsRef = useRef(cachedColumns)
  const isCacheFreshRef = useRef(isCacheFresh)
  useEffect(() => { cachedColumnsRef.current = cachedColumns }, [cachedColumns])
  useEffect(() => { isCacheFreshRef.current = isCacheFresh }, [isCacheFresh])

  // Sync local state when cache is populated externally (e.g. by another hook
  // instance or a WebSocket graph refresh).  Compare by content to avoid
  // triggering a re-render when the store creates a new array reference
  // for the same column data.
  const prevColumnFingerprint = useRef<string | null>(null)
  if (prevColumnFingerprint.current === null) {
    prevColumnFingerprint.current = columnFingerprint(initialColumns)
  }
  useEffect(() => {
    if (!enabled) return
    if (!cachedColumns) return
    const fingerprint = columnFingerprint(cachedColumns)
    if (fingerprint !== prevColumnFingerprint.current) {
      prevColumnFingerprint.current = fingerprint
      setDataInputColumns(cachedColumns)
    }
  }, [cachedColumns, enabled, setDataInputColumns])

  useEffect(() => {
    if (!enabled) {
      if (fallbackColumnsFingerprint !== prevColumnFingerprint.current) {
        prevColumnFingerprint.current = fallbackColumnsFingerprint
        setDataInputColumns(fallbackColumnsRef.current)
      }
      return
    }
    if (!dataInput || !inputKey) {
      if (fallbackColumnsFingerprint !== prevColumnFingerprint.current) {
        prevColumnFingerprint.current = fallbackColumnsFingerprint
        setDataInputColumns(fallbackColumnsRef.current)
      }
      return
    }
    // Show cached columns immediately (no loading flash)
    if (cachedColumnsRef.current) {
      const cachedFingerprint = columnFingerprint(cachedColumnsRef.current)
      if (cachedFingerprint !== prevColumnFingerprint.current) {
        prevColumnFingerprint.current = cachedFingerprint
        setDataInputColumns(cachedColumnsRef.current)
      }
      if (isCacheFreshRef.current) return // cache is current, skip API call
    } else if (fallbackColumnsFingerprint !== prevColumnFingerprint.current) {
      prevColumnFingerprint.current = fallbackColumnsFingerprint
      setDataInputColumns(fallbackColumnsRef.current)
    }
    // The read belongs to this run of the effect: when its inputs change it is let go, and
    // aborted, so a stale response never overwrites fresh data. Cached columns show meanwhile.
    const asked = ask([newest(columnReads)])
    const requestStructuralVersion = structuralVersion
    void asked.answer((signal) => {
      const target = prepareNodeRequest(requestScope, canvasTarget(dataInput, handle))
      return previewNode({
        graph: target.graph,
        target: target.wireTarget,
        rowLimit: 1,
        source: activeSource,
        signal,
      })
    }, {
      ok: (result) => {
        const fingerprint = columnFingerprint(result.columns)
        // Only update local state if columns actually changed (avoids re-render cascade)
        if (fingerprint !== prevColumnFingerprint.current) {
          prevColumnFingerprint.current = fingerprint
          setDataInputColumns(result.columns)
        }
        setColumnsCache(inputKey, result.columns, requestStructuralVersion, activeSource)
      },
      failed: (e) => {
        const detail = apiErrorMessage(e)
        addToast("warning", `Column fetch failed for "${dataInput}": ${detail}`)
        if (!cachedColumnsRef.current) setDataInputColumns([])
      },
    })
    return () => asked.release()
    // cachedColumns and isCacheFresh intentionally read via refs to break the
    // store-update → effect-refire loop.  The effect should only re-run when
    // the *inputs* change (dataInput, graph structure, source), not when the
    // *output* (cached columns) changes.
  }, [
    requestScope,
    dataInput,
    handle,
    inputKey,
    structuralVersion,
    graphFingerprint,
    submodels,
    preamble,
    activeSource,
    setColumnsCache,
    setDataInputColumns,
    addToast,
    enabled,
    fallbackColumnsFingerprint,
    columnReads,
  ])

  return shown.key === cacheKey ? shown.columns : initialColumns
}
