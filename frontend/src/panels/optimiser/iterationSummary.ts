import type { OptimiserSolveResult } from "../../api/types"

export type IterationSummary = {
  compact: string
  long: string
}

function finiteIterationCount(value: number | null | undefined): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null
}

export function formatOptimiserIterationSummary(result: OptimiserSolveResult): IterationSummary | null {
  const cdIterations = finiteIterationCount(result.cd_iterations)
  if (result.mode === "ratebook" && cdIterations !== null) {
    return {
      compact: `${cdIterations} CD iters`,
      long: `${cdIterations} CD iterations`,
    }
  }

  const iterations = finiteIterationCount(result.iterations)
  if (iterations !== null) {
    return {
      compact: `${iterations} iters`,
      long: `${iterations} iterations`,
    }
  }

  return null
}
