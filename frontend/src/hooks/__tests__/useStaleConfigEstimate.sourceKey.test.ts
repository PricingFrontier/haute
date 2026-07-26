import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { renderHook, waitFor, cleanup } from "@testing-library/react"
import { useStaleConfigEstimate } from "../useStaleConfigEstimate"
import useToastStore from "../../stores/useToastStore"
import useNodeResultsStore, { hashConfig } from "../../stores/useNodeResultsStore"

// Key-contract pin (Maginot: fingerprint / cache-key completeness).
// The solve/train staleness key must cover every input that affects the
// cached result: node config, active data source, and structuralVersion.
// A result computed against source A must be marked stale when the active
// source switches to B, even though the node config is untouched.

interface FakeEstimate {
  estimated_mb: number
}

const config = { algorithm: "catboost", gpu: false }
const sampleEstimate: FakeEstimate = { estimated_mb: 1024 }

beforeEach(() => {
  useToastStore.setState({ toasts: [], _toastCounter: 0 })
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe("useStaleConfigEstimate staleness key completeness", () => {
  it("marks the cached result stale when the active source switches", async () => {
    const endpoint = vi.fn().mockResolvedValue(sampleEstimate)
    const cachedOnSourceA = {
      configHash: hashConfig(config),
      source: "source_a",
      structuralVersion: 1,
    }

    const { result, rerender } = renderHook(
      ({ source }: { source: string }) =>
        useStaleConfigEstimate<FakeEstimate>(
          "node_1",
          config,
          cachedOnSourceA,
          endpoint,
          { source, structuralVersion: 1 },
        ),
      { initialProps: { source: "source_a" } },
    )

    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.isStale).toBe(false)

    rerender({ source: "source_b" })
    expect(result.current.isStale).toBe(true)
  })

  it("marks the cached result stale when structuralVersion advances", async () => {
    const endpoint = vi.fn().mockResolvedValue(sampleEstimate)
    const cached = {
      configHash: hashConfig(config),
      source: "source_a",
      structuralVersion: 1,
    }

    const { result, rerender } = renderHook(
      ({ structuralVersion }: { structuralVersion: number }) =>
        useStaleConfigEstimate<FakeEstimate>(
          "node_1",
          config,
          cached,
          endpoint,
          { source: "source_a", structuralVersion },
        ),
      { initialProps: { structuralVersion: 1 } },
    )

    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.isStale).toBe(false)

    rerender({ structuralVersion: 2 })
    expect(result.current.isStale).toBe(true)
  })

})

describe("solve/train cached results carry source identity", () => {
  beforeEach(() => {
    useNodeResultsStore.setState({
      solveJobs: {},
      solveResults: {},
      trainJobs: {},
      trainResults: {},
    })
  })

  it("completeSolveJob stamps the job's source and structuralVersion onto the cached result", () => {
    const store = useNodeResultsStore.getState()
    store.startSolveJob("node_1", "job_1", "Optimiser", {}, "hash_a", "source_a", 3)
    useNodeResultsStore.getState().completeSolveJob("node_1", {
      status: "completed",
      total_objective: 1,
      baseline_objective: 1,
      constraints: {},
      baseline_constraints: {},
      lambdas: {},
      converged: true,
    } as never)

    const cached = useNodeResultsStore.getState().solveResults["node_1"]
    expect(cached.source).toBe("source_a")
    expect(cached.structuralVersion).toBe(3)
  })

  it("completeTrainJob stamps the job's source and structuralVersion onto the cached result", () => {
    const store = useNodeResultsStore.getState()
    store.startTrainJob("node_1", "job_1", "Model Training", "hash_a", "source_a", 3)
    useNodeResultsStore.getState().completeTrainJob("node_1", {
      status: "completed",
      metrics: {},
      feature_importance: [],
      model_path: "m",
      train_rows: 1,
      validation_rows: 1,
    } as never)

    const cached = useNodeResultsStore.getState().trainResults["node_1"]
    expect(cached.source).toBe("source_a")
    expect(cached.structuralVersion).toBe(3)
  })
})
