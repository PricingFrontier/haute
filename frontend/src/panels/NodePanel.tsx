import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { X, Link2, AlertTriangle, RefreshCw } from "lucide-react"
import { NODE_TYPES, NODE_TYPE_META } from "../utils/nodeTypes"
import type { NodeTypeValue } from "../utils/nodeTypes"
import { sanitizeName } from "../utils/sanitizeName"
import ModellingConfig from "./ModellingConfig"
import OptimiserConfig from "./OptimiserConfig"
import {
  DataSourceEditor,
  TransformEditor,
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
} from "./editors"
import type { InputSource, SimpleNode, SimpleEdge } from "./editors"
import ColumnsTab from "./editors/ColumnsTab"
import GroupedColumnsTab from "./editors/GroupedColumnsTab"
import PanelShell from "./PanelShell"
import { useGraph } from "./useGraph"

// Re-export types (preserve public API for App.tsx)
export type { SimpleNode, SimpleEdge } from "./editors"

type NodePanelProps = {
  node: SimpleNode | null
  onClose: () => void
  onUpdateNode?: (id: string, data: Record<string, unknown>) => void
  onDeleteEdge?: (edgeId: string) => void
  onRefreshPreview?: () => void
  /** True when showing last-selected node while nothing is actively selected */
  dimmed?: boolean
  /** 1-based line number of the error in user code, if any */
  errorLine?: number | null
  /** Preview rows from the current node's preview data (input columns pass through) */
  previewRows?: Record<string, unknown>[]
}

// ─── Node types that do NOT show the Columns tab ──
// Output already has its own field selection; submodels/ports are placeholders;
// modelling nodes are sink-only (no outputs).
const NO_COLUMNS_TAB = new Set<string>([
  NODE_TYPES.OUTPUT,
  NODE_TYPES.SUBMODEL,
  NODE_TYPES.MODELLING,
])

// ─── Instance sub-panel (kept inline — it references multiple node-level concerns) ──

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
  const { edges } = useGraph()
  const origId = config.instanceOf as string
  // Fail loud (#84): a broken reference must surface in the ErrorBoundary
  // rather than rendering the stringified id as a silent fallback.
  const orig = nodeMap[origId]
  if (!orig) {
    throw new Error(
      `InstancePanel: referenced original node "${origId}" not found in graph. ` +
        `Either the original was deleted or the instanceOf id is stale; ` +
        `fix the node's config or recreate the instance.`,
    )
  }
  return (
    <div className="px-4 py-3 flex flex-col gap-3">
      <div className="flex items-center gap-2 px-3 py-2 rounded-lg" style={{ background: 'var(--accent-soft)', border: '1px solid var(--text-accent-line)' }}>
        <Link2 size={13} style={{ color: 'var(--accent)' }} className="shrink-0" />
        <div className="min-w-0">
          <div className="text-[11px] font-bold uppercase tracking-[0.08em]" style={{ color: 'var(--accent)' }}>Instance of</div>
          <div className="text-[13px] font-semibold truncate" style={{ color: 'var(--text-primary)' }}>
            {orig.data.label}
          </div>
        </div>
      </div>
      <p className="text-[11px] leading-relaxed" style={{ color: 'var(--text-muted)' }}>
        This node uses the same logic as the original. To edit the code or config, select the original node. Changes will automatically apply to all instances.
      </p>

      {/* Input Mapping */}
      {(() => {
        const origInputs = edges
          .filter((e) => e.target === origId)
          .map((e) => {
            const srcNode = nodeMap[e.source]
            return srcNode ? sanitizeName(srcNode.data.label) : e.source
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

/** Collect upstream columns from all nodes feeding into `nodeId`. */
function collectUpstreamColumns(nodeId: string, edges: SimpleEdge[], nodeMap: Record<string, SimpleNode>): { name: string; dtype: string }[] {
  const cols: { name: string; dtype: string }[] = []
  const seen = new Set<string>()
  edges.filter(e => e.target === nodeId).forEach(e => {
    const src = nodeMap[e.source]
    const srcCols = (src?.data as Record<string, unknown>)?._columns as { name: string; dtype: string }[] | undefined
    if (srcCols) srcCols.forEach(c => { if (!seen.has(c.name)) { seen.add(c.name); cols.push(c) } })
  })
  return cols
}

/** Check if any upstream node is an api_input type. */
function hasUpstreamApiInput(nodeId: string, edges: SimpleEdge[], nodeMap: Record<string, SimpleNode>): boolean {
  return edges
    .filter(e => e.target === nodeId)
    .some(e => nodeMap[e.source]?.data?.nodeType === NODE_TYPES.API_INPUT)
}

// ─── NodePanel ────────────────────────────────────────────────────

export default function NodePanel({ node, onClose, onUpdateNode, onDeleteEdge, onRefreshPreview, dimmed, errorLine, previewRows }: NodePanelProps) {
  const { allNodes, edges } = useGraph()
  const config = useMemo(() => (node?.data.config || {}) as Record<string, unknown>, [node?.data.config])
  const [activeTab, setActiveTab] = useState<"config" | "columns">("config")

  // Keep config and node in refs so handleConfigUpdate never captures stale values
  const configRef = useRef(config)
  const nodeRef = useRef(node)
  useEffect(() => { configRef.current = config }, [config])
  useEffect(() => { nodeRef.current = node }, [node])

  const handleConfigUpdate = useCallback((keyOrUpdates: string | Record<string, unknown>, value?: unknown) => {
    const currentNode = nodeRef.current
    if (!currentNode || !onUpdateNode) return
    const currentConfig = configRef.current
    const newConfig = typeof keyOrUpdates === "string"
      ? { ...currentConfig, [keyOrUpdates]: value }
      : { ...currentConfig, ...keyOrUpdates }
    onUpdateNode(currentNode.id, { ...currentNode.data, config: newConfig })
  }, [onUpdateNode])

  const configWithNodeId = useMemo(
    () => ({ ...config, _nodeId: node?.id ?? "" }),
    [config, node?.id]
  )

  // Compute input sources (must be before early return to satisfy hook ordering rules)
  const nodeMap = useMemo(() => Object.fromEntries(allNodes.map((n) => [n.id, n])), [allNodes])
  const inputSources: InputSource[] = useMemo(() => {
    if (!node) return []
    return edges
      .filter((e) => e.target === node.id)
      .map((e) => ({
        varName: sanitizeName(nodeMap[e.source]?.data.label || e.source),
        sourceLabel: nodeMap[e.source]?.data.label || e.source,
        edgeId: e.id,
      }))
  }, [edges, node, nodeMap])

  if (!node) return null

  const isInstance = !!config.instanceOf
  const nodeType = node.data.nodeType
  const showColumnsTab = !isInstance && !NO_COLUMNS_TAB.has(nodeType)

  // ── Render the right editor based on nodeType ──

  const accentColor = NODE_TYPE_META[nodeType as NodeTypeValue]?.color ?? "var(--accent)"

  const renderEditor = () => {
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
        return <ApiInputEditor config={config} onUpdate={handleConfigUpdate} accentColor={accentColor} />

      case NODE_TYPES.LIVE_SWITCH:
        return <LiveSwitchEditor config={config} onUpdate={handleConfigUpdate} inputSources={inputSources} accentColor={accentColor} />

      case NODE_TYPES.DATA_SOURCE:
        return <DataSourceEditor config={config} onUpdate={handleConfigUpdate} onRefreshPreview={onRefreshPreview} accentColor={accentColor} errorLine={errorLine} />

      case NODE_TYPES.DATA_SINK:
        return <SinkEditor config={config} onUpdate={handleConfigUpdate} nodeId={node.id} accentColor={accentColor} />

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
            upstreamColumns={collectUpstreamColumns(node.id, edges, nodeMap)}
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
            upstreamColumns={collectUpstreamColumns(node.id, edges, nodeMap)}
            errorLine={errorLine}
          />
        )

      case NODE_TYPES.RATING_STEP:
        return <RatingStepEditor config={config} onUpdate={handleConfigUpdate} inputSources={inputSources} onDeleteInput={onDeleteEdge} accentColor={accentColor} />

      case NODE_TYPES.MODEL_SCORE:
        return <ModelScoreEditor config={config} onUpdate={handleConfigUpdate} inputSources={inputSources} onDeleteInput={onDeleteEdge} errorLine={errorLine} accentColor={accentColor} />

      case NODE_TYPES.MODELLING: {
        const upstreamCols = collectUpstreamColumns(node.id, edges, nodeMap)
        // Modelling is a pass-through -- its own _columns (set by preview) ARE the upstream columns
        const effectiveCols = upstreamCols.length > 0
          ? upstreamCols
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
        const upstreamCols = collectUpstreamColumns(node.id, edges, nodeMap)
        const effectiveCols = upstreamCols.length > 0
          ? upstreamCols
          : ((node.data as Record<string, unknown>)?._columns as { name: string; dtype: string }[] | undefined) || []
        return (
          <OptimiserConfig
            config={configWithNodeId}
            onUpdate={handleConfigUpdate}
            upstreamColumns={effectiveCols}
            accentColor={accentColor}
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
            upstreamColumns={collectUpstreamColumns(node.id, edges, nodeMap)}
            hasApiInputUpstream={hasUpstreamApiInput(node.id, edges, nodeMap)}
          />
        )

      case NODE_TYPES.SUBMODEL:
        return <SubmodelEditor config={config} accentColor={accentColor} />

      default:
        // Fallback: show raw config
        if (Object.keys(config).length > 0) {
          return (
            <div className="px-4 py-3">
              <label className="text-[11px] font-bold uppercase tracking-[0.08em]" style={{ color: 'var(--text-muted)' }}>Config</label>
              {Object.entries(config).map(([key, value]) => (
                <div key={key} className="mt-1.5 flex items-center gap-2">
                  <span className="text-xs font-mono" style={{ color: 'var(--text-muted)' }}>{key}:</span>
                  <span className="text-xs font-mono truncate" style={{ color: 'var(--text-primary)' }}>{String(value)}</span>
                </div>
              ))}
            </div>
          )
        }
        return null
    }
  }

  const availableColumns = ((node.data as Record<string, unknown>)?._availableColumns as { name: string; dtype: string }[]) || []
  const currentColumns = ((node.data as Record<string, unknown>)?._columns as { name: string; dtype: string }[]) || []

  return (
    <PanelShell style={{ opacity: dimmed ? 0.6 : 1, transition: 'opacity 150ms' }}>
      <div className="px-3 py-2.5 flex items-center gap-2 shrink-0" style={{ borderBottom: '1px solid var(--border)' }}>
        <input
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
        {onRefreshPreview && (
          <button
            onClick={onRefreshPreview}
            className="px-2 py-1 rounded shrink-0 transition-opacity flex items-center gap-1 text-[11px] font-medium hover:opacity-[0.85]"
            style={{ background: 'var(--accent)', color: 'var(--text-on-accent)' }}
            title="Refresh preview"
          >
            <RefreshCw size={11} />
            Refresh
          </button>
        )}
        <button onClick={onClose} className="node-close-btn p-1 rounded shrink-0 transition-colors" style={{ color: 'var(--text-on-accent)' }}
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

      {/* Schema warnings for non-instance nodes */}
      {!isInstance && (() => {
        const warnings = (node.data._schemaWarnings as { column: string; status: string }[]) || []
        if (warnings.length === 0) return null
        return (
          <div className="px-4 py-2 shrink-0" style={{ borderBottom: '1px solid var(--border)' }}>
            <div className="flex flex-col gap-1.5 px-3 py-2 rounded-lg" style={{ background: 'var(--warning-soft)', border: '1px solid var(--warning-border)' }}>
              <div className="flex items-center gap-1.5">
                <AlertTriangle size={11} style={{ color: 'var(--warning-strong)' }} className="shrink-0" />
                <span className="text-[11px] font-bold uppercase tracking-[0.08em]" style={{ color: 'var(--warning-strong)' }}>
                  Stale columns ({warnings.length})
                </span>
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

      <div className="flex-1 min-h-0 overflow-y-auto">
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
        ) : (
          renderEditor()
        )}
      </div>
    </PanelShell>
  )
}
