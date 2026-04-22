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
  cachedResult: { configHash: string } | null | undefined,
  endpoint: ConfigEstimateEndpoint<TEstimate>,
  options: UseStaleConfigEstimateOptions = {},
): UseStaleConfigEstimateResult<TEstimate> {
  const { toastLabel = "Estimate failed", estimateKey = "" } = options
  const configHash = useMemo(() => hashConfig(config), [config])
  const isStale = !!cachedResult && cachedResult.configHash !== configHash
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
  }, [nodeId, configHash, estimateKey])

  return { ...state, configHash, isStale }
}
