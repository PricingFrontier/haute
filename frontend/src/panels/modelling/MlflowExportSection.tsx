/**
 * Manual "Log run to MLflow" action + result display.
 *
 * Logging is deliberately manual — training never logs automatically — so
 * this section states where a log would go (the node's own
 * `mlflow_destination`, or the local folder when it names none), stays visible
 * (disabled, with the reason and a Configure MLflow link) when *that*
 * destination cannot accept a log — never because some other remote is
 * unconfigured. A successful log shows only the run ID, plus the run link when
 * a Databricks or server backend returns one.
 *
 * The server records every successful log on the training job, so the section
 * says where the result was last logged, asks before logging it again as a new
 * run, and retries a failed attempt with the same operation ID — a log whose
 * response was lost then returns the run it created instead of a duplicate.
 */
import { useState, useCallback } from "react"
import { Loader2, FlaskConical } from "lucide-react"
import { logToMlflow } from "../../api/client"
import { ApiError } from "../../api/client"
import { apiErrorCode, apiErrorMessage } from "../../api/errors"
import { newOperationId } from "./exportReceipts"
import { configField } from "../../utils/configField"
import { MODEL_COLORS } from "../../theme/colors"
import { useMlflowDestinations } from "../../stores/useSettingsStore"
import useUIStore from "../../stores/useUIStore"
import {
  MLFLOW_DESTINATION_LABELS,
  isMlflowDestinationKey,
  mlflowLogAvailability,
} from "../../utils/mlflowDestinations"
import type { MlflowDestinationKey, MlflowExportReceipt } from "../../api/types"

type MlflowResult = {
  status: string
  backend?: string
  experiment_name?: string
  run_id?: string | null
  run_url?: string | null
  tracking_uri?: string
  error?: string | null
  /** The server's `mlflow_<category>` code for a classified remote failure. */
  error_code?: string | null
  /** The failed attempt's operation, reused by Retry. */
  operation_id?: string | null
}

/** Remote failures the user resolves from MLflow settings (test or fix the connection). */
const CONNECTION_ERROR_CODES = new Set(["mlflow_connectivity", "mlflow_authentication"])

type MlflowExportSectionProps = {
  /** The completed job to log, or null when no trained model is exportable. */
  trainJobId: string | null
  config: Record<string, unknown>
  /** Called before logging to clear any previous MLflow result in the parent */
  onMlflowResult?: (result: MlflowResult | null) => void
  /** The job's most recent recorded log, if it was logged before. */
  lastReceipt?: MlflowExportReceipt | null
  /** Called after every attempt so the parent can re-read the job's receipts. */
  onLogAttempted?: () => void
}

function receiptDestinationLabel(receipt: MlflowExportReceipt): string {
  return isMlflowDestinationKey(receipt.backend) ? MLFLOW_DESTINATION_LABELS[receipt.backend] : receipt.backend
}

export function MlflowExportSection({
  trainJobId,
  config,
  onMlflowResult,
  lastReceipt = null,
  onLogAttempted,
}: MlflowExportSectionProps) {
  // The node's own destination decides everything here: an unconfigured
  // remote the node names disables the action with that remote's reason, and
  // a remote nobody selected never does.
  const inventory = useMlflowDestinations()
  const availability = mlflowLogAvailability(
    inventory,
    configField(config, "mlflow_destination", ""),
  )
  const setMlflowSettingsOpen = useUIStore((s) => s.setMlflowSettingsOpen)
  const [loggingToMlflow, setLoggingToMlflow] = useState(false)
  const [mlflowResult, setMlflowResult] = useState<MlflowResult | null>(null)
  const [confirmingLogAgain, setConfirmingLogAgain] = useState(false)

  const logOperation = useCallback(async (operationId: string) => {
    if (!trainJobId) return
    setLoggingToMlflow(true)
    setConfirmingLogAgain(false)
    setMlflowResult(null)
    onMlflowResult?.(null)
    try {
      const result = await logToMlflow({
        job_id: trainJobId,
        experiment_name: configField(config, "mlflow_experiment", "") || null,
        // Read at click time, so choosing Local folder after training sends "".
        destination: configField(config, "mlflow_destination", "") as "" | MlflowDestinationKey,
        operation_id: operationId,
      })
      setMlflowResult(result)
      onMlflowResult?.(result)
    } catch (e) {
      const errResult = {
        status: "error",
        error: apiErrorMessage(e, "Logging to MLflow failed."),
        error_code: apiErrorCode(e),
        // A response that never arrived may still have logged the run, so Retry
        // resends this operation; a refused request can start a new one.
        operation_id: e instanceof ApiError ? null : operationId,
      }
      setMlflowResult(errResult)
      onMlflowResult?.(errResult)
    } finally {
      setLoggingToMlflow(false)
      onLogAttempted?.()
    }
  }, [trainJobId, config, onMlflowResult, onLogAttempted])

  const handleLogExperiment = useCallback(() => {
    if (lastReceipt && !confirmingLogAgain) {
      setConfirmingLogAgain(true)
      return
    }
    void logOperation(newOperationId())
  }, [lastReceipt, confirmingLogAgain, logOperation])

  return (
    <div className="space-y-1.5">
      <button
        onClick={handleLogExperiment}
        disabled={!trainJobId || !availability.available || loggingToMlflow}
        className="w-full flex items-center justify-center gap-2 px-3 py-2 rounded-lg text-xs font-medium transition-colors disabled:opacity-60"
        style={{
          background: loggingToMlflow ? "var(--chrome-hover)" : "var(--accent-soft-strong)",
          color: loggingToMlflow ? "var(--text-muted)" : MODEL_COLORS.logAction,
          border: "1px solid var(--accent-ring)",
        }}
      >
        {loggingToMlflow ? <Loader2 size={14} className="animate-spin" /> : <FlaskConical size={14} />}
        {loggingToMlflow ? "Logging..." : lastReceipt ? "Log again" : "Log run to MLflow"}
      </button>
      {lastReceipt && !mlflowResult && (
        <p data-testid="mlflow-last-logged" className="text-[10px]" style={{ color: "var(--text-muted)" }}>
          {`Last logged to ${receiptDestinationLabel(lastReceipt)} · ${lastReceipt.experiment_name} · `}
          {lastReceipt.run_url ? (
            <a href={lastReceipt.run_url} target="_blank" rel="noreferrer" className="underline" style={{ color: "var(--text-accent)" }}>
              Open run
            </a>
          ) : (
            <span className="font-mono">run {lastReceipt.run_id}</span>
          )}
        </p>
      )}
      {confirmingLogAgain && lastReceipt && (
        <div role="alert" className="px-3 py-2 rounded-lg text-xs space-y-1" style={{ background: "var(--warning-soft-subtle)", border: "1px solid var(--warning-border)", color: "var(--warning)" }}>
          <div>
            {`This result is already logged to ${lastReceipt.experiment_name}. Logging again creates a new run.`}
          </div>
          <div className="flex gap-2">
            <button type="button" onClick={handleLogExperiment} className="underline" style={{ color: "var(--text-accent)" }}>
              Log as a new run
            </button>
            <button type="button" onClick={() => setConfirmingLogAgain(false)} className="underline" style={{ color: "var(--text-muted)" }}>
              Cancel
            </button>
          </div>
        </div>
      )}
      <p
        data-testid="mlflow-export-destination"
        className="text-[10px]"
        style={{ color: "var(--text-muted)" }}
      >
        {availability.available ? (
          `Destination: ${availability.label} - ${availability.destination}`
        ) : (
          <>
            {`${availability.reason} `}
            {inventory.status !== "loading" && (
              <button
                onClick={() => setMlflowSettingsOpen(true)}
                className="underline"
                style={{ color: "var(--text-accent)" }}
              >
                Configure MLflow
              </button>
            )}
          </>
        )}
      </p>
      {mlflowResult && mlflowResult.status === "ok" && (
        <div
          data-testid="mlflow-log-success"
          className="px-3 py-2 rounded-lg text-xs space-y-1"
          style={{ background: "var(--accent-soft-subtle)", border: "1px solid var(--accent-soft-hover)" }}
        >
          <div style={{ color: "var(--text-muted)" }}>Run ID: {mlflowResult.run_id}</div>
          {mlflowResult.run_url && (
            <a href={mlflowResult.run_url} target="_blank" rel="noreferrer" className="underline" style={{ color: "var(--text-accent)" }}>
              {mlflowResult.backend === "databricks" ? "Open in Databricks" : "Open run"}
            </a>
          )}
        </div>
      )}
      {mlflowResult && mlflowResult.status === "error" && (
        <div role="alert" className="px-3 py-2 rounded-lg text-xs space-y-1" style={{ background: "var(--danger-soft-subtle)", border: "1px solid var(--danger-border)", color: "var(--danger-text-soft)" }}>
          <div>{mlflowResult.error}</div>
          {mlflowResult.error_code && CONNECTION_ERROR_CODES.has(mlflowResult.error_code) && (
            <button
              onClick={() => setMlflowSettingsOpen(true)}
              className="underline"
              style={{ color: "var(--text-accent)" }}
            >
              Test connection in MLflow settings
            </button>
          )}
          {mlflowResult.operation_id && (
            <button
              type="button"
              onClick={() => void logOperation(mlflowResult.operation_id!)}
              className="underline ml-2"
              style={{ color: "var(--text-accent)" }}
            >
              Retry
            </button>
          )}
        </div>
      )}
    </div>
  )
}
