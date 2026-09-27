
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
