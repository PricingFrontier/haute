import { useState, useEffect, useRef, useMemo } from "react"
import { previewNode } from "../api/client"
import useGraphStore, { computeStructuralFingerprint } from "../stores/useGraphStore"
import useNodeResultsStore from "../stores/useNodeResultsStore"
import useSettingsStore from "../stores/useSettingsStore"
import useToastStore from "../stores/useToastStore"
import { buildGraph } from "../utils/buildGraph"
import type { SimpleNode, SimpleEdge } from "../panels/editors/_shared"

type DataInputColumn = { name: string; dtype: string }

type UseDataInputColumnsOptions = {
  enabled?: boolean
  fallbackColumns?: DataInputColumn[]
}

const EMPTY_COLUMNS: DataInputColumn[] = []

/**
 * Fetches and caches columns from a data input node.
 * Shows cached columns immediately (no loading flash), re-fetches if stale.
 * Reads the active source from useSettingsStore so source-switch nodes
 * resolve through the same path as the normal preview.
 * Uses AbortController to cancel stale requests on rapid input switching.
 * Cache is keyed by (nodeId, source) so switching sources doesn't serve
 * stale columns from a different data path.
 */
export function useDataInputColumns(
  dataInput: string,
  allNodes: SimpleNode[],
  edges: SimpleEdge[],
  submodels?: Record<string, unknown>,
  preamble?: string,
  options: UseDataInputColumnsOptions = {},
): DataInputColumn[] {
  const setColumnsCache = useNodeResultsStore((s) => s.setColumns)
  const activeSource = useSettingsStore((s) => s.activeSource)
  const structuralVersion = useGraphStore((s) => s.structuralVersion)
  const enabled = options.enabled ?? true
  const fallbackColumns = options.fallbackColumns ?? EMPTY_COLUMNS
  const fallbackColumnsJson = useMemo(() => JSON.stringify(fallbackColumns), [fallbackColumns])

  // Source-aware cache key: "nodeId:source"
  const cacheKey = dataInput ? `${dataInput}:${activeSource}` : ""

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

  const graphFingerprint = useMemo(
    () => computeStructuralFingerprint(allNodes, edges, preamble),
    [allNodes, edges, preamble],
  )

  // Keep fresh refs for allNodes/edges so the effect body reads current data
  const allNodesRef = useRef(allNodes)
  const edgesRef = useRef(edges)
  const fallbackColumnsRef = useRef(fallbackColumns)
  useEffect(() => { allNodesRef.current = allNodes }, [allNodes])
  useEffect(() => { edgesRef.current = edges }, [edges])
  useEffect(() => { fallbackColumnsRef.current = fallbackColumns }, [fallbackColumns])

  const initialColumns = enabled ? (cachedColumns ?? fallbackColumns) : fallbackColumns
  const [dataInputColumns, setDataInputColumns] = useState<DataInputColumn[]>(
    initialColumns,
  )

  // Read cache state via refs so the fetch effect doesn't re-fire when the
  // store is updated with fresh columns (which would cause an infinite loop:
  // effect fires → setColumnsCache → cachedColumns changes → effect re-fires).
  const cachedColumnsRef = useRef(cachedColumns)
  const isCacheFreshRef = useRef(isCacheFresh)
  useEffect(() => { cachedColumnsRef.current = cachedColumns }, [cachedColumns])
  useEffect(() => { isCacheFreshRef.current = isCacheFresh }, [isCacheFresh])

  // Sync local state when cache is populated externally (e.g. by another hook
  // instance or a WebSocket graph refresh).  Compare by content to avoid
  // triggering a re-render when the store creates a new array reference
  // for the same column data.
  const prevCacheJson = useRef(JSON.stringify(initialColumns))
  useEffect(() => {
    if (!enabled) return
    if (!cachedColumns) return
    const json = JSON.stringify(cachedColumns)
    if (json !== prevCacheJson.current) {
      prevCacheJson.current = json
      // eslint-disable-next-line react-hooks/set-state-in-effect -- syncing store-owned cache into local state only when content actually changes (ref-equal but value-different arrays are skipped via JSON compare)
      setDataInputColumns(cachedColumns)
    }
  }, [cachedColumns, enabled])

  useEffect(() => {
    if (!enabled) {
      if (fallbackColumnsJson !== prevCacheJson.current) {
        prevCacheJson.current = fallbackColumnsJson
        setDataInputColumns(fallbackColumnsRef.current)
      }
      return
    }
    if (!dataInput) {
      if (fallbackColumnsJson !== prevCacheJson.current) {
        prevCacheJson.current = fallbackColumnsJson
        setDataInputColumns(fallbackColumnsRef.current)
      }
      return
    }
    // Show cached columns immediately (no loading flash)
    if (cachedColumnsRef.current) {
      setDataInputColumns(cachedColumnsRef.current)
      if (isCacheFreshRef.current) return // cache is current, skip API call
    }
    // Abort in-flight request when deps change (prevents stale responses overwriting fresh data)
    const controller = new AbortController()
    // Fetch fresh columns (cached value shown meanwhile)
    const requestStructuralVersion = structuralVersion
    const graph = buildGraph(allNodesRef.current, edgesRef.current, submodels, preamble)
    previewNode(graph, dataInput, 1, activeSource, { signal: controller.signal })
      .then((result) => {
        if (result.columns) {
          const json = JSON.stringify(result.columns)
          // Only update local state if columns actually changed (avoids re-render cascade)
          if (json !== prevCacheJson.current) {
            prevCacheJson.current = json
            setDataInputColumns(result.columns)
          }
          setColumnsCache(dataInput, result.columns, requestStructuralVersion, activeSource)
        }
      })
      .catch((e) => {
        if (e instanceof DOMException && e.name === "AbortError") return
        const detail = e instanceof Error ? e.message : String(e)
        addToast("warning", `Column fetch failed for "${dataInput}": ${detail}`)
        if (!cachedColumnsRef.current) setDataInputColumns([])
      })
    return () => controller.abort()
    // cachedColumns and isCacheFresh intentionally read via refs to break the
    // store-update → effect-refire loop.  The effect should only re-run when
    // the *inputs* change (dataInput, graph structure, source), not when the
    // *output* (cached columns) changes.
  }, [
    dataInput,
    structuralVersion,
    graphFingerprint,
    submodels,
    preamble,
    activeSource,
    setColumnsCache,
    addToast,
    enabled,
    fallbackColumnsJson,
  ])

  return dataInputColumns
}
