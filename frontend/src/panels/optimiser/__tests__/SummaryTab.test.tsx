import { cleanup, fireEvent, render, screen, within } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"
import SummaryTab from "../SummaryTab"
import { makeSolveResult } from "../../../test-utils/factories"
import {
  makeAdjustmentReport,
  makeOneStepAdjustmentReport,
  makeOnlineSolveResult,
  makeRatebookSolveResult,
} from "./fixtures"

afterEach(cleanup)

function attainmentRow(name: string): HTMLElement {
  const table = screen.getByRole("table", { name: "Constraint attainment" })
  const row = within(table).getByRole("rowheader", { name }).closest("tr")
  if (!row) throw new Error(`No attainment row for ${name}`)
  return row
}

describe("optimiser SummaryTab constraint attainment", () => {
  it("states bound, achieved, signed slack, status and λ in text", () => {
    render(
      <SummaryTab
        selectedPointIndex={null}
        result={makeSolveResult({
          mode: "online",
          constraints: { volume: 1_012_400 },
          effective_bounds: { volume: { kind: "min", bound: 1_000_000 } },
          lambdas: { volume: 0.0031 },
        })}
      />,
    )

    const cells = within(attainmentRow("volume")).getAllByRole("cell").map((cell) => cell.textContent)
    expect(cells).toEqual(["min", "1,000,000", "1,012,400", "+12,400 (+1.24%)", "Met", "0.003100"])
  })

  it("reads a positive λ beside positive slack as Met, never as binding", () => {
    render(
      <SummaryTab
        selectedPointIndex={null}
        result={makeSolveResult({
          constraints: { volume: 5.5 },
          effective_bounds: { volume: { kind: "min", bound: 5 } },
          lambdas: { volume: 23.0 },
        })}
      />,
    )

    expect(within(attainmentRow("volume")).getByText("Met")).toBeInTheDocument()
    expect(screen.queryByText(/binding/i)).not.toBeInTheDocument()
    expect(screen.getByRole("columnheader", { name: /λ \(multiplier\)/ })).toBeInTheDocument()
  })

  it("says a breach in words with its signed percentage", () => {
    render(
      <SummaryTab
        selectedPointIndex={null}
        result={makeSolveResult({
          constraints: { loss_ratio: 100.01 },
          effective_bounds: { loss_ratio: { kind: "max", bound: 100 } },
          lambdas: { loss_ratio: 0.5 },
        })}
      />,
    )

    const row = attainmentRow("loss_ratio")
    expect(within(row).getByText("Breached")).toBeInTheDocument()
    expect(within(row).getByText("-0.01 (-0.01%)")).toBeInTheDocument()
  })

  it("lists every constraint the backend bounds, swept or not, in its order", () => {
    render(
      <SummaryTab
        selectedPointIndex={null}
        result={makeSolveResult({
          constraints: { margin: 389.7, volume: 5.2 },
          effective_bounds: {
            volume: { kind: "min", bound: 5 },
            margin: { kind: "max", bound: 400 },
          },
          lambdas: { margin: 0, volume: 0.4 },
        })}
      />,
    )

    const table = screen.getByRole("table", { name: "Constraint attainment" })
    expect(within(table).getAllByRole("rowheader").map((cell) => cell.textContent)).toEqual(["volume", "margin"])
  })

  it("shows λ for a ratebook result too", () => {
    render(
      <SummaryTab
        selectedPointIndex={null}
        result={makeSolveResult({
          mode: "ratebook",
          constraints: { volume: 0.93 },
          effective_bounds: { volume: { kind: "min", bound: 0.9 } },
          lambdas: { volume: 0.55 },
        })}
      />,
    )

    expect(within(attainmentRow("volume")).getByText("0.550000")).toBeInTheDocument()
  })

  it("fails loudly when the backend sent no bound for a constraint", () => {
    expect(() => render(
      <SummaryTab
        selectedPointIndex={null}
        result={makeSolveResult({
          constraints: { volume: 5.2 },
          effective_bounds: {},
          lambdas: { volume: 0.4 },
        })}
      />,
    )).toThrow(/volume/)
  })
})

describe("optimiser SummaryTab diagnostics issues", () => {
  it("names each diagnostic that could not be produced, with its error, in an alert", () => {
    render(
      <SummaryTab
        selectedPointIndex={null}
        result={makeSolveResult({
          diagnostics_errors: [
            {
              diagnostic: "adjustments",
              error_type: "ValueError",
              message: "The solve result has no quotes to summarise",
            },
            {
              diagnostic: "frontier",
              error_type: "RuntimeError",
              message: "frontier exploded",
            },
          ],
        })}
      />,
    )

    const alert = screen.getByRole("alert", { name: "Diagnostic issues" })
    expect(alert).toHaveTextContent("Diagnostics Issues")
    expect(within(alert).getByText("Adjustment report")).toBeInTheDocument()
    expect(within(alert).getByText("Efficient frontier")).toBeInTheDocument()
    expect(within(alert).getByText("ValueError")).toBeInTheDocument()
    expect(within(alert).getByText("The solve result has no quotes to summarise")).toBeInTheDocument()
    expect(within(alert).getByText("frontier exploded")).toBeInTheDocument()
  })

  it("shows no alert when every diagnostic was produced", () => {
    render(<SummaryTab selectedPointIndex={null} result={makeSolveResult()} />)

    expect(screen.queryByRole("alert", { name: "Diagnostic issues" })).not.toBeInTheDocument()
  })
})

describe("optimiser SummaryTab adjustments summary", () => {
  it("states the as-solved shares compactly and links to the Adjustments tab", () => {
    const onOpenAdjustments = vi.fn()
    render(
      <SummaryTab
        selectedPointIndex={null}
        result={makeOnlineSolveResult()}
        onOpenAdjustments={onOpenAdjustments}
      />,
    )

    const summary = screen.getByRole("group", { name: "Adjustments" })
    expect(within(summary).getByText("Adjusted up").nextSibling).toHaveTextContent("42.0%")
    expect(within(summary).getByText("Adjusted down").nextSibling).toHaveTextContent("28.0%")
    expect(within(summary).getByText("Unadjusted").nextSibling).toHaveTextContent("30.0%")
    // 2.0% at the grid's minimum plus 6.0% at its maximum.
    expect(within(summary).getByText("At the range edge").nextSibling).toHaveTextContent("8.0%")
    fireEvent.click(within(summary).getByRole("button", { name: "View adjustments" }))
    expect(onOpenAdjustments).toHaveBeenCalledTimes(1)
    // The old unlabelled histogram is gone.
    expect(screen.queryByText("Scenario Value Distribution")).not.toBeInTheDocument()
  })

  it("counts a one-step grid's only step once at the range edge", () => {
    render(
      <SummaryTab
        selectedPointIndex={null}
        result={makeOnlineSolveResult({
          scenario_grid: [{ optimal_step: 0, scenario_value: 1.0 }],
          adjustments: makeOneStepAdjustmentReport(),
        })}
        onOpenAdjustments={() => {}}
      />,
    )

    const summary = screen.getByRole("group", { name: "Adjustments" })
    // The minimum and the maximum are the same step: its share, not their sum.
    expect(within(summary).getByText("At the range edge").nextSibling).toHaveTextContent("100.0%")
    expect(within(summary).getByText("Unadjusted").nextSibling).toHaveTextContent("100.0%")
  })

  it("fails loudly when a one-step report's minimum and maximum shares disagree", () => {
    const report = makeOneStepAdjustmentReport()
    const broken = makeOneStepAdjustmentReport({
      weightings: [{ ...report.weightings[0], share_at_max: 0.5 }],
    })
    // React reports the render error to the console before rethrowing it.
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => {})
    try {
      expect(() => render(
        <SummaryTab
          selectedPointIndex={null}
          result={makeOnlineSolveResult({ scenario_grid: [{ optimal_step: 0, scenario_value: 1.0 }], adjustments: broken })}
          onOpenAdjustments={() => {}}
        />,
      )).toThrow(/one-step grid's share at the minimum \(1\) and at the maximum \(0\.5\)/)
    } finally {
      consoleError.mockRestore()
    }
  })

  it("counts a ratebook result's quotes whose deployed factor differs from the evaluated step", () => {
    render(
      <SummaryTab
        selectedPointIndex={null}
        result={makeRatebookSolveResult()}
        onOpenAdjustments={() => {}}
      />,
    )

    const summary = screen.getByRole("group", { name: "Adjustments" })
    expect(within(summary).getByText("Adjusted up").nextSibling).toHaveTextContent("40.0%")
    expect(within(summary).getByText("Deployed ≠ evaluated step").nextSibling)
      .toHaveTextContent("18 quotes")
  })

  it("has no deployed-factor count for an online result", () => {
    render(
      <SummaryTab selectedPointIndex={null} result={makeOnlineSolveResult()} onOpenAdjustments={() => {}} />,
    )

    expect(screen.queryByText("Deployed ≠ evaluated step")).not.toBeInTheDocument()
  })

  it("omits the unadjusted share when the grid has no 1.0", () => {
    const quotes = { ...makeAdjustmentReport().weightings[0], share_unadjusted: null }
    render(
      <SummaryTab
        selectedPointIndex={null}
        result={makeOnlineSolveResult({
          adjustments: makeAdjustmentReport({ has_unadjusted: false, weightings: [quotes] }),
        })}
        onOpenAdjustments={() => {}}
      />,
    )

    const summary = screen.getByRole("group", { name: "Adjustments" })
    expect(within(summary).queryByText("Unadjusted")).not.toBeInTheDocument()
    expect(within(summary).getByText("The scenario grid has no 1.0 step, so no quote is unadjusted."))
      .toBeInTheDocument()
  })

  it("sends a selected point's adjustments to the Adjustments tab", () => {
    const onOpenAdjustments = vi.fn()
    render(
      <SummaryTab
        selectedPointIndex={2}
        result={makeOnlineSolveResult({ adjustments: null })}
        onOpenAdjustments={onOpenAdjustments}
      />,
    )

    const summary = screen.getByRole("group", { name: "Adjustments" })
    expect(summary).toHaveTextContent("Frontier point 3's adjustments load in the Adjustments tab.")
    fireEvent.click(within(summary).getByRole("button", { name: "View adjustments" }))
    expect(onOpenAdjustments).toHaveBeenCalledTimes(1)
  })

  it("shows no adjustments summary where the workspace offers no Adjustments tab", () => {
    render(<SummaryTab selectedPointIndex={null} result={makeOnlineSolveResult()} />)

    expect(screen.queryByRole("group", { name: "Adjustments" })).not.toBeInTheDocument()
  })
})
