import { useState, useCallback, useEffect, useMemo, useRef, type KeyboardEvent, type ReactElement } from "react"
import { Plus, Layers } from "lucide-react"
import type { OnUpdateConfig, OnUpdateConfigResult } from "./editors"
import { estimateOptimiserSolve } from "../api/client"
import { useConstraintHandlers } from "../hooks/useConstraintHandlers"
import { useStaleConfigEstimate } from "../hooks/useStaleConfigEstimate"
import type { OptimiserSolveResult } from "../api/types"
import useNodeResultsStore from "../stores/useNodeResultsStore"
import useSettingsStore from "../stores/useSettingsStore"
import useUIStore, { type OptimiserPane } from "../stores/useUIStore"
import useGraphStore from "../stores/useGraphStore"
import useToastStore from "../stores/useToastStore"
import { apiErrorMessage } from "../api/errors"
import { solveIdentityConfig } from "../utils/modellingExportConfig"
import { configField, safeParseFloat, safeParseInt } from "../utils/configField"
import {
  defaultExperimentName,
  effectiveMlflowDestination,
  mlflowDestinationConfigValue,
} from "../utils/mlflowDestinations"
import { CommittedTextField } from "../components/form"
import MlflowDestinationSelector from "../components/MlflowDestinationSelector"
import { withAlpha } from "../utils/color"
import { classifyBandingNode } from "../utils/banding"
import { buildGraph } from "../utils/buildGraph"
import { useGraph } from "./useGraph"
import { formatOptimiserIterationSummary } from "./optimiser/iterationSummary"
import OptimiserConstraintSettings, { type FrontierRangeConfig } from "./optimiser/OptimiserConstraintSettings"
import OptimiserSolveStatus from "./optimiser/OptimiserSolveStatus"
import OptimiserPublishSection from "./optimiser/OptimiserPublishSection"
import OptimiserAnalysisColumns from "./optimiser/OptimiserAnalysisColumns"
import { startOptimiserSolve, stopOptimiserSolve } from "./optimiser/solveActions"
import { useOptimiserReadiness } from "./optimiser/useOptimiserReadiness"
import { resolveOptimiserPane } from "./optimiser/optimiserPanes"
import { FieldHelpIcon } from "./modelling/FieldHelpIcon"
import { shallowNodeDataHash } from "../utils/shallowNodeHash"

type OptimiserConfigProps = {
  config: Record<string, unknown>
  onUpdate: OnUpdateConfig
  upstreamColumns?: { name: string; dtype: string }[]
  accentColor: string
  deferColumnFetch?: boolean
  /** The pane the node panel's tab strip selected; a pane the mode lacks shows Data. */
  activePane?: OptimiserPane
  /** Reports the panes holding a blocking Solve issue, for the host's tab indicators. */
  onPaneIssuesChange?: (nodeId: string, panes: readonly OptimiserPane[]) => void
  /** Writes config keys onto another node (Export's "Use in Apply node"). */
  onUpdateNodeConfig?: (nodeId: string, patch: Record<string, unknown>) => OnUpdateConfigResult
}

const MLFLOW_MANUAL_HELP =
  "Used only when you press “Log to MLflow” on a solved result. " +
  "Nothing is logged automatically."

const SECTION_LABEL_CLASS = "text-[11px] font-bold uppercase tracking-[0.08em]"

function singleFactorColumnsFromLevels(levels: Record<string, string[]>): string[][] {
  return Object.keys(levels).sort().map(name => [name])
}

export default function OptimiserConfig({
  config,
  onUpdate,
  upstreamColumns = [],
  accentColor,
  deferColumnFetch = false,
  activePane = "data",
  onPaneIssuesChange,
  onUpdateNodeConfig,
}: OptimiserConfigProps) {
  const { allNodes, edges, submodels } = useGraph()
  // ── Store-backed state (survives panel unmount) ──
  const nodeId = config._nodeId as string
  const solveJob = useNodeResultsStore((s) => s.solveJobs[nodeId])
  const cachedResult = useNodeResultsStore((s) => s.solveResults[nodeId])
  const activeSource = useSettingsStore((s) => s.activeSource)
  const structuralVersion = useGraphStore((s) => s.structuralVersion)

  // ── Local UI state (cheap, ok to recreate) ──
  const [submitting, setSubmitting] = useState(false)
  const [stopping, setStopping] = useState(false)

  const solving = submitting || !!solveJob
  const solveProgress = solveJob?.progress ?? null
  const solveError = solveJob ? solveJob.error : (cachedResult?.error ?? null)
  const solveTerminalStatus = solveJob?.progress ?? cachedResult?.terminalStatus ?? null
  const solveTerminalMetrics = solveTerminalStatus?.execution_metrics ?? null
  // The Solve pane reports the solve itself: the as-solved result, never the
  // frontier point the preview happens to show (a frontier solve opens on point 1).
  const solveResult: OptimiserSolveResult | null = cachedResult?.error ? null : (cachedResult?.originalResult ?? null)
  const solveIterationSummary = solveResult ? formatOptimiserIterationSummary(solveResult) : null
  const setOptimiserPane = useUIStore((s) => s.setOptimiserPane)

  // Where this node logs is its own config, so the default experiment path
  // follows the node's effective destination rather than the workspace's.
  const mlflowDestination = configField(config, "mlflow_destination", "")
  const mlflowExperimentDefault = defaultExperimentName(
    allNodes.find((node) => node.id === nodeId)?.data.label ?? "optimiser",
    effectiveMlflowDestination(mlflowDestination),
  )

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
  const maxCdIterations = configField(config, "max_cd_iterations", 10)
  const cdTolerance = configField(config, "cd_tolerance", 1e-3)
  const frontierSteps = configField(config, "frontier_steps", 15)
  const frontierEnabled = configField(config, "frontier_enabled", false)
  const frontierRanges = configField<Record<string, FrontierRangeConfig>>(config, "frontier_ranges", {})

  // Selectors, data-input columns and every reason the solve cannot start,
  // resolved by the same hook the result preview's Re-run uses.
  const analysisColumns = configField<string[]>(config, "analysis_columns", [])
  const {
    inputs: resolvedInputs,
    dataInputColumns,
    analysisFrameColumns,
    issues: solveIssues,
    canSolve,
    canAutoRange,
  } = useOptimiserReadiness({
    nodeId,
    config,
    allNodes,
    edges,
    submodels,
    fallbackColumns: upstreamColumns,
    fetchColumns: !deferColumnFetch,
  })
  const {
    inputNodes,
    bandingNodes,
    dataInput,
    malformedDataInput,
    missingExplicitDataInput,
    bandingSource,
    malformedBandingSource,
    missingExplicitBandingSource,
    effectiveBandingNode,
  } = resolvedInputs

  const buildGraphCb = useCallback(
    () => buildGraph(allNodes, edges, submodels),
    [allNodes, edges, submodels],
  )
  // The estimate reads the graph upstream of this node, so edits to the node
  // itself (which move the global structural version) must not re-request it.
  const estimateStructureKey = useMemo(() => [
    ...allNodes
      .filter((node) => node.id !== nodeId)
      .map((node) => `${node.id}:${shallowNodeDataHash(node.data)}`),
    ...edges.map((edge) => `${edge.source}>${edge.target}:${edge.sourceHandle ?? ""}:${edge.targetHandle ?? ""}`),
    JSON.stringify(submodels ?? null),
  ].join("\u0001"), [allNodes, edges, nodeId, submodels])
  // A solve result depends on everything but the export settings, and the size
  // estimate only on what shapes the solver's input rows.
  const solveIdentity = useMemo(() => solveIdentityConfig(config), [config])
  const estimateInputs = useMemo(() => ({
    data_input: config.data_input,
    mode: config.mode,
    quote_id: config.quote_id,
    scenario_index: config.scenario_index,
    scenario_value: config.scenario_value,
    banding_source: config.banding_source,
    factor_columns: config.factor_columns,
  }), [
    config.data_input,
    config.mode,
    config.quote_id,
    config.scenario_index,
    config.scenario_value,
    config.banding_source,
    config.factor_columns,
  ])

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
        signal,
      }),
    [buildGraphCb, nodeId, activeSource],
  )
  const {
    isStale,
    estimate: solveEstimate,
  } = useStaleConfigEstimate(
    nodeId,
    solveIdentity,
    cachedResult,
    solveEstimateEndpoint,
    { source: activeSource, structuralVersion },
    {
      toastLabel: "Solve estimate failed",
      enabled: !deferColumnFetch,
      estimateInputs,
      estimateStructureKey,
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
    setSubmitting(true)
    try {
      await startOptimiserSolve({ nodeId, config, allNodes, edges, submodels })
    } finally {
      setSubmitting(false)
    }
  }, [allNodes, config, edges, nodeId, submodels])

  const handleStop = useCallback(async () => {
    setStopping(true)
    try {
      await stopOptimiserSolve(nodeId)
    } catch (error) {
      useToastStore.getState().addToast("error", `Could not stop the optimisation: ${apiErrorMessage(error)}`)
    } finally {
      setStopping(false)
    }
  }, [nodeId])

  const effectiveBandingSource = effectiveBandingNode?.name ?? ""

  const bandingClassification = useMemo(
    () => classifyBandingNode(allNodes.find(node => node.id === effectiveBandingNode?.sourceNodeId), { includeDefault: true }),
    [allNodes, effectiveBandingNode?.sourceNodeId],
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
  const handleBandingSourceChange = useCallback((inputName: string) => {
    const sourceNodeId = bandingNodes.find(node => node.name === inputName)?.sourceNodeId
    const levels = classifyBandingNode(
      allNodes.find(node => node.id === sourceNodeId),
      { includeDefault: true },
    ).levels
    onUpdate({
      banding_source: inputName,
      factor_columns: singleFactorColumnsFromLevels(levels),
    })
  }, [allNodes, bandingNodes, onUpdate])

  // Signals that do not block the solve but usually mean a mis-mapped input.
  const solveWarnings: string[] = []
  const minScenarios = solveEstimate?.scenarios_per_quote_min
  const maxScenarios = solveEstimate?.scenarios_per_quote_max
  if (maxScenarios === 1) {
    solveWarnings.push("Each quote has one scenario, so the optimiser has nothing to choose between. Check the Scenario Index mapping.")
  } else if (minScenarios != null && maxScenarios != null && minScenarios !== maxScenarios) {
    solveWarnings.push(`Quotes have between ${minScenarios.toLocaleString()} and ${maxScenarios.toLocaleString()} scenarios each.`)
  }

  // The host badges the tabs holding a blocking issue; a string key keeps the
  // report to real changes.
  const panesNeedingAttention = [...new Set(solveIssues.map((issue) => issue.pane))].sort().join(",")
  useEffect(() => {
    onPaneIssuesChange?.(
      nodeId,
      panesNeedingAttention ? (panesNeedingAttention.split(",") as OptimiserPane[]) : [],
    )
  }, [nodeId, onPaneIssuesChange, panesNeedingAttention])

  // Ctrl+Enter: a focused field commits its draft on the same keystroke. React
  // renders that commit before a zero-delay timer fires, so the timer calls the
  // latest solve-if-ready, judged against the committed config.
  const solveIfReadyRef = useRef<() => void>(() => {})
  useEffect(() => {
    solveIfReadyRef.current = () => {
      if (canSolve && !solving) void handleSolve()
    }
  })
  const handleEditorKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key !== "Enter" || !(event.ctrlKey || event.metaKey)) return
    event.preventDefault()
    window.setTimeout(() => solveIfReadyRef.current(), 0)
  }

  const inputStyle = { background: "var(--bg-input)", border: "1px solid var(--border)", color: "var(--text-primary)" }
  const pane = resolveOptimiserPane(mode, activePane)
  let paneBody: ReactElement

  if (pane === "data") {
    paneBody = (
      <>
        {/* Mode Toggle */}
        <div>
          <label className={SECTION_LABEL_CLASS} style={{ color: "var(--text-muted)" }}>Mode</label>
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
          <label className={SECTION_LABEL_CLASS} style={{ color: "var(--text-muted)" }}>Objectives & Constraints</label>
          <div className="mt-1.5">
            {inputNodes.length > 0 ? (
              <select
                value={dataInput}
                onChange={(e) => onUpdate("data_input", e.target.value)}
                className="w-full mt-0.5 px-2.5 py-1.5 rounded-lg text-xs"
                style={inputStyle}
              >
                <option value="">Select input...</option>
                {missingExplicitDataInput && dataInput && <option value={dataInput}>Missing input</option>}
                {inputNodes.map(n => (
                  <option key={n.name} value={n.name}>{n.label}</option>
                ))}
              </select>
            ) : (
              <div className="mt-0.5 text-[11px] py-2 text-center" style={{ color: "var(--text-muted)" }}>
                No inputs connected.
              </div>
            )}
            {!missingExplicitDataInput && !dataInput && inputNodes.length > 1 && (
              <div className="mt-1 text-[11px]" style={{ color: "var(--text-muted)" }}>
                Select the Objectives &amp; Constraints input to enable solving.
              </div>
            )}
            {missingExplicitDataInput && (
              <div
                role="alert"
                className="mt-1 px-2.5 py-1.5 rounded text-[11px]"
                style={{
                  color: "var(--warning-strong)",
                  background: "var(--warning-soft)",
                  border: "1px solid var(--warning-border)",
                }}
              >
                {malformedDataInput
                  ? "The configured Objectives & Constraints input must be an input name."
                  : "The configured Objectives & Constraints input is not connected."}
              </div>
            )}
          </div>
        </div>

        {/* Objective */}
        <div>
          <label className={SECTION_LABEL_CLASS} style={{ color: "var(--text-muted)" }}>Objective</label>
          <div className="mt-1.5">
            <label className="text-xs" style={{ color: "var(--text-secondary)" }}>Column to maximise</label>
            <select
              value={objective}
              onChange={(e) => onUpdate("objective", e.target.value)}
              className="w-full mt-0.5 px-2.5 py-1.5 rounded-lg text-xs font-mono"
              style={inputStyle}
            >
              <option value="">Select objective...</option>
              {dataInputColumns.map(c => <option key={c.name} value={c.name}>{c.name} ({c.dtype})</option>)}
            </select>
          </div>
        </div>

        {/* Column Mappings */}
        <div>
          <label className={SECTION_LABEL_CLASS} style={{ color: "var(--text-muted)" }}>Column Mappings</label>
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
                  style={inputStyle}
                >
                  <option value="">Select {field.label.toLowerCase()}...</option>
                  {dataInputColumns.map(c => <option key={c.name} value={c.name}>{c.name}</option>)}
                </select>
              </div>
            ))}
          </div>
        </div>

        <OptimiserAnalysisColumns
          inputs={resolvedInputs}
          frameColumns={analysisFrameColumns}
          quoteIdColumn={quoteId}
          analysisColumns={analysisColumns}
          onUpdate={onUpdate}
          labelClassName={SECTION_LABEL_CLASS}
          inputStyle={inputStyle}
        />
      </>
    )
  } else if (pane === "factors") {
    paneBody = (
      <>
        {/* Banding source selector */}
        <div>
          <label className={SECTION_LABEL_CLASS} style={{ color: "var(--text-muted)" }}>
            Rating Factor Source
          </label>
          {bandingNodes.length > 0 ? (
            <select
              aria-label="Rating Factor Source"
              value={bandingSource}
              onChange={(e) => handleBandingSourceChange(e.target.value)}
              className="w-full mt-1 px-2.5 py-1.5 rounded-lg text-xs"
              style={inputStyle}
            >
              <option value="">Select input...</option>
              {missingExplicitBandingSource && bandingSource && (
                <option value={bandingSource}>Missing input</option>
              )}
              {bandingNodes.map(bn => (
                <option key={bn.name} value={bn.name}>{bn.label}</option>
              ))}
            </select>
          ) : (
            <div className="mt-1 text-[11px] py-2 text-center" style={{ color: "var(--text-muted)" }}>
              No Banding nodes found. Add a Banding node to define rating factors.
            </div>
          )}
        </div>

        {!missingExplicitBandingSource && !bandingSource && bandingNodes.length > 1 && (
          <div className="text-[11px]" style={{ color: "var(--text-muted)" }}>
            Select a Rating Factor Source to enable solving.
          </div>
        )}
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
                ? malformedBandingSource
                  ? "The configured Rating Factor Source must be an input name."
                  : `Selected Banding source ${bandingSource} is no longer directly connected.`
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
            <label className={SECTION_LABEL_CLASS} style={{ color: "var(--text-muted)" }}>
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
      </>
    )
  } else if (pane === "constraints") {
    paneBody = (
      <div>
        <div className="flex items-center justify-between">
          <label className={SECTION_LABEL_CLASS} style={{ color: "var(--text-muted)" }}>
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
          canAutoRange={canAutoRange}
          accentColor={accentColor}
          buildGraph={buildGraphCb}
          nodeId={nodeId}
          onUpdate={onUpdate}
          onRemoveConstraint={handleRemoveConstraint}
          onConstraintColumnChange={handleConstraintColumnChange}
          onConstraintValueChange={handleConstraintValueChange}
        />
      </div>
    )
  } else if (pane === "solve") {
    paneBody = (
      <>
        <OptimiserSolveStatus
          isStale={isStale}
          onSolve={handleSolve}
          onStop={solveJob ? handleStop : undefined}
          stopping={stopping}
          solving={solving}
          canSolve={canSolve}
          issues={solveIssues}
          warnings={solveWarnings}
          onReviewPane={(target) => setOptimiserPane(nodeId, target)}
          accentColor={accentColor}
          estimate={solveEstimate}
          progress={solveProgress}
          error={solveError}
          terminalMetrics={solveTerminalMetrics}
          terminalStatus={solveTerminalStatus}
          result={solveResult}
          iterationSummary={solveIterationSummary}
        />

        {/* Solver settings */}
        <section className="space-y-2 pt-2" style={{ borderTop: "1px solid var(--border)" }} aria-labelledby="optimiser-solver-settings-heading">
          <h3 id="optimiser-solver-settings-heading" className={SECTION_LABEL_CLASS} style={{ color: "var(--text-muted)" }}>
            Solver settings
          </h3>
          <div className="grid grid-cols-2 gap-2">
            <div>
              <label className="text-[11px]" style={{ color: "var(--text-muted)" }}>Max iterations</label>
              <CommittedTextField
                type="number" min={1} step={1}
                value={String(maxIter)}
                onCommit={(v) => onUpdate("max_iter", safeParseInt(v, 50))}
                className="w-full mt-0.5 px-2 py-1 rounded text-xs font-mono"
                style={inputStyle}
              />
            </div>
            <div>
              <label className="text-[11px]" style={{ color: "var(--text-muted)" }}>Tolerance</label>
              <CommittedTextField
                type="number" step={0.000001}
                value={String(tolerance)}
                onCommit={(v) => onUpdate("tolerance", safeParseFloat(v, 1e-6))}
                className="w-full mt-0.5 px-2 py-1 rounded text-xs font-mono"
                style={inputStyle}
              />
            </div>
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
                  style={inputStyle}
                />
              </div>
              <div>
                <label className="text-[11px]" style={{ color: "var(--text-muted)" }}>CD tolerance</label>
                <CommittedTextField
                  type="number" step={0.0001}
                  value={String(cdTolerance)}
                  onCommit={(v) => onUpdate("cd_tolerance", safeParseFloat(v, 1e-3))}
                  className="w-full mt-0.5 px-2 py-1 rounded text-xs font-mono"
                  style={inputStyle}
                />
              </div>
            </div>
          )}
          <div>
            <label className="text-[11px]" style={{ color: "var(--text-muted)" }}>Chunk size</label>
            <CommittedTextField
              type="number" min={1000} step={10000}
              value={String(chunkSize)}
              onCommit={(v) => onUpdate("chunk_size", safeParseInt(v, 500_000))}
              className="w-full mt-0.5 px-2 py-1 rounded text-xs font-mono"
              style={inputStyle}
            />
          </div>
        </section>
      </>
    )
  } else {
    paneBody = (
      <>
      <OptimiserPublishSection
        nodeId={nodeId}
        config={config}
        onUpdate={onUpdate}
        isStale={isStale}
        solving={solving}
        onUpdateNodeConfig={onUpdateNodeConfig}
      />
      <section className="space-y-2" aria-labelledby="optimiser-mlflow-logging-heading">
        <div className="flex items-center gap-1">
          <h3 id="optimiser-mlflow-logging-heading" className={SECTION_LABEL_CLASS} style={{ color: "var(--text-muted)" }}>
            MLflow logging
          </h3>
          <FieldHelpIcon label={MLFLOW_MANUAL_HELP} ariaLabel="About MLflow logging" />
        </div>
        <MlflowDestinationSelector
          value={mlflowDestination}
          onChange={(value) => onUpdate("mlflow_destination", mlflowDestinationConfigValue(value))}
          idPrefix="optimiser-mlflow-destination"
        />
        <div>
          <label className="text-[11px]" style={{ color: "var(--text-muted)" }}>
            <span className="inline-flex items-center gap-1">
              Experiment path
              <FieldHelpIcon
                label={
                  "The MLflow experiment this optimisation result is logged into: a named " +
                  "group that collects related runs so you can compare them. On Databricks " +
                  "it is a workspace folder path; on an MLflow server or local folder it is " +
                  `a plain name. Leave blank to use ${mlflowExperimentDefault}.`
                }
                ariaLabel="About the experiment path"
              />
            </span>
          </label>
          <CommittedTextField
            type="text"
            aria-label="MLflow experiment path"
            placeholder={mlflowExperimentDefault}
            value={configField(config, "mlflow_experiment", "")}
            onCommit={(v) => onUpdate("mlflow_experiment", v)}
            className="w-full mt-0.5 px-2.5 py-1.5 rounded-lg text-xs font-mono"
            style={inputStyle}
          />
        </div>
      </section>
      </>
    )
  }

  return (
    <div
      id={`optimiser-${pane}-pane`}
      role="tabpanel"
      aria-labelledby={`optimiser-${pane}-tab`}
      className="px-4 py-3 space-y-4"
      onKeyDown={handleEditorKeyDown}
    >
      {paneBody}
    </div>
  )
}
