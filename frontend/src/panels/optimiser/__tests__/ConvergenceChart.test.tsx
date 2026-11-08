import { cleanup, fireEvent, render, screen, within } from "@testing-library/react"
import { useState } from "react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import type { OptimiserBisectionProbe, OptimiserRatebookCdTraceRecord } from "../../../api/types"
import { makeFrontierSelect, makeHistoryEntry } from "../../../test-utils/factories"
import ConvergenceChart, { type FrontierPointHistory, type PointHistories } from "../ConvergenceChart"
import {
  makeOnlineHistory,
  makeOnlineSolveResult,
  makeRatebookCdTrace,
  makeRatebookSolveResult,
} from "./fixtures"

const selectFrontierPoint = vi.hoisted(() => vi.fn())
vi.mock("../../../api/client", () => ({ selectFrontierPoint }))

beforeEach(() => selectFrontierPoint.mockReset())
afterEach(cleanup)

type ConvergenceProps = Parameters<typeof ConvergenceChart>[0]

function renderConvergence(
  ui: Pick<ConvergenceProps, "solvedResult" | "selectedPoint"> & Partial<ConvergenceProps>,
) {
  return render(<ConvergenceChart jobId="job" pointHistories={{}} onPointHistory={() => {}} {...ui} width={900} />)
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

describe("ConvergenceChart (a selected frontier point)", () => {
  function probe(overrides: Partial<OptimiserBisectionProbe>): OptimiserBisectionProbe {
    return {
      step: 0,
      phase: "bracket",
      bracket_lo: null,
      bracket_hi: null,
      lambdas: { volume: 0 },
      total_objective: 100,
      total_constraints: { volume: 5 },
      all_constraints_satisfied: false,
      accepted: false,
      ...overrides,
    }
  }

  const BISECTION: FrontierPointHistory = {
    kind: "bisection",
    probes: [
      probe({}),
      probe({ step: 1, lambdas: { volume: 1 }, total_objective: 90, total_constraints: { volume: 7 }, all_constraints_satisfied: true, accepted: true }),
      probe({ step: 2, phase: "bisect", bracket_lo: 0, bracket_hi: 1, lambdas: { volume: 0.5 }, total_objective: 95, total_constraints: { volume: 6.5 }, all_constraints_satisfied: true, accepted: true }),
      probe({ step: 3, phase: "bisect", bracket_lo: 0, bracket_hi: 0.5, lambdas: { volume: 0.25 }, total_objective: 98, total_constraints: { volume: 5.5 } }),
    ],
  }

  /** Point 2, bounded at min 6: not the solve's own max 1.05 on `loss_ratio`. */
  function selectedPoint(index = 1) {
    return {
      index,
      result: makeOnlineSolveResult({
        iterations: 4,
        history: null,
        constraints: { volume: 6.5 },
        effective_bounds: { volume: { kind: "min", bound: 6 } },
      }),
    }
  }

  function loaded(history: FrontierPointHistory) {
    renderConvergence({
      solvedResult: makeOnlineSolveResult(),
      selectedPoint: selectedPoint(),
      pointHistories: { "job:1": history },
    })
  }

  /** The view as the preview owns it: loaded traces stay across point changes. */
  function Review({ point }: { point: ConvergenceProps["selectedPoint"] }) {
    const [pointHistories, setPointHistories] = useState<PointHistories>({})
    return (
      <ConvergenceChart
        jobId="job"
        solvedResult={makeOnlineSolveResult()}
        selectedPoint={point}
        pointHistories={pointHistories}
        onPointHistory={(key, history) => setPointHistories((current) => ({ ...current, [key]: history }))}
        width={900}
      />
    )
  }

  it("says which point it charts and how that point ended", () => {
    loaded(BISECTION)

    expect(screen.getByText("Frontier point 2: converged, 4 iterations")).toBeInTheDocument()
  })

  it("charts a bisection's probed λ within its bracket", () => {
    loaded(BISECTION)

    const lambda = chart("λ by step")
    expect(lambda).toHaveTextContent("Step")
    const panel = within(screen.getByRole("region", { name: "Probed λ" }))
    for (const series of ["Bracket low", "Bracket high"]) {
      expect(panel.getByText(series)).toBeInTheDocument()
    }
    expect(selectFrontierPoint).not.toHaveBeenCalled()
  })

  it("charts a bisection's total against the point's bound, marking the last accepted probe", () => {
    loaded(BISECTION)

    const total = chart("volume total by step")
    expect(total.querySelector('line[data-reference="min bound 6"]')).not.toBeNull()
    expect(total.querySelector('line[data-marker="Accepted (step 2)"]')).not.toBeNull()
    expect(chart("Objective by step")).toBeInTheDocument()
  })

  it("says when no probe was accepted", () => {
    loaded({ kind: "bisection", probes: [probe({})] })

    expect(screen.getByText("No probe was accepted.")).toBeInTheDocument()
    expect(chart("volume total by step").querySelector("line[data-marker]")).toBeNull()
  })

  it("lists every probe, a bracket probe having no bracket", () => {
    loaded(BISECTION)

    const rows = within(screen.getByRole("table", { name: "Probe values" })).getAllByRole("row").slice(1)
    expect(rows.map((row) => within(row).getAllByRole("cell").map((cell) => cell.textContent))).toEqual([
      ["bracket", "0.000000", "—", "—", "100", "5", "No", "No"],
      ["bracket", "1.000000", "—", "—", "90", "7", "Yes", "Yes"],
      ["bisect", "0.500000", "0.000000", "1.000000", "95", "6.5", "Yes", "Yes"],
      ["bisect", "0.250000", "0.000000", "0.500000", "98", "5.5", "No", "No"],
    ])
  })

  it("draws a subgradient point with the small multiples against its own bound", () => {
    const history = [
      makeHistoryEntry({ iteration: 0, max_lambda_change: 0.1, total_constraints: { volume: 5 }, lambdas: { volume: 0.1 } }),
      makeHistoryEntry({ iteration: 1, max_lambda_change: 0.01, total_constraints: { volume: 6.5 }, lambdas: { volume: 0.2 } }),
      makeHistoryEntry({ iteration: 2, max_lambda_change: 0.001, total_constraints: { volume: 6.5 }, lambdas: { volume: 0.2 } }),
    ]
    loaded({ kind: "subgradient", history })

    expect(chart("volume total by iteration").querySelector('line[data-reference="min bound 6"]')).not.toBeNull()
    expect(within(screen.getByRole("table", { name: "Iteration values" })).getAllByRole("row")).toHaveLength(4)
  })

  it("draws a ratebook point's coordinate descent against its own bound", () => {
    loaded({ kind: "ratebook", cd_trace: makeRatebookCdTrace() })

    expect(chart("volume total by CD pass").querySelector('line[data-reference="min bound 6"]')).not.toBeNull()
  })

  it("makes no request without a selected point", () => {
    renderConvergence({ solvedResult: makeOnlineSolveResult(), selectedPoint: null })

    expect(selectFrontierPoint).not.toHaveBeenCalled()
  })

  it("loads a point's trace once and keeps it for the review", async () => {
    selectFrontierPoint.mockResolvedValue(makeFrontierSelect({ point_history: BISECTION }))
    const { rerender } = render(<Review point={selectedPoint()} />)

    expect(screen.getByText("Loading frontier point 2's convergence trace...")).toBeInTheDocument()
    await screen.findByRole("img", { name: "λ by step" })
    expect(selectFrontierPoint.mock.calls).toEqual([
      [{ job_id: "job", point_index: 1, include_history: true }, { signal: expect.any(AbortSignal) }],
    ])

    rerender(<Review point={selectedPoint(3)} />)
    await screen.findByText("Frontier point 4: converged, 4 iterations")
    await screen.findByRole("img", { name: "λ by step" })
    expect(selectFrontierPoint).toHaveBeenCalledTimes(2)

    rerender(<Review point={selectedPoint()} />)
    expect(screen.getByRole("img", { name: "λ by step" })).toBeInTheDocument()
    expect(selectFrontierPoint).toHaveBeenCalledTimes(2)
  })

  it("offers Retry when the trace cannot be loaded", async () => {
    selectFrontierPoint.mockRejectedValueOnce(new Error("The server is unavailable"))
    selectFrontierPoint.mockResolvedValue(makeFrontierSelect({ point_history: BISECTION }))
    render(<Review point={selectedPoint()} />)

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The convergence trace could not be loaded: The server is unavailable",
    )

    fireEvent.click(screen.getByRole("button", { name: "Retry" }))
    await screen.findByRole("img", { name: "λ by step" })
  })

  it("fails a reply that carries no trace", async () => {
    selectFrontierPoint.mockResolvedValue(makeFrontierSelect({ point_history: null }))
    render(<Review point={selectedPoint()} />)

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The server sent no convergence trace for frontier point 2.",
    )
  })
})
