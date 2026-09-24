import type { ExploreHistogram } from "../../api/types"
import { NODE_GROUP_COLORS } from "../../theme/colors"
import { formatChartNumber } from "../../utils/chartHelpers"
import { distributionText } from "./distribution"

const WIDTH = 96
const HEIGHT = 22
const MUTED_STYLE = { color: "var(--text-muted)" } as const

/**
 * A numeric field's server-binned distribution as a small bar chart, or a word
 * for why there is none: no finite values, one repeated value, or a field past
 * the profile's histogram column limit.
 */
export default function DistributionSparkline({
  field,
  histogram,
}: {
  field: string
  histogram: ExploreHistogram | null | undefined
}) {
  if (!histogram || histogram.status !== "ok") {
    return (
      <span
        style={MUTED_STYLE}
        title={
          histogram?.skipped_reason === "column_limit"
            ? "Only the first numeric fields of a wide dataset are binned."
            : histogram?.skipped_reason === "integer_precision"
              ? "Its values are too large for the browser to show bin boundaries exactly."
              : undefined
        }
      >
        {distributionText(histogram)}
      </span>
    )
  }
  const tallest = Math.max(...histogram.bins.map((bin) => bin.count))
  const slot = WIDTH / histogram.bins.length
  const label = `Distribution of ${field}: ${distributionText(histogram)}`
  return (
    <svg
      width={WIDTH}
      height={HEIGHT}
      role="img"
      aria-label={label}
      data-testid="explore-distribution-sparkline"
      style={{ display: "block" }}
    >
      <title>{label}</title>
      {histogram.bins.map((bin, index) => {
        const height = tallest === 0 ? 0 : (bin.count / tallest) * (HEIGHT - 2)
        return (
          <rect
            key={index}
            data-testid="explore-distribution-bar"
            x={index * slot + 0.5}
            y={HEIGHT - height}
            width={Math.max(slot - 1, 1)}
            height={height}
            fill={NODE_GROUP_COLORS.explore}
            opacity={0.75}
          >
            <title>
              {`${formatChartNumber(bin.start)} to ${formatChartNumber(bin.end)}: ${bin.count.toLocaleString()}`}
            </title>
          </rect>
        )
      })}
    </svg>
  )
}
