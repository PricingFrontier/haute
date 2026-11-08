/**
 * The one lifecycle of a frontier-point request: the Rates materialisation,
 * the Adjustments point report, the Segments index and breakdowns, the Quotes
 * pages and the Export pane's factor-table load all go through it.
 *
 * `request` is what the view wants now (its `key` is its identity), or null for
 * nothing. A request is sent while it is wanted and has not failed. A new key,
 * null or unmount aborts the one in flight, and an aborted request's reply or
 * error is discarded silently: the server stops a point apply no request waits
 * for any more. A 409 `frontier_point_apply_replaced` (another point's apply
 * took the job's queue) is not a failure: the request is sent again while the
 * view still wants it. A 410 (the point or result is gone) fails without Retry,
 * since only a new solve helps; any other error fails with Retry. An error
 * `onLoaded` throws (a reply the view cannot use) is the request's failure too.
 */

import { useEffect, useRef, useState } from "react"
import { apiErrorCode, apiErrorMessage, apiErrorStatus } from "../../api/errors"

const APPLY_REPLACED = "frontier_point_apply_replaced"

/** A loaded point answer's identity: point indices mean something only within one job. */
export function frontierPointKey(jobId: string, pointIndex: number): string {
  return `${jobId}:${pointIndex}`
}

/** Why the wanted request failed; `gone` (a 410) offers no Retry. */
export type RequestFailure = { gone: boolean; message: string }

export function useLatestRequest<R extends { key: string }, T>(
  request: R | null,
  load: (request: R, signal: AbortSignal) => Promise<T>,
  onLoaded: (request: R, value: T) => void,
): { failure: RequestFailure | null; retry: () => void } {
  const [failure, setFailure] = useState<(RequestFailure & { key: string }) | null>(null)
  // A replaced request is sent again by bumping the attempt.
  const [attempt, setAttempt] = useState(0)
  const requestRef = useRef(request)
  const loadRef = useRef(load)
  const onLoadedRef = useRef(onLoaded)
  useEffect(() => {
    requestRef.current = request
    loadRef.current = load
    onLoadedRef.current = onLoaded
  })
  const key = request?.key ?? null
  const failed = failure !== null && failure.key === key
  useEffect(() => {
    const wanted = requestRef.current
    if (wanted === null || failed) return
    const controller = new AbortController()
    loadRef.current(wanted, controller.signal)
      .then((value) => {
        if (controller.signal.aborted) return
        onLoadedRef.current(wanted, value)
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) return
        if (apiErrorStatus(error) === 409 && apiErrorCode(error) === APPLY_REPLACED) {
          setAttempt((current) => current + 1)
          return
        }
        setFailure({
          key: wanted.key,
          gone: apiErrorStatus(error) === 410,
          message: apiErrorMessage(error, "The request failed."),
        })
      })
    return () => controller.abort()
  }, [key, failed, attempt])
  return { failure: failed ? failure : null, retry: () => setFailure(null) }
}
