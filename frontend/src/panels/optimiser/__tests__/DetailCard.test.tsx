import { cleanup, render, screen, within } from "@testing-library/react"
import { afterEach, describe, expect, it } from "vitest"
import DetailCard, { type DetailCardPoint } from "../DetailCard"
import { assessFrontierPoint, type ConstraintKinds, type DiscreteTradeOff } from "../frontierSlices"
import type { FrontierPoint, OptimiserRatebookFrontierPoint } from "../../../api/types"
import { makeSolveResult } from "../../../test-utils/factories"
import { makeOnlineFrontierPoint } from "./fixtures"

afterEach(cleanup)

const KINDS: ConstraintKinds = { volume: "min", margin: "max" }
const NAMES = ["volume", "margin"]

function onlinePoint(overrides: Partial<ReturnType<typeof makeOnlineFrontierPoint>> = {}): FrontierPoint {
  return makeOnlineFrontierPoint(0, {
    total_objective: 130_000,
    thresholds: { volume: 5, margin: 400 },
    bounds: { volume: 5, margin: 400 },
    totals: { volume: 5.5, margin: 389.7 },
    lambdas: { volume: 23.0, margin: 0.0123456789 },
    iterations: 17,
    ...overrides,
  })
}

function ratebookPoint(overrides: Partial<OptimiserRatebookFrontierPoint> = {}): FrontierPoint {
  return {
    mode: "ratebook",
    total_objective: 96,
    thresholds: { volume: 5.5, margin: 400 },
    bounds: { volume: 5.5, margin: 400 },
    totals: { volume: 4.968, margin: 380 },
    lambdas: { volume: 0.4, margin: 0 },
    converged: true,
    iterations: 6,
    clamp_rate: 0.02,
    n_quotes_clamped_low: 0,
    n_quotes_clamped_high: 0,
    ...overrides,
  }
}

const NO_NEXT: DiscreteTradeOff = {
  kind: "unavailable",
  reason: "No point in this slice relaxes the volume bound further.",
}

function detail(point: FrontierPoint, tradeOff: DiscreteTradeOff = NO_NEXT): DetailCardPoint {
  return {
    index: 2,
    point,
    kinds: KINDS,
    xName: "volume",
    assessment: assessFrontierPoint(point, NAMES, KINDS),
    tradeOff,
  }
}

/** The displayed result a selected point builds from its summary. */
function resultFor(point: FrontierPoint) {
  return makeSolveResult({
    total_objective: point.total_objective,
    constraints: point.totals,
    effective_bounds: {
      volume: { kind: "min", bound: point.bounds.volume },
      margin: { kind: "max", bound: point.bounds.margin },
    },
    lambdas: point.lambdas,
    selected_frontier_point: 2,
  })
}

function renderCard(point: FrontierPoint, tradeOff?: DiscreteTradeOff) {
  return render(<DetailCard result={resultFor(point)} frontierPoint={detail(point, tradeOff)} />)
}

/** The value beside a labelled row of the card's facts list. */
function fact(label: string | RegExp): string | null {
  const term = screen.getByText(label, { selector: "dt" })
  return term.nextElementSibling?.textContent ?? null
}

describe("optimiser DetailCard", () => {
  it("judges the point against the bound it was solved at, with λ apart from status", () => {
    renderCard(onlinePoint())

    const table = screen.getByRole("table", { name: "Constraint attainment" })
    const volume = within(table).getByRole("rowheader", { name: "volume" }).closest("tr")!
    // A positive λ beside positive slack is Met; nothing claims the bound binds.
    expect(within(volume).getAllByRole("cell").map((cell) => cell.textContent)).toEqual([
      "min",
      "5",
      "5.5",
      "+0.5 (+10.00%)",
      "Met",
      "23.000000",
    ])
    expect(within(table).getByRole("rowheader", { name: "margin" })).toBeInTheDocument()
    expect(screen.queryByText(/binding/i)).not.toBeInTheDocument()
  })

  it("points publishing to the Export pane", () => {
    renderCard(onlinePoint())
    expect(screen.getByText("Save or log this point from the node's Export pane.")).toBeInTheDocument()
  })

  it("states the point's number, convergence and iterations", () => {
    renderCard(onlinePoint())
    expect(screen.getByText("Point details")).toBeInTheDocument()
    expect(fact("Point")).toBe("3")
    expect(fact("Converged")).toBe("Yes")
    expect(fact("Iterations")).toBe("17")
    expect(fact("Feasibility")).toBe("Feasible: converged and every bound met")
  })

  it("counts a ratebook point's iterations as coordinate-descent passes", () => {
    renderCard(ratebookPoint({ totals: { volume: 5.6, margin: 380 } }))
    expect(fact("CD passes")).toBe("6")
  })

  it("shows λ exactly as the solver reports it, with the sign it enters each quote's choice with", () => {
    renderCard(onlinePoint())
    const list = screen.getByRole("list", { name: "Multipliers as reported" })
    const items = within(list).getAllByRole("listitem").map((item) => item.textContent)
    expect(items).toEqual([
      "volume (min): λ = 23, entering each quote's choice as +λ × volume",
      "margin (max): λ = 0.0123456789, entering each quote's choice as −λ × margin",
    ])
  })

  it("explains a converged ratebook point that breaches a bound", () => {
    renderCard(ratebookPoint())
    expect(fact("Converged")).toBe("Yes")
    expect(fact("Feasibility")).toBe(
      "Breached: coordinate descent converged (the factor values stopped moving), which does not "
      + "check bounds; volume 4.968 is below its minimum 5.5",
    )
  })

  it("explains a converged online point that breaches only an unswept bound", () => {
    renderCard(onlinePoint({ totals: { volume: 5.5, margin: 401 } }))
    expect(fact("Feasibility")).toBe(
      "Breached: the multipliers converged within the solver's tolerance, but margin 401 is above its maximum 400",
    )
  })

  it("names why an online point did not converge", () => {
    renderCard(onlinePoint({ converged: false, non_convergence_reason: "above_envelope" }))
    expect(fact("Converged")).toBe("No")
    expect(fact("Feasibility")).toBe(
      "Not converged: the bound lies beyond what the solver can reach (the λ search hit its cap); "
      + "this is the closest point it found",
    )
  })

  it("names why a ratebook point did not converge", () => {
    renderCard(ratebookPoint({ converged: false }))
    expect(fact("Feasibility")).toBe(
      "Not converged: the factor values were still moving when coordinate descent stopped",
    )
  })

  it("shows the discrete trade-off with its sign and what it is", () => {
    renderCard(onlinePoint(), { kind: "value", value: 20, nextIndex: 0 })
    expect(
      fact("Objective change per unit of volume bound relaxed, to the next point in this slice"),
    ).toBe("+20 (to point 1)")
    expect(screen.getByText(
      /A discrete step across the frontier, in which other achieved totals may also move\. It is not a check of λ\./,
    )).toBeInTheDocument()
  })

  it("shows a negative trade-off with its sign", () => {
    renderCard(onlinePoint(), { kind: "value", value: -0.25, nextIndex: 4 })
    expect(
      fact("Objective change per unit of volume bound relaxed, to the next point in this slice"),
    ).toBe("−0.25 (to point 5)")
  })

  it("shows — with the reason when there is no trade-off", () => {
    renderCard(onlinePoint(), { kind: "unavailable", reason: "The next point in this slice has the same volume bound." })
    expect(
      fact("Objective change per unit of volume bound relaxed, to the next point in this slice"),
    ).toBe("— The next point in this slice has the same volume bound.")
  })
})
