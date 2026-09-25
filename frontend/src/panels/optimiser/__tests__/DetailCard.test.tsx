import { cleanup, render, screen, within } from "@testing-library/react"
import { afterEach, describe, expect, it } from "vitest"
import DetailCard from "../DetailCard"
import { makeSolveResult } from "../../../test-utils/factories"

afterEach(cleanup)

describe("optimiser DetailCard", () => {
  it("judges the point against the bound it was solved at, with λ apart from status", () => {
    render(
      <DetailCard
        result={makeSolveResult({
          total_objective: 130_000,
          constraints: { volume: 5.5, margin: 389.7 },
          effective_bounds: {
            volume: { kind: "min", bound: 5 },
            margin: { kind: "max", bound: 400 },
          },
          lambdas: { volume: 23.0, margin: 0 },
          selected_frontier_point: 2,
        })}
      />,
    )

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
    render(
      <DetailCard
        result={makeSolveResult({
          constraints: { volume: 5.5 },
          effective_bounds: { volume: { kind: "min", bound: 5 } },
          lambdas: { volume: 0.1 },
        })}
      />,
    )

    expect(screen.getByText("Save or log this point from the node's Export pane.")).toBeInTheDocument()
  })
})
