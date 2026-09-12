/**
 * Manual "Log run to MLflow" action + result display.
 *
 * Logging is deliberately manual — training never logs automatically — so
 * this section states where a log would go, stays visible (disabled, with
 * the reason and a Configure MLflow link) when tracking is unavailable,
 * and gives every backend a useful success surface: run links for
 * Databricks/server, and the run ID, folder, and a copyable `mlflow ui`
 * command for the local file store.
 */
import { useState, useCallback } from "react"
import { Loader2, FlaskConical, Copy } from "lucide-react"
import { logToMlflow } from "../../api/client"
import { configField } from "../../utils/configField"
import { MODEL_COLORS } from "../../theme/colors"
import { useMlflowStatus } from "../../stores/useSettingsStore"
import useUIStore from "../../stores/useUIStore"

type MlflowResult = {
  status: string
  backend?: string
  experiment_name?: string
  run_id?: string | null
  run_url?: string | null
  tracking_uri?: string
  error?: string | null
}

const MODE_NAMES: Record<string, string> = {
  databricks: "Databricks",
  server: "MLflow server",
  local: "Local folder",
}

type MlflowExportSectionProps = {
  trainJobId: string
  config: Record<string, unknown>
  /** Called before logging to clear any previous MLflow result in the parent */
  onMlflowResult?: (result: MlflowResult | null) => void
}

type TerminalShell = "powershell" | "bash"

function shellQuote(uri: string, shell: TerminalShell) {
  if (shell === "powershell") return `'${uri.replace(/'/g, "''")}'`
  return `'${uri.replace(/'/g, "'\"'\"'")}'`
}

function defaultTerminalShell(): TerminalShell {
  return typeof navigator !== "undefined" && /win/i.test(navigator.platform)
    ? "powershell"
    : "bash"
}

export function MlflowExportSection({ trainJobId, config, onMlflowResult }: MlflowExportSectionProps) {
  const { mlflowStatus, mlflowMode, mlflowDestination, mlflowDetail } = useMlflowStatus()
  const setMlflowSettingsOpen = useUIStore((s) => s.setMlflowSettingsOpen)
  const [loggingToMlflow, setLoggingToMlflow] = useState(false)
  const [mlflowResult, setMlflowResult] = useState<MlflowResult | null>(null)
  const [copyFeedback, setCopyFeedback] = useState<"" | "copied" | "failed">("")
  const [terminalShell, setTerminalShell] = useState<TerminalShell>(defaultTerminalShell)

  const available = mlflowStatus === "connected"

  const handleLogExperiment = useCallback(async () => {
    setLoggingToMlflow(true)
    setMlflowResult(null)
    setCopyFeedback("")
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

  const localCommand = mlflowResult?.tracking_uri
    ? terminalShell === "powershell"
      ? `$env:MLFLOW_ALLOW_FILE_STORE='true'; mlflow ui --backend-store-uri ${shellQuote(mlflowResult.tracking_uri, terminalShell)}`
      : `MLFLOW_ALLOW_FILE_STORE=true mlflow ui --backend-store-uri ${shellQuote(mlflowResult.tracking_uri, terminalShell)}`
    : ""

  return (
    <div className="space-y-1.5">
      <button
        onClick={handleLogExperiment}
        disabled={!available || loggingToMlflow}
        className="w-full flex items-center justify-center gap-2 px-3 py-2 rounded-lg text-xs font-medium transition-colors disabled:opacity-60"
        style={{
          background: loggingToMlflow ? "var(--chrome-hover)" : "var(--accent-soft-strong)",
          color: loggingToMlflow ? "var(--text-muted)" : MODEL_COLORS.logAction,
          border: "1px solid var(--accent-ring)",
        }}
      >
        {loggingToMlflow ? <Loader2 size={14} className="animate-spin" /> : <FlaskConical size={14} />}
        {loggingToMlflow ? "Logging..." : "Log run to MLflow"}
      </button>
      {available ? (
        <p className="text-[10px]" style={{ color: "var(--text-muted)" }}>
          Destination: {MODE_NAMES[mlflowMode] ?? mlflowMode} — {mlflowDestination}
        </p>
      ) : (
        <p className="text-[10px]" style={{ color: "var(--text-muted)" }}>
          {mlflowStatus === "loading"
            ? "Checking MLflow…"
            : `MLflow is off${mlflowDetail ? ` — ${mlflowDetail}` : ""}. `}
          {mlflowStatus !== "loading" && (
            <button
              onClick={() => setMlflowSettingsOpen(true)}
              className="underline"
              style={{ color: "var(--text-accent)" }}
            >
              Configure MLflow
            </button>
          )}
        </p>
      )}
      {mlflowResult && mlflowResult.status === "ok" && (
        <div className="px-3 py-2 rounded-lg text-xs space-y-1" style={{ background: "var(--accent-soft-subtle)", border: "1px solid var(--accent-soft-hover)" }}>
          <div style={{ color: MODEL_COLORS.logAction }}>Logged to {mlflowResult.experiment_name}</div>
          {mlflowResult.run_url && (
            <a href={mlflowResult.run_url} target="_blank" rel="noreferrer" className="underline" style={{ color: "var(--text-accent)" }}>
              {mlflowResult.backend === "databricks" ? "Open in Databricks" : "Open run"}
            </a>
          )}
          {!mlflowResult.run_url && (
            <div className="space-y-1" style={{ color: "var(--text-muted)" }}>
              <div>Run ID: {mlflowResult.run_id}</div>
              {mlflowResult.backend === "local" && localCommand ? (
                <>
                  <div>Browse your runs by starting the MLflow UI from a terminal:</div>
                  <label className="flex items-center gap-1.5">
                    <span>Terminal</span>
                    <select
                      aria-label="Terminal"
                      value={terminalShell}
                      onChange={(event) => {
                        setTerminalShell(event.target.value as TerminalShell)
                        setCopyFeedback("")
                      }}
                      className="rounded px-1 py-0.5 text-[10px]"
                      style={{ background: "var(--bg-input)", border: "1px solid var(--border)" }}
                    >
                      <option value="powershell">PowerShell</option>
                      <option value="bash">bash/zsh</option>
                    </select>
                  </label>
                  <div className="flex items-center gap-1.5">
                    <code
                      className="flex-1 break-all rounded px-1.5 py-1 font-mono text-[10px]"
                      style={{ background: "var(--bg-input)", border: "1px solid var(--border)" }}
                    >
                      {localCommand}
                    </code>
                    <button
                      aria-label="Copy command"
                      title="Copy command"
                      onClick={async () => {
                        try {
                          if (!navigator.clipboard) throw new Error("clipboard unavailable")
                          await navigator.clipboard.writeText(localCommand)
                          setCopyFeedback("copied")
                        } catch {
                          setCopyFeedback("failed")
                        }
                      }}
                      className="rounded p-1"
                      style={{ border: "1px solid var(--border)", color: "var(--text-secondary)" }}
                    >
                      <Copy size={12} aria-hidden="true" />
                    </button>
                  </div>
                  {copyFeedback === "copied" && (
                    <div style={{ color: "var(--success)" }}>Copied</div>
                  )}
                  {copyFeedback === "failed" && (
                    <div style={{ color: "var(--warning-strong)" }}>
                      Copy failed — select the command text manually.
                    </div>
                  )}
                </>
              ) : (
                <div>
                  Run link unavailable — search for this run ID in your MLflow UI.
                </div>
              )}
            </div>
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
