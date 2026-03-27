import { useState, useEffect, useRef, useMemo } from "react"
import { previewNode } from "../api/client"
import useNodeResultsStore from "../stores/useNodeResultsStore"
import useSettingsStore from "../stores/useSettingsStore"
import useToastStore from "../stores/useToastStore"
import { buildGraph } from "../utils/buildGraph"
import type { SimpleNode, SimpleEdge } from "../panels/editors/_shared"

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
): { name: string; dtype: string }[] {
  const setColumnsCache = useNodeResultsStore((s) => s.setColumns)
  const activeSource = useSettingsStore((s) => s.activeSource)

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
    return entry ? entry.graphVersion === s.graphVersion : false
  })

  const addToast = useToastStore((s) => s.addToast)

  // Derive a fingerprint from node IDs + edge connections to avoid array-reference deps
  const graphFingerprint = useMemo(() => {
    const nodeIds = allNodes.map(n => n.id).sort().join(",")
    const edgeIds = edges.map(e => `${e.source}-${e.target}`).sort().join(",")
    return `${nodeIds}|${edgeIds}`
  }, [allNodes, edges])

  // Keep fresh refs for allNodes/edges so the effect body reads current data
  const allNodesRef = useRef(allNodes)
  const edgesRef = useRef(edges)
  useEffect(() => { allNodesRef.current = allNodes }, [allNodes])
  useEffect(() => { edgesRef.current = edges }, [edges])

  const [dataInputColumns, setDataInputColumns] = useState<{ name: string; dtype: string }[]>(
    cachedColumns ?? [],
  )

  useEffect(() => {
    if (!dataInput) {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- cleanup path: clear columns when no data input selected
      setDataInputColumns([])
      return
    }
    // Show cached columns immediately (no loading flash)
    if (cachedColumns) {
      setDataInputColumns(cachedColumns)
      if (isCacheFresh) return // cache is current, skip API call
    }
    // Abort in-flight request when deps change (prevents stale responses overwriting fresh data)
    const controller = new AbortController()
    // Fetch fresh columns (cached value shown meanwhile)
    const graph = buildGraph(allNodesRef.current, edgesRef.current, submodels, preamble)
    previewNode(graph, dataInput, 1, activeSource, { signal: controller.signal })
      .then((result) => {
        if (result.columns) {
          setDataInputColumns(result.columns)
          // getState() in .then() callback: reads graphVersion at completion time,
          // not at effect setup time, so the cached version stays accurate.
          setColumnsCache(dataInput, result.columns, useNodeResultsStore.getState().graphVersion, activeSource)
        }
      })
      .catch((e) => {
        if (e instanceof DOMException && e.name === "AbortError") return
        console.warn("Column fetch failed for node", dataInput, e)
        addToast("warning", `Column fetch failed for "${dataInput}"`)
        if (!cachedColumns) setDataInputColumns([])
      })
    return () => controller.abort()
  }, [dataInput, graphFingerprint, submodels, preamble, activeSource, setColumnsCache, cachedColumns, isCacheFresh, addToast])

  return dataInputColumns
}
