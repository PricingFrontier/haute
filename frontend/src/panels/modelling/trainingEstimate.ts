import { ApiError } from "../../api/client"

/** Backoff before each retry of an estimate refused by another evaluation preview. */
export const SUPERSEDED_PREVIEW_RETRY_DELAYS_MS: readonly number[] = [150, 400, 1_000, 2_000]
/** Wait between retries of an estimate refused by the pipeline's input-snapshot build. */
export const INPUT_SNAPSHOT_RETRY_DELAY_MS = 2_000
/** Longest an estimate waits in all for input-snapshot builds before it fails. */
export const INPUT_SNAPSHOT_WAIT_LIMIT_MS = 60_000

const EVALUATION_PREVIEW_HOLDER = "training_prep:training_evaluation_preview"
const INPUT_SNAPSHOT_BUILD_OPERATION = "input_snapshot_build"

function isInputSnapshotBuild(holder: string): boolean {
  const [profile, operation] = holder.split(":")
  return Boolean(profile) && operation === INPUT_SNAPSHOT_BUILD_OPERATION
}

/**
 * What an estimate refusal is waiting on when every holder of the training-prep
 * budget will release it by itself: other evaluation previews (the server
 * finishes a superseded one after the browser stops waiting) or the pipeline's
 * input-snapshot build, which can take a minute. Null for any other failure,
 * including a refusal by a running training job.
 */
export function transientEstimateHolders(error: unknown): "evaluation_preview" | "input_snapshot" | null {
  if (!(error instanceof ApiError) || error.status !== 507) return null
  const detail = error.rawDetail
  if (detail === null || typeof detail !== "object" || Array.isArray(detail)) return null
  const { reason, in_flight_operations: holders } = detail as Record<string, unknown>
  if (reason !== "in_flight_memory_budget_exceeded" || !Array.isArray(holders) || holders.length === 0) {
    return null
  }
  const snapshotBuilds = holders.filter(
    (holder) => typeof holder === "string" && isInputSnapshotBuild(holder),
  ).length
  const previews = holders.filter((holder) => holder === EVALUATION_PREVIEW_HOLDER).length
  if (snapshotBuilds + previews !== holders.length) return null
  return snapshotBuilds > 0 ? "input_snapshot" : "evaluation_preview"
}

function abortableDelay(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal.aborted) {
      reject(new DOMException("Aborted", "AbortError"))
      return
    }
    const onAbort = () => {
      clearTimeout(timeoutId)
      reject(new DOMException("Aborted", "AbortError"))
    }
    const timeoutId = setTimeout(() => {
      signal.removeEventListener("abort", onAbort)
      resolve()
    }, ms)
    signal.addEventListener("abort", onAbort, { once: true })
  })
}

export interface EstimateWaitOptions {
  /** Called before each wait for an input-snapshot build, so the caller can say so. */
  onWaiting?: () => void
  snapshotDelayMs?: number
  snapshotLimitMs?: number
}

/**
 * Run a training estimate, retrying while it is refused only by work that
 * releases the budget by itself. A refusal by other evaluation previews takes
 * the next short backoff delay; one naming an input-snapshot build waits
 * `snapshotDelayMs` from a `snapshotLimitMs` allowance. The two budgets belong
 * to this call and never reset, and a refusal whose own budget is spent is
 * returned, as is any other failure (including a running training job).
 */
export async function estimateAfterSupersededPreviews<T>(
  request: () => Promise<T>,
  signal: AbortSignal,
  delaysMs: readonly number[] = SUPERSEDED_PREVIEW_RETRY_DELAYS_MS,
  {
    onWaiting,
    snapshotDelayMs = INPUT_SNAPSHOT_RETRY_DELAY_MS,
    snapshotLimitMs = INPUT_SNAPSHOT_WAIT_LIMIT_MS,
  }: EstimateWaitOptions = {},
): Promise<T> {
  let previewRetries = 0
  let snapshotWaitedMs = 0
  // Bounded by the two budgets: every retry spends one of them.
  for (let attempt = 0; ; attempt += 1) {
    try {
      return await request()
    } catch (error) {
      if (signal.aborted) throw error
      const holders = transientEstimateHolders(error)
      if (holders === "evaluation_preview" && previewRetries < delaysMs.length) {
        await abortableDelay(delaysMs[previewRetries], signal)
        previewRetries += 1
      } else if (holders === "input_snapshot" && snapshotWaitedMs + snapshotDelayMs <= snapshotLimitMs) {
        onWaiting?.()
        await abortableDelay(snapshotDelayMs, signal)
        snapshotWaitedMs += snapshotDelayMs
      } else {
        throw error
      }
    }
  }
}
