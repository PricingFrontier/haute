/**
 * Shared resettable single-flight for store loaders.
 *
 * Both git loaders (working-branch status, branch listing) share the same
 * shape: coalesce concurrent reads onto one request, let a completed mutation
 * demand a refresh whose response was issued AFTER the mutation, survive test
 * resets without a detached request publishing stale state, and never let one
 * stuck request starve every later load. This module owns that bookkeeping so
 * the two loaders cannot drift apart; the loaders keep only their own state
 * publication.
 *
 * Semantics:
 *
 * - `load(fetcher)` joins the active request if one exists (same promise
 *   identity for every joiner), otherwise issues a fresh one.
 * - `load(fetcher, { refresh: true })` guarantees the caller a response from a
 *   request issued no earlier than the call: if a request is active, exactly
 *   one follow-up is queued PER ACTIVE REQUEST behind its settlement, so an
 *   older response cannot become the final published state. A refresh
 *   requested while a DIFFERENT request is active re-queues behind that newer
 *   request rather than being satisfied by a queue from an older generation.
 * - `reset()` detaches everything (for tests that hold a mocked request open
 *   forever). Safe because every settle path is identity-guarded via the
 *   `isCurrent` callback handed to the fetcher: a detached request can neither
 *   publish state nor clobber a newer request's slot.
 * - Stalled-request recovery: a request still pending after
 *   `stalePendingAfterMs` is detached and its awaiters settled (`staleResult`
 *   if given, else rejection with StalePendingRequestError), freeing the slot
 *   so the next load — including a refresh queued behind the stalled anchor —
 *   issues a fresh request instead of starving forever. The API client already
 *   bounds each request (30s timeout, ≤4 attempts ≈ 121s worst case), so the
 *   default fires only for a promise that escapes those bounds; it is the
 *   store-level backstop, not the primary timeout.
 */

/** Above the API client's retry-loop worst case, so the transport's own
 *  ApiTimeoutError is always the error the user actually sees for a slow
 *  network; this backstop fires only for a genuinely unbounded promise.
 *
 *  PAIRED INVARIANT with api/client.ts (request(): default timeout 30_000 ×
 *  (DEFAULT_RETRY_POLICY.maxRetries + 1) attempts + ~700ms backoff ≈ 121s):
 *  this constant must stay ABOVE that budget, or the watchdog pre-empts the
 *  transport's specific timeout error with the generic stalled-request
 *  message. A matching comment sits on DEFAULT_RETRY_POLICY — change either
 *  side only in step with the other. */
export const DEFAULT_STALE_PENDING_AFTER_MS = 150_000

export class StalePendingRequestError extends Error {
  constructor(pendingMs: number) {
    super(`No response after ${Math.round(pendingMs / 1000)}s.`)
    this.name = "StalePendingRequestError"
  }
}

export interface SingleFlightLoadOptions<T> {
  /** Demand a response issued no earlier than this call (see module doc). */
  refresh?: boolean
  /** What awaiters of a superseded/reset queued refresh resolve with when no
   *  newer work exists to join (a reset leaves none). */
  detachedValue: () => T
  /** Runs synchronously AFTER the fresh request owns the single-flight slot,
   *  so a synchronous store subscriber reacting to it by loading again
   *  coalesces onto this request instead of racing a duplicate into the slot.
   *  The place to publish a loading flag. */
  onStart?: () => void
  /** Publish the stalled-request failure. Called only while the stalled
   *  request still owns the slot — never over a newer request's state. */
  onStale?: (error: StalePendingRequestError) => void
  /** If given, a stalled request's awaiters resolve with this instead of
   *  rejecting (for loaders whose contract is never-reject). */
  staleResult?: () => T
}

/** The fetcher owns state publication and must guard every set() behind the
 *  supplied `isCurrent()` — re-checked before EACH publish, since any yield
 *  point may have detached the request in the meantime. `isCurrent()` is only
 *  meaningful in asynchronous continuations: the fetcher is invoked before the
 *  slot is assigned, so a synchronous call would always see false. */
export type SingleFlightFetcher<T> = (isCurrent: () => boolean) => Promise<T>

export interface SingleFlight<T> {
  load(fetcher: SingleFlightFetcher<T>, options: SingleFlightLoadOptions<T>): Promise<T>
  reset(): void
}

export function createSingleFlight<T>(
  config?: { stalePendingAfterMs?: number },
): SingleFlight<T> {
  const stalePendingAfterMs = config?.stalePendingAfterMs ?? DEFAULT_STALE_PENDING_AFTER_MS

  let inFlight: Promise<T> | null = null
  let queuedRefresh: Promise<T> | null = null
  // The request the queued refresh is anchored behind. A refresh requested
  // while a DIFFERENT request is active must queue behind that newer request
  // rather than being satisfied by a queue from an older generation.
  let queuedAnchor: Promise<T> | null = null
  // Cancels the active request's watchdog timer. reset() must run this: a
  // detached request keeps the pre-reset semantics (its awaiters settle with
  // the fetcher's real eventual result), so its watchdog must never fire a
  // stale error into whatever is running when the 150s elapse.
  let cancelWatchdog: (() => void) | null = null

  const issue = (
    fetcher: SingleFlightFetcher<T>,
    options: SingleFlightLoadOptions<T>,
  ): Promise<T> => {
    let staleTimer: ReturnType<typeof setTimeout> | undefined
    const request: Promise<T> = Promise.race([
      fetcher(() => inFlight === request),
      new Promise<T>((resolve, reject) => {
        staleTimer = setTimeout(() => {
          // A detached request's fire is a pure no-op: its awaiters keep the
          // detach semantics (settle with the fetcher's real eventual result),
          // and settling here would throw a stale rejection into whatever is
          // running when the timer elapses. reset() also cancels this timer;
          // this guard covers a fire already queued when the reset landed.
          if (inFlight !== request) return
          // Free the slot first: onStale's publish may synchronously trigger
          // a subscriber that loads again, and that load must start fresh.
          inFlight = null
          const error = new StalePendingRequestError(stalePendingAfterMs)
          try {
            options.onStale?.(error)
          } finally {
            // The settle must survive a throwing onStale subscriber — a stuck
            // request must not become stuck-request-plus-crashed-timer that
            // hangs every awaiter (including a queued mutation refresh).
            if (options.staleResult) resolve(options.staleResult())
            else reject(error)
          }
        }, stalePendingAfterMs)
      }),
    ]).finally(() => {
      clearTimeout(staleTimer)
      if (cancelWatchdog === cancel) cancelWatchdog = null
      if (inFlight === request) inFlight = null
    })
    const cancel = () => clearTimeout(staleTimer)
    cancelWatchdog = cancel
    inFlight = request
    options.onStart?.()
    return request
  }

  const load = (
    fetcher: SingleFlightFetcher<T>,
    options: SingleFlightLoadOptions<T>,
  ): Promise<T> => {
    if (inFlight) {
      if (!options.refresh) return inFlight
      if (!queuedRefresh || queuedAnchor !== inFlight) {
        const anchor = inFlight
        const queued: Promise<T> = anchor
          .then(() => undefined, () => undefined)
          .then(() => {
            // Identity guard: a test reset or a newer queued refresh may have
            // detached this continuation; a detached refresh must neither
            // clear a newer queue slot nor spawn its own request. Superseded
            // awaiters join whatever newer work exists.
            if (queuedRefresh !== queued) {
              return queuedRefresh ?? inFlight ?? options.detachedValue()
            }
            queuedRefresh = null
            queuedAnchor = null
            return load(fetcher, { ...options, refresh: false })
          })
        queuedRefresh = queued
        queuedAnchor = anchor
      }
      return queuedRefresh
    }
    return issue(fetcher, options)
  }

  return {
    load,
    reset: () => {
      cancelWatchdog?.()
      cancelWatchdog = null
      inFlight = null
      queuedRefresh = null
      queuedAnchor = null
    },
  }
}
