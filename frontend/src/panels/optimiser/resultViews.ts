import type { ResultsWorkspaceIntro } from "../ResultsWorkspace"
import { LAMBDA_LABEL } from "./lambdaCopy"

/** The optimiser result workspace's views, in tab order. */
export type OptimiserResultView =
  | "frontier"
  | "summary"
  | "rates"
  | "adjustments"
  | "segments"
  | "quotes"
  | "convergence"
  | "curves"
  | "statistics"

export const OPTIMISER_VIEW_LABELS: Record<OptimiserResultView, string> = {
  frontier: "Frontier",
  summary: "Summary",
  rates: "Rates",
  adjustments: "Adjustments",
  segments: "Segments",
  quotes: "Quotes",
  convergence: "Convergence",
  curves: "Curves",
  statistics: "Statistics",
}

/** Every view's intro; the Frontier pane has none, its chart and points table speak for it.
 *  Clamp rate is price-contour's search-space diagnostic (see the optimiser-validation
 *  roadmap's price-contour contract). */
export const OPTIMISER_VIEW_INTRODUCTIONS: Record<OptimiserResultView, ResultsWorkspaceIntro | null> = {
  frontier: null,
  summary: {
    title: "Solve summary",
    description:
      "The expected objective and constraint totals of the result shown, whether the solver "
      + "converged, and how many quotes the optimiser adjusted up or down.",
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
  adjustments: {
    title: "Adjustments",
    description:
      "How the optimiser adjusted the book relative to the base price: the quotes (or weight) "
      + "at each scenario value of the grid, where 1.0 is the base price with no adjustment. "
      + "A ratebook result is described at the grid step the solver evaluated for each quote. "
      + "It describes the solution only; nothing is compared with current pricing.",
  },
  segments: {
    title: "Segments",
    description:
      "Where the optimiser adjusted: for each level of an analysis column or rating factor, the "
      + "quotes and their mean chosen scenario value against 1.0, the base price with no "
      + "adjustment, with the shares adjusted up, down and at the edge of the scenario range. "
      + "It describes the solution only; nothing is compared with current pricing.",
  },
  quotes: {
    title: "Per-quote choices",
    description:
      "The scenario value the optimiser chose for each quote of the publish target, with its "
      + "expected objective and constraint values there. Sorting, the quote ID search and the "
      + "filters run over every quote; a ratebook result shows the step the solver evaluated, "
      + "the factor product and whether the deployed factor differs from that step.",
  },
  convergence: {
    title: "Convergence",
    description:
      `How the objective, each constraint total against its bound and the ${LAMBDA_LABEL} `
      + "settled over the solve's iterations, or over a ratebook solve's coordinate-descent "
      + "passes, one line per factor (λ in the values table).",
  },
  curves: {
    title: "Quote curves",
    description:
      "The optimiser's input for one quote at a time: the objective and each constraint across "
      + "the quote's scenarios, from the node's preview rows. Step through quotes or find one by ID.",
  },
  statistics: {
    title: "Scenario statistics",
    description:
      "The objective and each constraint at every scenario, summarised across the preview's "
      + "quotes: count, mean, spread and quantiles. It describes the input, not the solution.",
  },
}
