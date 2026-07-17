import { useEffect, useMemo, useReducer, useRef } from "react"
import useToastStore from "../stores/useToastStore"
import { hashConfig } from "../stores/useNodeResultsStore"

export interface UseStaleConfigEstimateResult<TEstimate> {
  configHash: string
  isStale: boolean
  estimate: TEstimate | null
  loading: boolean
  error: string | null
}

export type ConfigEstimateEndpoint<TEstimate> = (
  payload: void,
  options: { signal: AbortSignal },
) => Promise<TEstimate>

export interface UseStaleConfigEstimateOptions {
  toastLabel?: string
  estimateKey?: string | number
  enabled?: boolean
}

/**
 * The non-config inputs that affect a cached solve/train result. Staleness
 * key contract: a cached result is current only if its configHash, source,
 * AND structuralVersion all match the live values — the same identity
 * Explore checks (config+source folded into its hash, source re-checked on
 * the cached result). Omitting either field here re-opens the wrong-source
 * staleness bug pinned in useStaleConfigEstimate.sourceKey.test.ts.
 */
export interface StaleEstimateContext {
  source: string
  structuralVersion: number
}

export interface StaleCachedResult {
  configHash: string
  source?: string
  structuralVersion?: number
}

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

export function useStaleConfigEstimate<TEstimate>(
  nodeId: string,
  config: Record<string, unknown>,
  cachedResult: StaleCachedResult | null | undefined,
  endpoint: ConfigEstimateEndpoint<TEstimate>,
  context: StaleEstimateContext,
  options: UseStaleConfigEstimateOptions = {},
): UseStaleConfigEstimateResult<TEstimate> {
  const { toastLabel = "Estimate failed", estimateKey = "", enabled = true } = options
  const configHash = useMemo(() => hashConfig(config), [config])
  // A cached result missing source/structuralVersion (pre-contract shape)
  // fails the comparison and reads as stale — fail-safe, never fail-current.
  const isStale =
    !!cachedResult &&
    (cachedResult.configHash !== configHash ||
      cachedResult.source !== context.source ||
      cachedResult.structuralVersion !== context.structuralVersion)
  const [state, dispatch] = useReducer(
    estimateReducer<TEstimate>,
    INITIAL_STATE as EstimateState<TEstimate>,
  )

  const endpointRef = useRef(endpoint)
  const toastLabelRef = useRef(toastLabel)
  useEffect(() => {
    endpointRef.current = endpoint
    toastLabelRef.current = toastLabel
  }, [endpoint, toastLabel])

  useEffect(() => {
    if (!enabled) return
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
  }, [nodeId, configHash, context.source, context.structuralVersion, estimateKey, enabled])

  return { ...state, configHash, isStale }
}
