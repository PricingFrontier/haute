import { StrictMode } from "react"
import { cleanup, render } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it } from "vitest"

import useNodeResultsStore, {
  MAX_CACHED_SOLVE_RESULTS,
  MAX_CACHED_TRAIN_RESULTS,
} from "../../stores/useNodeResultsStore"
import type { OptimiserSolveResult } from "../../api/types"
import { makeTrainResult } from "../../test-utils/factories"

function resetStore() {
  useNodeResultsStore.setState({
    previews: {},
    pinnedPreviewNodeId: null,
    columnCache: {},
    solveResults: {},
    solveJobs: {},
    trainResults: {},
    trainJobs: {},
  })
}

function makeSolveResult(
  overrides: Partial<OptimiserSolveResult> = {},
): OptimiserSolveResult {
  return {
    total_objective: 100,
    baseline_objective: 80,
    constraints: { premium: 50 },
    baseline_constraints: { premium: 45 },
    lambdas: { premium: 0.1 },
    converged: true,
    ...overrides,
  }
}

describe("useNodeResultsStore render-pure preview getters", () => {
  beforeEach(() => {
    resetStore()
  })

  afterEach(() => {
    cleanup()
    resetStore()
  })

  it("does not refresh optimiser LRU recency when read during StrictMode render", () => {
    const store = useNodeResultsStore.getState()
    for (let i = 0; i < MAX_CACHED_SOLVE_RESULTS; i += 1) {
      store.startSolveJob(`s${i}`, `job-${i}`, `Solve ${i}`, {}, `hash-${i}`, "live", 0)
      store.completeSolveJob(`s${i}`, makeSolveResult({ total_objective: i }))
    }

    function RenderReader() {
      useNodeResultsStore.getState().getOptimiserPreview("s0")
      return null
    }

    render(
      <StrictMode>
        <RenderReader />
      </StrictMode>,
    )

    store.startSolveJob("s-new", "job-new", "Solve new", {}, "hash-new", "live", 0)
    store.completeSolveJob("s-new", makeSolveResult({ total_objective: 999 }))

    const { solveResults } = useNodeResultsStore.getState()
    expect(Object.keys(solveResults)).toHaveLength(MAX_CACHED_SOLVE_RESULTS)
    expect(solveResults.s0).toBeUndefined()
    expect(solveResults.s1).toBeDefined()
    expect(solveResults["s-new"]?.result.total_objective).toBe(999)
  })

  it("does not refresh modelling LRU recency when read during StrictMode render", () => {
    const store = useNodeResultsStore.getState()
    for (let i = 0; i < MAX_CACHED_TRAIN_RESULTS; i += 1) {
      store.startTrainJob(`t${i}`, `job-${i}`, `Train ${i}`, `hash-${i}`, "live", 0)
      store.completeTrainJob(`t${i}`, makeTrainResult({ final_test_metrics: { rmse: i } }))
    }

    function RenderReader() {
      useNodeResultsStore.getState().getModellingPreview("t0")
      return null
    }

    render(
      <StrictMode>
        <RenderReader />
      </StrictMode>,
    )

    store.startTrainJob("t-new", "job-new", "Train new", "hash-new", "live", 0)
    store.completeTrainJob("t-new", makeTrainResult({ final_test_metrics: { rmse: 999 } }))

    const { trainResults } = useNodeResultsStore.getState()
    expect(Object.keys(trainResults)).toHaveLength(MAX_CACHED_TRAIN_RESULTS)
    expect(trainResults.t0).toBeUndefined()
    expect(trainResults.t1).toBeDefined()
    expect(trainResults["t-new"]?.result.final_test_metrics.rmse).toBe(999)
  })
})
