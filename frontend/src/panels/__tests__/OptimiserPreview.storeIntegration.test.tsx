import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import OptimiserPreview from "../OptimiserPreview"
import type { ApplyOptimiserResponse } from "../../api/types"
import useNodeResultsStore, {
  resetNodeResultsDerivedCaches,
} from "../../stores/useNodeResultsStore"
import useGraphStore from "../../stores/useGraphStore"
import { makeFrontier, makeFrontierSelect } from "../../test-utils/factories"
import {
  makeOnlineFrontier,
  makeOnlineSolveResult,
  makeRatebookFrontier,
  makeRatebookSolveResult,
} from "../optimiser/__tests__/fixtures"

const mockSelectFrontierPoint = vi.fn()
const mockApplyOptimiser = vi.fn()

vi.mock("../../api/client", () => ({
  applyOptimiser: (...args: unknown[]) => mockApplyOptimiser(...args),
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

/** A local-only inventory, so every node can log and nothing is probed. */
const MLFLOW_INVENTORY = vi.hoisted(() => ({
  status: "ready" as const,
  installed: true,
  importable: true,
  auto: "local" as const,
  destinations: [
    {
      key: "local" as const,
      configured: true,
      destination: "C:/proj/mlruns",
      config_source: "default" as const,
      detail: "",
      probed: false,
      ok: false,
      category: "" as const,
    },
  ],
  detail: "",
}))

vi.mock("../../stores/useSettingsStore", () => ({
  default: (selector: (s: Record<string, unknown>) => unknown) =>
    selector({ mlflow: MLFLOW_INVENTORY }),
  useMlflowDestinations: () => MLFLOW_INVENTORY,
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
    optimiserApplyCache: [],
  })
}

describe("OptimiserPreview store integration", () => {
  beforeEach(() => {
    resetStore()
    mockSelectFrontierPoint.mockReset()
    mockApplyOptimiser.mockReset()
  })

  afterEach(() => {
    cleanup()
    resetStore()
  })

  it("re-renders from the result store when a frontier point is clicked", async () => {
    const store = useNodeResultsStore.getState()
    store.startSolveJob("opt_1", "job_123", "My Optimiser", { loss_ratio: { max: 1.05 } }, "h1", "live", 0)
    store.completeSolveJob(
      "opt_1",
      makeOnlineSolveResult({ frontier: makeFrontier(makeOnlineFrontier(5)) }),
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
      frontier_generation: 0,
      total_objective: 120,
      constraints: { volume: 0.9 },
      baseline_objective: 80,
      baseline_constraints: { volume: 0.8 },
      effective_bounds: { volume: { kind: "min", bound: 0.9 } },
      lambdas: { volume: 0.1 },
      converged: true,
      cd_iterations: 5,
      factor_tables: {
        region: [
          { __factor_group__: "North", optimal_scenario_value: 1.08, quote_count: 10 },
          { __factor_group__: "South", optimal_scenario_value: 0.92, quote_count: 10 },
        ],
      },
      diagnostics_errors: [],
      error: null,
    })
    const store = useNodeResultsStore.getState()
    store.startSolveJob("opt_1", "job_123", "Ratebook Optimiser", { volume: { min: 0.9 } }, "h1", "live", 0)
    store.completeSolveJob(
      "opt_1",
      makeRatebookSolveResult({
        factor_tables: {
          region: [{ __factor_group__: "Base", optimal_scenario_value: 1.0, quote_count: 10 }],
        },
        frontier: makeFrontier(makeRatebookFrontier([{ objective: 120, volume: 0.9, lambda: 0.1 }])),
      }),
    )

    const data = useNodeResultsStore.getState().getOptimiserPreview("opt_1")
    expect(data).not.toBeNull()
    render(<OptimiserPreview data={data!} nodeId="opt_1" allNodes={[]} edges={[]} />)

    expect(screen.getByText("Rates")).toBeInTheDocument()
    fireEvent.click(screen.getByText("Rates"))
    expect(screen.getByText("Materialising selected point rates...")).toBeInTheDocument()

    expect(await screen.findAllByText("North")).not.toHaveLength(0)
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
      frontier_generation: 0,
      total_objective: 120,
      constraints: { volume: 0.9 },
      baseline_objective: 80,
      baseline_constraints: { volume: 0.8 },
      effective_bounds: { volume: { kind: "min", bound: 0.9 } },
      lambdas: { volume: 0.1 },
      converged: true,
      cd_iterations: 5,
      factor_tables: {
        region: [
          { __factor_group__: "North", optimal_scenario_value: 1.08, quote_count: 10 },
          { __factor_group__: "South", optimal_scenario_value: 0.92, quote_count: 10 },
        ],
      },
      diagnostics_errors: [],
      error: null,
    })
    const store = useNodeResultsStore.getState()
    store.startSolveJob("opt_1", "job_123", "Ratebook Optimiser", { volume: { min: 0.9 } }, "h1", "live", 0)
    store.completeSolveJob(
      "opt_1",
      makeRatebookSolveResult({
        frontier: makeFrontier(makeRatebookFrontier([{ objective: 120, volume: 0.9, lambda: 0.1 }])),
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
        frontier_generation: 0,
        total_objective: 120,
        constraints: { volume: 0.9 },
        baseline_objective: 80,
        baseline_constraints: { volume: 0.8 },
        effective_bounds: { volume: { kind: "min", bound: 0.9 } },
        lambdas: { volume: 0.1 },
        converged: true,
        cd_iterations: 5,
        factor_tables: {
          region: [
            { __factor_group__: "North", optimal_scenario_value: 1.08, quote_count: 10 },
          ],
        },
        diagnostics_errors: [],
        error: null,
      })
    const store = useNodeResultsStore.getState()
    store.startSolveJob("opt_1", "job_123", "Ratebook Optimiser", { volume: { min: 0.9 } }, "h1", "live", 0)
    store.completeSolveJob(
      "opt_1",
      makeRatebookSolveResult({
        factor_tables: {
          region: [{ __factor_group__: "Base", optimal_scenario_value: 1.0, quote_count: 10 }],
        },
        frontier: makeFrontier(makeRatebookFrontier([{ objective: 120, volume: 0.9, lambda: 0.1 }])),
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

    expect(await screen.findAllByText("North")).not.toHaveLength(0)
    expect(mockSelectFrontierPoint).toHaveBeenCalledTimes(2)
  })

  it("shows a clear message if selected point materialisation returns no rate tables", async () => {
    mockSelectFrontierPoint.mockResolvedValueOnce({
      status: "ok",
      point_index: 0,
      frontier_generation: 0,
      total_objective: 120,
      constraints: { volume: 0.9 },
      baseline_objective: 80,
      baseline_constraints: { volume: 0.8 },
      effective_bounds: { volume: { kind: "min", bound: 0.9 } },
      lambdas: { volume: 0.1 },
      converged: true,
      cd_iterations: 5,
      factor_tables: {},
      diagnostics_errors: [],
      error: null,
    })
    const store = useNodeResultsStore.getState()
    store.startSolveJob("opt_1", "job_123", "Ratebook Optimiser", { volume: { min: 0.9 } }, "h1", "live", 0)
    store.completeSolveJob(
      "opt_1",
      makeRatebookSolveResult({
        factor_tables: {
          region: [{ __factor_group__: "Base", optimal_scenario_value: 1.0, quote_count: 10 }],
        },
        frontier: makeFrontier(makeRatebookFrontier([{ objective: 120, volume: 0.9, lambda: 0.1 }])),
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
    store.startSolveJob("opt_1", "job_123", "Ratebook Optimiser", { volume: { min: 0.9 } }, "h1", "live", 0)
    store.completeSolveJob(
      "opt_1",
      makeRatebookSolveResult({
        frontier: makeFrontier(makeRatebookFrontier([{ objective: 120, volume: 0.9, lambda: 0.1 }])),
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
      frontier_generation: 0,
      total_objective: 100,
      constraints: { volume: 0.9 },
      baseline_objective: 80,
      baseline_constraints: { volume: 0.8 },
      effective_bounds: { volume: { kind: "min", bound: 0.9 } },
      lambdas: { volume: 0.05 },
      converged: true,
      cd_iterations: 3,
      factor_tables: {
        region: [{ __factor_group__: "PointZero", optimal_scenario_value: 1.0, quote_count: 10 }],
      },
      diagnostics_errors: [],
      error: null,
    }
    const point1Response = {
      status: "ok",
      point_index: 1,
      frontier_generation: 0,
      total_objective: 130,
      constraints: { volume: 0.93 },
      baseline_objective: 80,
      baseline_constraints: { volume: 0.8 },
      effective_bounds: { volume: { kind: "min", bound: 0.9 } },
      lambdas: { volume: 0.55 },
      converged: true,
      cd_iterations: 5,
      factor_tables: {
        region: [{ __factor_group__: "PointOne", optimal_scenario_value: 1.21, quote_count: 10 }],
      },
      diagnostics_errors: [],
      error: null,
    }
    mockSelectFrontierPoint.mockImplementationOnce(
      () => new Promise((resolve) => { resolvePoint0 = resolve }),
    )
    mockSelectFrontierPoint.mockImplementationOnce(
      () => new Promise((resolve) => { resolvePoint1 = resolve }),
    )

    const store = useNodeResultsStore.getState()
    store.startSolveJob("opt_1", "job_123", "Ratebook Optimiser", { volume: { min: 0.9 } }, "h1", "live", 0)
    store.completeSolveJob(
      "opt_1",
      makeRatebookSolveResult({
        frontier: makeFrontier(makeRatebookFrontier([
          { objective: 100, volume: 0.9, lambda: 0.05 },
          { objective: 130, volume: 0.93, lambda: 0.55 },
        ])),
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
      expect(cached.result?.factor_tables).toEqual(point1Response.factor_tables)
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
    expect(final.result?.total_objective).toBe(130)
    expect(final.result?.factor_tables).toEqual(point1Response.factor_tables)
    // Point 1's stored summary was enriched on the way in.
    expect(final.frontier!.point_summaries[1]).toEqual(
      expect.objectContaining({ factor_tables: point1Response.factor_tables }),
    )
    // Point 0's late response is COMPLETELY discarded — the
    // OptimiserPreview rates effect cleanup aborts the stale fetch and
    // its sequence-id guard bails the .then() handler before the store
    // is ever touched.  Re-selecting point 0 will trigger a fresh
    // request, not stale leftovers.  This is the safer contract: no
    // partial enrichment from a request the user has already moved past.
    expect(final.frontier!.point_summaries[0].factor_tables).toBeNull()
  })

  describe("rates across a frontier recompute of the same job", () => {
    /** Install a ratebook solve of job_123 whose one frontier point is worth `objective`. */
    function solveRatebookGeneration(generation: number, objective: number) {
      const store = useNodeResultsStore.getState()
      store.startSolveJob("opt_1", "job_123", "Ratebook Optimiser", { volume: { min: 0.9 } }, "h1", "live", 0)
      store.completeSolveJob("opt_1", makeRatebookSolveResult({
        frontier_generation: generation,
        frontier: makeFrontier({
          ...makeRatebookFrontier([{ objective, volume: 0.9, lambda: 0.1 }]),
          frontier_generation: generation,
        }),
      }))
    }

    function ratesReply(generation: number, objective: number, group: string) {
      return makeFrontierSelect({
        point_index: 0,
        frontier_generation: generation,
        total_objective: objective,
        constraints: { volume: 0.9 },
        effective_bounds: { volume: { kind: "min", bound: 0.9 } },
        lambdas: { volume: 0.1 },
        factor_tables: {
          region: [{ __factor_group__: group, optimal_scenario_value: 1.08, quote_count: 10 }],
        },
      })
    }

    it("re-requests the reused point index and drops the earlier generation's late reply", async () => {
      let resolveEarlier!: (value: unknown) => void
      mockSelectFrontierPoint
        .mockImplementationOnce(() => new Promise((resolve) => { resolveEarlier = resolve }))
        .mockResolvedValueOnce(ratesReply(2, 200, "RecomputedRegion"))
      solveRatebookGeneration(1, 100)
      render(<OptimiserPreview data={useNodeResultsStore.getState().getOptimiserPreview("opt_1")!} nodeId="opt_1" allNodes={[]} edges={[]} />)

      fireEvent.click(screen.getByRole("tab", { name: "Rates" }))
      await waitFor(() => expect(mockSelectFrontierPoint).toHaveBeenCalledTimes(1))

      // The recompute keeps job_123, and point 0 is selected again: a different point.
      act(() => solveRatebookGeneration(2, 200))

      expect(await screen.findAllByText("RecomputedRegion")).not.toHaveLength(0)
      expect(mockSelectFrontierPoint).toHaveBeenCalledTimes(2)
      expect(mockSelectFrontierPoint).toHaveBeenLastCalledWith(
        { job_id: "job_123", point_index: 0, include_ratebook_tables: true },
        { signal: expect.any(AbortSignal) },
      )

      // Generation 1's reply lands last; it must not relabel generation 1's totals as generation 2's.
      await act(async () => { resolveEarlier(ratesReply(1, 100, "StaleRegion")) })

      const cached = useNodeResultsStore.getState().solveResults.opt_1
      expect(cached.originalResult?.frontier_generation).toBe(2)
      expect(cached.result?.total_objective).toBe(200)
      expect(cached.frontier!.point_summaries[0].total_objective).toBe(200)
      expect(cached.result?.factor_tables).toEqual(ratesReply(2, 200, "RecomputedRegion").factor_tables)
      expect(screen.queryByText("StaleRegion")).not.toBeInTheDocument()
    })

    it("reports a reply from a generation the result does not show instead of installing it", async () => {
      // The server recomputed before this result's generation 1 was installed over.
      mockSelectFrontierPoint.mockResolvedValueOnce(ratesReply(2, 300, "NewerRegion"))
      solveRatebookGeneration(1, 100)
      const before = useNodeResultsStore.getState().solveResults.opt_1
      render(<OptimiserPreview data={useNodeResultsStore.getState().getOptimiserPreview("opt_1")!} nodeId="opt_1" allNodes={[]} edges={[]} />)

      fireEvent.click(screen.getByRole("tab", { name: "Rates" }))

      expect(await screen.findByText(
        /Rate table load failed: The server answered for frontier generation 2, but this result shows generation 1\./,
      )).toBeInTheDocument()
      expect(screen.queryByText("NewerRegion")).not.toBeInTheDocument()
      expect(useNodeResultsStore.getState().solveResults.opt_1).toBe(before)
    })
  })

  describe("per-quote detail (Quotes)", () => {
    function applyResponse(quoteId: string, rowCount = 1250, frontierGeneration = 0): ApplyOptimiserResponse {
      return {
        status: "ok",
        total_objective: 1250000,
        constraints: { loss_ratio: 0.66 },
        from_artifact: true,
        columns: [
          { name: "quote_id", role: "id", sortable: false, filterable: false },
          { name: "optimal_scenario_value", role: "scenario", sortable: true, filterable: false },
        ],
        preview: [{ quote_id: quoteId, optimal_scenario_value: 1.05 }],
        row_count: rowCount,
        matched_row_count: rowCount,
        offset: 0,
        preview_row_count: 1,
        preview_row_limit: 100,
        frontier_generation: frontierGeneration,
        error: null,
      }
    }

    /** The request body of the tab's default page: every query field is sent. */
    const DEFAULT_PAGE = {
      sort_by: null,
      descending: false,
      quote_id_prefix: null,
      filters: {
        scenario_value_min: null,
        scenario_value_max: null,
        at_range_edge: false,
        analysis_equals: {},
        deployed_factor_differs: false,
      },
      offset: 0,
      limit: 100,
    }

    /** Install an online solve (with a frontier unless `frontier` is null) under `jobId`. */
    function solveOnline(jobId: string, { generation = 0, frontier = true } = {}) {
      const store = useNodeResultsStore.getState()
      store.startSolveJob("opt_1", jobId, "My Optimiser", { loss_ratio: { max: 1.05 } }, "h1", "live", 0)
      store.completeSolveJob("opt_1", makeOnlineSolveResult({
        frontier_generation: generation,
        frontier: frontier
          ? makeFrontier(makeOnlineFrontier(5, { frontier_generation: generation }))
          : null,
      }))
    }

    function renderLive() {
      const data = useNodeResultsStore.getState().getOptimiserPreview("opt_1")
      if (!data) throw new Error("No optimiser preview for opt_1")
      return render(<OptimiserPreview data={data} nodeId="opt_1" allNodes={[]} edges={[]} />)
    }

    function openQuotes() {
      fireEvent.click(screen.getByRole("tab", { name: "Quotes" }))
    }

    it("shows the solved result's per-quote detail without a point index", async () => {
      mockApplyOptimiser.mockResolvedValueOnce(applyResponse("Q001"))
      solveOnline("job_123", { frontier: false })
      renderLive()
      openQuotes()

      expect(await screen.findByText("Q001")).toBeInTheDocument()
      expect(mockApplyOptimiser).toHaveBeenCalledWith(
        { job_id: "job_123", ...DEFAULT_PAGE },
        { signal: expect.any(AbortSignal) },
      )
      expect(screen.getByText("Showing 1–1 of 1,250 matching (of 1,250)")).toBeInTheDocument()
    })

    it("follows the selected frontier point, and Retry refetches after a failure", async () => {
      mockApplyOptimiser
        .mockRejectedValueOnce(new Error("artifact missing"))
        .mockResolvedValueOnce(applyResponse("Q002"))
      solveOnline("job_123")
      useNodeResultsStore.getState().selectFrontierPoint("opt_1", 1)
      renderLive()
      openQuotes()

      expect(await screen.findByRole("alert")).toHaveTextContent("Per-quote detail could not be loaded: artifact missing")
      expect(mockApplyOptimiser).toHaveBeenCalledWith(
        { job_id: "job_123", point_index: 1, ...DEFAULT_PAGE },
        { signal: expect.any(AbortSignal) },
      )

      fireEvent.click(screen.getByRole("button", { name: "Retry" }))

      expect(await screen.findByText("Q002")).toBeInTheDocument()
      expect(mockApplyOptimiser).toHaveBeenCalledTimes(2)
      expect(mockApplyOptimiser).toHaveBeenLastCalledWith(
        { job_id: "job_123", point_index: 1, ...DEFAULT_PAGE },
        { signal: expect.any(AbortSignal) },
      )
    })

    it("makes no second request when Quotes is reopened for the same target", async () => {
      mockApplyOptimiser.mockResolvedValue(applyResponse("Q001"))
      solveOnline("job_123")
      renderLive()
      openQuotes()
      expect(await screen.findByText("Q001")).toBeInTheDocument()

      fireEvent.click(screen.getByRole("tab", { name: "Summary" }))
      openQuotes()

      expect(screen.getByText("Q001")).toBeInTheDocument()
      expect(mockApplyOptimiser).toHaveBeenCalledTimes(1)
    })

    it("refetches the same point index after a frontier recompute", async () => {
      mockApplyOptimiser
        .mockResolvedValueOnce(applyResponse("OLD_POINT"))
        .mockResolvedValueOnce(applyResponse("NEW_POINT", 1250, 1))
      solveOnline("job_123", { generation: 0 })
      renderLive()
      openQuotes()
      expect(await screen.findByText("OLD_POINT")).toBeInTheDocument()

      // The recomputed frontier reuses point index 0 for a different point.
      act(() => solveOnline("job_123", { generation: 1 }))
      openQuotes()

      expect(await screen.findByText("NEW_POINT")).toBeInTheDocument()
      expect(mockApplyOptimiser).toHaveBeenCalledTimes(2)
      expect(mockApplyOptimiser).toHaveBeenLastCalledWith(
        { job_id: "job_123", point_index: 0, ...DEFAULT_PAGE },
        { signal: expect.any(AbortSignal) },
      )
    })

    it("discards a late response from an earlier job", async () => {
      let resolveEarlier!: (response: ApplyOptimiserResponse) => void
      mockApplyOptimiser
        .mockImplementationOnce(() => new Promise((resolve) => { resolveEarlier = resolve }))
        .mockResolvedValueOnce(applyResponse("NEW_JOB"))
      solveOnline("job_123")
      renderLive()
      openQuotes()
      await waitFor(() => expect(mockApplyOptimiser).toHaveBeenCalledTimes(1))

      act(() => solveOnline("job_456"))
      openQuotes()
      expect(await screen.findByText("NEW_JOB")).toBeInTheDocument()

      await act(async () => { resolveEarlier(applyResponse("EARLIER_JOB")) })

      expect(screen.queryByText("EARLIER_JOB")).not.toBeInTheDocument()
      expect(screen.getByText("NEW_JOB")).toBeInTheDocument()
      const cached = useNodeResultsStore.getState().optimiserApplyCache
      expect(cached.map((entry) => entry.identity.jobId)).toEqual(["job_456"])
    })

    it("discards a late response from an earlier frontier generation", async () => {
      let resolveEarlier!: (response: ApplyOptimiserResponse) => void
      mockApplyOptimiser
        .mockImplementationOnce(() => new Promise((resolve) => { resolveEarlier = resolve }))
        .mockResolvedValueOnce(applyResponse("RECOMPUTED", 1250, 1))
      solveOnline("job_123", { generation: 0 })
      renderLive()
      openQuotes()
      await waitFor(() => expect(mockApplyOptimiser).toHaveBeenCalledTimes(1))

      act(() => solveOnline("job_123", { generation: 1 }))
      openQuotes()
      expect(await screen.findByText("RECOMPUTED")).toBeInTheDocument()

      await act(async () => { resolveEarlier(applyResponse("EARLIER_GENERATION")) })

      expect(screen.queryByText("EARLIER_GENERATION")).not.toBeInTheDocument()
      const cached = useNodeResultsStore.getState().optimiserApplyCache
      expect(cached.map((entry) => entry.identity.frontierGeneration)).toEqual([1])
    })
  })

  it("offers Retry when selected ratebook rates fail to load, and Retry refetches them", async () => {
    mockSelectFrontierPoint
      .mockRejectedValueOnce(Object.assign(new Error("HTTP 500"), { detail: "Rates unavailable." }))
      .mockResolvedValueOnce({
        status: "ok",
        point_index: 0,
        total_objective: 120,
        constraints: { volume: 0.9 },
        baseline_objective: 80,
        baseline_constraints: { volume: 0.8 },
        effective_bounds: { volume: { kind: "min", bound: 0.9 } },
        lambdas: { volume: 0.1 },
        converged: true,
        cd_iterations: 5,
        factor_tables: {
          region: [{ __factor_group__: "North", optimal_scenario_value: 1.08, quote_count: 120 }],
        },
        frontier_generation: 0,
        diagnostics_errors: [],
        error: null,
      })
    const store = useNodeResultsStore.getState()
    store.startSolveJob("opt_1", "job_123", "Ratebook Optimiser", { volume: { min: 0.9 } }, "h1", "live", 0)
    store.completeSolveJob("opt_1", makeRatebookSolveResult({
      frontier: makeFrontier(makeRatebookFrontier([{ objective: 120, volume: 0.9, lambda: 0.1 }])),
    }))
    const data = useNodeResultsStore.getState().getOptimiserPreview("opt_1")!
    render(<OptimiserPreview data={data} nodeId="opt_1" allNodes={[]} edges={[]} />)

    fireEvent.click(screen.getByRole("tab", { name: "Rates" }))
    expect(await screen.findByText(/Rate table load failed: Rates unavailable./)).toBeInTheDocument()

    fireEvent.click(screen.getByRole("button", { name: "Retry" }))

    expect(await screen.findAllByText("North")).not.toHaveLength(0)
    expect(mockSelectFrontierPoint).toHaveBeenCalledTimes(2)
  })
})
