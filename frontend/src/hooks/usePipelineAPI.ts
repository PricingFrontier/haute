import { useEffect, useCallback, useRef, useState } from "react"
import type { Node, Edge } from "@xyflow/react"
import type { PreviewData } from "../panels/DataPreview"
import { makePreviewData } from "../utils/makePreviewData"
import { ApiError, loadPipeline, previewNode, savePipeline } from "../api/client"
import type { RetryPolicy } from "../api/client"
import { resolveGraphFromRefs } from "../utils/buildGraph"
import { computeNextNodeId, normalizeEdges } from "../utils/graphHelpers"
import type { NodeResult } from "../api/types"
import useToastStore from "../stores/useToastStore"
import useSettingsStore from "../stores/useSettingsStore"
import useGraphStore from "../stores/useGraphStore"
import useNodeResultsStore from "../stores/useNodeResultsStore"
import { validateConfigRefs, formatConfigRefWarnings } from "../utils/validateConfigRefs"
import { findFirstInvalidEdgeJoin, formatEdgeJoinValidationIssue } from "../utils/edgeJoinValidation"
import { nodeData } from "../types/node"
import { parsePipelineResponse } from "../types/guards"
import { columnsEqualByFingerprint, type ColumnFingerprintInput } from "../utils/columnFingerprint"
export { columnFingerprint } from "../utils/columnFingerprint"

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
  previewBusy: boolean
  nodeStatuses: Record<string, "ok" | "error" | "running">
  fetchPreview: (node: Node, options?: FetchPreviewOptions) => void
  cancelPreview: () => void
  /** Refresh: lazily preview upstream nodes missing _columns, then preview the target node. */
  refreshPreview: (node: Node) => void
  handleSave: () => void
}

export interface FetchPreviewOptions {
  /**
   * Delay before the API preview starts. Expensive nodes can use a longer
   * idle delay so quick click-throughs are cancelled before backend work begins.
   */
  debounceMs?: number
}

function nodeLabel(node: Node): string {
  return String(node.data.label || node.id)
}

type ColumnDef = ColumnFingerprintInput[number]

const INITIAL_PIPELINE_RETRY_POLICY = {
  maxRetries: 6,
  baseDelayMs: 250,
} satisfies RetryPolicy

export const DOWNSTREAM_PREVIEW_CONCURRENCY_LIMIT = 4
export const PREVIEW_INITIAL_COLUMN_LIMIT = 200

/** Compare two column arrays by name+dtype; returns true if identical. */
function columnsEqual(a: ColumnDef[] | undefined, b: ColumnDef[] | undefined): boolean {
  return columnsEqualByFingerprint(a, b)
}

function resultToPreview(nodeId: string, label: string, r: NodeResult): PreviewData {
  const status = (r.status === "ok" || r.status === "error" || r.status === "loading") ? r.status : "ok"
  return makePreviewData(nodeId, label, {
    status,
    row_count: r.row_count ?? 0,
    column_count: r.column_count ?? 0,
    columns: r.columns ?? [],
    preview: r.preview ?? [],
    preview_columns: r.preview_columns,
    preview_row_count: r.preview_row_count,
    preview_row_limit: r.preview_row_limit,
    preview_truncated: r.preview_truncated,
    error: r.error ?? null,
    error_line: r.error_line ?? null,
    timing_ms: r.timing_ms ?? 0,
    memory_bytes: r.memory_bytes ?? 0,
    timings: r.timings ?? [],
    memory: r.memory ?? [],
    schema_warnings: r.schema_warnings ?? [],
    execution_metrics: r.execution_metrics ?? null,
  })
}

function previewColumnNamesForNode(node: Node): string[] | undefined {
  const columns = nodeData(node)._columns
  return columns && columns.length > 0
    ? columns.slice(0, PREVIEW_INITIAL_COLUMN_LIMIT).map((column) => column.name)
    : undefined
}

function applyPreviewColumnsToNodes(nodes: Node[], nodeId: string, columns: ColumnDef[], result: NodeResult): Node[] {
  return nodes.map((n) =>
    n.id === nodeId
      ? {
        ...n,
        data: {
          ...n.data,
          _columns: columns,
          _availableColumns: result.available_columns ?? columns,
          _schemaWarnings: result.schema_warnings ?? [],
        },
      }
      : n,
  )
}

function applyPreviewSchemaMapsToNodes(nodes: Node[], result: NodeResult): Node[] {
  const nodeColumns = result.node_columns ?? {}
  if (Object.keys(nodeColumns).length === 0) return nodes
  const nodeAvailableColumns = result.node_available_columns ?? {}
  const nodeSchemaWarnings = result.node_schema_warnings ?? {}
  return nodes.map((n) => {
    const columns = nodeColumns[n.id]
    if (!columns) return n
    return {
      ...n,
      data: {
        ...n.data,
        _columns: columns,
        _availableColumns: nodeAvailableColumns[n.id] ?? columns,
        _schemaWarnings: nodeSchemaWarnings[n.id] ?? [],
      },
    }
  })
}

function applyPreviewResultColumnsToNodes(nodes: Node[], nodeId: string, result: NodeResult): Node[] {
  const mapped = applyPreviewSchemaMapsToNodes(nodes, result)
  if (!result.columns || result.node_columns?.[nodeId]) return mapped
  return applyPreviewColumnsToNodes(mapped, nodeId, result.columns as ColumnDef[], result)
}

function isAbortError(err: unknown): boolean {
  return err instanceof Error
    ? err.name === "AbortError"
    : typeof err === "object" &&
      err !== null &&
      (err as { name?: unknown }).name === "AbortError"
}

function isPreviewSupersededError(err: unknown): boolean {
  if (!(err instanceof ApiError) || err.status !== 409) return false
  const text = `${err.detail ?? ""} ${err.message}`
  return text.toLowerCase().includes("superseded")
}

function previewErrorDetail(err: unknown): string {
  if (err instanceof ApiError && err.detail) return err.detail
  return err instanceof Error ? err.message : String(err)
}

export default function usePipelineAPI({
  selectedNode,
  graphRef, parentGraphRef, submodelsRef, setNodes,
  setNodesRaw, setEdgesRaw, setPreamble,
  preambleRef, pipelineNameRef, descriptionRef, sourceFileRef,
  nodeIdCounter: nodeIdCounterRef,
}: PipelineAPIParams): PipelineAPIReturn {
  const rowLimit = useSettingsStore((s) => s.rowLimit)
  const streamingChunkSize = useSettingsStore((s) => s.streamingChunkSize)
  const activeSource = useSettingsStore((s) => s.activeSource)
  const addToast = useToastStore((s) => s.addToast)
  const [loading, setLoading] = useState(true)
  const [previewData, setPreviewData] = useState<PreviewData | null>(null)
  const [previewBusy, setPreviewBusy] = useState(false)
  const [nodeStatuses, setNodeStatuses] = useState<Record<string, "ok" | "error" | "running">>({})
  const previewAbort = useRef<AbortController | null>(null)
  const previewDebounce = useRef<ReturnType<typeof setTimeout> | null>(null)
  const previewRequestSeq = useRef(0)

  // Stable refs for values that change across renders but shouldn't
  // trigger re-creation of callbacks. Read at call-time instead.
  const rowLimitRef = useRef(rowLimit)
  useEffect(() => { rowLimitRef.current = rowLimit }, [rowLimit])
  const streamingChunkSizeRef = useRef(streamingChunkSize)
  useEffect(() => { streamingChunkSizeRef.current = streamingChunkSize }, [streamingChunkSize])
  const activeSourceRef = useRef(activeSource)
  useEffect(() => { activeSourceRef.current = activeSource }, [activeSource])

  // Initial pipeline load
  useEffect(() => {
    const controller = new AbortController()
    let disposed = false

    loadPipeline({ signal: controller.signal, retry: INITIAL_PIPELINE_RETRY_POLICY })
      .then((raw) => {
        if (disposed) return
        // Narrow the response at the ingestion boundary.  Any drift in
        // the backend contract (missing `nodes`/`edges`, wrong type on an
        // optional field) throws a named Error that flows into the
        // `.catch` handler below — rather than surfacing downstream as a
        // cryptic "undefined is not iterable" three callbacks deep.
        const data = parsePipelineResponse(raw)
        const pipelineNodes = data.nodes
        const pipelineEdges = data.edges
        setNodesRaw(pipelineNodes)
        setEdgesRaw(normalizeEdges(pipelineEdges))
        if (data.preamble != null) {
          setPreamble(data.preamble)
          preambleRef.current = data.preamble
        }
        if (data.pipeline_name) pipelineNameRef.current = data.pipeline_name
        if (data.pipeline_description != null) descriptionRef.current = data.pipeline_description
        if (data.source_file) sourceFileRef.current = data.source_file
        if (data.submodels != null) submodelsRef.current = data.submodels
        // Populate source state from backend sidecar
        if (data.sources) {
          useSettingsStore.getState().setSources(data.sources)
        }
        if (data.active_source) {
          useSettingsStore.getState().setActiveSource(data.active_source)
        }
        nodeIdCounterRef.current = computeNextNodeId(pipelineNodes)
        // The loaded pipeline IS the on-disk state — mark it saved so
        // isDirty returns false until the user edits something.  The
        // preceding setNodesRaw / setEdgesRaw / setPreamble have already
        // written into useGraphStore, so markSaved captures the exact
        // snapshot we just loaded.
        useGraphStore.getState().markSaved()
        if (data.warning) addToast("warning", data.warning)
        setLoading(false)
      })
      .catch((err) => {
        if (disposed || (isAbortError(err) && controller.signal.aborted)) return
        addToast("error", `Failed to load pipeline: ${err.message}`)
        setLoading(false)
      })

    return () => {
      disposed = true
      controller.abort()
    }
  }, [setNodesRaw, setEdgesRaw, setPreamble, preambleRef, pipelineNameRef, descriptionRef, sourceFileRef, submodelsRef, nodeIdCounterRef, addToast])

  const fetchPreviewImmediate = useCallback((node: Node, existingRequestId?: number, options?: { bypassCache?: boolean }) => {
    const requestId = existingRequestId ?? ++previewRequestSeq.current
    // Abort any in-flight preview request
    previewAbort.current?.abort()
    previewAbort.current = null
    setPreviewBusy(true)

    const label = nodeLabel(node)
    const { getPreview, setPreview: storePreview } = useNodeResultsStore.getState()
    const structuralVersion = useGraphStore.getState().structuralVersion

    // Capture settings at the moment the fetch starts so the whole
    // cascade (this preview + any downstream propagation) uses a
    // consistent snapshot (Issues #33/#34).  Reading these refs again
    // later would let a concurrent user action (e.g. flipping the
    // active source while the root preview is still in flight) split
    // the cascade across two different sources.
    const snapshotRowLimit = rowLimitRef.current
    const snapshotSource = activeSourceRef.current
    const snapshotChunkSize = streamingChunkSizeRef.current
    const matchesRequestContext = (cached: { structuralVersion: number; source?: string; rowLimit?: number }) =>
      cached.structuralVersion === structuralVersion &&
      cached.source === snapshotSource &&
      cached.rowLimit === snapshotRowLimit
    const requestStillCurrent = () =>
      previewRequestSeq.current === requestId &&
      useGraphStore.getState().structuralVersion === structuralVersion

    // Cache-first: show cached data immediately if available
    const cached = getPreview(node.id)
    if (!options?.bypassCache && cached && cached.source === snapshotSource && cached.rowLimit === snapshotRowLimit) {
      if (requestStillCurrent()) setPreviewData(cached.data)
      // If cache is fresh for the same execution context, skip the API call.
      if (matchesRequestContext(cached)) {
        setPreviewBusy(false)
        return
      }
      // Otherwise continue to fetch fresh data in background (cached data shown meanwhile)
    } else {
      setPreviewData(makePreviewData(node.id, label, { status: "loading" }))
    }

    const graph = resolveGraphFromRefs(graphRef, parentGraphRef, submodelsRef, preambleRef)
    let cascadeNodes = graph.nodes
    const resolveCascadeGraph = () => ({
      ...resolveGraphFromRefs(graphRef, parentGraphRef, submodelsRef, preambleRef),
      nodes: cascadeNodes,
    })
    const controller = new AbortController()
    previewAbort.current = controller

    // Cascade downstream when a node's columns change. The snapshotted
    // rowLimit/source are closed over so every node in the cascade uses
    // the same values as the root preview (Issues #33/#34).
    const propagate = (changedNodeId: string) => {
      const { edges } = graphRef.current
      const childrenBySource = new Map<string, string[]>()
      const reachableNodeIds = new Set<string>()
      const queue = [changedNodeId]

      for (const edge of edges) {
        const children = childrenBySource.get(edge.source)
        if (children) {
          children.push(edge.target)
        } else {
          childrenBySource.set(edge.source, [edge.target])
        }
      }

      for (let i = 0; i < queue.length; i++) {
        const sourceId = queue[i]
        for (const targetId of childrenBySource.get(sourceId) ?? []) {
          if (reachableNodeIds.has(targetId)) continue
          reachableNodeIds.add(targetId)
          queue.push(targetId)
        }
      }

      if (reachableNodeIds.size === 0) return

      const inCascadeSourceIds = new Set<string>([changedNodeId, ...reachableNodeIds])
      const pendingParents = new Map<string, number>()
      for (const nodeId of reachableNodeIds) pendingParents.set(nodeId, 0)
      for (const edge of edges) {
        if (!reachableNodeIds.has(edge.target) || !inCascadeSourceIds.has(edge.source)) continue
        pendingParents.set(edge.target, (pendingParents.get(edge.target) ?? 0) + 1)
      }

      const hasChangedParent = new Set<string>()
      const scheduledNodeIds = new Set<string>()
      const settledNodeIds = new Set<string>()
      const readyQueue: string[] = []
      let activePreviewCount = 0

      const settleNode = (nodeId: string, columnsChanged: boolean) => {
        if (settledNodeIds.has(nodeId)) return
        settledNodeIds.add(nodeId)

        for (const childId of childrenBySource.get(nodeId) ?? []) {
          if (!reachableNodeIds.has(childId)) continue
          if (columnsChanged) hasChangedParent.add(childId)
          const remainingParents = (pendingParents.get(childId) ?? 0) - 1
          pendingParents.set(childId, remainingParents)
          if (remainingParents > 0) continue
          if (hasChangedParent.has(childId)) {
            queueNodePreview(childId)
          } else {
            settleNode(childId, false)
          }
        }
      }

      const drainReadyQueue = () => {
        while (
          activePreviewCount < DOWNSTREAM_PREVIEW_CONCURRENCY_LIMIT &&
          readyQueue.length > 0 &&
          requestStillCurrent()
        ) {
          const nodeId = readyQueue.shift()!
          activePreviewCount += 1
          const cascadeGraph = resolveCascadeGraph()
          const dsNode = cascadeNodes.find((n) => n.id === nodeId)
          const oldColumns = dsNode ? nodeData(dsNode)._columns : undefined
          previewNode({
            graph: cascadeGraph,
            nodeId,
            rowLimit: snapshotRowLimit,
            source: snapshotSource,
            requestedPreviewColumns: dsNode ? previewColumnNamesForNode(dsNode) : undefined,
            streamingChunkSize: snapshotChunkSize,
          })
            .then((result) => {
              if (!requestStillCurrent()) return
              if (!result.columns) {
                settleNode(nodeId, false)
                return
              }
              const newColumns = result.columns as ColumnDef[]
              cascadeNodes = applyPreviewResultColumnsToNodes(cascadeNodes, nodeId, result)
              setNodes((nds) => applyPreviewResultColumnsToNodes(nds, nodeId, result))
              settleNode(nodeId, !columnsEqual(oldColumns, newColumns))
            })
            .catch((err: unknown) => {
              if (!requestStillCurrent()) return
              if (isAbortError(err) || isPreviewSupersededError(err)) {
                settleNode(nodeId, false)
                return
              }
              const detail = previewErrorDetail(err)
              addToast("warning", `Preview propagation failed for "${nodeId}": ${detail}`)
              settleNode(nodeId, false)
            })
            .finally(() => {
              activePreviewCount -= 1
              drainReadyQueue()
            })
        }
      }

      const queueNodePreview = (nodeId: string) => {
        if (scheduledNodeIds.has(nodeId) || !requestStillCurrent()) return
        scheduledNodeIds.add(nodeId)
        readyQueue.push(nodeId)
        drainReadyQueue()
      }

      settleNode(changedNodeId, true)
    }

    previewNode({
      graph,
      nodeId: node.id,
      rowLimit: snapshotRowLimit,
      source: snapshotSource,
      requestedPreviewColumns: previewColumnNamesForNode(node),
      streamingChunkSize: snapshotChunkSize,
      signal: controller.signal,
    })
      .then((result) => {
        if (!requestStillCurrent()) return
        const preview = resultToPreview(node.id, label, result)
        setPreviewData(preview)
        // Cache the result for next time
        storePreview(node.id, preview, structuralVersion, snapshotSource, snapshotRowLimit)
        if (result.node_statuses) {
          setNodeStatuses(result.node_statuses as Record<string, "ok" | "error" | "running">)
        }
        if (result.columns) {
          const oldColumns = nodeData(node)._columns
          const newColumns = result.columns as ColumnDef[]
          cascadeNodes = applyPreviewResultColumnsToNodes(cascadeNodes, node.id, result)
          setNodes((nds) => applyPreviewResultColumnsToNodes(nds, node.id, result))
          // Cascade to downstream nodes if columns changed.
          if (!columnsEqual(oldColumns, newColumns)) {
            propagate(node.id)
          }
        }
      })
      .catch((err: unknown) => {
        if (!requestStillCurrent()) return
        if (isAbortError(err) || isPreviewSupersededError(err)) return
        const detail = previewErrorDetail(err)
        setPreviewData(makePreviewData(node.id, label, { status: "error", error: detail }))
        setNodeStatuses({})
      })
      .finally(() => {
        if (previewAbort.current === controller) {
          previewAbort.current = null
        }
        if (previewRequestSeq.current === requestId) {
          setPreviewBusy(false)
        }
      })
  }, [graphRef, parentGraphRef, submodelsRef, preambleRef, setNodes, addToast])

  const fetchPreview = useCallback((node: Node, options: FetchPreviewOptions = {}) => {
    const requestId = ++previewRequestSeq.current
    setPreviewBusy(true)
    // Cancel any previous node preview as soon as the user changes
    // selection. The next request is still debounced, but stale backend
    // work should not keep running during that debounce window.
    previewAbort.current?.abort()
    previewAbort.current = null
    if (previewDebounce.current) {
      clearTimeout(previewDebounce.current)
      previewDebounce.current = null
    }
    // Cached data should paint immediately. Deferring it creates a visible
    // "Executing pipeline..." flash when switching away from result panels.
    const cached = useNodeResultsStore.getState().getPreview(node.id)
    if (cached && cached.source === activeSourceRef.current && cached.rowLimit === rowLimitRef.current) {
      setPreviewData(cached.data)
    } else {
      setPreviewData(makePreviewData(node.id, nodeLabel(node), { status: "loading" }))
    }
    previewDebounce.current = setTimeout(() => {
      previewDebounce.current = null
      fetchPreviewImmediate(node, requestId)
    }, options.debounceMs ?? 200)
  }, [fetchPreviewImmediate])

  const cancelPreview = useCallback(() => {
    ++previewRequestSeq.current
    previewAbort.current?.abort()
    previewAbort.current = null
    setPreviewBusy(false)
    if (previewDebounce.current) {
      clearTimeout(previewDebounce.current)
      previewDebounce.current = null
    }
  }, [])

  /** Lazily preview upstream nodes that are missing _columns, then preview the target node. */
  const refreshPreview = useCallback((node: Node) => {
    const requestId = ++previewRequestSeq.current
    setPreviewBusy(true)
    previewAbort.current?.abort()
    previewAbort.current = null
    if (previewDebounce.current) {
      clearTimeout(previewDebounce.current)
      previewDebounce.current = null
    }
    const structuralVersion = useGraphStore.getState().structuralVersion
    const requestStillCurrent = () =>
      previewRequestSeq.current === requestId &&
      useGraphStore.getState().structuralVersion === structuralVersion

    const { nodes, edges } = graphRef.current
    const nodeMap = new Map(nodes.map((n) => [n.id, n]))

    // Find direct upstream nodes that have never been previewed (no _columns)
    const upstreamIds = edges
      .filter((e) => e.target === node.id)
      .map((e) => e.source)
    const staleUpstream = upstreamIds
      .map((id) => nodeMap.get(id))
      .filter((n): n is Node => !!n && !nodeData(n)._columns)

    if (staleUpstream.length === 0) {
      // No upstream gaps — just preview the selected node directly
      fetchPreviewImmediate(node, requestId, { bypassCache: true })
      return
    }

    // Show loading state for the target node
    if (requestStillCurrent()) {
      setPreviewData(makePreviewData(node.id, nodeLabel(node), { status: "loading" }))
    }

    const graph = resolveGraphFromRefs(graphRef, parentGraphRef, submodelsRef, preambleRef)

    // Capture settings once so every upstream preview in this refresh
    // uses the same snapshot (Issues #33/#34).
    const snapshotRowLimit = rowLimitRef.current
    const snapshotSource = activeSourceRef.current
    const snapshotChunkSize = streamingChunkSizeRef.current

    // Preview stale upstream nodes in parallel, then the target node
    Promise.all(
      staleUpstream.map((upstream) =>
          previewNode({
            graph,
            nodeId: upstream.id,
            rowLimit: snapshotRowLimit,
            source: snapshotSource,
            requestedPreviewColumns: previewColumnNamesForNode(upstream),
            streamingChunkSize: snapshotChunkSize,
          })
          .then((result) => {
            if (!requestStillCurrent()) return
            if (result.columns) {
              setNodes((nds) => applyPreviewResultColumnsToNodes(nds, upstream.id, result))
            }
          })
          .catch((err: unknown) => {
            if (!requestStillCurrent()) return
            if (isAbortError(err) || isPreviewSupersededError(err)) return
            const detail = previewErrorDetail(err)
            addToast("warning", `Upstream preview failed for "${upstream.data?.label || upstream.id}": ${detail}`)
          }),
      ),
    ).then(() => {
      if (!requestStillCurrent()) {
        if (previewRequestSeq.current === requestId) setPreviewBusy(false)
        return
      }
      fetchPreviewImmediate(node, requestId, { bypassCache: true })
    })
  }, [fetchPreviewImmediate, graphRef, parentGraphRef, submodelsRef, preambleRef, setNodes, addToast])

  const handleSave = useCallback(() => {
    const { nodes: n, edges: e } = graphRef.current
    // Warn about broken config references before saving
    const refWarnings = validateConfigRefs(n)
    if (refWarnings.length > 0) {
      addToast("warning", formatConfigRefWarnings(refWarnings))
    }
    const edgeJoinIssue = findFirstInvalidEdgeJoin(n, e)
    if (edgeJoinIssue) {
      addToast("error", `Cannot save: ${formatEdgeJoinValidationIssue(edgeJoinIssue)}`)
      return
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
        // We just wrote `n` / `e` / `preambleRef.current` to disk.  These
        // match the current useGraphStore state (the save handler reads
        // them from graphRef, which mirrors the store), so markSaved
        // with no args captures the correct baseline.
        useGraphStore.getState().markSaved()
        addToast("success", `Saved → ${data.file}`)
      })
      .catch((err: unknown) => {
        const detail = err instanceof ApiError && err.detail
          ? err.detail
          : err instanceof Error
            ? err.message
            : "unknown error"
        addToast("error", `Failed to save pipeline: ${detail}`)
      })
  }, [graphRef, submodelsRef, preambleRef, descriptionRef, sourceFileRef, pipelineNameRef, addToast])

  const selectedNodeId = selectedNode?.id ?? null

  // Clear node statuses when selected node id changes (including deselect)
  // so statuses from a previous node don't bleed into the next selection.
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- reset derived state on node switch
    setNodeStatuses({})
  }, [selectedNodeId])

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
    previewBusy,
    nodeStatuses,
    fetchPreview, cancelPreview, refreshPreview, handleSave,
  }
}
