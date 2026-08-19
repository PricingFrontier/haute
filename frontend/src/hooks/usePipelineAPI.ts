import { useEffect, useCallback, useRef, useState } from "react"
import type { Node } from "@xyflow/react"
import type { PreviewData } from "../panels/DataPreview"
import { makePreviewData } from "../utils/makePreviewData"
import {
  ApiError,
  loadPipeline,
  previewNode,
  previewRecoveryNode,
  savePipeline,
} from "../api/client"
import type { ApiTimeoutError, RetryPolicy } from "../api/client"
import { resolveGraphFromRefs } from "../utils/buildGraph"
import { computeNextNodeId, normalizeEdges } from "../utils/graphHelpers"
import type { NodeResult, PreviewNodeResponse } from "../api/types"
import useToastStore from "../stores/useToastStore"
import useSettingsStore from "../stores/useSettingsStore"
import useGraphStore, { captureGraphSnapshot } from "../stores/useGraphStore"
import useGitStore from "../stores/useGitStore"
import { isIdentityPromptDismissed } from "../stores/identityPrompt"
import useNodeResultsStore from "../stores/useNodeResultsStore"
import useDocumentStatusStore from "../stores/useDocumentStatusStore"
import { validateConfigRefs, formatConfigRefWarnings } from "../utils/validateConfigRefs"
import { findFirstInvalidEdgeJoin, formatEdgeJoinValidationIssue } from "../utils/edgeJoinValidation"
import { effectiveNodeType, nodeData } from "../types/node"
import type { PipelineEdge } from "../types/node"
import { NODE_TYPES } from "../utils/nodeTypes"
import {
  adaptPipelineEditorDocument,
  parsePipelineEditorDocument,
  type PipelineLoadStatus,
} from "../types/pipelineDocument"
import { columnsEqualByFingerprint, type ColumnFingerprintInput } from "../utils/columnFingerprint"
import { apiInputFrameLabels } from "../utils/apiInputPorts"
import {
  runtimeNodeIdForVisibleNode,
  type DrilledOccurrenceIdentity,
} from "../utils/submodelRuntimeTarget"
import { ensureInputSnapshots } from "./ensureInputSnapshots"
export { columnFingerprint } from "../utils/columnFingerprint"

interface PipelineAPIParams {
  selectedNode: Node | null
  graphRef: React.MutableRefObject<{ nodes: Node[]; edges: PipelineEdge[] }>
  parentGraphRef: React.MutableRefObject<{ nodes: Node[]; edges: PipelineEdge[]; submodels: Record<string, unknown> } | null>
  activeSubmodelIdentity: DrilledOccurrenceIdentity | null
  submodelsRef: React.MutableRefObject<Record<string, unknown>>
  setNodesRaw: (updater: Node[] | ((nds: Node[]) => Node[])) => void
  setEdgesRaw: (edges: PipelineEdge[]) => void
  setSubmodelsRaw?: (submodels: Record<string, unknown>) => void
  setCurrentSourceFile?: (sourceFile: string | null) => void
  setPreamble: (p: string) => void
  preambleRef: React.MutableRefObject<string>
  pipelineNameRef: React.MutableRefObject<string>
  descriptionRef: React.MutableRefObject<string>
  sourceFileRef: React.MutableRefObject<string>
  sourceRevisionRef: React.MutableRefObject<string>
  preservedBlocksRef: React.MutableRefObject<string[]>
  nodeIdCounter: React.MutableRefObject<number>
}

export interface PipelineAPIReturn {
  loading: boolean
  loadError: string | null
  previewData: PreviewData | null
  setPreviewData: React.Dispatch<React.SetStateAction<PreviewData | null>>
  previewBusy: boolean
  nodeStatuses: Record<string, "ok" | "error" | "running">
  fetchPreview: (node: Node, options?: FetchPreviewOptions) => void
  cancelPreview: () => void
  /** Refresh: lazily preview upstream nodes missing _columns, then preview the target node. */
  refreshPreview: (node: Node) => void
  /** Re-preview a multi-frame node showing a specific frame (the
   * frame-select dropdown on the canvas preview top-bar). Focused: it only
   * repaints `previewData` for the requested frame and does NOT run the
   * downstream column cascade (a frame switch shows different rows, not
   * different downstream columns). The node is resolved from the live graph
   * by id. */
  previewNodeFrame: (nodeId: string, portLabel: string) => void
  /** Save the pipeline. Resolves true on success, false on failure (never rejects);
   *  callers chaining follow-on work (such as Commit) await this. */
  handleSave: () => Promise<boolean>
  /** Atomically ingest a validated authoritative editor document. */
  adoptPipelineDocument: (document: import("../types/pipelineDocument").PipelineEditorDocument) => void
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

function previewPortLabel(node: Node): string | undefined {
  const data = nodeData(node)
  if (data.nodeType !== NODE_TYPES.API_INPUT) return undefined
  return apiInputFrameLabels(data.config)[0]
}

type ColumnDef = ColumnFingerprintInput[number]

const INITIAL_PIPELINE_RETRY_POLICY = {
  maxRetries: 6,
  baseDelayMs: 250,
} satisfies RetryPolicy

export const DOWNSTREAM_PREVIEW_CONCURRENCY_LIMIT = 4
export const PREVIEW_INITIAL_COLUMN_LIMIT = 200

const NON_EXECUTABLE_PREVIEW_TYPES = new Set<string>([
  NODE_TYPES.SUBMODEL,
  NODE_TYPES.SUBMODEL_PORT,
])

/** Compare two column arrays by name+dtype; returns true if identical. */
function columnsEqual(a: ColumnDef[] | undefined, b: ColumnDef[] | undefined): boolean {
  return columnsEqualByFingerprint(a, b)
}

function resultToPreview(
  nodeId: string,
  label: string,
  r: NodeResult | PreviewNodeResponse,
  selectedFrame?: string,
): PreviewData {
  return makePreviewData(nodeId, label, {
    status: r.status,
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
    // Per-frame schema for a multi-frame producer (drives the frame-select
    // dropdown). Prefer the node's own `frame_columns`; fall back to
    // `node_frame_columns[nodeId]` (the route-level map) when the per-node
    // field is absent.
    frame_columns:
      r.frame_columns && Object.keys(r.frame_columns).length > 0
        ? r.frame_columns
        : ("node_frame_columns" in r ? r.node_frame_columns?.[nodeId] : undefined),
    selected_frame: selectedFrame,
  })
}

function previewColumnNamesForNode(node: Node): string[] | undefined {
  const columns = nodeData(node)._columns
  return columns && columns.length > 0
    ? columns.slice(0, PREVIEW_INITIAL_COLUMN_LIMIT).map((column) => column.name)
    : undefined
}

function canPreviewNode(node: Node): boolean {
  const documentStatus = useDocumentStatusStore.getState()
  const documentCanPreview = documentStatus.capabilities?.can_preview === true &&
    documentStatus.graphSynchronized
  return documentCanPreview &&
    nodeData(node)._loadAvailability !== "unavailable" &&
    nodeData(node)._loadAvailability !== "blocked" &&
    !NON_EXECUTABLE_PREVIEW_TYPES.has(effectiveNodeType(node))
}

interface PreviewDocumentFence {
  sourceFile: string
  sourceRevision: string
  storeSourceRevision: string
  loadStatus: PipelineLoadStatus | null
  canPreview: boolean
  graphSynchronized: boolean
}

function capturePreviewDocumentFence(
  sourceRevisionRef: React.MutableRefObject<string>,
): PreviewDocumentFence {
  const document = useDocumentStatusStore.getState()
  return {
    sourceFile: document.sourceFile,
    sourceRevision: sourceRevisionRef.current,
    storeSourceRevision: document.sourceRevision ?? "",
    loadStatus: document.loadStatus,
    canPreview: document.capabilities?.can_preview === true,
    graphSynchronized: document.graphSynchronized,
  }
}

function isPreviewDocumentFenceCurrent(
  captured: PreviewDocumentFence,
  sourceRevisionRef: React.MutableRefObject<string>,
): boolean {
  const current = useDocumentStatusStore.getState()
  return captured.canPreview &&
    captured.graphSynchronized &&
    current.sourceFile === captured.sourceFile &&
    sourceRevisionRef.current === captured.sourceRevision &&
    (current.sourceRevision ?? "") === captured.storeSourceRevision &&
    current.loadStatus === captured.loadStatus &&
    current.capabilities?.can_preview === true &&
    current.graphSynchronized
}

function applyPreviewColumnsToNodes(nodes: Node[], nodeId: string, columns: ColumnDef[], result: NodeResult, source: string): Node[] {
  return nodes.map((n) =>
    n.id === nodeId
      ? {
        ...n,
        data: {
          ...n.data,
          _columns: columns,
          _availableColumns: result.available_columns ?? columns,
          _schemaWarnings: result.schema_warnings ?? [],
          _columnsSource: source,
        },
      }
      : n,
  )
}

function applyPreviewSchemaMapsToNodes(nodes: Node[], result: NodeResult, source: string): Node[] {
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
        _columnsSource: source,
      },
    }
  })
}

function applyPreviewResultColumnsToNodes(nodes: Node[], nodeId: string, result: NodeResult, source: string): Node[] {
  const mapped = applyPreviewSchemaMapsToNodes(nodes, result, source)
  if (!result.columns || result.node_columns?.[nodeId]) return mapped
  return applyPreviewColumnsToNodes(mapped, nodeId, result.columns as ColumnDef[], result, source)
}

/**
 * Drop column stashes whose capture source no longer matches the active
 * source. The stash is a cache keyed (implicitly) by node id; the active
 * source is an input that affects its contents, so a stash captured under
 * another source — or under an unknown one (no `_columnsSource` tag) —
 * must be invalidated, never served. Stripped nodes return to the
 * never-previewed state the lazy gap-fill in `refreshPreview` already
 * handles; editors see "columns not loaded yet", exactly as on first load.
 * Returns the input array unchanged when nothing is stale.
 */
function invalidateStaleColumnStashes(nodes: Node[], activeSource: string): Node[] {
  const isStale = (n: Node) => {
    const data = nodeData(n)
    return (data._columns !== undefined || data._availableColumns !== undefined) &&
      data._columnsSource !== activeSource
  }
  if (!nodes.some(isStale)) return nodes
  return nodes.map((n) => {
    if (!isStale(n)) return n
    const { _columns, _availableColumns, _schemaWarnings, _columnsSource, ...rest } = n.data
    void _columns; void _availableColumns; void _schemaWarnings; void _columnsSource
    return { ...n, data: rest }
  })
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

function isApiTimeoutError(err: unknown): err is ApiTimeoutError {
  return typeof err === "object" &&
    err !== null &&
    (err as { name?: unknown }).name === "ApiTimeoutError"
}

function previewErrorDetail(err: unknown): string {
  if (err instanceof ApiError && err.detail) return err.detail
  return err instanceof Error ? err.message : String(err)
}

export default function usePipelineAPI({
  selectedNode,
  graphRef, parentGraphRef, activeSubmodelIdentity, submodelsRef,
  setNodesRaw, setCurrentSourceFile,
  preambleRef, pipelineNameRef, descriptionRef, sourceFileRef, sourceRevisionRef, preservedBlocksRef,
  nodeIdCounter: nodeIdCounterRef,
}: PipelineAPIParams): PipelineAPIReturn {
  const rowLimit = useSettingsStore((s) => s.rowLimit)
  const streamingChunkSize = useSettingsStore((s) => s.streamingChunkSize)
  const activeSource = useSettingsStore((s) => s.activeSource)
  const addToast = useToastStore((s) => s.addToast)
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [previewData, setPreviewData] = useState<PreviewData | null>(null)
  const [previewBusy, setPreviewBusy] = useState(false)
  const [nodeStatuses, setNodeStatuses] = useState<Record<string, "ok" | "error" | "running">>({})
  const previewAbort = useRef<AbortController | null>(null)
  const previewDebounce = useRef<ReturnType<typeof setTimeout> | null>(null)
  const previewRequestSeq = useRef(0)
  const saveRequestSeq = useRef(0)
  const appliedSaveSeq = useRef(0)
  const invalidatePreviewRequests = useCallback(() => {
    ++previewRequestSeq.current
  }, [])

  // Stable refs for values that change across renders but shouldn't
  // trigger re-creation of callbacks. Read at call-time instead.
  const rowLimitRef = useRef(rowLimit)
  useEffect(() => { rowLimitRef.current = rowLimit }, [rowLimit])
  const streamingChunkSizeRef = useRef(streamingChunkSize)
  useEffect(() => { streamingChunkSizeRef.current = streamingChunkSize }, [streamingChunkSize])
  const activeSourceRef = useRef(activeSource)
  useEffect(() => { activeSourceRef.current = activeSource }, [activeSource])
  const ensureSnapshotsForNodes = useCallback(
    (nodes: Node[], signal: AbortSignal) =>
      ensureInputSnapshots(nodes, {
        signal,
        onBuildStart: () => addToast("info", "Building input snapshot…"),
      }),
    [addToast],
  )

  // Source switch invalidates column stashes captured under other sources.
  // Runs on mount too: the invariant is "no stash disagrees with the active
  // source", not just "clean up after a toggle" — a pipeline loaded with
  // stashes of unknown provenance gets them stripped and lazily re-filled.
  useEffect(() => {
    setNodesRaw((nds) => invalidateStaleColumnStashes(nds, activeSource))
  }, [activeSource, setNodesRaw])

  const adoptPipelineDocument = useCallback((data: import("../types/pipelineDocument").PipelineEditorDocument) => {
    if (data.source_file && data.source_revision === null) {
      throw new Error("parsePipelineEditorDocument: live document has no source_revision")
    }
    const adapted = adaptPipelineEditorDocument(data)
    const pipelineNodes = adapted.nodes
    const pipelineEdges = adapted.edges
    const loadedPreamble = data.preamble ?? ""
    const loadedSubmodels = adapted.submodels
    preambleRef.current = loadedPreamble
    submodelsRef.current = loadedSubmodels
    sourceRevisionRef.current = data.source_revision ?? ""
    preservedBlocksRef.current = data.preserved_blocks
    useDocumentStatusStore.getState().loadDocumentStatus(data, false)
    useGraphStore.getState().loadGraphSnapshot({
      nodes: pipelineNodes,
      edges: normalizeEdges(pipelineEdges),
      preamble: loadedPreamble,
      submodels: loadedSubmodels,
    })
    useDocumentStatusStore.getState().setGraphSynchronized(true)
    if (data.pipeline_name) pipelineNameRef.current = data.pipeline_name
    if (data.pipeline_description != null) descriptionRef.current = data.pipeline_description
    if (data.source_file) {
      sourceFileRef.current = data.source_file
      setCurrentSourceFile?.(data.source_file)
    }
    if (data.source_selection_trusted) {
      useSettingsStore.getState().setSources(data.sources)
      if (data.active_source) useSettingsStore.getState().setActiveSource(data.active_source)
    }
    nodeIdCounterRef.current = computeNextNodeId(pipelineNodes)
  }, [setCurrentSourceFile, preambleRef, pipelineNameRef, descriptionRef, sourceFileRef, sourceRevisionRef, preservedBlocksRef, submodelsRef, nodeIdCounterRef])

  // Initial pipeline load
  useEffect(() => {
    const controller = new AbortController()
    let disposed = false
    useDocumentStatusStore.getState().reset()

    loadPipeline({ signal: controller.signal, retry: INITIAL_PIPELINE_RETRY_POLICY })
      .then((raw) => {
        if (disposed) return
        // Narrow the response at the ingestion boundary.  Any drift in
        // the backend contract (missing `nodes`/`edges`, wrong type on an
        // optional field) throws a named Error that flows into the
        // `.catch` handler below — rather than surfacing downstream as a
        // cryptic "undefined is not iterable" three callbacks deep.
        const data = parsePipelineEditorDocument(raw)
        adoptPipelineDocument(data)
        setLoading(false)
      })
      .catch((err) => {
        if (disposed || (isAbortError(err) && controller.signal.aborted)) return
        const detail = err instanceof Error ? err.message : String(err)
        setLoadError(detail)
        addToast("error", `Failed to load pipeline: ${detail}`)
        setLoading(false)
      })

    return () => {
      disposed = true
      controller.abort()
    }
  }, [adoptPipelineDocument, addToast])

  const fetchPreviewImmediate = useCallback((node: Node, existingRequestId?: number, options?: { bypassCache?: boolean; snapshotsEnsured?: boolean }) => {
    const requestId = existingRequestId ?? ++previewRequestSeq.current
    // Abort any in-flight preview request
    previewAbort.current?.abort()
    previewAbort.current = null
    if (!canPreviewNode(node)) {
      setPreviewData(null)
      setPreviewBusy(false)
      return
    }
    if (
      useDocumentStatusStore.getState().loadStatus === "degraded" &&
      activeSubmodelIdentity !== null
    ) {
      setPreviewData(null)
      setPreviewBusy(false)
      return
    }
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
    const snapshotDocumentFence = capturePreviewDocumentFence(sourceRevisionRef)
    const snapshotDocumentRevision = snapshotDocumentFence.sourceRevision
    const snapshotDocumentStatus = snapshotDocumentFence.loadStatus
    const recoveryPreview = snapshotDocumentStatus === "degraded"
    const documentStillCurrent = () =>
      isPreviewDocumentFenceCurrent(snapshotDocumentFence, sourceRevisionRef)
    const matchesRequestContext = (cached: { structuralVersion: number; source?: string; rowLimit?: number }) =>
      cached.structuralVersion === structuralVersion &&
      cached.source === snapshotSource &&
      cached.rowLimit === snapshotRowLimit
    const requestStillCurrent = () =>
      previewRequestSeq.current === requestId &&
      useGraphStore.getState().structuralVersion === structuralVersion &&
      documentStillCurrent()

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
    let propagationDone: Promise<void> = Promise.resolve()
    const propagate = (changedNodeId: string): Promise<void> => new Promise((resolve) => {
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

      if (reachableNodeIds.size === 0) {
        resolve()
        return
      }

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
      let resolved = false
      let abortHandler: (() => void) | null = null

      const finishPropagation = () => {
        if (resolved) return
        resolved = true
        if (abortHandler) {
          controller.signal.removeEventListener("abort", abortHandler)
          abortHandler = null
        }
        resolve()
      }

      abortHandler = finishPropagation
      if (controller.signal.aborted) {
        finishPropagation()
        return
      }
      controller.signal.addEventListener("abort", abortHandler, { once: true })

      const maybeFinishPropagation = () => {
        if (resolved) return
        if (!requestStillCurrent() || controller.signal.aborted) {
          finishPropagation()
          return
        }
        if (
          settledNodeIds.size >= reachableNodeIds.size &&
          activePreviewCount === 0 &&
          readyQueue.length === 0
        ) {
          finishPropagation()
        }
      }

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
        maybeFinishPropagation()
      }

      const drainReadyQueue = () => {
        if (!requestStillCurrent() || controller.signal.aborted) {
          finishPropagation()
          return
        }
        while (
          activePreviewCount < DOWNSTREAM_PREVIEW_CONCURRENCY_LIMIT &&
          readyQueue.length > 0 &&
          requestStillCurrent()
        ) {
          const nodeId = readyQueue.shift()!
          const cascadeGraph = resolveCascadeGraph()
          const dsNode = cascadeNodes.find((n) => n.id === nodeId)
          if (!dsNode || !canPreviewNode(dsNode)) {
            settleNode(nodeId, false)
            continue
          }
          activePreviewCount += 1
          const oldColumns = dsNode ? nodeData(dsNode)._columns : undefined
          previewNode({
            graph: cascadeGraph,
            nodeId: runtimeNodeIdForVisibleNode(
              graphRef.current.nodes,
              nodeId,
              activeSubmodelIdentity,
            ),
            rowLimit: snapshotRowLimit,
            source: snapshotSource,
            requestedPreviewColumns: dsNode ? previewColumnNamesForNode(dsNode) : undefined,
            portLabel: previewPortLabel(dsNode),
            streamingChunkSize: snapshotChunkSize,
            signal: controller.signal,
          })
            .then((result) => {
              if (!requestStillCurrent()) return
              if (!result.columns) {
                settleNode(nodeId, false)
                return
              }
              const newColumns = result.columns as ColumnDef[]
              cascadeNodes = applyPreviewResultColumnsToNodes(cascadeNodes, nodeId, result, snapshotSource)
              setNodesRaw((nds) => applyPreviewResultColumnsToNodes(nds, nodeId, result, snapshotSource))
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
              maybeFinishPropagation()
            })
        }
        maybeFinishPropagation()
      }

      const queueNodePreview = (nodeId: string) => {
        if (scheduledNodeIds.has(nodeId) || !requestStillCurrent()) return
        scheduledNodeIds.add(nodeId)
        readyQueue.push(nodeId)
        drainReadyQueue()
      }

      settleNode(changedNodeId, true)
      maybeFinishPropagation()
    })

    const portLabel = previewPortLabel(node)
    const executePreview = () => {
      if (!requestStillCurrent() || controller.signal.aborted) {
        throw new DOMException("Preview request was superseded.", "AbortError")
      }
      if (recoveryPreview) {
        return previewRecoveryNode({
          sourceFile: sourceFileRef.current,
          sourceRevision: snapshotDocumentRevision,
          targetRecoveryId: node.id,
          rowLimit: snapshotRowLimit,
          source: snapshotSource,
          requestedPreviewColumns: previewColumnNamesForNode(node),
          portLabel,
          streamingChunkSize: snapshotChunkSize,
          signal: controller.signal,
        })
      }
      return previewNode({
          graph,
          nodeId: runtimeNodeIdForVisibleNode(
            graphRef.current.nodes,
            node.id,
            activeSubmodelIdentity,
          ),
          rowLimit: snapshotRowLimit,
          source: snapshotSource,
          requestedPreviewColumns: previewColumnNamesForNode(node),
          portLabel,
          streamingChunkSize: snapshotChunkSize,
          signal: controller.signal,
        })
    }
    const previewRequest =
      recoveryPreview || options?.snapshotsEnsured
        ? executePreview()
        : ensureSnapshotsForNodes(graph.nodes, controller.signal).then(
            executePreview,
          )
    previewRequest
      .then((result) => {
        // Superseded by a newer preview request: that request owns the
        // panel surface and will reach its own terminal state.
        if (previewRequestSeq.current !== requestId) return
        // The live document fence is authoritative even when a dirty local
        // graph was retained. Results admitted under an older revision/status
        // must never paint the panel or enter its cache.
        if (!documentStillCurrent()) return
        const preview = resultToPreview(node.id, label, result, portLabel)
        if (!requestStillCurrent()) {
          // The graph changed while this response was in flight (e.g. an
          // editor mirrored artifact metadata into node config, bumping
          // structuralVersion). Stale columns must not be written into the
          // restructured graph, but the panel must still reach a terminal
          // state — silently dropping the response would strand
          // "Executing pipeline..." forever with no request in flight and
          // no error surfaced. Paint only if the panel still shows this
          // node: a node deleted mid-flight has already had its panel
          // cleared by handleDeleteNode and must stay cleared.
          setPreviewData((prev) => (prev?.nodeId === node.id ? preview : prev))
          if (graphRef.current.nodes.some((n) => n.id === node.id)) {
            // Tagged with the fetch-time structuralVersion, so the next
            // preview sees a context mismatch and refetches in the
            // background instead of trusting this entry.
            storePreview(node.id, preview, structuralVersion, snapshotSource, snapshotRowLimit)
          }
          return
        }
        setPreviewData(preview)
        // Cache the result for next time
        storePreview(node.id, preview, structuralVersion, snapshotSource, snapshotRowLimit)
        if (result.node_statuses) {
          setNodeStatuses(result.node_statuses)
        }
        if (result.columns) {
          const oldColumns = nodeData(node)._columns
          const newColumns = result.columns as ColumnDef[]
          cascadeNodes = applyPreviewResultColumnsToNodes(cascadeNodes, node.id, result, snapshotSource)
          setNodesRaw((nds) => applyPreviewResultColumnsToNodes(nds, node.id, result, snapshotSource))
          // Cascade to downstream nodes if columns changed.
          if (!recoveryPreview && !columnsEqual(oldColumns, newColumns)) {
            propagationDone = propagate(node.id)
          }
        }
      })
      .catch((err: unknown) => {
        // Superseded by a newer preview request: that request owns the
        // panel surface.
        if (previewRequestSeq.current !== requestId) return
        if (!documentStillCurrent()) return
        if (isAbortError(err) || isPreviewSupersededError(err)) return
        const detail = previewErrorDetail(err)
        const failure = makePreviewData(node.id, label, { status: "error", error: detail })
        if (!requestStillCurrent()) {
          // Same terminal-state requirement as the success path: a failure
          // arriving after a mid-flight graph change must surface as an
          // error, not strand the panel on "loading".
          setPreviewData((prev) => (prev?.nodeId === node.id ? failure : prev))
          return
        }
        setPreviewData(failure)
        setNodeStatuses({})
        if (isApiTimeoutError(err)) {
          addToast("error", `Preview timed out for "${label}": ${detail}`)
        }
      })
      .finally(() => {
        if (previewRequestSeq.current === requestId) {
          setPreviewBusy(false)
        }
        void propagationDone.finally(() => {
          if (previewAbort.current === controller) {
            previewAbort.current = null
          }
        })
      })
  }, [graphRef, parentGraphRef, activeSubmodelIdentity, submodelsRef, preambleRef, sourceFileRef, sourceRevisionRef, setNodesRaw, addToast, ensureSnapshotsForNodes])

  const fetchPreview = useCallback((node: Node, options: FetchPreviewOptions = {}) => {
    const requestId = ++previewRequestSeq.current
    // Cancel any previous node preview as soon as the user changes
    // selection. The next request is still debounced, but stale backend
    // work should not keep running during that debounce window.
    previewAbort.current?.abort()
    previewAbort.current = null
    if (previewDebounce.current) {
      clearTimeout(previewDebounce.current)
      previewDebounce.current = null
    }
    if (!canPreviewNode(node)) {
      setPreviewData(null)
      setPreviewBusy(false)
      return
    }
    setPreviewBusy(true)
    if (useDocumentStatusStore.getState().loadStatus === "degraded") {
      if (activeSubmodelIdentity !== null) {
        setPreviewData(null)
        setPreviewBusy(false)
        return
      }
      fetchPreviewImmediate(node, requestId, {
        bypassCache: true,
        snapshotsEnsured: true,
      })
      return
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
  }, [activeSubmodelIdentity, fetchPreviewImmediate])

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
    previewAbort.current?.abort()
    previewAbort.current = null
    const controller = new AbortController()
    previewAbort.current = controller
    if (previewDebounce.current) {
      clearTimeout(previewDebounce.current)
      previewDebounce.current = null
    }
    if (!canPreviewNode(node)) {
      controller.abort()
      previewAbort.current = null
      setPreviewData(null)
      setPreviewBusy(false)
      return
    }
    setPreviewBusy(true)
    if (useDocumentStatusStore.getState().loadStatus === "degraded") {
      if (activeSubmodelIdentity !== null) {
        controller.abort()
        previewAbort.current = null
        setPreviewData(null)
        setPreviewBusy(false)
        return
      }
      fetchPreviewImmediate(node, requestId, {
        bypassCache: true,
        snapshotsEnsured: true,
      })
      return
    }
    const structuralVersion = useGraphStore.getState().structuralVersion
    const snapshotDocumentFence = capturePreviewDocumentFence(sourceRevisionRef)
    const documentStillCurrent = () =>
      isPreviewDocumentFenceCurrent(snapshotDocumentFence, sourceRevisionRef)
    const requestStillCurrent = () =>
      previewRequestSeq.current === requestId &&
      useGraphStore.getState().structuralVersion === structuralVersion &&
      documentStillCurrent()

    const { nodes, edges } = graphRef.current
    const nodeMap = new Map(nodes.map((n) => [n.id, n]))

    // Find direct upstream nodes that have never been previewed (no _columns)
    // or whose stash was captured under a different source (stale — the
    // invalidation effect strips these, but the graph ref may not have
    // flushed yet, so the filter checks the source tag directly too).
    const upstreamIds = edges
      .filter((e) => e.target === node.id)
      .map((e) => e.source)
    const staleUpstream = upstreamIds
      .map((id) => nodeMap.get(id))
      .filter((n): n is Node =>
        !!n && canPreviewNode(n) &&
        (!nodeData(n)._columns || nodeData(n)._columnsSource !== activeSourceRef.current))

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

    const previewStaleUpstream = () => new Promise<void>((resolve) => {
      let nextIndex = 0
      let activeCount = 0
      let settledCount = 0

      const finishOne = () => {
        activeCount -= 1
        settledCount += 1
        drain()
      }

      const drain = () => {
        if (!requestStillCurrent() || controller.signal.aborted) {
          resolve()
          return
        }
        if (settledCount >= staleUpstream.length) {
          resolve()
          return
        }
        while (
          activeCount < DOWNSTREAM_PREVIEW_CONCURRENCY_LIMIT &&
          nextIndex < staleUpstream.length
        ) {
          const upstream = staleUpstream[nextIndex++]
          activeCount += 1
          previewNode({
            graph,
            nodeId: runtimeNodeIdForVisibleNode(
              graphRef.current.nodes,
              upstream.id,
              activeSubmodelIdentity,
            ),
            rowLimit: snapshotRowLimit,
            source: snapshotSource,
            requestedPreviewColumns: previewColumnNamesForNode(upstream),
            portLabel: previewPortLabel(upstream),
            streamingChunkSize: snapshotChunkSize,
            signal: controller.signal,
          })
            .then((result) => {
              if (!requestStillCurrent()) return
              if (result.columns) {
                setNodesRaw((nds) => applyPreviewResultColumnsToNodes(nds, upstream.id, result, snapshotSource))
              }
            })
            .catch((err: unknown) => {
              if (!requestStillCurrent()) return
              if (isAbortError(err) || isPreviewSupersededError(err)) return
              const detail = previewErrorDetail(err)
              addToast("warning", `Upstream preview failed for "${upstream.data?.label || upstream.id}": ${detail}`)
            })
            .finally(finishOne)
        }
      }

      drain()
    })

    ensureSnapshotsForNodes(graph.nodes, controller.signal)
      .then(() => previewStaleUpstream())
      .then(() => {
        if (!requestStillCurrent()) {
          if (previewRequestSeq.current === requestId) setPreviewBusy(false)
          return
        }
        fetchPreviewImmediate(node, requestId, {
          bypassCache: true,
          snapshotsEnsured: true,
        })
      })
      .catch((err: unknown) => {
        if (previewRequestSeq.current !== requestId || isAbortError(err)) return
        if (!documentStillCurrent()) return
        const detail = previewErrorDetail(err)
        setPreviewData(
          makePreviewData(node.id, nodeLabel(node), {
            status: "error",
            error: detail,
          }),
        )
        setNodeStatuses({})
        setPreviewBusy(false)
      })
      .finally(() => {
        if (previewAbort.current === controller) {
          previewAbort.current = null
        }
      })
  }, [fetchPreviewImmediate, graphRef, parentGraphRef, activeSubmodelIdentity, submodelsRef, preambleRef, sourceRevisionRef, setNodesRaw, addToast, ensureSnapshotsForNodes])

  const previewNodeFrame = useCallback((nodeId: string, portLabel: string) => {
    const node = graphRef.current.nodes.find((n) => n.id === nodeId)
    if (!node || !canPreviewNode(node)) return
    const requestId = ++previewRequestSeq.current
    previewAbort.current?.abort()
    previewAbort.current = null
    if (previewDebounce.current) {
      clearTimeout(previewDebounce.current)
      previewDebounce.current = null
    }
    const controller = new AbortController()
    previewAbort.current = controller
    const label = nodeLabel(node)
    setPreviewBusy(true)
    // Keep the current table on screen (marked busy via setPreviewBusy) while
    // the requested frame loads, so switching frames doesn't flash empty.
    const structuralVersion = useGraphStore.getState().structuralVersion
    const snapshotDocumentFence = capturePreviewDocumentFence(sourceRevisionRef)
    const snapshotDocumentRevision = snapshotDocumentFence.sourceRevision
    const snapshotDocumentStatus = snapshotDocumentFence.loadStatus
    const documentStillCurrent = () =>
      isPreviewDocumentFenceCurrent(snapshotDocumentFence, sourceRevisionRef)
    const requestStillCurrent = () =>
      previewRequestSeq.current === requestId &&
      useGraphStore.getState().structuralVersion === structuralVersion &&
      documentStillCurrent()
    const graph = resolveGraphFromRefs(graphRef, parentGraphRef, submodelsRef, preambleRef)
    const recoveryPreview = snapshotDocumentStatus === "degraded"
    if (recoveryPreview && activeSubmodelIdentity !== null) {
      controller.abort()
      previewAbort.current = null
      setPreviewBusy(false)
      return
    }
    const snapshotsReady = recoveryPreview
      ? Promise.resolve()
      : ensureSnapshotsForNodes(graph.nodes, controller.signal)
    snapshotsReady.then(() => {
        if (!requestStillCurrent() || controller.signal.aborted) {
          throw new DOMException("Preview request was superseded.", "AbortError")
        }
        if (recoveryPreview) {
          return previewRecoveryNode({
            sourceFile: sourceFileRef.current,
            sourceRevision: snapshotDocumentRevision,
            targetRecoveryId: node.id,
            rowLimit: rowLimitRef.current,
            source: activeSourceRef.current,
            portLabel,
            streamingChunkSize: streamingChunkSizeRef.current,
            signal: controller.signal,
          })
        }
        return previewNode({
          graph,
          nodeId: runtimeNodeIdForVisibleNode(
            graphRef.current.nodes,
            node.id,
            activeSubmodelIdentity,
          ),
          rowLimit: rowLimitRef.current,
          source: activeSourceRef.current,
          portLabel,
          streamingChunkSize: streamingChunkSizeRef.current,
          signal: controller.signal,
        })
      })
      .then((result) => {
        if (previewRequestSeq.current !== requestId) return
        const preview = resultToPreview(node.id, label, result, portLabel)
        if (requestStillCurrent()) setPreviewData(preview)
      })
      .catch((err: unknown) => {
        if (previewRequestSeq.current !== requestId) return
        if (!documentStillCurrent()) return
        if (isAbortError(err) || isPreviewSupersededError(err)) return
        const detail = previewErrorDetail(err)
        setPreviewData(makePreviewData(node.id, label, { status: "error", error: detail }))
        if (isApiTimeoutError(err)) {
          addToast("error", `Preview timed out for "${label}": ${detail}`)
        }
      })
      .finally(() => {
        if (previewRequestSeq.current === requestId) setPreviewBusy(false)
        if (previewAbort.current === controller) previewAbort.current = null
      })
  }, [graphRef, parentGraphRef, activeSubmodelIdentity, submodelsRef, preambleRef, sourceFileRef, sourceRevisionRef, addToast, ensureSnapshotsForNodes])

  // Returns true when the save succeeded, false on failure — callers that
  // chain follow-on work (for example Commit) await this so they only proceed
  // once the ledger actually holds the latest editor state. Never rejects.
  const handleSave = useCallback(async (): Promise<boolean> => {
    const documentStatus = useDocumentStatusStore.getState()
    if (documentStatus.capabilities?.can_save !== true || !documentStatus.graphSynchronized) {
      addToast("error", "This pipeline is read-only until its load diagnostics are resolved.")
      return false
    }
    if (parentGraphRef.current) {
      addToast("error", "Return to the main pipeline before saving.")
      return false
    }
    const { nodes: n, edges: e } = graphRef.current
    // Warn about broken config references before saving
    const refWarnings = validateConfigRefs(n, submodelsRef.current)
    if (refWarnings.length > 0) {
      addToast("warning", formatConfigRefWarnings(refWarnings))
    }
    const edgeJoinIssue = findFirstInvalidEdgeJoin(n, e)
    if (edgeJoinIssue) {
      addToast("error", `Cannot save: ${formatEdgeJoinValidationIssue(edgeJoinIssue)}`)
      return false
    }
    const { sources: sc, activeSource: as_ } = useSettingsStore.getState()
    // Snapshot the exact graph/preamble/submodels that will reach the
    // backend, and stamp this attempt with a monotonic request id. The user
    // may keep editing — or start a newer save — while this request is in
    // flight, so we mark *this* snapshot saved (not the live store) and only
    // if a newer save hasn't already landed (concurrency guard via
    // saveRequestSeq / appliedSaveSeq).
    const savePreamble = preambleRef.current
    const saveSubmodels = structuredClone(submodelsRef.current)
    const savedSnapshot = captureGraphSnapshot({
      nodes: n,
      edges: e,
      preamble: savePreamble,
      submodels: saveSubmodels,
    })
    const saveRequestId = ++saveRequestSeq.current
    try {
      const data = await savePipeline({
        name: pipelineNameRef.current,
        description: descriptionRef.current,
        graph: { nodes: savedSnapshot.nodes, edges: savedSnapshot.edges, submodels: savedSnapshot.submodels },
        preamble: savePreamble,
        source_file: sourceFileRef.current,
        sources: sc,
        active_source: as_,
        preserved_blocks: preservedBlocksRef.current,
      })
      // Mark the exact graph snapshot that reached the backend, unless a
      // newer save has already been applied.
      if (saveRequestId > appliedSaveSeq.current) {
        useGraphStore.getState().markSaved(savedSnapshot)
        appliedSaveSeq.current = saveRequestId
        sourceRevisionRef.current = data.source_revision
        useDocumentStatusStore.getState().setSourceRevision(data.source_revision)
      }
      // Reflect the new ledger commit in the toolbar indicator (P2). null
      // when no working branch is configured — the indicator stays as-is.
      if (data.git_sha !== undefined) {
        useGitStore.getState().setLastSaveSha(data.git_sha)
      }
      // Let an open Git panel re-fetch its history (S38).
      useGitStore.getState().notifyHistoryChanged()
      addToast("success", `Saved → ${data.file}`)
      // The save succeeded but the backend flagged something unfinished (a
      // transform with no code, an API Input with no tables). These are
      // deliberately non-blocking, so they'd be invisible without their own
      // toast — and the user would only discover the problem on the next run.
      for (const warning of data.warnings ?? []) {
        addToast("warning", warning)
      }
      // A restored container has no git commit identity, so the save landed on
      // disk but was never version-captured. Prompt once per session — the
      // warning above keeps saying so after the user waves it away.
      if (data.identity_required && !isIdentityPromptDismissed()) {
        const git = useGitStore.getState()
        if (git.modal !== "identity") git.openModal("identity")
      }
      return true
    } catch (err: unknown) {
      const detail = err instanceof ApiError && err.detail
        ? err.detail
        : err instanceof Error
          ? err.message
          : "unknown error"
      addToast("error", `Failed to save pipeline: ${detail}`)
      return false
    }
  }, [graphRef, parentGraphRef, submodelsRef, preambleRef, descriptionRef, sourceFileRef, sourceRevisionRef, preservedBlocksRef, pipelineNameRef, addToast])

  const selectedNodeId = selectedNode?.id ?? null

  // Clear node statuses when selected node id changes (including deselect)
  // so statuses from a previous node don't bleed into the next selection.
  useEffect(() => {
    setNodeStatuses({})
  }, [selectedNodeId])

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      invalidatePreviewRequests()
      previewAbort.current?.abort()
      if (previewDebounce.current) clearTimeout(previewDebounce.current)
    }
  }, [invalidatePreviewRequests])

  return {
    loading,
    loadError,
    previewData, setPreviewData,
    previewBusy,
    nodeStatuses,
    fetchPreview, cancelPreview, refreshPreview, previewNodeFrame, handleSave, adoptPipelineDocument,
  }
}
