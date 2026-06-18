import { useState, useEffect, useMemo } from "react"
import { InputBindingSelector, MlflowStatusBadge, INPUT_STYLE } from "./_shared"
import type { InputSource, OnUpdateConfig } from "./_shared"
import { RegisteredModelPicker, ExperimentRunPicker } from "./MlflowModelPicker"
import { useMlflowBrowser } from "../../hooks/useMlflowBrowser"
import { configField } from "../../utils/configField"
import { optimiserSelectionMode } from "../../utils/mlflowOptimiser"
import { readJson } from "../../api/client"
import ToggleButtonGroup from "../../components/ToggleButtonGroup"

type ArtifactMeta = {
  version: string
  created_at: string
  mode: string
  objective: string
  lambdas: Record<string, number>
  constraints: Record<string, Record<string, number>>
  factor_tables?: Record<string, unknown[]>
}

type LoadedArtifactMeta = {
  artifactPath: string
  data: ArtifactMeta
}

type ArtifactLoadError = {
  artifactPath: string
  message: string
}

export default function OptimiserApplyEditor({
  config,
  onUpdate,
  inputSources,
  onDeleteInput,
  onRenameInput,
  accentColor,
}: {
  config: Record<string, unknown>
  onUpdate: OnUpdateConfig
  inputSources: InputSource[]
  onDeleteInput?: (edgeId: string) => void
  onRenameInput?: (edgeId: string, alias: string | null) => void
  accentColor: string
}) {
  const sourceType = configField(config, "sourceType", "file")
  const artifactPath = configField(config, "artifact_path", "")
  const versionColumn = configField(config, "version_column", "__optimiser_version__")
  const optimisedValueColumn = configField(config, "optimised_value_column", "")
  const ratebookInput = configField(config, "ratebook_input", "")
  const optimiserMode = configField(config, "optimiser_mode", "")

  const [meta, setMeta] = useState<LoadedArtifactMeta | null>(null)
  const [loadError, setLoadError] = useState<ArtifactLoadError | null>(null)

  const mlflow = useMlflowBrowser({ runTag: "optimiser", initialExpId: configField(config, "experiment_id", "") })
  const loadedMeta = sourceType === "file" && meta?.artifactPath === artifactPath ? meta.data : null
  const activeLoadError = sourceType === "file" && artifactPath && loadError?.artifactPath === artifactPath
    ? loadError.message
    : ""
  const selectedRunId = configField(config, "run_id", "")
  const selectedRun = useMemo(
    () => (sourceType === "run" ? mlflow.runs.find((run) => run.run_id === selectedRunId) : undefined),
    [sourceType, mlflow.runs, selectedRunId],
  )
  const selectedRegisteredModel = configField(config, "registered_model", "")
  const registeredModelVersions = useMemo(
    () => (mlflow.modelVersionsFor === selectedRegisteredModel ? mlflow.modelVersions : []),
    [mlflow.modelVersionsFor, mlflow.modelVersions, selectedRegisteredModel],
  )
  const selectedVersion = configField(config, "version", "latest")
  const selectedRegisteredVersion = useMemo(() => {
    if (sourceType !== "registered") return undefined
    if (selectedVersion === "latest") return registeredModelVersions[0]
    return registeredModelVersions.find((version) => version.version === selectedVersion)
  }, [sourceType, selectedVersion, registeredModelVersions])
  const resolvedOptimiserMode = useMemo(() => {
    if (sourceType === "file" && loadedMeta) return loadedMeta.mode
    if (sourceType === "run" && selectedRun !== undefined) return optimiserSelectionMode(selectedRun)
    if (sourceType === "registered" && selectedRegisteredVersion !== undefined) {
      return optimiserSelectionMode(selectedRegisteredVersion)
    }
    return ""
  }, [sourceType, loadedMeta, selectedRun, selectedRegisteredVersion])
  const isRatebookOptimiser = resolvedOptimiserMode === "ratebook"
  const showRatebookInput = isRatebookOptimiser && (inputSources.length > 1 || Boolean(ratebookInput))
  const ratebookInputSources = useMemo(
    () => (showRatebookInput ? inputSources : []),
    [showRatebookInput, inputSources],
  )
  const hasStaleRatebookInput = Boolean(ratebookInput)
    && !ratebookInputSources.some((source) => source.sourceNodeId === ratebookInput)

  // ``optimiser_mode`` is denormalised into config so codegen can wire up the
  // ratebook input without re-reading the artifact (codegen has no MLflow
  // access).  Mirror it from the resolved artifact metadata whenever it
  // changes; the equality guards keep this from triggering a write loop.
  useEffect(() => {
    if (resolvedOptimiserMode !== "online" && resolvedOptimiserMode !== "ratebook") return
    const updates: Record<string, unknown> = {}
    if (optimiserMode !== resolvedOptimiserMode) {
      updates.optimiser_mode = resolvedOptimiserMode
    }
    if (resolvedOptimiserMode === "online" && ratebookInput) {
      updates.ratebook_input = ""
    }
    if (Object.keys(updates).length > 0) onUpdate(updates)
  }, [optimiserMode, onUpdate, ratebookInput, resolvedOptimiserMode])

  // Load artifact metadata when file path changes
  useEffect(() => {
    if (sourceType !== "file" || !artifactPath) {
      return
    }
    readJson<ArtifactMeta>(artifactPath)
      .then((data) => {
        setMeta({ artifactPath, data })
        setLoadError(null)
      })
      .catch((e: unknown) => {
        console.warn("Artifact load failed:", e)
        setLoadError({ artifactPath, message: "Could not load artifact file" })
        setMeta(null)
      })
  }, [artifactPath, sourceType])

  return (
    <div className="flex-1 flex flex-col min-h-0 px-3 py-2 gap-3">
      <InputBindingSelector inputSources={inputSources} onRenameInput={onRenameInput} onDeleteInput={onDeleteInput} />

      {/* MLflow Status (shown when not in file mode) */}
      {sourceType !== "file" && <MlflowStatusBadge />}

      {showRatebookInput && (
        <div>
          <label
            htmlFor="optimiser-apply-ratebook-input"
            className="text-[11px] font-bold uppercase tracking-[0.08em] block mb-1"
            style={{ color: "var(--text-muted)" }}
          >
            Ratebook Input
          </label>
          <select
            id="optimiser-apply-ratebook-input"
            className="w-full px-2.5 py-1.5 rounded-lg text-[12px] focus:outline-none focus:ring-2"
            style={INPUT_STYLE}
            value={ratebookInput}
            onChange={(e) => onUpdate("ratebook_input", e.target.value)}
          >
            <option value="">First connected input</option>
            {hasStaleRatebookInput && (
              <option value={ratebookInput}>Missing input ({ratebookInput})</option>
            )}
            {ratebookInputSources.map((source) => (
              <option key={source.edgeId} value={source.sourceNodeId}>
                {source.sourceLabel || source.varName}
              </option>
            ))}
          </select>
        </div>
      )}

      {/* Source Type Toggle */}
      <div>
        <label className="text-[11px] font-bold uppercase tracking-[0.08em]" style={{ color: "var(--text-muted)" }}>Artifact Source</label>
        <div className="mt-1">
          <ToggleButtonGroup
            value={sourceType}
            onChange={(v) => onUpdate({ sourceType: v, optimiser_mode: "" })}
            options={[
              { key: "file", label: "File Path" },
              { key: "registered", label: "Registered" },
              { key: "run", label: "Experiment Run" },
            ]}
            accentColor={accentColor}
          />
        </div>
      </div>

      {/* File Path Mode */}
      {sourceType === "file" && (
        <div>
          <label className="text-[11px] font-bold uppercase tracking-[0.08em] block mb-1" style={{ color: 'var(--text-muted)' }}>
            Artifact Path
          </label>
          <input
            type="text"
            className="w-full px-2.5 py-1.5 rounded-lg text-[12px] font-mono focus:outline-none focus:ring-2"
            style={INPUT_STYLE}
            value={artifactPath}
            onChange={(e) => onUpdate("artifact_path", e.target.value)}
            placeholder="artifacts/optimiser_v1.json"
          />
          <p className="text-[10px] mt-1" style={{ color: 'var(--text-muted)' }}>
            Path to saved optimiser result (JSON from the Optimiser Save action)
          </p>
        </div>
      )}

      {/* Registered Model Mode */}
      {sourceType === "registered" && (
        <RegisteredModelPicker config={config} onUpdate={onUpdate} mlflow={mlflow} />
      )}

      {/* Experiment Run Mode */}
      {sourceType === "run" && (
        <ExperimentRunPicker
          config={config}
          onUpdate={onUpdate}
          mlflow={mlflow}
          renderRunLabel={(run) => {
            const mode = optimiserSelectionMode(run)
            return `${run.run_name || run.run_id.slice(0, 8)}${mode ? ` [${mode}]` : ""}${run.metrics.total_objective !== undefined ? ` obj=${run.metrics.total_objective.toFixed(2)}` : ""}`
          }}
        />
      )}

      {/* Version Column */}
      <div>
        <label className="text-[11px] font-bold uppercase tracking-[0.08em] block mb-1" style={{ color: 'var(--text-muted)' }}>
          Version Column
        </label>
        <input
          type="text"
          className="w-full px-2.5 py-1.5 rounded-lg text-[12px] font-mono focus:outline-none focus:ring-2"
          style={INPUT_STYLE}
          value={versionColumn}
          onChange={(e) => onUpdate("version_column", e.target.value)}
          placeholder="__optimiser_version__"
        />
        <p className="text-[10px] mt-1" style={{ color: 'var(--text-muted)' }}>
          Column added to output for monitoring / version tracking
        </p>
      </div>

      {/* Optimised Value Column */}
      <div>
        <label className="text-[11px] font-bold uppercase tracking-[0.08em] block mb-1" style={{ color: 'var(--text-muted)' }}>
          Optimised Value Column
        </label>
        <input
          type="text"
          className="w-full px-2.5 py-1.5 rounded-lg text-[12px] font-mono focus:outline-none focus:ring-2"
          style={INPUT_STYLE}
          value={optimisedValueColumn}
          onChange={(e) => onUpdate("optimised_value_column", e.target.value)}
          placeholder="optimised_value"
        />
        <p className="text-[10px] mt-1" style={{ color: 'var(--text-muted)' }}>
          Column containing the selected optimiser value
        </p>
      </div>

      {/* Artifact metadata display (file mode) */}
      {sourceType === "file" && activeLoadError && (
        <div className="rounded-lg px-3 py-2" style={{ background: 'var(--bg-elevated)', border: `1px solid ${accentColor}` }}>
          <div className="text-[11px]" style={{ color: accentColor }}>{activeLoadError}</div>
        </div>
      )}

      {sourceType === "file" && loadedMeta && <ArtifactMetaPanel meta={loadedMeta} accentColor={accentColor} />}
    </div>
  )
}


function ArtifactMetaPanel({ meta, accentColor }: { meta: ArtifactMeta; accentColor: string }) {
  return (
    <div className="rounded-lg px-3 py-2.5 space-y-2" style={{ background: 'var(--bg-elevated)', border: '1px solid var(--border)' }}>
      <div className="text-[11px] font-bold uppercase tracking-[0.08em]" style={{ color: 'var(--text-muted)' }}>
        Loaded Artifact
      </div>

      <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-[11px] font-mono">
        <span style={{ color: 'var(--text-muted)' }}>Mode</span>
        <span style={{ color: 'var(--text-primary)', fontWeight: 600 }}>{meta.mode}</span>

        <span style={{ color: 'var(--text-muted)' }}>Version</span>
        <span style={{ color: 'var(--text-primary)' }}>{meta.version || "\u2014"}</span>

        <span style={{ color: 'var(--text-muted)' }}>Created</span>
        <span style={{ color: 'var(--text-primary)' }}>
          {meta.created_at ? new Date(meta.created_at).toLocaleDateString() : "\u2014"}
        </span>

        <span style={{ color: 'var(--text-muted)' }}>Objective</span>
        <span style={{ color: 'var(--text-primary)' }}>{meta.objective || "\u2014"}</span>
      </div>

      {/* Lambdas (online mode) */}
      {meta.mode === "online" && meta.lambdas && Object.keys(meta.lambdas).length > 0 && (
        <div>
          <div className="text-[10px] font-bold uppercase tracking-[0.08em] mt-1 mb-0.5" style={{ color: 'var(--text-muted)' }}>
            Lambdas
          </div>
          {Object.entries(meta.lambdas).map(([k, v]) => (
            <div key={k} className="flex justify-between text-[11px] font-mono px-1">
              <span style={{ color: 'var(--text-secondary)' }}>{k}</span>
              <span style={{ color: accentColor, fontWeight: 600 }}>{typeof v === 'number' ? v.toFixed(4) : String(v)}</span>
            </div>
          ))}
        </div>
      )}

      {/* Factor tables (ratebook mode) */}
      {meta.mode === "ratebook" && meta.factor_tables && Object.keys(meta.factor_tables).length > 0 && (
        <div>
          <div className="text-[10px] font-bold uppercase tracking-[0.08em] mt-1 mb-0.5" style={{ color: 'var(--text-muted)' }}>
            Factor Tables
          </div>
          {Object.entries(meta.factor_tables).map(([name, entries]) => (
            <div key={name} className="flex justify-between text-[11px] font-mono px-1">
              <span style={{ color: 'var(--text-secondary)' }}>{name}</span>
              <span style={{ color: 'var(--text-muted)' }}>{Array.isArray(entries) ? entries.length : 0} levels</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
