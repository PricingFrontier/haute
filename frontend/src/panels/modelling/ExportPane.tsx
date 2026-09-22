/**
 * Export pane for the modelling editor: everything that publishes the last
 * trained model — MLflow logging and saving the model to a file.
 *
 * Both actions act on the node's last completed training result. They stay
 * visible but disabled while there is none, or while a new training run for
 * the node is in progress (its publication replaces the model files).
 */
import { AlertTriangle } from "lucide-react"
import { CommittedTextField } from "../../components/form"
import MlflowDestinationSelector from "../../components/MlflowDestinationSelector"
import { useMlflowBrowser } from "../../hooks/useMlflowBrowser"
import { useMlflowDestinations } from "../../stores/useSettingsStore"
import { configField } from "../../utils/configField"
import {
  defaultExperimentName,
  effectiveMlflowDestination,
  mlflowDestinationConfigValue,
  mlflowLogAvailability,
} from "../../utils/mlflowDestinations"
import type { OnUpdateConfig } from "../editors"
import { FieldHelpIcon } from "./FieldHelpIcon"
import { MlflowExportSection } from "./MlflowExportSection"
import { ModelFileExportSection } from "./ModelFileExportSection"
import type { ExportableAlgorithm } from "./modelExport"
import { useExportReceipts } from "./exportReceipts"
import { MODELLING_INPUT_STYLE } from "./styles"

/**
 * The manual-only note. It is help, not state, so it lives behind the MLflow
 * section's Info icon and nowhere else.
 */
const MLFLOW_MANUAL_HELP =
  "Used only when you press “Log run to MLflow” after training completes. " +
  "Nothing is logged automatically."

type ExportPaneProps = {
  algorithm: ExportableAlgorithm
  config: Record<string, unknown>
  onUpdate: OnUpdateConfig
  nodeLabel: string
  /** The last completed job whose model can be exported, if any. */
  trainedJobId: string | null
  training: boolean
  /** The training settings changed since `trainedJobId` was trained. */
  trainedResultStale: boolean
  /** A result restored after a reload is gone from the server. */
  trainedResultExpired?: boolean
}

export function ExportPane({
  algorithm,
  config,
  onUpdate,
  nodeLabel,
  trainedJobId,
  training,
  trainedResultStale,
  trainedResultExpired = false,
}: ExportPaneProps) {
  const exportBlockedReason = training
    ? "Training is running - export is available when it completes."
    : trainedJobId === null
      ? trainedResultExpired
        ? "The last training result for this node is no longer available (the server restarted or it expired). Train this model again to export it."
        : "Train this model to export it."
      : null
  const exportableJobId = exportBlockedReason === null ? trainedJobId : null
  const { receipts, refresh: refreshReceipts } = useExportReceipts(exportableJobId)

  // Where this node logs is its own config, so every derived value below —
  // the default experiment path, whether logging is possible, and which
  // destination the datalist browses — follows `mlflow_destination`.
  const mlflowDestination = configField(config, "mlflow_destination", "")
  const mlflowInventory = useMlflowDestinations()
  const mlflowAvailability = mlflowLogAvailability(mlflowInventory, mlflowDestination)
  const effectiveDestination = effectiveMlflowDestination(mlflowDestination)
  const experimentDefault = defaultExperimentName(nodeLabel, effectiveDestination)

  // Experiment suggestions belong to one backend, and the shared browser hook
  // owns that lifecycle: it scopes every request to this node's destination
  // and drops arrays, guards and in-flight responses whenever the effective
  // destination changes.
  const { experiments, refreshExperiments } = useMlflowBrowser({
    destination: mlflowDestination,
  })
  const loadExperimentOptions = () => {
    if (!mlflowAvailability.available) return
    refreshExperiments()
  }

  return (
    <>
      {exportBlockedReason !== null && (
        <p className="text-[11px]" style={{ color: "var(--text-muted)" }}>
          {exportBlockedReason}
        </p>
      )}
      {exportBlockedReason === null && trainedResultStale && (
        <div
          className="flex items-start gap-2 rounded-lg px-3 py-2 text-xs"
          style={{ background: "var(--warning-soft-subtle)", border: "1px solid var(--warning-border)" }}
        >
          <AlertTriangle size={12} className="mt-0.5 shrink-0" style={{ color: "var(--warning-strong)" }} />
          <span style={{ color: "var(--warning)" }}>
            Training settings changed since this model was trained. Exports use the last trained model.
          </span>
        </div>
      )}
      <section className="space-y-2" aria-labelledby="mlflow-logging-heading">
        <div className="flex items-center gap-1">
          <h3
            id="mlflow-logging-heading"
            className="text-[11px] font-bold uppercase tracking-[0.08em]"
            style={{ color: "var(--text-muted)" }}
          >
            MLflow logging
          </h3>
          <FieldHelpIcon label={MLFLOW_MANUAL_HELP} ariaLabel="About MLflow logging" />
        </div>
        <MlflowDestinationSelector
          value={mlflowDestination}
          onChange={(value) => onUpdate("mlflow_destination", mlflowDestinationConfigValue(value))}
        />
        <label className="block text-[11px]" style={{ color: "var(--text-muted)" }}>
          <span className="inline-flex items-center gap-1">
            Experiment path
            <FieldHelpIcon
              label={
                "The MLflow experiment this run is logged into: a named group that collects " +
                "related runs so you can compare them. On Databricks it is a workspace folder " +
                "path; on an MLflow server or local folder it is a plain name. " +
                `Leave blank to use ${experimentDefault}.`
              }
              ariaLabel="About the experiment path"
            />
          </span>
          <CommittedTextField
            type="text"
            aria-label="MLflow experiment path"
            value={configField(config, "mlflow_experiment", "")}
            onCommit={(value) => onUpdate("mlflow_experiment", value)}
            placeholder={experimentDefault}
            list="mlflow-experiment-options"
            onFocus={loadExperimentOptions}
            className="mt-0.5 w-full rounded-lg px-2.5 py-1.5 text-xs font-mono"
            style={MODELLING_INPUT_STYLE}
          />
          <datalist id="mlflow-experiment-options">
            {experiments.map((experiment) => (
              <option key={experiment.experiment_id} value={experiment.name} />
            ))}
          </datalist>
        </label>
        <MlflowExportSection
          key={exportableJobId ?? "none"}
          trainJobId={exportableJobId}
          config={config}
          lastReceipt={receipts.mlflow.at(-1) ?? null}
          onLogAttempted={refreshReceipts}
        />
      </section>
      <div className="pt-2" style={{ borderTop: "1px solid var(--border)" }}>
        <ModelFileExportSection
          key={exportableJobId ?? "none"}
          trainJobId={exportableJobId}
          algorithm={algorithm}
          config={config}
          onUpdate={onUpdate}
          lastReceipt={receipts.model_files.at(-1) ?? null}
          onSaved={refreshReceipts}
        />
      </div>
    </>
  )
}
