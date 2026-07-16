import { SELECT_STYLE } from "./_shared"
import type { OnUpdateConfig } from "./_shared"
import type { MlflowBrowserState, Run } from "../../hooks/useMlflowBrowser"
import { configField } from "../../utils/configField"
import { CommittedTextField } from "../../components/form"

// ─── Registered Model Picker ─────────────────────────────────────

export interface RegisteredModelPickerProps {
  config: Record<string, unknown>
  onUpdate: OnUpdateConfig
  mlflow: MlflowBrowserState
}

/**
 * Model Name + Version dropdowns for MLflow registered models.
 * Shared between ModelScoreEditor and OptimiserApplyEditor.
 */
export function RegisteredModelPicker({
  config,
  onUpdate,
  mlflow,
}: RegisteredModelPickerProps) {
  const {
    models,
    modelVersions,
    modelVersionsFor,
    loadingModels,
    errorModels,
    errorVersions,
    refreshModels,
    refreshVersions,
  } = mlflow

  const selectedModel = configField(config, "registered_model", "")
  const selectedModelVersions = modelVersionsFor === selectedModel ? modelVersions : []

  return (
    <div className="flex flex-col gap-2">
      <div>
        <label
          className="text-[11px] font-bold uppercase tracking-[0.08em]"
          style={{ color: "var(--text-muted)" }}
        >
          Model Name
        </label>
        <select
          className="mt-1 w-full text-xs px-2.5 py-1.5 rounded-lg focus:outline-none focus:ring-2"
          style={SELECT_STYLE}
          value={selectedModel}
          onFocus={refreshModels}
          onChange={(e) =>
            onUpdate({ registered_model: e.target.value, version: "latest" })
          }
        >
          <option value="">
            {loadingModels ? "Loading..." : "Select a model..."}
          </option>
          {selectedModel &&
            !models.some((m) => m.name === selectedModel) && (
              <option value={selectedModel}>{selectedModel}</option>
            )}
          {models.map((m) => (
            <option key={m.name} value={m.name}>
              {m.name}
            </option>
          ))}
        </select>
        {errorModels && (
          <span className="text-[10px] mt-0.5" style={{ color: "var(--danger)" }}>
            {errorModels}
          </span>
        )}
      </div>
      {selectedModel && (
        <div>
          <label
            className="text-[11px] font-bold uppercase tracking-[0.08em]"
            style={{ color: "var(--text-muted)" }}
          >
            Version
          </label>
          <select
            className="mt-1 w-full text-xs px-2.5 py-1.5 rounded-lg focus:outline-none focus:ring-2"
            style={SELECT_STYLE}
            value={configField(config, "version", "latest")}
            onFocus={() => refreshVersions(selectedModel)}
            onChange={(e) => onUpdate("version", e.target.value)}
          >
            <option value="latest">latest</option>
            {selectedModelVersions.map((v) => (
              <option key={v.version} value={v.version}>
                v{v.version} — {v.status}
                {v.description ? ` (${v.description})` : ""}
              </option>
            ))}
          </select>
          {errorVersions && (
            <span
              className="text-[10px] mt-0.5"
              style={{ color: "var(--danger)" }}
            >
              {errorVersions}
            </span>
          )}
        </div>
      )}
    </div>
  )
}

// ─── Experiment Run Picker ───────────────────────────────────────

export interface ExperimentRunPickerProps {
  config: Record<string, unknown>
  onUpdate: OnUpdateConfig
  mlflow: MlflowBrowserState
  /** Custom render for run <option> labels. Defaults to run_name or truncated run_id. */
  renderRunLabel?: (run: Run) => string
  /** Extra config keys merged into onUpdate when a run is selected (e.g. artifact_path). */
  onRunSelected?: (run: Run) => Record<string, unknown>
  /** Whether to show the "Artifact Path" text input (ModelScoreEditor uses it, OptimiserApplyEditor does not). */
  showArtifactPath?: boolean
}

/**
 * Experiment + Run dropdowns for MLflow experiment-based model selection.
 * Shared between ModelScoreEditor and OptimiserApplyEditor.
 */
export function ExperimentRunPicker({
  config,
  onUpdate,
  mlflow,
  renderRunLabel,
  onRunSelected,
  showArtifactPath = false,
}: ExperimentRunPickerProps) {
  const {
    experiments,
    runs,
    loadingExperiments,
    loadingRuns,
    errorExperiments,
    errorRuns,
    browseExpId,
    setBrowseExpId,
    setRuns,
    resetRunsGuard,
    refreshExperiments,
    refreshRuns,
  } = mlflow

  const defaultRunLabel = (run: Run) => {
    const name = run.run_name || run.run_id.slice(0, 8)
    const metricStr = Object.entries(run.metrics)
      .slice(0, 2)
      .map(([k, v]) => ` ${k}=${typeof v === "number" ? v.toFixed(4) : v}`)
      .join("")
    return `${name}${metricStr}`
  }

  const getRunLabel = renderRunLabel ?? defaultRunLabel

  return (
    <div className="flex flex-col gap-2">
      <div>
        <label
          className="text-[11px] font-bold uppercase tracking-[0.08em]"
          style={{ color: "var(--text-muted)" }}
        >
          Experiment
        </label>
        <select
          className="mt-1 w-full text-xs px-2.5 py-1.5 rounded-lg focus:outline-none focus:ring-2"
          style={SELECT_STYLE}
          value={browseExpId}
          onFocus={refreshExperiments}
          onChange={(e) => {
            const eid = e.target.value
            const exp = experiments.find((x) => x.experiment_id === eid)
            setBrowseExpId(eid)
            onUpdate({ experiment_id: eid, experiment_name: exp?.name || eid })
            setRuns([])
            resetRunsGuard()
            if (eid) refreshRuns(eid)
          }}
        >
          <option value="">
            {loadingExperiments
              ? "Loading..."
              : "Select an experiment..."}
          </option>
          {browseExpId &&
            !experiments.some((e) => e.experiment_id === browseExpId) && (
              <option value={browseExpId}>
                {configField(config, "experiment_name", "") || browseExpId}
              </option>
            )}
          {experiments.map((exp) => (
            <option key={exp.experiment_id} value={exp.experiment_id}>
              {exp.name}
            </option>
          ))}
        </select>
        {errorExperiments && (
          <span className="text-[10px] mt-0.5" style={{ color: "var(--danger)" }}>
            {errorExperiments}
          </span>
        )}
      </div>
      {browseExpId && (
        <div>
          <label
            className="text-[11px] font-bold uppercase tracking-[0.08em]"
            style={{ color: "var(--text-muted)" }}
          >
            Run
          </label>
          <select
            className="mt-1 w-full text-xs px-2.5 py-1.5 rounded-lg focus:outline-none focus:ring-2"
            style={SELECT_STYLE}
            value={configField(config, "run_id", "")}
            onFocus={() => refreshRuns(browseExpId)}
            onChange={(e) => {
              const runId = e.target.value
              const run = runs.find((r) => r.run_id === runId)
              const extra = run && onRunSelected ? onRunSelected(run) : {}
              onUpdate({
                run_id: runId,
                run_name: run?.run_name || "",
                ...extra,
              })
            }}
          >
            <option value="">
              {loadingRuns ? "Loading..." : "Select a run..."}
            </option>
            {configField(config, "run_id", "") &&
              !runs.some((r) => r.run_id === config.run_id) && (
                <option value={configField(config, "run_id", "")}>
                  {configField(config, "run_name", "") ||
                    configField(config, "run_id", "")}
                </option>
              )}
            {runs.map((r) => (
              <option key={r.run_id} value={r.run_id}>
                {getRunLabel(r)}
              </option>
            ))}
          </select>
          {errorRuns && (
            <span
              className="text-[10px] mt-0.5"
              style={{ color: "var(--danger)" }}
            >
              {errorRuns}
            </span>
          )}
        </div>
      )}
      {/* Persisted Run ID — always visible when in run mode */}
      <div>
        <label
          className="text-[11px] font-bold uppercase tracking-[0.08em]"
          style={{ color: "var(--text-muted)" }}
        >
          Run ID
        </label>
        <CommittedTextField
          type="text"
          className="mt-1 w-full text-xs px-2.5 py-1.5 rounded-lg font-mono focus:outline-none focus:ring-2"
          style={SELECT_STYLE}
          value={configField(config, "run_id", "")}
          onCommit={(v) => onUpdate("run_id", v)}
          placeholder="e.g. a1b2c3d4e5f6..."
        />
      </div>
      {showArtifactPath && (
        <div>
          <label
            className="text-[11px] font-bold uppercase tracking-[0.08em]"
            style={{ color: "var(--text-muted)" }}
          >
            Artifact Path
          </label>
          <CommittedTextField
            type="text"
            className="mt-1 w-full text-xs px-2.5 py-1.5 rounded-lg font-mono focus:outline-none focus:ring-2"
            style={SELECT_STYLE}
            value={configField(config, "artifact_path", "")}
            onCommit={(v) => onUpdate("artifact_path", v)}
            placeholder="e.g. model.cbm"
          />
        </div>
      )}
    </div>
  )
}
