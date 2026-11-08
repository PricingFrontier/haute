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

/** Convergence's note for a selected frontier point: "Frontier point N:
 *  converged, K iterations" (the count omitted when the point reports none). */
export function selectedPointConvergenceNote(index: number, result: OptimiserSolveResult): string {
  const iterations = formatOptimiserIterationSummary(result)
  const outcome = result.converged ? "converged" : "not converged"
  return `Frontier point ${index + 1}: ${outcome}`
    + (iterations ? `, ${iterations.long}` : "")
}
