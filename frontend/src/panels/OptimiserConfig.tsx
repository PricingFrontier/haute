import { useState, useCallback, useEffect, useMemo } from "react"
import { Loader2, ChevronDown, ChevronRight, AlertTriangle, Plus, X, Target, Layers, RefreshCw } from "lucide-react"
import type { SimpleNode, SimpleEdge, OnUpdateConfig } from "./editors"
import { solveOptimiser, estimateOptimiserSolve, estimateOptimiserFrontierAutoRange } from "../api/client"
import { useDataInputColumns } from "../hooks/useDataInputColumns"
import { useConstraintHandlers } from "../hooks/useConstraintHandlers"
import { useStaleConfigEstimate } from "../hooks/useStaleConfigEstimate"
import type { SolveResult } from "./OptimiserPreview"
import { NODE_TYPES } from "../utils/nodeTypes"
import useNodeResultsStore from "../stores/useNodeResultsStore"
import useSettingsStore from "../stores/useSettingsStore"
import useGraphStore from "../stores/useGraphStore"
import { formatElapsed } from "../utils/formatValue"
import { configField, safeParseFloat, safeParseInt } from "../utils/configField"
import { withAlpha } from "../utils/color"
import { extractBandingLevelsForNode } from "../utils/banding"
import { buildGraph } from "../utils/buildGraph"
import { useGraph } from "./useGraph"

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
  accentColor: string
}

const CONSTRAINT_TYPES = [
  { value: "min", label: "Minimum" },
  { value: "max", label: "Maximum" },
]

type FrontierRangeConfig = { min?: number; max?: number }

function optionalConfigNumber(config: Record<string, unknown>, key: string): number | undefined {
  const value = config[key]
  return typeof value === "number" && Number.isFinite(value) ? value : undefined
}

function parseOptionalNumber(raw: string): number | undefined {
  const trimmed = raw.trim()
  if (trimmed === "") return undefined
  const parsed = Number(trimmed)
  return Number.isFinite(parsed) ? parsed : undefined
}

function formatScenariosPerQuote(min?: number | null, max?: number | null, mean?: number | null): string {
  if (min == null && max == null) return mean == null ? "" : mean.toLocaleString(undefined, { maximumFractionDigits: 1 })
  if (min != null && max != null) {
    return min === max ? min.toLocaleString() : `${min.toLocaleString()}-${max.toLocaleString()}`
  }
  return (min ?? max)?.toLocaleString() ?? ""
}

export default function OptimiserConfig({
  config,
  onUpdate,
  upstreamColumns = [],
  accentColor,
}: OptimiserConfigProps) {
  const { allNodes, edges, submodels } = useGraph()
  // ── Store-backed state (survives panel unmount) ──
  const nodeId = config._nodeId as string
  const solveJob = useNodeResultsStore((s) => s.solveJobs[nodeId])
  const cachedResult = useNodeResultsStore((s) => s.solveResults[nodeId])
  const startSolveJob = useNodeResultsStore((s) => s.startSolveJob)
  const activeSource = useSettingsStore((s) => s.activeSource)
  const structuralVersion = useGraphStore((s) => s.structuralVersion)

  // ── Local UI state (cheap, ok to recreate) ──
  const [submitting, setSubmitting] = useState(false)
  const [autoRangeLoading, setAutoRangeLoading] = useState(false)
  const [autoRangeError, setAutoRangeError] = useState<string | null>(null)

  const solving = submitting || !!solveJob
  const solveProgress = solveJob?.progress ?? null
  const solveError = solveJob?.error ?? null
  const solveResult: SolveResult | null = cachedResult?.result ?? null
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
  const frontierMin = optionalConfigNumber(config, "frontier_min")
  const frontierMax = optionalConfigNumber(config, "frontier_max")
  const frontierSteps = configField(config, "frontier_steps", 15)
  const frontierEnabled = configField(config, "frontier_enabled", false)
  const frontierRanges = configField<Record<string, FrontierRangeConfig>>(config, "frontier_ranges", {})

  // Input nodes connected to this optimiser
  const inputNodes = useMemo(
    () => nodeId ? findInputNodes(nodeId, allNodes, edges) : [],
    [nodeId, allNodes, edges],
  )

  // Data input selection — which connected input provides objectives & constraints
  const dataInput = configField(config, "data_input", "")
  const constraintEntries = Object.entries(constraints)
  const constraintCount = constraintEntries.length

  // Columns from the selected data input node — cached in store
  // Prefer columns already collected for the optimiser panel so opening it
  // does not fire a second row-limit-1 preview request just to populate menus.
  const hasUpstreamColumns = upstreamColumns.length > 0
  const fetchedDataInputColumns = useDataInputColumns(dataInput, allNodes, edges, submodels, undefined, {
    enabled: !hasUpstreamColumns,
    fallbackColumns: upstreamColumns,
  })
  const dataInputColumns = hasUpstreamColumns ? upstreamColumns : fetchedDataInputColumns

  const buildGraphCb = useCallback(
    () => buildGraph(allNodes, edges, submodels),
    [allNodes, edges, submodels],
  )

  // ── Solve-cost estimate, via the shared config-estimate hook ──
  // Previews source row/column counts read from parquet metadata so the
  // user knows what volume of scored data the solver will process.
  // The hook owns the abort / toast / loading lifecycle.
  const solveEstimateEndpoint = useCallback(
    (_payload: void, { signal }: { signal: AbortSignal }) =>
      estimateOptimiserSolve(
        {
          graph: buildGraphCb(),
          node_id: nodeId,
          source: activeSource,
        },
        { signal },
      ),
    [buildGraphCb, nodeId, activeSource],
  )
  const {
    configHash: currentConfigHash,
    isStale,
    estimate: solveEstimate,
  } = useStaleConfigEstimate(
    nodeId,
    config,
    cachedResult,
    solveEstimateEndpoint,
    {
      toastLabel: "Solve estimate failed",
      estimateKey: `${activeSource}:${structuralVersion}`,
    },
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

  const rangeForConstraint = useCallback(
    (name: string): FrontierRangeConfig => {
      const configured = frontierRanges[name]
      return {
        min: typeof configured?.min === "number" && Number.isFinite(configured.min) ? configured.min : frontierMin,
        max: typeof configured?.max === "number" && Number.isFinite(configured.max) ? configured.max : frontierMax,
      }
    },
    [frontierRanges, frontierMin, frontierMax],
  )

  const handleFrontierRangeChange = useCallback(
    (name: string, key: keyof FrontierRangeConfig, value: number | undefined) => {
      const nextRange: FrontierRangeConfig = { ...rangeForConstraint(name) }
      if (value === undefined) {
        delete nextRange[key]
      } else {
        nextRange[key] = value
      }
      const nextRanges = { ...frontierRanges }
      if (nextRange.min === undefined && nextRange.max === undefined) {
        delete nextRanges[name]
      } else {
        nextRanges[name] = nextRange
      }
      const updates: Record<string, unknown> = { frontier_ranges: nextRanges }
      if (constraintCount === 1) {
        updates[key === "min" ? "frontier_min" : "frontier_max"] = value
      }
      onUpdate(updates)
    },
    [constraintCount, frontierRanges, onUpdate, rangeForConstraint],
  )

  const handleAutoRange = useCallback(async () => {
    setAutoRangeLoading(true)
    setAutoRangeError(null)
    try {
      const response = await estimateOptimiserFrontierAutoRange({
        graph: buildGraphCb(),
        node_id: nodeId,
      })
      const nextRanges: Record<string, FrontierRangeConfig> = {}
      const missingRanges: string[] = []
      for (const [name] of constraintEntries) {
        const range = response.ranges[name]
        if (range) {
          nextRanges[name] = range
        } else {
          missingRanges.push(name)
        }
      }
      if (missingRanges.length > 0) {
        throw new Error(`No ranges returned for: ${missingRanges.join(", ")}`)
      }
      if (Object.keys(nextRanges).length === 0) {
        throw new Error("No ranges returned for the selected constraints")
      }
      const firstRange = nextRanges[constraintEntries[0]?.[0]]
      onUpdate({
        frontier_ranges: nextRanges,
        ...(constraintCount === 1 && firstRange
          ? { frontier_min: firstRange.min, frontier_max: firstRange.max }
          : {}),
      })
      if (response.warning) setAutoRangeError(response.warning)
    } catch (err) {
      setAutoRangeError(err instanceof Error ? err.message : "Auto range failed")
    } finally {
      setAutoRangeLoading(false)
    }
  }, [buildGraphCb, constraintCount, constraintEntries, nodeId, onUpdate])

  const renderConstraintBoundRows = () => (
    <div className="space-y-1.5">
      {constraintEntries.map(([name, spec]) => {
        const constraintType = Object.keys(spec).find(key => key === "min" || key === "max") || "min"
        const constraintValue = spec[constraintType] ?? 0
        return (
          <div key={name} data-testid="constraint-bound-row" className="grid grid-cols-[90px_64px] items-center gap-1.5">
            <select
              value={constraintType}
              onChange={(e) => handleConstraintValueChange(name, e.target.value, constraintValue)}
              className="px-1 py-1 rounded text-[10px]"
              style={{ background: "var(--bg-input)", border: "1px solid var(--border)", color: "var(--text-secondary)" }}
            >
              {CONSTRAINT_TYPES.map(ct => <option key={ct.value} value={ct.value}>{ct.label}</option>)}
            </select>
            <input
              type="number"
              step="any"
              value={constraintValue}
              onChange={(e) => handleConstraintValueChange(name, constraintType, safeParseFloat(e.target.value, 0))}
              className="w-full px-1.5 py-1 rounded text-[11px] font-mono text-right"
              style={{ background: "var(--bg-input)", border: "1px solid var(--border)", color: "var(--text-primary)" }}
            />
          </div>
        )
      })}
    </div>
  )

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
            Constraints ({constraintCount})
          </label>
          <button
            onClick={handleAddConstraint}
            className="flex items-center gap-1 px-2 py-0.5 rounded text-[11px] transition-colors"
            style={{ color: "var(--accent)", background: "var(--accent-soft)" }}
          >
            <Plus size={10} /> Add
          </button>
        </div>
        <div className="mt-1.5" data-testid="constraints-settings">
          {constraintCount > 0 && (
            <div
              data-testid="constraint-settings-card"
              className="p-2 rounded-lg space-y-2"
              style={{ background: "var(--bg-surface)", border: "1px solid var(--border)" }}
            >
              <div className="space-y-1.5">
                {constraintEntries.map(([name]) => {
                  return (
                    <div key={name} data-testid="constraint-row" className="flex items-center gap-1.5">
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
              </div>

              <div className="pt-2 space-y-2" style={{ borderTop: "1px solid var(--border)" }}>
                <div>
                  <label className="text-[11px]" style={{ color: "var(--text-muted)" }}>Result type</label>
                  <div className="mt-1 flex gap-1">
                    {[
                      { enabled: false, label: "Individual point" },
                      { enabled: true, label: "Efficient frontier" },
                    ].map(option => (
                      <button
                        key={option.label}
                        onClick={() => onUpdate("frontier_enabled", option.enabled)}
                        className="flex-1 px-2 py-1 rounded text-[11px] font-medium transition-colors"
                        style={{
                          background: frontierEnabled === option.enabled ? withAlpha(accentColor, 0.15) : "var(--chrome-hover)",
                          color: frontierEnabled === option.enabled ? accentColor : "var(--text-muted)",
                          border: `1px solid ${frontierEnabled === option.enabled ? withAlpha(accentColor, 0.3) : "transparent"}`,
                        }}
                      >
                        {option.label}
                      </button>
                    ))}
                  </div>
                </div>
                {!frontierEnabled ? (
                  <div data-testid="individual-point-settings" className="space-y-2">
                    {renderConstraintBoundRows()}
                  </div>
                ) : (
                  <div className="space-y-2">
                    <div className="flex justify-end">
                      <button
                        type="button"
                        onClick={handleAutoRange}
                        disabled={autoRangeLoading || constraintCount === 0 || !canSolve}
                        className="flex items-center gap-1 px-2 py-1 rounded text-[10px] font-medium disabled:opacity-50"
                        style={{ background: withAlpha(accentColor, 0.12), color: accentColor }}
                      >
                        {autoRangeLoading ? <Loader2 size={10} className="animate-spin" /> : <RefreshCw size={10} />}
                        Auto range
                      </button>
                    </div>
                    <div className="space-y-1.5">
                      {constraintEntries.map(([name]) => {
                        const range = rangeForConstraint(name)
                        const minMissing = range.min === undefined
                        const maxMissing = range.max === undefined
                        return (
                          <div
                            key={name}
                            data-testid="frontier-range-row"
                            className={constraintCount > 1 ? "grid grid-cols-[minmax(0,1fr)_80px_80px] items-end gap-1.5" : "grid grid-cols-2 gap-2"}
                          >
                            {constraintCount > 1 && (
                              <span className="min-w-0 truncate pb-1.5 text-[11px] font-mono" style={{ color: "var(--text-secondary)" }}>
                                {name}
                              </span>
                            )}
                            <div>
                              <label className="text-[11px]" style={{ color: "var(--text-muted)" }}>Min value</label>
                              <input
                                type="number"
                                step="any"
                                value={range.min ?? ""}
                                aria-label={`${name} min value`}
                                aria-invalid={minMissing || undefined}
                                placeholder="Required"
                                onChange={(e) => handleFrontierRangeChange(name, "min", parseOptionalNumber(e.target.value))}
                                className="w-full mt-0.5 px-2 py-1 rounded text-xs font-mono"
                                style={{
                                  background: minMissing ? "var(--warning-soft)" : "var(--bg-input)",
                                  border: `1px solid ${minMissing ? "var(--warning-border-strong)" : "var(--border)"}`,
                                  color: "var(--text-primary)",
                                }}
                              />
                            </div>
                            <div>
                              <label className="text-[11px]" style={{ color: "var(--text-muted)" }}>Max value</label>
                              <input
                                type="number"
                                step="any"
                                value={range.max ?? ""}
                                aria-label={`${name} max value`}
                                aria-invalid={maxMissing || undefined}
                                placeholder="Required"
                                onChange={(e) => handleFrontierRangeChange(name, "max", parseOptionalNumber(e.target.value))}
                                className="w-full mt-0.5 px-2 py-1 rounded text-xs font-mono"
                                style={{
                                  background: maxMissing ? "var(--warning-soft)" : "var(--bg-input)",
                                  border: `1px solid ${maxMissing ? "var(--warning-border-strong)" : "var(--border)"}`,
                                  color: "var(--text-primary)",
                                }}
                              />
                            </div>
                          </div>
                        )
                      })}
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
                    {autoRangeError && (
                      <div className="text-[11px]" style={{ color: "var(--warning)" }}>
                        {autoRangeError}
                      </div>
                    )}
                  </div>
                )}
              </div>
            </div>
          )}
        </div>
      </div>

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
        <div className="flex items-center gap-2 px-3 py-2 rounded-lg text-xs" style={{ background: "var(--warning-soft)", border: "1px solid var(--warning-border)" }}>
          <RefreshCw size={12} style={{ color: "var(--warning-strong)" }} className="shrink-0" />
          <span style={{ color: "var(--warning)" }}>Config changed since last solve</span>
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

      {/* Source size preview (hidden when unreadable — metadata isn't available for live data) */}
      {solveEstimate && solveEstimate.quote_count != null && solveEstimate.expanded_row_count != null && (
        <div className="grid grid-cols-3 gap-2 px-3 py-2 rounded-lg text-[11px]" style={{ background: "var(--bg-surface)", border: "1px solid var(--border)" }}>
          <div className="min-w-0">
            <div style={{ color: "var(--text-muted)" }}>Quotes</div>
            <div className="font-mono truncate" style={{ color: "var(--text-primary)" }}>
              {solveEstimate.quote_count.toLocaleString()}
            </div>
          </div>
          <div className="min-w-0">
            <div style={{ color: "var(--text-muted)" }}>Scenarios / quote</div>
            <div className="font-mono truncate" style={{ color: "var(--text-primary)" }}>
              {formatScenariosPerQuote(
                solveEstimate.scenarios_per_quote_min,
                solveEstimate.scenarios_per_quote_max,
                solveEstimate.scenarios_per_quote_mean,
              )}
            </div>
          </div>
          <div className="min-w-0">
            <div style={{ color: "var(--text-muted)" }}>Total rows</div>
            <div className="font-mono truncate" style={{ color: "var(--text-primary)" }}>
              {solveEstimate.expanded_row_count.toLocaleString()}
            </div>
          </div>
        </div>
      )}

      {/* Actions */}
      <div className="space-y-2 pt-2" style={{ borderTop: "1px solid var(--border)" }}>
        {solving ? (
          <div className="px-3 py-2.5 rounded-lg text-xs space-y-2" style={{ background: withAlpha(accentColor, 0.06), border: `1px solid ${withAlpha(accentColor, 0.2)}` }}>
            {solveProgress ? (
              <div className="space-y-1">
                <div className="flex justify-between text-[11px]">
                  <span className="flex items-center gap-1.5" style={{ color: accentColor }}>
                    <Loader2 size={12} className="animate-spin shrink-0" />
                    {solveProgress.message || "Solving..."}
                  </span>
                  <span style={{ color: "var(--text-muted)" }}>{formatElapsed(solveProgress.elapsed_seconds)}</span>
                </div>
                <div className="w-full h-1.5 rounded-full overflow-hidden" style={{ background: withAlpha(accentColor, 0.15) }}>
                  <div
                    className="h-full rounded-full transition-all duration-300"
                    style={{ width: `${Math.max(solveProgress.progress * 100, 2)}%`, background: accentColor }}
                  />
                </div>
              </div>
            ) : (
              <div className="flex items-center gap-2">
                <Loader2 size={12} className="animate-spin shrink-0" style={{ color: accentColor }} />
                <span style={{ color: accentColor }}>Executing pipeline...</span>
              </div>
            )}
          </div>
        ) : (
          <button
            onClick={handleSolve}
            disabled={!canSolve}
            className="w-full flex items-center justify-center gap-2 px-3 py-2 rounded-lg text-xs font-medium transition-colors"
            style={{
              background: accentColor,
              color: "var(--text-on-accent)",
              opacity: !canSolve ? 0.5 : 1,
            }}
          >
            <Target size={14} />
            Optimise
          </button>
        )}
      </div>

      {/* Error */}
      {solveError && (
        <div className="px-3 py-2.5 rounded-lg text-xs space-y-1.5" style={{ background: "var(--danger-soft-subtle)", border: "1px solid var(--danger-border)" }}>
          <div className="flex items-start gap-2">
            <AlertTriangle size={14} className="shrink-0 mt-0.5" style={{ color: "var(--danger)" }} />
            <div className="space-y-1 min-w-0">
              <div className="font-semibold" style={{ color: "var(--danger)" }}>Optimisation failed</div>
              <div style={{ color: "var(--danger-text-soft)", lineHeight: "1.5" }}>{solveError}</div>
            </div>
          </div>
        </div>
      )}

      {/* Results */}
      {solveResult && (
        <div className="space-y-2">
          {/* Non-convergence warning banner */}
          {!solveResult.converged && (
            <div className="flex items-start gap-2 px-3 py-2 rounded-lg text-xs" style={{ background: "var(--warning-soft-strong)", border: "1px solid var(--warning-border-strong)" }}>
              <AlertTriangle size={14} className="shrink-0 mt-0.5" style={{ color: "var(--warning-strong)" }} />
              <div>
                <div className="font-semibold" style={{ color: "var(--warning-strong)" }}>Solver did not converge</div>
                <div style={{ color: "var(--warning)", lineHeight: "1.5" }}>
                  {solveResult.warning || "Try increasing max iterations or relaxing the tolerance."}
                </div>
              </div>
            </div>
          )}

          {/* Convergence status */}
          <div className="px-3 py-2 rounded-lg text-xs space-y-1" style={{ background: solveResult.converged ? "var(--success-soft)" : "var(--warning-soft-subtle)", border: `1px solid ${solveResult.converged ? "var(--success-border)" : "var(--warning-soft-selected)"}` }}>
            <div style={{ color: solveResult.converged ? "var(--success)" : "var(--warning-strong)" }}>
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
