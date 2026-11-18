import { useEffect, useCallback, useRef, useState } from "react"
import type { Node } from "@xyflow/react"
import type { PreviewData } from "../panels/DataPreview"
import { makePreviewData } from "../utils/makePreviewData"
import {
  ApiError,
  loadPipeline,
  previewInputs,
  previewNode,
  previewRecoveryNode,
  savePipeline,
} from "../api/client"
import type { ApiTimeoutError, RetryPolicy } from "../api/client"
import { syncConnectionId } from "../api/syncConnection"
import {
  canvasNodeAddress,
  documentRequestScope,
  frameTargetPayload,
  preparePipelineRequest,
  resultKey,
  type FrameTargetPayload,
} from "../utils/requestGateway"
import { addressKey, nodeFrame } from "../utils/nodeAddress"
import { answerTargetKey, keyText, keysOnCanvas, placeAddress } from "../utils/answerAddresses"
import type { PreparedGraph, PreviewInputsResponse } from "../api/types"
import { toCanonicalGraphPayload } from "../utils/graphSnapshot"
import { type SavedStepCode, withEditedSteps, withSavedStepCode } from "../utils/editedSteps"
import { computeNextNodeId, normalizeEdges } from "../utils/graphHelpers"
import type { PreviewNodeResponse, SchemaWarning } from "../api/types"
import useToastStore from "../stores/useToastStore"
import useSettingsStore from "../stores/useSettingsStore"
import useGraphStore, { captureGraphSnapshot, canvasGraph, documentGraph } from "../stores/useGraphStore"
import { reportSaveCapture } from "../stores/saveCapture"
import useNodeResultsStore from "../stores/useNodeResultsStore"
import useNodeDataStore from "../stores/useNodeDataStore"
import useDocumentStatusStore, { documentReadOnlyReason } from "../stores/useDocumentStatusStore"
import { trackPendingSave } from "../stores/pendingSaves"
import { operationKey } from "../utils/operationKey"
import useUIStore from "../stores/useUIStore"
import useWorkbenchStore from "../stores/useWorkbenchStore"
import { validateConfigRefs, formatConfigRefWarnings } from "../utils/validateConfigRefs"
import { findFirstInvalidEdgeJoin, formatEdgeJoinValidationIssue } from "../utils/edgeJoinValidation"
import { effectiveNodeType, nodeData } from "../types/node"
import type { NodeStatus, PipelineEdge, SubmodelPortData } from "../types/node"
import { NODE_TYPES, isRequestInputType } from "../utils/nodeTypes"
import {
  adaptPipelineEditorDocument,
  parsePipelineEditorDocument,
  type PipelineEditorDocument,
  type PipelineLoadStatus,
} from "../types/pipelineDocument"
import { type ColumnFingerprintInput } from "../utils/columnFingerprint"
import { requestInputSourceHandles } from "../utils/apiInputPorts"
import { workbenchNodePorts, type WorkbenchTables } from "../utils/workbenchTables"
import {
  submodelOutputPreview,
  submodelOutputHandle,
  type SubmodelOutputPreview,
  type DrilledOccurrenceIdentity,
} from "../utils/submodelRuntimeTarget"
import { executionWarnings } from "../utils/executionDiagnostics"
import { refreshRereadInput } from "../utils/inputSnapshotSource"
import { newPreviewRequestId, pollPreviewProgress } from "./previewProgressPoller"
import { apiErrorCode, apiErrorMessage, isStaleRevision } from "../api/errors"
import { useDebouncedCallback } from "./useDebouncedCallback"
import { answer, ask, newest, signalPart, type Part } from "../utils/answers"
import { useRequestKind } from "./useRequestKind"
import { constantDrafts, constantsPayload, saveRefusal } from "../utils/globalConstants"

interface PipelineAPIParams {
  selectedNode: Node | null
  activeSubmodelIdentity: DrilledOccurrenceIdentity | null
  setNodesRaw: (updater: Node[] | ((nds: Node[]) => Node[])) => void
  setEdgesRaw: (edges: PipelineEdge[]) => void
  setSubmodelsRaw?: (submodels: Record<string, unknown>) => void
  setPreamble: (p: string) => void
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
  nodeStatuses: Record<string, NodeStatus>
  fetchPreview: (node: Node, options?: FetchPreviewOptions) => void
  cancelPreview: () => void
  /**
   * The Stop button: stop the running preview (and the input preparation it
   * waits on) on the server, and show the node's last stored result again.
   * A snapshot build that refuses to stop keeps the preview running, and
   * pressing Stop again cancels it again.
   */
  stopPreview: () => void
  /** Refresh: lazily preview upstream nodes missing _columns, then preview the target node. */
  refreshPreview: (node: Node, options?: RefreshPreviewOptions) => void
  /** Re-preview a multi-frame node showing a specific frame (the
   * frame-select dropdown on the canvas preview top-bar). Focused: it only
   * repaints `previewData` for the requested frame. The node is resolved from
   * the live graph by id. */
  previewNodeFrame: (nodeId: string, portLabel: string) => void
  /** Save the pipeline. Resolves true on success, false on failure (never rejects);
   *  callers chaining follow-on work (such as Commit) await this. */
  handleSave: () => Promise<boolean>
  /**
   * Atomically ingest a validated authoritative editor document, with the server fingerprint its
   * response named (null when the response names none, so the next live-sync resync refetches it).
   */
  adoptPipelineDocument: (
    document: import("../types/pipelineDocument").PipelineEditorDocument,
    documentFingerprint: string | null,
  ) => void
}

export interface FetchPreviewOptions {
  /**
   * Delay before the API preview starts. Expensive nodes can use a longer
   * idle delay so quick click-throughs are cancelled before backend work begins.
   */
  debounceMs?: number
}

export interface RefreshPreviewOptions {
  /**
   * The preview frame's Refresh: when the target is a structured Quote Input
   * (or an instance of one), re-read its file and cache every table again,
   * whether or not the file changed, before previewing it.
   */
  rereadSource?: boolean
}

function nodeLabel(node: Node): string {
  return String(node.data.label || node.id)
}

/**
 * The frame a node's preview is of: *workbench*, the newest fetch of the workbench's
 * tables, gives a workbench node its ports.
 */
function previewPortLabel(
  node: Node,
  workbench: WorkbenchTables | null,
  chosenFrame?: string,
): string | undefined {
  const data = nodeData(node)
  // A Workbench Output's tables, and a request input's, are its frames: its
  // preview is of the table last chosen in it while that is still one of its
  // tables, else of its first.
  let ports: string[]
  if (data.nodeType === NODE_TYPES.WORKBENCH_OUTPUT) {
    ports = workbenchNodePorts(data.nodeType, workbench)
  } else if (isRequestInputType(data.nodeType)) {
    ports = requestInputSourceHandles({ id: node.id, data }, workbench)
  } else {
    return undefined
  }
  return chosenFrame !== undefined && ports.includes(chosenFrame) ? chosenFrame : ports[0]
}

type ColumnDef = ColumnFingerprintInput[number]

const INITIAL_PIPELINE_RETRY_POLICY = {
  maxRetries: 6,
  baseDelayMs: 250,
} satisfies RetryPolicy

/** How many upstream previews a refresh runs at once. */
export const PREVIEW_FANOUT_CONCURRENCY_LIMIT = 4
/** How many times a preview prepares again for a graph changed during its preparation. */
export const MAX_PREVIEW_PREPARATION_RESTARTS = 2

interface ImmediatePreviewOptions {
  bypassCache?: boolean
  snapshotsEnsured?: boolean
  /**
   * An input whose snapshot is built again from its source before this preview
   * prepares; the preview then starts again as a new request.
   */
  rereadInput?: Node
  /** Preparations already abandoned because the graph changed under them. */
  preparationRestarts?: number
  /**
   * The key the node's results are kept under (`resultKey`), taken when the
   * preview was first asked for; a preview continued after an await keeps it.
   */
  nodeKey?: string
}
export const PREVIEW_INITIAL_COLUMN_LIMIT = 200

const NON_EXECUTABLE_PREVIEW_TYPES = new Set<string>([
  NODE_TYPES.SUBMODEL_PORT,
])

/**
 * A node's preview as it is shown and stored: an occurrence's output under that
 * output's cache key, another node's under its result key (`nodeKey`), carried as
 * `cacheKey` when it is not the node's id.
 */
function keyedPreviewData(data: PreviewData, output: SubmodelOutputPreview | undefined, nodeKey: string): PreviewData {
  if (!output) return nodeKey === data.nodeId ? data : { ...data, cacheKey: nodeKey }
  return {
    ...data,
    nodeLabel: output.outputName,
    cacheKey: output.cacheKey,
    outputHandle: output.outputHandle,
    selected_frame: output.outputName,
    frame_columns: Object.fromEntries(output.outputNames.map((name) => [name, name === output.outputName ? data.columns : []])),
  }
}

/** Generation ids of shared snapshots this preview wrote that other reads may now use. */
function capturedGenerationIds(result: PreviewNodeResponse): string[] {
  if (!("seed_plan" in result) || !result.seed_plan) return []
  return result.seed_plan
    .filter((entry) => entry.kind === "captured")
    .map((entry) => entry.generation_id)
}

function resultToPreview(
  nodeId: string,
  label: string,
  r: PreviewNodeResponse,
  selectedFrame?: string,
  /** The key the answer runs the target under (`answerTargetKey`); its maps name it so. */
  targetKey: string | null = null,
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
    seed_plan: "seed_plan" in r ? r.seed_plan ?? [] : [],
    // Per-frame schema for a multi-frame producer (drives the frame-select
    // dropdown). Prefer the node's own `frame_columns`; fall back to the
    // route-level map under the target's key when the per-node field is absent.
    frame_columns:
      r.frame_columns && Object.keys(r.frame_columns).length > 0
        ? r.frame_columns
        : (targetKey !== null && "node_frame_columns" in r ? r.node_frame_columns?.[targetKey] : undefined),
    selected_frame: selectedFrame,
  })
}

function previewColumnNamesForNode(
  node: Node,
  source: string,
  structuralVersion: number,
  portLabel: string | undefined,
): string[] | undefined {
  const data = nodeData(node)
  // A remembered preview layout is only a valid projection for the graph that
  // produced it. After an edit, let the server discover the current schema.
  if (data._columnsSource !== source || data._columnsStructuralVersion !== structuralVersion) return undefined
  // A frame of a multi-frame node is projected onto that frame's columns: the
  // flat ones are of whichever frame was last previewed.
  const columns = (portLabel === undefined ? undefined : data._frameColumns?.[portLabel]) ?? data._columns
  return columns && columns.length > 0
    ? columns.slice(0, PREVIEW_INITIAL_COLUMN_LIMIT).map((column) => column.name)
    : undefined
}

function submodelRecoveryPreviewNotice(node: Node): PreviewData {
  // Recovery preview is planned server-side from the root document, so a
  // drilled submodel view has no admissible preview; say so instead of
  // silently blanking the panel.
  return makePreviewData(node.id, nodeLabel(node), {
    status: "error",
    error:
      "Submodel output preview is unavailable while the pipeline is in recovery mode. " +
      "Repair the pipeline, or preview a ready node on the main canvas.",
  })
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

/** One preview whose inputs are prepared before it runs. */
interface PreviewInputTarget {
  target: FrameTargetPayload
  source: string
  requestedPreviewColumns?: string[]
}

/**
 * The input nodes a preview-inputs answer names, by address key (a pipeline node and a
 * child of the same id are two inputs): one of the pipeline's own, or a child in its
 * occurrence's definition, as the prepared document holds them.
 */
function inputNodesOf(graph: PreparedGraph, answer: PreviewInputsResponse): Array<[string, Node]> {
  const nodes: Array<[string, Node]> = []
  for (const key of answer.input_node_ids) {
    const address = answer.node_addresses[keyText(key)]
    if (!address) continue
    const at = addressKey({ occurrence: address.occurrence, node: address.node_id })
    if (address.occurrence === null) {
      const node = graph.nodes.find((candidate) => candidate.id === address.node_id)
      if (node) nodes.push([at, node as unknown as Node])
      continue
    }
    const occurrence = graph.nodes.find((candidate) => candidate.id === address.occurrence)
    const config = occurrence?.data?.config as { definition_id?: unknown } | undefined
    const definition = typeof config?.definition_id === "string"
      ? (graph.submodels as Record<string, { graph?: { nodes?: unknown[] } } | undefined> | undefined)?.[config.definition_id]
      : undefined
    const child = (definition?.graph?.nodes as Node[] | undefined)?.find((candidate) => candidate.id === address.node_id)
    if (child) nodes.push([at, child])
  }
  return nodes
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

/** A preview's document, as a late answer's part: it holds while the preview fence does. */
function previewDocumentPart(
  captured: PreviewDocumentFence,
  sourceRevisionRef: React.MutableRefObject<string>,
): Part {
  return {
    holds: () => isPreviewDocumentFenceCurrent(captured, sourceRevisionRef),
    subscribe: (listener) => useDocumentStatusStore.subscribe(() => listener()),
  }
}

function applyPreviewColumnsToNodes(nodes: Node[], nodeId: string, columns: ColumnDef[], result: PreviewNodeResponse, source: string, structuralVersion: number): Node[] {
  return nodes.map((n) =>
    n.id === nodeId
      ? {
        ...n,
        data: {
          ...n.data,
          _columns: columns,
          _availableColumns: result.available_columns,
          _frameColumns: result.frame_columns,
          _schemaWarnings: result.schema_warnings,
          _columnsSource: source,
          _columnsStructuralVersion: structuralVersion,
        },
      }
      : n,
  )
}

/**
 * An open occurrence's Input card runs nothing of its own: each of its ports
 * carries the columns of the endpoint feeding that port, as the answer's frame
 * provenance names it, read from the preview's schema maps under its key.
 */
function boundaryInputFrames(
  node: Node,
  result: PreviewNodeResponse,
  openOccurrence: string,
): Record<string, ColumnDef[]> {
  const data = nodeData(node) as Partial<SubmodelPortData>
  const frames: Record<string, ColumnDef[]> = {}
  if (data.portDirection !== "input" || data.instanceId !== openOccurrence) return frames
  const nodeColumns: Record<string, ColumnDef[] | undefined> = result.node_columns
  const nodeFrameColumns: Record<string, Record<string, ColumnDef[]> | undefined> = result.node_frame_columns
  for (const entry of result.occurrence_ports) {
    if (entry.occurrence !== openOccurrence || entry.direction !== "input" || !entry.endpoint) continue
    const key = keyText(entry.endpoint.key)
    const handle = entry.endpoint.handle
    const columns = (handle ? nodeFrameColumns[key]?.[handle] : undefined) ?? nodeColumns[key]
    if (columns) frames[entry.port] = columns
  }
  return frames
}

/**
 * Record a preview's schema maps on the nodes they describe, as the canvas open on
 * `openOccurrence` (null for the main pipeline) shows them: each key is placed by its
 * address (`keysOnCanvas`); a child of a collapsed occurrence is described by its card's
 * own entries, never its children's.
 */
function applyPreviewSchemaMapsToNodes(
  nodes: Node[],
  result: PreviewNodeResponse,
  source: string,
  structuralVersion: number,
  openOccurrence: string | null,
): Node[] {
  const nodeColumns: Record<string, ColumnDef[] | undefined> = result.node_columns
  const nodeFrameColumns: Record<string, Record<string, ColumnDef[]> | undefined> = result.node_frame_columns
  const nodeAvailableColumns: Record<string, ColumnDef[] | undefined> = result.node_available_columns
  const nodeSchemaWarnings: Record<string, SchemaWarning[] | undefined> = result.node_schema_warnings
  const keyOf = keysOnCanvas(result.node_addresses, openOccurrence)
  return nodes.map((n) => {
    if (openOccurrence !== null && nodeData(n).nodeType === NODE_TYPES.SUBMODEL_PORT) {
      const ports = boundaryInputFrames(n, result, openOccurrence)
      if (Object.keys(ports).length === 0) return n
      // Ports this preview did not reach keep their columns from the same source.
      const kept = nodeData(n)._columnsSource === source ? nodeData(n)._frameColumns : undefined
      return { ...n, data: {
        ...n.data,
        _frameColumns: { ...kept, ...ports },
        _columnsSource: source,
        _columnsStructuralVersion: structuralVersion,
      } }
    }
    const key = keyOf.get(n.id)
    if (key === undefined) return n
    const columns = nodeColumns[key]
    const frames = nodeFrameColumns[key]
    const warnings = nodeSchemaWarnings[key]
    if (!columns) {
      // A submodel occurrence has no flat column list of its own, only its
      // output ports' columns keyed by handle.
      return frames || warnings ? { ...n, data: {
        ...n.data,
        ...(frames ? { _frameColumns: frames } : {}),
        ...(warnings ? { _schemaWarnings: warnings } : {}),
        _columnsSource: source, _columnsStructuralVersion: structuralVersion,
      } } : n
    }
    return {
      ...n,
      data: {
        ...n.data,
        _columns: columns,
        _availableColumns: nodeAvailableColumns[key] ?? columns,
        _frameColumns: frames,
        _schemaWarnings: warnings ?? [],
        _columnsSource: source,
        _columnsStructuralVersion: structuralVersion,
      },
    }
  })
}

/**
 * A preview inside a submodel also names the pipeline's own nodes, such as the
 * parents feeding the occurrence. Recorded on the pipeline's own nodes in the
 * document, they seed the Input card the next time an occurrence is opened
 * (`buildSubmodelViewGraph`), even when that preview is served from the cache.
 */
function recordRootPreviewColumns(
  result: PreviewNodeResponse,
  source: string,
  structuralVersion: number,
): void {
  useGraphStore.getState().setRootNodesRaw((nodes) =>
    applyPreviewSchemaMapsToNodes(nodes, result, source, structuralVersion, null),
  )
}

/**
 * Record a preview of canvas node `nodeId` on the nodes on screen, open on
 * `openOccurrence` (null for the main pipeline). `targetKey` is the key the
 * answer ran the node under (`answerTargetKey`).
 */
export function applyPreviewResultColumnsToNodes(
  nodes: Node[],
  nodeId: string,
  result: PreviewNodeResponse,
  source: string,
  structuralVersion: number,
  openOccurrence: string | null,
  targetKey: string | null,
): Node[] {
  const mapped = applyPreviewSchemaMapsToNodes(nodes, result, source, structuralVersion, openOccurrence)
  const withColumns = targetKey !== null && targetKey in result.node_columns
    ? mapped
    : applyPreviewColumnsToNodes(mapped, nodeId, result.columns, result, source, structuralVersion)
  // The previewed node's notes (a Model Score's full-policy-term note), however its columns came.
  return withColumns.map((n) => (n.id === nodeId ? { ...n, data: { ...n.data, _notes: result.notes } } : n))
}

/** A node status's weight when a collapsed occurrence's card shows its children's as one. */
const STATUS_WEIGHT: Record<NodeStatus, number> = { ok: 0, running: 1, warning: 2, error: 3 }

function canvasNodeStatuses(
  result: PreviewNodeResponse,
  requestedNodeId: string,
  openOccurrence: string | null,
): Record<string, NodeStatus> {
  // Each key's status is shown where its address sits on the canvas; a collapsed
  // occurrence's card shows the worst of its children's.
  const statuses: Record<string, NodeStatus> = {}
  // The answer addresses the keys it names; its execution metrics address theirs.
  const place = (key: string) => {
    const address = result.node_addresses[key] ?? result.execution_metrics?.node_addresses[key]
    return address ? placeAddress(address, openOccurrence) : null
  }
  for (const [key, status] of Object.entries(result.node_statuses ?? {})) {
    const placed = place(key)
    if (!placed) continue
    const shown = statuses[placed.id]
    if (!placed.viaCard || shown === undefined || STATUS_WEIGHT[status] > STATUS_WEIGHT[shown]) {
      statuses[placed.id] = status
    }
  }
  const promoteWarning = (key: string) => {
    const nodeId = place(key)?.id
    if (nodeId !== undefined && statuses[nodeId] === "ok") statuses[nodeId] = "warning"
  }

  for (const [key, warnings] of Object.entries(result.node_schema_warnings ?? {})) {
    if (warnings.length > 0) promoteWarning(key)
  }
  if ((result.schema_warnings?.length ?? 0) > 0 && statuses[requestedNodeId] === "ok") {
    statuses[requestedNodeId] = "warning"
  }

  // The requested node by its canvas id, the nodes a diagnostic implicates by key.
  const warnings = executionWarnings(result.execution_metrics)
  if (warnings.requested && statuses[requestedNodeId] === "ok") statuses[requestedNodeId] = "warning"
  for (const key of warnings.keys) promoteWarning(key)

  return statuses
}

/**
 * Drop column stashes whose capture source no longer matches the active
 * source. The stash is a cache keyed (implicitly) by node id; the active
 * source is an input that affects its contents, so a stash captured under
 * another source — or under an unknown one (no `_columnsSource` tag) —
 * must be invalidated, never served. Stripped nodes return to the
 * never-previewed state the lazy gap-fill in `refreshPreview` already
 * handles; editors see "columns not loaded yet", exactly as on first load.
 * Per-frame stashes count too: a submodel occurrence's outputs and a
 * drilled view's Input card hold only those. Returns the input array
 * unchanged when nothing is stale.
 */
function invalidateStaleColumnStashes(nodes: Node[], activeSource: string): Node[] {
  const isStale = (n: Node) => {
    const data = nodeData(n)
    return (data._columns !== undefined || data._availableColumns !== undefined || data._frameColumns !== undefined) &&
      data._columnsSource !== activeSource
  }
  if (!nodes.some(isStale)) return nodes
  return nodes.map((n) => {
    if (!isStale(n)) return n
    const { _columns, _availableColumns, _frameColumns, _schemaWarnings, _notes, _columnsSource, _columnsStructuralVersion, ...rest } = n.data
    void _columns; void _availableColumns; void _frameColumns; void _schemaWarnings; void _notes; void _columnsSource; void _columnsStructuralVersion
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

/** The server dropped this preview for a newer one of the same node: its 409 says so by code. */
function isPreviewSupersededError(err: unknown): boolean {
  return err instanceof ApiError && err.status === 409 && apiErrorCode(err) === "request_superseded"
}

function isApiTimeoutError(err: unknown): err is ApiTimeoutError {
  return typeof err === "object" &&
    err !== null &&
    (err as { name?: unknown }).name === "ApiTimeoutError"
}

/** A request that failed with no HTTP answer: the server may still have acted on it. */
function isLostAnswer(err: unknown): boolean {
  return err instanceof TypeError || isApiTimeoutError(err)
}

export default function usePipelineAPI({
  selectedNode,
  activeSubmodelIdentity, 
  setNodesRaw,
  pipelineNameRef, descriptionRef, sourceFileRef, sourceRevisionRef, preservedBlocksRef,
  nodeIdCounter: nodeIdCounterRef,
}: PipelineAPIParams): PipelineAPIReturn {
  const rowLimit = useSettingsStore((s) => s.rowLimit)
  const activeSource = useSettingsStore((s) => s.activeSource)
  const addToast = useToastStore((s) => s.addToast)
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [previewData, setPreviewData] = useState<PreviewData | null>(null)
  const [previewBusy, setPreviewBusy] = useState(false)
  const [nodeStatuses, setNodeStatuses] = useState<Record<string, NodeStatus>>({})
  const previewAbort = useRef<AbortController | null>(null)
  const previewRequestSeq = useRef(0)
  // Requests the user stopped, so their abort settles as a stop rather than
  // being ignored like a superseded request.
  const stoppedRequests = useRef(new Set<number>())
  // Set while a stopped request's snapshot build refused to stop: Stop then
  // cancels that build again instead of starting over.
  const stopRetry = useRef<(() => Promise<void>) | null>(null)
  // The node the latest preview request is for, and the key its results are
  // kept under, so a stop can restore its last stored result.
  const previewTarget = useRef<{ node: Node; nodeKey: string } | null>(null)
  // The result a stop put back on screen: automatic calculation leaves it
  // alone (it is not a continuation of the stopped run) until Refresh or
  // another preview replaces it.
  const stoppedDisplay = useRef<PreviewData | null>(null)
  const saveRequestSeq = useRef(0)
  const appliedSaveSeq = useRef(0)
  const invalidatePreviewRequests = useCallback(() => {
    ++previewRequestSeq.current
  }, [])
  /**
   * The preview a request is, as a late answer's part: the newest of the panel's previews
   * while no later preview, stop or cancel has advanced the serial. Nothing announces the
   * serial, which never moves back; it is asked again as each answer settles.
   */
  const previewTurn = useCallback((requestId: number): Part => ({
    holds: () => previewRequestSeq.current === requestId,
    subscribe: () => () => undefined,
  }), [])
  // A frame preview is stored only when it is of the frame the node previews,
  // so the node-data epoch it is current at — and the frame to fetch again — is
  // kept beside the object the panel shows.
  const framePreviewEpochs = useRef(new WeakMap<PreviewData, { epoch: number; portLabel: string }>())
  // Frame names for ordinary nodes, stable public output handles for occurrences,
  // by result key, so each occurrence of a shared definition keeps its own choice.
  // Refresh and recalculation retain the choice; selecting an occurrence resets it.
  const chosenFrames = useRef(new Map<string, string>())
  const outputFor = useCallback((node: Node) =>
    effectiveNodeType(node) === NODE_TYPES.SUBMODEL
      ? submodelOutputPreview(node, useGraphStore.getState().submodels, chosenFrames.current.get(resultKey(node.id)))
      : undefined,
  [])
  const portLabelFor = useCallback(
    (node: Node) => effectiveNodeType(node) === NODE_TYPES.SUBMODEL
      ? outputFor(node)?.outputName
      : previewPortLabel(node, useWorkbenchStore.getState().tables, chosenFrames.current.get(resultKey(node.id))),
    [outputFor],
  )
  const fetchPreviewImmediateRef = useRef<
    ((node: Node, existingRequestId?: number, options?: ImmediatePreviewOptions) => void) | null
  >(null)

  // Stable refs for values that change across renders but shouldn't
  // trigger re-creation of callbacks. Read at call-time instead.
  const rowLimitRef = useRef(rowLimit)
  useEffect(() => { rowLimitRef.current = rowLimit }, [rowLimit])
  const activeSourceRef = useRef(activeSource)
  useEffect(() => { activeSourceRef.current = activeSource }, [activeSource])
  const ensureSnapshotsForNodes = useCallback(
    async (nodes: Node[], signal: AbortSignal, { force = false }: { force?: boolean } = {}) => {
      const { ensureInputSnapshots } = await import("./ensureInputSnapshots")
      return ensureInputSnapshots(nodes, {
        signal,
        force,
        // A forced build is the one the user asked for, and the loading panel
        // already says what it is doing.
        onBuildStart: force ? undefined : () => addToast("info", "Building input snapshot…"),
        onProgress: (message) => {
          if (signal.aborted || previewAbort.current?.signal !== signal) return
          setPreviewData((previous) => previous ? {
            ...previous,
            ...(message ? { status: "loading" as const } : {}),
            loading_message: message ?? undefined,
          } : previous)
        },
      })
    },
    [addToast],
  )
  // Refresh's re-read of a structured Quote Input. A forced build publishes
  // each table on its own, some even when it fails or is stopped, so every
  // outcome tells the node's readers to ask again.
  const rereadInputSnapshot = useCallback(
    async (input: Node, signal: AbortSignal) => {
      const askAgain = () => useNodeDataStore.getState().bumpEpoch()
      await answer([signalPart(signal)], ensureSnapshotsForNodes([input], signal, { force: true }), {
        ok: askAgain,
        failed: (error) => {
          askAgain()
          throw error
        },
        stale: (settled) => {
          askAgain()
          if (!("value" in settled)) throw settled.error
        },
      })
    },
    [ensureSnapshotsForNodes],
  )
  // Prepare only the inputs the previews of `targets` read: the backend
  // answers from the seed plan each would run under, so an input above a
  // shared snapshot, or outside every target's lineage, is never built.
  const ensureSnapshotsForPreviews = useCallback(
    async (
      graph: PreparedGraph,
      targets: PreviewInputTarget[],
      signal: AbortSignal,
    ) => {
      let ensured: Promise<void> = Promise.resolve()
      // Prepared for the caller while its signal holds: an aborted preview builds nothing.
      await answer([signalPart(signal)], Promise.all(
        targets.map((target) =>
          previewInputs({
            graph,
            target: target.target,
            source: target.source,
            requestedPreviewColumns: target.requestedPreviewColumns,
            signal,
          }),
        ),
      ), {
        ok: (inputs) => {
          const wanted = new Map<string, Node>()
          for (const read of inputs) {
            for (const [at, input] of inputNodesOf(graph, read)) wanted.set(at, input)
          }
          ensured = ensureSnapshotsForNodes([...wanted.values()], signal)
        },
        stale: (settled) => {
          throw "error" in settled ? settled.error : new DOMException("Input snapshot ensure was cancelled.", "AbortError")
        },
      })
      return ensured
    },
    [ensureSnapshotsForNodes],
  )

  // Source switch invalidates column stashes captured under other sources.
  // Runs on mount too: the invariant is "no stash disagrees with the active
  // source", not just "clean up after a toggle" — a pipeline loaded with
  // stashes of unknown provenance gets them stripped and lazily re-filled.
  useEffect(() => {
    setNodesRaw((nds) => invalidateStaleColumnStashes(nds, activeSource))
    // While a submodel is open the pipeline's own nodes hold stashes too
    // (`recordRootPreviewColumns`): they seed the Input card on the next
    // drill-in, so they answer to the same invariant.
    if (useGraphStore.getState().focus !== null) {
      useGraphStore.getState().setRootNodesRaw((nodes) => invalidateStaleColumnStashes(nodes, activeSource))
    }
  }, [activeSource, setNodesRaw])

  const adoptPipelineDocument = useCallback((
    data: import("../types/pipelineDocument").PipelineEditorDocument,
    documentFingerprint: string | null,
  ) => {
    if (data.source_file && data.source_revision === null) {
      throw new Error("parsePipelineEditorDocument: live document has no source_revision")
    }
    const adapted = adaptPipelineEditorDocument(data)
    const pipelineNodes = adapted.nodes
    const pipelineEdges = adapted.edges
    const loadedPreamble = data.preamble ?? ""
    const loadedSubmodels = adapted.submodels
    sourceRevisionRef.current = data.source_revision ?? ""
    preservedBlocksRef.current = data.preserved_blocks
    useDocumentStatusStore.getState().loadDocumentStatus(data, false, documentFingerprint)
    // Rules set aside by a Banding type switch last until the pipeline reloads.
    useUIStore.getState().clearBandingRuleStash()
    const normalizedEdges = normalizeEdges(pipelineEdges)
    useGraphStore.getState().loadGraphSnapshot({
      nodes: pipelineNodes,
      edges: normalizedEdges,
      preamble: loadedPreamble,
      submodels: loadedSubmodels,
      globalConstants: constantDrafts(data.global_constants),
      globalConstantsError: data.global_constants_error,
    })
    useDocumentStatusStore.getState().setGraphSynchronized(true)
    if (data.pipeline_name) pipelineNameRef.current = data.pipeline_name
    if (data.pipeline_description != null) descriptionRef.current = data.pipeline_description
    if (data.source_file) sourceFileRef.current = data.source_file
    if (data.source_selection_trusted) {
      useSettingsStore.getState().setSources(data.sources)
      if (data.active_source) useSettingsStore.getState().setActiveSource(data.active_source)
    }
    nodeIdCounterRef.current = computeNextNodeId(pipelineNodes)
  }, [pipelineNameRef, descriptionRef, sourceFileRef, sourceRevisionRef, preservedBlocksRef, nodeIdCounterRef])

  // Initial pipeline load
  const pipelineLoads = useRequestKind()
  useEffect(() => {
    useDocumentStatusStore.getState().reset()
    // The load belongs to this run of the effect: unmounting lets it go, and aborts it.
    const asked = ask([newest(pipelineLoads)])
    const failed = (err: unknown) => {
      const detail = err instanceof Error ? err.message : String(err)
      setLoadError(detail)
      addToast("error", `Failed to load pipeline: ${detail}`)
      setLoading(false)
    }
    void asked.answer((signal) => loadPipeline({ signal, retry: INITIAL_PIPELINE_RETRY_POLICY }), {
      ok: async ({ document: raw, documentFingerprint }) => {
        // Narrow the response at the ingestion boundary.  Any drift in
        // the backend contract (missing `nodes`/`edges`, wrong type on an
        // optional field) throws a named Error that is reported as the
        // load's failure — rather than surfacing downstream as a
        // cryptic "undefined is not iterable" three callbacks deep.
        let data: PipelineEditorDocument
        try {
          data = await parsePipelineEditorDocument(raw)
        } catch (err: unknown) {
          asked.apply(() => failed(err))
          return
        }
        asked.apply(() => {
          adoptPipelineDocument(data, documentFingerprint)
          setLoading(false)
        })
      },
      failed,
    })
    return () => asked.release()
  }, [adoptPipelineDocument, addToast, pipelineLoads])

  // After a stop, show the node's last stored result for the current source,
  // row limit and frame, or nothing (the panel then offers Refresh). *nodeKey*
  // is the key the stopped request was asked under.
  const restoreAfterStop = useCallback((node: Node, nodeKey: string) => {
    const stored = useNodeResultsStore.getState().getPreview(outputFor(node)?.cacheKey ?? nodeKey)
    const restored =
      stored &&
      stored.source === activeSourceRef.current &&
      stored.rowLimit === rowLimitRef.current &&
      stored.data.selected_frame === portLabelFor(node)
        ? stored.data
        : null
    stoppedDisplay.current = restored
    setPreviewData(restored)
  }, [portLabelFor, outputFor])

  // Send one preview request with a fresh id and poll that request's own step
  // progress onto the loading panel of *nodeId* while it runs; the response,
  // not the progress, is what settles the panel.
  const sendWithProgress = useCallback(
    (
      nodeId: string,
      signal: AbortSignal,
      isCurrent: () => boolean,
      send: (requestId: string) => Promise<PreviewNodeResponse>,
    ) => {
      const requestId = newPreviewRequestId()
      const stopPolling = pollPreviewProgress(requestId, signal, (progress) => {
        if (!isCurrent()) return
        setPreviewData((previous) =>
          previous && previous.nodeId === nodeId && previous.status === "loading"
            ? { ...previous, progress }
            : previous,
        )
      })
      return send(requestId).finally(stopPolling)
    },
    [],
  )

  /**
   * Settle a request the user stopped. Returns false when the request was not
   * stopped, so the caller handles the outcome as before.
   */
  const settleStopped = useCallback((node: Node, nodeKey: string, requestId: number, err: unknown): boolean => {
    if (!stoppedRequests.current.has(requestId)) return false
    stoppedRequests.current.delete(requestId)
    if (previewRequestSeq.current !== requestId) return true
    const failed = err as { name?: unknown; jobIds?: unknown; message?: unknown } | null
    if (failed?.name === "CancellationFailed" && Array.isArray(failed.jobIds)) {
      // The snapshot builds may still be running, so the preview stays running
      // and Stop cancels them again, until every one has stopped.
      let pending = failed.jobIds as string[]
      addToast("error", `Stopping failed: ${String(failed.message)} Press Stop to try again.`)
      stopRetry.current = async () => {
        const { cancelInputSnapshotBuilds } = await import("./ensureInputSnapshots")
        // The builds are cancelled whatever the panel shows now; the stopped preview is put
        // back only while it is still the panel's.
        const cancelled = (stillRunning: Awaited<ReturnType<typeof cancelInputSnapshotBuilds>>): boolean => {
          if (stillRunning) {
            pending = stillRunning.jobIds
            addToast("error", `Stopping failed: ${stillRunning.message} Press Stop to try again.`)
            return false
          }
          stopRetry.current = null
          return true
        }
        await answer([previewTurn(requestId)], cancelInputSnapshotBuilds(pending), {
          ok: (stillRunning) => {
            if (!cancelled(stillRunning)) return
            restoreAfterStop(node, nodeKey)
            setPreviewBusy(false)
          },
          stale: (settled) => {
            if (!("value" in settled)) throw settled.error
            cancelled(settled.value)
          },
        })
      }
      return true
    }
    restoreAfterStop(node, nodeKey)
    setPreviewBusy(false)
    return true
  }, [addToast, previewTurn, restoreAfterStop])

  const fetchPreviewImmediate = useCallback((node: Node, existingRequestId?: number, options?: ImmediatePreviewOptions) => {
    const requestId = existingRequestId ?? ++previewRequestSeq.current
    // Taken now, or when the preview was first asked for, and kept after every await.
    const nodeKey = options?.nodeKey ?? resultKey(node.id)
    previewTarget.current = { node, nodeKey }
    if (existingRequestId === undefined) stopRetry.current = null
    // Abort any in-flight preview request
    previewAbort.current?.abort()
    previewAbort.current = null
    if (!canPreviewNode(node)) {
      setPreviewData(null)
      setPreviewBusy(false)
      return
    }
    const output = outputFor(node)
    if (output === null) {
      setPreviewData(null)
      setPreviewBusy(false)
      return
    }
    if (
      useDocumentStatusStore.getState().loadStatus === "degraded" &&
      (activeSubmodelIdentity !== null || output !== undefined)
    ) {
      setPreviewData(submodelRecoveryPreviewNotice(node))
      setPreviewBusy(false)
      return
    }
    setPreviewBusy(true)

    const label = output?.outputName ?? nodeLabel(node)
    const cacheKey = output?.cacheKey ?? nodeKey
    // The answer's nodes are placed on the canvas the request is sent from.
    const openOccurrence = useGraphStore.getState().focus?.instanceId ?? null
    const { getPreview, setPreview: storePreview } = useNodeResultsStore.getState()
    const structuralVersion = useGraphStore.getState().structuralVersion

    // Capture settings at the moment the fetch starts so this preview uses
    // a consistent snapshot (Issues #33/#34).  Reading these refs again
    // later would let a concurrent user action (e.g. flipping the active
    // source while the preview is still in flight) split it across two
    // different sources.
    const snapshotRowLimit = rowLimitRef.current
    const snapshotSource = activeSourceRef.current
    // A snapshot published, refreshed, or cleared after the request is sent
    // may change its rows: the stored preview matches only at this epoch.
    const snapshotNodeDataEpoch = useNodeDataStore.getState().epoch
    const snapshotDocumentFence = capturePreviewDocumentFence(sourceRevisionRef)
    const snapshotDocumentRevision = snapshotDocumentFence.sourceRevision
    const snapshotDocumentStatus = snapshotDocumentFence.loadStatus
    const recoveryPreview = snapshotDocumentStatus === "degraded"
    const documentStillCurrent = () =>
      isPreviewDocumentFenceCurrent(snapshotDocumentFence, sourceRevisionRef)
    const matchesRequestContext = (cached: {
      structuralVersion: number
      source?: string
      rowLimit?: number
      nodeDataEpoch?: number
    }) =>
      cached.structuralVersion === structuralVersion &&
      cached.source === snapshotSource &&
      cached.rowLimit === snapshotRowLimit &&
      cached.nodeDataEpoch === snapshotNodeDataEpoch
    const requestStillCurrent = () =>
      previewRequestSeq.current === requestId &&
      useGraphStore.getState().structuralVersion === structuralVersion &&
      documentStillCurrent()

    const portLabel = output ? undefined : portLabelFor(node)
    // What the request reads, by address: the occurrence's output port, or the node's frame.
    const target = output?.target ?? nodeFrame(canvasNodeAddress(node.id), portLabel ?? null)
    const wireTarget = frameTargetPayload(target)
    const selectedFrame = output?.outputName ?? portLabel
    const requestedPreviewColumns = output ? undefined : previewColumnNamesForNode(node, snapshotSource, structuralVersion, portLabel)
    // Cache-first: show cached data immediately if available
    const cached = getPreview(cacheKey)
    if (
      !options?.bypassCache &&
      cached &&
      cached.source === snapshotSource &&
      cached.rowLimit === snapshotRowLimit &&
      cached.data.selected_frame === selectedFrame
    ) {
      if (requestStillCurrent()) setPreviewData(cached.data)
      // If cache is fresh for the same execution context, skip the API call.
      if (matchesRequestContext(cached)) {
        setPreviewBusy(false)
        return
      }
      // Otherwise continue to fetch fresh data in background (cached data shown meanwhile)
    } else {
      setPreviewData(keyedPreviewData(makePreviewData(node.id, label, { status: "loading" }), output, nodeKey))
    }

    const graph = preparePipelineRequest(documentRequestScope).graph
    const controller = new AbortController()
    previewAbort.current = controller

    // Snapshots this request captured raise the node-data epoch once its
    // response is applied, so every other reader asks again. Its own stored
    // preview stays current across that increment — unless something else
    // moved the epoch while it was in flight, which leaves it stale and so
    // fetched again.
    let storedPreview: PreviewData | null = null
    let storedAtEpoch = snapshotNodeDataEpoch
    let rootCapturedIds: string[] = []
    const announceOwnCaptures = (generationIds: string[]) => {
      const { epoch, bumpEpoch, noteAnnouncedCaptures } = useNodeDataStore.getState()
      const announced = noteAnnouncedCaptures(generationIds)
      if (announced && storedPreview && epoch === storedAtEpoch) {
        storedAtEpoch = epoch + 1
        useNodeResultsStore.getState().advancePreviewEpoch(cacheKey, storedPreview, storedAtEpoch)
      }
      if (announced) bumpEpoch()
    }

    // The answer belongs to this preview, of the document as it was asked of: a newer preview,
    // a stop or a cancel, or another document, leaves it to say nothing.
    const asked = ask([previewTurn(requestId), previewDocumentPart(snapshotDocumentFence, sourceRevisionRef)])
    const superseded = () => new DOMException("Preview request was superseded.", "AbortError")
    const withProgress = (send: (requestId: string) => Promise<PreviewNodeResponse>) =>
      sendWithProgress(node.id, controller.signal, requestStillCurrent, send)
    const executePreview = () => {
      if (!asked.current() || controller.signal.aborted) throw superseded()
      if (useGraphStore.getState().structuralVersion !== structuralVersion) {
        // Preparation asks the backend which inputs the preview reads, so an
        // editor settling its node's config — the Apply editor mirroring its
        // artifact — often lands here. Prepare again for the graph as it now
        // is, as a new request, rather than execute the obsolete graph; only a
        // graph that keeps changing stops with the refresh instruction.
        const restarts = options?.preparationRestarts ?? 0
        const current = canvasGraph().nodes.find((candidate) => candidate.id === node.id)
        const restart = fetchPreviewImmediateRef.current
        if (current && restart && restarts < MAX_PREVIEW_PREPARATION_RESTARTS) {
          asked.apply(() => restart(current, undefined, {
            ...options,
            nodeKey,
            snapshotsEnsured: false,
            preparationRestarts: restarts + 1,
          }))
          throw superseded()
        }
        throw new Error("The pipeline changed while preparing this preview. Refresh to preview the updated pipeline.")
      }
      if (recoveryPreview) {
        return withProgress((requestId) => previewRecoveryNode({
          sourceFile: sourceFileRef.current,
          sourceRevision: snapshotDocumentRevision,
          targetRecoveryId: node.id,
          rowLimit: snapshotRowLimit,
          source: snapshotSource,
          requestedPreviewColumns,
          portLabel,
          requestId,
          signal: controller.signal,
        }))
      }
      return withProgress((requestId) => previewNode({
          graph,
          target: wireTarget,
          rowLimit: snapshotRowLimit,
          source: snapshotSource,
          requestedPreviewColumns,
          requestId,
          signal: controller.signal,
        }))
    }
    // The re-read published the input's tables and raised the node-data epoch,
    // so the preview starts again as a new request: at the raised epoch, for the
    // graph as it now is. A stopped, superseded or deleted one runs nothing more.
    const previewAfterReread = (): never => {
      const current = canvasGraph().nodes.find((candidate) => candidate.id === node.id)
      const restart = fetchPreviewImmediateRef.current
      if (current && restart && !controller.signal.aborted) {
        asked.apply(() => restart(current, undefined, { ...options, nodeKey, rereadInput: undefined }))
      }
      throw superseded()
    }
    const previewRequest = (async (): Promise<PreviewNodeResponse> => {
      if (recoveryPreview || options?.snapshotsEnsured) return executePreview()
      if (options?.rereadInput) {
        await rereadInputSnapshot(options.rereadInput, controller.signal)
        return previewAfterReread()
      }
      await ensureSnapshotsForPreviews(
        graph,
        [
          {
            target: wireTarget,
            source: snapshotSource,
            requestedPreviewColumns,
          },
        ],
        controller.signal,
      )
      return executePreview()
    })()
    // Announced before the preview stops being busy, so the refetch of a stale displayed
    // preview never sees the entry before its re-stamp; a stop whose snapshot build refused
    // to stop keeps the preview running.
    const finish = () => {
      if (rootCapturedIds.length > 0) announceOwnCaptures(rootCapturedIds)
      if (previewRequestSeq.current === requestId && stopRetry.current === null) setPreviewBusy(false)
      if (previewAbort.current === controller) previewAbort.current = null
      asked.release()
    }
    void asked.answer(previewRequest, {
      ok: (result) => {
        try {
          rootCapturedIds = capturedGenerationIds(result)
          const targetKey = answerTargetKey(result, target)
          const preview = keyedPreviewData({ ...resultToPreview(node.id, label, result, portLabel, targetKey), target }, output, nodeKey)
          if (useGraphStore.getState().structuralVersion !== structuralVersion) {
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
            if (canvasGraph().nodes.some((n) => n.id === node.id)) {
              // Tagged with the fetch-time structuralVersion, so the next
              // preview sees a context mismatch and refetches in the
              // background instead of trusting this entry.
              storePreview(cacheKey, preview, structuralVersion, snapshotSource, snapshotRowLimit, snapshotNodeDataEpoch)
              storedPreview = preview
            }
            return
          }
          setPreviewData(preview)
          // Cache the result for next time
          storePreview(cacheKey, preview, structuralVersion, snapshotSource, snapshotRowLimit, snapshotNodeDataEpoch)
          storedPreview = preview
          if (result.node_statuses) {
            setNodeStatuses(canvasNodeStatuses(result, node.id, openOccurrence))
          }
          if (result.columns) {
            // An occurrence's output previewed from the main pipeline describes the
            // occurrence's ports through the answer's maps, never the occurrence itself.
            setNodesRaw((nds) => output
              ? applyPreviewSchemaMapsToNodes(nds, result, snapshotSource, structuralVersion, openOccurrence)
              : applyPreviewResultColumnsToNodes(nds, node.id, result, snapshotSource, structuralVersion, openOccurrence, targetKey))
            if (openOccurrence !== null && !output) recordRootPreviewColumns(result, snapshotSource, structuralVersion)
          }
        } finally {
          finish()
        }
      },
      failed: (err) => {
        try {
          if (settleStopped(node, nodeKey, requestId, err)) return
          if (isAbortError(err) || isPreviewSupersededError(err)) return
          const detail = apiErrorMessage(err)
          const failure = keyedPreviewData(makePreviewData(node.id, label, { status: "error", error: detail }), output, nodeKey)
          if (useGraphStore.getState().structuralVersion !== structuralVersion) {
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
        } finally {
          finish()
        }
      },
      // Superseded by a newer preview request, or another document: that request owns the
      // panel surface. A stopped one settles as a stop; captures it made are announced.
      stale: (settled) => {
        try {
          if ("value" in settled) rootCapturedIds = capturedGenerationIds(settled.value)
          else settleStopped(node, nodeKey, requestId, settled.error)
        } finally {
          finish()
        }
      },
    })
  }, [activeSubmodelIdentity, sourceFileRef, sourceRevisionRef, setNodesRaw, addToast, ensureSnapshotsForPreviews, rereadInputSnapshot, sendWithProgress, settleStopped, portLabelFor, outputFor, previewTurn])

  useEffect(() => {
    fetchPreviewImmediateRef.current = fetchPreviewImmediate
  }, [fetchPreviewImmediate])

  // A node preview waits for the selection to settle; Optimiser clicks pass a
  // longer per-call delay.
  const previewDebounce = useDebouncedCallback(fetchPreviewImmediate, 200)

  const fetchPreview = useCallback((node: Node, options: FetchPreviewOptions = {}) => {
    const requestId = ++previewRequestSeq.current
    // The key the node's results are kept under, taken as it is selected.
    const nodeKey = resultKey(node.id)
    previewTarget.current = { node, nodeKey }
    stopRetry.current = null
    // Cancel any previous node preview as soon as the user changes
    // selection. The next request is still debounced, but stale backend
    // work should not keep running during that debounce window.
    previewAbort.current?.abort()
    previewAbort.current = null
    previewDebounce.cancel()
    if (!canPreviewNode(node)) {
      setPreviewData(null)
      setPreviewBusy(false)
      return
    }
    // A new occurrence selection starts with its first declaration, even if
    // its previous visit showed another output.
    if (effectiveNodeType(node) === NODE_TYPES.SUBMODEL) chosenFrames.current.delete(resultKey(node.id))
    const output = outputFor(node)
    if (output === null) {
      setPreviewData(null)
      setPreviewBusy(false)
      return
    }
    const cacheKey = output?.cacheKey ?? nodeKey
    // A stored preview shows only for the frame the node previews.
    const portLabel = portLabelFor(node)
    if (useUIStore.getState().calculationMode === "manual") {
      // Manual calculation: show the node's last result for the current
      // source and row limit, and run nothing until Refresh is pressed.
      const stored = useNodeResultsStore.getState().getPreview(cacheKey)
      setPreviewData(
        stored &&
          stored.source === activeSourceRef.current &&
          stored.rowLimit === rowLimitRef.current &&
          stored.data.selected_frame === portLabel
          ? stored.data
          : null,
      )
      setPreviewBusy(false)
      return
    }
    setPreviewBusy(true)
    if (useDocumentStatusStore.getState().loadStatus === "degraded") {
      if (activeSubmodelIdentity !== null || output !== undefined) {
        setPreviewData(submodelRecoveryPreviewNotice(node))
        setPreviewBusy(false)
        return
      }
      fetchPreviewImmediate(node, requestId, {
        bypassCache: true,
        snapshotsEnsured: true,
        nodeKey,
      })
      return
    }
    // Cached data should paint immediately. Deferring it creates a visible
    // "Executing pipeline..." flash when switching away from result panels.
    const cached = useNodeResultsStore.getState().getPreview(cacheKey)
    if (
      cached &&
      cached.source === activeSourceRef.current &&
      cached.rowLimit === rowLimitRef.current &&
      cached.data.selected_frame === portLabel
    ) {
      setPreviewData(cached.data)
    } else {
      setPreviewData(keyedPreviewData(makePreviewData(node.id, nodeLabel(node), { status: "loading" }), output, nodeKey))
    }
    previewDebounce.schedule([node, requestId, { nodeKey }], options.debounceMs)
  }, [activeSubmodelIdentity, fetchPreviewImmediate, previewDebounce, portLabelFor, outputFor])

  const cancelPreview = useCallback(() => {
    ++previewRequestSeq.current
    previewAbort.current?.abort()
    previewAbort.current = null
    setPreviewBusy(false)
    previewDebounce.cancel()
  }, [previewDebounce])

  const stopPreview = useCallback(() => {
    const retry = stopRetry.current
    if (retry) {
      void retry()
      return
    }
    previewDebounce.cancel()
    const controller = previewAbort.current
    if (controller) {
      // The request's own abort path settles it (see settleStopped); an abort
      // during input preparation cancels the snapshot build it started.
      stoppedRequests.current.add(previewRequestSeq.current)
      controller.abort()
      return
    }
    // Nothing sent yet (a debounced preview): settle here.
    ++previewRequestSeq.current
    if (previewTarget.current) restoreAfterStop(previewTarget.current.node, previewTarget.current.nodeKey)
    setPreviewBusy(false)
  }, [previewDebounce, restoreAfterStop])

  /** Lazily preview upstream nodes that are missing _columns, then preview the target node. */
  const refreshPreview = useCallback((node: Node, options: RefreshPreviewOptions = {}) => {
    const requestId = ++previewRequestSeq.current
    // The key the node's results are kept under, taken before the upstream previews.
    const nodeKey = resultKey(node.id)
    previewTarget.current = { node, nodeKey }
    stopRetry.current = null
    previewAbort.current?.abort()
    previewAbort.current = null
    const controller = new AbortController()
    previewAbort.current = controller
    previewDebounce.cancel()
    if (effectiveNodeType(node) === NODE_TYPES.SUBMODEL) {
      fetchPreviewImmediate(node, requestId, { bypassCache: true, nodeKey })
      return
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
        setPreviewData(submodelRecoveryPreviewNotice(node))
        setPreviewBusy(false)
        return
      }
      fetchPreviewImmediate(node, requestId, {
        bypassCache: true,
        snapshotsEnsured: true,
        nodeKey,
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

    const { nodes, edges } = canvasGraph()
    const nodeMap = new Map(nodes.map((n) => [n.id, n]))

    // Occurrences have no flat _columns to fill: the target's schema maps
    // provide their public output schemas without previewing extra outputs.
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
        !!n && canPreviewNode(n) && effectiveNodeType(n) !== NODE_TYPES.SUBMODEL &&
        (!nodeData(n)._columns || nodeData(n)._columnsSource !== activeSourceRef.current))

    if (staleUpstream.length === 0) {
      // No upstream gaps — just preview the selected node directly. An input
      // has no upstream, so this is where the frame's Refresh re-reads a
      // structured Quote Input's source first.
      const rereadInput = options.rereadSource ? refreshRereadInput(node) : null
      fetchPreviewImmediate(node, requestId, { bypassCache: true, rereadInput: rereadInput ?? undefined, nodeKey })
      return
    }

    // Show loading state for the target node
    if (requestStillCurrent()) {
      setPreviewData(keyedPreviewData(makePreviewData(node.id, nodeLabel(node), { status: "loading" }), undefined, nodeKey))
    }

    const graph = preparePipelineRequest(documentRequestScope).graph

    // Capture settings once so every upstream preview in this refresh
    // uses the same snapshot (Issues #33/#34).
    const snapshotRowLimit = rowLimitRef.current
    const snapshotSource = activeSourceRef.current
    // The answers' nodes are placed on the canvas the refresh is sent from.
    const openOccurrence = useGraphStore.getState().focus?.instanceId ?? null

    // The refresh belongs to this preview, of the document as it was asked of.
    const asked = ask([previewTurn(requestId), previewDocumentPart(snapshotDocumentFence, sourceRevisionRef)])
    const announce = (result: PreviewNodeResponse) => {
      // The refreshed preview is requested after this, at the raised epoch.
      const { bumpEpoch, noteAnnouncedCaptures } = useNodeDataStore.getState()
      if (noteAnnouncedCaptures(capturedGenerationIds(result))) bumpEpoch()
    }
    const done = () => {
      if (previewAbort.current === controller) previewAbort.current = null
      asked.release()
    }
    const previewStaleUpstream = () => new Promise<void>((resolve) => {
      let nextIndex = 0
      let activeCount = 0
      let settledCount = 0

      // The target's own step progress follows these upstream previews.
      const reportUpstream = () => {
        if (!requestStillCurrent()) return
        setPreviewData((previous) =>
          previous && previous.nodeId === node.id && previous.status === "loading"
            ? {
                ...previous,
                loading_message: `Previewing inputs (${settledCount} of ${staleUpstream.length})`,
              }
            : previous,
        )
      }
      const finishOne = () => {
        activeCount -= 1
        settledCount += 1
        reportUpstream()
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
          activeCount < PREVIEW_FANOUT_CONCURRENCY_LIMIT &&
          nextIndex < staleUpstream.length
        ) {
          const upstream = staleUpstream[nextIndex++]
          activeCount += 1
          const upstreamTarget = nodeFrame(canvasNodeAddress(upstream.id), portLabelFor(upstream) ?? null)
          void asked.answer(previewNode({
            graph,
            target: frameTargetPayload(upstreamTarget),
            rowLimit: snapshotRowLimit,
            source: snapshotSource,
            requestedPreviewColumns: previewColumnNamesForNode(upstream, snapshotSource, structuralVersion, portLabelFor(upstream)),
            signal: controller.signal,
          }), {
            ok: (result) => {
              try {
                announce(result)
                if (useGraphStore.getState().structuralVersion !== structuralVersion) return
                if (result.columns) {
                  const upstreamKey = answerTargetKey(result, upstreamTarget)
                  setNodesRaw((nds) => applyPreviewResultColumnsToNodes(nds, upstream.id, result, snapshotSource, structuralVersion, openOccurrence, upstreamKey))
                  if (openOccurrence !== null) recordRootPreviewColumns(result, snapshotSource, structuralVersion)
                }
              } finally {
                finishOne()
              }
            },
            failed: (err) => {
              try {
                if (useGraphStore.getState().structuralVersion !== structuralVersion) return
                if (isAbortError(err) || isPreviewSupersededError(err)) return
                const detail = apiErrorMessage(err)
                addToast("warning", `Upstream preview failed for "${upstream.data?.label || upstream.id}": ${detail}`)
              } finally {
                finishOne()
              }
            },
            stale: (settled) => {
              try {
                if ("value" in settled) announce(settled.value)
              } finally {
                finishOne()
              }
            },
          })
        }
      }

      reportUpstream()
      drain()
    })

    // Stopped while previewing upstream nodes: nothing more runs. A refresh a newer preview
    // overtook leaves that one the panel; one whose document moved stops being busy.
    const afterUpstream = () => {
      if (controller.signal.aborted) {
        settleStopped(node, nodeKey, requestId, null)
        return
      }
      if (!requestStillCurrent()) {
        if (previewRequestSeq.current === requestId) setPreviewBusy(false)
        return
      }
      fetchPreviewImmediate(node, requestId, {
        bypassCache: true,
        snapshotsEnsured: true,
        nodeKey,
      })
    }
    void asked.answer(ensureSnapshotsForPreviews(
      graph,
      [node, ...staleUpstream].map((previewed) => ({
        target: frameTargetPayload(nodeFrame(canvasNodeAddress(previewed.id), portLabelFor(previewed) ?? null)),
        source: snapshotSource,
        requestedPreviewColumns: previewColumnNamesForNode(previewed, snapshotSource, structuralVersion, portLabelFor(previewed)),
      })),
      controller.signal,
    ), {
      ok: async () => {
        await asked.answer(previewStaleUpstream(), {
          ok: () => {
            try {
              afterUpstream()
            } finally {
              done()
            }
          },
          stale: () => {
            try {
              afterUpstream()
            } finally {
              done()
            }
          },
        })
      },
      failed: (err) => {
        try {
          if (settleStopped(node, nodeKey, requestId, err)) return
          if (isAbortError(err)) return
          const detail = apiErrorMessage(err)
          setPreviewData(
            keyedPreviewData(makePreviewData(node.id, nodeLabel(node), {
              status: "error",
              error: detail,
            }), undefined, nodeKey),
          )
          setNodeStatuses({})
          setPreviewBusy(false)
        } finally {
          done()
        }
      },
      stale: (settled) => {
        try {
          if (!("value" in settled)) settleStopped(node, nodeKey, requestId, settled.error)
          else afterUpstream()
        } finally {
          done()
        }
      },
    })
  }, [fetchPreviewImmediate, previewDebounce, activeSubmodelIdentity, sourceRevisionRef, setNodesRaw, addToast, ensureSnapshotsForPreviews, portLabelFor, previewTurn, settleStopped])

  const previewNodeFrame = useCallback((nodeId: string, portLabel: string) => {
    const node = canvasGraph().nodes.find((n) => n.id === nodeId)
    if (!node || !canPreviewNode(node)) return
    if (effectiveNodeType(node) === NODE_TYPES.SUBMODEL) {
      const handle = submodelOutputHandle(node, portLabel)
      chosenFrames.current.set(resultKey(nodeId), handle)
      previewDebounce.cancel()
      fetchPreviewImmediate(node)
      return
    }
    chosenFrames.current.set(resultKey(nodeId), portLabel)
    const frameTarget = nodeFrame(canvasNodeAddress(node.id), portLabel)
    const wireFrameTarget = frameTargetPayload(frameTarget)
    // The key the node's results are kept under, taken before the request.
    const nodeKey = resultKey(node.id)
    const requestId = ++previewRequestSeq.current
    previewTarget.current = { node, nodeKey }
    stopRetry.current = null
    previewAbort.current?.abort()
    previewAbort.current = null
    previewDebounce.cancel()
    const controller = new AbortController()
    previewAbort.current = controller
    const label = nodeLabel(node)
    const requestNodeDataEpoch = useNodeDataStore.getState().epoch
    setPreviewBusy(true)
    // Keep the current table on screen (marked busy via setPreviewBusy) while
    // the requested frame loads, so switching frames doesn't flash empty.
    const structuralVersion = useGraphStore.getState().structuralVersion
    const snapshotRowLimit = rowLimitRef.current
    const snapshotSource = activeSourceRef.current
    const snapshotDocumentFence = capturePreviewDocumentFence(sourceRevisionRef)
    const snapshotDocumentRevision = snapshotDocumentFence.sourceRevision
    const snapshotDocumentStatus = snapshotDocumentFence.loadStatus
    const documentStillCurrent = () =>
      isPreviewDocumentFenceCurrent(snapshotDocumentFence, sourceRevisionRef)
    const requestStillCurrent = () =>
      previewRequestSeq.current === requestId &&
      useGraphStore.getState().structuralVersion === structuralVersion &&
      documentStillCurrent()
    const graph = preparePipelineRequest(documentRequestScope).graph
    const recoveryPreview = snapshotDocumentStatus === "degraded"
    if (recoveryPreview && activeSubmodelIdentity !== null) {
      controller.abort()
      previewAbort.current = null
      setPreviewBusy(false)
      return
    }
    // The frame belongs to this preview, of the document as it was asked of.
    const asked = ask([previewTurn(requestId), previewDocumentPart(snapshotDocumentFence, sourceRevisionRef)])
    const frameRequest = (async (): Promise<PreviewNodeResponse> => {
      if (!recoveryPreview) {
        await ensureSnapshotsForPreviews(
          graph,
          [
            {
              target: wireFrameTarget,
              source: snapshotSource,
            },
          ],
          controller.signal,
        )
      }
      if (!requestStillCurrent() || controller.signal.aborted) {
        throw new DOMException("Preview request was superseded.", "AbortError")
      }
      if (recoveryPreview) {
        return sendWithProgress(node.id, controller.signal, requestStillCurrent, (previewRequestId) =>
          previewRecoveryNode({
            sourceFile: sourceFileRef.current,
            sourceRevision: snapshotDocumentRevision,
            targetRecoveryId: node.id,
            rowLimit: snapshotRowLimit,
            source: snapshotSource,
            portLabel,
            requestId: previewRequestId,
            signal: controller.signal,
          }),
        )
      }
      return sendWithProgress(node.id, controller.signal, requestStillCurrent, (previewRequestId) =>
        previewNode({
          graph,
          target: wireFrameTarget,
          rowLimit: snapshotRowLimit,
          source: snapshotSource,
          requestId: previewRequestId,
          signal: controller.signal,
        }),
      )
    })()
    // A stop whose snapshot build refused to stop keeps the preview running.
    const finish = () => {
      if (previewRequestSeq.current === requestId && stopRetry.current === null) setPreviewBusy(false)
      if (previewAbort.current === controller) previewAbort.current = null
      asked.release()
    }
    void asked.answer(frameRequest, {
      ok: (result) => {
        try {
          const { epoch, bumpEpoch, noteAnnouncedCaptures } = useNodeDataStore.getState()
          const captured = noteAnnouncedCaptures(capturedGenerationIds(result))
          const preview = keyedPreviewData(
            { ...resultToPreview(node.id, label, result, portLabel, answerTargetKey(result, frameTarget)), target: frameTarget },
            undefined,
            nodeKey,
          )
          // Its own captures leave it current unless something else moved the
          // epoch while it was in flight.
          const currentAt = captured && epoch === requestNodeDataEpoch ? epoch + 1 : requestNodeDataEpoch
          framePreviewEpochs.current.set(preview, { epoch: currentAt, portLabel })
          if (useGraphStore.getState().structuralVersion === structuralVersion) {
            setPreviewData(preview)
            // The frame the node previews is its stored preview, which selecting
            // the node again shows.
            if (portLabelFor(node) === portLabel) {
              useNodeResultsStore
                .getState()
                .setPreview(nodeKey, preview, structuralVersion, snapshotSource, snapshotRowLimit, currentAt)
            }
          }
          if (captured) bumpEpoch()
        } finally {
          finish()
        }
      },
      failed: (err) => {
        try {
          if (settleStopped(node, nodeKey, requestId, err)) return
          if (isAbortError(err) || isPreviewSupersededError(err)) return
          const detail = apiErrorMessage(err)
          setPreviewData(keyedPreviewData(makePreviewData(node.id, label, { status: "error", error: detail }), undefined, nodeKey))
          if (isApiTimeoutError(err)) {
            addToast("error", `Preview timed out for "${label}": ${detail}`)
          }
        } finally {
          finish()
        }
      },
      // Another preview owns the panel, or another document: captures the frame made are
      // announced, and a stopped one settles as a stop.
      stale: (settled) => {
        try {
          if ("value" in settled) {
            const { bumpEpoch, noteAnnouncedCaptures } = useNodeDataStore.getState()
            if (noteAnnouncedCaptures(capturedGenerationIds(settled.value))) bumpEpoch()
          } else {
            settleStopped(node, nodeKey, requestId, settled.error)
          }
        } finally {
          finish()
        }
      },
    })
  }, [previewDebounce, fetchPreviewImmediate, activeSubmodelIdentity, sourceFileRef, sourceRevisionRef, addToast, ensureSnapshotsForPreviews, sendWithProgress, settleStopped, portLabelFor, previewTurn])

  // Returns true when the save succeeded, false on failure — callers that
  // chain follow-on work (for example Commit) await this so they only proceed
  // once the ledger actually holds the latest editor state. Never rejects.
  const handleSave = useCallback(async (): Promise<boolean> => {
    const documentStatus = useDocumentStatusStore.getState()
    if (documentStatus.capabilities?.can_save !== true || !documentStatus.graphSynchronized) {
      addToast("error", documentReadOnlyReason())
      return false
    }
    // The document whatever the view shows: inside a submodel the pipeline's own nodes and
    // edges, which every edit there has already reached through its definition.
    const { nodes: n, edges: e } = documentGraph()
    // Warn about broken config references before saving
    const refWarnings = validateConfigRefs(n)
    if (refWarnings.length > 0) {
      addToast("warning", formatConfigRefWarnings(refWarnings))
    }
    // Vouch for the column stashes this gate reads: a stash left by an older
    // graph or another source must not block a save with a claim about
    // "current upstream columns" it cannot support.
    const edgeJoinIssue = findFirstInvalidEdgeJoin(n, e, {
      structuralVersion: useGraphStore.getState().structuralVersion,
      activeSource: useSettingsStore.getState().activeSource,
    }, useWorkbenchStore.getState().tables)
    if (edgeJoinIssue) {
      addToast("error", `Cannot save: ${formatEdgeJoinValidationIssue(edgeJoinIssue)}`)
      return false
    }
    const { sources: sc, activeSource: as_ } = useSettingsStore.getState()
    // A constants file that failed to load is read-only: the save sends no
    // constants and the server keeps the file. Otherwise every constant must
    // be valid; a missing source value is allowed.
    const { globalConstants: saveConstants, globalConstantsError } = useGraphStore.getState()
    if (globalConstantsError === null) {
      const refusal = saveRefusal(saveConstants, sc)
      if (refusal) {
        addToast("error", `Cannot save: ${refusal}`)
        return false
      }
    }
    // Snapshot the exact graph/preamble/submodels that will reach the
    // backend, and stamp this attempt with a monotonic request id. The user
    // may keep editing — or start a newer save — while this request is in
    // flight, so we mark *this* snapshot saved (not the live store) and only
    // if a newer save hasn't already landed (concurrency guard via
    // saveRequestSeq / appliedSaveSeq).
    const savePreamble = useGraphStore.getState().preamble
    const saveSourceRevision = sourceRevisionRef.current
    const saveSourceFile = documentStatus.sourceFile
    // Only the stepped nodes whose steps were edited since the last save are rendered from
    // them; every other one is sent with the code that save wrote (CGEN-11).
    const authored = withEditedSteps(
      { nodes: n, submodels: structuredClone(useGraphStore.getState().submodels) },
      useGraphStore.getState().lastSavedSnapshot,
    )
    const savedSnapshot = captureGraphSnapshot({
      nodes: authored.document.nodes,
      edges: e,
      preamble: savePreamble,
      submodels: authored.document.submodels,
      globalConstants: saveConstants,
    })
    const requestGraph = toCanonicalGraphPayload(savedSnapshot)
    const saveRequestId = ++saveRequestSeq.current
    const savePipelineName = pipelineNameRef.current
    const saveDescription = descriptionRef.current
    const savePreservedBlocks = preservedBlocksRef.current
    // Mark the exact graph snapshot that reached the backend, unless a newer
    // save has already been applied or another revision arrived meanwhile.
    const acknowledge = (sourceRevision: string, documentFingerprint: string, stepCode: readonly SavedStepCode[]) => {
      const observedRevision = sourceRevisionRef.current
      if (observedRevision !== saveSourceRevision && observedRevision !== sourceRevision) return
      // The baseline holds the code the server wrote for each edited node: the browser's own
      // rendering may not have arrived, and the next save sends this code for those steps.
      useGraphStore.getState().markSaved(withSavedStepCode(savedSnapshot, stepCode))
      appliedSaveSeq.current = saveRequestId
      sourceRevisionRef.current = sourceRevision
      const status = useDocumentStatusStore.getState()
      status.acknowledgeSave(sourceRevision, documentFingerprint)
      status.setGraphSynchronized(true)
      useUIStore.getState().setSyncBanner(null)
    }
    const request = {
      name: savePipelineName,
      description: saveDescription,
      graph: {
        nodes: requestGraph.nodes,
        edges: requestGraph.edges,
        submodels: requestGraph.submodels,
        ...(globalConstantsError === null
          ? { global_constants: constantsPayload(saveConstants) }
          : {}),
      },
      preamble: savePreamble,
      // The pipeline's file, also while a submodel's own file is the one shown.
      source_file: saveSourceFile,
      // A never-persisted document keeps an empty revision ref; the server wants null.
      base_revision: saveSourceRevision || null,
      sources: sc,
      active_source: as_,
      preserved_blocks: savePreservedBlocks,
      connection_id: syncConnectionId(),
      // A repeat under the same operation writes once: the server answers it with
      // what the first request did when that one committed.
      operation: operationKey(),
      edited_steps: authored.edited,
    }
    // Live sync holds a frame of this file until the save, its repeat included,
    // has settled.
    let settleSave!: () => void
    trackPendingSave(saveSourceFile, new Promise<void>((resolve) => { settleSave = resolve }))
    const sending = (async () => {
      try {
        return await savePipeline(request)
      } catch (err: unknown) {
        // No answer: the save may have been written. Sent once more, exactly, its
        // answer is the first request's if that committed, else the save made now.
        if (!isLostAnswer(err)) throw err
        return savePipeline(request)
      }
    })()
    // The save's answer marks its snapshot saved only while no newer save has been applied
    // and the document is still this file; what it wrote is reported either way.
    const thisSave: Part = {
      holds: () => saveRequestId > appliedSaveSeq.current && useDocumentStatusStore.getState().sourceFile === saveSourceFile,
      subscribe: (listener) => useDocumentStatusStore.subscribe(() => listener()),
    }
    const failedSave = (err: unknown) => {
      const detail = apiErrorMessage(err, "unknown error")
      addToast("error", `Failed to save pipeline: ${detail}`)
    }
    const wrote = (data: Awaited<ReturnType<typeof savePipeline>>) => {
      addToast("success", `Saved → ${data.file}`)
      // The ledger commit, the warnings and the identity prompt, as every save reports them.
      reportSaveCapture(data)
    }
    let saved = false
    try {
      await answer([thisSave], sending, {
        ok: (data) => {
          try {
            acknowledge(data.source_revision, data.document_fingerprint, data.step_code ?? [])
            wrote(data)
            saved = true
          } catch (err: unknown) {
            failedSave(err)
          }
        },
        failed: (err) => {
          if (isStaleRevision(err)) {
            if (sourceRevisionRef.current !== saveSourceRevision) return
            // The conflict is final until the user reloads (no implicit overwrite retry).
            useDocumentStatusStore.getState().setGraphSynchronized(false)
            useUIStore
              .getState()
              .setSyncBanner(
                "Pipeline changed on disk while you have unsaved changes. Reload the file or discard local edits first.",
              )
            addToast(
              "error",
              "Save rejected: the pipeline changed on disk. Reload the file or discard local edits first.",
            )
            return
          }
          failedSave(err)
        },
        stale: (settled) => {
          if ("value" in settled) {
            wrote(settled.value)
            saved = true
          } else if (!isStaleRevision(settled.error)) {
            failedSave(settled.error)
          }
        },
      })
    } finally {
      settleSave()
    }
    return saved
  }, [descriptionRef, sourceRevisionRef, preservedBlocksRef, pipelineNameRef, addToast])

  // In automatic calculation, the displayed preview is fetched again once a
  // snapshot is published, refreshed, or cleared after its request was sent: its rows may have been
  // computed from data that has since changed. A refetch whose seeds did not
  // change is a backend cache hit. Other nodes' stored previews are fetched
  // again when next displayed.
  const nodeDataEpoch = useNodeDataStore((s) => s.epoch)
  useEffect(() => {
    if (!previewData || previewBusy) return
    // In manual calculation the stale preview stays on screen, marked out of
    // date, until Refresh is pressed.
    if (useUIStore.getState().calculationMode === "manual") return
    // Nor does a result a stop put back start the stopped run again.
    if (previewData === stoppedDisplay.current) return
    const frame = framePreviewEpochs.current.get(previewData)
    if (frame) {
      if (frame.epoch !== nodeDataEpoch) previewNodeFrame(previewData.nodeId, frame.portLabel)
      return
    }
    const stored = useNodeResultsStore.getState().previews[previewData.cacheKey ?? previewData.nodeId]
    if (!stored || stored.data !== previewData || stored.nodeDataEpoch === nodeDataEpoch) return
    const node = canvasGraph().nodes.find((candidate) => candidate.id === previewData.nodeId)
    if (node) fetchPreviewImmediate(node)
  }, [nodeDataEpoch, previewData, previewBusy, fetchPreviewImmediate, previewNodeFrame])

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
    }
  }, [invalidatePreviewRequests])

  return {
    loading,
    loadError,
    previewData, setPreviewData,
    previewBusy,
    nodeStatuses,
    fetchPreview, cancelPreview, stopPreview, refreshPreview, previewNodeFrame, handleSave, adoptPipelineDocument,
  }
}
