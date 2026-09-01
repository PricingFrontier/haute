import { useState, useCallback, useEffect, useMemo } from "react"
import { ChevronDown, ChevronRight, Plus, Layers } from "lucide-react"
import type { SimpleNode, SimpleEdge, OnUpdateConfig } from "./editors"
import { solveOptimiser, estimateOptimiserSolve } from "../api/client"
import { useDataInputColumns } from "../hooks/useDataInputColumns"
import { useConstraintHandlers } from "../hooks/useConstraintHandlers"
import { useStaleConfigEstimate } from "../hooks/useStaleConfigEstimate"
import type { OptimiserSolveResult } from "../api/types"
import { NODE_TYPES } from "../utils/nodeTypes"
import useNodeResultsStore, { type SolveProgress } from "../stores/useNodeResultsStore"
import {
  captureDocumentExecutionFence,
  isDocumentExecutionFenceCurrent,
} from "../stores/useDocumentStatusStore"
import useSettingsStore from "../stores/useSettingsStore"
import useGraphStore from "../stores/useGraphStore"
import {
  executionErrorDetailMessage,
  executionJobStatusFromReason,
  executionMetricsFromError,
  executionTerminalReasonFromError,
} from "../utils/executionDiagnostics"
import { configField, safeParseFloat, safeParseInt } from "../utils/configField"
import { CommittedTextField } from "../components/form"
import { withAlpha } from "../utils/color"
import { classifyBandingNode } from "../utils/banding"
import { buildGraph } from "../utils/buildGraph"
import { useGraph } from "./useGraph"
import { formatOptimiserIterationSummary } from "./optimiser/iterationSummary"
import OptimiserConstraintSettings, { type FrontierRangeConfig } from "./optimiser/OptimiserConstraintSettings"
import OptimiserSolveStatus from "./optimiser/OptimiserSolveStatus"

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
  deferColumnFetch?: boolean
}

function requestErrorDetail(error: unknown): string {
  const detailMessage = executionErrorDetailMessage(error)
  if (detailMessage) return detailMessage
  if (error && typeof error === "object" && "detail" in error) {
    const detail = (error as { detail?: unknown }).detail
    if (typeof detail === "string" && detail.trim()) return detail
  }
  return error instanceof Error ? error.message : String(error)
}

function solveFailureStatus(error: unknown, message: string): SolveProgress | undefined {
  const metrics = executionMetricsFromError(error)
  if (!metrics) return undefined
  const terminalReason = executionTerminalReasonFromError(error)
  return {
    status: executionJobStatusFromReason(terminalReason),
    progress: 1,
    message,
    elapsed_seconds: 0,
    terminal_reason: terminalReason,
    execution_metrics: metrics,
  }
}

function singleFactorColumnsFromLevels(levels: Record<string, string[]>): string[][] {
  return Object.keys(levels).sort().map(name => [name])
}

function columnsForNode(nodes: SimpleNode[], nodeId: string): { name: string; dtype: string }[] {
  const node = nodes.find(n => n.id === nodeId)
  const columns = (node?.data as Record<string, unknown> | undefined)?._columns
  return Array.isArray(columns) ? columns as { name: string; dtype: string }[] : []
}

export default function OptimiserConfig({
  config,
  onUpdate,
  upstreamColumns = [],
  accentColor,
  deferColumnFetch = false,
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

  const solving = submitting || !!solveJob
  const solveProgress = solveJob?.progress ?? null
  const solveError = solveJob ? solveJob.error : (cachedResult?.error ?? null)
  const solveTerminalStatus = solveJob?.progress ?? cachedResult?.terminalStatus ?? null
  const solveTerminalMetrics = solveTerminalStatus?.execution_metrics ?? null
  const solveResult: OptimiserSolveResult | null = cachedResult?.error ? null : (cachedResult?.result ?? null)
  const solveIterationSummary = solveResult ? formatOptimiserIterationSummary(solveResult) : null
  // Collapse state from UI store (persisted)
  const advancedOpen = useSettingsStore((s) => s.isSectionOpen("optimiser.advanced"))
  const mlflowOpen = useSettingsStore((s) => s.isSectionOpen("optimiser.mlflow"))
  const toggleAdvanced = useSettingsStore((s) => s.toggleSection)

  const mode = configField(config, "mode", "online")
  const factorColumns = configField<string[][]>(config, "factor_columns", [])
  const hasConfiguredFactorColumns = Object.prototype.hasOwnProperty.call(config, "factor_columns")
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
  // Prefer the configured data-input node's cached columns so multi-input
  // optimisers do not mix factor-table fields into objective/constraint menus.
  // Fall back to the panel's upstream column union until that node has schema.
  const selectedDataInputColumns = useMemo(
    () => dataInput ? columnsForNode(allNodes, dataInput) : [],
    [allNodes, dataInput],
  )
  const fallbackDataInputColumns = selectedDataInputColumns.length > 0
    ? selectedDataInputColumns
    : upstreamColumns
  const hasDataInputColumns = fallbackDataInputColumns.length > 0
  const fetchedDataInputColumns = useDataInputColumns(dataInput, allNodes, edges, submodels, undefined, {
    enabled: !hasDataInputColumns && !deferColumnFetch,
    fallbackColumns: fallbackDataInputColumns,
  })
  const dataInputColumns = hasDataInputColumns ? fallbackDataInputColumns : fetchedDataInputColumns

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
      estimateOptimiserSolve({
        graph: buildGraphCb(),
        node_id: nodeId,
        source: activeSource,
        streamingChunkSize: useSettingsStore.getState().streamingChunkSize,
        signal,
      }),
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
    { source: activeSource, structuralVersion },
    {
      toastLabel: "Solve estimate failed",
      enabled: !deferColumnFetch,
    },
  )

  // --- Constraints helpers ---
  const {
    handleAddConstraint,
    handleRemoveConstraint,
    handleConstraintColumnChange,
    handleConstraintValueChange,
  } = useConstraintHandlers(
    constraints,
    objective,
    dataInputColumns,
    onUpdate,
    frontierRanges,
  )

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
    const documentFence = captureDocumentExecutionFence()
    if (!isDocumentExecutionFenceCurrent(documentFence)) return
    setSubmitting(true)
    const nodeLabel = allNodes.find(n => n.id === nodeId)?.data.label || "Optimiser"
    const solveSource = useSettingsStore.getState().activeSource
    const solveStructuralVersion = useGraphStore.getState().structuralVersion
    try {
      const result = await solveOptimiser({
        graph: buildGraphCb(),
        node_id: nodeId,
        streamingChunkSize: useSettingsStore.getState().streamingChunkSize,
      })
      if (!isDocumentExecutionFenceCurrent(documentFence)) return
      if (result.status === "started" && result.job_id) {
        // Register job in store — background hook picks up polling
        startSolveJob(nodeId, result.job_id, nodeLabel, constraints, currentConfigHash, solveSource, solveStructuralVersion)
      } else if (result.status === "error") {
        startSolveJob(nodeId, `startup-failure:${nodeId}`, nodeLabel, constraints, currentConfigHash, solveSource, solveStructuralVersion)
        useNodeResultsStore.getState().failSolveJob(nodeId, result.error || "Unknown error")
      }
    } catch (e) {
      if (!isDocumentExecutionFenceCurrent(documentFence)) return
      const errorMessage = requestErrorDetail(e)
      const terminalStatus = solveFailureStatus(e, errorMessage)
      startSolveJob(nodeId, `startup-failure:${nodeId}`, nodeLabel, constraints, currentConfigHash, solveSource, solveStructuralVersion)
      useNodeResultsStore.getState().failSolveJob(nodeId, errorMessage, terminalStatus)
    } finally {
      setSubmitting(false)
    }
  }, [allNodes, buildGraphCb, constraints, currentConfigHash, nodeId, startSolveJob])

  // Banding node selection — only from connected inputs
  const bandingNodes = useMemo(
    () => nodeId ? findInputBandingNodes(nodeId, allNodes, edges) : [],
    [nodeId, allNodes, edges],
  )
  const bandingSource = configField(config, "banding_source", "").trim()
  const selectedBandingNode = bandingNodes.find(node => node.id === bandingSource)
  const missingExplicitBandingSource = !!bandingSource && !selectedBandingNode
  const effectiveBandingSource = selectedBandingNode?.id || (!bandingSource && bandingNodes.length === 1 ? bandingNodes[0].id : "")

  const bandingClassification = useMemo(
    () => classifyBandingNode(allNodes.find(node => node.id === effectiveBandingSource), { includeDefault: true }),
    [allNodes, effectiveBandingSource],
  )
  const bandingLevels = bandingClassification.levels
  const bandingFactorNames = useMemo(() => Object.keys(bandingLevels).sort(), [bandingLevels])
  const inferredFactorColumns = useMemo(
    () => singleFactorColumnsFromLevels(bandingLevels),
    [bandingLevels],
  )

  // Auto-persist implicit ratebook defaults. When both the source and its
  // derived factors are missing they must be one config transaction because
  // NodePanel intentionally spreads each update over its last committed ref.
  useEffect(() => {
    if (mode !== "ratebook" || bandingSource || !effectiveBandingSource) return
    if (
      !hasConfiguredFactorColumns
      && factorColumns.length === 0
      && inferredFactorColumns.length > 0
    ) {
      onUpdate({
        banding_source: effectiveBandingSource,
        factor_columns: inferredFactorColumns,
      })
      return
    }
    onUpdate("banding_source", effectiveBandingSource)
  }, [
    mode,
    bandingSource,
    effectiveBandingSource,
    hasConfiguredFactorColumns,
    factorColumns.length,
    inferredFactorColumns,
    onUpdate,
  ])

  useEffect(() => {
    if (
      mode === "ratebook" &&
      !!bandingSource &&
      effectiveBandingSource &&
      !hasConfiguredFactorColumns &&
      factorColumns.length === 0 &&
      inferredFactorColumns.length > 0
    ) {
      onUpdate("factor_columns", inferredFactorColumns)
    }
  }, [
    mode,
    bandingSource,
    effectiveBandingSource,
    hasConfiguredFactorColumns,
    factorColumns.length,
    inferredFactorColumns,
    onUpdate,
  ])

  // When banding source changes, auto-select all its factors
  const handleBandingSourceChange = useCallback((bandingNodeId: string) => {
    const levels = classifyBandingNode(
      allNodes.find(node => node.id === bandingNodeId),
      { includeDefault: true },
    ).levels
    onUpdate({
      banding_source: bandingNodeId,
      factor_columns: singleFactorColumnsFromLevels(levels),
    })
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
                aria-label="Rating Factor Source"
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

          {(missingExplicitBandingSource || bandingClassification.zeroLevelOutputs.length > 0) && (
            <div
              role="alert"
              className="px-3 py-2 rounded-lg text-xs"
              style={{
                background: "var(--warning-soft)",
                border: "1px solid var(--warning-border)",
              }}
            >
              {[
                missingExplicitBandingSource
                  ? `Selected Banding source ${bandingSource} is no longer directly connected.`
                  : null,
                bandingClassification.zeroLevelOutputs.length > 0
                  ? `Banding outputs ${bandingClassification.zeroLevelOutputs.join(", ")} have no valid levels. Add labelled rules before selecting them.`
                  : null,
              ].filter(Boolean).join(" ")}
            </div>
          )}

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
                        background: selected ? withAlpha(accentColor, 0.1) : "var(--bg-panel)",
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
        <OptimiserConstraintSettings
          constraints={constraints}
          frontierRanges={frontierRanges}
          frontierEnabled={frontierEnabled}
          frontierSteps={frontierSteps}
          dataInputColumns={dataInputColumns}
          objective={objective}
          canSolve={canSolve}
          accentColor={accentColor}
          buildGraph={buildGraphCb}
          nodeId={nodeId}
          onUpdate={onUpdate}
          onRemoveConstraint={handleRemoveConstraint}
          onConstraintColumnChange={handleConstraintColumnChange}
          onConstraintValueChange={handleConstraintValueChange}
        />
      </div>
      {/* Solver Tuning */}
      <div>
        <label className="text-[11px] font-bold uppercase tracking-[0.08em]" style={{ color: "var(--text-muted)" }}>Solver</label>
        <div className="mt-1.5 grid grid-cols-2 gap-2">
          <div>
            <label className="text-[11px]" style={{ color: "var(--text-muted)" }}>Max iterations</label>
            <CommittedTextField
              type="number" min={1} step={1}
              value={String(maxIter)}
              onCommit={(v) => onUpdate("max_iter", safeParseInt(v, 50))}
              className="w-full mt-0.5 px-2 py-1 rounded text-xs font-mono"
              style={{ background: "var(--bg-input)", border: "1px solid var(--border)", color: "var(--text-primary)" }}
            />
          </div>
          <div>
            <label className="text-[11px]" style={{ color: "var(--text-muted)" }}>Tolerance</label>
            <CommittedTextField
              type="number" step={0.000001}
              value={String(tolerance)}
              onCommit={(v) => onUpdate("tolerance", safeParseFloat(v, 1e-6))}
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
              <CommittedTextField
                type="number" min={1000} step={10000}
                value={String(chunkSize)}
                onCommit={(v) => onUpdate("chunk_size", safeParseInt(v, 500_000))}
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
                  <CommittedTextField
                    type="number" min={1} step={1}
                    value={String(maxCdIterations)}
                    onCommit={(v) => onUpdate("max_cd_iterations", safeParseInt(v, 10))}
                    className="w-full mt-0.5 px-2 py-1 rounded text-xs font-mono"
                    style={{ background: "var(--bg-input)", border: "1px solid var(--border)", color: "var(--text-primary)" }}
                  />
                </div>
                <div>
                  <label className="text-[11px]" style={{ color: "var(--text-muted)" }}>CD tolerance</label>
                  <CommittedTextField
                    type="number" step={0.0001}
                    value={String(cdTolerance)}
                    onCommit={(v) => onUpdate("cd_tolerance", safeParseFloat(v, 1e-3))}
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
              <CommittedTextField
                type="text"
                placeholder="Leave blank for default"
                value={configField(config, "mlflow_experiment", "")}
                onCommit={(v) => onUpdate("mlflow_experiment", v)}
                className="w-full mt-0.5 px-2.5 py-1.5 rounded-lg text-xs font-mono"
                style={{ background: "var(--bg-input)", border: "1px solid var(--border)", color: "var(--text-primary)" }}
              />
            </div>
          </div>
        )}
      </div>

      <OptimiserSolveStatus
        isStale={isStale}
        onSolve={handleSolve}
        solving={solving}
        canSolve={canSolve}
        accentColor={accentColor}
        estimate={solveEstimate}
        progress={solveProgress}
        error={solveError}
        terminalMetrics={solveTerminalMetrics}
        terminalStatus={solveTerminalStatus}
        result={solveResult}
        iterationSummary={solveIterationSummary}
      />
    </div>
  )
}
