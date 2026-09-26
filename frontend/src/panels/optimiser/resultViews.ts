import type { ResultsWorkspaceIntro } from "../ResultsWorkspace"
import { LAMBDA_LABEL } from "./lambdaCopy"

/** The optimiser result workspace's views, in tab order. */
export type OptimiserResultView = "frontier" | "summary" | "rates" | "quotes" | "convergence"

export const OPTIMISER_VIEW_LABELS: Record<OptimiserResultView, string> = {
  frontier: "Frontier",
  summary: "Summary",
  rates: "Rates",
  quotes: "Quotes",
  convergence: "Convergence",
}

/** Every view's intro. Clamp rate is price-contour's search-space diagnostic
 *  (see the optimiser-validation roadmap's price-contour contract). */
export const OPTIMISER_VIEW_INTRODUCTIONS: Record<OptimiserResultView, ResultsWorkspaceIntro> = {
  frontier: {
    title: "Efficient frontier",
    description:
      "Each point is a solve at a different constraint target: the highest expected objective "
      + "found at that level. Select a point to inspect it; the selected point is the Export "
      + "pane's publish target.",
  },
  summary: {
    title: "Solve summary",
    description:
      "The expected objective and constraint totals of the result shown, whether the solver "
      + "converged, and how the chosen scenario values are spread.",
  },
  rates: {
    title: "Ratebook rates",
    description:
      "The rate chosen for each factor level. A quote's combined factor is the product of its "
      + "levels' rates, collared to the scenario range the solve scored. Clamp rate is a "
      + "search-space diagnostic: the mean, over every grouped solve, of the fraction of "
      + "(quote, candidate) targets that fell strictly outside the scenario range. Quotes at a "
      + "grid edge are not counted in it.",
  },
  quotes: {
    title: "Per-quote choices",
    description:
      "The scenario value the optimiser chose for each quote of the publish target, with its "
      + "expected objective and constraint values.",
  },
  convergence: {
    title: "Convergence",
    description:
      `How the objective, each constraint total against its bound and the ${LAMBDA_LABEL} `
      + "settled over the solve's iterations, or over a ratebook solve's coordinate-descent "
      + "passes, one line per factor (λ in the values table).",
  },
}
