/**
 * Live training progress bar with iteration/loss stats.
 * Extracted from ModellingConfig.tsx for readability.
 */
import type { TrainProgress } from "../../stores/useNodeResultsStore"
import { MODEL_COLORS } from "../../theme/colors"
import { formatElapsed } from "../../utils/formatValue"
import ExecutionDiagnosticsSummary from "../../components/ExecutionDiagnosticsSummary"
import { LossChart } from "./LossChart"

type TrainingProgressProps = {
  trainProgress: TrainProgress
  estimatedRemainingSeconds?: number | null
}

export function TrainingProgress({ trainProgress, estimatedRemainingSeconds = null }: TrainingProgressProps) {
  const tuningParts: string[] = []
  if (trainProgress.phase) {
    if (trainProgress.trial_index != null && trainProgress.trial_count != null) {
      tuningParts.push(`Trial ${trainProgress.trial_index} of ${trainProgress.trial_count}`)
    }
    if (trainProgress.fold_index != null && trainProgress.fold_count != null) {
      tuningParts.push(`Fold ${trainProgress.fold_index} of ${trainProgress.fold_count}`)
    }
    if (trainProgress.completed_fits != null && trainProgress.total_fits != null) {
      tuningParts.push(`${trainProgress.completed_fits} of ${trainProgress.total_fits} fits`)
    }
    if (trainProgress.best_objective != null) {
      tuningParts.push(`Best objective ${trainProgress.best_objective.toFixed(4)}`)
    }
  }

  return (
    <div className="px-3 py-2.5 rounded-lg text-xs space-y-2" style={{ background: "var(--model-accent-soft)", border: "1px solid var(--accent-soft-hover)" }}>
      {/* Progress bar */}
      <div className="space-y-1">
        <div className="flex justify-between text-[11px]">
          <span style={{ color: MODEL_COLORS.accent }}>{trainProgress.message || "Training..."}</span>
          <span style={{ color: "var(--text-muted)" }}>{formatElapsed(trainProgress.elapsed_seconds)}</span>
        </div>
        <div className="w-full h-1.5 rounded-full overflow-hidden" style={{ background: MODEL_COLORS.accentSoft }}>
          <div
            className="h-full rounded-full transition-all duration-300"
            style={{ width: `${Math.max(trainProgress.progress * 100, 2)}%`, background: MODEL_COLORS.accent }}
          />
        </div>
      </div>

      {/* Iteration + loss stats */}
      {tuningParts.length > 0 && (
        <div
          className="text-[11px] font-mono"
          style={{ color: "var(--text-secondary)" }}
        >
          {tuningParts.join(" · ")}
        </div>
      )}
      {!trainProgress.phase && trainProgress.total_iterations > 0 && (
        <div className="flex gap-4 text-[11px] font-mono" style={{ color: "var(--text-secondary)" }}>
          <span>
            Round <span style={{ color: "var(--text-primary)" }}>{trainProgress.iteration}</span>
            /{trainProgress.total_iterations}
          </span>
          {Object.entries(trainProgress.train_loss).map(([name, value]) => (
            <span key={name}>
              {name}: <span style={{ color: "var(--text-primary)" }}>{value.toFixed(4)}</span>
            </span>
          ))}
        </div>
      )}

      {estimatedRemainingSeconds != null && (
        <div className="text-[11px]" style={{ color: "var(--text-muted)" }}>Estimated remaining: {formatElapsed(estimatedRemainingSeconds)}</div>
      )}
      {trainProgress.train_loss_history && (
        <div>
          {trainProgress.train_loss_history_truncated && <p className="text-[10px]" style={{ color: "var(--text-muted)" }}>Showing latest retained loss-history window.</p>}
          <LossChart lossHistory={trainProgress.train_loss_history} />
        </div>
      )}

      <ExecutionDiagnosticsSummary
        metrics={trainProgress.execution_metrics}
        status={trainProgress.status}
        terminalReason={trainProgress.terminal_reason}
      />
    </div>
  )
}
