/**
 * The previews derived from the results store's records. Records are replaced,
 * never mutated, so each preview is memoised on the record it reads: a render
 * that reads the same record gets the same preview object, and a replaced
 * record's preview is collected with it.
 */
import type { ModellingPreviewData } from "../panels/ModellingPreview"
import type { OptimiserPreviewData } from "../panels/OptimiserPreview"
import type { CachedTrainResult, SolvedCachedSolveResult, TrainResult } from "./useNodeResultsStore"

const optimiserPreviews = new WeakMap<SolvedCachedSolveResult, OptimiserPreviewData>()
const modellingPreviews = new WeakMap<CachedTrainResult, Map<string, ModellingPreviewData>>()

/** A training outcome with a usable result: not a failed job, nor a result the server marked an error. */
export function hasTrainResult(cached: CachedTrainResult): cached is CachedTrainResult & { result: TrainResult } {
  return cached.result !== null && cached.result.status !== "error"
}

/** The optimiser preview of a solved result. */
export function optimiserPreviewFor(cached: SolvedCachedSolveResult): OptimiserPreviewData {
  let preview = optimiserPreviews.get(cached)
  if (!preview) {
    preview = {
      result: cached.result,
      solvedResult: cached.originalResult,
      jobId: cached.jobId,
      constraints: cached.constraints,
      nodeLabel: cached.nodeLabel,
      frontier: cached.frontier,
      selectedPointIndex: cached.selectedPointIndex,
    }
    optimiserPreviews.set(cached, preview)
  }
  return preview
}

/** The modelling preview of a completed training result under its node's label. */
export function modellingPreviewFor(
  cached: CachedTrainResult & { result: TrainResult },
  nodeLabel: string,
): ModellingPreviewData {
  let byLabel = modellingPreviews.get(cached)
  if (!byLabel) {
    byLabel = new Map()
    modellingPreviews.set(cached, byLabel)
  }
  let preview = byLabel.get(nodeLabel)
  if (!preview) {
    preview = {
      result: cached.result,
      jobId: cached.jobId,
      nodeLabel,
      configHash: cached.configHash,
      // The terminal status, never a later job's progress: a re-train's clock
      // is not this result's.
      elapsedSeconds: cached.terminalStatus?.elapsed_seconds ?? null,
    }
    byLabel.set(nodeLabel, preview)
  }
  return preview
}
