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
import useUIStore from "../stores/useUIStore"
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
  lastSavedRef: React.MutableRefObject<string>
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

/** Compare two column arrays by name+dtype — returns true if identical. */
function columnsEqual(a: ColumnDef[] | undefined, b: ColumnDef[] | undefined): boolean {
  if (!a && !b) return true
  if (!a || !b || a.length !== b.length) return false
  return a.every((col, i) => col.name === b[i].name && col.dtype === b[i].dtype)
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
  preambleRef, pipelineNameRef, descriptionRef, sourceFileRef, lastSavedRef,
  nodeIdCounter: nodeIdCounterRef,
}: PipelineAPIParams): PipelineAPIReturn {
  const rowLimit = useSettingsStore((s) => s.rowLimit)
  const activeSource = useSettingsStore((s) => s.activeSource)
  const setDirty = useUIStore((s) => s.setDirty)
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

  // ─── Downstream column propagation ──────────────────────────────
  // After a node's preview returns changed columns, cascade to downstream
  // nodes so their _columns (and thus editor dropdowns) stay fresh.
  const propagatingRef = useRef(new Set<string>())
  const propagateRef = useRef<(changedNodeId: string) => void>(() => {})

  const propagateDownstream = useCallback((changedNodeId: string) => {
    const { edges, nodes } = graphRef.current
    const downstreamIds = edges
      .filter((e) => e.source === changedNodeId)
      .map((e) => e.target)

    if (downstreamIds.length === 0) return

    const graph = resolveGraphFromRefs(graphRef, parentGraphRef, submodelsRef, preambleRef)

    for (const dsId of downstreamIds) {
      if (propagatingRef.current.has(dsId)) continue
      propagatingRef.current.add(dsId)

      const dsNode = nodes.find((n) => n.id === dsId)
      const oldColumns = (dsNode?.data as Record<string, unknown>)?._columns as ColumnDef[] | undefined

      previewNode(graph, dsId, rowLimitRef.current, activeSourceRef.current)
        .then((result) => {
          if (result.columns) {
            const newColumns = result.columns as ColumnDef[]
            setNodes((nds) => nds.map((n) =>
              n.id === dsId
                ? { ...n, data: { ...n.data, _columns: newColumns, _availableColumns: result.available_columns ?? newColumns, _schemaWarnings: result.schema_warnings ?? [] } }
                : n,
            ))
            if (!columnsEqual(oldColumns, newColumns)) {
              propagateRef.current(dsId)
            }
          }
        })
        .catch((e) => { console.warn("propagation_failed", dsId, e); addToast("warning", `Preview propagation failed for "${dsId}"`) })
        .finally(() => { propagatingRef.current.delete(dsId) })
    }
  }, [graphRef, parentGraphRef, submodelsRef, preambleRef, setNodes, addToast])

  useEffect(() => { propagateRef.current = propagateDownstream }, [propagateDownstream])

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
        lastSavedRef.current = JSON.stringify({ nodes: pipelineNodes, edges: pipelineEdges, preamble: data.preamble || "" })
        if (data.warning) addToast("warning", data.warning)
        setLoading(false)
      })
      .catch((err) => {
        addToast("error", `Failed to load pipeline: ${err.message}`)
        setLoading(false)
      })
  }, [setNodesRaw, setEdgesRaw, setPreamble, preambleRef, pipelineNameRef, descriptionRef, sourceFileRef, submodelsRef, nodeIdCounterRef, lastSavedRef, addToast])

  const fetchPreviewImmediate = useCallback((node: Node) => {
    // Abort any in-flight preview request
    previewAbort.current?.abort()
    const controller = new AbortController()
    previewAbort.current = controller

    const label = nodeLabel(node)
    const { getPreview, setPreview: storePreview, graphVersion } = useNodeResultsStore.getState()

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

    previewNode(graph, node.id, rowLimitRef.current, activeSourceRef.current, { signal: controller.signal })
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
          // Cascade to downstream nodes if columns changed
          if (!columnsEqual(oldColumns, newColumns)) {
            propagateDownstream(node.id)
          }
        }
      })
      .catch((err) => {
        if (err instanceof ApiError || err.name !== "AbortError") {
          setPreviewData(makePreviewData(node.id, label, { status: "error", error: err.message }))
          setNodeStatuses({})
        }
      })
  }, [graphRef, parentGraphRef, submodelsRef, preambleRef, setNodes, propagateDownstream])

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

    // Preview stale upstream nodes in parallel, then the target node
    Promise.all(
      staleUpstream.map((upstream) =>
        previewNode(graph, upstream.id, rowLimitRef.current, activeSourceRef.current)
          .then((result) => {
            if (result.columns) {
              setNodes((nds) => nds.map((n) =>
                n.id === upstream.id
                  ? { ...n, data: { ...n.data, _columns: result.columns, _availableColumns: result.available_columns ?? result.columns, _schemaWarnings: result.schema_warnings ?? [] } }
                  : n,
              ))
            }
          })
          .catch((e) => { console.warn("upstream_preview_failed", upstream.id, e); addToast("warning", `Upstream preview failed for "${upstream.data?.label || upstream.id}"`) }),
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
        lastSavedRef.current = JSON.stringify({ nodes: n, edges: e, preamble: preambleRef.current })
        setDirty(false)
        addToast("success", `Saved → ${data.file}`)
      })
      .catch((err: unknown) => {
        console.warn("Pipeline save failed:", err)
        addToast("error", "Failed to save pipeline")
      })
  }, [graphRef, submodelsRef, preambleRef, descriptionRef, sourceFileRef, pipelineNameRef, lastSavedRef, setDirty, addToast])

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
