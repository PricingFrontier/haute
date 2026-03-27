import { useState, useEffect } from "react"
import { InputSourcesBar, MlflowStatusBadge, INPUT_STYLE } from "./_shared"
import type { InputSource, OnUpdateConfig } from "./_shared"
import { RegisteredModelPicker, ExperimentRunPicker } from "./MlflowModelPicker"
import { useMlflowBrowser } from "../../hooks/useMlflowBrowser"
import { configField } from "../../utils/configField"
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

export default function OptimiserApplyEditor({
  config,
  onUpdate,
  inputSources,
  onDeleteInput,
  accentColor,
}: {
  config: Record<string, unknown>
  onUpdate: OnUpdateConfig
  inputSources: InputSource[]
  onDeleteInput?: (edgeId: string) => void
  accentColor: string
}) {
  const sourceType = configField(config, "sourceType", "file")
  const artifactPath = configField(config, "artifact_path", "")
  const versionColumn = configField(config, "version_column", "__optimiser_version__")

  const [meta, setMeta] = useState<ArtifactMeta | null>(null)
  const [loadError, setLoadError] = useState("")

  const mlflow = useMlflowBrowser({ runTag: "optimiser", initialExpId: configField(config, "experiment_id", "") })

  // Load artifact metadata when file path changes
  useEffect(() => {
    if (sourceType !== "file" || !artifactPath) {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- cleanup path: clear metadata when source type changes or path is empty
      if (sourceType === "file") { setMeta(null); setLoadError("") }
      return
    }
    readJson<ArtifactMeta>(artifactPath)
      .then((data) => {
        setMeta(data)
        setLoadError("")
      })
      .catch((e: unknown) => {
        console.warn("Artifact load failed:", e)
        setLoadError("Could not load artifact file")
        setMeta(null)
      })
  }, [artifactPath, sourceType])

  return (
    <div className="flex-1 flex flex-col min-h-0 px-3 py-2 gap-3">
      <InputSourcesBar inputSources={inputSources} onDeleteInput={onDeleteInput} />

      {/* MLflow Status (shown when not in file mode) */}
      {sourceType !== "file" && <MlflowStatusBadge />}

      {/* Source Type Toggle */}
      <div>
        <label className="text-[11px] font-bold uppercase tracking-[0.08em]" style={{ color: "var(--text-muted)" }}>Artifact Source</label>
        <div className="mt-1">
          <ToggleButtonGroup
            value={sourceType}
            onChange={(v) => onUpdate("sourceType", v)}
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
            const mode = run.metrics.converged !== undefined ? (run.metrics.cd_iterations !== undefined ? "ratebook" : "online") : ""
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

      {/* Artifact metadata display (file mode) */}
      {sourceType === "file" && loadError && (
        <div className="rounded-lg px-3 py-2" style={{ background: 'var(--bg-elevated)', border: `1px solid ${accentColor}` }}>
          <div className="text-[11px]" style={{ color: accentColor }}>{loadError}</div>
        </div>
      )}

      {sourceType === "file" && meta && <ArtifactMetaPanel meta={meta} accentColor={accentColor} />}
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
