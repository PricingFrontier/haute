import { useEffect, useCallback, useRef, useState } from "react"
import type { Node, Edge } from "@xyflow/react"
import type { PreviewData } from "../panels/DataPreview"
import { makePreviewData } from "../utils/makePreviewData"
import { loadPipeline, previewNode, savePipeline, ApiError } from "../api/client"
import { resolveGraphFromRefs } from "../utils/buildGraph"
import { computeNextNodeId, normalizeEdges } from "../utils/graphHelpers"
import type { NodeResult } from "../api/types"
import useToastStore from "../stores/useToastStore"
import useSettingsStore from "../stores/useSettingsStore"
import useUIStore, { serializeSnapshot } from "../stores/useUIStore"
import useNodeResultsStore from "../stores/useNodeResultsStore"
import { validateConfigRefs, formatConfigRefWarnings } from "../utils/validateConfigRefs"

interface PipelineAPIParams {
  selectedNode: Node | null
  graphRef: React.MutableRefObject<{ nodes: Node[]; edges: Edge[] }>
  parentGraphRef: React.MutableRefObject<{ nodes: Node[]; edges: Edge[]; submodels: Record<string, unknown> } | null>
  submodelsRef: React.MutableRefObject<Record<string, unknown>>
  setNodes: (updater: Node[] | ((nds: Node[]) => Node[])) => void
  setNodesRaw: (nodes: Node[]) => void
  setEdgesRaw: (edges: Edge[]) => void
  setPreamble: (p: string) => void
  preambleRef: React.MutableRefObject<string>
  pipelineNameRef: React.MutableRefObject<string>
  descriptionRef: React.MutableRefObject<string>
  sourceFileRef: React.MutableRefObject<string>
  nodeIdCounter: React.MutableRefObject<number>
}

export interface PipelineAPIReturn {
  loading: boolean
  previewData: PreviewData | null
  setPreviewData: React.Dispatch<React.SetStateAction<PreviewData | null>>
  nodeStatuses: Record<string, "ok" | "error" | "running">
  fetchPreview: (node: Node) => void
  /** Refresh: lazily preview upstream nodes missing _columns, then preview the target node. */
  refreshPreview: (node: Node) => void
  handleSave: () => void
}

function nodeLabel(node: Node): string {
  return String(node.data.label || node.id)
}

type ColumnDef = { name: string; dtype: string }

/**
 * Produce a collision-safe string fingerprint for a column list.
 *
 * Encoding: each column is written as
 *   `${name.length}:${name}\u0002${dtype.length}:${dtype}`
 * and columns are joined with `\u0003`. The length-prefix is what makes the
 * scheme collision-safe: no value of `name` or `dtype` — including ones
 * containing the separators — can be confused with a different column list,
 * because the parser would need to see a different length prefix.
 *
 * `undefined` maps to `""` so never-previewed nodes get a stable sentinel
 * distinct from any non-empty list (the empty list `[]` maps to `"0:"` via
 * the implementation below, not `""`, so the two cases remain distinguishable).
 *
 * This is the fingerprint consumed by {@link columnsEqual} — the hot path
 * that decides whether a preview cascade must propagate downstream.
 */
export function columnFingerprint(columns: ColumnDef[] | undefined): string {
  if (columns === undefined) return ""
  if (columns.length === 0) return "0:"
  const parts: string[] = new Array(columns.length)
  for (let i = 0; i < columns.length; i++) {
    const { name, dtype } = columns[i]
    parts[i] = `${name.length}:${name}\u0002${dtype.length}:${dtype}`
  }
  return parts.join("\u0003")
}

/** Compare two column arrays by name+dtype — returns true if identical. */
function columnsEqual(a: ColumnDef[] | undefined, b: ColumnDef[] | undefined): boolean {
  return columnFingerprint(a) === columnFingerprint(b)
}

function resultToPreview(nodeId: string, label: string, r: NodeResult): PreviewData {
  const status = (r.status === "ok" || r.status === "error" || r.status === "loading") ? r.status : "ok"
  return makePreviewData(nodeId, label, {
    status,
    row_count: r.row_count ?? 0,
    column_count: r.column_count ?? 0,
    columns: r.columns ?? [],
    preview: r.preview ?? [],
    error: r.error ?? null,
    error_line: r.error_line ?? null,
    timing_ms: r.timing_ms ?? 0,
    memory_bytes: r.memory_bytes ?? 0,
    timings: r.timings ?? [],
    memory: r.memory ?? [],
    schema_warnings: r.schema_warnings ?? [],
  })
}

export default function usePipelineAPI({
  selectedNode,
  graphRef, parentGraphRef, submodelsRef, setNodes,
  setNodesRaw, setEdgesRaw, setPreamble,
  preambleRef, pipelineNameRef, descriptionRef, sourceFileRef,
  nodeIdCounter: nodeIdCounterRef,
}: PipelineAPIParams): PipelineAPIReturn {
  const rowLimit = useSettingsStore((s) => s.rowLimit)
  const activeSource = useSettingsStore((s) => s.activeSource)
  const addToast = useToastStore((s) => s.addToast)
  const [loading, setLoading] = useState(true)
  const [previewData, setPreviewData] = useState<PreviewData | null>(null)
  const [nodeStatuses, setNodeStatuses] = useState<Record<string, "ok" | "error" | "running">>({})
  const previewAbort = useRef<AbortController | null>(null)
  const previewDebounce = useRef<ReturnType<typeof setTimeout> | null>(null)

  // Stable refs for values that change across renders but shouldn't
  // trigger re-creation of callbacks. Read at call-time instead.
  const rowLimitRef = useRef(rowLimit)
  useEffect(() => { rowLimitRef.current = rowLimit }, [rowLimit])
  const activeSourceRef = useRef(activeSource)
  useEffect(() => { activeSourceRef.current = activeSource }, [activeSource])

  // Initial pipeline load
  useEffect(() => {
    loadPipeline()
      .then((data) => {
        const pipelineNodes = data.nodes ?? []
        const pipelineEdges = data.edges ?? []
        setNodesRaw(pipelineNodes)
        setEdgesRaw(normalizeEdges(pipelineEdges))
        if (data.preamble !== undefined) {
          setPreamble(data.preamble || "")
          preambleRef.current = data.preamble || ""
        }
        if (data.pipeline_name) pipelineNameRef.current = data.pipeline_name
        if (data.pipeline_description !== undefined) descriptionRef.current = data.pipeline_description || ""
        if (data.source_file) sourceFileRef.current = data.source_file
        if (data.submodels) submodelsRef.current = data.submodels
        // Populate source state from backend sidecar
        if (data.sources && Array.isArray(data.sources)) {
          useSettingsStore.getState().setSources(data.sources)
        }
        if (data.active_source) {
          useSettingsStore.getState().setActiveSource(data.active_source)
        }
        nodeIdCounterRef.current = computeNextNodeId(pipelineNodes)
        // The loaded pipeline IS the on-disk state — mark it saved so
        // selectIsDirty returns false until the user edits something.
        useUIStore.getState().markSaved(
          serializeSnapshot({
            nodes: pipelineNodes,
            edges: pipelineEdges,
            preamble: data.preamble || "",
          }),
        )
        if (data.warning) addToast("warning", data.warning)
        setLoading(false)
      })
      .catch((err) => {
        addToast("error", `Failed to load pipeline: ${err.message}`)
        setLoading(false)
      })
  }, [setNodesRaw, setEdgesRaw, setPreamble, preambleRef, pipelineNameRef, descriptionRef, sourceFileRef, submodelsRef, nodeIdCounterRef, addToast])

  const fetchPreviewImmediate = useCallback((node: Node) => {
    // Abort any in-flight preview request
    previewAbort.current?.abort()
    const controller = new AbortController()
    previewAbort.current = controller

    const label = nodeLabel(node)
    const { getPreview, setPreview: storePreview, graphVersion } = useNodeResultsStore.getState()

    // Capture settings at the moment the fetch starts so the whole
    // cascade (this preview + any downstream propagation) uses a
    // consistent snapshot (Issues #33/#34).  Reading these refs again
    // later would let a concurrent user action (e.g. flipping the
    // active source while the root preview is still in flight) split
    // the cascade across two different sources.
    const snapshotRowLimit = rowLimitRef.current
    const snapshotSource = activeSourceRef.current

    // Cache-first: show cached data immediately if available
    const cached = getPreview(node.id)
    if (cached) {
      setPreviewData(cached.data)
      // If cache is fresh (same graph version), skip the API call
      if (cached.graphVersion === graphVersion) return
      // Otherwise continue to fetch fresh data in background (cached data shown meanwhile)
    } else {
      setPreviewData(makePreviewData(node.id, label, { status: "loading" }))
    }

    const graph = resolveGraphFromRefs(graphRef, parentGraphRef, submodelsRef, preambleRef)

    // Recursively cascade downstream when a node's columns change.  The
    // snapshotted rowLimit/source are closed over so every node in the
    // cascade uses the same values as the root preview (Issues #33/#34).
    const propagate = (changedNodeId: string) => {
      const { edges, nodes: currentNodes } = graphRef.current
      const downstreamIds = edges
        .filter((e) => e.source === changedNodeId)
        .map((e) => e.target)
      if (downstreamIds.length === 0) return
      const cascadeGraph = resolveGraphFromRefs(graphRef, parentGraphRef, submodelsRef, preambleRef)
      for (const dsId of downstreamIds) {
        const dsNode = currentNodes.find((n) => n.id === dsId)
        const oldColumns = (dsNode?.data as Record<string, unknown>)?._columns as ColumnDef[] | undefined
        previewNode(cascadeGraph, dsId, snapshotRowLimit, snapshotSource)
          .then((result) => {
            if (result.columns) {
              const newColumns = result.columns as ColumnDef[]
              setNodes((nds) => nds.map((n) =>
                n.id === dsId
                  ? { ...n, data: { ...n.data, _columns: newColumns, _availableColumns: result.available_columns ?? newColumns, _schemaWarnings: result.schema_warnings ?? [] } }
                  : n,
              ))
              if (!columnsEqual(oldColumns, newColumns)) {
                propagate(dsId)
              }
            }
          })
          .catch((err: unknown) => {
            const detail = err instanceof Error ? err.message : String(err)
            addToast("warning", `Preview propagation failed for "${dsId}": ${detail}`)
          })
      }
    }

    previewNode(graph, node.id, snapshotRowLimit, snapshotSource, { signal: controller.signal })
      .then((result) => {
        const preview = resultToPreview(node.id, label, result)
        setPreviewData(preview)
        // Cache the result for next time
        storePreview(node.id, preview, useNodeResultsStore.getState().graphVersion)
        if (result.node_statuses) {
          setNodeStatuses(result.node_statuses as Record<string, "ok" | "error" | "running">)
        }
        if (result.columns) {
          const oldColumns = (node.data as Record<string, unknown>)?._columns as ColumnDef[] | undefined
          const newColumns = result.columns as ColumnDef[]
          setNodes((nds) => nds.map((n) => n.id === node.id ? { ...n, data: { ...n.data, _columns: newColumns, _availableColumns: result.available_columns ?? newColumns, _schemaWarnings: result.schema_warnings ?? [] } } : n))
          // Cascade to downstream nodes if columns changed.
          if (!columnsEqual(oldColumns, newColumns)) {
            propagate(node.id)
          }
        }
      })
      .catch((err) => {
        if (err instanceof ApiError || err.name !== "AbortError") {
          setPreviewData(makePreviewData(node.id, label, { status: "error", error: err.message }))
          setNodeStatuses({})
        }
      })
  }, [graphRef, parentGraphRef, submodelsRef, preambleRef, setNodes, addToast])

  const fetchPreview = useCallback((node: Node) => {
    if (previewDebounce.current) clearTimeout(previewDebounce.current)
    // Show cached data immediately if available (no loading flash)
    const cached = useNodeResultsStore.getState().getPreview(node.id)
    if (cached) {
      setPreviewData(cached.data)
    } else {
      setPreviewData(makePreviewData(node.id, nodeLabel(node), { status: "loading" }))
    }
    previewDebounce.current = setTimeout(() => fetchPreviewImmediate(node), 200)
  }, [fetchPreviewImmediate])

  /** Lazily preview upstream nodes that are missing _columns, then preview the target node. */
  const refreshPreview = useCallback((node: Node) => {
    const { nodes, edges } = graphRef.current
    const nodeMap = new Map(nodes.map((n) => [n.id, n]))

    // Find direct upstream nodes that have never been previewed (no _columns)
    const upstreamIds = edges
      .filter((e) => e.target === node.id)
      .map((e) => e.source)
    const staleUpstream = upstreamIds
      .map((id) => nodeMap.get(id))
      .filter((n): n is Node => !!n && !(n.data as Record<string, unknown>)?._columns)

    if (staleUpstream.length === 0) {
      // No upstream gaps — just preview the selected node directly
      fetchPreviewImmediate(node)
      return
    }

    // Show loading state for the target node
    setPreviewData(makePreviewData(node.id, nodeLabel(node), { status: "loading" }))

    const graph = resolveGraphFromRefs(graphRef, parentGraphRef, submodelsRef, preambleRef)

    // Capture settings once so every upstream preview in this refresh
    // uses the same snapshot (Issues #33/#34).
    const snapshotRowLimit = rowLimitRef.current
    const snapshotSource = activeSourceRef.current

    // Preview stale upstream nodes in parallel, then the target node
    Promise.all(
      staleUpstream.map((upstream) =>
        previewNode(graph, upstream.id, snapshotRowLimit, snapshotSource)
          .then((result) => {
            if (result.columns) {
              setNodes((nds) => nds.map((n) =>
                n.id === upstream.id
                  ? { ...n, data: { ...n.data, _columns: result.columns, _availableColumns: result.available_columns ?? result.columns, _schemaWarnings: result.schema_warnings ?? [] } }
                  : n,
              ))
            }
          })
          .catch((err: unknown) => {
            const detail = err instanceof Error ? err.message : String(err)
            addToast("warning", `Upstream preview failed for "${upstream.data?.label || upstream.id}": ${detail}`)
          }),
      ),
    ).then(() => {
      fetchPreviewImmediate(node)
    })
  }, [fetchPreviewImmediate, graphRef, parentGraphRef, submodelsRef, preambleRef, setNodes, addToast])

  const handleSave = useCallback(() => {
    const { nodes: n, edges: e } = graphRef.current
    // Warn about broken config references before saving
    const refWarnings = validateConfigRefs(n)
    if (refWarnings.length > 0) {
      addToast("warning", formatConfigRefWarnings(refWarnings))
    }
    const { sources: sc, activeSource: as_ } = useSettingsStore.getState()
    savePipeline({
      name: pipelineNameRef.current,
      description: descriptionRef.current,
      graph: { nodes: n, edges: e, submodels: submodelsRef.current },
      preamble: preambleRef.current,
      source_file: sourceFileRef.current,
      sources: sc,
      active_source: as_,
    })
      .then((data) => {
        useUIStore.getState().markSaved(
          serializeSnapshot({ nodes: n, edges: e, preamble: preambleRef.current }),
        )
        addToast("success", `Saved → ${data.file}`)
      })
      .catch((err: unknown) => {
        const detail = err instanceof Error ? err.message : "unknown error"
        addToast("error", `Failed to save pipeline: ${detail}`)
      })
  }, [graphRef, submodelsRef, preambleRef, descriptionRef, sourceFileRef, pipelineNameRef, addToast])

  // Clear node statuses when selected node changes (including deselect)
  // so statuses from a previous node don't bleed into the next selection.
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- reset derived state on node switch
    setNodeStatuses({})
  }, [selectedNode])

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      previewAbort.current?.abort()
      if (previewDebounce.current) clearTimeout(previewDebounce.current)
    }
  }, [])

  return {
    loading,
    previewData, setPreviewData,
    nodeStatuses,
    fetchPreview, refreshPreview, handleSave,
  }
}
