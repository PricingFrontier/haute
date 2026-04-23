/**
 * Live training progress bar with iteration/loss stats.
 * Extracted from ModellingConfig.tsx for readability.
 */
import type { TrainProgress } from "../../stores/useNodeResultsStore"
import { MODEL_COLORS } from "../../theme/colors"
import { formatElapsed } from "../../utils/formatValue"

type TrainingProgressProps = {
  trainProgress: TrainProgress
}

export function TrainingProgress({ trainProgress }: TrainingProgressProps) {
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
      {trainProgress.total_iterations > 0 && (
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
    </div>
  )
}
