import { ApiError } from "../../api/client"

/** Backoff before each retry of an estimate refused by another evaluation preview. */
export const SUPERSEDED_PREVIEW_RETRY_DELAYS_MS: readonly number[] = [150, 400, 1_000, 2_000]

const EVALUATION_PREVIEW_HOLDER = "training_prep:training_evaluation_preview"

/**
 * True when the server refused the estimate only because other evaluation
 * previews hold the training-prep memory budget. Aborting a superseded
 * estimate stops the browser waiting, but the server finishes that preview,
 * so its replacement can briefly lose admission to it.
 */
export function refusedByEvaluationPreviews(error: unknown): boolean {
  if (!(error instanceof ApiError) || error.status !== 507) return false
  const detail = error.rawDetail
  if (detail === null || typeof detail !== "object" || Array.isArray(detail)) return false
  const { reason, in_flight_operations: holders } = detail as Record<string, unknown>
  return (
    reason === "in_flight_memory_budget_exceeded"
    && Array.isArray(holders)
    && holders.length > 0
    && holders.every((holder) => holder === EVALUATION_PREVIEW_HOLDER)
  )
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

/**
 * Run a training estimate, retrying with bounded backoff only while it is
 * refused by other evaluation previews. Any other failure, including a
 * refusal by a running training job, is returned at once.
 */
export async function estimateAfterSupersededPreviews<T>(
  request: () => Promise<T>,
  signal: AbortSignal,
  delaysMs: readonly number[] = SUPERSEDED_PREVIEW_RETRY_DELAYS_MS,
): Promise<T> {
  for (let attempt = 0; ; attempt += 1) {
    try {
      return await request()
    } catch (error) {
      if (attempt >= delaysMs.length || signal.aborted || !refusedByEvaluationPreviews(error)) {
        throw error
      }
      await abortableDelay(delaysMs[attempt], signal)
    }
  }
}
