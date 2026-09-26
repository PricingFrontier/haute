import { cleanup, render, screen, within } from "@testing-library/react"
import { afterEach, describe, expect, it } from "vitest"

import type { OptimiserRatebookCdTraceRecord } from "../../../api/types"
import { makeHistoryEntry } from "../../../test-utils/factories"
import ConvergenceChart from "../ConvergenceChart"
import {
  makeOnlineHistory,
  makeOnlineSolveResult,
  makeRatebookCdTrace,
  makeRatebookSolveResult,
} from "./fixtures"

afterEach(cleanup)

function renderConvergence(ui: Parameters<typeof ConvergenceChart>[0]) {
  return render(<ConvergenceChart {...ui} width={900} />)
}

/** One small multiple: its SVG by accessible name. */
function chart(name: string): SVGSVGElement {
  return screen.getByRole("img", { name }) as unknown as SVGSVGElement
}

function tickLabels(svg: Element): string[] {
  return Array.from(svg.querySelectorAll('[data-testid="chart-value-tick"] text')).map((t) => t.textContent!)
}

function pathCoordinates(svg: Element): number[] {
  return Array.from(svg.querySelectorAll("path"))
    .flatMap((path) => (path.getAttribute("d") ?? "").split(/[\sMLC,]+/).filter(Boolean))
    .map(Number)
}

describe("ConvergenceChart (online)", () => {
  it("draws the objective on a real axis", () => {
    renderConvergence({ solvedResult: makeOnlineSolveResult(), selectedPoint: null })

    const objective = chart("Objective by iteration")
    // History objectives 1.1M and 1.2M, padded: real values, not 0..1.
    expect(tickLabels(objective)).toEqual(["1.09M", "1.12M", "1.15M", "1.18M", "1.21M"])
    const xLabels = Array.from(objective.querySelectorAll('[data-testid="chart-x-tick"] text')).map((t) => t.textContent)
    expect(xLabels).toEqual(["1", "2"])
  })

  it("draws the largest λ change on a log axis", () => {
    renderConvergence({ solvedResult: makeOnlineSolveResult(), selectedPoint: null })

    expect(tickLabels(chart("Largest λ change by iteration"))).toEqual(["0.01", "0.1"])
  })

  it("draws a zero λ change at the axis floor and says so", () => {
    const history = [
      makeHistoryEntry({ iteration: 0, max_lambda_change: 0.1, total_constraints: { loss_ratio: 0.7 }, lambdas: { loss_ratio: 0.1 } }),
      makeHistoryEntry({ iteration: 1, max_lambda_change: 0.001, total_constraints: { loss_ratio: 0.7 }, lambdas: { loss_ratio: 0.1 } }),
      makeHistoryEntry({ iteration: 2, max_lambda_change: 0, total_constraints: { loss_ratio: 0.7 }, lambdas: { loss_ratio: 0.1 } }),
    ]
    renderConvergence({ solvedResult: makeOnlineSolveResult({ history }), selectedPoint: null })

    const lambdaChange = chart("Largest λ change by iteration")
    // The floor is the decade below the smallest change, 0.0001.
    expect(tickLabels(lambdaChange)).toEqual(["0.0001", "0.001", "0.01", "0.1"])
    expect(pathCoordinates(lambdaChange).every(Number.isFinite)).toBe(true)
    expect(screen.getByText(
      "1 iteration with no λ change (0) is drawn at the axis floor, 0.0001.",
    )).toBeInTheDocument()
  })

  it("says so instead of drawing when λ never changed", () => {
    const history = [makeHistoryEntry({ iteration: 0, max_lambda_change: 0, total_constraints: { loss_ratio: 0.7 }, lambdas: { loss_ratio: 0 } })]
    renderConvergence({ solvedResult: makeOnlineSolveResult({ history }), selectedPoint: null })

    expect(screen.queryByRole("img", { name: "Largest λ change by iteration" })).not.toBeInTheDocument()
    expect(screen.getByText("λ did not change in any iteration (the largest change is 0 throughout).")).toBeInTheDocument()
  })

  it("draws each constraint total against its bound with the first feasible iteration", () => {
    renderConvergence({ solvedResult: makeOnlineSolveResult(), selectedPoint: null })

    const total = chart("loss_ratio total by iteration")
    const bound = total.querySelector('line[data-reference="max bound 1.05"]')!
    expect(bound.getAttribute("stroke-dasharray")).toBe("5,3")
    // Iteration 2 is the first whose constraints were all met.
    expect(total.querySelector('line[data-marker="First feasible (iteration 2)"]')).not.toBeNull()
    expect(screen.getByText("max bound 1.05")).toBeInTheDocument()
  })

  it("says when no iteration met every constraint", () => {
    const history = makeOnlineHistory().map((entry) => ({ ...entry, all_constraints_satisfied: false }))
    renderConvergence({ solvedResult: makeOnlineSolveResult({ history }), selectedPoint: null })

    expect(chart("loss_ratio total by iteration").querySelector("line[data-marker]")).toBeNull()
    expect(screen.getByText("No iteration met every constraint.")).toBeInTheDocument()
  })

  it("draws λ per constraint", () => {
    renderConvergence({ solvedResult: makeOnlineSolveResult(), selectedPoint: null })

    const lambdas = chart("λ by iteration")
    expect(lambdas.querySelector('path[data-series="loss_ratio"]')).not.toBeNull()
  })

  it("draws only the objective and λ change for a solve without constraints", () => {
    const history = [makeHistoryEntry({ iteration: 0, total_objective: 5, max_lambda_change: 0 })]
    renderConvergence({
      solvedResult: makeOnlineSolveResult({
        history,
        constraints: {},
        baseline_constraints: {},
        effective_bounds: {},
        lambdas: {},
      }),
      selectedPoint: null,
    })

    expect(chart("Objective by iteration")).toBeInTheDocument()
    expect(screen.queryByRole("img", { name: "λ by iteration" })).not.toBeInTheDocument()
  })

  it("lists every iteration's values in a closed values table", () => {
    renderConvergence({ solvedResult: makeOnlineSolveResult(), selectedPoint: null })

    const table = screen.getByRole("table", { name: "Iteration values" })
    expect(table.closest("details")).not.toHaveAttribute("open")
    expect(screen.getByText("View iteration values")).toBeInTheDocument()
    const headers = within(table).getAllByRole("columnheader").map((cell) => cell.textContent)
    expect(headers).toEqual([
      "Iteration", "Objective", "Largest λ change", "loss_ratio total", "λ loss_ratio", "All constraints met",
    ])
    const rows = within(table).getAllByRole("row").slice(1)
    expect(rows.map((row) => within(row).getAllByRole("cell").map((cell) => cell.textContent))).toEqual([
      ["1,100,000", "1.00e-1", "0.7", "0.004000", "No"],
      ["1,200,000", "1.00e-2", "0.65", "0.005000", "Yes"],
    ])
  })

  it("says whose history it shows when a frontier point is selected", () => {
    renderConvergence({
      solvedResult: makeOnlineSolveResult(),
      selectedPoint: { index: 1, result: makeOnlineSolveResult({ iterations: 11, history: null }) },
    })

    expect(screen.getByText(
      "History is recorded for the solved result; frontier point 2: converged, 11 iterations",
    )).toBeInTheDocument()
  })

  it("fails loudly for an online solve without history", () => {
    expect(() =>
      renderConvergence({ solvedResult: makeOnlineSolveResult({ history: null }), selectedPoint: null }),
    ).toThrow(/online solve always records its history/)
  })

  it("fails loudly for a history entry missing a constraint", () => {
    const history = [makeHistoryEntry({ iteration: 0, total_constraints: {}, lambdas: { loss_ratio: 0 } })]
    expect(() =>
      renderConvergence({ solvedResult: makeOnlineSolveResult({ history }), selectedPoint: null }),
    ).toThrow(/loss_ratio/)
  })
})

describe("ConvergenceChart (ratebook)", () => {
  it("draws the objective by CD pass, one line per factor", () => {
    renderConvergence({ solvedResult: makeRatebookSolveResult(), selectedPoint: null })

    const objective = chart("Objective by CD pass")
    const xLabels = Array.from(objective.querySelectorAll('[data-testid="chart-x-tick"] text')).map((t) => t.textContent)
    expect(xLabels).toEqual(["1", "2", "3", "4"])
    expect(objective.querySelectorAll('circle[data-series="region"]')).toHaveLength(4)
    expect(objective.querySelectorAll('circle[data-series="vehicle_age"]')).toHaveLength(4)
    expect(tickLabels(objective).length).toBeGreaterThan(1)
  })

  it("draws each constraint total against its bound by CD pass", () => {
    renderConvergence({ solvedResult: makeRatebookSolveResult(), selectedPoint: null })

    const total = chart("volume total by CD pass")
    expect(total.querySelector('line[data-reference="min bound 0.9"]')).not.toBeNull()
    expect(total.querySelector('path[data-series="region"]')).not.toBeNull()
  })

  it("lists every record in the values table", () => {
    renderConvergence({ solvedResult: makeRatebookSolveResult(), selectedPoint: null })

    const table = screen.getByRole("table", { name: "Coordinate-descent values" })
    const headers = within(table).getAllByRole("columnheader").map((cell) => cell.textContent)
    expect(headers).toEqual(["CD pass", "Factor", "Objective", "volume total", "λ volume"])
    expect(within(table).getAllByRole("row")).toHaveLength(9)
  })

  it("says when the trace kept only its last records", () => {
    const trace = { ...makeRatebookCdTrace(), truncated: true }
    renderConvergence({ solvedResult: makeRatebookSolveResult({ ratebook_cd_trace: trace }), selectedPoint: null })

    expect(screen.getByText(
      "Showing the last 8 coordinate-descent records; earlier ones were not kept.",
    )).toBeInTheDocument()
  })

  it("says the trace is recorded by live solves only when a result has none", () => {
    renderConvergence({ solvedResult: makeRatebookSolveResult({ ratebook_cd_trace: null }), selectedPoint: null })

    expect(screen.getByText(
      "The coordinate-descent trace is recorded by live solves only; this result has none.",
    )).toBeInTheDocument()
    expect(screen.queryByRole("img")).not.toBeInTheDocument()
  })

  it("fails loudly for two records of one pass and factor", () => {
    const [first] = makeRatebookCdTrace().records
    const records: OptimiserRatebookCdTraceRecord[] = [first, { ...first }]
    expect(() =>
      renderConvergence({
        solvedResult: makeRatebookSolveResult({ ratebook_cd_trace: { records, truncated: false } }),
        selectedPoint: null,
      }),
    ).toThrow(/pass 1.*region.*twice/)
  })
})
