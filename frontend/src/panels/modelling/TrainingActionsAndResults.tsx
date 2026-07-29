import { useMemo } from "react"
import { Play, Loader2, AlertTriangle, RefreshCw, CheckCircle2, Database, XCircle } from "lucide-react"
import type { TrainResult, TrainProgress } from "../../stores/useNodeResultsStore"
import type { TrainEstimate } from "../../api/types"
import { MODEL_COLORS } from "../../theme/colors"
import { TrainingProgress as TrainingProgressPanel } from "./TrainingProgress"
import ExecutionDiagnosticsSummary from "../../components/ExecutionDiagnosticsSummary"
import type { ExecutionMetrics } from "../../api/types"

// The backend's bytes_per_row already includes full phase-model overhead
// (evaluation partitions, pools, CatBoost internals, diagnostics, tuning).
// No extra multiplier.
const TRAINING_OVERHEAD = 1.0

function formatMb(mb: number): string {
  return mb < 1024 ? `${mb.toFixed(0)} MB` : `${(mb / 1024).toFixed(1)} GB`
}

export type TrainingActionsAndResultsProps = {
  /** Validation messages to reveal after an invalid training attempt. */
  validationMessages?: readonly string[]
  training: boolean
  trainProgress: TrainProgress | null
  estimatedRemainingSeconds?: number | null
  trainResult: TrainResult | null
  isStale: boolean
  ramEstimate: TrainEstimate | null
  ramEstimateLoading: boolean
  ramEstimateError?: string | null
  rowLimit: number | null
  terminalMetrics?: ExecutionMetrics | null
  terminalStatus?: string | null
  terminalReason?: string | null
  /** True while the short start request is waiting for its cancellable job handle. */
  submitting?: boolean
  cancelling?: boolean
  tuningEnabled?: boolean
  onTrain: () => void
  onCancel: () => void
}

export function TrainingActionsAndResults({
  validationMessages = [],
  training,
  trainProgress,
  estimatedRemainingSeconds = null,
  trainResult,
  isStale,
  ramEstimate,
  ramEstimateLoading,
  ramEstimateError = null,
  rowLimit,
  terminalMetrics = null,
  terminalStatus = null,
  terminalReason = null,
  submitting = false,
  cancelling = false,
  tuningEnabled = false,
  onTrain,
  onCancel,
}: TrainingActionsAndResultsProps) {
  // Recalculate training MB and GPU VRAM reactively as row_limit changes
  const adjusted = useMemo(() => {
    if (!ramEstimate || ramEstimate.total_rows == null) return null
    const sourceRows = ramEstimate.total_rows
    const hasUserLimit = rowLimit != null && rowLimit > 0

    // Effective rows for RAM: user limit, then RAM-safe limit, capped at source
    let rows = sourceRows
    if (hasUserLimit) rows = Math.min(rows, rowLimit)
    if (ramEstimate.safe_row_limit != null) rows = Math.min(rows, ramEstimate.safe_row_limit)

    const trainingMb = rows * ramEstimate.bytes_per_row * TRAINING_OVERHEAD / (1024 * 1024)
    const isLimited = rows < sourceRows

    // Amber when RAM requires downsampling, unless the user's limit
    // is already at or below the safe limit (they've preempted it)
    const wasDownsampled = ramEstimate.was_downsampled
      && !(hasUserLimit && rowLimit <= (ramEstimate.safe_row_limit ?? sourceRows))

    // GPU VRAM: scale from the backend estimate using effective rows.
    // Unlike RAM, don't clamp to safe_row_limit — show what the user's
    // chosen row count would actually need on the GPU.
    let gpuVramMb = ramEstimate.gpu_vram_estimated_mb ?? null
    if (gpuVramMb != null) {
      const gpuRows = hasUserLimit ? Math.min(rowLimit, sourceRows) : sourceRows
      const originalRows = ramEstimate.safe_row_limit ?? sourceRows
      if (originalRows > 0 && gpuRows !== originalRows) {
        gpuVramMb = gpuVramMb * gpuRows / originalRows
      }
    }

    return { rows, trainingMb, wasDownsampled, isLimited, gpuVramMb }
  }, [ramEstimate, rowLimit])

  const busy = submitting || training
  const trainIcon = submitting
    ? <Database size={14} className="animate-pulse" />
    : training
      ? <Loader2 size={14} className="animate-spin" />
      : <Play size={14} />
  const trainLabel = submitting
    ? `Preparing ${tuningEnabled ? "tuning" : "training"} data...`
    : training
      ? (trainProgress?.message || "Training...")
      : tuningEnabled
        ? "Tune & Train"
        : "Train Model"

  return (
    <>
      {/* Staleness indicator */}
      {isStale && (
        <div className="flex items-center gap-2 px-3 py-2 rounded-lg text-xs" style={{ background: "var(--warning-soft)", border: "1px solid var(--warning-border)" }}>
          <RefreshCw size={12} style={{ color: "var(--warning-strong)" }} className="shrink-0" />
        <span style={{ color: "var(--warning)" }}>Config changed since last training</span>
          <button
            onClick={onTrain}
            disabled={training || submitting}
            className="ml-auto px-2 py-0.5 rounded text-[11px] font-medium"
            style={{ background: MODEL_COLORS.accentSoft, color: MODEL_COLORS.accent }}
          >
            Re-train
          </button>
        </div>
      )}

      {/* RAM Estimate */}
      {ramEstimateLoading && (
        <div className="flex items-center gap-2 px-3 py-2 rounded-lg text-xs" style={{ background: "var(--model-accent-soft)", border: "1px solid var(--accent-soft-hover)" }}>
          <Loader2 size={12} className="animate-spin" style={{ color: MODEL_COLORS.accent }} />
          <span style={{ color: "var(--text-muted)" }}>Estimating dataset size...</span>
        </div>
      )}
      {ramEstimateError && !ramEstimateLoading && !ramEstimate && (
        <div className="flex items-center gap-2 px-3 py-2 rounded-lg text-xs" style={{ background: "var(--warning-soft-subtle)", border: "1px solid var(--warning-border)" }}>
          <AlertTriangle size={12} className="shrink-0" style={{ color: "var(--warning-strong)" }} />
          <span style={{ color: "var(--warning)" }}>RAM estimate unavailable — training will still work</span>
        </div>
      )}
      {ramEstimate && !ramEstimateLoading && adjusted && (
        <div className="px-3 py-2.5 rounded-lg text-xs space-y-1.5" style={{
          background: adjusted.wasDownsampled ? "var(--warning-soft-subtle)" : "var(--success-soft-subtle)",
          border: `1px solid ${adjusted.wasDownsampled ? "var(--warning-border)" : "var(--success-soft-strong)"}`,
        }}>
          <div className="flex items-center gap-2">
            {adjusted.wasDownsampled && <AlertTriangle size={12} className="shrink-0" style={{ color: "var(--warning-strong)" }} />}
            <span className="font-medium" style={{ color: adjusted.wasDownsampled ? "var(--warning)" : "var(--success)" }}>
              {adjusted.wasDownsampled ? "Will downsample" : "Dataset fits in memory"}
            </span>
          </div>
          <div className="grid grid-cols-2 gap-x-4 gap-y-0.5 text-[11px] font-mono" style={{ color: "var(--text-secondary)" }}>
            <span>Source rows</span>
            <span style={{ color: "var(--text-primary)" }}>{ramEstimate.total_rows!.toLocaleString()}</span>
            {adjusted.isLimited && (
              <>
                <span>Training rows</span>
                <span style={{ color: adjusted.wasDownsampled ? "var(--warning-strong)" : "var(--text-primary)" }}>{adjusted.rows.toLocaleString()}</span>
              </>
            )}
            <span>Est. training RAM</span>
            <span style={{ color: "var(--text-primary)" }}>{formatMb(adjusted.trainingMb)}</span>
            <span>Available RAM</span>
            <span style={{ color: "var(--text-primary)" }}>{formatMb(ramEstimate.available_mb)}</span>
            {adjusted.gpuVramMb != null && (
              <>
                <span>Est. GPU VRAM</span>
                <span style={{ color: ramEstimate.gpu_vram_available_mb != null && adjusted.gpuVramMb > ramEstimate.gpu_vram_available_mb ? "var(--warning-strong)" : "var(--text-primary)" }}>
                  {formatMb(adjusted.gpuVramMb)}
                </span>
                {ramEstimate.gpu_vram_available_mb != null && (
                  <>
                    <span>GPU VRAM</span>
                    <span style={{ color: "var(--text-primary)" }}>
                      {formatMb(ramEstimate.gpu_vram_available_mb)}
                    </span>
                  </>
                )}
              </>
            )}
          </div>
          {adjusted.gpuVramMb != null && ramEstimate.gpu_vram_available_mb != null && adjusted.gpuVramMb > ramEstimate.gpu_vram_available_mb && (
            <div className="flex items-center gap-2 mt-1" style={{ color: "var(--warning-strong)" }}>
              <AlertTriangle size={12} className="shrink-0" />
              <span>
                GPU training needs ~{formatMb(adjusted.gpuVramMb)} but GPU has {formatMb(ramEstimate.gpu_vram_available_mb)}. Select CPU and retry, or reduce rows/features.
              </span>
            </div>
          )}
        </div>
      )}

      {/* Actions */}
      <div className="pt-2" style={{ borderTop: "1px solid var(--border)" }}>
        <button
          onClick={onTrain}
          disabled={busy}
          className="w-full flex items-center justify-center gap-2 px-3 py-2 rounded-lg text-xs font-medium transition-colors"
          style={{
            background: busy ? "var(--chrome-hover)" : MODEL_COLORS.accent,
            color: busy ? "var(--text-muted)" : "var(--text-on-accent)",
            opacity: busy ? 0.6 : 1,
          }}
        >
          {trainIcon}
          {trainLabel}
        </button>
        {validationMessages.length > 0 && !busy && (
          <div
            role="alert"
            className="mt-2 flex items-start gap-2 rounded-lg px-3 py-2 text-xs"
            style={{ background: "var(--warning-soft-subtle)", border: "1px solid var(--warning-border)" }}
          >
            <AlertTriangle size={12} className="mt-0.5 shrink-0" style={{ color: "var(--warning-strong)" }} />
            <div className="min-w-0" style={{ color: "var(--warning)" }}>
              <div className="font-medium">Complete before training</div>
              <ul className="mt-1 list-disc space-y-1 pl-4">
                {validationMessages.map((message) => (
                  <li key={message}>{message}</li>
                ))}
              </ul>
            </div>
          </div>
        )}
        {training && (
          <button
            type="button"
            onClick={onCancel}
            disabled={cancelling}
            className="mt-2 w-full flex items-center justify-center gap-2 px-3 py-2 rounded-lg text-xs font-medium transition-colors"
            style={{
              background: "var(--danger-soft-subtle)",
              border: "1px solid var(--danger-border)",
              color: "var(--danger)",
              opacity: cancelling ? 0.6 : 1,
            }}
          >
            {cancelling
              ? <Loader2 size={14} className="animate-spin" />
              : <XCircle size={14} />}
            {cancelling ? "Cancelling..." : "Cancel training"}
          </button>
        )}
      </div>

      {/* Live Training Progress */}
      {trainProgress && <TrainingProgressPanel trainProgress={trainProgress} estimatedRemainingSeconds={estimatedRemainingSeconds} />}

      {/* Completion badge — results are in the preview panel below */}
      {trainResult && trainResult.status !== "error" && !training && !submitting && (
        <div className="flex items-center gap-2 px-3 py-2 rounded-lg text-xs" style={{ background: "var(--success-soft-faint)", border: "1px solid var(--success-border)" }}>
          <CheckCircle2 size={12} style={{ color: "var(--success)" }} className="shrink-0" />
          <span style={{ color: "var(--success)" }}>
            Model trained — results in preview panel below
          </span>
        </div>
      )}

      {/* Error display — keep in config panel since there's no preview to show */}
      {trainResult && trainResult.status === "error" && (
        <div className="px-3 py-2.5 rounded-lg text-xs space-y-1.5" style={{ background: "var(--danger-soft-subtle)", border: "1px solid var(--danger-border)" }}>
          <div className="flex items-start gap-2">
            <AlertTriangle size={14} className="shrink-0 mt-0.5" style={{ color: "var(--danger)" }} />
            <div className="space-y-1 min-w-0">
              <div className="font-semibold" style={{ color: "var(--danger)" }}>
                {terminalStatus === "cancelled" ? "Training cancelled" : "Training failed"}
              </div>
              <div style={{ color: "var(--danger-text-soft)", lineHeight: "1.5" }}>{trainResult.error}</div>
              <ExecutionDiagnosticsSummary
                metrics={terminalMetrics}
                status={terminalStatus}
                terminalReason={terminalReason}
              />
            </div>
          </div>
        </div>
      )}
    </>
  )
}
