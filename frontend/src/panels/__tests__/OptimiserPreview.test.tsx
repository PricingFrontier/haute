import { describe, it, expect, vi, afterEach, beforeEach } from "vitest"
import { render, screen, fireEvent, cleanup, waitFor, within } from "@testing-library/react"
import useOptimiserPublishStore from "../../stores/useOptimiserPublishStore"
import OptimiserPreview from "../OptimiserPreview"
import type { OptimiserPreviewData, FrontierData } from "../OptimiserPreview"
import type { OptimiserSolveResult } from "../../api/types"
import type { SimpleNode } from "../editors"
import type { MlflowInventoryState } from "../../utils/mlflowDestinations"
import {
  makeAdjustmentReport,
  makeOnlineFrontier,
  makeOnlineFrontierPoint,
  makeOnlineSolveResult,
  makePointSummary,
  makeRatebookSolveResult,
} from "../optimiser/__tests__/fixtures"
import { makeFrontierSelect } from "../../test-utils/factories"

// ── Mocks ────────────────────────────────────────────────────────

const mockSelectFrontierPointAPI = vi.fn()
const mockSaveOptimiser = vi.fn()
const mockLogOptimiserToMlflow = vi.fn()
const mockApplyOptimiser = vi.fn()
const mockSolveOptimiser = vi.fn()
const mockGetOptimiserSegments = vi.fn()
const mockGetOptimiserSegmentIndex = vi.fn()

vi.mock("../../api/client", () => ({
  getOptimiserSegments: (...args: unknown[]) => mockGetOptimiserSegments(...args),
  getOptimiserSegmentIndex: (...args: unknown[]) => mockGetOptimiserSegmentIndex(...args),
  solveOptimiser: (...args: unknown[]) => mockSolveOptimiser(...args),
  cancelOptimiserSolve: vi.fn(),
  selectFrontierPoint: (...args: unknown[]) => mockSelectFrontierPointAPI(...args),
  saveOptimiser: (...args: unknown[]) => mockSaveOptimiser(...args),
  logOptimiserToMlflow: (...args: unknown[]) => mockLogOptimiserToMlflow(...args),
  applyOptimiser: (...args: unknown[]) => mockApplyOptimiser(...args),
}))

vi.mock("../../hooks/useDragResize", () => ({
  useDragResize: () => ({
    height: 320,
    containerRef: { current: null },
    onDragStart: vi.fn(),
    resizeToHeight: vi.fn(),
  }),
}))

const mockStoreSelectPoint = vi.fn()
/** The cached solve the stale check compares with the node's config; none by default. */
const mockSolveState = vi.hoisted(() => ({
  results: {} as Record<string, unknown>,
  /** The source-aware data-input column cache the editor and Re-run share. */
  columnCache: {} as Record<string, unknown>,
}))
const mockStoreUpdateAfterSelect = vi.fn()
const mockStartSolveJob = vi.fn()

vi.mock("../../stores/useNodeResultsStore", async (importOriginal) => {
  const state = () => ({
    getOptimiserPreview: () => null,
    selectFrontierPoint: mockStoreSelectPoint,
    updateFrontierAfterSelect: mockStoreUpdateAfterSelect,
    solveResults: mockSolveState.results,
    solveJobs: {},
    columnCache: mockSolveState.columnCache,
    setColumns: vi.fn(),
    startSolveJob: mockStartSolveJob,
    failSolveJob: vi.fn(),
  })
  return {
    ...(await importOriginal<typeof import("../../stores/useNodeResultsStore")>()),
    default: Object.assign(
      (selector: (s: Record<string, unknown>) => unknown) => selector(state()),
      { getState: state },
    ),
  }
})

/**
 * The inventory the preview reads. `base()` is the default workspace: MLflow
 * installed, local only, so auto is local and every node can log.
 */
const mlflowMockState = vi.hoisted(() => {
  const base = (): MlflowInventoryState => ({
    status: "ready",
    installed: true,
    importable: true,
    destinations: [
      {
        key: "local",
        configured: true,
        destination: "C:/proj/mlruns",
        config_source: "default",
        detail: "",
        probed: false,
        ok: false,
        category: "",
      },
    ],
    detail: "",
  })
  return { base, current: base() }
})

vi.mock("../../stores/useSettingsStore", () => ({
  default: Object.assign(
    (selector: (s: Record<string, unknown>) => unknown) =>
      selector({ mlflow: mlflowMockState.current, activeSource: "live" }),
    { getState: () => ({ activeSource: "live" }) },
  ),
  useMlflowDestinations: () => mlflowMockState.current,
}))

/** The input feeding the optimiser in the stale-result tests. */
const QUOTES_NODE: SimpleNode = {
  id: "quotes",
  data: {
    label: "quotes",
    description: "",
    nodeType: "dataInput",
    config: {},
    _defaultInputName: "quotes",
    _sourceHandleInputNames: {},
    _columns: [
      { name: "profit", dtype: "Float64" },
      { name: "quote_id", dtype: "String" },
      { name: "scenario_index", dtype: "Int64" },
      { name: "scenario_value", dtype: "Float64" },
    ],
  },
}

/** An optimiser node carrying `config`, as the preview finds it in the graph. */
function optimiserNode(config: Record<string, unknown> = {}): SimpleNode {
  return {
    id: "opt_1",
    data: { label: "My Optimiser", description: "", nodeType: "optimiser", config },
  }
}

// ── Helpers ──────────────────────────────────────────────────────

function makeSolveResult(
  overrides: Partial<OptimiserSolveResult> = {},
): OptimiserSolveResult {
  return makeOnlineSolveResult(overrides)
}

function makeFrontier(n = 5, overrides: Partial<FrontierData> = {}): FrontierData {
  // Each point is solved at its own swept max, 0.58, 0.59, ...: point 4
  // (0.63 against 0.62) breaches it, though it meets the configured 1.05.
  return makeOnlineFrontier(n, overrides)
}

/** The displayed result for a selected point, as the results store builds it:
 *  the point's server summary over the as-solved result. */
function pointResult(frontier: FrontierData, index: number, base = makeSolveResult()): OptimiserSolveResult {
  const summary = frontier.point_summaries[index]
  const overlay: Record<string, unknown> = {}
  for (const [field, value] of Object.entries(summary)) overlay[field] = value ?? undefined
  return { ...base, ...overlay, selected_frontier_point: index }
}

/** The text of each cell in one constraint's attainment row. */
function attainmentCells(name: string): (string | null)[] {
  const table = screen.getByRole("table", { name: "Constraint attainment" })
  const row = within(table).getByRole("rowheader", { name }).closest("tr")
  if (!row) throw new Error(`No attainment row for ${name}`)
  return within(row).getAllByRole("cell").map((cell) => cell.textContent)
}

function makeData(overrides: Partial<OptimiserPreviewData> = {}): OptimiserPreviewData {
  // A result given without its solve is the as-solved result itself, unless
  // it is a selected point's (see pointResult), whose solve is the default.
  const result = overrides.result ?? makeSolveResult()
  const solvedResult = overrides.solvedResult
    ?? (result.selected_frontier_point == null ? result : makeSolveResult())
  return {
    result,
    solvedResult,
    jobId: "job_123",
    constraints: { loss_ratio: { max: 1.05 } },
    nodeLabel: "My Optimiser",
    frontier: null,
    selectedPointIndex: null,
    ...overrides,
  }
}

function renderPreview(overrides: Partial<Parameters<typeof OptimiserPreview>[0]> = {}) {
  const props = {
    data: makeData(),
    nodeId: "opt_1",
    allNodes: [],
    edges: [],
    ...overrides,
  }
  return { ...render(<OptimiserPreview {...props} />), props }
}

// ── Tests ────────────────────────────────────────────────────────

describe("OptimiserPreview", () => {
  afterEach(cleanup)

  beforeEach(() => {
    vi.clearAllMocks()
    useOptimiserPublishStore.setState({ byNode: {} })
    mockSolveState.results = {}
    mockSolveState.columnCache = {}
    mlflowMockState.current = mlflowMockState.base()
    mockSelectFrontierPointAPI.mockResolvedValue({
      status: "ok",
      total_objective: 1250000,
      constraints: { loss_ratio: 0.66 },
      baseline_objective: 1200000,
      baseline_constraints: { loss_ratio: 0.60 },
      lambdas: { loss_ratio: 0.006 },
      converged: true,
      error: null,
    })
    mockSaveOptimiser.mockResolvedValue({ status: "ok", path: "output/optimiser_My_Optimiser_opt_1.json", message: "" })
    mockLogOptimiserToMlflow.mockResolvedValue({ status: "ok", backend: "mlflow", experiment_name: "", run_id: "abc123", run_url: null, tracking_uri: "", error: null })
    mockApplyOptimiser.mockResolvedValue({
      status: "ok",
      total_objective: 1250000,
      constraints: { loss_ratio: 0.66 },
      from_artifact: false,
      preview: [{ quote_id: "Q001", optimal_scenario_value: 1.05 }],
      row_count: 1,
      preview_row_count: 1,
      preview_row_limit: 100,
      preview_truncated: false,
      error: null,
    })
  })

  describe("Summary tab (default when no frontier)", () => {
    it("renders node label in header", () => {
      renderPreview()
      expect(screen.getByText("My Optimiser")).toBeInTheDocument()
    })

    it("shows Converged status when converged", () => {
      renderPreview()
      expect(screen.getByText(/Converged/)).toBeInTheDocument()
    })

    it("shows Not converged status when not converged", () => {
      renderPreview({ data: makeData({ result: makeSolveResult({ converged: false }) }) })
      expect(screen.getByText(/Not converged/)).toBeInTheDocument()
    })

    it("renders iteration count", () => {
      renderPreview()
      expect(screen.getByText(/15 iters/)).toBeInTheDocument()
    })

    it("renders quote count", () => {
      renderPreview()
      expect(screen.getByText("Converged | 15 iters | 50,000 quotes")).toBeInTheDocument()
    })

    it("surfaces frontier computation failures", () => {
      renderPreview({
        data: makeData({
          result: makeSolveResult({
            frontier: null,
            frontier_error: "Frontier unavailable: frontier exploded",
          }),
          frontier: null,
        }),
      })

      expect(screen.getByText("Frontier unavailable: frontier exploded")).toBeInTheDocument()
    })

    it("renders Objective label and values", () => {
      renderPreview()
      // Click Summary tab in case it's not the default (no frontier data -> summary is default)
      fireEvent.click(screen.getByText("Summary"))
      expect(screen.getByText("Objective")).toBeInTheDocument()
      expect(screen.getByText("Optimised")).toBeInTheDocument()
      expect(screen.queryByText("Baseline")).not.toBeInTheDocument()
    })

    it("renders formatted objective value", () => {
      renderPreview()
      fireEvent.click(screen.getByText("Summary"))
      // 1234567 formatted as "1.23M"
      expect(screen.getByText("1.23M")).toBeInTheDocument()
    })

    it("renders constraints section", () => {
      renderPreview()
      fireEvent.click(screen.getByText("Summary"))
      expect(screen.getByText("Constraints")).toBeInTheDocument()
      expect(screen.getAllByText("loss_ratio").length).toBeGreaterThanOrEqual(1)
    })

    it("renders lambda values", () => {
      renderPreview()
      fireEvent.click(screen.getByText("Summary"))
      expect(screen.getByRole("columnheader", { name: /λ \(multiplier\)/ })).toBeInTheDocument()
      expect(screen.queryByText(/shadow price/)).not.toBeInTheDocument()
      expect(screen.getByText("0.005000")).toBeInTheDocument()
    })

    it("does not render objective baseline comparisons", () => {
      renderPreview()
      fireEvent.click(screen.getByText("Summary"))
      expect(screen.queryByText("Uplift")).not.toBeInTheDocument()
      expect(screen.queryByText("2.88%")).not.toBeInTheDocument()
    })

    it("does not render constraint baseline ratios", () => {
      renderPreview()
      fireEvent.click(screen.getByText("Summary"))
      expect(screen.queryByText(/108\.3%/)).not.toBeInTheDocument()
    })

    it("shows Summary tab button", () => {
      renderPreview()
      expect(screen.getByText("Summary")).toBeInTheDocument()
    })
  })

  describe("tab switching", () => {
    it("hides Frontier tab when no frontier data", () => {
      renderPreview()
      expect(screen.queryByText("Frontier")).not.toBeInTheDocument()
    })

    it("falls back to Summary when frontier data disappears while Frontier is active", () => {
      const { rerender } = renderPreview({
        data: makeData({ frontier: makeFrontier() }),
      })
      expect(screen.getByText(/5 frontier points/)).toBeInTheDocument()

      rerender(<OptimiserPreview data={makeData({ frontier: null })} nodeId="opt_1" allNodes={[]} edges={[]} />)

      expect(screen.queryByText("Frontier")).not.toBeInTheDocument()
      expect(screen.queryByText(/No frontier data available/)).not.toBeInTheDocument()
      expect(screen.getByText("Objective")).toBeInTheDocument()
      expect(screen.getByText("Optimised")).toBeInTheDocument()
    })

    it("shows one selected ratebook factor at a time in the Rates tab", () => {
      renderPreview({
        data: makeData({
          result: makeSolveResult({
            mode: "ratebook",
            factor_tables: {
              age_band: [
                { __factor_group__: "17-24", optimal_scenario_value: 0.875, quote_count: 10 },
                { __factor_group__: "25-39", optimal_scenario_value: 1.125, quote_count: 10 },
              ],
              region: [
                { __factor_group__: "North", optimal_scenario_value: 1.05, quote_count: 10 },
              ],
            },
          }),
        }),
      })

      fireEvent.click(screen.getByText("Rates"))

      expect(screen.getByRole("heading", { name: "age_band" })).toBeInTheDocument()
      expect(screen.getAllByText("17-24").length).toBeGreaterThan(0)
      expect(screen.getAllByText("0.8750").length).toBeGreaterThan(0)
      expect(screen.getAllByText("25-39").length).toBeGreaterThan(0)
      expect(screen.getAllByText("1.1250").length).toBeGreaterThan(0)
      expect(screen.queryByText("North")).not.toBeInTheDocument()

      fireEvent.click(screen.getByRole("button", { name: "region" }))

      expect(screen.getByRole("heading", { name: "region" })).toBeInTheDocument()
      expect(screen.getAllByText("North").length).toBeGreaterThan(0)
      expect(screen.getAllByText("1.0500").length).toBeGreaterThan(0)
      expect(screen.queryByText("17-24")).not.toBeInTheDocument()
    })

    it("keeps the chosen Rates factor across tab switches", () => {
      renderPreview({
        data: makeData({
          result: makeSolveResult({
            mode: "ratebook",
            factor_tables: {
              age_band: [
                { __factor_group__: "17-24", optimal_scenario_value: 0.875, quote_count: 10 },
                { __factor_group__: "25-39", optimal_scenario_value: 1.125, quote_count: 10 },
              ],
              region: [
                { __factor_group__: "North", optimal_scenario_value: 1.05, quote_count: 10 },
              ],
            },
          }),
        }),
      })

      fireEvent.click(screen.getByText("Rates"))
      fireEvent.click(screen.getByRole("button", { name: "region" }))
      fireEvent.click(screen.getByText("Summary"))
      fireEvent.click(screen.getByText("Rates"))

      expect(screen.getByRole("heading", { name: "region" })).toBeInTheDocument()
    })

    it("shares the chosen key between Rates and Segments", async () => {
      mockGetOptimiserSegmentIndex.mockReturnValue(new Promise(() => {}))
      mockGetOptimiserSegments.mockReturnValue(new Promise(() => {}))
      const factorKey = (key: string) => ({
        key,
        source: "factor" as const,
        binning: "categorical" as const,
        available: true,
        unavailable_reason: null,
      })
      renderPreview({
        data: makeData({
          result: makeSolveResult({
            mode: "ratebook",
            factor_tables: {
              age_band: [
                { __factor_group__: "17-24", optimal_scenario_value: 0.875, quote_count: 10 },
              ],
              region: [
                { __factor_group__: "North", optimal_scenario_value: 1.05, quote_count: 10 },
              ],
            },
            segment_keys: [factorKey("age_band"), factorKey("region")],
          }),
        }),
      })

      fireEvent.click(screen.getByText("Rates"))
      fireEvent.click(screen.getByRole("button", { name: "region" }))
      fireEvent.click(screen.getByText("Segments"))

      expect(screen.getByRole("button", { name: "region" }).getAttribute("aria-pressed")).toBe("true")
      await waitFor(() => expect(mockGetOptimiserSegments).toHaveBeenCalled())
      expect(mockGetOptimiserSegments.mock.calls[0][0]).toMatchObject({ key: "region", weight: "quotes" })

      fireEvent.click(screen.getByRole("button", { name: "age_band" }))
      fireEvent.click(screen.getByText("Rates"))
      expect(screen.getByRole("heading", { name: "age_band" })).toBeInTheDocument()
    })

    it("orders Rates tab factors and levels by the configured banding source", () => {
      renderPreview({
        allNodes: [
          {
            id: "banding_1",
            data: {
              label: "Age Vehicle Banding",
              description: "",
              nodeType: "banding",
              config: {
                factors: [
                  {
                    banding: "breakpoints",
                    column: "proposer_age",
                    outputColumn: "proposer_age_band",
                    rules: [
                      { boundary: "27", label: "20-27" },
                      { boundary: "34", label: "28-34" },
                    ],
                    default: "missing",
                  },
                  {
                    banding: "breakpoints",
                    column: "vehicle_age",
                    outputColumn: "vehicle_age_band",
                    rules: [
                      { boundary: "3", label: "1-3" },
                      { boundary: "5", label: "4-5" },
                      { boundary: "11", label: "10-11" },
                    ],
                    default: "missing",
                  },
                  {
                    banding: "categorical",
                    column: "channel",
                    outputColumn: "channel_band",
                    rules: [
                      { value: "direct_web", assignment: "direct_web" },
                      { value: "broker", assignment: "broker" },
                    ],
                  },
                ],
              },
            },
          },
          {
            id: "opt_1",
            data: {
              label: "My Optimiser",
              description: "",
              nodeType: "optimiser",
              config: { banding_source: "Age_Vehicle_Banding" },
            },
          },
        ],
        edges: [
          {
            id: "e1",
            source: "banding_1",
            target: "opt_1",
            data: { _inputName: "Age_Vehicle_Banding" },
          },
        ],
        data: makeData({
          result: makeSolveResult({
            mode: "ratebook",
            factor_tables: {
              channel_band: [
                { __factor_group__: "broker", optimal_scenario_value: 1.2, quote_count: 10 },
                { __factor_group__: "direct_web", optimal_scenario_value: 1.1, quote_count: 10 },
              ],
              vehicle_age_band: [
                { __factor_group__: "10-11", optimal_scenario_value: 0.9, quote_count: 10 },
                { __factor_group__: "1-3", optimal_scenario_value: 1.0, quote_count: 10 },
                { __factor_group__: "missing", optimal_scenario_value: 0.8, quote_count: 10 },
                { __factor_group__: "4-5", optimal_scenario_value: 1.05, quote_count: 10 },
              ],
              proposer_age_band: [
                { __factor_group__: "28-34", optimal_scenario_value: 0.95, quote_count: 10 },
                { __factor_group__: "20-27", optimal_scenario_value: 1.1, quote_count: 10 },
                { __factor_group__: "missing", optimal_scenario_value: 0.85, quote_count: 10 },
              ],
            },
          }),
        }),
      })

      fireEvent.click(screen.getByText("Rates"))

      fireEvent.click(screen.getByRole("button", { name: "vehicle_age_band" }))
      const levels = screen.getAllByTestId("relativity-row").map(row => row.getAttribute("data-key"))
      expect(levels).toEqual(["1-3", "4-5", "10-11", "missing"])
    })

    it("keeps factor tables out of Summary once the Rates tab exists", () => {
      renderPreview({
        data: makeData({
          result: makeSolveResult({
            mode: "ratebook",
            factor_tables: {
              age_band: [
                { __factor_group__: "17-24", optimal_scenario_value: 0.875, quote_count: 10 },
              ],
            },
          }),
        }),
      })

      fireEvent.click(screen.getByText("Summary"))

      expect(screen.queryByText("Factor Tables")).not.toBeInTheDocument()
      // A level appears on Summary only in the beeswarm's own values table.
      const levelCells = screen.queryAllByText("17-24")
      expect(levelCells).toHaveLength(1)
      expect(levelCells[0].closest("table")).toHaveAttribute("aria-label", "Mechanical price effect values")
    })

    // The beeswarm's own behaviour is pinned in optimiser/__tests__/RatebookImpactBeeswarm.test.tsx.
    it("shows a ratebook mechanical price effect beeswarm on Summary", () => {
      renderPreview({
        data: makeData({
          result: makeSolveResult({
            mode: "ratebook",
            factor_tables: {
              region: [
                { __factor_group__: "North", optimal_scenario_value: 1.05, quote_count: 10 },
                { __factor_group__: "South", optimal_scenario_value: 0.98, quote_count: 10 },
              ],
            },
          }),
        }),
      })

      fireEvent.click(screen.getByText("Summary"))

      expect(screen.getByTestId("ratebook-impact-beeswarm")).toBeInTheDocument()
      expect(screen.getByLabelText("region North: +5.0%")).toBeInTheDocument()
    })

    it("does not show the mechanical price effect chart for online results", () => {
      renderPreview({
        data: makeData({
          result: makeSolveResult({
            mode: "online",
            factor_tables: {
              age_band: [
                { __factor_group__: "17-24", optimal_scenario_value: 0.75, quote_count: 10 },
              ],
            },
          }),
        }),
      })

      fireEvent.click(screen.getByText("Summary"))

      expect(screen.queryByText("Mechanical Price Effect")).not.toBeInTheDocument()
    })

    it("hides the Rates tab when a result has no ratebook rates", () => {
      renderPreview({
        data: makeData({
          result: makeSolveResult({
            mode: "online",
            factor_tables: {
              age_band: [
                { __factor_group__: "17-24", optimal_scenario_value: 0.875, quote_count: 10 },
              ],
            },
          }),
        }),
      })

      expect(screen.queryByText("Rates")).not.toBeInTheDocument()
      expect(screen.queryByText("Mechanical Price Effect")).not.toBeInTheDocument()
    })

    it("hides the Rates tab for ratebook results without materialised factor tables", () => {
      renderPreview({
        data: makeData({
          result: makeSolveResult({
            mode: "ratebook",
            factor_tables: {},
          }),
        }),
      })

      expect(screen.queryByText("Rates")).not.toBeInTheDocument()
    })

    it("surfaces a selected-point contract violation instead of leaving rates loading", async () => {
      mockStoreUpdateAfterSelect.mockImplementationOnce(() => {
        throw new Error("Selected frontier response has the wrong point index")
      })
      renderPreview({
        data: makeData({
          result: makeSolveResult({
            mode: "ratebook",
            factor_tables: {},
          }),
          frontier: makeFrontier(),
          selectedPointIndex: 0,
        }),
      })

      fireEvent.click(screen.getByText("Rates"))

      expect(await screen.findByText(
        "Rate table load failed: Selected frontier response has the wrong point index",
      )).toBeInTheDocument()
      expect(screen.queryByText("Materialising selected point rates...")).not.toBeInTheDocument()
    })

    it("switches to Convergence tab on click", () => {
      renderPreview()
      fireEvent.click(screen.getByText("Convergence"))
      expect(screen.getByRole("img", { name: "Objective by iteration" })).toBeInTheDocument()
    })

    it("offers Convergence for a ratebook result without a trace and says why it is empty", () => {
      renderPreview({
        data: makeData({ result: makeSolveResult({ mode: "ratebook", history: null, ratebook_cd_trace: null }) }),
      })
      fireEvent.click(screen.getByRole("tab", { name: "Convergence" }))
      expect(screen.getByText(
        "The coordinate-descent trace is recorded by live solves only; this result has none.",
      )).toBeInTheDocument()
    })

    it("defaults to Frontier tab when frontier data exists", () => {
      renderPreview({ data: makeData({ frontier: makeFrontier() }) })
      // Chart info text is visible by default
      expect(screen.getByText(/5 frontier points/)).toBeInTheDocument()
    })

    it("keeps frontier point navigation available on the Summary tab", () => {
      renderPreview({
        data: makeData({
          frontier: makeFrontier(),
          selectedPointIndex: 2,
        }),
      })

      fireEvent.click(screen.getByText("Summary"))

      expect(screen.getByText("Point 3 of 5")).toBeInTheDocument()
      fireEvent.click(screen.getByRole("button", { name: "Next frontier point" }))
      expect(mockStoreSelectPoint).toHaveBeenCalledWith("opt_1", 3)
      expect(mockSelectFrontierPointAPI).not.toHaveBeenCalled()
    })
  })

  describe("Frontier tab with data", () => {
    it("renders frontier scatter chart area", () => {
      renderPreview({ data: makeData({ frontier: makeFrontier() }) })
      expect(screen.getByText(/5 frontier points/)).toBeInTheDocument()
    })

    it("communicates when the frontier payload is capped", () => {
      renderPreview({
        data: makeData({
          frontier: makeFrontier(5, {
            n_points: 2001,
            points_limit: 2000,
            points_truncated: true,
          }),
        }),
      })

      expect(screen.getByText(/Showing 5 of 2,001 frontier points/)).toBeInTheDocument()
      expect(screen.getByText(/response cap is 2,000/)).toBeInTheDocument()
    })

    it("hides Frontier tab when frontier has empty points", () => {
      renderPreview({
        data: makeData({
          frontier: {
            points: [],
            point_summaries: [],
            n_points: 0,
            points_returned: 0,
            frontier_generation: 0,
            constraint_names: [],
            swept_axes: [],
            points_limit: 2000,
            points_truncated: false,
          },
        }),
      })
      expect(screen.queryByText("Frontier")).not.toBeInTheDocument()
    })

    it("keeps hook order stable if frontier data disappears while the tab is mounted", () => {
      const { rerender } = renderPreview({ data: makeData({ frontier: makeFrontier() }) })
      expect(screen.getByText(/5 frontier points/)).toBeInTheDocument()

      rerender(<OptimiserPreview data={makeData({ frontier: null })} nodeId="opt_1" allNodes={[]} edges={[]} />)

      expect(screen.queryByText(/No frontier data available/)).not.toBeInTheDocument()
      expect(screen.getByText("Objective")).toBeInTheDocument()
      expect(screen.getByText("Optimised")).toBeInTheDocument()
    })

    it("shows detail card content when a point is selected", () => {
      renderPreview({
        data: makeData({
          frontier: makeFrontier(),
          selectedPointIndex: 2,
        }),
      })
      expect(screen.getByText("Point details")).toBeInTheDocument()
    })

    it("offers no publish actions on the detail card and points to the Export pane", () => {
      renderPreview({ data: makeData({ frontier: makeFrontier(), selectedPointIndex: 0 }) })
      expect(screen.getByText("Point details")).toBeInTheDocument()
      expect(screen.queryByRole("button", { name: /Save/ })).not.toBeInTheDocument()
      expect(screen.queryByRole("button", { name: /Log to MLflow/ })).not.toBeInTheDocument()
      expect(screen.getByText("Save or log this point from the node's Export pane.")).toBeInTheDocument()
    })

    it("clicking a scatter point switches locally without a select API call", () => {
      renderPreview({
        data: makeData({ frontier: makeFrontier() }),
      })
      // The SVG circles are the frontier points; find them and click one
      const circles = document.querySelectorAll("circle[style*='cursor: pointer']")
      expect(circles.length).toBe(5)
      fireEvent.click(circles[2])
      expect(mockStoreSelectPoint).toHaveBeenCalledWith("opt_1", 2)
      expect(mockSelectFrontierPointAPI).not.toHaveBeenCalled()
      expect(mockStoreUpdateAfterSelect).not.toHaveBeenCalled()
    })

    it("clicking the selected scatter point keeps it selected", () => {
      renderPreview({
        data: makeData({
          frontier: makeFrontier(),
          selectedPointIndex: 2,
        }),
      })

      fireEvent.click(screen.getByRole("button", { name: "Select frontier point 3" }))

      expect(mockStoreSelectPoint).not.toHaveBeenCalled()
      expect(mockSelectFrontierPointAPI).not.toHaveBeenCalled()
    })

    it("detail card states the point's attainment against its own bound in text", () => {
      const frontier = makeFrontier()
      renderPreview({
        data: makeData({ frontier, selectedPointIndex: 0, result: pointResult(frontier, 0) }),
      })
      expect(attainmentCells("loss_ratio")).toEqual(["max", "0.58", "0.55", "+0.03 (+5.17%)", "Met", "0.001000"])
    })

    it("detail card does not show baseline comparisons for selected frontier points", () => {
      renderPreview({
        data: makeData({
          frontier: makeFrontier(1, {
            points: [
              makeOnlineFrontierPoint(0, {
                total_objective: 1250000,
                totals: { loss_ratio: 0.72 },
                lambdas: { loss_ratio: 0.012345 },
              }),
            ],
          }),
          selectedPointIndex: 0,
        }),
      })

      expect(screen.queryByText(/vs baseline/i)).not.toBeInTheDocument()
      expect(screen.queryByText(/120\.0%/)).not.toBeInTheDocument()
    })

    it("detail card shows lambda values", () => {
      const frontier = makeFrontier()
      renderPreview({
        data: makeData({ frontier, selectedPointIndex: 0, result: pointResult(frontier, 0) }),
      })
      expect(screen.getByRole("columnheader", { name: /λ \(multiplier\)/ })).toBeInTheDocument()
      expect(screen.queryByText(/shadow price/)).not.toBeInTheDocument()
    })

    it("constraint dropdown appears when multiple constraints exist", () => {
      const frontier: FrontierData = {
        points: Array.from({ length: 3 }, (_, i) => makeOnlineFrontierPoint(i, {
          thresholds: { loss_ratio: 0.6, volume: 90 },
          bounds: { loss_ratio: 0.6, volume: 90 },
          totals: { loss_ratio: 0.55 + i * 0.02, volume: 100 + i * 10 },
          lambdas: { loss_ratio: 0.001, volume: 0 },
        })),
        point_summaries: Array.from({ length: 3 }, (_, i) => makePointSummary({
          total_objective: 1200000 + i * 10000,
          constraints: { loss_ratio: 0.55 + i * 0.02, volume: 100 + i * 10 },
        })),
        n_points: 3,
        points_returned: 3,
        constraint_names: ["loss_ratio", "volume"],
        swept_axes: ["loss_ratio", "volume"],
        points_limit: 2000,
        points_truncated: false,
        frontier_generation: 0,
      }
      renderPreview({
        data: makeData({
          frontier,
          result: makeSolveResult({
            constraints: { loss_ratio: 0.65, volume: 100 },
            effective_bounds: {
              loss_ratio: { kind: "max", bound: 1.05 },
              volume: { kind: "min", bound: 95 },
            },
            lambdas: { loss_ratio: 0.005, volume: 0 },
          }),
          constraints: { loss_ratio: { max: 1.05 }, volume: { min: 95 } },
        }),
      })
      expect(screen.getByText("X axis:")).toBeInTheDocument()
    })

    it("header stepper buttons navigate between points locally without a select API call", () => {
      renderPreview({
        data: makeData({
          frontier: makeFrontier(),
          selectedPointIndex: 2,
        }),
      })
      expect(screen.getByText("Point 3 of 5")).toBeInTheDocument()
      fireEvent.click(screen.getByRole("button", { name: "Next frontier point" }))
      expect(mockStoreSelectPoint).toHaveBeenCalledWith("opt_1", 3)
      expect(mockSelectFrontierPointAPI).not.toHaveBeenCalled()
    })
  })

  describe("frontier slices (OPT-V06)", () => {
    /** A 2×3 sweep: volume (min) at 5, 5.5, 6 against margin (max) at 400 and
     *  450, margin varying fastest, so each slice's global indices interleave. */
    const GRID = [
      // [volume bound, margin bound, objective, volume total, margin total]
      [5, 400, 130, 5.2, 390],
      [5, 450, 140, 5.1, 440],
      [5.5, 400, 120, 5.6, 395],
      [5.5, 450, 128, 5.7, 445],
      [6, 400, 105, 6.1, 398],
      [6, 450, 110, 6.2, 449],
    ]

    function gridFrontier(): FrontierData {
      const points = GRID.map(([volume, margin, objective, volumeTotal, marginTotal], i) => makeOnlineFrontierPoint(i, {
        total_objective: objective,
        thresholds: { volume, margin },
        bounds: { volume, margin },
        totals: { volume: volumeTotal, margin: marginTotal },
        lambdas: { volume: 0.5, margin: 0.01 },
      }))
      return makeOnlineFrontier(6, {
        points,
        point_summaries: points.map((point) => makePointSummary({
          total_objective: point.total_objective,
          constraints: point.totals,
          effective_bounds: {
            volume: { kind: "min", bound: point.bounds.volume },
            margin: { kind: "max", bound: point.bounds.margin },
          },
          lambdas: point.lambdas,
          iterations: point.iterations,
        })),
        constraint_names: ["volume", "margin"],
        swept_axes: ["volume", "margin"],
      })
    }

    /** The as-solved result, solved at volume ≥ 5.5 and margin ≤ `margin`. */
    function gridSolve(margin = 400): OptimiserSolveResult {
      return makeSolveResult({
        total_objective: 121,
        constraints: { volume: 5.6, margin: 396 },
        effective_bounds: {
          volume: { kind: "min", bound: 5.5 },
          margin: { kind: "max", bound: margin },
        },
        lambdas: { volume: 0.5, margin: 0.01 },
      })
    }

    function gridData(overrides: Partial<OptimiserPreviewData> = {}): OptimiserPreviewData {
      const frontier = gridFrontier()
      const index = overrides.selectedPointIndex ?? null
      const solved = overrides.solvedResult ?? gridSolve()
      return makeData({
        frontier,
        solvedResult: solved,
        result: index == null ? solved : pointResult(frontier, index, solved),
        constraints: { volume: { min: 5.5 }, margin: { max: 400 } },
        ...overrides,
      })
    }

    function shownPoints(): (string | null)[] {
      return screen.getAllByRole("button", { name: /^Select frontier point/ })
        .map((button) => button.getAttribute("aria-label"))
    }

    it("shows point 1's slice by default, one line in bound order, and names the slice", () => {
      renderPreview({ data: gridData() })

      expect(shownPoints()).toEqual([
        "Select frontier point 1",
        "Select frontier point 3",
        "Select frontier point 5",
      ])
      const holding = screen.getByLabelText("Holding margin at")
      expect(within(holding).getAllByRole("option").map((option) => option.textContent)).toEqual(["400", "450"])
      expect(screen.getByText(/This slice holds 3 of the 6 frontier points\./)).toBeInTheDocument()
    })

    it("offers only the swept constraints on the X axis and re-slices when it changes", () => {
      renderPreview({ data: gridData() })

      fireEvent.change(screen.getByLabelText("X axis:"), { target: { value: "1" } })

      expect(shownPoints()).toEqual(["Select frontier point 1", "Select frontier point 2"])
      expect(screen.getByLabelText("Holding volume at")).toBeInTheDocument()
    })

    it("picks another slice from the Holding select", () => {
      renderPreview({ data: gridData() })

      fireEvent.change(screen.getByLabelText("Holding margin at"), { target: { value: "1" } })

      expect(shownPoints()).toEqual([
        "Select frontier point 2",
        "Select frontier point 4",
        "Select frontier point 6",
      ])
    })

    it("selects the global point index from a slice, never its place in the slice", () => {
      renderPreview({ data: gridData() })
      fireEvent.change(screen.getByLabelText("Holding margin at"), { target: { value: "1" } })

      // Point 4 is the second of its slice: global index 3, slice-local 1.
      fireEvent.click(screen.getByRole("button", { name: "Select frontier point 4" }))

      expect(mockStoreSelectPoint).toHaveBeenCalledWith("opt_1", 3)
      expect(mockSelectFrontierPointAPI).not.toHaveBeenCalled()
    })

    it("switches to the slice of a point selected from outside the displayed slice", () => {
      const { rerender, props } = renderPreview({ data: gridData() })
      expect(shownPoints()).toContain("Select frontier point 1")

      // Summary or the stepper selects point 4, which lies in the other slice.
      rerender(<OptimiserPreview {...props} data={gridData({ selectedPointIndex: 3 })} />)

      expect(shownPoints()).toEqual([
        "Select frontier point 2",
        "Select frontier point 4",
        "Select frontier point 6",
      ])
      expect(screen.getByLabelText("Holding margin at")).toHaveValue("1")
    })

    it("keeps a slice the user picks while the selection stays put", () => {
      renderPreview({ data: gridData({ selectedPointIndex: 3 }) })

      fireEvent.change(screen.getByLabelText("Holding margin at"), { target: { value: "0" } })

      expect(shownPoints()).toEqual([
        "Select frontier point 1",
        "Select frontier point 3",
        "Select frontier point 5",
      ])
    })

    it("steps within the selected point's slice and stops at its ends", () => {
      const { rerender, props } = renderPreview({ data: gridData({ selectedPointIndex: 2 }) })

      expect(screen.getByText("Point 3 of 6")).toBeInTheDocument()
      fireEvent.click(screen.getByRole("button", { name: "Next frontier point" }))
      expect(mockStoreSelectPoint).toHaveBeenLastCalledWith("opt_1", 4)
      fireEvent.click(screen.getByRole("button", { name: "Previous frontier point" }))
      expect(mockStoreSelectPoint).toHaveBeenLastCalledWith("opt_1", 0)

      rerender(<OptimiserPreview {...props} data={gridData({ selectedPointIndex: 4 })} />)
      // Point 6 (index 5) follows globally but lies in the other slice.
      expect(screen.getByRole("button", { name: "Next frontier point" })).toBeDisabled()
    })

    it("always draws the as-solved anchor, hollow with a note when it lies off the slice", () => {
      renderPreview({ data: gridData() })
      const marker = () => screen.getByTestId("frontier-as-solved-marker")
      // Solved at margin ≤ 400: the default slice's held bound.
      expect(marker()).toHaveAttribute("data-on-slice", "true")
      expect(screen.getByText("As solved")).toBeInTheDocument()

      fireEvent.change(screen.getByLabelText("Holding margin at"), { target: { value: "1" } })

      expect(marker()).toHaveAttribute("data-on-slice", "false")
      expect(screen.getByText("As solved (different slice)")).toBeInTheDocument()
    })

    it("names the y axis by the objective column the result was solved for", () => {
      renderPreview({
        data: gridData(),
        allNodes: [optimiserNode({ objective: "expected_income" })],
      })
      expect(screen.getByRole("group", { name: "Efficient frontier: expected_income against volume" }))
        .toBeInTheDocument()
    })

    it("names the y axis Objective when the config no longer matches the result", () => {
      mockSolveState.results = { opt_1: { configHash: "an-older-config", source: "live", structuralVersion: 0 } }
      renderPreview({
        data: gridData(),
        allNodes: [optimiserNode({ objective: "expected_income" })],
      })
      expect(screen.getByRole("group", { name: "Efficient frontier: Objective against volume" }))
        .toBeInTheDocument()
    })

    it("shows the selected point's trade-off to its slice neighbour in the detail card", () => {
      renderPreview({ data: gridData({ selectedPointIndex: 2 }) })
      const term = screen.getByText(
        "Objective change per unit of volume bound relaxed, to the next point in this slice",
        { selector: "dt" },
      )
      // (130 − 120) / (5.5 − 5) to point 1, in point 3's slice.
      expect(term.nextElementSibling).toHaveTextContent("+20 (to point 1)")
    })

    it("lists the slice's points in a values table", () => {
      renderPreview({ data: gridData() })
      fireEvent.click(screen.getByText("View slice values"))
      const table = screen.getByRole("table", { name: "Frontier slice values" })
      const rows = within(table).getAllByRole("row").slice(1)
      expect(rows.map((row) => within(row).getByRole("rowheader").textContent)).toEqual([
        "Point 1",
        "Point 3",
        "Point 5",
      ])
      expect(within(rows[1]).getAllByRole("cell").map((cell) => cell.textContent)).toEqual([
        "5.5",
        "5.6",
        "120",
        "Yes",
        "12",
        "Feasible",
      ])
    })
  })

  describe("Convergence tab", () => {
    it("draws the solve's history as small multiples on real axes", () => {
      renderPreview()
      fireEvent.click(screen.getByText("Convergence"))
      for (const name of [
        "Objective by iteration",
        "Largest λ change by iteration",
        "loss_ratio total by iteration",
        "λ by iteration",
      ]) {
        expect(screen.getByRole("img", { name })).toBeInTheDocument()
      }
    })

    it("lists each iteration, and whether it met every constraint, in the values table", () => {
      renderPreview()
      fireEvent.click(screen.getByText("Convergence"))
      const table = screen.getByRole("table", { name: "Iteration values" })
      const met = within(table).getAllByRole("row").slice(1).map((row) => within(row).getAllByRole("cell").at(-1)?.textContent)
      expect(met).toEqual(["No", "Yes"])
    })
  })

  describe("Quotes tab", () => {
    it("offers Quotes for online results and no Export tab", () => {
      renderPreview()
      expect(screen.getByRole("tab", { name: "Quotes" })).toBeInTheDocument()
      expect(screen.queryByRole("tab", { name: "Export" })).not.toBeInTheDocument()
    })

    it("has no Quotes tab for ratebook results", () => {
      renderPreview({ data: makeData({ result: makeSolveResult({ mode: "ratebook" }) }) })
      expect(screen.queryByRole("tab", { name: "Quotes" })).not.toBeInTheDocument()
    })
  })

  describe("selected-point integrity", () => {
    /** The preview's data after the stepper selected `index`, as the store builds it. */
    function selectedData(frontier: FrontierData, index: number, overrides: Partial<OptimiserPreviewData> = {}) {
      return makeData({
        frontier,
        selectedPointIndex: index,
        result: pointResult(frontier, index),
        solvedResult: makeSolveResult(),
        ...overrides,
      })
    }

    function adjustmentRequests() {
      return mockSelectFrontierPointAPI.mock.calls.filter(([payload]) => payload.include_adjustments)
    }

    it("summarises the as-solved adjustments and opens the Adjustments tab from Summary", () => {
      renderPreview()

      const summary = screen.getByRole("group", { name: "Adjustments" })
      expect(within(summary).getByText("Adjusted up").nextSibling).toHaveTextContent("42.0%")
      fireEvent.click(within(summary).getByRole("button", { name: "View adjustments" }))

      expect(screen.getByRole("tab", { name: "Adjustments" })).toHaveAttribute("aria-selected", "true")
      expect(screen.getByRole("img", { name: "Chosen scenario values histogram" })).toBeInTheDocument()
      expect(screen.getByText("As solved: 50,000 quotes")).toBeInTheDocument()
      expect(adjustmentRequests()).toHaveLength(0)
    })

    it("loads a selected point's adjustments only while the Adjustments tab is open", async () => {
      const frontier = makeFrontier()
      mockSelectFrontierPointAPI.mockResolvedValue(makeFrontierSelect({
        point_index: 2,
        adjustments: makeAdjustmentReport({ n_quotes: 50000 }),
      }))
      renderPreview({ data: selectedData(frontier, 2) })
      fireEvent.click(screen.getByRole("tab", { name: "Summary" }))

      expect(screen.getByRole("group", { name: "Adjustments" }))
        .toHaveTextContent("Frontier point 3's adjustments load in the Adjustments tab.")
      expect(adjustmentRequests()).toHaveLength(0)

      fireEvent.click(screen.getByRole("tab", { name: "Adjustments" }))
      expect(await screen.findByText("Frontier point 3: 50,000 quotes")).toBeInTheDocument()
      expect(adjustmentRequests()).toEqual([
        [{ job_id: "job_123", point_index: 2, include_adjustments: true }, expect.anything()],
      ])

      // The loaded report belongs to the review: reopening the tab asks again for nothing.
      fireEvent.click(screen.getByRole("tab", { name: "Summary" }))
      fireEvent.click(screen.getByRole("tab", { name: "Adjustments" }))
      expect(screen.getByText("Frontier point 3: 50,000 quotes")).toBeInTheDocument()
      expect(adjustmentRequests()).toHaveLength(1)
    })

    it("offers Adjustments for a ratebook result, summarised on Summary", () => {
      const ratebook = makeRatebookSolveResult()
      renderPreview({ data: makeData({ result: ratebook, solvedResult: ratebook }) })

      const summary = screen.getByRole("group", { name: "Adjustments" })
      expect(within(summary).getByText("Deployed ≠ evaluated step").nextSibling)
        .toHaveTextContent("18 quotes")
      fireEvent.click(within(summary).getByRole("button", { name: "View adjustments" }))

      expect(screen.getByRole("tab", { name: "Adjustments" })).toHaveAttribute("aria-selected", "true")
      expect(screen.getByText("As solved: 200 quotes")).toBeInTheDocument()
      expect(within(screen.getByRole("group", { name: "Deployed factor" }))
        .getByText("Deployed factor differs from evaluated step").nextSibling)
        .toHaveTextContent("18 quotes (9.0%)")
    })

    it("keeps Convergence for a selected point and says whose history it shows", () => {
      const frontier = makeFrontier()
      renderPreview({ data: selectedData(frontier, 1) })

      fireEvent.click(screen.getByRole("tab", { name: "Convergence" }))

      expect(screen.getByText(
        "History is recorded for the solved result; frontier point 2: converged, 11 iterations",
      )).toBeInTheDocument()
      // The solve's two recorded iterations.
      expect(within(screen.getByRole("table", { name: "Iteration values" })).getAllByRole("row")).toHaveLength(3)
    })

    it("says when the selected point did not converge", () => {
      const frontier = makeFrontier()
      frontier.point_summaries[1] = { ...frontier.point_summaries[1], converged: false, iterations: 50 }
      renderPreview({ data: selectedData(frontier, 1) })

      fireEvent.click(screen.getByRole("tab", { name: "Convergence" }))

      expect(screen.getByText(
        "History is recorded for the solved result; frontier point 2: not converged, 50 iterations",
      )).toBeInTheDocument()
    })


    it("keeps the tab when the stepper selects another point", () => {
      const frontier = makeFrontier()
      const { rerender, props } = renderPreview({ data: selectedData(frontier, 1) })
      fireEvent.click(screen.getByRole("tab", { name: "Convergence" }))

      rerender(<OptimiserPreview {...props} data={selectedData(frontier, 2)} />)

      expect(screen.getByRole("tab", { name: "Convergence" })).toHaveAttribute("aria-selected", "true")
      expect(screen.getByText(/frontier point 3: converged, 12 iterations/)).toBeInTheDocument()
    })

    it("returns to the default tab for a new solve job", () => {
      const frontier = makeFrontier()
      const { rerender, props } = renderPreview({ data: selectedData(frontier, 1) })
      fireEvent.click(screen.getByRole("tab", { name: "Convergence" }))

      rerender(<OptimiserPreview {...props} data={selectedData(frontier, 0, { jobId: "job_456" })} />)

      expect(screen.getByRole("tab", { name: "Frontier" })).toHaveAttribute("aria-selected", "true")
    })

    it("returns to the default tab for another optimiser node", () => {
      const { rerender, props } = renderPreview()
      fireEvent.click(screen.getByRole("tab", { name: "Convergence" }))

      rerender(<OptimiserPreview {...props} nodeId="opt_2" />)

      expect(screen.getByRole("tab", { name: "Summary" })).toHaveAttribute("aria-selected", "true")
    })

    it("keeps the as-solved marker where the solve is when a point is selected", () => {
      const frontier = makeFrontier()
      const { rerender, props } = renderPreview({ data: selectedData(frontier, 0) })
      const marker = () => screen.getByTestId("frontier-as-solved-marker").querySelector("circle")!
      const before = [marker().getAttribute("cx"), marker().getAttribute("cy")]

      rerender(<OptimiserPreview {...props} data={selectedData(frontier, 3)} />)

      expect([marker().getAttribute("cx"), marker().getAttribute("cy")]).toEqual(before)
    })

    it("shows the displayed result's warning as a strip", () => {
      const frontier = makeFrontier()
      frontier.point_summaries[1] = {
        ...frontier.point_summaries[1],
        converged: false,
        warning: "Solver did not converge. Consider increasing max_iter or relaxing tolerance.",
      }
      renderPreview({ data: selectedData(frontier, 1) })

      expect(screen.getByRole("status", { name: "Result warning" })).toHaveTextContent(
        "Solver did not converge. Consider increasing max_iter or relaxing tolerance.",
      )
    })

    it("shows no warning strip for a result without a warning", () => {
      renderPreview()
      expect(screen.queryByRole("status", { name: "Result warning" })).not.toBeInTheDocument()
    })
  })

  describe("stale result", () => {
    it("says when the config changed since the solve and re-runs from the preview", async () => {
      mockSolveState.results = { opt_1: { configHash: "an-older-config", source: "live", structuralVersion: 0 } }
      mockSolveOptimiser.mockResolvedValue({ status: "started", job_id: "job_999" })
      renderPreview({
        allNodes: [QUOTES_NODE, optimiserNode({ objective: "profit" })],
        edges: [{ id: "e1", source: "quotes", target: "opt_1" }],
      })

      expect(screen.getByText("The configuration has changed since this result was solved.")).toBeInTheDocument()
      fireEvent.click(screen.getByRole("button", { name: "Re-run" }))
      await waitFor(() => expect(mockSolveOptimiser).toHaveBeenCalledWith(expect.objectContaining({ node_id: "opt_1" })))
      await waitFor(() => expect(mockStartSolveJob).toHaveBeenCalledWith(
        "opt_1", "job_999", "My Optimiser", {}, expect.any(String), "live", expect.any(Number),
      ))
    })

    it("keeps Re-run disabled with the reason while the Solve pane would refuse", () => {
      mockSolveState.results = { opt_1: { configHash: "an-older-config", source: "live", structuralVersion: 0 } }
      renderPreview({
        allNodes: [
          QUOTES_NODE,
          optimiserNode({
            objective: "profit",
            constraints: { volume: { min: 1 } },
            frontier_enabled: true,
            frontier_steps: 10_001,
            frontier_ranges: { volume: { min: 0, max: 2 } },
          }),
        ],
        edges: [{ id: "e1", source: "quotes", target: "opt_1" }],
      })

      expect(screen.getByRole("button", { name: "Re-run" })).toBeDisabled()
      expect(screen.getByText(/It cannot be re-run yet: The frontier would run 10,001 solves/)).toBeInTheDocument()
      fireEvent.click(screen.getByRole("button", { name: "Re-run" }))
      expect(mockSolveOptimiser).not.toHaveBeenCalled()
    })

    it("judges Re-run against the cached input columns the editor uses", async () => {
      const { default: useGraphStore } = await import("../../stores/useGraphStore")
      mockSolveState.results = { opt_1: { configHash: "an-older-config", source: "live", structuralVersion: 0 } }
      mockSolveState.columnCache = {
        "opt_1:live": {
          columns: [{ name: "profit", dtype: "Float64" }, { name: "scenario_index", dtype: "Int64" }, { name: "scenario_value", dtype: "Float64" }],
          structuralVersion: useGraphStore.getState().structuralVersion,
        },
      }
      const unknownColumnsInput = { ...QUOTES_NODE, data: { ...QUOTES_NODE.data, _columns: undefined } }
      renderPreview({
        allNodes: [unknownColumnsInput, optimiserNode({ objective: "profit" })],
        edges: [{ id: "e1", source: "quotes", target: "opt_1" }],
      })

      expect(screen.getByRole("button", { name: "Re-run" })).toBeDisabled()
      expect(screen.getByText(/Quote ID uses "quote_id", which the input does not have/)).toBeInTheDocument()
    })

    it("shows no strip while the result matches the config", () => {
      renderPreview()
      expect(screen.queryByText("The configuration has changed since this result was solved.")).not.toBeInTheDocument()
    })
  })

  describe("Summary tab constraint status", () => {
    it("says a met constraint is Met in text", () => {
      const data = makeData({
        result: makeSolveResult({
          constraints: { loss_ratio: 0.60 },
          baseline_constraints: { loss_ratio: 0.60 },
        }),
      })
      renderPreview({ data })
      fireEvent.click(screen.getByText("Summary"))
      expect(attainmentCells("loss_ratio")[4]).toBe("Met")
    })

    it("says a breached constraint is Breached, with its signed slack", () => {
      const data = makeData({
        result: makeSolveResult({
          constraints: { loss_ratio: 999 },
          baseline_constraints: { loss_ratio: 1 },
        }),
      })
      renderPreview({ data })
      fireEvent.click(screen.getByText("Summary"))
      const cells = attainmentCells("loss_ratio")
      expect(cells[4]).toBe("Breached")
      expect(cells[3]).toBe("-997.95 (-95,042.86%)")
    })
  })

  describe("constraint attainment across panes (G03)", () => {
    it("prints the selected point's own bound and status on Summary and the detail card alike", () => {
      const frontier = makeFrontier()
      renderPreview({
        data: makeData({ frontier, selectedPointIndex: 4, result: pointResult(frontier, 4) }),
      })

      const detail = attainmentCells("loss_ratio")
      fireEvent.click(screen.getByText("Summary"))
      const summary = attainmentCells("loss_ratio")

      expect(summary).toEqual(detail)
      // The point's swept 0.62 breaches; the configured and as-solved 1.05 would not.
      expect(summary.slice(0, 3)).toEqual(["max", "0.62", "0.63"])
      expect(summary[4]).toBe("Breached")
    })
  })

  describe("Summary tab lambda values", () => {
    it("renders lambda values with 6 decimal places", () => {
      renderPreview()
      fireEvent.click(screen.getByText("Summary"))
      expect(screen.getByText("0.005000")).toBeInTheDocument()
    })

    it("renders the λ column beside its constraint", () => {
      renderPreview()
      fireEvent.click(screen.getByText("Summary"))
      expect(screen.getByRole("columnheader", { name: /λ \(multiplier\)/ })).toBeInTheDocument()
      expect(attainmentCells("loss_ratio")[5]).toBe("0.005000")
    })
  })

  describe("tab defaults", () => {
    it("defaults to Summary tab when no frontier data", () => {
      renderPreview()
      expect(screen.getByText("Objective")).toBeInTheDocument()
      expect(screen.getByText("Optimised")).toBeInTheDocument()
    })

    it("defaults to Frontier tab when frontier data exists", () => {
      renderPreview({ data: makeData({ frontier: makeFrontier() }) })
      expect(screen.getByText(/5 frontier points/)).toBeInTheDocument()
      expect(screen.queryByText("Optimised")).not.toBeInTheDocument()
    })
  })

  describe("collapse/expand", () => {
    it("collapse button hides the main panel", () => {
      renderPreview()
      fireEvent.click(screen.getByLabelText("Collapse preview panel"))
      expect(screen.getByText("My Optimiser")).toBeInTheDocument()
    })
  })


  describe("ratebook mode", () => {
    it("shows CD iterations for ratebook mode", () => {
      renderPreview({
        data: makeData({ result: makeSolveResult({ mode: "ratebook", cd_iterations: 8 }) }),
      })
      expect(screen.getByText(/8 CD iters/)).toBeInTheDocument()
    })

    it("falls back to solver iterations when ratebook CD iterations are absent", () => {
      renderPreview({
        data: makeData({
          result: makeSolveResult({ mode: "ratebook", cd_iterations: null, iterations: 11 }),
        }),
      })

      expect(screen.getByText(/11 iters/)).toBeInTheDocument()
      expect(screen.queryByText(/\? CD iters/)).not.toBeInTheDocument()
    })

    it("shows λ on Summary for a ratebook result, as the detail card does", () => {
      renderPreview({
        data: makeData({ result: makeSolveResult({ mode: "ratebook" }) }),
      })
      fireEvent.click(screen.getByText("Summary"))
      expect(screen.getByRole("columnheader", { name: /λ \(multiplier\)/ })).toBeInTheDocument()
      expect(attainmentCells("loss_ratio")[5]).toBe("0.005000")
    })

    it("shows clamp rate in ratebook mode", () => {
      renderPreview({
        data: makeData({ result: makeSolveResult({ mode: "ratebook", clamp_rate: 0.05 }) }),
      })
      fireEvent.click(screen.getByText("Summary"))
      expect(screen.getByText("Clamp rate")).toBeInTheDocument()
      expect(screen.getByText("5.0%")).toBeInTheDocument()
    })

    it("renders ratebook rates in the dedicated Rates tab", () => {
      renderPreview({
        data: makeData({
          result: makeSolveResult({
            mode: "ratebook",
            factor_tables: {
              age_band: [
                { __factor_group__: "18-25", optimal_scenario_value: 1.15, quote_count: 10 },
                { __factor_group__: "26-35", optimal_scenario_value: 0.95, quote_count: 10 },
              ],
            },
          }),
        }),
      })
      fireEvent.click(screen.getByText("Rates"))
      expect(screen.getByRole("heading", { name: "age_band" })).toBeInTheDocument()
      expect(screen.getAllByText("18-25").length).toBeGreaterThan(0)
      expect(screen.getAllByText("1.1500").length).toBeGreaterThan(0)
    })
  })
})
