import { useState } from "react"
import { ChevronDown } from "lucide-react"
import { InputSourcesBar, CodeEditor, MlflowStatusBadge, SELECT_STYLE } from "./_shared"
import type { InputSource, OnUpdateConfig } from "./_shared"
import { RegisteredModelPicker, ExperimentRunPicker } from "./MlflowModelPicker"
import { useMlflowBrowser } from "../../hooks/useMlflowBrowser"
import { configField } from "../../utils/configField"
import ToggleButtonGroup from "../../components/ToggleButtonGroup"

export default function ModelScoreEditor({
  config,
  onUpdate,
  inputSources,
  onDeleteInput,
  errorLine,
  accentColor,
}: {
  config: Record<string, unknown>
  onUpdate: OnUpdateConfig
  inputSources: InputSource[]
  onDeleteInput?: (edgeId: string) => void
  errorLine?: number | null
  accentColor: string
}) {
  const sourceType = configField(config, "sourceType", "registered")
  const task = configField(config, "task", "regression")
  const outputColumn = configField(config, "output_column", "prediction")
  const defaultCode = configField(config, "code", "")

  const mlflow = useMlflowBrowser({ initialExpId: configField(config, "experiment_id", "") })

  const [showCode, setShowCode] = useState(!!defaultCode)

  return (
    <div className="flex-1 flex flex-col min-h-0 px-3 py-2 gap-3">
      <InputSourcesBar inputSources={inputSources} onDeleteInput={onDeleteInput} />

      {/* MLflow Status */}
      <MlflowStatusBadge />

      {/* Source Type Toggle */}
      <div>
        <label className="text-[11px] font-bold uppercase tracking-[0.08em]" style={{ color: "var(--text-muted)" }}>Model Source</label>
        <div className="mt-1">
          <ToggleButtonGroup
            value={sourceType}
            onChange={(v) => onUpdate("sourceType", v)}
            options={[
              { key: "registered", label: "Registered Model" },
              { key: "run", label: "Experiment Run" },
            ]}
            accentColor={accentColor}
          />
        </div>
      </div>

      {/* Registered Model Selection */}
      {sourceType === "registered" && (
        <RegisteredModelPicker config={config} onUpdate={onUpdate} mlflow={mlflow} />
      )}

      {/* Run-based Selection */}
      {sourceType === "run" && (
        <ExperimentRunPicker
          config={config}
          onUpdate={onUpdate}
          mlflow={mlflow}
          showArtifactPath
          onRunSelected={(run) => ({ artifact_path: run.artifacts[0] || "" })}
        />
      )}

      {/* Task and Output Column */}
      <div className="flex gap-2">
        <div className="flex-1">
          <label className="text-[11px] font-bold uppercase tracking-[0.08em]" style={{ color: "var(--text-muted)" }}>Task</label>
          <select
            className="mt-1 w-full text-xs px-2.5 py-1.5 rounded-lg focus:outline-none focus:ring-2"
            style={SELECT_STYLE}
            value={task}
            onChange={(e) => onUpdate("task", e.target.value)}
          >
            <option value="regression">Regression</option>
            <option value="classification">Classification</option>
          </select>
        </div>
        <div className="flex-1">
          <label className="text-[11px] font-bold uppercase tracking-[0.08em]" style={{ color: "var(--text-muted)" }}>Output Column</label>
          <input
            type="text"
            className="mt-1 w-full text-xs px-2.5 py-1.5 rounded-lg focus:outline-none focus:ring-2"
            style={SELECT_STYLE}
            value={outputColumn}
            onChange={(e) => onUpdate("output_column", e.target.value)}
            placeholder="prediction"
          />
        </div>
      </div>

      {task === "classification" && (
        <p className="text-[10px]" style={{ color: "var(--text-muted)" }}>
          Classification models also generate a <code className="px-0.5 rounded" style={{ background: "var(--bg-hover)" }}>{outputColumn}_proba</code> column.
        </p>
      )}

      {/* Optional Post-processing Code */}
      <div>
        <button
          onClick={() => setShowCode(!showCode)}
          className="flex items-center gap-1.5 text-[11px] font-medium transition-colors"
          style={{ color: "var(--text-muted)" }}
        >
          <ChevronDown size={11} style={{ transform: showCode ? "rotate(0deg)" : "rotate(-90deg)", transition: "transform 0.15s" }} />
          Post-processing Code (optional)
        </button>
      </div>
      {showCode && (
        <>
          <CodeEditor
            defaultValue={defaultCode}
            onChange={(val) => onUpdate("code", val)}
            errorLine={errorLine}
            placeholder=""
          />
          <div className="text-[11px] font-mono" style={{ color: 'var(--text-muted)', opacity: 0.6 }}>
            return df
          </div>
        </>
      )}
    </div>
  )
}
