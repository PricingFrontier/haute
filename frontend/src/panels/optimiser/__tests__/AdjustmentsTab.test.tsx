import { useState } from "react"
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import type { OptimiserAdjustmentReport, OptimiserSolveResult } from "../../../api/types"
import { makeFrontierSelect } from "../../../test-utils/factories"
import AdjustmentsTab, { type PointAdjustmentReports } from "../AdjustmentsTab"
import {
  makeAdjustmentReport,
  makeOneStepAdjustmentReport,
  makeOnlineSolveResult,
  makeRatebookAdjustmentReport,
  makeRatebookSolveResult,
} from "./fixtures"

const mockSelectFrontierPoint = vi.fn()

vi.mock("../../../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../../api/client")>()
  return {
    ...actual,
    selectFrontierPoint: (...args: unknown[]) => mockSelectFrontierPoint(...args),
  }
})

const { ApiError } = await import("../../../api/client")

afterEach(cleanup)
beforeEach(() => mockSelectFrontierPoint.mockReset())

type Deferred<T> = { promise: Promise<T>; resolve: (value: T) => void; reject: (error: unknown) => void }

function deferred<T>(): Deferred<T> {
  let resolve!: (value: T) => void
  let reject!: (error: unknown) => void
  const promise = new Promise<T>((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

/** The preview owns the loaded point reports for its review; this stands in for it. */
function Harness({
  solvedResult = makeOnlineSolveResult(),
  pointIndex = null,
  frontierGeneration = 0,
  initialReports = {},
  onLoaded,
}: {
  solvedResult?: OptimiserSolveResult
  pointIndex?: number | null
  frontierGeneration?: number
  initialReports?: PointAdjustmentReports
  onLoaded?: (key: string, report: OptimiserAdjustmentReport) => void
}) {
  const [reports, setReports] = useState<PointAdjustmentReports>(initialReports)
  return (
    <AdjustmentsTab
      jobId="job_1"
      frontierGeneration={frontierGeneration}
      pointIndex={pointIndex}
      solvedResult={solvedResult}
      pointReports={reports}
      onPointReport={(key, report) => {
        onLoaded?.(key, report)
        setReports((current) => ({ ...current, [key]: report }))
      }}
      width={640}
    />
  )
}

function chart() {
  return screen.getByRole("img", { name: /Chosen scenario values/ })
}

describe("AdjustmentsTab: the as-solved report", () => {
  it("draws one bar per grid value on a scenario-value axis against the base price", () => {
    const report = makeAdjustmentReport()
    report.bars[6] = { ...report.bars[6], quotes: 0, weights: { optimal_objective: 0 } }
    render(<Harness solvedResult={makeOnlineSolveResult({ adjustments: report })} />)

    const svg = chart()
    expect(within(svg).getByText("Scenario value (1.0 = base price)")).toBeInTheDocument()
    expect(within(svg).getByText("Quotes")).toBeInTheDocument()
    const bars = within(svg).getAllByTestId("adjustment-bar")
    expect(bars).toHaveLength(7)
    // A grid value nobody chose is still a bar, empty.
    expect(bars[6]).toHaveAttribute("height", "0")
    expect(Number(bars[3].getAttribute("height"))).toBeGreaterThan(0)
    for (const label of ["0.85", "0.9", "1", "1.15"]) {
      expect(within(svg).getByText(label)).toBeInTheDocument()
    }
    expect(within(svg).getByTestId("base-price-line")).toBeInTheDocument()
    expect(screen.getByText("1.0 = base price (no adjustment)")).toBeInTheDocument()
    expect(screen.queryByRole("alert")).not.toBeInTheDocument()
    expect(mockSelectFrontierPoint).not.toHaveBeenCalled()
  })

  it("states the quantiles, the mean and the shares of the chosen weighting", () => {
    render(<Harness />)

    const quantiles = screen.getByRole("group", { name: "Quantiles" })
    expect(within(quantiles).getByText("P5").nextSibling).toHaveTextContent("0.9")
    expect(within(quantiles).getByText("Median").nextSibling).toHaveTextContent("1")
    expect(within(quantiles).getByText("P95").nextSibling).toHaveTextContent("1.15")
    expect(within(quantiles).getByText("Mean").nextSibling).toHaveTextContent("1.013")
    const shares = screen.getByRole("group", { name: "Shares" })
    expect(within(shares).getByText("Adjusted up").nextSibling).toHaveTextContent("42.0%")
    expect(within(shares).getByText("Adjusted down").nextSibling).toHaveTextContent("28.0%")
    expect(within(shares).getByText("Unadjusted").nextSibling).toHaveTextContent("30.0%")
    expect(within(shares).getByText("At range minimum (0.85)").nextSibling).toHaveTextContent("2.0%")
    expect(within(shares).getByText("At range maximum (1.15)").nextSibling).toHaveTextContent("6.0%")
  })

  it("states a one-step grid's only step once as the range edge", () => {
    render(<Harness solvedResult={makeOnlineSolveResult({
      scenario_grid: [{ optimal_step: 0, scenario_value: 1.0 }],
      adjustments: makeOneStepAdjustmentReport(),
    })} />)

    const shares = screen.getByRole("group", { name: "Shares" })
    // The minimum and the maximum are the same step: one row, its share.
    expect(within(shares).getByText("At the range edge (1)").nextSibling).toHaveTextContent("100.0%")
    expect(within(shares).queryByText(/At range minimum/)).not.toBeInTheDocument()
    expect(within(shares).queryByText(/At range maximum/)).not.toBeInTheDocument()
  })

  it("weighs the bars and figures by the objective at the chosen scenario on request", () => {
    render(<Harness />)

    const quotes = screen.getByRole("button", { name: "Quotes" })
    const objective = screen.getByRole("button", { name: "Objective at the chosen scenario" })
    expect(quotes).toHaveAttribute("aria-pressed", "true")
    const quoteHeights = screen.getAllByTestId("adjustment-bar").map((bar) => bar.getAttribute("height"))

    fireEvent.click(objective)

    expect(objective).toHaveAttribute("aria-pressed", "true")
    expect(within(chart()).getByText("Objective at the chosen scenario")).toBeInTheDocument()
    const shares = screen.getByRole("group", { name: "Shares" })
    expect(within(shares).getByText("Adjusted up").nextSibling).toHaveTextContent("46.5%")
    expect(screen.getAllByTestId("adjustment-bar").map((bar) => bar.getAttribute("height")))
      .not.toEqual(quoteHeights)
    // The refused weighting is named with its reason, never offered.
    expect(screen.queryByRole("button", { name: /loss_ratio/ })).not.toBeInTheDocument()
    expect(screen.getByText(/loss_ratio at the chosen scenario cannot weigh the adjustments/))
      .toBeInTheDocument()
  })

  it("notes a grid without 1.0 and states no unadjusted share", () => {
    const report = makeAdjustmentReport({
      has_unadjusted: false,
      bars: [0.9, 0.95, 1.05, 1.1].map((scenario_value, optimal_step) => ({
        optimal_step,
        scenario_value,
        quotes: 1,
        weights: {},
      })),
      weightings: [{
        ...makeAdjustmentReport().weightings[0],
        total: 4,
        share_up: 0.5,
        share_down: 0.5,
        share_unadjusted: null,
      }],
      n_quotes: 4,
      diagnostics_errors: [],
    })
    render(<Harness solvedResult={makeOnlineSolveResult({ adjustments: report })} />)

    expect(screen.getByText("The scenario grid has no 1.0 step, so no quote is unadjusted."))
      .toBeInTheDocument()
    const shares = screen.getByRole("group", { name: "Shares" })
    expect(within(shares).queryByText("Unadjusted")).not.toBeInTheDocument()
    // 1.0 lies between 0.95 and 1.05, so the line is still drawn between them.
    expect(within(chart()).getByTestId("base-price-line")).toBeInTheDocument()
  })

  it("focuses a bar to read its exact values and lists every value in a table", () => {
    render(<Harness />)

    expect(screen.getByRole("status")).toHaveTextContent("Hover or focus a bar")
    fireEvent.focus(screen.getByRole("button", { name: /^Scenario value 1\.05:/ }))

    expect(screen.getByRole("status")).toHaveTextContent("1.05")
    expect(screen.getByRole("status")).toHaveTextContent("12,000 quotes")
    expect(screen.getByRole("status")).toHaveTextContent("24.0% of quotes")
    const table = screen.getByRole("table", { name: "Adjustment values" })
    expect(within(table).getAllByRole("row")).toHaveLength(8)
    // Scenario value | Step | Quotes | Share of quotes
    expect(within(table).getByRole("rowheader", { name: "1" }).parentElement)
      .toHaveTextContent("1315,00030.0%")
  })

  it("says why an online solve has no report", () => {
    render(
      <Harness
        solvedResult={makeOnlineSolveResult({
          adjustments: null,
          diagnostics_errors: [
            { diagnostic: "adjustments", error_type: "ValueError", message: "chosen step outside the grid" },
          ],
        })}
      />,
    )

    expect(screen.getByRole("alert")).toHaveTextContent("chosen step outside the grid")
  })

  it("fails loudly for an online solve with neither a report nor a reason", () => {
    vi.spyOn(console, "error").mockImplementation(() => {})
    expect(() => render(<Harness solvedResult={makeOnlineSolveResult({ adjustments: null })} />))
      .toThrow(/adjustment report/)
  })
})

describe("AdjustmentsTab: a ratebook report", () => {
  it("describes the evaluated steps and counts the quotes whose deployed factor differs", () => {
    render(<Harness solvedResult={makeRatebookSolveResult()} />)

    expect(within(chart()).getAllByTestId("adjustment-bar")).toHaveLength(9)
    expect(screen.getByText("As solved: 200 quotes")).toBeInTheDocument()
    const deployed = screen.getByRole("group", { name: "Deployed factor" })
    expect(within(deployed).getByText("Deployed factor differs from evaluated step").nextSibling)
      .toHaveTextContent("18 quotes (9.0%)")
    expect(deployed).toHaveTextContent(
      "Inside the scenario range the Optimiser Apply node deploys the unsnapped product of the "
      + "factor rates; the solve evaluated each quote at the nearest grid step. A product past "
      + "a grid edge deploys at that edge, the step the solve evaluated, so it is not counted.",
    )
  })

  it("loads a ratebook point's report with its own count", async () => {
    mockSelectFrontierPoint.mockResolvedValue(makeFrontierSelect({
      point_index: 1,
      adjustments: makeRatebookAdjustmentReport({ deployed_factor_differs: 0 }),
    }))
    render(<Harness solvedResult={makeRatebookSolveResult()} pointIndex={1} />)

    expect(await screen.findByText("Frontier point 2: 200 quotes")).toBeInTheDocument()
    const deployed = screen.getByRole("group", { name: "Deployed factor" })
    expect(within(deployed).getByText("Deployed factor differs from evaluated step").nextSibling)
      .toHaveTextContent("0 quotes (0.0%)")
  })

  it("says nothing about a deployed factor for an online report", () => {
    render(<Harness />)

    expect(screen.queryByRole("group", { name: "Deployed factor" })).not.toBeInTheDocument()
  })
})

describe("AdjustmentsTab: a selected frontier point", () => {
  it("loads the point's report through frontier select and keeps it for the review", async () => {
    const request = deferred<ReturnType<typeof makeFrontierSelect>>()
    mockSelectFrontierPoint.mockReturnValue(request.promise)
    const onLoaded = vi.fn()
    const { rerender } = render(<Harness pointIndex={2} onLoaded={onLoaded} />)

    expect(mockSelectFrontierPoint).toHaveBeenCalledTimes(1)
    expect(mockSelectFrontierPoint.mock.calls[0][0]).toEqual({
      job_id: "job_1",
      point_index: 2,
      include_adjustments: true,
    })
    expect(screen.getByText("Loading frontier point 3's adjustments...")).toBeInTheDocument()

    const pointReport = makeAdjustmentReport({ n_quotes: 50000 })
    await act(async () => request.resolve(makeFrontierSelect({ point_index: 2, adjustments: pointReport })))

    expect(onLoaded).toHaveBeenCalledWith("job_1:0:2", pointReport)
    expect(chart()).toBeInTheDocument()
    rerender(<Harness pointIndex={2} onLoaded={onLoaded} />)
    expect(mockSelectFrontierPoint).toHaveBeenCalledTimes(1)
  })

  it("makes no request for a point already loaded in this review", () => {
    render(<Harness pointIndex={1} initialReports={{ "job_1:0:1": makeAdjustmentReport() }} />)

    expect(mockSelectFrontierPoint).not.toHaveBeenCalled()
    expect(chart()).toBeInTheDocument()
  })

  it("discards a reply for a point the stepper has moved past", async () => {
    const first = deferred<ReturnType<typeof makeFrontierSelect>>()
    const second = deferred<ReturnType<typeof makeFrontierSelect>>()
    mockSelectFrontierPoint.mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise)
    const onLoaded = vi.fn()
    const { rerender } = render(<Harness pointIndex={1} onLoaded={onLoaded} />)
    rerender(<Harness pointIndex={2} onLoaded={onLoaded} />)

    const firstSignal = mockSelectFrontierPoint.mock.calls[0][1].signal as AbortSignal
    expect(firstSignal.aborted).toBe(true)
    await act(async () => first.resolve(makeFrontierSelect({ point_index: 1, adjustments: makeAdjustmentReport() })))
    expect(onLoaded).not.toHaveBeenCalled()

    await act(async () => second.resolve(makeFrontierSelect({ point_index: 2, adjustments: makeAdjustmentReport() })))
    expect(onLoaded).toHaveBeenCalledTimes(1)
    expect(onLoaded.mock.calls[0][0]).toBe("job_1:0:2")
  })

  it("reissues a request another point's apply replaced, without showing an error", async () => {
    mockSelectFrontierPoint
      .mockRejectedValueOnce(new ApiError("HTTP 409", 409, "replaced", undefined, {
        error_code: "frontier_point_apply_replaced",
        message: "A newer frontier point replaced this one.",
      }))
      .mockResolvedValueOnce(makeFrontierSelect({ point_index: 2, adjustments: makeAdjustmentReport() }))
    render(<Harness pointIndex={2} />)

    await act(async () => {})
    await act(async () => {})

    expect(mockSelectFrontierPoint).toHaveBeenCalledTimes(2)
    expect(screen.queryByRole("alert")).not.toBeInTheDocument()
    expect(chart()).toBeInTheDocument()
  })

  it("offers Retry after a failure and reissues the request", async () => {
    mockSelectFrontierPoint
      .mockRejectedValueOnce(new ApiError("HTTP 507", 507, "Not enough memory to materialise it."))
      .mockResolvedValueOnce(makeFrontierSelect({ point_index: 2, adjustments: makeAdjustmentReport() }))
    render(<Harness pointIndex={2} />)
    await act(async () => {})

    const alert = screen.getByRole("alert")
    expect(alert).toHaveTextContent("Not enough memory to materialise it.")
    fireEvent.click(within(alert).getByRole("button", { name: "Retry" }))
    await act(async () => {})

    expect(mockSelectFrontierPoint).toHaveBeenCalledTimes(2)
    expect(chart()).toBeInTheDocument()
  })

  it("says a point that is no longer available cannot be loaded, without Retry", async () => {
    mockSelectFrontierPoint.mockRejectedValueOnce(new ApiError("HTTP 410", 410, "gone", undefined, {
      error_code: "frontier_point_unavailable",
      message: "Frontier point 3 is no longer available. Re-run the solve to inspect it.",
    }))
    render(<Harness pointIndex={2} />)
    await act(async () => {})

    const alert = screen.getByRole("alert")
    expect(alert).toHaveTextContent("Frontier point 3 is no longer available. Re-run the solve to inspect it.")
    expect(within(alert).queryByRole("button", { name: "Retry" })).not.toBeInTheDocument()
  })

  it("discards an earlier generation's reply once a recompute reuses the point index", async () => {
    const earlier = deferred<ReturnType<typeof makeFrontierSelect>>()
    const recomputed = deferred<ReturnType<typeof makeFrontierSelect>>()
    mockSelectFrontierPoint.mockReturnValueOnce(earlier.promise).mockReturnValueOnce(recomputed.promise)
    const onLoaded = vi.fn()
    const { rerender } = render(<Harness pointIndex={2} frontierGeneration={0} onLoaded={onLoaded} />)
    rerender(<Harness pointIndex={2} frontierGeneration={1} onLoaded={onLoaded} />)

    expect(mockSelectFrontierPoint).toHaveBeenCalledTimes(2)
    await act(async () => recomputed.resolve(makeFrontierSelect({
      point_index: 2,
      frontier_generation: 1,
      adjustments: makeAdjustmentReport({ n_quotes: 222 }),
    })))
    await act(async () => earlier.resolve(makeFrontierSelect({
      point_index: 2,
      frontier_generation: 0,
      adjustments: makeAdjustmentReport({ n_quotes: 111 }),
    })))

    expect(onLoaded).toHaveBeenCalledTimes(1)
    expect(onLoaded.mock.calls[0][0]).toBe("job_1:1:2")
    expect(onLoaded.mock.calls[0][1].n_quotes).toBe(222)
  })

  it("reports a reply from a generation the view does not show instead of keeping it", async () => {
    // The server recomputed the frontier before the node installed the new generation.
    mockSelectFrontierPoint.mockResolvedValueOnce(makeFrontierSelect({
      point_index: 2,
      frontier_generation: 1,
      adjustments: makeAdjustmentReport(),
    }))
    const onLoaded = vi.fn()
    render(<Harness pointIndex={2} frontierGeneration={0} onLoaded={onLoaded} />)
    await act(async () => {})

    expect(onLoaded).not.toHaveBeenCalled()
    expect(screen.getByRole("alert")).toHaveTextContent(
      "The server answered for frontier generation 1, but this result shows generation 0.",
    )
  })

  it("fails loudly when the server answers a point without its report", async () => {
    mockSelectFrontierPoint.mockResolvedValueOnce(makeFrontierSelect({ point_index: 2, adjustments: null }))
    render(<Harness pointIndex={2} />)
    await act(async () => {})

    expect(screen.getByRole("alert")).toHaveTextContent("no adjustment report")
  })
})
