import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { X, Link2, AlertTriangle, RefreshCw } from "lucide-react"
import { NODE_TYPES, NODE_TYPE_META } from "../utils/nodeTypes"
import type { NodeTypeValue } from "../utils/nodeTypes"
import { sanitizeName } from "../utils/sanitizeName"
import { withAlpha } from "../utils/color"
import {
  DataSourceEditor,
  TransformEditor,
  EdgeJoinEditor,
  ExploreCodeEditor,
  ExploreOverviewConfig,
  ModelScoreEditor,
  BandingEditor,
  RatingStepEditor,
  OutputEditor,
  ExternalFileEditor,
  ApiInputEditor,
  LiveSwitchEditor,
  SinkEditor,
  ScenarioExpanderEditor,
  OptimiserApplyEditor,
  ConstantEditor,
  SubmodelEditor,
  ColumnsTab,
  GroupedColumnsTab,
  ModellingConfig,
  OptimiserConfig,
  LazyEditorBoundary,
} from "./LazyNodeEditors"
import type { InputSource, SimpleNode, SimpleEdge } from "./editors"
import { effectiveNodeType, type HauteNodeData } from "../types/node"
import useUIStore, { type ExplorePane } from "../stores/useUIStore"
import PanelShell from "./PanelShell"
import PreviewPanelTabs from "./PreviewPanelTabs"
import Tooltip from "../components/Tooltip"
import NodeTypeTooltip from "../components/NodeTypeTooltip"
import { ErrorBoundary } from "../components/ErrorBoundary"
import { useGraph } from "./useGraph"

// Re-export types (preserve public API for App.tsx)
export type { SimpleNode, SimpleEdge } from "./editors"

type NodePanelProps = {
  node: SimpleNode | null
  onClose: () => void
  onUpdateNode?: (id: string, data: Record<string, unknown>) => void
  onDeleteEdge?: (edgeId: string) => void
  /** Set (or clear, when alias is null) a connection's input binding alias. */
  onSetInputAlias?: (edgeId: string, alias: string | null) => void
  onSwapEdgeJoinInputs?: (nodeId: string) => void
  onRefreshPreview?: () => void
  /** True when showing last-selected node while nothing is actively selected */
  dimmed?: boolean
  /** 1-based line number of the error in user code, if any */
  errorLine?: number | null
  /** Preview rows from the current node's preview data (input columns pass through) */
  previewRows?: Record<string, unknown>[]
  /** True while the selected node preview request is still in flight. */
  selectedPreviewLoading?: boolean
}

// ─── Node types that do NOT show the Columns tab ──
// Output already has its own field selection; submodels/ports are placeholders;
// modelling and explore nodes are sink-only (no outputs).
//
// Bundle 3a — API_INPUT was added here as part of the v2-consolidation
// decision. The v2-native column-filter surface is the per-column
// `selected: bool` inside `tables[].columns[]` (in the Schema panel).
// The legacy `Columns` tab wrote the universal-but-apiInput-illegitimate
// keys `selected_columns` / `column_renames` via `GroupedColumnsTab`
// and let the user double-author the same intent. Removing the tab
// here removes the only UI write path for those keys on apiInput;
// any residual values on disk are stripped at load time by Bundle 2.a
// (`_normalise_loaded_config` in `src/haute/_config_io.py`) and at
// write time by Bundle 2.α. Contract pinning test:
// `src/__tests__/editors/apiInputBundle3aContract.test.tsx`.
const NO_COLUMNS_TAB = new Set<string>([
  NODE_TYPES.API_INPUT,
  NODE_TYPES.OUTPUT,
  NODE_TYPES.SUBMODEL,
  NODE_TYPES.SUBMODEL_PORT,
  NODE_TYPES.MODELLING,
  NODE_TYPES.EXPLORE,
])

const NO_REFRESH_PREVIEW = new Set<string>([
  NODE_TYPES.SUBMODEL,
  NODE_TYPES.SUBMODEL_PORT,
])

// Right-panel panes for Explore nodes. Code prepares the analysis dataset;
// the remaining panes are empty scaffolding for upcoming EDA work.
const EXPLORE_PANES = [
  { key: "code", label: "Polars Code" },
  { key: "overview", label: "Overview" },
  { key: "relationships", label: "Relationships" },
  { key: "charts", label: "Charts" },
  { key: "export", label: "Export" },
] as const satisfies readonly { key: ExplorePane; label: string }[]

// ─── Instance sub-panel (kept inline — it references multiple node-level concerns) ──

type InstanceOriginalResolution =
  | {
      status: "found"
      original: SimpleNode
      originalNodeMap: Record<string, SimpleNode>
      originalEdges: SimpleEdge[]
      submodelName?: string
    }
  | { status: "invalid"; rawInstanceOf: unknown }
  | { status: "missing"; originalId: string }
  | { status: "ambiguous"; originalId: string; locations: string[] }
  | { status: "malformedSubmodel"; originalId: string; submodelName: string; reason: string }

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
}

function isSimpleNodeArray(value: unknown): value is SimpleNode[] {
  return Array.isArray(value) && value.every((item) => (
    isRecord(item) &&
    typeof item.id === "string" &&
    isRecord(item.data) &&
    typeof item.data.label === "string"
  ))
}

function isSimpleEdgeArray(value: unknown): value is SimpleEdge[] {
  return Array.isArray(value) && value.every((item) => (
    isRecord(item) &&
    typeof item.id === "string" &&
    typeof item.source === "string" &&
    typeof item.target === "string"
  ))
}

function submodelGraphFromMetadata(
  submodelName: string,
  metadata: unknown,
):
  | { status: "ok"; submodelName: string; nodes: SimpleNode[]; edges: SimpleEdge[] }
  | { status: "malformed"; submodelName: string; reason: string } {
  if (!isRecord(metadata)) {
    return { status: "malformed", submodelName, reason: "metadata must be an object" }
  }
  const graph = isRecord(metadata.graph) ? metadata.graph : metadata
  if (!isSimpleNodeArray(graph.nodes)) {
    return { status: "malformed", submodelName, reason: "graph.nodes must be an array of nodes" }
  }
  if (graph.edges !== undefined && !isSimpleEdgeArray(graph.edges)) {
    return { status: "malformed", submodelName, reason: "graph.edges must be an array of edges" }
  }
  return {
    status: "ok",
    submodelName,
    nodes: graph.nodes,
    edges: graph.edges ?? [],
  }
}

function resolveInstanceOriginal(
  originalId: unknown,
  visibleNodeMap: Record<string, SimpleNode>,
  visibleEdges: SimpleEdge[],
  submodels: Record<string, unknown> | undefined,
): InstanceOriginalResolution {
  if (typeof originalId !== "string" || originalId.length === 0) {
    return { status: "invalid", rawInstanceOf: originalId }
  }

  const matches: Extract<InstanceOriginalResolution, { status: "found" }>[] = []
  const visibleOriginal = visibleNodeMap[originalId]
  if (visibleOriginal) {
    matches.push({
      status: "found",
      original: visibleOriginal,
      originalNodeMap: visibleNodeMap,
      originalEdges: visibleEdges,
      submodelName: undefined,
    })
  }

  for (const [submodelName, metadata] of Object.entries(submodels ?? {})) {
    const graph = submodelGraphFromMetadata(submodelName, metadata)
    if (graph.status === "malformed") {
      return { status: "malformedSubmodel", originalId, submodelName, reason: graph.reason }
    }
    const matchingNodes = graph.nodes.filter((n) => n.id === originalId)
    if (matchingNodes.length === 0) continue
    if (matchingNodes.length > 1) {
      return {
        status: "ambiguous",
        originalId,
        locations: matchingNodes.map((_, index) => `${graph.submodelName}#${index + 1}`),
      }
    }
    const submodelNodeMap = Object.fromEntries(graph.nodes.map((n) => [n.id, n]))
    matches.push({
      status: "found",
      original: matchingNodes[0],
      originalNodeMap: submodelNodeMap,
      originalEdges: graph.edges,
      submodelName: graph.submodelName,
    })
  }

  if (matches.length === 1) return matches[0]
  if (matches.length > 1) {
    return {
      status: "ambiguous",
      originalId,
      locations: matches.map((match) => match.submodelName ?? "visible graph"),
    }
  }
  return { status: "missing", originalId }
}

function uniquePreservingOrder(values: string[]): string[] {
  const seen = new Set<string>()
  const unique: string[] = []
  for (const value of values) {
    if (seen.has(value)) continue
    seen.add(value)
    unique.push(value)
  }
  return unique
}

function resolveOriginalInputNames({
  originalId,
  originalEdges,
  originalNodeMap,
  visibleEdges,
  visibleNodeMap,
  submodelName,
}: {
  originalId: string
  originalEdges: SimpleEdge[]
  originalNodeMap: Record<string, SimpleNode>
  visibleEdges: SimpleEdge[]
  visibleNodeMap: Record<string, SimpleNode>
  submodelName?: string
}): string[] {
  const internalInputs = originalEdges
    .filter((e) => e.target === originalId)
    .map((e) => {
      const srcNode = originalNodeMap[e.source]
      return srcNode ? sanitizeName(srcNode.data.label) : e.source
    })

  if (!submodelName) return uniquePreservingOrder(internalInputs)

  const submodelNodeId = `submodel__${submodelName}`
  const boundaryInputs = visibleEdges
    .filter((e) => e.target === submodelNodeId && e.targetHandle === `in__${originalId}`)
    .map((e) => {
      const srcNode = visibleNodeMap[e.source]
      return srcNode ? sanitizeName(srcNode.data.label) : e.source
    })

  return uniquePreservingOrder([...internalInputs, ...boundaryInputs])
}

function InstanceReferenceDiagnostic({
  resolution,
}: {
  resolution: Exclude<InstanceOriginalResolution, { status: "found" }>
}) {
  const detail = resolution.status === "invalid"
    ? "Instance config must use a non-empty string instanceOf id."
    : resolution.status === "malformedSubmodel"
      ? `Wrapper "${resolution.submodelName}" has invalid metadata: ${resolution.reason}.`
      : resolution.status === "ambiguous"
        ? `Found more than one original named "${resolution.originalId}" in: ${resolution.locations.join(", ")}.`
        : `No visible node or submodel original exists with id "${resolution.originalId}".`
  const configDiagnostic = resolution.status === "invalid"
    ? { instanceOf: resolution.rawInstanceOf }
    : { instanceOf: resolution.originalId }
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
            Broken instance reference
          </span>
        </div>
        <p className="text-[12px] leading-relaxed" style={{ color: 'var(--text-secondary)' }}>
          {detail}
        </p>
        <pre
          className="text-[11px] leading-relaxed font-mono whitespace-pre-wrap break-words rounded-md px-3 py-2 select-text overflow-x-auto"
          style={{ background: 'var(--bg-surface)', border: '1px solid var(--border)', color: 'var(--text-primary)' }}
        >
          {JSON.stringify(configDiagnostic, null, 2)}
        </pre>
      </div>
    </div>
  )
}

function InstancePanel({
  node,
  config,
  nodeMap,
  handleConfigUpdate,
}: {
  node: SimpleNode
  config: Record<string, unknown>
  nodeMap: Record<string, SimpleNode>
  handleConfigUpdate: (keyOrUpdates: string | Record<string, unknown>, value?: unknown) => void
}) {
  const { edges, submodels } = useGraph()
  const originalResolution = resolveInstanceOriginal(config.instanceOf, nodeMap, edges, submodels)
  if (originalResolution.status !== "found") {
    return <InstanceReferenceDiagnostic resolution={originalResolution} />
  }
  const {
    original: orig,
    originalNodeMap,
    originalEdges,
    submodelName,
  } = originalResolution
  const origId = orig.id
  return (
    <div className="px-4 py-3 flex flex-col gap-3">
      <div className="flex items-center gap-2 px-3 py-2 rounded-lg" style={{ background: 'var(--accent-soft)', border: '1px solid var(--text-accent-line)' }}>
        <Link2 size={13} style={{ color: 'var(--accent)' }} className="shrink-0" />
        <div className="min-w-0">
          <div className="text-[11px] font-bold uppercase tracking-[0.08em]" style={{ color: 'var(--accent)' }}>Instance of</div>
          <div className="text-[13px] font-semibold truncate" style={{ color: 'var(--text-primary)' }}>
            {orig.data.label}
          </div>
          {submodelName && (
            <div className="text-[11px] truncate" style={{ color: 'var(--text-muted)' }}>
              in {submodelName}
            </div>
          )}
        </div>
      </div>
      <p className="text-[11px] leading-relaxed" style={{ color: 'var(--text-muted)' }}>
        This node uses the same logic as the original. To edit the code or config, select the original node. Changes will automatically apply to all instances.
      </p>

      {/* Input Mapping */}
      {(() => {
        const origInputs = resolveOriginalInputNames({
          originalId: origId,
          originalEdges,
          originalNodeMap,
          visibleEdges: edges,
          visibleNodeMap: nodeMap,
          submodelName,
        })
        const instInputs = edges
          .filter((e) => e.target === node.id)
          .map((e) => {
            const srcNode = nodeMap[e.source]
            return {
              varName: srcNode ? sanitizeName(srcNode.data.label) : e.source,
              label: srcNode ? srcNode.data.label : e.source,
            }
          })

        if (origInputs.length === 0 && instInputs.length === 0) return null

        const currentMapping = (config.inputMapping || {}) as Record<string, string>

        // Auto-initialise mapping if empty or stale.
        const autoMap: Record<string, string> = {}
        const usedInst = new Set<string>()
        for (const orig of origInputs) {
          const exact = instInputs.find((i) => i.varName === orig && !usedInst.has(i.varName))
          if (exact) { autoMap[orig] = exact.varName; usedInst.add(exact.varName) }
        }
        for (const orig of origInputs) {
          if (autoMap[orig]) continue
          const sub = instInputs.find((i) => !usedInst.has(i.varName) && i.varName.includes(orig))
          if (sub) { autoMap[orig] = sub.varName; usedInst.add(sub.varName) }
        }
        const remaining = instInputs.filter((i) => !usedInst.has(i.varName))
        const unmapped = origInputs.filter((o) => !autoMap[o])
        unmapped.forEach((orig, idx) => {
          if (idx < remaining.length) autoMap[orig] = remaining[idx].varName
        })

        const effectiveMap: Record<string, string> = {}
        const instVarNames = new Set(instInputs.map((i) => i.varName))
        for (const orig of origInputs) {
          if (currentMapping[orig] && instVarNames.has(currentMapping[orig])) {
            effectiveMap[orig] = currentMapping[orig]
          } else {
            effectiveMap[orig] = autoMap[orig] || ""
          }
        }

        const handleMappingChange = (origParam: string, instVar: string) => {
          const newMapping = { ...effectiveMap, [origParam]: instVar }
          handleConfigUpdate("inputMapping", newMapping)
        }

        return (
          <div className="flex flex-col gap-2">
            <div className="text-[11px] font-bold uppercase tracking-[0.08em]" style={{ color: 'var(--text-muted)' }}>
              Input Mapping
            </div>
            <p className="text-[10px] leading-relaxed" style={{ color: 'var(--text-muted)' }}>
              Map each original input to a connected upstream node.
            </p>
            <div className="flex flex-col gap-1.5">
              {origInputs.map((orig) => (
                <div key={orig} className="flex items-center gap-2 px-2 py-1.5 rounded-md" style={{ background: 'var(--bg-surface)', border: '1px solid var(--border)' }}>
                  <span className="text-[11px] font-mono shrink-0 min-w-[90px] truncate" style={{ color: 'var(--text-secondary)' }} title={orig}>
                    {orig}
                  </span>
                  <span className="text-[10px] shrink-0" style={{ color: 'var(--text-muted)' }}>→</span>
                  <select
                    className="flex-1 min-w-0 text-[11px] font-mono px-1.5 py-1 rounded border bg-transparent appearance-none cursor-pointer truncate"
                    style={{ color: 'var(--text-primary)', borderColor: 'var(--border)', background: 'var(--bg-panel)' }}
                    value={effectiveMap[orig] || ""}
                    onChange={(e) => handleMappingChange(orig, e.target.value)}
                  >
                    <option value="">— unmapped —</option>
                    {instInputs.map((i) => (
                      <option key={i.varName} value={i.varName}>{i.label}</option>
                    ))}
                  </select>
                </div>
              ))}
            </div>
          </div>
        )
      })()}

      {(() => {
        const warnings = (node.data._schemaWarnings as { column: string; status: string }[]) || []
        if (warnings.length === 0) return null
        return (
          <div className="flex flex-col gap-1.5 px-3 py-2 rounded-lg" style={{ background: 'var(--warning-soft)', border: '1px solid var(--warning-border)' }}>
            <div className="flex items-center gap-1.5">
              <AlertTriangle size={11} style={{ color: 'var(--warning-strong)' }} className="shrink-0" />
              <span className="text-[11px] font-bold uppercase tracking-[0.08em]" style={{ color: 'var(--warning-strong)' }}>
                Missing columns ({warnings.length})
              </span>
            </div>
            <p className="text-[10px] leading-relaxed" style={{ color: 'var(--text-muted)' }}>
              The original node receives columns that are not available at this instance&apos;s position:
            </p>
            <div className="flex flex-wrap gap-1 mt-0.5">
              {warnings.map((w) => (
                <span key={w.column} className="px-1.5 py-0.5 rounded text-[10px] font-mono" style={{ background: 'var(--warning-soft-emphasis)', color: 'var(--warning)' }}>
                  {w.column}
                </span>
              ))}
            </div>
          </div>
        )
      })()}
    </div>
  )
}

// ─── Helpers ──────────────────────────────────────────────────────

type ColumnInfo = { name: string; dtype: string }

/** Collect upstream columns from already-filtered incoming edges. */
function collectColumnsFromEdges(edges: SimpleEdge[], nodeMap: Record<string, SimpleNode>): ColumnInfo[] {
  const cols: ColumnInfo[] = []
  const seen = new Set<string>()
  edges.forEach(e => {
    const src = nodeMap[e.source]
    const srcCols = (src?.data as Record<string, unknown>)?._columns as ColumnInfo[] | undefined
    if (srcCols) srcCols.forEach(c => { if (!seen.has(c.name)) { seen.add(c.name); cols.push(c) } })
  })
  return cols
}

function columnsSignature(columns: ColumnInfo[] | undefined): string {
  return columns?.map((column) => `${column.name}\u0002${column.dtype}`).join("\u0001") ?? ""
}

function upstreamColumnsSignature(edges: SimpleEdge[], nodeMap: Record<string, SimpleNode>): string {
  return edges
    .map((edge) => {
      const src = nodeMap[edge.source]
      const srcCols = (src?.data as Record<string, unknown> | undefined)?._columns as ColumnInfo[] | undefined
      return `${edge.source}\u0003${columnsSignature(srcCols)}`
    })
    .join("\u0004")
}

function upstreamNodeTypeSignature(edges: SimpleEdge[], nodeMap: Record<string, SimpleNode>): string {
  return edges
    .map((edge) => `${edge.source}\u0002${nodeMap[edge.source]?.data?.nodeType ?? ""}`)
    .join("\u0001")
}

function upstreamLabelSignature(edges: SimpleEdge[], nodeMap: Record<string, SimpleNode>): string {
  return edges
    .map((edge) => `${edge.id}\u0002${edge.source}\u0003${nodeMap[edge.source]?.data?.label ?? ""}${edge.inputAlias ?? ""}`)
    .join("\u0001")
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
          href="/docs/building-models/nodes/"
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
          style={{ background: 'var(--bg-surface)', border: '1px solid var(--border)', color: 'var(--text-primary)' }}
        >
          {JSON.stringify(config, null, 2)}
        </pre>
      </div>
    </div>
  )
}

// Cached preview-result fields cleared on user config edits.  Listing the
// keys explicitly (rather than stripping every leading-underscore field)
// preserves selection/trace state — `_status`, `_traceActive`, etc. — which
// are intentionally NOT invalidated by a config change.  Adding a new cached
// field to ``HauteNodeData`` will not surface here automatically — the choice
// to clear or preserve a new field must be made deliberately by extending
// this list.
const CACHED_PREVIEW_KEYS: readonly (keyof HauteNodeData)[] = [
  "_columns",
  "_availableColumns",
  "_schemaWarnings",
]

function clearCachedResultShape(data: HauteNodeData): HauteNodeData {
  const next = { ...data }
  for (const key of CACHED_PREVIEW_KEYS) {
    delete next[key]
  }
  return next
}

// ─── NodePanel ────────────────────────────────────────────────────

export default function NodePanel({
  node,
  onClose,
  onUpdateNode,
  onDeleteEdge,
  onSetInputAlias,
  onSwapEdgeJoinInputs,
  onRefreshPreview,
  dimmed,
  errorLine,
  previewRows,
  selectedPreviewLoading = false,
}: NodePanelProps) {
  const { allNodes, edges } = useGraph()
  const config = useMemo(() => (node?.data.config || {}) as Record<string, unknown>, [node?.data.config])
  const [activeTab, setActiveTab] = useState<"config" | "columns">("config")
  const rememberedExplorePane = useUIStore((s) => node?.id ? s.explorePanes[node.id] : undefined)
  const setExplorePane = useUIStore((s) => s.setExplorePane)

  // Keep config and node in refs so handleConfigUpdate never captures stale values
  const configRef = useRef(config)
  const nodeRef = useRef(node)
  useEffect(() => { configRef.current = config }, [config])
  useEffect(() => { nodeRef.current = node }, [node])

  // Bundle 3b — dismissal state for the stale-columns banner.
  // Stored as the warning-signature the user dismissed, so the banner
  // reappears whenever the warning content (columns / statuses / count)
  // changes.  Reset on node switch so dismissals don't bleed across
  // nodes while the panel stays mounted.
  const [dismissedStaleWarningSig, setDismissedStaleWarningSig] = useState<string | null>(null)
  useEffect(() => { setDismissedStaleWarningSig(null) }, [node?.id])

  const handleConfigUpdate = useCallback((keyOrUpdates: string | Record<string, unknown>, value?: unknown) => {
    const currentNode = nodeRef.current
    if (!currentNode || !onUpdateNode) return
    const currentConfig = configRef.current
    const newConfig = typeof keyOrUpdates === "string"
      ? { ...currentConfig, [keyOrUpdates]: value }
      : { ...currentConfig, ...keyOrUpdates }
    onUpdateNode(currentNode.id, clearCachedResultShape({ ...currentNode.data, config: newConfig }))
  }, [onUpdateNode])

  const configWithNodeId = useMemo(
    () => ({ ...config, _nodeId: node?.id ?? "" }),
    [config, node?.id]
  )

  // Compute input sources (must be before early return to satisfy hook ordering rules)
  const nodeMap = useMemo(() => Object.fromEntries(allNodes.map((n) => [n.id, n])), [allNodes])
  const selectedNodeId = node?.id ?? null
  const upstreamEdges = useMemo(
    () => selectedNodeId ? edges.filter((e) => e.target === selectedNodeId) : [],
    [edges, selectedNodeId],
  )
  const upstreamSourceLabelSignature = upstreamLabelSignature(upstreamEdges, nodeMap)
  const inputSources: InputSource[] = useMemo(() => {
    if (!selectedNodeId) return []
    return upstreamEdges.map((e) => ({
      sourceNodeId: e.source,
      varName: sanitizeName(nodeMap[e.source]?.data.label || e.source),
      sourceLabel: nodeMap[e.source]?.data.label || e.source,
      edgeId: e.id,
      inputAlias: e.inputAlias ?? undefined,
    }))
    // Keyed by selected node + label signature so selected-node edits that
    // rebuild nodeMap do not churn this array; only upstream label/edge changes
    // do.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedNodeId, upstreamSourceLabelSignature])
  const upstreamSchemaSignature = upstreamColumnsSignature(upstreamEdges, nodeMap)
  const upstreamColumns = useMemo(() => {
    if (!selectedNodeId) return []
    return collectColumnsFromEdges(upstreamEdges, nodeMap)
    // Intentionally keyed by selected node plus upstream schema content.
    // Selected-node config/label edits rebuild nodeMap but do not change the
    // upstream schema, so they should preserve this array identity.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedNodeId, upstreamSchemaSignature])
  const upstreamTypesSignature = upstreamNodeTypeSignature(upstreamEdges, nodeMap)
  const hasApiInputUpstream = useMemo(
    () => upstreamEdges.some((edge) => nodeMap[edge.source]?.data?.nodeType === NODE_TYPES.API_INPUT),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [selectedNodeId, upstreamTypesSignature],
  )

  if (!node) return null

  const isInstance = !!config.instanceOf
  const nodeType = effectiveNodeType(node)
  const isKnownNodeType = Object.hasOwn(NODE_TYPE_META, nodeType)
  const showColumnsTab = isKnownNodeType && !isInstance && !NO_COLUMNS_TAB.has(nodeType)
  const showExplorePanes = isKnownNodeType && !isInstance && nodeType === NODE_TYPES.EXPLORE
  const showRefreshPreview = !!onRefreshPreview && !NO_REFRESH_PREVIEW.has(nodeType)
  const refreshTitle = showExplorePanes ? "Refresh Explore outputs" : "Refresh preview"
  const activeExplorePane = showExplorePanes ? rememberedExplorePane ?? "code" : "code"
  const activeExplorePaneMeta = EXPLORE_PANES.find((pane) => pane.key === activeExplorePane) ?? EXPLORE_PANES[0]

  // ── Render the right editor based on nodeType ──

  const accentColor = NODE_TYPE_META[nodeType as NodeTypeValue]?.color ?? "var(--accent)"
  // Type-identity chip (tooltips-descriptions §3.4-c, mandatory): once a
  // node's label is edited, the panel otherwise never says what TYPE is
  // being edited. For edgeJoin this is also the only non-canvas descriptive
  // surface (no palette entry exists). Guarded on isKnownNodeType.
  const typeMeta = isKnownNodeType ? NODE_TYPE_META[nodeType as NodeTypeValue] : undefined
  const TypeChipIcon = typeMeta?.icon

  const renderEditor = () => {
    if (!isKnownNodeType) {
      return <UnknownNodeTypeDiagnostic nodeType={nodeType} config={config} />
    }

    if (isInstance) {
      return (
        <InstancePanel
          node={node}
          config={config}
          nodeMap={nodeMap}
          handleConfigUpdate={handleConfigUpdate}
        />
      )
    }

    switch (nodeType) {
      case NODE_TYPES.API_INPUT:
        // Bundle 3a — the per-node config file lives at
        // config/quote_input/<sanitised_label>.json on disk. The
        // backend's canonical scheme uses `_sanitize_func_name(label)`
        // (`_config_io.py:320-321`) as the filename. The frontend
        // previously sent `${node.id}.json` here, which the cache-
        // status GET routed to a path the backend never wrote → silent
        // `cached=false` response → cache button looked unresponsive.
        // Using `sanitizeName(label)` (the frontend twin of
        // `_sanitize_func_name`, defined in `frontend/src/utils/sanitizeName.ts`)
        // brings the two sides into agreement. Collision uniqueness
        // for labels-that-sanitise-to-the-same-string is already
        // enforced at save time via `_validate_unique_sanitized_names`
        // (`_save_pipeline.py:165-184`) → HTTP 400, no silent clobber.
        return <ApiInputEditor config={config} onUpdate={handleConfigUpdate} accentColor={accentColor} configPath={`config/quote_input/${sanitizeName(node.data.label)}.json`} />

      case NODE_TYPES.LIVE_SWITCH:
        return <LiveSwitchEditor config={config} onUpdate={handleConfigUpdate} inputSources={inputSources} accentColor={accentColor} />

      case NODE_TYPES.DATA_SOURCE:
        return <DataSourceEditor config={config} onUpdate={handleConfigUpdate} onRefreshPreview={onRefreshPreview} accentColor={accentColor} errorLine={errorLine} />

      case NODE_TYPES.DATA_SINK:
        return <SinkEditor config={config} onUpdate={handleConfigUpdate} nodeId={node.id} accentColor={accentColor} />

      case NODE_TYPES.EXPLORE:
        if (activeExplorePane === "code") {
          return (
            <div
              id="explore-code-pane"
              role="tabpanel"
              aria-labelledby="explore-code-tab"
              data-testid="explore-code-pane"
              className="h-full min-h-0 flex flex-col"
            >
              <ExploreCodeEditor
                config={config}
                onUpdate={handleConfigUpdate}
                inputSources={inputSources}
                onDeleteInput={onDeleteEdge}
                onRenameInput={onSetInputAlias}
                errorLine={errorLine}
                upstreamColumns={upstreamColumns}
              />
            </div>
          )
        }
        return (
          <div
            id={`explore-${activeExplorePaneMeta.key}-pane`}
            role="tabpanel"
            aria-labelledby={`explore-${activeExplorePaneMeta.key}-tab`}
            data-testid={`explore-${activeExplorePaneMeta.key}-pane`}
            className="h-full"
          >
            {activeExplorePane === "overview" && (
              <ExploreOverviewConfig config={config} onUpdate={handleConfigUpdate} />
            )}
          </div>
        )

      case NODE_TYPES.EXTERNAL_FILE:
        return <ExternalFileEditor config={config} onUpdate={handleConfigUpdate} inputSources={inputSources} onDeleteInput={onDeleteEdge} onRenameInput={onSetInputAlias} errorLine={errorLine} accentColor={accentColor} />

      case NODE_TYPES.OUTPUT:
        return <OutputEditor config={config} onUpdate={handleConfigUpdate} nodeId={node.id} />

      case NODE_TYPES.BANDING:
        return (
          <BandingEditor
            config={config}
            onUpdate={handleConfigUpdate}
            inputSources={inputSources}
            onDeleteInput={onDeleteEdge}
            onRenameInput={onSetInputAlias}
            upstreamColumns={upstreamColumns}
            accentColor={accentColor}
            previewRows={previewRows}
          />
        )

      case NODE_TYPES.SCENARIO_EXPANDER:
        return (
          <ScenarioExpanderEditor
            config={config}
            onUpdate={handleConfigUpdate}
            inputSources={inputSources}
            onDeleteInput={onDeleteEdge}
            onRenameInput={onSetInputAlias}
            upstreamColumns={upstreamColumns}
            errorLine={errorLine}
          />
        )

      case NODE_TYPES.RATING_STEP:
        return (
          <RatingStepEditor
            config={config}
            onUpdate={handleConfigUpdate}
            inputSources={inputSources}
            onDeleteInput={onDeleteEdge}
            onRenameInput={onSetInputAlias}
            upstreamColumns={upstreamColumns}
            previewRows={previewRows}
            accentColor={accentColor}
            errorLine={errorLine}
            nodeId={node.id}
          />
        )

      case NODE_TYPES.MODEL_SCORE:
        return <ModelScoreEditor config={config} onUpdate={handleConfigUpdate} inputSources={inputSources} onDeleteInput={onDeleteEdge} onRenameInput={onSetInputAlias} errorLine={errorLine} accentColor={accentColor} />

      case NODE_TYPES.MODELLING: {
        // Modelling is a pass-through -- its own _columns (set by preview) ARE the upstream columns
        const effectiveCols = upstreamColumns.length > 0
          ? upstreamColumns
          : ((node.data as Record<string, unknown>)?._columns as { name: string; dtype: string }[] | undefined) || []
        return (
          <ModellingConfig
            config={configWithNodeId}
            onUpdate={handleConfigUpdate}
            upstreamColumns={effectiveCols}
          />
        )
      }

      case NODE_TYPES.OPTIMISER: {
        const effectiveCols = upstreamColumns.length > 0
          ? upstreamColumns
          : ((node.data as Record<string, unknown>)?._columns as { name: string; dtype: string }[] | undefined) || []
        return (
          <OptimiserConfig
            config={configWithNodeId}
            onUpdate={handleConfigUpdate}
            upstreamColumns={effectiveCols}
            accentColor={accentColor}
            deferColumnFetch={selectedPreviewLoading}
          />
        )
      }

      case NODE_TYPES.OPTIMISER_APPLY:
        return (
          <OptimiserApplyEditor
            config={config}
            onUpdate={handleConfigUpdate}
            inputSources={inputSources}
            onDeleteInput={onDeleteEdge}
            onRenameInput={onSetInputAlias}
            accentColor={accentColor}
          />
        )

      case NODE_TYPES.CONSTANT:
        return <ConstantEditor config={config} onUpdate={handleConfigUpdate} />

      case NODE_TYPES.POLARS:
        return (
          <TransformEditor
            config={config}
            onUpdate={handleConfigUpdate}
            inputSources={inputSources}
            onDeleteInput={onDeleteEdge}
            onRenameInput={onSetInputAlias}
            errorLine={errorLine}
            upstreamColumns={upstreamColumns}
            hasApiInputUpstream={hasApiInputUpstream}
          />
        )

      case NODE_TYPES.EDGE_JOIN:
        return (
          <EdgeJoinEditor
            config={config}
            onUpdate={handleConfigUpdate}
            nodeId={node.id}
            accentColor={accentColor}
            onDeleteInput={onDeleteEdge}
            onSwapInputs={onSwapEdgeJoinInputs ? () => onSwapEdgeJoinInputs(node.id) : undefined}
          />
        )

      case NODE_TYPES.SUBMODEL:
        return <SubmodelEditor config={config} accentColor={accentColor} nodeId={node?.id ?? ""} edges={edges} />

      default:
        return null
    }
  }

  const availableColumns = ((node.data as Record<string, unknown>)?._availableColumns as { name: string; dtype: string }[]) || []
  const currentColumns = ((node.data as Record<string, unknown>)?._columns as { name: string; dtype: string }[]) || []

  return (
    <PanelShell testId="node-panel" style={{ opacity: dimmed ? 0.6 : 1, transition: 'opacity 150ms' }}>
      <div className="px-3 py-2.5 flex items-center gap-2 shrink-0" style={{ borderBottom: '1px solid var(--border)' }}>
        {typeMeta && TypeChipIcon && (
          <Tooltip
            content={<NodeTypeTooltip type={nodeType as NodeTypeValue} />}
            placement="bottom"
          >
            {(tooltipTriggerProps) => (
              <div
                {...tooltipTriggerProps}
                data-testid="node-panel-type-chip"
                tabIndex={0}
                aria-label={`Node type: ${typeMeta.name}`}
                className="w-[22px] h-[22px] rounded-md flex items-center justify-center shrink-0"
                style={{ background: `${withAlpha(typeMeta.color, 0.094)}` }}
              >
                <TypeChipIcon size={13} style={{ color: typeMeta.color }} />
              </div>
            )}
          </Tooltip>
        )}
        <input
          data-testid="node-panel-label-input"
          type="text"
          value={node.data.label}
          onChange={(e) => {
            if (onUpdateNode) {
              onUpdateNode(node.id, { ...node.data, label: e.target.value })
            }
          }}
          className="node-label-input flex-1 min-w-0 px-2 py-1 text-[13px] font-semibold border border-transparent rounded-md focus:outline-none bg-transparent"
          style={{ color: 'var(--text-primary)', borderColor: 'transparent' }}
        />
        {showRefreshPreview && (
          <button
            onClick={onRefreshPreview}
            className="px-2 py-1 rounded shrink-0 transition-opacity flex items-center gap-1 text-[11px] font-medium hover:opacity-[0.85]"
            style={{ background: 'var(--accent)', color: 'var(--text-on-accent)' }}
            title={refreshTitle}
          >
            <RefreshCw size={11} />
            Refresh
          </button>
        )}
        <button data-testid="node-panel-close" onClick={onClose} className="node-close-btn p-1 rounded shrink-0 transition-colors" style={{ color: 'var(--text-on-accent)' }}
          title="Close"
        >
          <X size={14} strokeWidth={2.5} />
        </button>
      </div>

      {/* Tab bar — only show when Columns tab is available.  Hover
          background is applied via Tailwind only for the INACTIVE tab
          so the accent-soft background of the active tab doesn't
          flicker on mouseover.  Inactive tabs deliberately omit an
          inline `background` so the Tailwind `hover:` rule can apply
          (inline styles would otherwise win over class rules). */}
      {showColumnsTab && (
        <div className="flex shrink-0" style={{ borderBottom: '1px solid var(--border)' }}>
          {(["config", "columns"] as const).map((tab) => {
            const isActive = activeTab === tab
            const activeStyle: React.CSSProperties = {
              color: 'var(--accent)',
              borderBottom: '2px solid var(--accent)',
              background: 'var(--accent-soft)',
            }
            const inactiveStyle: React.CSSProperties = {
              color: 'var(--text-muted)',
              borderBottom: '2px solid transparent',
            }
            return (
              <button
                key={tab}
                onClick={() => setActiveTab(tab)}
                className={`flex-1 px-3 py-1.5 text-[11px] font-semibold uppercase tracking-[0.08em] transition-colors${
                  isActive ? '' : ' hover:bg-[var(--bg-hover)]'
                }`}
                style={isActive ? activeStyle : inactiveStyle}
              >
                {tab}
              </button>
            )
          })}
        </div>
      )}

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

      {/* Schema warnings for non-instance nodes.  Bundle 3b: dismiss
          (×) + Refresh-and-check controls.  Suppressed when the current
          warning signature matches the user's last dismissal. */}
      {!isInstance && !showExplorePanes && (() => {
        const warnings = (node.data._schemaWarnings as { column: string; status: string }[]) || []
        if (warnings.length === 0) return null
        const sig = warnings.map((w) => `${w.column}|${w.status}`).join(',')
        if (sig === dismissedStaleWarningSig) return null
        return (
          <div className="px-4 py-2 shrink-0" style={{ borderBottom: '1px solid var(--border)' }}>
            <div className="flex flex-col gap-1.5 px-3 py-2 rounded-lg" style={{ background: 'var(--warning-soft)', border: '1px solid var(--warning-border)' }}>
              <div className="flex items-center justify-between gap-2">
                <div className="flex items-center gap-1.5">
                  <AlertTriangle size={11} style={{ color: 'var(--warning-strong)' }} className="shrink-0" />
                  <span className="text-[11px] font-bold uppercase tracking-[0.08em]" style={{ color: 'var(--warning-strong)' }}>
                    Stale columns ({warnings.length})
                  </span>
                </div>
                <div className="flex items-center gap-1.5 shrink-0">
                  <button
                    onClick={() => {
                      setDismissedStaleWarningSig(sig)
                      onRefreshPreview?.()
                    }}
                    className="px-2 py-1 rounded shrink-0 transition-opacity flex items-center gap-1 text-[11px] font-medium hover:opacity-[0.85]"
                    style={{ background: 'var(--accent)', color: 'var(--text-on-accent)' }}
                    title="Re-run preview and re-check schema warnings"
                  >
                    <RefreshCw size={11} />
                    Refresh and check
                  </button>
                  <button
                    onClick={() => setDismissedStaleWarningSig(sig)}
                    className="p-1 rounded shrink-0 transition-colors hover:opacity-[0.85]"
                    style={{ color: 'var(--warning-strong)' }}
                    title="Dismiss"
                    aria-label="Dismiss"
                  >
                    <X size={12} strokeWidth={2.5} />
                  </button>
                </div>
              </div>
              <p className="text-[10px] leading-relaxed" style={{ color: 'var(--text-muted)' }}>
                These columns are referenced in config but not found in the upstream schema:
              </p>
              <div className="flex flex-wrap gap-1 mt-0.5">
                {warnings.map((w) => (
                  <span key={w.column} className="px-1.5 py-0.5 rounded text-[10px] font-mono" style={{ background: 'var(--warning-soft-emphasis)', color: 'var(--warning)' }}>
                    {w.column}
                  </span>
                ))}
              </div>
            </div>
          </div>
        )
      })()}

      {/* Editor-body error boundary (scoped DELIBERATELY to the scroll area,
          NOT the whole panel): if a lazy editor's dynamic import rejects — the
          chunk 404s on a stale build, a network blip — React throws past the
          Suspense boundary. Catching it HERE keeps the failure inside the body,
          below the header, so the close button (and label/refresh) always stay
          reachable. Catching it at the App-level NodePanel boundary instead
          replaced the entire panel with a fallback that had no way to close —
          the banner sat over the close button (BUGS.md: hard-to-remove banner). */}
      <div className="flex-1 min-h-0 overflow-y-auto">
        <ErrorBoundary name="NodeEditor">
          <LazyEditorBoundary>
            {activeTab === "columns" && showColumnsTab ? (
              nodeType === NODE_TYPES.API_INPUT ? (
                <GroupedColumnsTab
                  config={config}
                  onUpdate={handleConfigUpdate}
                  availableColumns={availableColumns}
                  columns={currentColumns}
                />
              ) : (
                <ColumnsTab
                  config={config}
                  onUpdate={handleConfigUpdate}
                  availableColumns={availableColumns}
                  columns={currentColumns}
                />
              )
            ) : renderEditor()}
          </LazyEditorBoundary>
        </ErrorBoundary>
      </div>
    </PanelShell>
  )
}
