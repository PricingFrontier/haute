import type { ExploreHistogram } from "../../api/types"
import { formatChartNumber } from "../../utils/chartHelpers"

/**
 * What a numeric field's distribution cell says, for its accessible name and
 * for the Numeric Summary export: the bins' span, or why there are none.
 */
export function distributionText(histogram: ExploreHistogram | null | undefined): string {
  if (!histogram) return "-"
  switch (histogram.status) {
    case "skipped":
      return "Not binned"
    case "empty":
      return "No finite values"
    case "constant":
      return `All ${formatChartNumber(histogram.bins[0].start)}`
    case "ok": {
      const first = histogram.bins[0]
      const last = histogram.bins[histogram.bins.length - 1]
      return `${histogram.bins.length} bins, ${formatChartNumber(first.start)} to ${formatChartNumber(last.end)}`
    }
  }
}
