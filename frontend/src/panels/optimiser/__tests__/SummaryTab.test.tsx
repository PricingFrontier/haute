import { cleanup, render, screen, within } from "@testing-library/react"
import { afterEach, describe, expect, it } from "vitest"
import SummaryTab from "../SummaryTab"
import { makeSolveResult } from "../../../test-utils/factories"

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
              diagnostic: "scenario_value_stats",
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
    expect(within(alert).getByText("Scenario-value statistics")).toBeInTheDocument()
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
