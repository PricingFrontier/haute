import { getPreviewProgress } from "../api/client"
import type { PreviewProgressResponse } from "../api/types"

export const PREVIEW_PROGRESS_POLL_MS = 250

/** A fresh id for one preview request, so only its own progress is polled. */
export function newPreviewRequestId(): string {
  return crypto.randomUUID()
}

/**
 * Poll one preview request's step progress until the returned stop function
 * is called or *signal* aborts. The preview request is authoritative: a poll
 * that finds nothing (not registered yet, a cache hit, already settled) or
 * fails is simply asked again, and only the request's own settlement stops
 * polling.
 */
export function pollPreviewProgress(
  requestId: string,
  signal: AbortSignal,
  onProgress: (progress: PreviewProgressResponse) => void,
): () => void {
  let stopped = false
  let timer: ReturnType<typeof setTimeout> | undefined
  const stop = () => {
    stopped = true
    if (timer !== undefined) clearTimeout(timer)
  }
  signal.addEventListener("abort", stop, { once: true })
  const poll = async () => {
    try {
      const progress = await getPreviewProgress(requestId, { signal })
      if (!stopped && progress) onProgress(progress)
    } catch {
      // Expected while the request is starting or settling; ask again.
    }
    if (!stopped) timer = setTimeout(() => void poll(), PREVIEW_PROGRESS_POLL_MS)
  }
  timer = setTimeout(() => void poll(), PREVIEW_PROGRESS_POLL_MS)
  return () => {
    signal.removeEventListener("abort", stop)
    stop()
  }
}
