import { useState, useCallback, useEffect, useMemo } from "react"
import { Loader2, ChevronDown, ChevronRight, AlertTriangle, Plus, X, Target, Layers, RefreshCw } from "lucide-react"
import type { SimpleNode, SimpleEdge, OnUpdateConfig } from "./editors"
import { solveOptimiser } from "../api/client"
import { useDataInputColumns } from "../hooks/useDataInputColumns"
import { useConstraintHandlers } from "../hooks/useConstraintHandlers"
import type { SolveResult } from "./OptimiserPreview"
import { NODE_TYPES } from "../utils/nodeTypes"
import useNodeResultsStore, { hashConfig } from "../stores/useNodeResultsStore"
import useSettingsStore from "../stores/useSettingsStore"
import { formatElapsed } from "../utils/formatValue"
import { configField, safeParseFloat, safeParseInt } from "../utils/configField"
import { withAlpha } from "../utils/color"
import { extractBandingLevelsForNode } from "../utils/banding"
import { buildGraph } from "../utils/buildGraph"

// ─── Banding factor extraction ───

type BandingNodeInfo = { id: string; label: string }
type InputNodeInfo = { id: string; label: string; nodeType: string }

/** List all nodes that are direct inputs to a given node. */
function findInputNodes(
  nodeId: string,
  allNodes: SimpleNode[],
  edges: SimpleEdge[],
): InputNodeInfo[] {
  const sourceIds = edges.filter(e => e.target === nodeId).map(e => e.source)
  const nodeMap = new Map(allNodes.map(n => [n.id, n]))
  return sourceIds
    .map(id => nodeMap.get(id))
    .filter((n): n is SimpleNode => !!n)
    .map(n => ({ id: n.id, label: n.data.label || n.id, nodeType: n.data.nodeType }))
}

/** List banding nodes among the inputs to a given node. */
function findInputBandingNodes(
  nodeId: string,
  allNodes: SimpleNode[],
  edges: SimpleEdge[],
): BandingNodeInfo[] {
  return findInputNodes(nodeId, allNodes, edges)
    .filter(n => n.nodeType === NODE_TYPES.BANDING)
    .map(({ id, label }) => ({ id, label }))
}

type OptimiserConfigProps = {
  config: Record<string, unknown>
  onUpdate: OnUpdateConfig
  upstreamColumns?: { name: string; dtype: string }[]
  allNodes: SimpleNode[]
  edges: SimpleEdge[]
  submodels?: Record<string, unknown>
  accentColor: string
}

const CONSTRAINT_TYPES = [
  { value: "min", label: "Min (relative)" },
  { value: "max", label: "Max (relative)" },
  { value: "min_abs", label: "Min (absolute)" },
  { value: "max_abs", label: "Max (absolute)" },
]

export default function OptimiserConfig({ config, onUpdate, allNodes, edges, submodels, accentColor }: OptimiserConfigProps) {
  // ── Store-backed state (survives panel unmount) ──
  const nodeId = config._nodeId as string
  const solveJob = useNodeResultsStore((s) => s.solveJobs[nodeId])
  const cachedResult = useNodeResultsStore((s) => s.solveResults[nodeId])
  const startSolveJob = useNodeResultsStore((s) => s.startSolveJob)

  // ── Local UI state (cheap, ok to recreate) ──
  const [submitting, setSubmitting] = useState(false)

  const solving = submitting || !!solveJob
  const solveProgress = solveJob?.progress ?? null
  const solveError = solveJob?.error ?? null
  const solveResult: SolveResult | null = cachedResult?.result ?? null
  // Staleness detection: has config changed since last solve?
  const currentConfigHash = useMemo(() => hashConfig(config), [config])
  const isStale = !!cachedResult && cachedResult.configHash !== currentConfigHash
  // Collapse state from UI store (persisted)
  const advancedOpen = useSettingsStore((s) => s.isSectionOpen("optimiser.advanced"))
  const mlflowOpen = useSettingsStore((s) => s.isSectionOpen("optimiser.mlflow"))
  const toggleAdvanced = useSettingsStore((s) => s.toggleSection)

  const mode = configField(config, "mode", "online")
  const factorColumns = configField<string[][]>(config, "factor_columns", [])
  const objective = configField(config, "objective", "")
  const constraints = configField<Record<string, Record<string, number>>>(config, "constraints", {})
  const quoteId = configField(config, "quote_id", "quote_id")
  const scenarioIndex = configField(config, "scenario_index", "scenario_index")
  const scenarioValue = configField(config, "scenario_value", "scenario_value")
  const maxIter = configField(config, "max_iter", 50)
  const tolerance = configField(config, "tolerance", 1e-6)
  const chunkSize = configField(config, "chunk_size", 500_000)
  const recordHistory = configField(config, "record_history", false)
  const maxCdIterations = configField(config, "max_cd_iterations", 10)
  const cdTolerance = configField(config, "cd_tolerance", 1e-3)
  const frontierMin = configField(config, "frontier_min", 0.80)
  const frontierMax = configField(config, "frontier_max", 1.10)
  const frontierSteps = configField(config, "frontier_steps", 15)

  // Input nodes connected to this optimiser
  const inputNodes = useMemo(
    () => nodeId ? findInputNodes(nodeId, allNodes, edges) : [],
    [nodeId, allNodes, edges],
  )

  // Data input selection — which connected input provides objectives & constraints
  const dataInput = configField(config, "data_input", "")

  // Columns from the selected data input node — cached in store
  const dataInputColumns = useDataInputColumns(dataInput, allNodes, edges, submodels)

  const buildGraphCb = useCallback(
    () => buildGraph(allNodes, edges, submodels),
    [allNodes, edges, submodels],
  )

  // --- Constraints helpers ---
  const {
    handleAddConstraint,
    handleRemoveConstraint,
    handleConstraintColumnChange,
    handleConstraintValueChange,
  } = useConstraintHandlers(constraints, objective, dataInputColumns, onUpdate)

  // --- Factor toggle helpers (ratebook) ---

  const handleToggleFactor = useCallback((factorName: string) => {
    // Each banding factor maps to a factor group of [factorName]
    const isSelected = factorColumns.some(g => g.length === 1 && g[0] === factorName)
    if (isSelected) {
      onUpdate("factor_columns", factorColumns.filter(g => !(g.length === 1 && g[0] === factorName)))
    } else {
      onUpdate("factor_columns", [...factorColumns, [factorName]])
    }
  }, [factorColumns, onUpdate])

  // --- Actions (polling is handled by useBackgroundJobs hook in App.tsx) ---

  const handleSolve = useCallback(async () => {
    setSubmitting(true)
    const nodeLabel = allNodes.find(n => n.id === nodeId)?.data.label || "Optimiser"
    try {
      const result = await solveOptimiser({ graph: buildGraphCb(), node_id: nodeId })
      if (result.status === "started" && result.job_id) {
        // Register job in store — background hook picks up polling
        startSolveJob(nodeId, result.job_id, nodeLabel, constraints, currentConfigHash)
      } else if (result.status === "error") {
        useNodeResultsStore.getState().failSolveJob(nodeId, result.error || "Unknown error")
      }
    } catch (e) {
      useNodeResultsStore.getState().failSolveJob(nodeId, String(e))
    } finally {
      setSubmitting(false)
    }
  }, [nodeId, allNodes, buildGraphCb, constraints, currentConfigHash, startSolveJob])

  // Banding node selection — only from connected inputs
  const bandingNodes = useMemo(
    () => nodeId ? findInputBandingNodes(nodeId, allNodes, edges) : [],
    [nodeId, allNodes, edges],
  )
  const bandingSource = configField(config, "banding_source", "")
  const effectiveBandingSource = bandingSource || (bandingNodes.length > 0 ? bandingNodes[0].id : "")

  // Auto-persist the effective banding source so the backend can read it
  useEffect(() => {
    if (mode === "ratebook" && !bandingSource && effectiveBandingSource) {
      onUpdate("banding_source", effectiveBandingSource)
    }
  }, [mode, bandingSource, effectiveBandingSource, onUpdate])

  const bandingLevels = useMemo(
    () => effectiveBandingSource ? extractBandingLevelsForNode(allNodes, effectiveBandingSource) : {},
    [allNodes, effectiveBandingSource],
  )
  const bandingFactorNames = useMemo(() => Object.keys(bandingLevels).sort(), [bandingLevels])

  // When banding source changes, auto-select all its factors
  const handleBandingSourceChange = useCallback((bandingNodeId: string) => {
    onUpdate("banding_source", bandingNodeId)
    const levels = extractBandingLevelsForNode(allNodes, bandingNodeId)
    const allFactors = Object.keys(levels).map(name => [name])
    onUpdate("factor_columns", allFactors)
  }, [allNodes, onUpdate])

  const canSolve = !!objective &&
    (mode !== "ratebook" || factorColumns.length > 0)

  return (
    <div className="px-4 py-3 space-y-4">
      {/* Mode Toggle */}
      <div>
        <label className="text-[11px] font-bold uppercase tracking-[0.08em]" style={{ color: "var(--text-muted)" }}>Mode</label>
        <div className="mt-1.5 flex gap-1">
          {(["online", "ratebook"] as const).map(m => (
            <button
              key={m}
              onClick={() => onUpdate("mode", m)}
              className="flex-1 px-3 py-1.5 rounded-lg text-xs font-medium transition-colors"
              style={{
                background: mode === m ? withAlpha(accentColor, 0.15) : "var(--chrome-hover)",
                color: mode === m ? accentColor : "var(--text-muted)",
                border: `1px solid ${mode === m ? withAlpha(accentColor, 0.3) : "transparent"}`,
              }}
            >
              {m === "online" ? "Online" : "Ratebook"}
            </button>
          ))}
        </div>
      </div>

      {/* Objectives & Constraints Input */}
      <div>
        <label className="text-[11px] font-bold uppercase tracking-[0.08em]" style={{ color: "var(--text-muted)" }}>Objectives & Constraints</label>
        <div className="mt-1.5">
          {inputNodes.length > 0 ? (
            <select
              value={dataInput}
              onChange={(e) => onUpdate("data_input", e.target.value)}
              className="w-full mt-0.5 px-2.5 py-1.5 rounded-lg text-xs"
              style={{ background: "var(--bg-input)", border: "1px solid var(--border)", color: "var(--text-primary)" }}
            >
              <option value="">Select input...</option>
              {inputNodes.map(n => (
                <option key={n.id} value={n.id}>{n.label}</option>
              ))}
            </select>
          ) : (
            <div className="mt-0.5 text-[11px] py-2 text-center" style={{ color: "var(--text-muted)" }}>
              No inputs connected.
            </div>
          )}
        </div>
      </div>

      {/* Objective */}
      <div>
        <label className="text-[11px] font-bold uppercase tracking-[0.08em]" style={{ color: "var(--text-muted)" }}>Objective</label>
        <div className="mt-1.5">
          <label className="text-xs" style={{ color: "var(--text-secondary)" }}>Column to maximise</label>
          <select
            value={objective}
            onChange={(e) => onUpdate("objective", e.target.value)}
            className="w-full mt-0.5 px-2.5 py-1.5 rounded-lg text-xs font-mono"
            style={{ background: "var(--bg-input)", border: "1px solid var(--border)", color: "var(--text-primary)" }}
          >
            <option value="">Select objective...</option>
            {dataInputColumns.map(c => <option key={c.name} value={c.name}>{c.name} ({c.dtype})</option>)}
          </select>
        </div>
      </div>

      {/* Ratebook: Banding Source + Rating Factors */}
      {mode === "ratebook" && (
        <div className="space-y-3">
          {/* Banding source selector */}
          <div>
            <label className="text-[11px] font-bold uppercase tracking-[0.08em]" style={{ color: "var(--text-muted)" }}>
              Rating Factor Source
            </label>
            {bandingNodes.length > 0 ? (
              <select
                value={effectiveBandingSource}
                onChange={(e) => handleBandingSourceChange(e.target.value)}
                className="w-full mt-1 px-2.5 py-1.5 rounded-lg text-xs"
                style={{ background: "var(--bg-input)", border: "1px solid var(--border)", color: "var(--text-primary)" }}
              >
                {bandingNodes.map(bn => (
                  <option key={bn.id} value={bn.id}>{bn.label}</option>
                ))}
              </select>
            ) : (
              <div className="mt-1 text-[11px] py-2 text-center" style={{ color: "var(--text-muted)" }}>
                No Banding nodes found. Add a Banding node to define rating factors.
              </div>
            )}
          </div>

          {/* Factor toggles from selected banding node */}
          {bandingFactorNames.length > 0 && (
            <div>
              <label className="text-[11px] font-bold uppercase tracking-[0.08em]" style={{ color: "var(--text-muted)" }}>
                <Layers size={10} className="inline mr-1" />
                Rating Factors ({factorColumns.length} selected)
              </label>
              <div className="mt-1.5 space-y-1">
                {bandingFactorNames.map(name => {
                  const levels = bandingLevels[name] || []
                  const selected = factorColumns.some(g => g.length === 1 && g[0] === name)
                  return (
                    <button
                      key={name}
                      onClick={() => handleToggleFactor(name)}
                      className="w-full flex items-center justify-between px-2.5 py-1.5 rounded-lg text-xs transition-colors"
                      style={{
                        background: selected ? withAlpha(accentColor, 0.1) : "var(--bg-surface)",
                        border: `1px solid ${selected ? withAlpha(accentColor, 0.3) : "var(--border)"}`,
                      }}
                    >
                      <span className="font-mono" style={{ color: selected ? accentColor : "var(--text-primary)" }}>{name}</span>
                      <span className="text-[10px]" style={{ color: selected ? withAlpha(accentColor, 0.7) : "var(--text-muted)" }}>
                        {levels.length} levels
                      </span>
                    </button>
                  )
                })}
              </div>
            </div>
          )}
        </div>
      )}

      {/* Column Mappings */}
      <div>
        <label className="text-[11px] font-bold uppercase tracking-[0.08em]" style={{ color: "var(--text-muted)" }}>Column Mappings</label>
        <div className="mt-1.5 space-y-2">
          {[
            { key: "quote_id", label: "Quote ID", value: quoteId, default: "quote_id" },
            { key: "scenario_index", label: "Scenario Index", value: scenarioIndex, default: "scenario_index" },
            { key: "scenario_value", label: "Scenario Value", value: scenarioValue, default: "scenario_value" },
          ].map(field => (
            <div key={field.key}>
              <label className="text-[11px]" style={{ color: "var(--text-muted)" }}>{field.label}</label>
              <select
                value={field.value}
                onChange={(e) => onUpdate(field.key, e.target.value)}
                className="w-full mt-0.5 px-2.5 py-1.5 rounded-lg text-xs font-mono"
                style={{ background: "var(--bg-input)", border: "1px solid var(--border)", color: "var(--text-primary)" }}
              >
                <option value="">Select {field.label.toLowerCase()}...</option>
                {dataInputColumns.map(c => <option key={c.name} value={c.name}>{c.name}</option>)}
              </select>
            </div>
          ))}
        </div>
      </div>

      {/* Constraints */}
      <div>
        <div className="flex items-center justify-between">
          <label className="text-[11px] font-bold uppercase tracking-[0.08em]" style={{ color: "var(--text-muted)" }}>
            Constraints ({Object.keys(constraints).length})
          </label>
          <button
            onClick={handleAddConstraint}
            className="flex items-center gap-1 px-2 py-0.5 rounded text-[11px] transition-colors"
            style={{ color: "var(--accent)", background: "var(--accent-soft)" }}
          >
            <Plus size={10} /> Add
          </button>
        </div>
        <div className="mt-1.5 space-y-2">
          {Object.entries(constraints).map(([name, spec]) => {
            const constraintType = Object.keys(spec)[0] || "min"
            const constraintValue = spec[constraintType] ?? 0.9
            return (
              <div key={name} className="flex items-center gap-1.5 p-2 rounded-lg" style={{ background: "var(--bg-surface)", border: "1px solid var(--border)" }}>
                <select
                  value={name}
                  onChange={(e) => handleConstraintColumnChange(name, e.target.value)}
                  className="flex-1 min-w-0 px-1.5 py-1 rounded text-[11px] font-mono"
                  style={{ background: "var(--bg-input)", border: "1px solid var(--border)", color: "var(--text-primary)" }}
                >
                  <option value={name}>{name}</option>
                  {dataInputColumns.filter(c => c.name !== name && c.name !== objective && !constraints[c.name]).map(c => (
                    <option key={c.name} value={c.name}>{c.name}</option>
                  ))}
                </select>
                <select
                  value={constraintType}
                  onChange={(e) => handleConstraintValueChange(name, e.target.value, constraintValue)}
                  className="w-[90px] px-1 py-1 rounded text-[10px]"
                  style={{ background: "var(--bg-input)", border: "1px solid var(--border)", color: "var(--text-secondary)" }}
                >
                  {CONSTRAINT_TYPES.map(ct => <option key={ct.value} value={ct.value}>{ct.label}</option>)}
                </select>
                <input
                  type="number"
                  step={0.01}
                  value={constraintValue}
                  onChange={(e) => handleConstraintValueChange(name, constraintType, safeParseFloat(e.target.value, 0))}
                  className="w-16 px-1.5 py-1 rounded text-[11px] font-mono text-right"
                  style={{ background: "var(--bg-input)", border: "1px solid var(--border)", color: "var(--text-primary)" }}
                />
                <button
                  onClick={() => handleRemoveConstraint(name)}
                  className="p-0.5 rounded transition-colors shrink-0"
                  style={{ color: "var(--text-muted)" }}
                >
                  <X size={12} />
                </button>
              </div>
            )
          })}
          {Object.keys(constraints).length === 0 && (
            <div className="text-[11px] py-2 text-center" style={{ color: "var(--text-muted)" }}>
              No constraints added. Click "Add" to set volume or loss ratio bounds.
            </div>
          )}
        </div>
      </div>

      {/* Efficient Frontier (only when constraints are configured) */}
      {Object.keys(constraints).length > 0 && (
        <div>
          <label className="text-[11px] font-bold uppercase tracking-[0.08em]" style={{ color: "var(--text-muted)" }}>
            Efficient Frontier
          </label>
          <div className="mt-1.5 grid grid-cols-3 gap-2">
            <div>
              <label className="text-[11px]" style={{ color: "var(--text-muted)" }}>Min multiplier</label>
              <input
                type="number"
                step={0.01}
                value={frontierMin}
                onChange={(e) => onUpdate("frontier_min", safeParseFloat(e.target.value, 0.80))}
                className="w-full mt-0.5 px-2 py-1 rounded text-xs font-mono"
                style={{ background: "var(--bg-input)", border: "1px solid var(--border)", color: "var(--text-primary)" }}
              />
            </div>
            <div>
              <label className="text-[11px]" style={{ color: "var(--text-muted)" }}>Max multiplier</label>
              <input
                type="number"
                step={0.01}
                value={frontierMax}
                onChange={(e) => onUpdate("frontier_max", safeParseFloat(e.target.value, 1.10))}
                className="w-full mt-0.5 px-2 py-1 rounded text-xs font-mono"
                style={{ background: "var(--bg-input)", border: "1px solid var(--border)", color: "var(--text-primary)" }}
              />
            </div>
            <div>
              <label className="text-[11px]" style={{ color: "var(--text-muted)" }}>Steps</label>
              <input
                type="number"
                min={2}
                step={1}
                value={frontierSteps}
                onChange={(e) => onUpdate("frontier_steps", safeParseInt(e.target.value, 15))}
                className="w-full mt-0.5 px-2 py-1 rounded text-xs font-mono"
                style={{ background: "var(--bg-input)", border: "1px solid var(--border)", color: "var(--text-primary)" }}
              />
            </div>
          </div>
        </div>
      )}

      {/* Solver Tuning */}
      <div>
        <label className="text-[11px] font-bold uppercase tracking-[0.08em]" style={{ color: "var(--text-muted)" }}>Solver</label>
        <div className="mt-1.5 grid grid-cols-2 gap-2">
          <div>
            <label className="text-[11px]" style={{ color: "var(--text-muted)" }}>Max iterations</label>
            <input
              type="number" min={1} step={1}
              value={maxIter}
              onChange={(e) => onUpdate("max_iter", safeParseInt(e.target.value, 50))}
              className="w-full mt-0.5 px-2 py-1 rounded text-xs font-mono"
              style={{ background: "var(--bg-input)", border: "1px solid var(--border)", color: "var(--text-primary)" }}
            />
          </div>
          <div>
            <label className="text-[11px]" style={{ color: "var(--text-muted)" }}>Tolerance</label>
            <input
              type="number" step={0.000001}
              value={tolerance}
              onChange={(e) => onUpdate("tolerance", safeParseFloat(e.target.value, 1e-6))}
              className="w-full mt-0.5 px-2 py-1 rounded text-xs font-mono"
              style={{ background: "var(--bg-input)", border: "1px solid var(--border)", color: "var(--text-primary)" }}
            />
          </div>
        </div>
      </div>

      {/* Advanced (collapsible) */}
      <div>
        <button
          onClick={() => toggleAdvanced("optimiser.advanced")}
          className="flex items-center gap-1 text-[11px] font-bold uppercase tracking-[0.08em]"
          style={{ color: "var(--text-muted)" }}
        >
          {advancedOpen ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
          Advanced
        </button>
        {advancedOpen && (
          <div className="mt-1.5 space-y-2">
            <div>
              <label className="text-[11px]" style={{ color: "var(--text-muted)" }}>Chunk size</label>
              <input
                type="number" min={1000} step={10000}
                value={chunkSize}
                onChange={(e) => onUpdate("chunk_size", safeParseInt(e.target.value, 500_000))}
                className="w-full mt-0.5 px-2 py-1 rounded text-xs font-mono"
                style={{ background: "var(--bg-input)", border: "1px solid var(--border)", color: "var(--text-primary)" }}
              />
            </div>
            <div className="flex items-center gap-2">
              <label className="text-[11px]" style={{ color: "var(--text-muted)" }}>Record history</label>
              <button
                onClick={() => onUpdate("record_history", !recordHistory)}
                className="px-2 py-0.5 rounded text-[11px] font-mono"
                style={{
                  background: recordHistory ? withAlpha(accentColor, 0.15) : "var(--chrome-hover)",
                  color: recordHistory ? accentColor : "var(--text-muted)",
                  border: `1px solid ${recordHistory ? withAlpha(accentColor, 0.3) : "transparent"}`,
                }}
              >
                {recordHistory ? "On" : "Off"}
              </button>
            </div>
            {mode === "ratebook" && (
              <div className="grid grid-cols-2 gap-2">
                <div>
                  <label className="text-[11px]" style={{ color: "var(--text-muted)" }}>CD iterations</label>
                  <input
                    type="number" min={1} step={1}
                    value={maxCdIterations}
                    onChange={(e) => onUpdate("max_cd_iterations", safeParseInt(e.target.value, 10))}
                    className="w-full mt-0.5 px-2 py-1 rounded text-xs font-mono"
                    style={{ background: "var(--bg-input)", border: "1px solid var(--border)", color: "var(--text-primary)" }}
                  />
                </div>
                <div>
                  <label className="text-[11px]" style={{ color: "var(--text-muted)" }}>CD tolerance</label>
                  <input
                    type="number" step={0.0001}
                    value={cdTolerance}
                    onChange={(e) => onUpdate("cd_tolerance", safeParseFloat(e.target.value, 1e-3))}
                    className="w-full mt-0.5 px-2 py-1 rounded text-xs font-mono"
                    style={{ background: "var(--bg-input)", border: "1px solid var(--border)", color: "var(--text-primary)" }}
                  />
                </div>
              </div>
            )}
          </div>
        )}
      </div>

      {/* MLflow (collapsible) */}
      <div>
        <button
          onClick={() => toggleAdvanced("optimiser.mlflow")}
          className="flex items-center gap-1 text-[11px] font-bold uppercase tracking-[0.08em]"
          style={{ color: "var(--text-muted)" }}
        >
          {mlflowOpen ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
          MLflow Logging
        </button>
        {mlflowOpen && (
          <div className="mt-1.5 space-y-2">
            <div>
              <label className="text-[11px]" style={{ color: "var(--text-muted)" }}>Experiment path</label>
              <input
                type="text"
                placeholder="Leave blank for default"
                value={configField(config, "mlflow_experiment", "")}
                onChange={(e) => onUpdate("mlflow_experiment", e.target.value)}
                className="w-full mt-0.5 px-2.5 py-1.5 rounded-lg text-xs font-mono"
                style={{ background: "var(--bg-input)", border: "1px solid var(--border)", color: "var(--text-primary)" }}
              />
            </div>
          </div>
        )}
      </div>

      {/* Staleness indicator */}
      {isStale && (
        <div className="flex items-center gap-2 px-3 py-2 rounded-lg text-xs" style={{ background: "rgba(245,158,11,.08)", border: "1px solid rgba(245,158,11,.2)" }}>
          <RefreshCw size={12} style={{ color: "#f59e0b" }} className="shrink-0" />
          <span style={{ color: "#fbbf24" }}>Config changed since last solve</span>
          <button
            onClick={handleSolve}
            disabled={solving || !canSolve}
            className="ml-auto px-2 py-0.5 rounded text-[11px] font-medium"
            style={{ background: withAlpha(accentColor, 0.15), color: accentColor }}
          >
            Re-run
          </button>
        </div>
      )}

      {/* Actions */}
      <div className="space-y-2 pt-2" style={{ borderTop: "1px solid var(--border)" }}>
        <button
          onClick={handleSolve}
          disabled={solving || !canSolve}
          className="w-full flex items-center justify-center gap-2 px-3 py-2 rounded-lg text-xs font-medium transition-colors"
          style={{
            background: solving ? "var(--chrome-hover)" : accentColor,
            color: solving ? "var(--text-muted)" : "#fff",
            opacity: !canSolve ? 0.5 : 1,
          }}
        >
          {solving ? <Loader2 size={14} className="animate-spin" /> : <Target size={14} />}
          {solving ? "Optimising..." : "Optimise"}
        </button>
      </div>

      {/* Submitting (before job starts polling) */}
      {submitting && !solveProgress && (
        <div className="px-3 py-2.5 rounded-lg text-xs flex items-center gap-2" style={{ background: withAlpha(accentColor, 0.06), border: `1px solid ${withAlpha(accentColor, 0.2)}` }}>
          <Loader2 size={12} className="animate-spin" style={{ color: accentColor }} />
          <span style={{ color: accentColor }}>Executing pipeline...</span>
        </div>
      )}

      {/* Live Progress */}
      {solveProgress && (
        <div className="px-3 py-2.5 rounded-lg text-xs space-y-2" style={{ background: withAlpha(accentColor, 0.06), border: `1px solid ${withAlpha(accentColor, 0.2)}` }}>
          <div className="space-y-1">
            <div className="flex justify-between text-[11px]">
              <span style={{ color: accentColor }}>{solveProgress.message || "Solving..."}</span>
              <span style={{ color: "var(--text-muted)" }}>{formatElapsed(solveProgress.elapsed_seconds)}</span>
            </div>
            <div className="w-full h-1.5 rounded-full overflow-hidden" style={{ background: withAlpha(accentColor, 0.15) }}>
              <div
                className="h-full rounded-full transition-all duration-300"
                style={{ width: `${Math.max(solveProgress.progress * 100, 2)}%`, background: accentColor }}
              />
            </div>
          </div>
        </div>
      )}

      {/* Error */}
      {solveError && (
        <div className="px-3 py-2.5 rounded-lg text-xs space-y-1.5" style={{ background: "rgba(239,68,68,.08)", border: "1px solid rgba(239,68,68,.2)" }}>
          <div className="flex items-start gap-2">
            <AlertTriangle size={14} className="shrink-0 mt-0.5" style={{ color: "#ef4444" }} />
            <div className="space-y-1 min-w-0">
              <div className="font-semibold" style={{ color: "#ef4444" }}>Optimisation failed</div>
              <div style={{ color: "#fca5a5", lineHeight: "1.5" }}>{solveError}</div>
            </div>
          </div>
        </div>
      )}

      {/* Results */}
      {solveResult && (
        <div className="space-y-2">
          {/* Non-convergence warning banner */}
          {!solveResult.converged && (
            <div className="flex items-start gap-2 px-3 py-2 rounded-lg text-xs" style={{ background: "rgba(245,158,11,.1)", border: "1px solid rgba(245,158,11,.25)" }}>
              <AlertTriangle size={14} className="shrink-0 mt-0.5" style={{ color: "#f59e0b" }} />
              <div>
                <div className="font-semibold" style={{ color: "#f59e0b" }}>Solver did not converge</div>
                <div style={{ color: "#fbbf24", lineHeight: "1.5" }}>
                  {solveResult.warning || "Try increasing max iterations or relaxing the tolerance."}
                </div>
              </div>
            </div>
          )}

          {/* Convergence status */}
          <div className="px-3 py-2 rounded-lg text-xs space-y-1" style={{ background: solveResult.converged ? "rgba(34,197,94,.1)" : "rgba(245,158,11,.06)", border: `1px solid ${solveResult.converged ? "rgba(34,197,94,.2)" : "rgba(245,158,11,.15)"}` }}>
            <div style={{ color: solveResult.converged ? "#22c55e" : "#f59e0b" }}>
              {solveResult.converged ? "Converged" : "Did not converge"}
              {solveResult.mode === "ratebook"
                ? ` in ${solveResult.cd_iterations ?? "?"} CD iterations`
                : ` in ${solveResult.iterations ?? "?"} iterations`}
              {solveResult.n_quotes != null && solveResult.n_steps != null && (
                <> ({solveResult.n_quotes.toLocaleString()} quotes, {solveResult.n_steps} steps)</>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
