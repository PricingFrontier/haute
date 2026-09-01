import { AlertTriangle, Loader2, RefreshCw, Target } from "lucide-react"
import type {
  ExecutionMetrics,
  OptimiserEstimate,
  OptimiserSolveResult,
} from "../../api/types"
import ExecutionDiagnosticsSummary from "../../components/ExecutionDiagnosticsSummary"
import type { SolveProgress } from "../../stores/useNodeResultsStore"
import { withAlpha } from "../../utils/color"
import { formatElapsed } from "../../utils/formatValue"
import type { IterationSummary } from "./iterationSummary"

type OptimiserSolveStatusProps = {
  isStale: boolean
  onSolve: () => void
  solving: boolean
  canSolve: boolean
  accentColor: string
  estimate: OptimiserEstimate | null
  progress: SolveProgress | null
  error: string | null
  terminalMetrics: ExecutionMetrics | null
  terminalStatus: Pick<SolveProgress, "status" | "terminal_reason"> | null
  result: OptimiserSolveResult | null
  iterationSummary: IterationSummary | null
}

function formatScenariosPerQuote(
  min?: number | null,
  max?: number | null,
  mean?: number | null,
): string {
  if (min == null && max == null) {
    return mean == null
      ? ""
      : mean.toLocaleString(undefined, { maximumFractionDigits: 1 })
  }
  if (min != null && max != null) {
    return min === max
      ? min.toLocaleString()
      : `${min.toLocaleString()}-${max.toLocaleString()}`
  }
  return (min ?? max)?.toLocaleString() ?? ""
}

export default function OptimiserSolveStatus({
  isStale,
  onSolve,
  solving,
  canSolve,
  accentColor,
  estimate,
  progress,
  error,
  terminalMetrics,
  terminalStatus,
  result,
  iterationSummary,
}: OptimiserSolveStatusProps) {
  return (
    <>
      {/* Staleness indicator */}
      {isStale && (
        <div
          className="flex items-center gap-2 px-3 py-2 rounded-lg text-xs"
          style={{
            background: "var(--warning-soft)",
            border: "1px solid var(--warning-border)",
          }}
        >
          <RefreshCw
            size={12}
            style={{ color: "var(--warning-strong)" }}
            className="shrink-0"
          />
          <span style={{ color: "var(--warning)" }}>
            Config changed since last solve
          </span>
          <button
            onClick={onSolve}
            disabled={solving || !canSolve}
            className="ml-auto px-2 py-0.5 rounded text-[11px] font-medium"
            style={{ background: withAlpha(accentColor, 0.15), color: accentColor }}
          >
            Re-run
          </button>
        </div>
      )}

      {/* Source size preview (hidden when unreadable — metadata isn't available for live data) */}
      {estimate && estimate.quote_count != null && estimate.expanded_row_count != null && (
        <div
          className="grid grid-cols-3 gap-2 px-3 py-2 rounded-lg text-[11px]"
          style={{
            background: "var(--bg-panel)",
            border: "1px solid var(--border)",
          }}
        >
          <div className="min-w-0">
            <div style={{ color: "var(--text-muted)" }}>Quotes</div>
            <div className="font-mono truncate" style={{ color: "var(--text-primary)" }}>
              {estimate.quote_count.toLocaleString()}
            </div>
          </div>
          <div className="min-w-0">
            <div style={{ color: "var(--text-muted)" }}>Scenarios / quote</div>
            <div className="font-mono truncate" style={{ color: "var(--text-primary)" }}>
              {formatScenariosPerQuote(
                estimate.scenarios_per_quote_min,
                estimate.scenarios_per_quote_max,
                estimate.scenarios_per_quote_mean,
              )}
            </div>
          </div>
          <div className="min-w-0">
            <div style={{ color: "var(--text-muted)" }}>Total rows</div>
            <div className="font-mono truncate" style={{ color: "var(--text-primary)" }}>
              {estimate.expanded_row_count.toLocaleString()}
            </div>
          </div>
        </div>
      )}

      {/* Actions */}
      <div className="space-y-2 pt-2" style={{ borderTop: "1px solid var(--border)" }}>
        {solving ? (
          <div
            className="px-3 py-2.5 rounded-lg text-xs space-y-2"
            style={{
              background: withAlpha(accentColor, 0.06),
              border: `1px solid ${withAlpha(accentColor, 0.2)}`,
            }}
          >
            {progress ? (
              <div className="space-y-1">
                <div className="flex justify-between text-[11px]">
                  <span className="flex items-center gap-1.5" style={{ color: accentColor }}>
                    <Loader2 size={12} className="animate-spin shrink-0" />
                    {progress.message || "Solving..."}
                  </span>
                  <span style={{ color: "var(--text-muted)" }}>
                    {formatElapsed(progress.elapsed_seconds)}
                  </span>
                </div>
                <div
                  className="w-full h-1.5 rounded-full overflow-hidden"
                  style={{ background: withAlpha(accentColor, 0.15) }}
                >
                  <div
                    className="h-full rounded-full transition-all duration-300"
                    style={{ width: `${Math.max(progress.progress * 100, 2)}%`, background: accentColor }}
                  />
                </div>
                <ExecutionDiagnosticsSummary
                  metrics={progress.execution_metrics}
                  status={progress.status}
                  terminalReason={progress.terminal_reason}
                />
              </div>
            ) : (
              <div className="flex items-center gap-2">
                <Loader2
                  size={12}
                  className="animate-spin shrink-0"
                  style={{ color: accentColor }}
                />
                <span style={{ color: accentColor }}>Executing pipeline...</span>
              </div>
            )}
          </div>
        ) : (
          <button
            onClick={onSolve}
            disabled={!canSolve}
            className="w-full flex items-center justify-center gap-2 px-3 py-2 rounded-lg text-xs font-medium transition-colors"
            style={{ background: accentColor, color: "var(--text-on-accent)", opacity: !canSolve ? 0.5 : 1 }}
          >
            <Target size={14} />
            Optimise
          </button>
        )}
      </div>

      {/* Error */}
      {error && (
        <div
          className="px-3 py-2.5 rounded-lg text-xs space-y-1.5"
          style={{
            background: "var(--danger-soft-subtle)",
            border: "1px solid var(--danger-border)",
          }}
        >
          <div className="flex items-start gap-2">
            <AlertTriangle
              size={14}
              className="shrink-0 mt-0.5"
              style={{ color: "var(--danger)" }}
            />
            <div className="space-y-1 min-w-0">
              <div className="font-semibold" style={{ color: "var(--danger)" }}>
                Optimisation failed
              </div>
              <div style={{ color: "var(--danger-text-soft)", lineHeight: "1.5" }}>
                {error}
              </div>
              <ExecutionDiagnosticsSummary
                metrics={terminalMetrics}
                status={terminalStatus?.status}
                terminalReason={terminalStatus?.terminal_reason}
              />
            </div>
          </div>
        </div>
      )}

      {/* Results */}
      {result && (
        <div className="space-y-2">
          {!result.converged && (
            <div
              className="flex items-start gap-2 px-3 py-2 rounded-lg text-xs"
              style={{
                background: "var(--warning-soft-strong)",
                border: "1px solid var(--warning-border-strong)",
              }}
            >
              <AlertTriangle
                size={14}
                className="shrink-0 mt-0.5"
                style={{ color: "var(--warning-strong)" }}
              />
              <div>
                <div
                  className="font-semibold"
                  style={{ color: "var(--warning-strong)" }}
                >
                  Solver did not converge
                </div>
                <div style={{ color: "var(--warning)", lineHeight: "1.5" }}>
                  {result.warning || "Try increasing max iterations or relaxing the tolerance."}
                </div>
              </div>
            </div>
          )}

          <div
            className="px-3 py-2 rounded-lg text-xs space-y-1"
            style={{
              background: result.converged
                ? "var(--banner-success-bg)"
                : "var(--warning-soft-subtle)",
              border: `1px solid ${
                result.converged
                  ? "var(--banner-success-border)"
                  : "var(--warning-soft-selected)"
              }`,
            }}
          >
            <div
              style={{
                color: result.converged
                  ? "var(--banner-success-text)"
                  : "var(--warning-strong)",
              }}
            >
              {result.converged ? "Converged" : "Did not converge"}
              {iterationSummary ? ` in ${iterationSummary.long}` : ""}
              {result.n_quotes != null && result.n_steps != null && (
                <>
                  {" "}({result.n_quotes.toLocaleString()} quotes, {result.n_steps} steps)
                </>
              )}
            </div>
          </div>
        </div>
      )}
    </>
  )
}
