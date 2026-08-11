import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { X, Link2, AlertTriangle, RefreshCw, Lock } from "lucide-react"
import { fetchExplorePivotMembers } from "../api/client"
import { NODE_TYPES, NODE_TYPE_META } from "../utils/nodeTypes"
import type { NodeTypeValue } from "../utils/nodeTypes"
import { sanitizeName } from "../utils/sanitizeName"
import { apiInputFrameLabels, edgeInputName } from "../utils/apiInputPorts"
import {
  TransformEditor,
  EdgeJoinEditor,
  ExploreCodeEditor,
  ExploreOverviewConfig,
  ExplorePivotsConfig,
  ExploreChartsConfig,
  ModelScoreEditor,
  BandingEditor,
  RatingStepEditor,
  OutputEditor,
  ExternalFileEditor,
  ApiInputEditor,
  LiveSwitchEditor,
  DataInputEditor,
  DataOutputEditor,
  ScenarioExpanderEditor,
  OptimiserApplyEditor,
  ConstantEditor,
  SubmodelEditor,
  ColumnsTab,
  ModellingConfig,
  OptimiserConfig,
  PolarsCodePanel,
  LazyEditorBoundary,
} from "./LazyNodeEditors"
import type { InputSource, SimpleNode, SimpleEdge, OnUpdateConfig, OnUpdateConfigResult, OnReplaceConfig } from "./editors"
import {
  effectiveNodeType,
  isSubmodelDefinition,
  isSubmodelInstanceConfig,
  type HauteNodeData,
} from "../types/node"
import useUIStore, { type ExplorePane, type ModellingPane } from "../stores/useUIStore"
import useNodeResultsStore from "../stores/useNodeResultsStore"
import useSettingsStore from "../stores/useSettingsStore"
import PanelShell from "./PanelShell"
import PreviewPanelTabs from "./PreviewPanelTabs"
import { useGraph } from "./useGraph"
import { buildGraph } from "../utils/buildGraph"
import { CommittedTextField } from "../components/form"

type NodePanelProps = {
  node: SimpleNode | null
  onClose: () => void
  onUpdateNode?: (id: string, data: Record<string, unknown>) => OnUpdateConfigResult
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
}

type NodePanelTab = "config" | "polars" | "columns"

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
      || !apiInputFrameLabels(sourceNode.data.config).includes(edge.sourceHandle))

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

// ─── NodePanel ────────────────────────────────────────────────────

export default function NodePanel({
  node,
  onClose,
  onUpdateNode,
  onDeleteEdge,
  onSwapEdgeJoinInputs,
  onRefreshPreview,
  dimmed,
  errorLine,
  previewRows,
  selectedPreviewLoading = false,
  readOnly = false,
}: NodePanelProps) {
  const { allNodes, edges, submodels, preamble } = useGraph()
  const config = useMemo(() => (node?.data.config || {}) as Record<string, unknown>, [node?.data.config])
  const [activeTab, setActiveTab] = useState<NodePanelTab>("config")
  const [labelUpdateError, setLabelUpdateError] = useState<string | null>(null)
  const rememberedExplorePane = useUIStore((s) => node?.id ? s.explorePanes[node.id] : undefined)
  const setExplorePane = useUIStore((s) => s.setExplorePane)
  const setExplorePreviewPane = useUIStore((s) => s.setExplorePreviewPane)
  const rememberedModellingPane = useUIStore((s) => node?.id ? s.modellingPanes[node.id] : undefined)
  const setModellingPane = useUIStore((s) => s.setModellingPane)
  const hasActiveTrainJob = useNodeResultsStore((s) => node?.id ? Boolean(s.trainJobs[node.id]) : false)
  const activeSource = useSettingsStore((s) => s.activeSource)
  const streamingChunkSize = useSettingsStore((s) => s.streamingChunkSize)

  // Keep config and node in refs so handleConfigUpdate never captures stale values
  const configRef = useRef(config)
  const nodeRef = useRef(node)
  useEffect(() => { configRef.current = config }, [config])
  useEffect(() => { nodeRef.current = node }, [node])
  useEffect(() => { setLabelUpdateError(null) }, [node?.id, node?.data.label])
  useEffect(() => { setActiveTab("config") }, [node?.id])

  const loadPivotFilterMembers = useCallback(
    (field: string, search: string, signal: AbortSignal) => {
      const currentNode = nodeRef.current
      if (!currentNode) throw new Error("Explore node is unavailable.")
      return fetchExplorePivotMembers({
        graph: buildGraph(allNodes, edges, submodels, preamble),
        node_id: currentNode.id,
        field,
        source: activeSource,
        search: search || undefined,
        streamingChunkSize,
        signal,
      })
    },
    [activeSource, allNodes, edges, preamble, streamingChunkSize, submodels],
  )

  // Bundle 3b — dismissal state for the stale-columns banner.
  // Stored as the warning-signature the user dismissed, so the banner
  // reappears whenever the warning content (columns / statuses / count)
  // changes.  Reset on node switch so dismissals don't bleed across
  // nodes while the panel stays mounted.
  const [dismissedStaleWarningSig, setDismissedStaleWarningSig] = useState<string | null>(null)
  useEffect(() => { setDismissedStaleWarningSig(null) }, [node?.id])

  const handleConfigUpdate = useCallback<OnUpdateConfig>((keyOrUpdates, value) => {
    if (readOnly) {
      return { ok: false, error: "This submodel instance is read-only." }
    }
    const currentNode = nodeRef.current
    if (!currentNode || !onUpdateNode) {
      return { ok: false, error: "Node update handler is unavailable." }
    }
    const currentConfig = configRef.current
    const newConfig = typeof keyOrUpdates === "string"
      ? { ...currentConfig, [keyOrUpdates]: value }
      : { ...currentConfig, ...keyOrUpdates }
    const changedKeys =
      typeof keyOrUpdates === "string"
        ? [keyOrUpdates]
        : Object.keys(keyOrUpdates)
    const selectionOnlyUpdate =
      changedKeys.length === 1 && changedKeys[0] === "selected_columns"
    return onUpdateNode(
      currentNode.id,
      clearCachedResultShape(
        { ...currentNode.data, config: newConfig },
        { preserveAvailableColumns: selectionOnlyUpdate },
      ),
    )
  }, [onUpdateNode, readOnly])

  const handleConfigReplace = useCallback<OnReplaceConfig>((nextConfig) => {
    if (readOnly) {
      return { ok: false, error: "This submodel instance is read-only." }
    }
    const currentNode = nodeRef.current
    if (!currentNode || !onUpdateNode) return { ok: false, error: "Node update handler is unavailable." }
    return onUpdateNode(currentNode.id, clearCachedResultShape({ ...currentNode.data, config: nextConfig }))
  }, [onUpdateNode, readOnly])

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
  const upstreamInputSourceSig = upstreamInputSourceSignature(upstreamEdges, nodeMap, submodels)
  const inputSources: InputSource[] = useMemo(() => {
    if (!selectedNodeId) return []
    return upstreamEdges.map((edge) => inputSourceForEdge(edge, nodeMap, submodels))
    // Keyed by selected node plus each upstream edge's source/display identity:
    // source labels, source handles, resolved frame labels, and resolution
    // state can all change while the edge id stays the same.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedNodeId, upstreamInputSourceSig, submodels])
  const upstreamSchemaSignature = upstreamColumnsSignature(upstreamEdges, nodeMap)
  const upstreamColumns = useMemo(() => {
    if (!selectedNodeId) return []
    return collectColumnsFromEdges(upstreamEdges, nodeMap)
    // Intentionally keyed by selected node plus upstream schema content.
    // Selected-node config/label edits rebuild nodeMap but do not change the
    // upstream schema, so they should preserve this array identity.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedNodeId, upstreamSchemaSignature])
  if (!node) return null

  const isInstance = !!config.instanceOf
  const nodeType = effectiveNodeType(node)
  const isKnownNodeType = Object.hasOwn(NODE_TYPE_META, nodeType)
  const showColumnsTab = isKnownNodeType && !isInstance && !NO_COLUMNS_TAB.has(nodeType)
  const showPolarsTab = isKnownNodeType && !isInstance && POLARS_TAB_TYPES.has(nodeType)
  const showExplorePanes = isKnownNodeType && !isInstance && nodeType === NODE_TYPES.EXPLORE
  const showRefreshPreview = !!onRefreshPreview && !NO_REFRESH_PREVIEW.has(nodeType)
  const refreshTitle = showExplorePanes ? "Refresh Explore outputs" : "Refresh preview"
  const activeExplorePane = showExplorePanes ? rememberedExplorePane ?? "code" : "code"
  const activeExplorePaneMeta = EXPLORE_PANES.find((pane) => pane.key === activeExplorePane) ?? EXPLORE_PANES[0]
  const algorithm = typeof config.algorithm === "string" ? config.algorithm.toLowerCase() : ""
  const showModellingPanes = isKnownNodeType && !isInstance && nodeType === NODE_TYPES.MODELLING && (algorithm === "catboost" || algorithm === "glm")
  const activeModellingPane = showModellingPanes ? rememberedModellingPane ?? "target" : "target"
  const modellingTabs = MODELLING_PANES.map((pane) => ({
    ...pane,
    indicator: pane.key === "train" && hasActiveTrainJob
      ? { kind: "active" as const, label: "Training is running" }
      : undefined,
  }))

  // ── Render the right editor based on nodeType ──

  const accentColor = NODE_TYPE_META[nodeType as NodeTypeValue]?.color ?? "var(--accent)"

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

      case NODE_TYPES.DATA_INPUT:
        return <DataInputEditor config={config} onUpdate={handleConfigUpdate} onReplaceConfig={handleConfigReplace} accentColor={accentColor} errorLine={errorLine} />

      case NODE_TYPES.DATA_OUTPUT:
        return <DataOutputEditor config={config} onUpdate={handleConfigUpdate} onReplaceConfig={handleConfigReplace} nodeId={node.id} accentColor={accentColor} />

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
            {activeExplorePane === "pivots" && (
              <ExplorePivotsConfig
                config={config}
                onUpdate={handleConfigUpdate}
                upstreamColumns={upstreamColumns}
                onUpdatePreview={() => setExplorePreviewPane(node.id, "pivots")}
                loadFilterMembers={loadPivotFilterMembers}
              />
            )}
            {activeExplorePane === "charts" && (
              <ExploreChartsConfig
                config={config}
                onUpdate={handleConfigUpdate}
                nodeId={node.id}
                onShowPivots={() => setExplorePane(node.id, "pivots")}
              />
            )}
          </div>
        )

      case NODE_TYPES.EXTERNAL_FILE:
        return <ExternalFileEditor config={config} onUpdate={handleConfigUpdate} inputSources={inputSources} onDeleteInput={onDeleteEdge} errorLine={errorLine} accentColor={accentColor} />

      case NODE_TYPES.OUTPUT:
        return <OutputEditor config={config} onUpdate={handleConfigUpdate} nodeId={node.id} />

      case NODE_TYPES.BANDING:
        return (
          <BandingEditor
            config={config}
            onUpdate={handleConfigUpdate}
            inputSources={inputSources}
            onDeleteInput={onDeleteEdge}
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
            upstreamColumns={upstreamColumns}
          />
        )

      case NODE_TYPES.RATING_STEP:
        return (
          <RatingStepEditor
            config={config}
            onUpdate={handleConfigUpdate}
            inputSources={inputSources}
            onDeleteInput={onDeleteEdge}
            upstreamColumns={upstreamColumns}
            previewRows={previewRows}
            accentColor={accentColor}
            errorLine={errorLine}
            nodeId={node.id}
          />
        )

      case NODE_TYPES.MODEL_SCORE:
        return <ModelScoreEditor config={config} onUpdate={handleConfigUpdate} inputSources={inputSources} onDeleteInput={onDeleteEdge} errorLine={errorLine} accentColor={accentColor} />

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
            activePane={activeModellingPane}
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
            errorLine={errorLine}
            upstreamColumns={upstreamColumns}
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
        return <SubmodelEditor config={config} accentColor={accentColor} />

      default:
        return null
    }
  }

  const availableColumns = ((node.data as Record<string, unknown>)?._availableColumns as { name: string; dtype: string }[]) || []
  const currentColumns = ((node.data as Record<string, unknown>)?._columns as { name: string; dtype: string }[]) || []
  const editorTabs: NodePanelTab[] = [
    "config",
    ...(showPolarsTab ? ["polars" as const] : []),
    ...(showColumnsTab ? ["columns" as const] : []),
  ]

  return (
    <PanelShell testId="node-panel" style={{ opacity: dimmed ? 0.6 : 1, transition: 'opacity 150ms' }}>
      <div className="px-3 py-2.5 shrink-0" style={{ borderBottom: '1px solid var(--border)' }}>
        <div className="flex items-center gap-2">
          <CommittedTextField
            data-testid="node-panel-label-input"
            type="text"
            value={node.data.label}
            disabled={readOnly}
            onCommit={(v) => {
              if (!onUpdateNode) return
              const result = onUpdateNode(node.id, { ...node.data, label: v })
              setLabelUpdateError(result.ok ? null : result.error)
            }}
            className="node-label-input flex-1 min-w-0 px-2 py-1 text-[13px] font-semibold border border-transparent rounded-md focus:outline-none bg-transparent"
            style={{ color: 'var(--text-primary)', borderColor: 'transparent' }}
          />
          {readOnly && (
            <span
              data-testid="node-panel-readonly"
              className="inline-flex items-center gap-1 text-[10px] font-semibold uppercase tracking-[0.08em]"
              style={{ color: "var(--text-muted)" }}
            ><Lock size={11} aria-hidden="true" />Read-only</span>
          )}
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
        {labelUpdateError && (
          <p
            role="alert"
            data-testid="node-panel-label-error"
            className="mt-1 px-2 text-[11px]"
            style={{ color: 'var(--danger)' }}
          >
            {labelUpdateError}
          </p>
        )}
      </div>

      {/* Tab bar — only show when Columns tab is available.  Hover
          background is applied via Tailwind only for the INACTIVE tab
          so the accent-soft background of the active tab doesn't
          flicker on mouseover.  Inactive tabs deliberately omit an
          inline `background` so the Tailwind `hover:` rule can apply
          (inline styles would otherwise win over class rules). */}
      {(showColumnsTab || showPolarsTab) && (
        <div className="flex shrink-0" style={{ borderBottom: '1px solid var(--border)' }}>
          {editorTabs.map((tab) => {
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
                {tab === "polars" ? "Polars" : tab}
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

      <div
        className="flex-1 min-h-0 overflow-y-auto"
        data-testid="node-panel-editor"
        inert={readOnly ? true : undefined}
        aria-readonly={readOnly}
      >
        <LazyEditorBoundary>
          {activeTab === "polars" && showPolarsTab ? (
            <PolarsCodePanel
              config={config}
              onUpdate={handleConfigUpdate}
              inputSources={nodeType === NODE_TYPES.DATA_INPUT ? [] : inputSources}
              onDeleteInput={onDeleteEdge}
              errorLine={errorLine}
              upstreamColumns={upstreamColumns}
              hint={POLARS_TAB_HINTS[nodeType] ?? null}
            />
          ) : activeTab === "columns" && showColumnsTab ? (
            <ColumnsTab
              config={config}
              onUpdate={handleConfigUpdate}
              availableColumns={availableColumns}
              columns={currentColumns}
            />
          ) : renderEditor()}
        </LazyEditorBoundary>
      </div>
    </PanelShell>
  )
}
