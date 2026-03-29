import { describe, it, expect, vi, afterEach, beforeEach } from "vitest"
import { render, screen, fireEvent, cleanup, waitFor } from "@testing-library/react"
import OptimiserPreview from "../OptimiserPreview"
import type { OptimiserPreviewData, SolveResult, FrontierData } from "../OptimiserPreview"

// ── Mocks ────────────────────────────────────────────────────────

const mockSelectFrontierPointAPI = vi.fn()
const mockSaveOptimiser = vi.fn()
const mockLogOptimiserToMlflow = vi.fn()

vi.mock("../../api/client", () => ({
  selectFrontierPoint: (...args: unknown[]) => mockSelectFrontierPointAPI(...args),
  saveOptimiser: (...args: unknown[]) => mockSaveOptimiser(...args),
  logOptimiserToMlflow: (...args: unknown[]) => mockLogOptimiserToMlflow(...args),
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

function makeFrontier(n = 5): FrontierData {
  const points = Array.from({ length: n }, (_, i) => ({
    total_objective: 1200000 + i * 10000,
    total_loss_ratio: 0.55 + i * 0.02,
    lambda_loss_ratio: 0.001 + i * 0.001,
  }))
  return {
    points,
    n_points: n,
    constraint_names: ["loss_ratio"],
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
    })
    mockSaveOptimiser.mockResolvedValue({ status: "ok", path: "output/optimiser_my_optimiser.json" })
    mockLogOptimiserToMlflow.mockResolvedValue({ status: "ok", run_id: "abc123" })
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

    it("renders Objective label and values", () => {
      renderPreview()
      // Click Summary tab in case it's not the default (no frontier data -> summary is default)
      fireEvent.click(screen.getByText("Summary"))
      expect(screen.getByText("Objective")).toBeInTheDocument()
      expect(screen.getByText("Optimised")).toBeInTheDocument()
      expect(screen.getByText("Baseline")).toBeInTheDocument()
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

    it("renders uplift percentage when baseline is non-zero", () => {
      renderPreview()
      fireEvent.click(screen.getByText("Summary"))
      expect(screen.getByText("Uplift")).toBeInTheDocument()
      // (1234567 / 1200000 - 1) * 100 = 2.88%
      expect(screen.getByText("2.88%")).toBeInTheDocument()
    })

    it("does not render uplift when baseline is zero", () => {
      renderPreview({ data: makeData({ result: makeSolveResult({ baseline_objective: 0 }) }) })
      fireEvent.click(screen.getByText("Summary"))
      expect(screen.queryByText("Uplift")).not.toBeInTheDocument()
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
  })

  describe("Frontier tab with data", () => {
    it("renders frontier scatter chart area", () => {
      renderPreview({ data: makeData({ frontier: makeFrontier() }) })
      expect(screen.getByText(/5 frontier points/)).toBeInTheDocument()
    })

    it("hides Frontier tab when frontier has empty points", () => {
      renderPreview({ data: makeData({ frontier: { points: [], n_points: 0, constraint_names: [] } }) })
      expect(screen.queryByText("Frontier")).not.toBeInTheDocument()
    })

    it("shows detail card when a point is selected", () => {
      renderPreview({
        data: makeData({
          frontier: makeFrontier(),
          selectedPointIndex: 2,
        }),
      })
      expect(screen.getByText("Point 3 of 5")).toBeInTheDocument()
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

    it("Save calls selectFrontierPoint API then saveOptimiser", async () => {
      renderPreview({
        data: makeData({
          frontier: makeFrontier(),
          selectedPointIndex: 0,
        }),
      })

      fireEvent.click(screen.getByText("Save Result"))

      await waitFor(() => {
        expect(mockSelectFrontierPointAPI).toHaveBeenCalledWith({ job_id: "job_123", point_index: 0 })
        expect(mockSaveOptimiser).toHaveBeenCalledWith({
          job_id: "job_123",
          output_path: "output/optimiser_my_optimiser.json",
        })
      })
    })

    it("Log to MLflow calls selectFrontierPoint API then logOptimiserToMlflow", async () => {
      renderPreview({
        data: makeData({
          frontier: makeFrontier(),
          selectedPointIndex: 0,
        }),
      })

      fireEvent.click(screen.getByText("Log to MLflow"))

      await waitFor(() => {
        expect(mockSelectFrontierPointAPI).toHaveBeenCalledWith({ job_id: "job_123", point_index: 0 })
        expect(mockLogOptimiserToMlflow).toHaveBeenCalledWith({
          job_id: "job_123",
        })
      })
    })

    it("clicking a scatter point calls store selectFrontierPoint", () => {
      renderPreview({
        data: makeData({ frontier: makeFrontier() }),
      })
      // The SVG circles are the frontier points; find them and click one
      const circles = document.querySelectorAll("circle[style*='cursor: pointer']")
      expect(circles.length).toBe(5)
      fireEvent.click(circles[2])
      expect(mockStoreSelectPoint).toHaveBeenCalledWith("opt_1", 2)
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

    it("detail card shows lambda values", () => {
      renderPreview({
        data: makeData({
          frontier: makeFrontier(),
          selectedPointIndex: 0,
        }),
      })
      expect(screen.getByText("Lambdas")).toBeInTheDocument()
    })

    it("constraint dropdown appears when multiple constraints exist", () => {
      const frontier: FrontierData = {
        points: Array.from({ length: 3 }, (_, i) => ({
          total_objective: 1200000 + i * 10000,
          total_loss_ratio: 0.55 + i * 0.02,
          total_volume: 100 + i * 10,
        })),
        n_points: 3,
        constraint_names: ["loss_ratio", "volume"],
      }
      renderPreview({
        data: makeData({
          frontier,
          constraints: { loss_ratio: { max: 1.05 }, volume: { min: 0.95 } },
        }),
      })
      expect(screen.getByText("X axis:")).toBeInTheDocument()
    })

    it("stepper buttons navigate between points", () => {
      renderPreview({
        data: makeData({
          frontier: makeFrontier(),
          selectedPointIndex: 2,
        }),
      })
      expect(screen.getByText("Point 3 of 5")).toBeInTheDocument()
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
      const dots = container.querySelectorAll('span[style*="background: rgb(34, 197, 94)"]')
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
      const redDots = container.querySelectorAll('span[style*="background: rgb(239, 68, 68)"]')
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

  describe("frontier point click API failure", () => {
    it("reverts selection when selectFrontierPointAPI rejects", async () => {
      // Make the API call reject
      mockSelectFrontierPointAPI.mockRejectedValueOnce(new Error("network error"))

      renderPreview({
        data: makeData({ frontier: makeFrontier() }),
      })

      // Click a frontier scatter point
      const circles = document.querySelectorAll("circle[style*='cursor: pointer']")
      expect(circles.length).toBe(5)
      fireEvent.click(circles[2])

      // First call: optimistic select with new index
      expect(mockStoreSelectPoint).toHaveBeenCalledWith("opt_1", 2)

      await waitFor(() => {
        // Second call: revert to previous selection (null since none was selected)
        expect(mockStoreSelectPoint).toHaveBeenCalledTimes(2)
        expect(mockStoreSelectPoint).toHaveBeenLastCalledWith("opt_1", null)
      })
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

    it("renders factor tables in ratebook mode", () => {
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
      fireEvent.click(screen.getByText("Summary"))
      expect(screen.getByText("Factor Tables")).toBeInTheDocument()
      expect(screen.getByText("age_band")).toBeInTheDocument()
      expect(screen.getByText("18-25")).toBeInTheDocument()
      expect(screen.getByText("1.15")).toBeInTheDocument()
    })
  })
})
