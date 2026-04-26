/**
 * MLflow log-experiment button + result display.
 * Extracted from ModellingConfig.tsx for readability.
 */
import { useState, useCallback } from "react"
import { Loader2, FlaskConical } from "lucide-react"
import { logToMlflow } from "../../api/client"
import { configField } from "../../utils/configField"
import { MODEL_COLORS } from "../../theme/colors"

type MlflowResult = {
  status: string
  backend?: string
  experiment_name?: string
  run_id?: string | null
  run_url?: string | null
  tracking_uri?: string
  error?: string | null
}

type MlflowExportSectionProps = {
  trainJobId: string
  mlflowBackend: { installed: boolean; backend: string; host: string }
  config: Record<string, unknown>
  /** Called before logging to clear any previous MLflow result in the parent */
  onMlflowResult?: (result: MlflowResult | null) => void
}

export function MlflowExportSection({ trainJobId, mlflowBackend, config, onMlflowResult }: MlflowExportSectionProps) {
  const [loggingToMlflow, setLoggingToMlflow] = useState(false)
  const [mlflowResult, setMlflowResult] = useState<MlflowResult | null>(null)

  const handleLogExperiment = useCallback(async () => {
    setLoggingToMlflow(true)
    setMlflowResult(null)
    onMlflowResult?.(null)
    try {
      const result = await logToMlflow({
        job_id: trainJobId,
        experiment_name: configField(config, "mlflow_experiment", "") || null,
        model_name: configField(config, "model_name", "") || null,
      })
      setMlflowResult(result)
      onMlflowResult?.(result)
    } catch (e) {
      const errResult = { status: "error", error: String(e) }
      setMlflowResult(errResult)
      onMlflowResult?.(errResult)
    } finally {
      setLoggingToMlflow(false)
    }
  }, [trainJobId, config, onMlflowResult])

  return (
    <div className="space-y-1.5">
      <button
        onClick={handleLogExperiment}
        disabled={loggingToMlflow}
        className="w-full flex items-center justify-center gap-2 px-3 py-2 rounded-lg text-xs font-medium transition-colors"
        style={{
          background: loggingToMlflow ? "var(--chrome-hover)" : "var(--accent-soft-strong)",
          color: loggingToMlflow ? "var(--text-muted)" : MODEL_COLORS.logAction,
          border: "1px solid var(--accent-ring)",
        }}
      >
        {loggingToMlflow ? <Loader2 size={14} className="animate-spin" /> : <FlaskConical size={14} />}
        {loggingToMlflow ? "Logging..." : `Log to MLflow (${mlflowBackend.backend})`}
      </button>
      {mlflowResult && mlflowResult.status === "ok" && (
        <div className="px-3 py-2 rounded-lg text-xs space-y-0.5" style={{ background: "var(--accent-soft-subtle)", border: "1px solid var(--accent-soft-hover)" }}>
          <div style={{ color: MODEL_COLORS.logAction }}>Logged to {mlflowResult.experiment_name}</div>
          {mlflowResult.run_url && (
            <a href={mlflowResult.run_url} target="_blank" rel="noreferrer" className="underline" style={{ color: "var(--text-accent)" }}>
              Open in Databricks
            </a>
          )}
          {!mlflowResult.run_url && mlflowResult.tracking_uri && (
            <div style={{ color: "var(--text-muted)" }}>Run ID: {mlflowResult.run_id}</div>
          )}
        </div>
      )}
      {mlflowResult && mlflowResult.status === "error" && (
        <div className="px-3 py-2 rounded-lg text-xs" style={{ background: "var(--danger-soft-subtle)", border: "1px solid var(--danger-border)", color: "var(--danger-text-soft)" }}>
          {mlflowResult.error}
        </div>
      )}
    </div>
  )
}
