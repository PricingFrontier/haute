import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import OptimiserPreview, { type SolveResult } from "../OptimiserPreview"
import useNodeResultsStore, {
  resetNodeResultsDerivedCaches,
} from "../../stores/useNodeResultsStore"
import useGraphStore from "../../stores/useGraphStore"

const mockSelectFrontierPoint = vi.fn()

vi.mock("../../api/client", () => ({
  applyOptimiser: vi.fn(),
  saveOptimiser: vi.fn(),
  logOptimiserToMlflow: vi.fn(),
  selectFrontierPoint: (...args: unknown[]) => mockSelectFrontierPoint(...args),
}))

vi.mock("../../hooks/useDragResize", () => ({
  useDragResize: () => ({
    height: 320,
    containerRef: { current: null },
    onDragStart: vi.fn(),
  }),
}))

vi.mock("../../stores/useSettingsStore", () => ({
  default: (selector: (s: Record<string, unknown>) => unknown) =>
    selector({
      mlflow: { status: "connected", backend: "local", host: "" },
    }),
}))

function resetStore() {
  resetNodeResultsDerivedCaches()
  useGraphStore.setState({ structuralVersion: 0 })
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

function makeSolveResult(overrides: Partial<SolveResult> = {}): SolveResult {
  return {
    total_objective: 100,
    baseline_objective: 80,
    constraints: { loss_ratio: 0.6 },
    baseline_constraints: { loss_ratio: 0.55 },
    lambdas: { loss_ratio: 0.1 },
    converged: true,
    ...overrides,
  }
}

describe("OptimiserPreview store integration", () => {
  beforeEach(() => {
    resetStore()
    mockSelectFrontierPoint.mockReset()
  })

  afterEach(() => {
    cleanup()
    resetStore()
  })

  it("re-renders from the result store when a frontier point is clicked", async () => {
    const store = useNodeResultsStore.getState()
    store.startSolveJob("opt_1", "job_123", "My Optimiser", { loss_ratio: { max: 1.05 } }, "h1")
    store.completeSolveJob(
      "opt_1",
      makeSolveResult({
        frontier: {
          status: "ok",
          points: Array.from({ length: 5 }, (_, i) => ({
            total_objective: 120 + i,
            loss_ratio: 0.55 + i * 0.02,
            lambda_loss_ratio: 0.01 + i * 0.01,
            converged: true,
          })),
          n_points: 5,
          points_returned: 5,
          constraint_names: ["loss_ratio"],
          points_limit: 2000,
          points_truncated: false,
        },
      }),
    )

    const data = useNodeResultsStore.getState().getOptimiserPreview("opt_1")
    expect(data).not.toBeNull()
    render(<OptimiserPreview data={data!} nodeId="opt_1" allNodes={[]} edges={[]} />)

    fireEvent.click(screen.getByRole("button", { name: "Select frontier point 3" }))

    expect(await screen.findByText("Point 3 of 5")).toBeInTheDocument()
    expect(useNodeResultsStore.getState().solveResults.opt_1.selectedPointIndex).toBe(2)
  })

  it("materialises selected ratebook frontier rates into the Rates tab", async () => {
    mockSelectFrontierPoint.mockResolvedValueOnce({
      status: "ok",
      point_index: 0,
      total_objective: 120,
      constraints: { volume: 0.9 },
      baseline_objective: 80,
      baseline_constraints: { volume: 0.8 },
      lambdas: { volume: 0.1 },
      converged: true,
      cd_iterations: 5,
      factor_tables: {
        region: [
          { __factor_group__: "North", optimal_scenario_value: 1.08 },
          { __factor_group__: "South", optimal_scenario_value: 0.92 },
        ],
      },
      error: null,
    })
    const store = useNodeResultsStore.getState()
    store.startSolveJob("opt_1", "job_123", "Ratebook Optimiser", { volume: { min: 0.9 } }, "h1")
    store.completeSolveJob(
      "opt_1",
      makeSolveResult({
        mode: "ratebook",
        factor_tables: {
          region: [{ __factor_group__: "Base", optimal_scenario_value: 1.0 }],
        },
        frontier: {
          status: "ok",
          points: [
            {
              total_objective: 120,
              total_volume: 0.9,
              lambda_volume: 0.1,
              converged: true,
            },
          ],
          n_points: 1,
          points_returned: 1,
          constraint_names: ["volume"],
          points_limit: 2000,
          points_truncated: false,
        },
      }),
    )

    const data = useNodeResultsStore.getState().getOptimiserPreview("opt_1")
    expect(data).not.toBeNull()
    render(<OptimiserPreview data={data!} nodeId="opt_1" allNodes={[]} edges={[]} />)

    expect(screen.getByText("Rates")).toBeInTheDocument()
    fireEvent.click(screen.getByText("Rates"))
    expect(screen.getByText("Materialising selected point rates...")).toBeInTheDocument()

    expect(await screen.findByText("North")).toBeInTheDocument()
    expect(screen.getAllByText("1.0800").length).toBeGreaterThan(0)
    expect(mockSelectFrontierPoint).toHaveBeenCalledWith(
      {
        job_id: "job_123",
        point_index: 0,
        include_ratebook_tables: true,
      },
      { signal: expect.any(AbortSignal) },
    )
  })

  it("materialises selected ratebook frontier rates into the Summary beeswarm", async () => {
    mockSelectFrontierPoint.mockResolvedValueOnce({
      status: "ok",
      point_index: 0,
      total_objective: 120,
      constraints: { volume: 0.9 },
      baseline_objective: 80,
      baseline_constraints: { volume: 0.8 },
      lambdas: { volume: 0.1 },
      converged: true,
      cd_iterations: 5,
      factor_tables: {
        region: [
          { __factor_group__: "North", optimal_scenario_value: 1.08 },
          { __factor_group__: "South", optimal_scenario_value: 0.92 },
        ],
      },
      error: null,
    })
    const store = useNodeResultsStore.getState()
    store.startSolveJob("opt_1", "job_123", "Ratebook Optimiser", { volume: { min: 0.9 } }, "h1")
    store.completeSolveJob(
      "opt_1",
      makeSolveResult({
        mode: "ratebook",
        frontier: {
          status: "ok",
          points: [
            {
              total_objective: 120,
              total_volume: 0.9,
              lambda_volume: 0.1,
              converged: true,
            },
          ],
          n_points: 1,
          points_returned: 1,
          constraint_names: ["volume"],
          points_limit: 2000,
          points_truncated: false,
        },
      }),
    )

    const data = useNodeResultsStore.getState().getOptimiserPreview("opt_1")
    expect(data).not.toBeNull()
    render(<OptimiserPreview data={data!} nodeId="opt_1" allNodes={[]} edges={[]} />)

    fireEvent.click(screen.getByText("Summary"))

    expect(screen.getByText("Materialising selected point rates...")).toBeInTheDocument()
    expect(await screen.findByTestId("ratebook-impact-beeswarm")).toBeInTheDocument()
    expect(screen.getByText("Mechanical Price Effect")).toBeInTheDocument()
    expect(screen.getByLabelText("region North: +8.0%")).toBeInTheDocument()
    expect(screen.getByLabelText("region South: -8.0%")).toBeInTheDocument()
    await waitFor(() => {
      expect(screen.queryByText("Materialising selected point rates...")).not.toBeInTheDocument()
    })
    expect(mockSelectFrontierPoint).toHaveBeenCalledWith(
      {
        job_id: "job_123",
        point_index: 0,
        include_ratebook_tables: true,
      },
      { signal: expect.any(AbortSignal) },
    )
  })

  it("retries selected ratebook rate materialisation after the first request is aborted", async () => {
    mockSelectFrontierPoint
      .mockImplementationOnce((_payload: unknown, options: { signal: AbortSignal }) => new Promise((_resolve, reject) => {
        options.signal.addEventListener("abort", () => {
          reject(new DOMException("Aborted", "AbortError"))
        })
      }))
      .mockResolvedValueOnce({
        status: "ok",
        point_index: 0,
        total_objective: 120,
        constraints: { volume: 0.9 },
        baseline_objective: 80,
        baseline_constraints: { volume: 0.8 },
        lambdas: { volume: 0.1 },
        converged: true,
        cd_iterations: 5,
        factor_tables: {
          region: [
            { __factor_group__: "North", optimal_scenario_value: 1.08 },
          ],
        },
        error: null,
      })
    const store = useNodeResultsStore.getState()
    store.startSolveJob("opt_1", "job_123", "Ratebook Optimiser", { volume: { min: 0.9 } }, "h1")
    store.completeSolveJob(
      "opt_1",
      makeSolveResult({
        mode: "ratebook",
        factor_tables: {
          region: [{ __factor_group__: "Base", optimal_scenario_value: 1.0 }],
        },
        frontier: {
          status: "ok",
          points: [
            {
              total_objective: 120,
              total_volume: 0.9,
              lambda_volume: 0.1,
              converged: true,
            },
          ],
          n_points: 1,
          points_returned: 1,
          constraint_names: ["volume"],
          points_limit: 2000,
          points_truncated: false,
        },
      }),
    )

    const data = useNodeResultsStore.getState().getOptimiserPreview("opt_1")
    expect(data).not.toBeNull()
    render(<OptimiserPreview data={data!} nodeId="opt_1" allNodes={[]} edges={[]} />)

    fireEvent.click(screen.getByText("Rates"))
    expect(screen.getByText("Materialising selected point rates...")).toBeInTheDocument()
    expect(mockSelectFrontierPoint).toHaveBeenCalledTimes(1)

    fireEvent.click(screen.getByText("Frontier"))
    fireEvent.click(screen.getByText("Rates"))

    expect(await screen.findByText("North")).toBeInTheDocument()
    expect(mockSelectFrontierPoint).toHaveBeenCalledTimes(2)
  })

  it("shows a clear message if selected point materialisation returns no rate tables", async () => {
    mockSelectFrontierPoint.mockResolvedValueOnce({
      status: "ok",
      point_index: 0,
      total_objective: 120,
      constraints: { volume: 0.9 },
      baseline_objective: 80,
      baseline_constraints: { volume: 0.8 },
      lambdas: { volume: 0.1 },
      converged: true,
      cd_iterations: 5,
      factor_tables: {},
      error: null,
    })
    const store = useNodeResultsStore.getState()
    store.startSolveJob("opt_1", "job_123", "Ratebook Optimiser", { volume: { min: 0.9 } }, "h1")
    store.completeSolveJob(
      "opt_1",
      makeSolveResult({
        mode: "ratebook",
        factor_tables: {
          region: [{ __factor_group__: "Base", optimal_scenario_value: 1.0 }],
        },
        frontier: {
          status: "ok",
          points: [
            {
              total_objective: 120,
              total_volume: 0.9,
              lambda_volume: 0.1,
              converged: true,
            },
          ],
          n_points: 1,
          points_returned: 1,
          constraint_names: ["volume"],
          points_limit: 2000,
          points_truncated: false,
        },
      }),
    )

    const data = useNodeResultsStore.getState().getOptimiserPreview("opt_1")
    expect(data).not.toBeNull()
    render(<OptimiserPreview data={data!} nodeId="opt_1" allNodes={[]} edges={[]} />)

    fireEvent.click(screen.getByText("Rates"))

    expect(await screen.findByText(/No rate tables were returned/)).toBeInTheDocument()
    expect(screen.queryByText("Materialising selected point rates...")).not.toBeInTheDocument()
  })

  it("shows backend detail when selected rate materialisation fails", async () => {
    mockSelectFrontierPoint.mockRejectedValueOnce(
      Object.assign(new Error("HTTP 400"), {
        detail: "Ratebook runtime state is not available for this job.",
      }),
    )
    const store = useNodeResultsStore.getState()
    store.startSolveJob("opt_1", "job_123", "Ratebook Optimiser", { volume: { min: 0.9 } }, "h1")
    store.completeSolveJob(
      "opt_1",
      makeSolveResult({
        mode: "ratebook",
        frontier: {
          status: "ok",
          points: [
            {
              total_objective: 120,
              total_volume: 0.9,
              lambda_volume: 0.1,
              converged: true,
            },
          ],
          n_points: 1,
          points_returned: 1,
          constraint_names: ["volume"],
          points_limit: 2000,
          points_truncated: false,
        },
      }),
    )

    const data = useNodeResultsStore.getState().getOptimiserPreview("opt_1")
    expect(data).not.toBeNull()
    render(<OptimiserPreview data={data!} nodeId="opt_1" allNodes={[]} edges={[]} />)

    fireEvent.click(screen.getByText("Rates"))

    expect(
      await screen.findByText(/Rate table load failed: Ratebook runtime state is not available/),
    ).toBeInTheDocument()
    expect(screen.queryByText(/Rate table load failed: HTTP 400/)).not.toBeInTheDocument()
  })

  it("drops a stale rate-materialisation response when a newer click has already won", async () => {
    // Rapid-click race: a user clicks point 0, the rates request goes
    // out, then they click point 1 before point 0's response arrives.
    // Network jitter delivers point 1's response FIRST.  When point 0's
    // response finally lands, the store must NOT regress to point 0 —
    // the user is now looking at point 1.
    //
    // The previous coverage was a synchronous store-only test; this is
    // the full async integration through the React component.
    let resolvePoint0!: (value: unknown) => void
    let resolvePoint1!: (value: unknown) => void
    const point0Response = {
      status: "ok",
      point_index: 0,
      total_objective: 100,
      constraints: { volume: 0.9 },
      baseline_objective: 80,
      baseline_constraints: { volume: 0.8 },
      lambdas: { volume: 0.05 },
      converged: true,
      cd_iterations: 3,
      factor_tables: {
        region: [{ __factor_group__: "PointZero", optimal_scenario_value: 1.0 }],
      },
      error: null,
    }
    const point1Response = {
      status: "ok",
      point_index: 1,
      total_objective: 130,
      constraints: { volume: 0.93 },
      baseline_objective: 80,
      baseline_constraints: { volume: 0.8 },
      lambdas: { volume: 0.55 },
      converged: true,
      cd_iterations: 5,
      factor_tables: {
        region: [{ __factor_group__: "PointOne", optimal_scenario_value: 1.21 }],
      },
      error: null,
    }
    mockSelectFrontierPoint.mockImplementationOnce(
      () => new Promise((resolve) => { resolvePoint0 = resolve }),
    )
    mockSelectFrontierPoint.mockImplementationOnce(
      () => new Promise((resolve) => { resolvePoint1 = resolve }),
    )

    const store = useNodeResultsStore.getState()
    store.startSolveJob("opt_1", "job_123", "Ratebook Optimiser", { volume: { min: 0.9 } }, "h1")
    store.completeSolveJob(
      "opt_1",
      makeSolveResult({
        mode: "ratebook",
        frontier: {
          status: "ok",
          points: [
            {
              total_objective: 100,
              total_volume: 0.9,
              lambda_volume: 0.05,
              converged: true,
            },
            {
              total_objective: 130,
              total_volume: 0.93,
              lambda_volume: 0.55,
              converged: true,
            },
          ],
          n_points: 2,
          points_returned: 2,
          constraint_names: ["volume"],
          points_limit: 2000,
          points_truncated: false,
        },
      }),
    )

    const data = useNodeResultsStore.getState().getOptimiserPreview("opt_1")
    expect(data).not.toBeNull()
    render(<OptimiserPreview data={data!} nodeId="opt_1" allNodes={[]} edges={[]} />)

    // ── Switch to Rates tab — materialise fires for the auto-selected
    //    point 0; promise is held unresolved (network slow). ──
    fireEvent.click(screen.getByText("Rates"))
    await waitFor(() => {
      expect(mockSelectFrontierPoint).toHaveBeenCalledTimes(1)
    })
    expect(mockSelectFrontierPoint.mock.calls[0][0]).toMatchObject({
      job_id: "job_123",
      point_index: 0,
      include_ratebook_tables: true,
    })
    expect(useNodeResultsStore.getState().solveResults.opt_1.selectedPointIndex).toBe(0)

    // ── User clicks "Next frontier point" before point 0 resolves. ──
    //    The Rates effect cleanup aborts the in-flight request and
    //    fires a new one for point 1.
    fireEvent.click(screen.getByRole("button", { name: "Next frontier point" }))
    await waitFor(() => {
      expect(useNodeResultsStore.getState().solveResults.opt_1.selectedPointIndex).toBe(1)
    })
    await waitFor(() => {
      expect(mockSelectFrontierPoint).toHaveBeenCalledTimes(2)
    })
    expect(mockSelectFrontierPoint.mock.calls[1][0]).toMatchObject({
      job_id: "job_123",
      point_index: 1,
      include_ratebook_tables: true,
    })

    // ── Resolve point 1 FIRST (the user's current selection). ──
    resolvePoint1(point1Response)
    await waitFor(() => {
      const cached = useNodeResultsStore.getState().solveResults.opt_1
      expect(cached.result.factor_tables).toEqual(point1Response.factor_tables)
    })

    // ── Resolve point 0 LATE — must NOT clobber the store. ──
    resolvePoint0(point0Response)
    // Allow microtasks to flush.
    await Promise.resolve()
    await Promise.resolve()

    const final = useNodeResultsStore.getState().solveResults.opt_1
    // The user's selection stays on point 1.
    expect(final.selectedPointIndex).toBe(1)
    // The visible result reflects point 1's response, not the late point 0.
    expect(final.result.total_objective).toBe(130)
    expect(final.result.factor_tables).toEqual(point1Response.factor_tables)
    // Point 1's per-point row was enriched on the way in.
    expect(final.frontier!.points[1]).toEqual(
      expect.objectContaining({ factor_tables: point1Response.factor_tables }),
    )
    // Point 0's late response is COMPLETELY discarded — the
    // OptimiserPreview rates effect cleanup aborts the stale fetch and
    // its sequence-id guard bails the .then() handler before the store
    // is ever touched.  Re-selecting point 0 will trigger a fresh
    // request, not stale leftovers.  This is the safer contract: no
    // partial enrichment from a request the user has already moved past.
    expect(final.frontier!.points[0]).not.toHaveProperty("factor_tables")
  })
})
