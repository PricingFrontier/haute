import { describe, it, expect, vi, afterEach, beforeEach } from "vitest"
import { render, screen, fireEvent, cleanup, waitFor } from "@testing-library/react"
import OptimiserPreview from "../OptimiserPreview"
import type { OptimiserPreviewData, SolveResult, FrontierData } from "../OptimiserPreview"

// ── Mocks ────────────────────────────────────────────────────────

const mockSelectFrontierPointAPI = vi.fn()
const mockSaveOptimiser = vi.fn()
const mockLogOptimiserToMlflow = vi.fn()
const mockApplyOptimiser = vi.fn()

vi.mock("../../api/client", () => ({
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
  }),
}))

const mockStoreSelectPoint = vi.fn()
const mockStoreUpdateAfterSelect = vi.fn()

vi.mock("../../stores/useNodeResultsStore", () => ({
  default: (selector: (s: Record<string, unknown>) => unknown) =>
    selector({
      getOptimiserPreview: () => null,
      selectFrontierPoint: mockStoreSelectPoint,
      updateFrontierAfterSelect: mockStoreUpdateAfterSelect,
    }),
}))

vi.mock("../../stores/useSettingsStore", () => ({
  default: (selector: (s: Record<string, unknown>) => unknown) =>
    selector({
      mlflow: { status: "connected", backend: "local", host: "" },
    }),
}))

// ── Helpers ──────────────────────────────────────────────────────

function makeSolveResult(overrides: Partial<SolveResult> = {}): SolveResult {
  return {
    total_objective: 1234567,
    baseline_objective: 1200000,
    constraints: { loss_ratio: 0.65 },
    baseline_constraints: { loss_ratio: 0.60 },
    lambdas: { loss_ratio: 0.005 },
    converged: true,
    iterations: 15,
    n_quotes: 50000,
    history: [
      { iteration: 1, total_objective: 1100000, max_lambda_change: 0.1, all_constraints_satisfied: false },
      { iteration: 2, total_objective: 1200000, max_lambda_change: 0.01, all_constraints_satisfied: true },
    ],
    ...overrides,
  }
}

function makeFrontier(n = 5, overrides: Partial<FrontierData> = {}): FrontierData {
  const points = Array.from({ length: n }, (_, i) => ({
    total_objective: 1200000 + i * 10000,
    total_loss_ratio: 0.55 + i * 0.02,
    lambda_loss_ratio: 0.001 + i * 0.001,
    converged: true,
  }))
  return {
    points,
    n_points: n,
    points_returned: n,
    constraint_names: ["loss_ratio"],
    points_limit: 2000,
    points_truncated: false,
    ...overrides,
  }
}

function makeData(overrides: Partial<OptimiserPreviewData> = {}): OptimiserPreviewData {
  return {
    result: makeSolveResult(),
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
    ...overrides,
  }
  return { ...render(<OptimiserPreview {...props} />), props }
}

// ── Tests ────────────────────────────────────────────────────────

describe("OptimiserPreview", () => {
  afterEach(cleanup)

  beforeEach(() => {
    vi.clearAllMocks()
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
    mockSaveOptimiser.mockResolvedValue({ status: "ok", path: "output/optimiser_my_optimiser.json", message: "" })
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
      expect(screen.getByText(/50,000 quotes/)).toBeInTheDocument()
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
      expect(screen.getByText("Lambdas")).toBeInTheDocument()
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

      rerender(<OptimiserPreview data={makeData({ frontier: null })} nodeId="opt_1" />)

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
                { __factor_group__: "17-24", optimal_scenario_value: 0.875 },
                { __factor_group__: "25-39", optimal_scenario_value: 1.125 },
              ],
              region: [
                { __factor_group__: "North", optimal_scenario_value: 1.05 },
              ],
            },
          }),
        }),
      })

      fireEvent.click(screen.getByText("Rates"))

      expect(screen.getByText("age_band")).toBeInTheDocument()
      expect(screen.getByText("17-24")).toBeInTheDocument()
      expect(screen.getAllByText("0.8750").length).toBeGreaterThan(0)
      expect(screen.getByText("25-39")).toBeInTheDocument()
      expect(screen.getAllByText("1.1250").length).toBeGreaterThan(0)
      expect(screen.queryByText("North")).not.toBeInTheDocument()

      fireEvent.change(screen.getByLabelText("Rate factor"), { target: { value: "region" } })

      expect(screen.getByText("region")).toBeInTheDocument()
      expect(screen.getByText("North")).toBeInTheDocument()
      expect(screen.getAllByText("1.0500").length).toBeGreaterThan(0)
      expect(screen.queryByText("17-24")).not.toBeInTheDocument()
    })

    it("keeps factor tables out of Summary once the Rates tab exists", () => {
      renderPreview({
        data: makeData({
          result: makeSolveResult({
            mode: "ratebook",
            factor_tables: {
              age_band: [
                { __factor_group__: "17-24", optimal_scenario_value: 0.875 },
              ],
            },
          }),
        }),
      })

      fireEvent.click(screen.getByText("Summary"))

      expect(screen.queryByText("Factor Tables")).not.toBeInTheDocument()
      expect(screen.queryByText("17-24")).not.toBeInTheDocument()
    })

    it("shows a ratebook mechanical price effect beeswarm on Summary", () => {
      renderPreview({
        data: makeData({
          result: makeSolveResult({
            mode: "ratebook",
            factor_tables: {
              age_band: [
                { __factor_group__: "17-24", optimal_scenario_value: 0.75 },
                { __factor_group__: "25-39", optimal_scenario_value: 1.40 },
                { __factor_group__: "40-49", optimal_scenario_value: 1.41 },
                { __factor_group__: "50-59", optimal_scenario_value: 1.42 },
                { __factor_group__: "60-69", optimal_scenario_value: 1.43 },
              ],
              region: [
                { __factor_group__: "North", optimal_scenario_value: 1.05 },
                { __factor_group__: "South", optimal_scenario_value: 0.98 },
              ],
            },
          }),
        }),
      })

      fireEvent.click(screen.getByText("Summary"))

      expect(screen.getByText("Mechanical Price Effect")).toBeInTheDocument()
      expect(screen.getByTestId("ratebook-impact-beeswarm")).toBeInTheDocument()
      const factorLabels = screen.getAllByTestId("ratebook-impact-factor")
      expect(factorLabels.map((label) => label.textContent)).toEqual(["age_band", "region"])
      expect(screen.getByLabelText("age_band 17-24: -25.0%")).toBeInTheDocument()
      expect(screen.getByLabelText("age_band 25-39: +40.0%")).toBeInTheDocument()
      expect(screen.getByText("Log rate effect")).toBeInTheDocument()
      expect(screen.getByText("Factor value")).toBeInTheDocument()
      expect(screen.getByText("Low")).toBeInTheDocument()
      expect(screen.getByText("High")).toBeInTheDocument()

      const decreasingDot = screen.getByLabelText("age_band 17-24: -25.0%")
      const increasingDots = [
        screen.getByLabelText("age_band 25-39: +40.0%"),
        screen.getByLabelText("age_band 40-49: +41.0%"),
        screen.getByLabelText("age_band 50-59: +42.0%"),
        screen.getByLabelText("age_band 60-69: +43.0%"),
      ]
      expect(decreasingDot).toHaveAttribute("data-impact-direction", "decreasing")
      expect(increasingDots[0]).toHaveAttribute("data-impact-direction", "increasing")
      expect(decreasingDot).toHaveAttribute("data-factor-value-position", "0.00")
      expect(increasingDots[3]).toHaveAttribute("data-factor-value-position", "1.00")
      expect(decreasingDot).toHaveAttribute(
        "fill",
        "color-mix(in srgb, var(--chart-impact-value-low) 100%, var(--chart-impact-value-high) 0%)",
      )
      expect(increasingDots[3]).toHaveAttribute(
        "fill",
        "color-mix(in srgb, var(--chart-impact-value-low) 0%, var(--chart-impact-value-high) 100%)",
      )
      expect(new Set(increasingDots.map((dot) => dot.getAttribute("cy"))).size).toBeGreaterThan(1)
    })

    it("orders mechanical price effect factors by quote-count weighted impact", () => {
      renderPreview({
        data: makeData({
          result: makeSolveResult({
            mode: "ratebook",
            factor_tables: {
              sparse_extreme: [
                { __factor_group__: "Rare", optimal_scenario_value: 2.50, quote_count: 1 },
                { __factor_group__: "Common", optimal_scenario_value: 1.00, quote_count: 999 },
              ],
              common_moderate: [
                { __factor_group__: "Low", optimal_scenario_value: 0.90, quote_count: 500 },
                { __factor_group__: "High", optimal_scenario_value: 1.10, quote_count: 500 },
              ],
            },
          }),
        }),
      })

      fireEvent.click(screen.getByText("Summary"))

      const factorLabels = screen.getAllByTestId("ratebook-impact-factor")
      expect(factorLabels.map((label) => label.textContent)).toEqual([
        "common_moderate",
        "sparse_extreme",
      ])
    })

    it("colours dash-separated numeric bands across unicode dash variants", () => {
      renderPreview({
        data: makeData({
          result: makeSolveResult({
            mode: "ratebook",
            factor_tables: {
              age_band: [
                { __factor_group__: "18–19", optimal_scenario_value: 0.95, quote_count: 10 },
                { __factor_group__: "20—29", optimal_scenario_value: 1.00, quote_count: 10 },
                { __factor_group__: "30 − 39", optimal_scenario_value: 1.05, quote_count: 10 },
                { __factor_group__: "40 - 49", optimal_scenario_value: 1.10, quote_count: 10 },
              ],
            },
          }),
        }),
      })

      fireEvent.click(screen.getByText("Summary"))

      expect(screen.getByLabelText("age_band 18–19: -5.0%")).toHaveAttribute(
        "data-factor-value-position",
        "0.00",
      )
      expect(screen.getByLabelText("age_band 20—29: 0.0%")).not.toHaveAttribute(
        "data-factor-value-position",
        "unknown",
      )
      expect(screen.getByLabelText("age_band 30 − 39: +5.0%")).not.toHaveAttribute(
        "data-factor-value-position",
        "unknown",
      )
      expect(screen.getByLabelText("age_band 40 - 49: +10.0%")).toHaveAttribute(
        "data-factor-value-position",
        "1.00",
      )
    })

    it("colours ratebook impact dots by factor value rather than impact direction", () => {
      renderPreview({
        data: makeData({
          result: makeSolveResult({
            mode: "ratebook",
            factor_tables: {
              net_premium: [
                {
                  __factor_group__: "100",
                  net_premium: 100,
                  optimal_scenario_value: 1.25,
                },
                {
                  __factor_group__: "500",
                  net_premium: 500,
                  optimal_scenario_value: 0.80,
                },
              ],
              region: [
                { __factor_group__: "North", optimal_scenario_value: 1.05 },
              ],
            },
          }),
        }),
      })

      fireEvent.click(screen.getByText("Summary"))

      const lowValueIncreasingDot = screen.getByLabelText("net_premium 100: +25.0%")
      const highValueDecreasingDot = screen.getByLabelText("net_premium 500: -20.0%")
      const unorderedCategoryDot = screen.getByLabelText("region North: +5.0%")

      expect(lowValueIncreasingDot).toHaveAttribute("data-impact-direction", "increasing")
      expect(lowValueIncreasingDot).toHaveAttribute("data-factor-value-position", "0.00")
      expect(lowValueIncreasingDot).toHaveAttribute(
        "fill",
        "color-mix(in srgb, var(--chart-impact-value-low) 100%, var(--chart-impact-value-high) 0%)",
      )
      expect(highValueDecreasingDot).toHaveAttribute("data-impact-direction", "decreasing")
      expect(highValueDecreasingDot).toHaveAttribute("data-factor-value-position", "1.00")
      expect(highValueDecreasingDot).toHaveAttribute(
        "fill",
        "color-mix(in srgb, var(--chart-impact-value-low) 0%, var(--chart-impact-value-high) 100%)",
      )
      expect(unorderedCategoryDot).toHaveAttribute("data-factor-value-position", "unknown")
      expect(unorderedCategoryDot).toHaveAttribute("fill", "var(--chart-impact-value-neutral)")
    })

    it("does not show the mechanical price effect chart for online results", () => {
      renderPreview({
        data: makeData({
          result: makeSolveResult({
            mode: "online",
            factor_tables: {
              age_band: [
                { __factor_group__: "17-24", optimal_scenario_value: 0.75 },
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
                { __factor_group__: "17-24", optimal_scenario_value: 0.875 },
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
            factor_tables: undefined,
          }),
        }),
      })

      expect(screen.queryByText("Rates")).not.toBeInTheDocument()
    })

    it("switches to Convergence tab on click", () => {
      renderPreview()
      fireEvent.click(screen.getByText("Convergence"))
      expect(screen.getByText("Iterations")).toBeInTheDocument()
    })

    it("hides Convergence tab when no history data", () => {
      renderPreview({ data: makeData({ result: makeSolveResult({ history: null }) }) })
      expect(screen.queryByText("Convergence")).not.toBeInTheDocument()
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
            n_points: 0,
            points_returned: 0,
            constraint_names: [],
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

      rerender(<OptimiserPreview data={makeData({ frontier: null })} nodeId="opt_1" />)

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

    it("detail card shows Save Result button", () => {
      renderPreview({
        data: makeData({
          frontier: makeFrontier(),
          selectedPointIndex: 0,
        }),
      })
      expect(screen.getByText("Save Result")).toBeInTheDocument()
    })

    it("detail card shows Log to MLflow button when MLflow is available", () => {
      renderPreview({
        data: makeData({
          frontier: makeFrontier(),
          selectedPointIndex: 0,
        }),
      })
      expect(screen.getByText("Log to MLflow")).toBeInTheDocument()
    })

    it("Save passes the selected point index directly without selecting it first", async () => {
      renderPreview({
        data: makeData({
          frontier: makeFrontier(),
          selectedPointIndex: 0,
        }),
      })

      fireEvent.click(screen.getByText("Save Result"))

      await waitFor(() => {
        expect(mockSaveOptimiser).toHaveBeenCalledWith({
          job_id: "job_123",
          output_path: "output/optimiser_my_optimiser.json",
          point_index: 0,
        })
      })
      expect(mockSelectFrontierPointAPI).not.toHaveBeenCalled()
    })

    it("Log to MLflow passes the selected point index directly without selecting it first", async () => {
      renderPreview({
        data: makeData({
          frontier: makeFrontier(),
          selectedPointIndex: 0,
        }),
      })

      fireEvent.click(screen.getByText("Log to MLflow"))

      await waitFor(() => {
        expect(mockLogOptimiserToMlflow).toHaveBeenCalledWith({
          job_id: "job_123",
          point_index: 0,
        })
      })
      expect(mockSelectFrontierPointAPI).not.toHaveBeenCalled()
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

    it("clicking the selected scatter point deselects locally without a select API call", () => {
      renderPreview({
        data: makeData({
          frontier: makeFrontier(),
          selectedPointIndex: 2,
        }),
      })

      const selectedPoint = screen.getByRole("button", { name: "Select frontier point 3" })
      fireEvent.click(selectedPoint)

      expect(mockStoreSelectPoint).toHaveBeenCalledWith("opt_1", null)
      expect(mockSelectFrontierPointAPI).not.toHaveBeenCalled()
    })

    it("detail card shows constraint values with met/unmet indicators", () => {
      renderPreview({
        data: makeData({
          frontier: makeFrontier(),
          selectedPointIndex: 0,
        }),
      })
      // The constraint name should appear in the detail card
      expect(screen.getByText("Constraints")).toBeInTheDocument()
    })

    it("detail card does not show baseline comparisons for selected frontier points", () => {
      renderPreview({
        data: makeData({
          frontier: makeFrontier(1, {
            points: [
              {
                total_objective: 1250000,
                total_loss_ratio: 0.72,
                lambda_loss_ratio: 0.012345,
              },
            ],
          }),
          selectedPointIndex: 0,
        }),
      })

      expect(screen.queryByText(/vs baseline/i)).not.toBeInTheDocument()
      expect(screen.queryByText(/120\.0%/)).not.toBeInTheDocument()
    })

    it("detail card shows lambda values", () => {
      renderPreview({
        data: makeData({
          frontier: makeFrontier(),
          selectedPointIndex: 0,
        }),
      })
      expect(screen.getByText("Lambdas")).toBeInTheDocument()
    })

    it("detail card reads nested constraint and lambda maps from frontier rows", () => {
      renderPreview({
        data: makeData({
          frontier: makeFrontier(1, {
            points: [
              {
                total_objective: 1250000,
                constraints: { loss_ratio: 0.72 },
                lambdas: { loss_ratio: 0.012345 },
              },
            ],
          }),
          selectedPointIndex: 0,
        }),
      })

      expect(screen.getByText("0.7200")).toBeInTheDocument()
      expect(screen.getByText("0.012345")).toBeInTheDocument()
    })

    it("constraint dropdown appears when multiple constraints exist", () => {
      const frontier: FrontierData = {
        points: Array.from({ length: 3 }, (_, i) => ({
          total_objective: 1200000 + i * 10000,
          total_loss_ratio: 0.55 + i * 0.02,
          total_volume: 100 + i * 10,
        })),
        n_points: 3,
        points_returned: 3,
        constraint_names: ["loss_ratio", "volume"],
        points_limit: 2000,
        points_truncated: false,
      }
      renderPreview({
        data: makeData({
          frontier,
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

  describe("Convergence tab", () => {
    it("renders convergence chart and iteration table", () => {
      renderPreview()
      fireEvent.click(screen.getByText("Convergence"))
      expect(screen.getByText("Iterations")).toBeInTheDocument()
      // Check iteration numbers are rendered
      expect(screen.getByText("1")).toBeInTheDocument()
      expect(screen.getByText("2")).toBeInTheDocument()
    })

    it("renders objective and lambda change columns", () => {
      renderPreview()
      fireEvent.click(screen.getByText("Convergence"))
      // "Objective" appears in convergence legend
      expect(screen.getByText("Max dLambda")).toBeInTheDocument()
    })

    it("renders constraints-satisfied column", () => {
      renderPreview()
      fireEvent.click(screen.getByText("Convergence"))
      // First iteration: N, Second: Y
      expect(screen.getByText("N")).toBeInTheDocument()
      expect(screen.getByText("Y")).toBeInTheDocument()
    })
  })

  describe("Export tab", () => {
    it("renders Export tab button", () => {
      renderPreview()
      expect(screen.getByText("Export")).toBeInTheDocument()
    })

    it("switches to Export tab on click and shows save option", () => {
      renderPreview()
      fireEvent.click(screen.getByText("Export"))
      expect(screen.getByText("Save to file")).toBeInTheDocument()
      expect(screen.getByText("Save result")).toBeInTheDocument()
    })

    it("Export tab shows Log to MLflow section when MLflow connected", () => {
      renderPreview()
      fireEvent.click(screen.getByText("Export"))
      const mlflowElements = screen.getAllByText("Log to MLflow")
      expect(mlflowElements.length).toBeGreaterThanOrEqual(2)
    })

    it("shows on-demand result detail loading state", async () => {
      let resolveApply: (value: unknown) => void = () => {}
      mockApplyOptimiser.mockReturnValueOnce(new Promise((resolve) => { resolveApply = resolve }))

      renderPreview()
      fireEvent.click(screen.getByText("Export"))
      fireEvent.click(screen.getByRole("button", { name: /Load detail/i }))

      expect(mockApplyOptimiser).toHaveBeenCalledWith(
        { job_id: "job_123" },
        { signal: expect.any(AbortSignal) },
      )
      expect(screen.getByText("Loading result detail...")).toBeInTheDocument()
      expect(screen.getByRole("button", { name: /Save result/i })).toBeDisabled()
      expect(screen.getByRole("button", { name: /Log to MLflow/i })).toBeDisabled()

      resolveApply({
        status: "ok",
        total_objective: 1250000,
        constraints: { loss_ratio: 0.66 },
        from_artifact: false,
        preview: [{ quote_id: "Q001" }],
        row_count: 1,
        preview_row_count: 1,
        preview_row_limit: 100,
        preview_truncated: false,
        error: null,
      })

      await waitFor(() => {
        expect(screen.queryByText("Loading result detail...")).not.toBeInTheDocument()
      })
    })

    it("shows loaded detail metadata and capped slice state", async () => {
      mockApplyOptimiser.mockResolvedValueOnce({
        status: "ok",
        total_objective: 1250000,
        constraints: { loss_ratio: 0.66 },
        from_artifact: true,
        preview: [{ quote_id: "Q001" }],
        row_count: 1250,
        preview_row_count: 100,
        preview_row_limit: 100,
        preview_truncated: true,
        error: null,
      })

      renderPreview()
      fireEvent.click(screen.getByText("Export"))
      fireEvent.click(screen.getByRole("button", { name: /Load detail/i }))

      await waitFor(() => {
        expect(screen.getByText(/100 of 1,250 rows loaded/)).toBeInTheDocument()
      })
      expect(screen.getByText(/capped at 100/)).toBeInTheDocument()
      expect(screen.getByRole("button", { name: /Save result/i })).toBeDisabled()
      expect(screen.getByRole("button", { name: /Log to MLflow/i })).toBeDisabled()
    })

    it("shows result detail failure state", async () => {
      mockApplyOptimiser.mockRejectedValueOnce(new Error("artifact missing"))

      renderPreview()
      fireEvent.click(screen.getByText("Export"))
      fireEvent.click(screen.getByRole("button", { name: /Load detail/i }))

      await waitFor(() => {
        expect(screen.getByText(/Detail load failed/)).toBeInTheDocument()
      })
      expect(screen.getByText(/artifact missing/)).toBeInTheDocument()
      expect(screen.getByRole("button", { name: /Save result/i })).not.toBeDisabled()
      expect(screen.getByRole("button", { name: /Log to MLflow/i })).not.toBeDisabled()
    })

    it("loads result detail for the selected frontier point by passing point_index to apply", async () => {
      renderPreview({
        data: makeData({
          frontier: makeFrontier(),
          selectedPointIndex: 0,
        }),
      })
      fireEvent.click(screen.getByText("Export"))
      fireEvent.click(screen.getByRole("button", { name: /Load detail/i }))

      await waitFor(() => {
        expect(mockApplyOptimiser).toHaveBeenCalledWith(
          { job_id: "job_123", point_index: 0 },
          { signal: expect.any(AbortSignal) },
        )
      })
      expect(mockSelectFrontierPointAPI).not.toHaveBeenCalled()
    })

    it("aborts an in-flight result detail request when the job changes", async () => {
      let firstReject: (reason?: unknown) => void = () => {}
      mockApplyOptimiser
        .mockImplementationOnce((_payload: unknown, options: { signal: AbortSignal }) => {
          options.signal.addEventListener("abort", () => {
            firstReject(new DOMException("Aborted", "AbortError"))
          })
          return new Promise((_resolve, reject) => {
            firstReject = reject
          })
        })

      const { rerender } = renderPreview()
      fireEvent.click(screen.getByText("Export"))
      fireEvent.click(screen.getByRole("button", { name: /Load detail/i }))
      const firstSignal = mockApplyOptimiser.mock.calls[0][1].signal as AbortSignal

      rerender(<OptimiserPreview data={makeData({ jobId: "job_456" })} nodeId="opt_1" />)
      expect(firstSignal.aborted).toBe(true)

      await waitFor(() => {
        expect(screen.queryByText("Loading result detail...")).not.toBeInTheDocument()
      })
      expect(mockApplyOptimiser).toHaveBeenCalledTimes(1)
      expect(screen.queryByText(/Detail load failed/)).not.toBeInTheDocument()
    })
  })

  describe("Summary tab constraint indicators", () => {
    it("renders met constraint with green indicator dot", () => {
      const data = makeData({
        result: makeSolveResult({
          constraints: { loss_ratio: 0.60 },
          baseline_constraints: { loss_ratio: 0.60 },
        }),
        constraints: { loss_ratio: { max: 1.05 } },
      })
      const { container } = renderPreview({ data })
      fireEvent.click(screen.getByText("Summary"))
      const dots = container.querySelectorAll('span[style*="background: var(--success)"]')
      expect(dots.length).toBeGreaterThanOrEqual(1)
    })

    it("renders unmet constraint with red indicator dot", () => {
      const data = makeData({
        result: makeSolveResult({
          constraints: { loss_ratio: 999 },
          baseline_constraints: { loss_ratio: 1 },
        }),
        constraints: { loss_ratio: { max: 1.05 } },
      })
      const { container } = renderPreview({ data })
      fireEvent.click(screen.getByText("Summary"))
      const redDots = container.querySelectorAll('span[style*="background: var(--danger)"]')
      expect(redDots.length).toBeGreaterThanOrEqual(1)
    })
  })

  describe("Summary tab lambda values", () => {
    it("renders lambda values with 6 decimal places", () => {
      renderPreview()
      fireEvent.click(screen.getByText("Summary"))
      expect(screen.getByText("0.005000")).toBeInTheDocument()
    })

    it("renders lambda constraint name", () => {
      renderPreview()
      fireEvent.click(screen.getByText("Summary"))
      const lambdaSection = screen.getByText("Lambdas")
      expect(lambdaSection).toBeInTheDocument()
      expect(screen.getAllByText("loss_ratio").length).toBeGreaterThanOrEqual(1)
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
      // Find the collapse button (ChevronDown in header)
      const buttons = screen.getAllByRole("button")
      const collapseBtn = buttons.find(
        (b) => b.querySelector("svg") && b !== buttons[buttons.length - 1] && !b.textContent,
      )
      if (collapseBtn) {
        fireEvent.click(collapseBtn)
        expect(screen.getByText("My Optimiser")).toBeInTheDocument()
      }
    })
  })

  describe("save and log failure messages", () => {
    it("shows error text when save fails", async () => {
      mockSaveOptimiser.mockRejectedValueOnce(new Error("disk full"))

      renderPreview({
        data: makeData({
          frontier: makeFrontier(),
          selectedPointIndex: 0,
        }),
      })

      fireEvent.click(screen.getByText("Save Result"))

      await waitFor(() => {
        expect(screen.getByText(/Save failed/)).toBeInTheDocument()
      })
    })

    it("shows error text when MLflow log fails", async () => {
      mockLogOptimiserToMlflow.mockRejectedValueOnce(new Error("tracking server down"))

      renderPreview({
        data: makeData({
          frontier: makeFrontier(),
          selectedPointIndex: 0,
        }),
      })

      fireEvent.click(screen.getByText("Log to MLflow"))

      await waitFor(() => {
        expect(screen.getByText(/MLflow log failed/)).toBeInTheDocument()
      })
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

    it("hides Lambdas section in ratebook mode", () => {
      renderPreview({
        data: makeData({ result: makeSolveResult({ mode: "ratebook" }) }),
      })
      fireEvent.click(screen.getByText("Summary"))
      expect(screen.queryByText("Lambdas")).not.toBeInTheDocument()
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
                { __factor_group__: "18-25", optimal_scenario_value: 1.15 },
                { __factor_group__: "26-35", optimal_scenario_value: 0.95 },
              ],
            },
          }),
        }),
      })
      fireEvent.click(screen.getByText("Rates"))
      expect(screen.getByText("age_band")).toBeInTheDocument()
      expect(screen.getByText("18-25")).toBeInTheDocument()
      expect(screen.getAllByText("1.1500").length).toBeGreaterThan(0)
    })
  })
})
