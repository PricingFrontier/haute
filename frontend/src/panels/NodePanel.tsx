import { useCallback, useMemo, useState } from "react"
import { X, AlertTriangle, RefreshCw, Lock } from "lucide-react"
import { fetchExplorePivotMembers } from "../api/client"
import { NODE_TYPES, NODE_TYPE_META, REQUEST_INPUT_TYPES, isRequestInputType } from "../utils/nodeTypes"
import type { NodeTypeValue } from "../utils/nodeTypes"
import { authoritativeSourceHandles, edgeInputName } from "../utils/apiInputPorts"
import {
  ColumnsTab,
  SteppedCodePane,
  LazyEditorBoundary,
} from "./LazyNodeEditors"
import { stepInputNames, steppedSurfaceFor } from "../utils/polarsStepInputs"
import type { InputSource, SimpleNode, SimpleEdge, OnUpdateConfig, OnUpdateConfigResult, OnReplaceConfig } from "./editors"
import {
  effectiveNodeType,
  isSubmodelDefinition,
  isSubmodelInstanceConfig,
  type HauteNodeData,
  type LoadAvailability,
  type PipelineGraph,
} from "../types/node"
import type { PipelineDiagnostic } from "../types/pipelineDocument"
import useUIStore, { type ExplorePane, type ModellingPane, type OptimiserPane } from "../stores/useUIStore"
import useNodeDataStore, { profileForConsumer } from "../stores/useNodeDataStore"
import useNodeResultsStore, { hashConfig } from "../stores/useNodeResultsStore"
import useSettingsStore from "../stores/useSettingsStore"
import useDocumentStatusStore, { documentReadOnlyReason } from "../stores/useDocumentStatusStore"
import { recoverySummaryKey, useRecoverySummaryStore } from "../stores/useRecoverySummaryStore"
import { buildNodeDataCacheIdentity } from "./dataPointIdentity"
import { modellingPanesFor, resolveModellingPane } from "./modelling/modellingPanes"
import { OPTIMISER_PANES, resolveOptimiserPane } from "./optimiser/optimiserPanes"
import PanelShell from "./PanelShell"
import { SidePanelClose } from "../haute-ui"
import PreviewPanelTabs from "./PreviewPanelTabs"
import { useGraph } from "./useGraph"
import { buildGraph } from "../utils/buildGraph"
import { NODE_REFERENCE_URL } from "../utils/documentation"
import { CommittedTextField } from "../components/form"
import {
  useNodePanelSession,
  useNodeRenameSession,
  type NodePanelTab,
} from "./useNodePanelSession"
import { NodeConfigEditor } from "./NodeConfigEditor"
import { useRecordedNodeColumns } from "./useRecordedNodeColumns"

type NodePanelProps = {
  node: SimpleNode | null
  onClose: () => void
  onUpdateNode?: (id: string, data: Record<string, unknown>) => OnUpdateConfigResult
  onRenameNode?: (id: string, label: string) => Promise<OnUpdateConfigResult>
  onDeleteEdge?: (edgeId: string) => void
  onDeleteSubmodelInputPort?: (portName: string) => void
  onSwapEdgeJoinInputs?: (nodeId: string) => void
  onRefreshPreview?: () => void
  onRecoverSettings?: () => void
  /** True when showing last-selected node while nothing is actively selected */
  dimmed?: boolean
  /** 1-based line number of the error in user code, if any */
  errorLine?: number | null
  /** The last run's error message for this node, if it failed */
  runError?: string | null
  /** Preview rows from the current node's preview data (input columns pass through) */
  previewRows?: Record<string, unknown>[]
  /** True while the selected node preview request is still in flight. */
  selectedPreviewLoading?: boolean
  /** True while inspecting a created submodel instance's shared definition. */
  readOnly?: boolean
  /** True when the current pipeline document is not executable/mutable. */
  documentReadOnly?: boolean
  /** True while a submodel is open, so the node shown is inside one. */
  insideSubmodel?: boolean
  /** Node-scoped save for a `scoped_editable` node while the document stays fenced. */
  scopedSave?: () => Promise<{ ok: boolean; error?: string }>
  /** Opens an explicitly confirmed document-level recovery action. */
  onRemoveUnavailableNode?: (target: { sourceFile: string; recoveryId: string; action?: "remove" | "reset" | "recover" }) => void
}

// ─── Node types that do NOT show the Columns tab ──
// Output already has its own field selection; submodels/ports are placeholders;
// modelling, optimiser and explore nodes are sink-only (no outputs).
//
// A request input's column selection lives in `tables[].columns[]`, edited in the
// Quote Input's Schema panel and in the workbench for a Workbench Input.
const NO_COLUMNS_TAB = new Set<string>([
  ...REQUEST_INPUT_TYPES,
  NODE_TYPES.OUTPUT,
  NODE_TYPES.WORKBENCH_OUTPUT,
  NODE_TYPES.SUBMODEL,
  NODE_TYPES.SUBMODEL_PORT,
  NODE_TYPES.MODELLING,
  NODE_TYPES.OPTIMISER,
  NODE_TYPES.EXPLORE,
])

const POLARS_TAB_TYPES = new Set<string>([
  NODE_TYPES.DATA_INPUT,
  NODE_TYPES.EXTERNAL_FILE,
  NODE_TYPES.SCENARIO_EXPANDER,
  NODE_TYPES.RATING_STEP,
  NODE_TYPES.MODEL_SCORE,
])

const POLARS_TAB_HINTS: Record<string, React.ReactNode> = {
  [NODE_TYPES.DATA_INPUT]: <><code>df</code> = the opened input snapshot</>,
  [NODE_TYPES.EXTERNAL_FILE]: <><code>obj</code> = loaded file, assign to <code>df</code></>,
  [NODE_TYPES.SCENARIO_EXPANDER]: <>use <code>df</code> for expanded data</>,
  [NODE_TYPES.RATING_STEP]: <>use <code>df</code> for rated data</>,
  [NODE_TYPES.MODEL_SCORE]: <>Post-processing Code (optional)</>,
}

// Right-panel panes for Explore nodes. Code prepares the analysis dataset;
// Overview, Pivots, and Charts configure display, while Export remains scaffolding.
const EXPLORE_PANES = [
  { key: "code", label: "Transform" },
  { key: "overview", label: "Overview" },
  { key: "pivots", label: "Pivots" },
  { key: "charts", label: "Charts" },
  { key: "export", label: "Export" },
] as const satisfies readonly { key: ExplorePane; label: string }[]

type ColumnInfo = { name: string; dtype: string }

/**
 * The columns an edge carries: the source's columns for the output handle
 * the edge leaves from (a submodel output), else the source's columns.
 */
function edgeSourceColumns(edge: SimpleEdge, nodeMap: Record<string, SimpleNode>): ColumnInfo[] | undefined {
  const data = nodeMap[edge.source]?.data as Record<string, unknown> | undefined
  const frames = data?._frameColumns as Record<string, ColumnInfo[]> | undefined
  const handle = edge.sourceHandle
  if (handle && frames?.[handle]) return frames[handle]
  return data?._columns as ColumnInfo[] | undefined
}

/** Collect upstream columns from already-filtered incoming edges. */
function collectColumnsFromEdges(edges: SimpleEdge[], nodeMap: Record<string, SimpleNode>): ColumnInfo[] {
  const cols: ColumnInfo[] = []
  const seen = new Set<string>()
  edges.forEach(e => {
    const srcCols = edgeSourceColumns(e, nodeMap)
    if (srcCols) srcCols.forEach(c => { if (!seen.has(c.name)) { seen.add(c.name); cols.push(c) } })
  })
  return cols
}

function columnsSignature(columns: ColumnInfo[] | undefined): string {
  return columns?.map((column) => `${column.name}\u0002${column.dtype}`).join("\u0001") ?? ""
}

function upstreamColumnsSignature(edges: SimpleEdge[], nodeMap: Record<string, SimpleNode>): string {
  return edges
    .map((edge) => `${edge.source}\u0003${edge.sourceHandle ?? ""}\u0003${columnsSignature(edgeSourceColumns(edge, nodeMap))}`)
    .join("\u0004")
}

function inputSourceForEdge(
  edge: SimpleEdge,
  nodeMap: Record<string, SimpleNode>,
  submodels?: Record<string, unknown>,
): InputSource {
  const sourceNode = nodeMap[edge.source]
  if (!sourceNode) {
    throw new Error(`Cannot derive input name for edge ${edge.id}: source node ${edge.source} is missing`)
  }
  const sourceLabel = sourceNode.data.label || edge.source
  const name = edgeInputName(edge, sourceNode, submodels)
  const frameUnresolved =
    isRequestInputType(sourceNode.data.nodeType)
    && (edge.sourceHandle === null
      || edge.sourceHandle === undefined
      || !authoritativeSourceHandles(sourceNode).includes(edge.sourceHandle))

  const columns = edgeSourceColumns(edge, nodeMap)
  return {
    sourceNodeId: edge.source,
    name,
    sourceLabel,
    edgeId: edge.id,
    ...(frameUnresolved ? { frameUnresolved: true } : {}),
    ...(columns ? { columns } : {}),
    ...(typeof edge.targetHandle === "string" ? { targetHandle: edge.targetHandle } : {}),
  }
}

function upstreamInputSourceSignature(
  edges: SimpleEdge[],
  nodeMap: Record<string, SimpleNode>,
  submodels?: Record<string, unknown>,
): string {
  return JSON.stringify(
    edges.map((edge) => {
      const source = inputSourceForEdge(edge, nodeMap, submodels)
      return [
        edge.id,
        edge.source,
        source.sourceLabel,
        edge.sourceHandle === undefined ? "<undefined>" : edge.sourceHandle,
        edge.targetHandle ?? null,
        source.name,
        source.frameUnresolved === true,
        columnsSignature(source.columns),
      ]
    }),
  )
}

function UnknownNodeTypeDiagnostic({
  nodeType,
  config,
}: {
  nodeType: string
  config: Record<string, unknown>
}) {
  return (
    <div className="px-4 py-3 flex flex-col gap-3">
      <div
        role="alert"
        className="flex flex-col gap-2 rounded-lg px-3 py-3"
        style={{ background: 'var(--warning-soft)', border: '1px solid var(--warning-border)' }}
      >
        <div className="flex items-center gap-2">
          <AlertTriangle size={14} style={{ color: 'var(--warning-strong)' }} className="shrink-0" />
          <span className="text-[12px] font-bold uppercase tracking-[0.08em]" style={{ color: 'var(--warning-strong)' }}>
            Unknown node type
          </span>
        </div>
        <p className="text-[12px] leading-relaxed" style={{ color: 'var(--text-secondary)' }}>
          Node type <code className="font-mono">{nodeType}</code> is not registered in this UI build. This node is shown as a diagnostic only so its config is not edited through the wrong editor.
        </p>
        <a
          href={NODE_REFERENCE_URL}
          target="_blank"
          rel="noopener noreferrer"
          className="text-[12px] font-semibold underline underline-offset-2 w-fit"
          style={{ color: 'var(--text-accent)' }}
        >
          Open node documentation
        </a>
      </div>

      <div className="flex flex-col gap-1.5">
        <div className="text-[11px] font-bold uppercase tracking-[0.08em]" style={{ color: 'var(--text-muted)' }}>
          Raw config diagnostic
        </div>
        <pre
          data-testid="unknown-node-config-diagnostic"
          aria-label="Raw config diagnostic"
          className="text-[11px] leading-relaxed font-mono whitespace-pre-wrap break-words rounded-md px-3 py-2 select-text overflow-x-auto"
          style={{ background: 'var(--bg-panel)', border: '1px solid var(--border)', color: 'var(--text-primary)' }}
        >
          {JSON.stringify(config, null, 2)}
        </pre>
      </div>
    </div>
  )
}

// Cached preview-result fields cleared on user config edits.  Listing the
// keys explicitly (rather than stripping every leading-underscore field)
// lets selected_columns-only edits preserve `_availableColumns`: that
// pre-filter schema remains valid while the post-filter preview is stale.
// preserves selection/trace state — `_status`, `_traceActive`, etc. — which
// are intentionally NOT invalidated by a config change.  Adding a new cached
// field to ``HauteNodeData`` will not surface here automatically — the choice
// to clear or preserve a new field must be made deliberately by extending
// this list.
const CACHED_PREVIEW_KEYS: readonly (keyof HauteNodeData)[] = [
  "_columns",
  "_availableColumns",
  "_frameColumns",
  "_schemaWarnings",
  "_notes",
  "_columnsSource",
  "_columnsStructuralVersion",
]

function clearCachedResultShape(
  data: HauteNodeData,
  { preserveAvailableColumns = false }: { preserveAvailableColumns?: boolean } = {},
): HauteNodeData {
  const next = { ...data }
  for (const key of CACHED_PREVIEW_KEYS) {
    if (preserveAvailableColumns && key === "_availableColumns") continue
    delete next[key]
  }
  return next
}

type NodeRecoveryStatusProps = {
  recoveryId: string | null
  blockedPath: string[] | null
  scopedSave?: () => Promise<{ ok: boolean; error?: string }>
  scopedSaving: boolean
}

/** Recovery summary, completeness gaps, blockers, and the node-scoped save. */
function NodeRecoveryStatus({
  recoveryId,
  blockedPath,
  scopedSave,
  scopedSaving,
}: NodeRecoveryStatusProps) {
  const documentSourceFile = useDocumentStatusStore((s) => s.sourceFile)
  const summary = useRecoverySummaryStore((s) =>
    recoveryId ? s.summaries[recoverySummaryKey(documentSourceFile, recoveryId)] : undefined,
  )
  const dismissSummary = useRecoverySummaryStore((s) => s.dismissSummary)
  const completeness = useDocumentStatusStore((s) => s.completeness)
  const entries = useMemo(
    () => (recoveryId ? completeness.filter((entry) => entry.element_id === recoveryId) : []),
    [completeness, recoveryId],
  )
  // What the recover could not fix. The document lists provider gaps itself;
  // an engine issue (a missing grid size, an invalid range) only reaches the
  // panel through the recover's own report.
  const unresolved = useMemo(
    () =>
      summary
        ? summary.completeness.filter(
            (entry) => !entries.some((known) => known.path === entry.path && known.code === entry.code),
          )
        : [],
    [entries, summary],
  )
  const saving = scopedSaving
  const [saveError, setSaveError] = useState<string | null>(null)
  if (!summary && entries.length === 0 && blockedPath === null && !scopedSave) return null
  const counts = summary
    ? {
        retained: summary.fieldChanges.filter((change) => change.outcome === "retained").length,
        defaulted: summary.fieldChanges.filter((change) => change.outcome === "defaulted").length,
        needsInput: summary.fieldChanges.filter((change) => change.outcome === "needs_input").length,
        removed: summary.fieldChanges.filter((change) => change.outcome === "removed").length,
      }
    : null
  return (
    <div
      data-testid="node-recovery-status"
      className="shrink-0 space-y-2 px-3 py-2 text-[11px]"
      style={{ borderBottom: "1px solid var(--border)" }}
    >
      {blockedPath !== null && (
        <p style={{ color: "var(--warning)" }}>
          Blocked by an unavailable upstream node
          {blockedPath.length > 0 ? `: ${blockedPath.join(" → ")}` : ""}. You can still edit
          and save this node.
        </p>
      )}
      {summary && counts && (
        <section
          aria-label="Recovery summary"
          className="rounded p-2"
          style={{ background: "var(--bg-elevated)" }}
        >
          <div className="flex items-start justify-between gap-2">
            <p style={{ color: "var(--text-primary)" }}>
              Recovered: {counts.retained} retained, {counts.defaulted} defaulted,{" "}
              {counts.needsInput} need input, {counts.removed} removed.
            </p>
            <button
              type="button"
              aria-label="Dismiss recovery summary"
              onClick={() => dismissSummary(summary.sourceFile, summary.recoveryId)}
              style={{ color: "var(--text-muted)" }}
            >
              <X size={12} />
            </button>
          </div>
          {unresolved.length > 0 && (
            <div className="mt-1" aria-label="Still to complete">
              <p style={{ color: "var(--warning)" }}>Still to complete:</p>
              <ul className="mt-0.5" style={{ color: "var(--text-secondary)" }}>
                {unresolved.map((entry) => (
                  <li key={`${entry.path}:${entry.code}`}>
                    <span className="font-mono">{entry.path}</span> - {entry.message}
                  </li>
                ))}
              </ul>
            </div>
          )}
          <details className="mt-1">
            <summary className="cursor-pointer" style={{ color: "var(--text-secondary)" }}>
              Field details
            </summary>
            <ul className="mt-1 space-y-0.5" style={{ color: "var(--text-secondary)" }}>
              {summary.fieldChanges.map((change, index) => (
                <li key={`${change.path}:${index}`}>
                  <span className="font-mono">{change.path || "settings"}</span>: {change.outcome}{" "}
                  - {change.reason}
                </li>
              ))}
            </ul>
          </details>
          {summary.previousConfig !== null && (
            <details className="mt-1">
              <summary className="cursor-pointer" style={{ color: "var(--text-secondary)" }}>
                Previous configuration
              </summary>
              <pre
                className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap rounded p-2 text-[10px]"
                style={{ background: "var(--bg-input)", color: "var(--text-secondary)" }}
              >
                {JSON.stringify(summary.previousConfig, null, 2)}
              </pre>
            </details>
          )}
          {summary.changes.some((change) => change.diff) && (
            <details className="mt-1">
              <summary className="cursor-pointer" style={{ color: "var(--text-secondary)" }}>
                Source diff
              </summary>
              {summary.changes.map((change) =>
                change.diff ? (
                  <pre
                    key={change.path}
                    className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap rounded p-2 text-[10px]"
                    style={{ background: "var(--bg-input)", color: "var(--text-secondary)" }}
                  >
                    {change.diff}
                  </pre>
                ) : null,
              )}
            </details>
          )}
        </section>
      )}
      {entries.length > 0 && (
        <section aria-label="Missing required values">
          <p style={{ color: "var(--warning)" }}>Missing required values:</p>
          <ul className="mt-0.5" style={{ color: "var(--text-secondary)" }}>
            {entries.map((entry) => (
              <li key={`${entry.path}:${entry.code}`}>
                <span className="font-mono">{entry.path}</span> - {entry.message}
              </li>
            ))}
          </ul>
        </section>
      )}
      {scopedSave && (
        <div className="flex items-center gap-2">
          <button
            type="button"
            data-testid="node-scoped-save"
            disabled={saving}
            onClick={() => {
              setSaveError(null)
              void scopedSave().then((result) => {
                if (!result.ok) setSaveError(result.error ?? "Save failed.")
              })
            }}
            className="rounded px-2 py-1 font-medium"
            style={{ background: "var(--accent)", color: "var(--text-on-accent)" }}
          >
            {saving ? "Saving…" : "Save node"}
          </button>
          <span style={{ color: "var(--text-muted)" }}>
            Saves only this node while the pipeline stays read-only.
          </span>
        </div>
      )}
      {saveError && (
        <p role="alert" style={{ color: "var(--danger)" }}>
          {saveError}
        </p>
      )}
    </div>
  )
}

type NodePanelHeaderProps = {
  nodeId: string
  label: string
  readOnly: boolean
  onRenameNode?: (nodeId: string, label: string) => Promise<OnUpdateConfigResult>
  onClose: () => void
}

function NodePanelHeader({
  nodeId,
  label,
  readOnly,
  onRenameNode,
  onClose,
}: NodePanelHeaderProps) {
  const rename = useNodeRenameSession(nodeId)
  return (
    <div className="px-3 py-2.5 shrink-0" style={{ borderBottom: "1px solid var(--border)" }}>
      <div className="flex items-center gap-2">
        <CommittedTextField
          data-testid="node-panel-label-input"
          type="text"
          value={label}
          disabled={readOnly || rename.pending}
          onCommit={(value) => rename.commit(value, onRenameNode)}
          className="node-label-input flex-1 min-w-0 px-2 py-1 text-[13px] font-semibold border border-transparent rounded-md focus:outline-none bg-transparent"
          style={{ color: "var(--text-primary)", borderColor: "transparent" }}
        />
        {readOnly && (
          <span
            data-testid="node-panel-readonly"
            className="inline-flex items-center gap-1 text-[10px] font-semibold uppercase tracking-[0.08em]"
            style={{ color: "var(--text-muted)" }}
          >
            <Lock size={11} aria-hidden="true" />Read-only
          </span>
        )}
        <SidePanelClose data-testid="node-panel-close" onClick={onClose} />
      </div>
      {rename.error && (
        <p
          role="alert"
          data-testid="node-panel-label-error"
          className="mt-1 px-2 text-[11px]"
          style={{ color: "var(--danger)" }}
        >
          {rename.error}
        </p>
      )}
    </div>
  )
}

type RecoveryNodePanelProps = {
  node: SimpleNode
  availability: Exclude<LoadAvailability, "ready">
  diagnostics: PipelineDiagnostic[]
  submodels: Record<string, unknown> | undefined
  canRepair: boolean
  onClose: () => void
  onRemoveUnavailableNode?: NodePanelProps["onRemoveUnavailableNode"]
}

type RecoveryNode = { type?: string | null; data: Record<string, unknown> }

function diagnosticsFor(data: HauteNodeData, diagnostics: PipelineDiagnostic[]): PipelineDiagnostic[] {
  const diagnosticIds = new Set(data._loadDiagnosticIds ?? [])
  return diagnostics.filter((diagnostic) => diagnosticIds.has(diagnostic.diagnostic_id))
}

/** Recover and Reset rebuild a known ordinary node: never a submodel or its port. */
function rebuildsSettings(node: RecoveryNode): boolean {
  const nodeType = effectiveNodeType(node)
  return Object.hasOwn(NODE_TYPE_META, nodeType)
    && nodeType !== NODE_TYPES.SUBMODEL
    && nodeType !== NODE_TYPES.SUBMODEL_PORT
}

/** Reset writes a new node's settings; a new Edge Join has no keys, and cannot be written. */
function resettable(node: RecoveryNode): boolean {
  return rebuildsSettings(node) && effectiveNodeType(node) !== NODE_TYPES.EDGE_JOIN
}

/** Diagnostics that rebuilding settings cannot mend: only removal is supported. */
function connectionsBroken(data: HauteNodeData, diagnostics: PipelineDiagnostic[]): boolean {
  return diagnosticsFor(data, diagnostics).some((diagnostic) => ["node_topology_invalid", "node_instance_removed"].includes(diagnostic.code))
}

/** An unavailable submodel's own unavailable nodes: the editor does not open its definition. */
function unavailableDefinitionNodes(
  node: SimpleNode,
  submodels: Record<string, unknown> | undefined,
): PipelineGraph["nodes"] {
  const config = (node.data as HauteNodeData).config
  if (effectiveNodeType(node) !== NODE_TYPES.SUBMODEL || !isSubmodelInstanceConfig(config)) return []
  const definition = submodels?.[config.definitionId]
  if (!isSubmodelDefinition(definition, config.definitionId)) return []
  return definition.graph.nodes.filter((inner) => (
    (inner.data as HauteNodeData)._loadAvailability === "unavailable"
  ))
}

type UnavailableDefinitionNodeProps = {
  node: PipelineGraph["nodes"][number]
  diagnostics: PipelineDiagnostic[]
  canRepair: boolean
  onRemoveUnavailableNode?: NodePanelProps["onRemoveUnavailableNode"]
}

/** One unavailable node of an unavailable submodel, recovered by its own identity in the submodel's file. */
function UnavailableDefinitionNode({
  node,
  diagnostics,
  canRepair,
  onRemoveUnavailableNode,
}: UnavailableDefinitionNodeProps) {
  const data = node.data as HauteNodeData
  const label = String(data.label)
  const repair = onRemoveUnavailableNode
  const target = canRepair
    && typeof data._sourceFile === "string"
    && typeof data._recoveryId === "string"
    ? { sourceFile: data._sourceFile, recoveryId: data._recoveryId }
    : null
  // An unknown decorator, an instance or a node with wrong connections has
  // nothing a rebuild can mend, but can still be removed.
  const rebuilds = target !== null && rebuildsSettings(node) && !connectionsBroken(data, diagnostics)
  return (
    <li
      className="rounded-lg p-3 text-[11px] leading-relaxed"
      style={{ background: "var(--danger-soft)", border: "1px solid var(--danger-border)" }}
    >
      <div className="font-semibold" style={{ color: "var(--text-primary)" }}>{label}</div>
      {diagnosticsFor(data, diagnostics).map((diagnostic) => (
        <div key={diagnostic.diagnostic_id} className="mt-1" style={{ color: "var(--danger-text)" }}>
          {diagnostic.message}
        </div>
      ))}
      {target && repair && (
        <div className="mt-2 flex gap-2">
          {rebuilds && (
            <>
              <button
                type="button"
                aria-label={`Recover settings for ${label}`}
                onClick={() => repair({ ...target, action: "recover" })}
                className="flex-1 rounded px-3 py-1.5 font-semibold"
                style={{ color: "var(--text-on-accent)", background: "var(--accent)" }}
              >
                Recover settings
              </button>
              {resettable(node) && (
                <button
                  type="button"
                  aria-label={`Reset node ${label}`}
                  onClick={() => repair({ ...target, action: "reset" })}
                  className="flex-1 rounded px-3 py-1.5 font-semibold"
                  style={{ color: "var(--text-on-accent)", background: "var(--accent)" }}
                >
                  Reset node
                </button>
              )}
            </>
          )}
          <button
            type="button"
            aria-label={`Remove node ${label}`}
            onClick={() => repair(target)}
            className="flex-1 rounded px-3 py-1.5 font-semibold"
            style={{
              color: "var(--danger-text)",
              background: "var(--danger-soft)",
              border: "1px solid var(--danger-border)",
            }}
          >
            Remove node
          </button>
        </div>
      )}
    </li>
  )
}

function RecoveryNodePanel({
  node,
  availability,
  diagnostics,
  submodels,
  canRepair,
  onClose,
  onRemoveUnavailableNode,
}: RecoveryNodePanelProps) {
  const recoveryData = node.data as HauteNodeData
  const nodeDiagnostics = diagnosticsFor(recoveryData, diagnostics)
  const sourceLocation = recoveryData._sourceFile
    ? `${recoveryData._sourceFile}${
      recoveryData._sourceSpan ? `:${recoveryData._sourceSpan.start_line}` : ""
    }`
    : null
  const hasRecoveryTarget = typeof recoveryData._sourceFile === "string"
    && typeof recoveryData._recoveryId === "string"
    && onRemoveUnavailableNode !== undefined
  const canRemove = availability === "unavailable"
    && canRepair
    && hasRecoveryTarget
  // Recover and Reset share eligibility (unavailable, known ordinary, owned,
  // connections sound), except that an Edge Join cannot be reset.
  const settingsMendable = canRemove && !connectionsBroken(recoveryData, diagnostics)
  const canRecover = settingsMendable && rebuildsSettings(node)
  const canReset = settingsMendable && resettable(node)
  const innerNodes = availability === "unavailable" ? unavailableDefinitionNodes(node, submodels) : []

  return (
    <PanelShell testId="node-panel">
      <div
        className="flex items-center gap-2 px-3 py-2.5"
        style={{ borderBottom: "1px solid var(--border)" }}
      >
        <AlertTriangle size={15} aria-hidden="true" style={{ color: "var(--danger)" }} />
        <div className="min-w-0 flex-1">
          <div className="truncate text-[13px] font-semibold" style={{ color: "var(--text-primary)" }}>
            {node.data.label}
          </div>
          <div className="text-[10px] uppercase tracking-[0.08em]" style={{ color: "var(--danger-text)" }}>
            {availability}
          </div>
        </div>
        <span
          data-testid="node-panel-readonly"
          className="inline-flex items-center gap-1 text-[10px] font-semibold uppercase tracking-[0.08em]"
          style={{ color: "var(--text-muted)" }}
        >
          <Lock size={11} aria-hidden="true" />Read-only
        </span>
        <SidePanelClose data-testid="node-panel-close" onClick={onClose} />
      </div>
      <div className="flex-1 space-y-4 overflow-y-auto p-4" data-testid="node-recovery-diagnostics">
        <p className="text-[12px] leading-relaxed" style={{ color: "var(--text-secondary)" }}>
          {availability === "unavailable"
            ? "This authored node could not be validated and cannot be edited or executed."
            : "This node is valid, but an unavailable upstream dependency prevents it from running."}
        </p>
        {(sourceLocation || recoveryData._configReference) && (
          <dl className="space-y-2 text-[11px]">
            {sourceLocation && (
              <div>
                <dt style={{ color: "var(--text-muted)" }}>Source</dt>
                <dd className="mt-0.5 break-all font-mono" style={{ color: "var(--text-primary)" }}>
                  {sourceLocation}
                </dd>
              </div>
            )}
            {recoveryData._configReference && (
              <div>
                <dt style={{ color: "var(--text-muted)" }}>Configuration</dt>
                <dd className="mt-0.5 break-all font-mono" style={{ color: "var(--text-primary)" }}>
                  {recoveryData._configReference}
                </dd>
              </div>
            )}
          </dl>
        )}
        {recoveryData._loadBlockingPath && recoveryData._loadBlockingPath.length > 0 && (
          <div>
            <div className="text-[10px] uppercase tracking-[0.08em]" style={{ color: "var(--text-muted)" }}>
              Blocking path
            </div>
            <div className="mt-1 break-words font-mono text-[11px]" style={{ color: "var(--warning)" }}>
              {recoveryData._loadBlockingPath.join(" → ")}
            </div>
          </div>
        )}
        <section aria-label="Node load diagnostics">
          <h2 className="text-[10px] font-semibold uppercase tracking-[0.08em]" style={{ color: "var(--text-muted)" }}>
            Diagnostics
          </h2>
          {nodeDiagnostics.length === 0 ? (
            <p className="mt-2 text-[11px]" style={{ color: "var(--text-secondary)" }}>
              No detailed diagnostic was included for this element.
            </p>
          ) : (
            <ul className="mt-2 space-y-3">
              {nodeDiagnostics.map((diagnostic) => (
                <li
                  key={diagnostic.diagnostic_id}
                  className="rounded-lg p-3 text-[11px] leading-relaxed"
                  style={{ background: "var(--danger-soft)", border: "1px solid var(--danger-border)" }}
                >
                  <div style={{ color: "var(--danger-text)" }}>{diagnostic.message}</div>
                  {diagnostic.remediation && (
                    <div className="mt-1" style={{ color: "var(--text-secondary)" }}>
                      {diagnostic.remediation}
                    </div>
                  )}
                  {diagnostic.incident_id && (
                    <div className="mt-1 font-mono text-[10px]" style={{ color: "var(--text-muted)" }}>
                      Incident {diagnostic.incident_id}
                    </div>
                  )}
                </li>
              ))}
            </ul>
          )}
        </section>
        {innerNodes.length > 0 && (
          <section aria-label="Unavailable nodes in this submodel">
            <h2 className="text-[10px] font-semibold uppercase tracking-[0.08em]" style={{ color: "var(--text-muted)" }}>
              Unavailable nodes in this submodel
            </h2>
            <ul className="mt-2 space-y-3">
              {innerNodes.map((inner) => (
                <UnavailableDefinitionNode
                  key={inner.id}
                  node={inner}
                  diagnostics={diagnostics}
                  canRepair={canRepair}
                  onRemoveUnavailableNode={onRemoveUnavailableNode}
                />
              ))}
            </ul>
          </section>
        )}
        {(canRemove || canRecover) && (
          <div className="space-y-2">
            {canRecover && (
              <button
                type="button"
                onClick={() => onRemoveUnavailableNode({
                  sourceFile: recoveryData._sourceFile!,
                  recoveryId: recoveryData._recoveryId!,
                  action: "recover",
                })}
                className="w-full rounded px-3 py-2 text-[12px] font-semibold"
                style={{ color: "var(--text-on-accent)", background: "var(--accent)" }}
              >
                Recover settings
              </button>
            )}
            {canReset && (
              <button
                type="button"
                onClick={() => onRemoveUnavailableNode({
                  sourceFile: recoveryData._sourceFile!,
                  recoveryId: recoveryData._recoveryId!,
                  action: "reset",
                })}
                className="w-full rounded px-3 py-2 text-[12px] font-semibold"
                style={{ color: "var(--text-on-accent)", background: "var(--accent)" }}
              >
                Reset node
              </button>
            )}
            {canRemove && <button
              type="button"
              onClick={() => onRemoveUnavailableNode({
                sourceFile: recoveryData._sourceFile!,
                recoveryId: recoveryData._recoveryId!,
              })}
              className="w-full rounded px-3 py-2 text-[12px] font-semibold"
              style={{ color: "var(--danger-text)", background: "var(--danger-soft)", border: "1px solid var(--danger-border)" }}
            >
              Remove unavailable node
            </button>}
          </div>
        )}
      </div>
    </PanelShell>
  )
}

type NodeEditorTabStripProps = {
  visible: boolean
  tabs: NodePanelTab[]
  activeTab: NodePanelTab
  onSelect: (tab: NodePanelTab) => void
  /** What the config tab is called for this node; a Transform's config is its Polars steps or code. */
  configLabel?: string
}

function NodeEditorTabStrip({ visible, tabs, activeTab, onSelect, configLabel = "config" }: NodeEditorTabStripProps) {
  if (!visible) return null
  return (
    <div className="flex shrink-0" style={{ borderBottom: "1px solid var(--border)" }}>
      {tabs.map((tab) => {
        const isActive = activeTab === tab
        return (
          <button
            key={tab}
            onClick={() => onSelect(tab)}
            className={`flex-1 px-3 py-1.5 text-[11px] font-semibold uppercase tracking-[0.08em] transition-colors${
              isActive ? "" : " hover:bg-[var(--bg-hover)]"
            }`}
            style={isActive
              ? {
                  color: "var(--accent)",
                  borderBottom: "2px solid var(--accent)",
                  background: "var(--accent-soft)",
                }
              : {
                  color: "var(--text-muted)",
                  borderBottom: "2px solid transparent",
                }}
          >
            {tab === "polars" ? "Transform" : tab === "config" ? configLabel : tab}
          </button>
        )
      })}
    </div>
  )
}

function NodeNotes({ notes }: { notes: string[] }) {
  if (notes.length === 0) return null
  return (
    <div className="px-4 py-2 shrink-0" style={{ borderBottom: "1px solid var(--border)" }}>
      <div
        data-testid="node-notes"
        className="flex flex-col gap-1 px-3 py-2 rounded-lg"
        style={{ background: "var(--accent-soft-subtle)", border: "1px solid var(--border)" }}
      >
        {notes.map((note) => (
          <p key={note} className="text-[11px] leading-relaxed" style={{ color: "var(--text-secondary)" }}>
            {note}
          </p>
        ))}
      </div>
    </div>
  )
}

type SchemaWarningBannerProps = {
  warnings: { column: string; status: string }[]
  dismissedSignature: string | null
  onDismiss: (signature: string) => void
  onRefreshPreview?: () => void
}

function SchemaWarningBanner({
  warnings,
  dismissedSignature,
  onDismiss,
  onRefreshPreview,
}: SchemaWarningBannerProps) {
  if (warnings.length === 0) return null
  const signature = warnings.map((warning) => `${warning.column}|${warning.status}`).join(",")
  if (signature === dismissedSignature) return null

  return (
    <div className="px-4 py-2 shrink-0" style={{ borderBottom: "1px solid var(--border)" }}>
      <div className="flex flex-col gap-1.5 px-3 py-2 rounded-lg" style={{ background: "var(--warning-soft)", border: "1px solid var(--warning-border)" }}>
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-1.5">
            <AlertTriangle size={11} style={{ color: "var(--warning-strong)" }} className="shrink-0" />
            <span className="text-[11px] font-bold uppercase tracking-[0.08em]" style={{ color: "var(--warning-strong)" }}>
              Stale columns ({warnings.length})
            </span>
          </div>
          <div className="flex items-center gap-1.5 shrink-0">
            <button
              onClick={() => {
                onDismiss(signature)
                onRefreshPreview?.()
              }}
              className="px-2 py-1 rounded shrink-0 transition-opacity flex items-center gap-1 text-[11px] font-medium hover:opacity-[0.85]"
              style={{ background: "var(--accent)", color: "var(--text-on-accent)" }}
              title="Re-run preview and re-check schema warnings"
            >
              <RefreshCw size={11} />
              Refresh and check
            </button>
            <button
              onClick={() => onDismiss(signature)}
              className="p-1 rounded shrink-0 transition-colors hover:opacity-[0.85]"
              style={{ color: "var(--warning-strong)" }}
              title="Dismiss"
              aria-label="Dismiss"
            >
              <X size={12} strokeWidth={2.5} />
            </button>
          </div>
        </div>
        <p className="text-[10px] leading-relaxed" style={{ color: "var(--text-muted)" }}>
          These columns are referenced in config but not found in the upstream schema:
        </p>
        <div className="flex flex-wrap gap-1 mt-0.5">
          {warnings.map((warning) => (
            <span key={warning.column} className="px-1.5 py-0.5 rounded text-[10px] font-mono" style={{ background: "var(--warning-soft-emphasis)", color: "var(--warning)" }}>
              {warning.column}
            </span>
          ))}
        </div>
      </div>
    </div>
  )
}

type NodeEditorBodyProps = {
  nodeId: string
  documentReadOnly: boolean
  readOnly: boolean
  config: Record<string, unknown>
  activeTab: NodePanelTab
  showPolarsTab: boolean
  showColumnsTab: boolean
  nodeType: string
  inputSources: InputSource[]
  onDeleteEdge?: (edgeId: string) => void
  errorLine?: number | null
  /** The last run's error message for this node, if it failed. */
  runError?: string | null
  upstreamColumns: { name: string; dtype: string }[]
  /** The node's own recorded columns, which the Transform tab offers after its input columns. */
  nodeColumns: { name: string; dtype: string }[] | undefined
  availableColumns: { name: string; dtype: string }[]
  currentColumns: { name: string; dtype: string }[]
  onUpdateConfig: OnUpdateConfig
  onReplaceConfig: OnReplaceConfig
  configEditor: React.ReactNode
}

function NodeEditorBody({
  nodeId,
  documentReadOnly,
  readOnly,
  config,
  activeTab,
  showPolarsTab,
  showColumnsTab,
  nodeType,
  inputSources,
  onDeleteEdge,
  errorLine,
  runError,
  upstreamColumns,
  nodeColumns,
  availableColumns,
  currentColumns,
  onUpdateConfig,
  onReplaceConfig,
  configEditor,
}: NodeEditorBodyProps) {
  let editor = configEditor
  if (activeTab === "polars" && showPolarsTab) {
    const chips = nodeType === NODE_TYPES.DATA_INPUT ? [] : inputSources
    // Every Polars-tab surface authors steps: the step builder while
    // `config.steps` is a list, rendering against the surface's eligible
    // input names (never the chips); `stepInputNames` refuses a type outside
    // the surface table rather than falling back to a plain code box.
    const surface = steppedSurfaceFor(nodeType)
    if (surface === undefined) throw new Error(`Transform tab on ${nodeType}, which does not author steps.`)
    editor = (
      <SteppedCodePane
        config={config}
        onUpdate={onUpdateConfig}
        onReplaceConfig={onReplaceConfig}
        inputSources={chips}
        inputNames={stepInputNames(nodeType, inputSources.map((source) => source.name))}
        onDeleteInput={onDeleteEdge}
        errorLine={errorLine}
        runError={runError}
        upstreamColumns={upstreamColumns}
        nodeColumns={nodeColumns}
        start={surface.start}
        nodeId={nodeId}
        codeHint={POLARS_TAB_HINTS[nodeType] ?? null}
      />
    )
  } else if (activeTab === "columns" && showColumnsTab) {
    editor = (
      <ColumnsTab
        config={config}
        onUpdate={onUpdateConfig}
        availableColumns={availableColumns}
        columns={currentColumns}
      />
    )
  }

  return (
    <div
      className="flex-1 min-h-0 overflow-y-auto"
      data-testid="node-panel-editor"
      inert={readOnly ? true : undefined}
      aria-readonly={readOnly}
    >
      {documentReadOnly ? (
        <div className="space-y-3 p-3" data-testid="node-document-readonly-inspector">
          <p className="text-[11px] leading-relaxed" style={{ color: "var(--text-secondary)" }}>
            This node is valid and available for inspection. Resolve the pipeline load diagnostics before editing or running it.
          </p>
          <pre
            aria-label="Read-only node configuration"
            className="overflow-auto whitespace-pre-wrap break-words rounded-md p-2 text-[10px]"
            style={{ background: "var(--bg-input)", color: "var(--text-secondary)" }}
          >
            {JSON.stringify(config, null, 2)}
          </pre>
        </div>
      ) : (
        <LazyEditorBoundary>{editor}</LazyEditorBoundary>
      )}
    </div>
  )
}

// ─── NodePanel ────────────────────────────────────────────────────

type ActiveNodePanelProps = Omit<NodePanelProps, "node"> & { node: SimpleNode }

export default function NodePanel(props: NodePanelProps) {
  if (!props.node) return null
  return <NodePanelContent key={props.node.id} {...props} node={props.node} />
}

function NodePanelContent({
  node,
  onClose,
  onUpdateNode,
  onRenameNode,
  onDeleteEdge,
  onDeleteSubmodelInputPort,
  onSwapEdgeJoinInputs,
  onRefreshPreview,
  dimmed,
  errorLine,
  runError,
  previewRows,
  selectedPreviewLoading = false,
  readOnly = false,
  documentReadOnly = false,
  insideSubmodel = false,
  scopedSave,
  onRemoveUnavailableNode,
}: ActiveNodePanelProps) {
  const [scopedSaving, setScopedSaving] = useState(false)
  // The editor freezes while a scoped save is in flight so keystrokes cannot
  // race the adoption of the authoritative response.
  const runScopedSave = useMemo(
    () =>
      scopedSave
        ? async () => {
            setScopedSaving(true)
            try {
              return await scopedSave()
            } finally {
              setScopedSaving(false)
            }
          }
        : undefined,
    [scopedSave],
  )
  const { allNodes, edges, submodels, preamble } = useGraph()
  const config = useMemo(() => (node.data.config || {}) as Record<string, unknown>, [node.data.config])
  const {
    activeTab,
    selectTab,
    dismissedWarningSignature,
    dismissWarning,
  } = useNodePanelSession()
  const rememberedExplorePane = useUIStore((s) => s.explorePanes[node.id])
  const setExplorePane = useUIStore((s) => s.setExplorePane)
  const rememberedModellingPane = useUIStore((s) => s.modellingPanes[node.id])
  // The lazily loaded modelling editor owns issue derivation; the panel only badges tabs.
  const [modellingPaneIssues, setModellingPaneIssues] = useState<{ nodeId: string; panes: readonly ModellingPane[] }>({ nodeId: "", panes: [] })
  const onModellingPaneIssuesChange = useCallback((nodeId: string, panes: readonly ModellingPane[]) => {
    setModellingPaneIssues({ nodeId, panes })
  }, [])
  const setModellingPane = useUIStore((s) => s.setModellingPane)
  const hasActiveTrainJob = useNodeResultsStore((s) => Boolean(s.trainJobs[node.id]))
  const rememberedOptimiserPane = useUIStore((s) => s.optimiserPanes[node.id])
  const setOptimiserPane = useUIStore((s) => s.setOptimiserPane)
  const hasActiveSolveJob = useNodeResultsStore((s) => Boolean(s.solveJobs[node.id]))
  // The lazily loaded optimiser editor owns its Solve issues; the panel only badges tabs.
  const [optimiserPaneIssues, setOptimiserPaneIssues] = useState<{ nodeId: string; panes: readonly OptimiserPane[] }>({ nodeId: "", panes: [] })
  const onOptimiserPaneIssuesChange = useCallback((nodeId: string, panes: readonly OptimiserPane[]) => {
    setOptimiserPaneIssues({ nodeId, panes })
  }, [])
  const activeSource = useSettingsStore((s) => s.activeSource)
  const documentDiagnostics = useDocumentStatusStore((s) => s.diagnostics)
  const canRepair = useDocumentStatusStore((s) => s.capabilities?.can_repair === true)
  const reservedApiInputFrameLabels = useDocumentStatusStore(
    (s) => s.capabilities?.reserved_api_input_frame_labels,
  )
  const reservedApiInputFrameLabelSet = useMemo(
    () => new Set(reservedApiInputFrameLabels ?? []),
    [reservedApiInputFrameLabels],
  )

  // Current Explore cache identity hash — the same client identity gate the
  // Explore preview applies, so a Chart Configure subview never treats a
  // retained result from a superseded identity as current, and the member
  // picker never renders members from a superseded identity.
  const exploreConfigHash = useMemo(() => {
    if (!node || effectiveNodeType(node) !== NODE_TYPES.EXPLORE) return null
    const identity = buildNodeDataCacheIdentity({ node, allNodes, edges, submodels, preamble })
    return hashConfig({ graph: identity, source: activeSource })
  }, [node, allNodes, edges, submodels, preamble, activeSource])

  // The columns of the data this node reads, when another consumer of the
  // point has already established them; never a request of its own. Gated on
  // the identity above, so the previous point's columns are never offered
  // after a source switch or a rewiring.
  const exploreProfile = useNodeDataStore((s) =>
    profileForConsumer(s, node.id, exploreConfigHash),
  )

  const loadPivotFilterMembers = useCallback(
    (field: string, search: string, signal: AbortSignal) => {
      if (!node) throw new Error("Explore node is unavailable.")
      return fetchExplorePivotMembers({
        graph: buildGraph(allNodes, edges, submodels),
        node_id: node.id,
        field,
        source: activeSource,
        search: search || undefined,
        signal,
      })
    },
    // Keyed by the Explore cache identity hash: any render that keeps the
    // same hash captures a graph snapshot whose data-affecting parts are
    // identical, so display-only pivot edits do not churn the loader or
    // reload members, while a hash change rebuilds the closure with the new
    // graph/source in the same render that re-keys the member picker — there
    // is no ref-update ordering to race against.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [exploreConfigHash],
  )

  const effectiveReadOnly = readOnly || scopedSaving
  const handleConfigUpdate = useCallback<OnUpdateConfig>((keyOrUpdates, value) => {
    if (readOnly || scopedSaving) {
      return {
        ok: false,
        error: documentReadOnly
          ? documentReadOnlyReason()
          : "This submodel instance is read-only.",
      }
    }
    if (!onUpdateNode) {
      return { ok: false, error: "Node update handler is unavailable." }
    }
    const newConfig = typeof keyOrUpdates === "string"
      ? { ...config, [keyOrUpdates]: value }
      : { ...config, ...keyOrUpdates }
    const changedKeys =
      typeof keyOrUpdates === "string"
        ? [keyOrUpdates]
        : Object.keys(keyOrUpdates)
    const selectionOnlyUpdate =
      changedKeys.length === 1 && changedKeys[0] === "selected_columns"
    return onUpdateNode(
      node.id,
      clearCachedResultShape(
        { ...node.data, config: newConfig },
        { preserveAvailableColumns: selectionOnlyUpdate },
      ),
    )
  }, [config, documentReadOnly, node, onUpdateNode, readOnly, scopedSaving])

  const handleConfigReplace = useCallback<OnReplaceConfig>((nextConfig) => {
    if (readOnly || scopedSaving) {
      return {
        ok: false,
        error: documentReadOnly
          ? documentReadOnlyReason()
          : "This submodel instance is read-only.",
      }
    }
    if (!onUpdateNode) return { ok: false, error: "Node update handler is unavailable." }
    return onUpdateNode(node.id, clearCachedResultShape({ ...node.data, config: nextConfig }))
  }, [documentReadOnly, node, onUpdateNode, readOnly, scopedSaving])

  // Export's "Use in Apply node" points another node at a saved artifact, under
  // the same read-only guards as this node's own edits.
  const handleOtherNodeConfigUpdate = useCallback((targetId: string, patch: Record<string, unknown>): OnUpdateConfigResult => {
    if (readOnly || scopedSaving) {
      return {
        ok: false,
        error: documentReadOnly ? documentReadOnlyReason() : "This submodel instance is read-only.",
      }
    }
    if (!onUpdateNode) return { ok: false, error: "Node update handler is unavailable." }
    const target = allNodes.find((candidate) => candidate.id === targetId)
    if (!target) return { ok: false, error: `Cannot update missing node "${targetId}".` }
    const targetConfig = (target.data.config ?? {}) as Record<string, unknown>
    return onUpdateNode(targetId, clearCachedResultShape({ ...target.data, config: { ...targetConfig, ...patch } }))
  }, [allNodes, documentReadOnly, onUpdateNode, readOnly, scopedSaving])

  const configWithNodeId = useMemo(
    () => ({ ...config, _nodeId: node.id }),
    [config, node.id]
  )

  // Compute input sources (must be before early return to satisfy hook ordering rules)
  const nodeMap = useMemo(() => Object.fromEntries(allNodes.map((n) => [n.id, n])), [allNodes])
  const selectedNodeId = node.id
  const upstreamEdges = useMemo(
    () => edges.filter((e) => e.target === selectedNodeId),
    [edges, selectedNodeId],
  )
  const upstreamInputSourceSig = upstreamInputSourceSignature(upstreamEdges, nodeMap, submodels)
  const inputSources: InputSource[] = useMemo(() => {
    return upstreamEdges.map((edge) => inputSourceForEdge(edge, nodeMap, submodels))
    // Keyed by selected node plus each upstream edge's source/display identity:
    // source labels, source handles, resolved frame labels, and resolution
    // state can all change while the edge id stays the same.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedNodeId, upstreamInputSourceSig, submodels])
  const upstreamSchemaSignature = upstreamColumnsSignature(upstreamEdges, nodeMap)
  const upstreamColumns = useMemo(() => {
    return collectColumnsFromEdges(upstreamEdges, nodeMap)
    // Intentionally keyed by selected node plus upstream schema content.
    // Selected-node config/label edits rebuild nodeMap but do not change the
    // upstream schema, so they should preserve this array identity.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedNodeId, upstreamSchemaSignature])
  const nodeColumns = useRecordedNodeColumns(node.data as HauteNodeData, activeSource)
  const pivotColumns = useMemo(
    () =>
      exploreProfile
        ? exploreProfile.profile.columns.map(({ name, dtype }) => ({ name, dtype }))
        : upstreamColumns,
    [exploreProfile, upstreamColumns],
  )
  const recoveryAvailability = (node.data as HauteNodeData)._loadAvailability ?? "ready"
  const scopedEditable = (node.data as HauteNodeData)._scopedEditable === true
  if (
    recoveryAvailability === "unavailable" ||
    (recoveryAvailability === "blocked" && !(scopedEditable && scopedSave))
  ) {
    return (
      <RecoveryNodePanel
        node={node}
        availability={recoveryAvailability}
        diagnostics={documentDiagnostics}
        submodels={submodels}
        canRepair={canRepair}
        onClose={onClose}
        onRemoveUnavailableNode={onRemoveUnavailableNode}
      />
    )
  }

  const nodeType = effectiveNodeType(node)
  const isKnownNodeType = Object.hasOwn(NODE_TYPE_META, nodeType)
  const showColumnsTab = isKnownNodeType && !NO_COLUMNS_TAB.has(nodeType)
  const showPolarsTab = isKnownNodeType && POLARS_TAB_TYPES.has(nodeType)
  const showExplorePanes = isKnownNodeType && nodeType === NODE_TYPES.EXPLORE
  const activeExplorePane = showExplorePanes ? rememberedExplorePane ?? "code" : "code"
  const algorithm = typeof config.algorithm === "string" ? config.algorithm.toLowerCase() : ""
  const modellingPanes = modellingPanesFor(algorithm)
  const showModellingPanes = isKnownNodeType && nodeType === NODE_TYPES.MODELLING && modellingPanes.length > 0
  const activeModellingPane = showModellingPanes ? resolveModellingPane(algorithm, rememberedModellingPane) : "target"
  const flaggedModellingPanes = showModellingPanes && modellingPaneIssues.nodeId === node.id
    ? modellingPaneIssues.panes
    : []
  const modellingTabs = modellingPanes.map((pane) => ({
    ...pane,
    indicator: pane.key === "train" && hasActiveTrainJob
      ? { kind: "active" as const, label: "Training is running" }
      : flaggedModellingPanes.includes(pane.key)
        ? { kind: "warning" as const, label: `${pane.label} needs attention`, compact: true }
        : undefined,
  }))
  const showOptimiserPanes = isKnownNodeType && nodeType === NODE_TYPES.OPTIMISER
  const activeOptimiserPane = resolveOptimiserPane(showOptimiserPanes ? rememberedOptimiserPane : undefined)
  const flaggedOptimiserPanes = showOptimiserPanes && optimiserPaneIssues.nodeId === node.id
    ? optimiserPaneIssues.panes
    : []
  const optimiserTabs = OPTIMISER_PANES.map((pane) => ({
    ...pane,
    indicator: pane.key === "solve" && hasActiveSolveJob
      ? { kind: "active" as const, label: "Solve is running" }
      : flaggedOptimiserPanes.includes(pane.key)
        ? { kind: "warning" as const, label: `${pane.label} needs attention`, compact: true }
        : undefined,
  }))

  const accentColor = NODE_TYPE_META[nodeType as NodeTypeValue]?.color ?? "var(--accent)"
  const configEditor = !isKnownNodeType ? (
    <UnknownNodeTypeDiagnostic nodeType={nodeType} config={config} />
  ) : (
    <NodeConfigEditor
      nodeType={nodeType as NodeTypeValue}
      readOnly={nodeType === NODE_TYPES.SUBMODEL && (effectiveReadOnly || documentReadOnly)}
      config={config}
      configWithNodeId={configWithNodeId}
      node={node}
      onUpdateConfig={handleConfigUpdate}
      onReplaceConfig={handleConfigReplace}
      inputSources={inputSources}
      upstreamColumns={upstreamColumns}
      nodeColumns={nodeColumns}
      pivotColumns={pivotColumns}
      activeExplorePane={activeExplorePane}
      activeModellingPane={activeModellingPane}
      activeOptimiserPane={activeOptimiserPane}
      onOptimiserPaneIssuesChange={onOptimiserPaneIssuesChange}
      onUpdateNodeConfig={handleOtherNodeConfigUpdate}
      onModellingPaneIssuesChange={onModellingPaneIssuesChange}
      onDeleteEdge={onDeleteEdge}
      onDeleteSubmodelInputPort={
        readOnly || documentReadOnly ? undefined : onDeleteSubmodelInputPort
      }
      onSwapEdgeJoinInputs={onSwapEdgeJoinInputs}
      onShowPivots={() => setExplorePane(node.id, "pivots")}
      errorLine={errorLine}
      runError={runError}
      previewRows={previewRows}
      selectedPreviewLoading={selectedPreviewLoading}
      loadPivotFilterMembers={loadPivotFilterMembers}
      exploreConfigHash={exploreConfigHash}
      reservedApiInputFrameLabels={reservedApiInputFrameLabelSet}
      accentColor={accentColor}
      insideSubmodel={insideSubmodel}
    />
  )

  const availableColumns = ((node.data as Record<string, unknown>)?._availableColumns as { name: string; dtype: string }[]) || []
  const currentColumns = ((node.data as Record<string, unknown>)?._columns as { name: string; dtype: string }[]) || []
  const schemaWarnings = (node.data._schemaWarnings as { column: string; status: string }[]) || []
  const editorTabs: NodePanelTab[] = [
    "config",
    ...(showPolarsTab ? ["polars" as const] : []),
    ...(showColumnsTab ? ["columns" as const] : []),
  ]

  return (
    <PanelShell testId="node-panel" style={{ opacity: dimmed ? 0.6 : 1, transition: 'opacity 150ms' }}>
      <NodePanelHeader
        key={String(node.data.label)}
        nodeId={node.id}
        label={String(node.data.label)}
        readOnly={readOnly || Boolean(scopedSave)}
        onRenameNode={onRenameNode}
        onClose={onClose}
      />

      <NodeRecoveryStatus
        recoveryId={(node.data as HauteNodeData)._recoveryId ?? null}
        blockedPath={
          recoveryAvailability === "blocked"
            ? ((node.data as HauteNodeData)._loadBlockingPath ?? [])
            : null
        }
        scopedSave={runScopedSave}
        scopedSaving={scopedSaving}
      />

      <NodeEditorTabStrip
        visible={showColumnsTab || showPolarsTab}
        tabs={editorTabs}
        activeTab={activeTab}
        onSelect={selectTab}
        configLabel={nodeType === NODE_TYPES.POLARS ? "Transform" : undefined}
      />

      {showExplorePanes && (
        <PreviewPanelTabs
          tabs={EXPLORE_PANES}
          activeTab={activeExplorePane}
          onChange={(pane) => setExplorePane(node.id, pane)}
          ariaLabel="Explore panes"
          accentColor={accentColor}
          equalWidth
          idPrefix="explore"
        />
      )}
      {showModellingPanes && (
        <PreviewPanelTabs
          tabs={modellingTabs}
          activeTab={activeModellingPane}
          onChange={(pane) => setModellingPane(node.id, pane)}
          ariaLabel="Modelling panes"
          accentColor={accentColor}
          equalWidth
          idPrefix="modelling"
        />
      )}
      {showOptimiserPanes && (
        <PreviewPanelTabs
          tabs={optimiserTabs}
          activeTab={activeOptimiserPane}
          onChange={(pane) => setOptimiserPane(node.id, pane)}
          ariaLabel="Optimiser panes"
          accentColor={accentColor}
          equalWidth
          idPrefix="optimiser"
        />
      )}

      {!showExplorePanes && (
        <SchemaWarningBanner
          warnings={schemaWarnings}
          dismissedSignature={dismissedWarningSignature}
          onDismiss={dismissWarning}
          onRefreshPreview={onRefreshPreview}
        />
      )}

      {!showExplorePanes && <NodeNotes notes={(node.data._notes as string[] | undefined) ?? []} />}

      <NodeEditorBody
        nodeId={node.id}
        documentReadOnly={documentReadOnly}
        readOnly={effectiveReadOnly}
        config={config}
        activeTab={activeTab}
        showPolarsTab={showPolarsTab}
        showColumnsTab={showColumnsTab}
        nodeType={nodeType}
        inputSources={inputSources}
        onDeleteEdge={onDeleteEdge}
        errorLine={errorLine}
        runError={runError}
        upstreamColumns={upstreamColumns}
        nodeColumns={nodeColumns}
        availableColumns={availableColumns}
        currentColumns={currentColumns}
        onUpdateConfig={handleConfigUpdate}
        onReplaceConfig={handleConfigReplace}
        configEditor={configEditor}
      />
    </PanelShell>
  )
}
