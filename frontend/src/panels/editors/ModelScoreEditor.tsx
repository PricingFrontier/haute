import { useState } from "react"
import { InputSourcesBar, SELECT_STYLE } from "./_shared"
import type { InputSource, OnUpdateConfig } from "./_shared"
import { RegisteredModelPicker, ExperimentRunPicker } from "./MlflowModelPicker"
import { useMlflowBrowser } from "../../hooks/useMlflowBrowser"
import { configField } from "../../utils/configField"
import MlflowDestinationSelector from "../../components/MlflowDestinationSelector"
import {
  effectiveMlflowDestination,
  mlflowDestinationConfigValue,
} from "../../utils/mlflowDestinations"
import type { MlflowDestinationKey } from "../../api/types"
import ToggleButtonGroup from "../../components/ToggleButtonGroup"
import { CommittedTextField } from "../../components/form"
import {
  recordedModelTask,
  resolveLoadedVersion,
  type ModelTask,
} from "../../utils/mlflowModelMetadata"

const TASK_LABELS: Record<ModelTask, string> = {
  regression: "Regression",
  classification: "Classification",
}

export default function ModelScoreEditor({
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
  errorLine?: number | null
  accentColor: string
}) {
  const sourceType = configField(config, "sourceType", "registered")
  const task = configField(config, "task", "regression")
  const outputColumn = configField(config, "output_column", "prediction")
  const mlflowDestination = configField(config, "mlflow_destination", "")

  const mlflow = useMlflowBrowser({
    destination: mlflowDestination,
    initialExpId: configField(config, "experiment_id", ""),
  })

  // A haute training run records its task, so the selected model's task is
  // shown read-only rather than chosen. Scoring rejects a CatBoost model whose
  // recorded loss contradicts the node's task either way.
  const registeredModel = configField(config, "registered_model", "")
  const selectedParams = sourceType === "run"
    ? mlflow.runs.find((r) => r.run_id === configField(config, "run_id", ""))?.params
    : mlflow.modelVersionsFor === registeredModel && registeredModel
      ? resolveLoadedVersion(
          mlflow.modelVersions,
          configField(config, "version", "latest"),
          configField(config, "alias", ""),
        )?.params
      : undefined
  const recordedTask = recordedModelTask(selectedParams)
  const taskUpdate = (params: Record<string, string> | undefined) => {
    const next = recordedModelTask(params)
    return next ? { task: next } : {}
  }

  // A run id or a registered model name means nothing at another backend, so
  // changing destination drops the whole selection in the same config update
  // and says so until the next pick.
  const [selectionCleared, setSelectionCleared] = useState(false)

  const handleDestinationChange = (next: MlflowDestinationKey) => {
    if (next === effectiveMlflowDestination(mlflowDestination)) return
    onUpdate({
      mlflow_destination: mlflowDestinationConfigValue(next),
      run_id: "",
      run_name: "",
      experiment_id: "",
      experiment_name: "",
      artifact_path: "",
      registered_model: "",
      version: "latest",
      alias: undefined,
    })
    setSelectionCleared(true)
  }

  // Forward the picker's own arguments unchanged: an explicit `undefined`
  // second argument is not the same call as a one-argument update.
  const handlePickerUpdate: OnUpdateConfig = (...args) => {
    setSelectionCleared(false)
    return onUpdate(...args)
  }

  return (
    <div className="flex-1 flex flex-col min-h-0 px-3 py-2 gap-3">
      <InputSourcesBar inputSources={inputSources} onDeleteInput={onDeleteInput} />

      {/* Where this node browses and loads from */}
      <div>
        <MlflowDestinationSelector
          value={mlflowDestination}
          onChange={handleDestinationChange}
          idPrefix="model-score-mlflow-destination"
        />
        {selectionCleared && (
          <p
            data-testid="mlflow-selection-cleared"
            className="mt-1 text-[10px]"
            style={{ color: "var(--warning-strong)" }}
          >
            Selection cleared - run and model identifiers are not portable across destinations.
          </p>
        )}
      </div>

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
        <p className="mt-1 text-[10px]" style={{ color: "var(--text-muted)" }}>
          {sourceType === "registered"
            ? "Registered model - a named, versioned model in the registry (recommended)."
            : "Experiment run - pick one specific training run by experiment."}
        </p>
      </div>

      {/* Registered Model Selection */}
      {sourceType === "registered" && (
        <RegisteredModelPicker
          config={config}
          onUpdate={handlePickerUpdate}
          mlflow={mlflow}
          onVersionSelected={(version) => taskUpdate(version?.params)}
        />
      )}

      {/* Run-based Selection */}
      {sourceType === "run" && (
        <ExperimentRunPicker
          config={config}
          onUpdate={handlePickerUpdate}
          mlflow={mlflow}
          showArtifactPath
          onRunSelected={(run) => ({
            artifact_path: run.artifacts[0] || "",
            ...taskUpdate(run.params),
          })}
        />
      )}

      {/* Task and Output Column */}
      <div className="flex gap-2">
        <div className="flex-1">
          <label className="text-[11px] font-bold uppercase tracking-[0.08em]" style={{ color: "var(--text-muted)" }}>Task</label>
          {recordedTask ? (
            <p
              data-testid="model-score-recorded-task"
              className="mt-1 w-full text-xs px-2.5 py-1.5 rounded-lg"
              style={{ ...SELECT_STYLE, color: "var(--text-muted)" }}
            >
              {TASK_LABELS[recordedTask]}
            </p>
          ) : (
            <select
              className="mt-1 w-full text-xs px-2.5 py-1.5 rounded-lg focus:outline-none focus:ring-2"
              style={SELECT_STYLE}
              value={task}
              onChange={(e) => onUpdate("task", e.target.value)}
            >
              <option value="regression">Regression</option>
              <option value="classification">Classification</option>
            </select>
          )}
        </div>
        <div className="flex-1">
          <label className="text-[11px] font-bold uppercase tracking-[0.08em]" style={{ color: "var(--text-muted)" }}>Output Column</label>
          <CommittedTextField
            type="text"
            className="mt-1 w-full text-xs px-2.5 py-1.5 rounded-lg focus:outline-none focus:ring-2"
            style={SELECT_STYLE}
            value={outputColumn}
            onCommit={(v) => onUpdate("output_column", v)}
            placeholder="prediction"
          />
        </div>
      </div>

      {recordedTask && (
        <p className="text-[10px]" style={{ color: "var(--text-muted)" }}>
          Task recorded by the training run.
        </p>
      )}
      {recordedTask && recordedTask !== task && (
        <div role="alert" className="flex items-center gap-2 text-[10px]" style={{ color: "var(--warning-strong)" }}>
          <span>This node scores as {task}, but the model was trained for {recordedTask}.</span>
          <button
            type="button"
            className="underline"
            onClick={() => onUpdate("task", recordedTask)}
          >
            Use {recordedTask}
          </button>
        </div>
      )}

      {task === "classification" && (
        <p className="text-[10px]" style={{ color: "var(--text-muted)" }}>
          Classification models also generate a <code className="px-0.5 rounded" style={{ background: "var(--bg-hover)" }}>{outputColumn}_proba</code> column.
        </p>
      )}

    </div>
  )
}
