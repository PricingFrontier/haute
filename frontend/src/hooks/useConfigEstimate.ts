/**
 * Shared lifecycle for per-node configuration estimates.
 *
 * A config panel that wants to display a per-node estimate (e.g. training
 * RAM, solver cost) has the same set of needs:
 *   - fetch once when a node becomes active
 *   - re-fetch when a hashed slice of the node's config changes
 *   - cancel any in-flight request on hash change or unmount
 *   - swallow AbortError silently (expected on cancellation)
 *   - surface any other error as both a warning toast and an `error` field
 *
 * This hook owns that cycle.  Consumers pass a `(payload, { signal })`
 * fetcher — typically a thin wrapper around an API client function bound to
 * the current graph — and a `configHash` that invalidates the cached result
 * when it changes.  The hook is generic over the estimate shape so the same
 * engine can drive modelling RAM, optimiser row-count, or any future panel.
 */
import { useEffect, useReducer, useRef } from "react"
import useToastStore from "../stores/useToastStore"

export interface UseConfigEstimateResult<TEstimate> {
  /** The latest estimate, or null before the first resolution / after a reset. */
  estimate: TEstimate | null
  /** True while a request is in-flight for the current (nodeId, configHash). */
  loading: boolean
  /** Non-Abort error message from the latest attempt, or null if the last
   *  attempt succeeded or is still pending. */
  error: string | null
}

/** Type of the fetcher callback consumers pass in. */
export type ConfigEstimateEndpoint<TEstimate> = (
  /** Payload — opaque to the hook; consumers bake graph/node info into a closure. */
  payload: void,
  options: { signal: AbortSignal },
) => Promise<TEstimate>

export interface UseConfigEstimateOptions {
  /** Prefix for the warning toast on non-Abort errors. Defaults to "Estimate failed". */
  toastLabel?: string
}

// ── Internal reducer state ────────────────────────────────────────────
// A reducer (rather than three `useState` calls) keeps the three fields
// in lockstep — on reject, for example, we want `estimate: null` AND
// `loading: false` AND `error: <msg>` committed atomically.  It also
// avoids the `react-hooks/set-state-in-effect` warning that fires when
// you set multiple state values in an effect body.

interface EstimateState<TEstimate> {
  estimate: TEstimate | null
  loading: boolean
  error: string | null
}

type EstimateAction<TEstimate> =
  | { type: "start" }
  | { type: "resolve"; estimate: TEstimate }
  | { type: "reject"; message: string }

function estimateReducer<TEstimate>(
  _state: EstimateState<TEstimate>,
  action: EstimateAction<TEstimate>,
): EstimateState<TEstimate> {
  switch (action.type) {
    case "start":
      return { estimate: null, loading: true, error: null }
    case "resolve":
      return { estimate: action.estimate, loading: false, error: null }
    case "reject":
      return { estimate: null, loading: false, error: action.message }
  }
}

const INITIAL_STATE = { estimate: null, loading: false, error: null }

/**
 * Load and manage a per-node configuration estimate.
 *
 * @param nodeId     The node whose estimate is being tracked.  Passing an
 *                   empty string suppresses the fetch entirely (no active
 *                   node — nothing to estimate).
 * @param configHash A stable hash of the config slice that affects the
 *                   estimate.  When this string changes the hook aborts
 *                   any in-flight request and kicks off a new one.
 * @param endpoint   The fetcher.  Called with an AbortSignal so the hook
 *                   can cancel on change/unmount.  Must reject with a
 *                   DOMException of name "AbortError" on cancellation.
 * @param options    Optional behaviour overrides — most importantly the
 *                   toast label prefix so each panel can surface a
 *                   domain-specific message ("RAM estimate failed",
 *                   "Cost estimate failed") rather than a generic one.
 *
 * @returns Current estimate + loading/error state.
 */
export function useConfigEstimate<TEstimate>(
  nodeId: string,
  configHash: string,
  endpoint: ConfigEstimateEndpoint<TEstimate>,
  options: UseConfigEstimateOptions = {},
): UseConfigEstimateResult<TEstimate> {
  const { toastLabel = "Estimate failed" } = options
  const [state, dispatch] = useReducer(
    estimateReducer<TEstimate>,
    INITIAL_STATE as EstimateState<TEstimate>,
  )

  // Mirror the latest endpoint + label into refs so the effect always sees
  // the newest closure without having to re-run on every render.  The
  // mirror itself lives in an effect so we don't mutate refs during render.
  const endpointRef = useRef(endpoint)
  const toastLabelRef = useRef(toastLabel)
  useEffect(() => {
    endpointRef.current = endpoint
    toastLabelRef.current = toastLabel
  })

  useEffect(() => {
    if (!nodeId) return

    const controller = new AbortController()
    dispatch({ type: "start" })

    endpointRef
      .current(undefined, { signal: controller.signal })
      .then((result) => {
        if (controller.signal.aborted) return
        dispatch({ type: "resolve", estimate: result })
      })
      .catch((err: unknown) => {
        // Silent on cancellation — the consumer expects the in-flight
        // promise to vanish when inputs change or the panel unmounts.
        if (
          (err instanceof DOMException && err.name === "AbortError") ||
          controller.signal.aborted
        ) {
          return
        }
        const msg = err instanceof Error ? err.message : String(err)
        dispatch({ type: "reject", message: msg })
        useToastStore
          .getState()
          .addToast("warning", `${toastLabelRef.current}: ${msg}`)
      })

    return () => controller.abort()
  }, [nodeId, configHash])

  return state
}
