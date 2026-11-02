/**
 * A training's remaining-time estimate from the progress it reports: the rate
 * between its last two samples of rounds (or, while tuning, fits) completed.
 */
import { TERMINAL_JOB_STATUSES } from "../api/types"
import type { TrainProgress } from "../stores/useNodeResultsStore"

export type TrainEstimateSample = {
  iteration: number
  elapsedSeconds: number
  totalIterations: number
  mode?: "fits"
}

export function nextTrainEstimate(
  previous: TrainEstimateSample[],
  progress: TrainProgress,
): {
  samples: TrainEstimateSample[]
  estimatedRemainingSeconds: number | null
} {
  if (TERMINAL_JOB_STATUSES.has(progress.status)) {
    return { samples: [], estimatedRemainingSeconds: null }
  }

  const tuningSample = (
    progress.phase != null
    && progress.completed_fits != null
    && progress.total_fits != null
  )
  const sample: TrainEstimateSample = {
    iteration: tuningSample ? progress.completed_fits! : progress.iteration,
    elapsedSeconds: progress.elapsed_seconds,
    totalIterations: tuningSample ? progress.total_fits! : progress.total_iterations,
    ...(tuningSample ? { mode: "fits" as const } : {}),
  }
  const validSample =
    Number.isFinite(sample.iteration)
    && Number.isFinite(sample.elapsedSeconds)
    && Number.isFinite(sample.totalIterations)
    && sample.iteration > 0
    && sample.elapsedSeconds > 0
    && sample.totalIterations > sample.iteration
  if (!validSample) {
    return { samples: previous, estimatedRemainingSeconds: null }
  }

  const prior = previous.at(-1)
  if (!prior) return { samples: [sample], estimatedRemainingSeconds: null }
  if (
    prior.mode !== sample.mode
    ||
    sample.iteration <= prior.iteration
    || sample.elapsedSeconds <= prior.elapsedSeconds
  ) {
    return { samples: previous, estimatedRemainingSeconds: null }
  }

  const rate =
    (sample.iteration - prior.iteration)
    / (sample.elapsedSeconds - prior.elapsedSeconds)
  const remaining = (sample.totalIterations - sample.iteration) / rate
  const estimatedRemainingSeconds =
    Number.isFinite(rate)
    && rate > 0
    && Number.isFinite(remaining)
    && remaining > 0
      ? remaining
      : null

  return {
    samples: [prior, sample],
    estimatedRemainingSeconds,
  }
}
