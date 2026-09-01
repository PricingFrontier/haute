import { useCallback, useMemo } from "react"
import { X, Link2, AlertTriangle, RefreshCw, Lock } from "lucide-react"
import { fetchExplorePivotMembers } from "../api/client"
import { NODE_TYPES, NODE_TYPE_META } from "../utils/nodeTypes"
import type { NodeTypeValue } from "../utils/nodeTypes"
import { authoritativeSourceHandles, edgeInputName } from "../utils/apiInputPorts"
import {
  ColumnsTab,
  PolarsCodePanel,
  LazyEditorBoundary,
} from "./LazyNodeEditors"
import type { InputSource, SimpleNode, SimpleEdge, OnUpdateConfig, OnUpdateConfigResult, OnReplaceConfig } from "./editors"
import {
  effectiveNodeType,
  isSubmodelDefinition,
  isSubmodelInstanceConfig,
  type HauteNodeData,
  type LoadAvailability,
} from "../types/node"
import type { PipelineDiagnostic } from "../types/pipelineDocument"
import useUIStore, { type ExplorePane, type ModellingPane } from "../stores/useUIStore"
import useNodeResultsStore, { hashConfig } from "../stores/useNodeResultsStore"
import useSettingsStore from "../stores/useSettingsStore"
import useDocumentStatusStore, { documentReadOnlyReason } from "../stores/useDocumentStatusStore"
import { buildExploreCacheIdentity } from "./explore/cacheIdentity"
import PanelShell from "./PanelShell"
import PreviewPanelTabs from "./PreviewPanelTabs"
import { useGraph } from "./useGraph"
import { buildGraph } from "../utils/buildGraph"
import { CommittedTextField } from "../components/form"
import {
  useNodePanelSession,
  useNodeRenameSession,
  type NodePanelTab,
} from "./useNodePanelSession"
import { NodeConfigEditor } from "./NodeConfigEditor"

type NodePanelProps = {
  node: SimpleNode | null
  onClose: () => void
  onUpdateNode?: (id: string, data: Record<string, unknown>) => OnUpdateConfigResult
  onRenameNode?: (id: string, label: string) => Promise<OnUpdateConfigResult>
  onDeleteEdge?: (edgeId: string) => void
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
  /** True while inspecting a created submodel instance's shared definition. */
  readOnly?: boolean
  /** True when the current pipeline document is not executable/mutable. */
  documentReadOnly?: boolean
  /** Opens the document-level remove-only recovery flow. */
  onRemoveUnavailableNode?: (target: { sourceFile: string; recoveryId: string }) => void
}

// ─── Node types that do NOT show the Columns tab ──
// Output already has its own field selection; submodels/ports are placeholders;
// modelling and explore nodes are sink-only (no outputs).
//
// API input column selection lives in `tables[].columns[]` in its Schema panel.
const NO_COLUMNS_TAB = new Set<string>([
  NODE_TYPES.API_INPUT,
  NODE_TYPES.OUTPUT,
  NODE_TYPES.SUBMODEL,
  NODE_TYPES.SUBMODEL_PORT,
  NODE_TYPES.MODELLING,
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

const NO_REFRESH_PREVIEW = new Set<string>([
  NODE_TYPES.SUBMODEL,
  NODE_TYPES.SUBMODEL_PORT,
])

// Right-panel panes for Explore nodes. Code prepares the analysis dataset;
// Overview, Pivots, and Charts configure display, while Export remains scaffolding.
const EXPLORE_PANES = [
  { key: "code", label: "Polars Code" },
  { key: "overview", label: "Overview" },
  { key: "pivots", label: "Pivots" },
  { key: "charts", label: "Charts" },
  { key: "export", label: "Export" },
] as const satisfies readonly { key: ExplorePane; label: string }[]

const MODELLING_PANES = [
  { key: "target", label: "Target" },
  { key: "features", label: "Features" },
  { key: "params", label: "Params" },
  { key: "split", label: "Split" },
  { key: "train", label: "Train" },
] as const satisfies readonly { key: ModellingPane; label: string }[]

// ─── Instance sub-panel (kept inline — it references multiple node-level concerns) ──

type InstanceOriginalResolution =
  | {
      status: "found"
      original: SimpleNode
      originalNodeMap: Record<string, SimpleNode>
      originalEdges: SimpleEdge[]
      definitionId?: string
    }
  | { status: "invalid"; rawInstanceOf: unknown }
  | { status: "missing"; originalId: string }
  | { status: "ambiguous"; originalId: string; locations: string[] }
  | { status: "malformedSubmodel"; originalId: string; definitionId: string; reason: string }

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
      definitionId: undefined,
    })
  }

  for (const [definitionId, value] of Object.entries(submodels ?? {})) {
    if (!isSubmodelDefinition(value, definitionId)) {
      return {
        status: "malformedSubmodel",
        originalId,
        definitionId,
        reason: "definition does not satisfy the canonical identity and port contract",
      }
    }
    const nodes = value.graph.nodes as unknown as SimpleNode[]
    const edges = value.graph.edges as SimpleEdge[]
    const matchingNodes = nodes.filter((node) => node.id === originalId)
    if (matchingNodes.length === 0) continue
    if (matchingNodes.length > 1) {
      return {
        status: "ambiguous",
        originalId,
        locations: matchingNodes.map((_, index) => definitionId + "#" + (index + 1)),
      }
    }
    matches.push({
      status: "found",
      original: matchingNodes[0],
      originalNodeMap: Object.fromEntries(nodes.map((node) => [node.id, node])),
      originalEdges: edges,
      definitionId,
    })
  }

  if (matches.length === 1) return matches[0]
  if (matches.length > 1) {
    return {
      status: "ambiguous",
      originalId,
      locations: matches.map((match) => match.definitionId ?? "visible graph"),
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
  definitionId,
  submodels,
}: {
  originalId: string
  originalEdges: SimpleEdge[]
  originalNodeMap: Record<string, SimpleNode>
  visibleEdges: SimpleEdge[]
  visibleNodeMap: Record<string, SimpleNode>
  definitionId?: string
  submodels?: Record<string, unknown>
}): string[] {
  const internalInputs = originalEdges
    .filter((edge) => edge.target === originalId)
    .map((edge) => {
      const sourceNode = originalNodeMap[edge.source]
      if (!sourceNode) {
        throw new Error(
          "Cannot derive instance input name: source node " + edge.source + " is missing",
        )
      }
      return edgeInputName(edge, sourceNode, submodels)
    })

  if (!definitionId) return uniquePreservingOrder(internalInputs)

  const definition = submodels?.[definitionId]
  if (!isSubmodelDefinition(definition, definitionId)) {
    throw new Error(
      "Cannot derive instance inputs: definition " + definitionId + " is missing or malformed",
    )
  }
  const publicInputPortIds = new Set(
    definition.inputPorts
      .filter((port) => port.targets.some((target) => target.nodeId === originalId))
      .map((port) => port.portId),
  )
  if (publicInputPortIds.size === 0) return uniquePreservingOrder(internalInputs)

  const occurrenceIds = new Set<string>()
  for (const visibleNode of Object.values(visibleNodeMap)) {
    if (visibleNode.data.nodeType !== NODE_TYPES.SUBMODEL) continue
    if (!isSubmodelInstanceConfig(visibleNode.data.config)) {
      throw new Error(
        "Cannot derive instance inputs: submodel occurrence "
        + visibleNode.id + " has malformed identity config",
      )
    }
    if (visibleNode.data.config.definitionId === definitionId) {
      occurrenceIds.add(visibleNode.id)
    }
  }

  const boundaryInputs = visibleEdges
    .filter((edge) => {
      if (!occurrenceIds.has(edge.target) || !edge.targetHandle?.startsWith("in__")) {
        return false
      }
      return publicInputPortIds.has(edge.targetHandle.slice("in__".length))
    })
    .map((edge) => {
      const sourceNode = visibleNodeMap[edge.source]
      if (!sourceNode) {
        throw new Error(
          "Cannot derive instance input name: source node " + edge.source + " is missing",
        )
      }
      return edgeInputName(edge, sourceNode, submodels)
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
      ? `Submodel "${resolution.definitionId}" has invalid metadata: ${resolution.reason}.`
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
          style={{ background: 'var(--bg-panel)', border: '1px solid var(--border)', color: 'var(--text-primary)' }}
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
  handleConfigUpdate: OnUpdateConfig
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
    definitionId,
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
          {definitionId && (
            <div className="text-[11px] truncate" style={{ color: 'var(--text-muted)' }}>
              in {definitionId}
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
          definitionId,
          submodels,
        })
        const instInputs = edges
          .filter((e) => e.target === node.id)
          .map((e) => {
            const srcNode = nodeMap[e.source]
            return {
              name: srcNode ? edgeInputName(e, srcNode, submodels) : (() => {
                throw new Error(`Cannot derive instance input name: source node ${e.source} is missing`)
              })(),
              label: srcNode ? srcNode.data.label : e.source,
            }
          })

        if (origInputs.length === 0 && instInputs.length === 0) return null

        const currentMapping = (config.inputMapping || {}) as Record<string, string>

        // Auto-initialise mapping if empty or stale. Mirrors the backend's
        // build_instance_mapping: exact match, then substring ONLY when the
        // pairing is unambiguous in both directions, then positional for
        // names with no substring evidence at all. A contested substring
        // pairing (e.g. originals rate/base_rate against sources
        // x_base_rate/x_rate) is deliberately left unmapped: the old greedy
        // pick could bind the frames crosswise, and the backend now refuses
        // to save/run an ambiguous mapping — the user must choose here.
        const autoMap: Record<string, string> = {}
        const usedInst = new Set<string>()
        for (const orig of origInputs) {
          const exact = instInputs.find((i) => i.name === orig && !usedInst.has(i.name))
          if (exact) { autoMap[orig] = exact.name; usedInst.add(exact.name) }
        }
        const subOrigs = origInputs.filter((o) => !autoMap[o])
        const subInsts = instInputs.filter((i) => !usedInst.has(i.name))
        const candByOrig = new Map(
          subOrigs.map((o) => [o, subInsts.filter((i) => i.name.includes(o))]),
        )
        const candCountByInst = new Map(
          subInsts.map((i) => [i.name, subOrigs.filter((o) => i.name.includes(o)).length]),
        )
        const ambiguousOrigs = new Set(
          subOrigs.filter((o) => {
            const cands = candByOrig.get(o) ?? []
            return cands.length > 0 && !(cands.length === 1 && candCountByInst.get(cands[0].name) === 1)
          }),
        )
        for (const o of subOrigs) {
          const cands = candByOrig.get(o) ?? []
          if (cands.length === 1 && candCountByInst.get(cands[0].name) === 1) {
            autoMap[o] = cands[0].name
            usedInst.add(cands[0].name)
          }
        }
        const remaining = instInputs.filter((i) => !usedInst.has(i.name))
        const unmapped = origInputs.filter((o) => !autoMap[o] && !ambiguousOrigs.has(o))
        unmapped.forEach((orig, idx) => {
          if (idx < remaining.length) autoMap[orig] = remaining[idx].name
        })

        const effectiveMap: Record<string, string> = {}
        const instNames = new Set(instInputs.map((i) => i.name))
        for (const orig of origInputs) {
          if (currentMapping[orig] && instNames.has(currentMapping[orig])) {
            effectiveMap[orig] = currentMapping[orig]
          } else {
            effectiveMap[orig] = autoMap[orig] || ""
          }
        }
        const unresolvedAmbiguous = [...ambiguousOrigs].filter((o) => !effectiveMap[o])

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
            {unresolvedAmbiguous.length > 0 && (
              <div className="flex items-start gap-1.5 px-2 py-1.5 rounded-md" style={{ background: 'var(--warning-soft)', border: '1px solid var(--warning-border)' }}>
                <AlertTriangle size={11} style={{ color: 'var(--warning-strong)' }} className="shrink-0 mt-0.5" />
                <span className="text-[10px] leading-relaxed" style={{ color: 'var(--warning-strong)' }}>
                  Name matching is ambiguous for {unresolvedAmbiguous.join(", ")} — several upstream
                  sources fit. Pick each one explicitly; saving and running are blocked until mapped.
                </span>
              </div>
            )}
            <div className="flex flex-col gap-1.5">
              {origInputs.map((orig) => (
                <div key={orig} className="flex items-center gap-2 px-2 py-1.5 rounded-md" style={{ background: 'var(--bg-panel)', border: '1px solid var(--border)' }}>
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
                      <option key={i.name} value={i.name}>{i.label}</option>
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
    sourceNode.data.nodeType === NODE_TYPES.API_INPUT
    && (edge.sourceHandle === null
      || edge.sourceHandle === undefined
      || !authoritativeSourceHandles(sourceNode).includes(edge.sourceHandle))

  return {
    sourceNodeId: edge.source,
    name,
    sourceLabel,
    edgeId: edge.id,
    ...(frameUnresolved ? { frameUnresolved: true } : {}),
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
        source.name,
        source.frameUnresolved === true,
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
  "_schemaWarnings",
  "_columnsSource",
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

type NodePanelHeaderProps = {
  nodeId: string
  label: string
  readOnly: boolean
  onRenameNode?: (nodeId: string, label: string) => Promise<OnUpdateConfigResult>
  showRefreshPreview: boolean
  refreshTitle: string
  onRefreshPreview?: () => void
  onClose: () => void
}

function NodePanelHeader({
  nodeId,
  label,
  readOnly,
  onRenameNode,
  showRefreshPreview,
  refreshTitle,
  onRefreshPreview,
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
        {showRefreshPreview && (
          <button
            onClick={onRefreshPreview}
            className="px-2 py-1 rounded shrink-0 transition-opacity flex items-center gap-1 text-[11px] font-medium hover:opacity-[0.85]"
            style={{ background: "var(--accent)", color: "var(--text-on-accent)" }}
            title={refreshTitle}
          >
            <RefreshCw size={11} />
            Refresh
          </button>
        )}
        <button
          data-testid="node-panel-close"
          onClick={onClose}
          className="node-close-btn p-1 rounded shrink-0 transition-colors"
          style={{ color: "var(--text-on-accent)" }}
          title="Close"
        >
          <X size={14} strokeWidth={2.5} />
        </button>
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
  canRepair: boolean
  onClose: () => void
  onRemoveUnavailableNode?: NodePanelProps["onRemoveUnavailableNode"]
}

function RecoveryNodePanel({
  node,
  availability,
  diagnostics,
  canRepair,
  onClose,
  onRemoveUnavailableNode,
}: RecoveryNodePanelProps) {
  const recoveryData = node.data as HauteNodeData
  const diagnosticIds = new Set(recoveryData._loadDiagnosticIds ?? [])
  const nodeDiagnostics = diagnostics.filter((diagnostic) => (
    diagnosticIds.has(diagnostic.diagnostic_id)
  ))
  const sourceLocation = recoveryData._sourceFile
    ? `${recoveryData._sourceFile}${
      recoveryData._sourceSpan ? `:${recoveryData._sourceSpan.start_line}` : ""
    }`
    : null
  const canRemove = availability === "unavailable"
    && canRepair
    && typeof recoveryData._sourceFile === "string"
    && typeof recoveryData._recoveryId === "string"
    && onRemoveUnavailableNode !== undefined

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
        <button
          data-testid="node-panel-close"
          onClick={onClose}
          className="node-close-btn shrink-0 rounded p-1 transition-colors"
          style={{ color: "var(--text-muted)" }}
          title="Close"
        >
          <X size={14} strokeWidth={2.5} />
        </button>
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
        {canRemove && (
          <button
            type="button"
            onClick={() => onRemoveUnavailableNode({
              sourceFile: recoveryData._sourceFile!,
              recoveryId: recoveryData._recoveryId!,
            })}
            className="w-full rounded px-3 py-2 text-[12px] font-semibold"
            style={{ color: "var(--danger-text)", background: "var(--danger-soft)", border: "1px solid var(--danger-border)" }}
          >
            Remove unavailable node
          </button>
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
}

function NodeEditorTabStrip({ visible, tabs, activeTab, onSelect }: NodeEditorTabStripProps) {
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
            {tab === "polars" ? "Polars" : tab}
          </button>
        )
      })}
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
  upstreamColumns: { name: string; dtype: string }[]
  availableColumns: { name: string; dtype: string }[]
  currentColumns: { name: string; dtype: string }[]
  onUpdateConfig: OnUpdateConfig
  configEditor: React.ReactNode
}

function NodeEditorBody({
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
  upstreamColumns,
  availableColumns,
  currentColumns,
  onUpdateConfig,
  configEditor,
}: NodeEditorBodyProps) {
  let editor = configEditor
  if (activeTab === "polars" && showPolarsTab) {
    editor = (
      <PolarsCodePanel
        config={config}
        onUpdate={onUpdateConfig}
        inputSources={nodeType === NODE_TYPES.DATA_INPUT ? [] : inputSources}
        onDeleteInput={onDeleteEdge}
        errorLine={errorLine}
        upstreamColumns={upstreamColumns}
        hint={POLARS_TAB_HINTS[nodeType] ?? null}
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
  onSwapEdgeJoinInputs,
  onRefreshPreview,
  dimmed,
  errorLine,
  previewRows,
  selectedPreviewLoading = false,
  readOnly = false,
  documentReadOnly = false,
  onRemoveUnavailableNode,
}: ActiveNodePanelProps) {
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
  const setModellingPane = useUIStore((s) => s.setModellingPane)
  const hasActiveTrainJob = useNodeResultsStore((s) => Boolean(s.trainJobs[node.id]))
  const cachedExploreResult = useNodeResultsStore((s) => s.exploreResults[node.id])
  const activeSource = useSettingsStore((s) => s.activeSource)
  const streamingChunkSize = useSettingsStore((s) => s.streamingChunkSize)
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
    const identity = buildExploreCacheIdentity({ node, allNodes, edges, submodels, preamble })
    return hashConfig({ graph: identity, source: activeSource })
  }, [node, allNodes, edges, submodels, preamble, activeSource])

  const loadPivotFilterMembers = useCallback(
    (field: string, search: string, signal: AbortSignal) => {
      if (!node) throw new Error("Explore node is unavailable.")
      return fetchExplorePivotMembers({
        graph: buildGraph(allNodes, edges, submodels, preamble),
        node_id: node.id,
        field,
        source: activeSource,
        search: search || undefined,
        streamingChunkSize,
        signal,
      })
    },
    // Keyed by the Explore cache identity hash (plus the fetch chunk size):
    // any render that keeps the same hash captures a graph snapshot whose
    // data-affecting parts are identical, so display-only pivot edits do not
    // churn the loader or reload members, while a hash change rebuilds the
    // closure with the new graph/source in the same render that re-keys the
    // member picker — there is no ref-update ordering to race against.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [exploreConfigHash, streamingChunkSize],
  )

  const handleConfigUpdate = useCallback<OnUpdateConfig>((keyOrUpdates, value) => {
    if (readOnly) {
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
  }, [config, documentReadOnly, node, onUpdateNode, readOnly])

  const handleConfigReplace = useCallback<OnReplaceConfig>((nextConfig) => {
    if (readOnly) {
      return {
        ok: false,
        error: documentReadOnly
          ? documentReadOnlyReason()
          : "This submodel instance is read-only.",
      }
    }
    if (!onUpdateNode) return { ok: false, error: "Node update handler is unavailable." }
    return onUpdateNode(node.id, clearCachedResultShape({ ...node.data, config: nextConfig }))
  }, [documentReadOnly, node, onUpdateNode, readOnly])

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
  const pivotColumns = useMemo(() => {
    const report = cachedExploreResult?.configHash === exploreConfigHash
      ? cachedExploreResult.result
      : null
    return report
      ? report.columns.map(({ name, dtype }) => ({ name, dtype }))
      : upstreamColumns
  }, [cachedExploreResult, exploreConfigHash, upstreamColumns])
  const recoveryAvailability = (node.data as HauteNodeData)._loadAvailability ?? "ready"
  if (recoveryAvailability !== "ready") {
    return (
      <RecoveryNodePanel
        node={node}
        availability={recoveryAvailability}
        diagnostics={documentDiagnostics}
        canRepair={canRepair}
        onClose={onClose}
        onRemoveUnavailableNode={onRemoveUnavailableNode}
      />
    )
  }

  const isInstance = !!config.instanceOf
  const nodeType = effectiveNodeType(node)
  const isKnownNodeType = Object.hasOwn(NODE_TYPE_META, nodeType)
  const showColumnsTab = isKnownNodeType && !isInstance && !NO_COLUMNS_TAB.has(nodeType)
  const showPolarsTab = isKnownNodeType && !isInstance && POLARS_TAB_TYPES.has(nodeType)
  const showExplorePanes = isKnownNodeType && !isInstance && nodeType === NODE_TYPES.EXPLORE
  const showRefreshPreview = !!onRefreshPreview && !NO_REFRESH_PREVIEW.has(nodeType)
  const refreshTitle = showExplorePanes ? "Refresh Explore outputs" : "Refresh preview"
  const activeExplorePane = showExplorePanes ? rememberedExplorePane ?? "code" : "code"
  const algorithm = typeof config.algorithm === "string" ? config.algorithm.toLowerCase() : ""
  const showModellingPanes = isKnownNodeType && !isInstance && nodeType === NODE_TYPES.MODELLING && (algorithm === "catboost" || algorithm === "glm")
  const activeModellingPane = showModellingPanes ? rememberedModellingPane ?? "target" : "target"
  const modellingTabs = MODELLING_PANES.map((pane) => ({
    ...pane,
    indicator: pane.key === "train" && hasActiveTrainJob
      ? { kind: "active" as const, label: "Training is running" }
      : undefined,
  }))

  const accentColor = NODE_TYPE_META[nodeType as NodeTypeValue]?.color ?? "var(--accent)"
  const configEditor = !isKnownNodeType ? (
    <UnknownNodeTypeDiagnostic nodeType={nodeType} config={config} />
  ) : isInstance ? (
    <InstancePanel
      node={node}
      config={config}
      nodeMap={nodeMap}
      handleConfigUpdate={handleConfigUpdate}
    />
  ) : (
    <NodeConfigEditor
      nodeType={nodeType as NodeTypeValue}
      config={config}
      configWithNodeId={configWithNodeId}
      node={node}
      onUpdateConfig={handleConfigUpdate}
      onReplaceConfig={handleConfigReplace}
      inputSources={inputSources}
      upstreamColumns={upstreamColumns}
      pivotColumns={pivotColumns}
      activeExplorePane={activeExplorePane}
      activeModellingPane={activeModellingPane}
      onDeleteEdge={onDeleteEdge}
      onSwapEdgeJoinInputs={onSwapEdgeJoinInputs}
      onShowPivots={() => setExplorePane(node.id, "pivots")}
      errorLine={errorLine}
      previewRows={previewRows}
      selectedPreviewLoading={selectedPreviewLoading}
      loadPivotFilterMembers={loadPivotFilterMembers}
      exploreConfigHash={exploreConfigHash}
      reservedApiInputFrameLabels={reservedApiInputFrameLabelSet}
      accentColor={accentColor}
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
        readOnly={readOnly}
        onRenameNode={onRenameNode}
        showRefreshPreview={showRefreshPreview}
        refreshTitle={refreshTitle}
        onRefreshPreview={onRefreshPreview}
        onClose={onClose}
      />

      <NodeEditorTabStrip
        visible={showColumnsTab || showPolarsTab}
        tabs={editorTabs}
        activeTab={activeTab}
        onSelect={selectTab}
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

      {!isInstance && !showExplorePanes && (
        <SchemaWarningBanner
          warnings={schemaWarnings}
          dismissedSignature={dismissedWarningSignature}
          onDismiss={dismissWarning}
          onRefreshPreview={onRefreshPreview}
        />
      )}

      <NodeEditorBody
        documentReadOnly={documentReadOnly}
        readOnly={readOnly}
        config={config}
        activeTab={activeTab}
        showPolarsTab={showPolarsTab}
        showColumnsTab={showColumnsTab}
        nodeType={nodeType}
        inputSources={inputSources}
        onDeleteEdge={onDeleteEdge}
        errorLine={errorLine}
        upstreamColumns={upstreamColumns}
        availableColumns={availableColumns}
        currentColumns={currentColumns}
        onUpdateConfig={handleConfigUpdate}
        configEditor={configEditor}
      />
    </PanelShell>
  )
}
