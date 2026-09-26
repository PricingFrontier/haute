import { cleanup, render } from "@testing-library/react"
import { afterEach, describe, expect, it } from "vitest"

import type { OptimiserSolveResult } from "../../../api/types"
import ConvergenceChart from "../ConvergenceChart"

function result(history: Array<{ total_objective: number; max_lambda_change: number }>): OptimiserSolveResult {
  return {
    history: history.map((entry, index) => ({
      iteration: index + 1,
      all_constraints_satisfied: true,
      ...entry,
    })),
  } as unknown as OptimiserSolveResult
}

function paths(container: HTMLElement): string[] {
  return Array.from(container.querySelectorAll("path")).map((path) => path.getAttribute("d") ?? "")
}

describe("ConvergenceChart", () => {
  afterEach(cleanup)

  it("scales each series on its own padded domain, so both use the full height", () => {
    const { container } = render(
      <ConvergenceChart
        selectedPoint={null}
        solvedResult={result([
          { total_objective: 1000, max_lambda_change: 0.5 },
          { total_objective: 900, max_lambda_change: 0.1 },
          { total_objective: 800, max_lambda_change: 0.01 },
        ])}
      />,
    )

    const [objective, lambdaChange] = paths(container)
    expect(objective).toBe("M6.0,14.8 L200.0,70.0 L394.0,125.2")
    expect(lambdaChange).toBe("M6.0,14.8 L200.0,104.9 L394.0,125.2")
  })

  it("draws a constant series through the middle of the chart", () => {
    const { container } = render(
      <ConvergenceChart
        selectedPoint={null}
        solvedResult={result([
          { total_objective: 5, max_lambda_change: 0 },
          { total_objective: 5, max_lambda_change: 0 },
        ])}
      />,
    )

    const [objective, lambdaChange] = paths(container)
    expect(objective).toBe("M6.0,70.0 L394.0,70.0")
    expect(lambdaChange).toBe("M6.0,70.0 L394.0,70.0")
  })
})
