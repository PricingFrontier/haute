import { useMemo } from "react"
import type { TrainResult } from "../../stores/useNodeResultsStore"
import { CHART_COLORS } from "../../theme/colors"
import {
  ChartEmptyState,
  ChartLegend,
  ChartSvg,
  ChartValueGrid,
  MODELLING_CHART_AXIS_FONT_SIZE as axisFontSize,
  MODELLING_CHART_AXIS_TEXT_COLOR as axisTextColor,
  MODELLING_CHART_GRID_COLOR as gridColor,
  ResponsiveChart,
} from "./ChartScaffold"
import { chartDomain, chartTicks, formatChartNumber } from "../../utils/chartHelpers"

interface ResidualsTabProps {
  result: TrainResult
  width?: number
  height?: number
}
type HistogramBin = { bin_center: number; count: number; weighted_count: number }
type ScatterPoint = { actual: number; predicted: number; weight: number }
const barColor = CHART_COLORS.predicted
const zeroLineColor = CHART_COLORS.residualZero
const scatterColor = CHART_COLORS.predicted
const referenceColor = "var(--text-muted)"

export function ResidualsTab({ result, width, height = 280 }: ResidualsTabProps) {
  const hasHistogram = Boolean(result.residuals_histogram?.length)
  const hasScatter = Boolean(result.actual_vs_predicted?.length)
  if (!hasHistogram && !hasScatter)
    return <ChartEmptyState>No residuals data available</ChartEmptyState>
  return (
    <ResponsiveChart width={width}>
      {(containerWidth) => {
        const twoColumns = containerWidth >= 760 && hasHistogram && hasScatter
        const chartWidth = twoColumns ? Math.max(280, (containerWidth - 24) / 2) : containerWidth
        return (
          <section
            className={twoColumns ? "grid grid-cols-2 gap-6" : "space-y-6"}
            aria-label="Residual validation charts"
          >
            {hasHistogram && (
              <ResidualsHistogram
                data={result.residuals_histogram!}
                stats={result.residuals_stats}
                width={chartWidth}
                height={height}
              />
            )}
            {hasScatter && (
              <ActualVsPredictedScatter
                data={result.actual_vs_predicted!}
                width={chartWidth}
                height={height}
              />
            )}
          </section>
        )
      }}
    </ResponsiveChart>
  )
}

function ResidualsHistogram({
  data,
  stats,
  width,
  height,
}: {
  data: HistogramBin[]
  stats?: Record<string, number>
  width: number
  height: number
}) {
  const marginLeft = 68,
    marginRight = 24,
    marginTop = 16,
    marginBottom = 42
  const plotWidth = Math.max(1, width - marginLeft - marginRight),
    plotHeight = Math.max(1, height - marginTop - marginBottom)
  const centers = data.map((bin) => bin.bin_center),
    sortedCenters = [...centers].sort((left, right) => left - right)
  const positiveSteps = sortedCenters
    .slice(1)
    .map((value, index) => value - sortedCenters[index])
    .filter((value) => value > 0)
  const binStep = positiveSteps.length ? Math.min(...positiveSteps) : 1
  const [xMin, xMax] = [Math.min(...centers) - binStep / 2, Math.max(...centers) + binStep / 2]
  const xSpan = xMax - xMin || 1
  const maxCount = Math.max(0, ...data.map((bin) => bin.weighted_count))
  const yMax = maxCount || 1
  const xScale = (value: number) => marginLeft + ((value - xMin) / xSpan) * plotWidth
  const yScale = (value: number) => marginTop + plotHeight - (value / yMax) * plotHeight
  const barWidth = Math.min(
    plotWidth,
    Math.abs(xScale(centers[0] + binStep / 2) - xScale(centers[0] - binStep / 2)) * 0.85,
  )
  const tickCount = width < 400 ? 3 : 5
  const hasZeroReference = xMin <= 0 && xMax >= 0
  return (
    <div>
      <h4 className="text-[15px] font-medium" style={{ color: "var(--text-primary)" }}>
        Residuals distribution
      </h4>
      <div className="text-[12px]" style={{ color: "var(--text-muted)" }}>
        Weighted residual counts by bin
      </div>
      <ChartSvg
        width={width}
        height={height}
        className="mt-1"
        ariaLabel="Residuals distribution histogram"
      >
        <title>Residuals distribution histogram</title>
        <ChartValueGrid ticks={chartTicks(0, yMax, 5)} left={marginLeft} right={marginLeft + plotWidth} y={yScale} />
        {chartTicks(xMin, xMax, tickCount).map((value, index, all) => (
          <text
            key={value}
            x={xScale(value)}
            y={marginTop + plotHeight + 15}
            textAnchor={index === 0 ? "start" : index === all.length - 1 ? "end" : "middle"}
            fontSize={axisFontSize}
            fill={axisTextColor}
          >
            {formatChartNumber(value)}
          </text>
        ))}
        {data.map((bin, index) => {
          const barHeight = (bin.weighted_count / yMax) * plotHeight
          return (
            <rect
              key={index}
              data-testid="residual-histogram-bar"
              x={xScale(bin.bin_center) - barWidth / 2}
              y={marginTop + plotHeight - barHeight}
              width={barWidth}
              height={barHeight}
              fill={barColor}
              opacity={0.6}
              rx={1}
            />
          )
        })}
        {hasZeroReference && (
          <line
            x1={xScale(0)}
            y1={marginTop}
            x2={xScale(0)}
            y2={marginTop + plotHeight}
            stroke={zeroLineColor}
            strokeDasharray="4,3"
          />
        )}
        <text
          x={marginLeft + plotWidth / 2}
          y={height - 4}
          textAnchor="middle"
          fontSize={axisFontSize}
          fill={axisTextColor}
        >
          Residual
        </text>
        <text
          x={12}
          y={marginTop + plotHeight / 2}
          textAnchor="middle"
          fontSize={axisFontSize}
          fill={axisTextColor}
          transform={`rotate(-90,12,${marginTop + plotHeight / 2})`}
        >
          Weighted count
        </text>
      </ChartSvg>
      <ChartLegend
        compact
        items={[
          { label: "Weighted count", color: barColor, swatch: "bar" },
          ...(hasZeroReference
            ? [{ label: "Zero residual", color: zeroLineColor, dashed: true }]
            : []),
        ]}
      />
      {stats && <StatsRow stats={stats} />}
    </div>
  )
}

function StatsRow({ stats }: { stats: Record<string, number> }) {
  const entries = (
    [
      ["mean", "Mean"],
      ["std", "Std"],
      ["skew", "Skew"],
    ] as const
  ).filter(([name]) => Number.isFinite(stats[name]))
  if (!entries.length) return null
  return (
    <div
      className="mt-1.5 flex flex-wrap gap-x-3 gap-y-1 text-[12px] font-mono"
      style={{ color: "var(--text-muted)" }}
    >
      {entries.map(([name, label]) => (
        <span key={name}>
          {label}: <span style={{ color: "var(--text-primary)" }}>{stats[name].toFixed(4)}</span>
        </span>
      ))}
    </div>
  )
}

function ActualVsPredictedScatter({
  data,
  width,
  height,
}: {
  data: ScatterPoint[]
  width: number
  height: number
}) {
  const marginLeft = 68,
    marginRight = 24,
    marginTop = 16,
    marginBottom = 42
  const plotWidth = Math.max(1, width - marginLeft - marginRight),
    plotHeight = Math.max(1, height - marginTop - marginBottom)
  const points = useMemo(
    () =>
      data.length <= 2000
        ? data
        : Array.from(
            { length: 2000 },
            (_, index) => data[Math.floor((index * data.length) / 2000)],
          ),
    [data],
  )
  const [domainLow, domainHigh] = chartDomain(
    points.flatMap((point) => [point.actual, point.predicted]),
  )
  const domainSpan = domainHigh - domainLow
  const xScale = (value: number) => marginLeft + ((value - domainLow) / domainSpan) * plotWidth
  const yScale = (value: number) =>
    marginTop + plotHeight - ((value - domainLow) / domainSpan) * plotHeight
  const tickCount = width < 400 ? 3 : 5
  return (
    <div>
      <h4 className="text-[15px] font-medium" style={{ color: "var(--text-primary)" }}>
        Actual vs predicted
      </h4>
      <div className="text-[12px]" style={{ color: "var(--text-muted)" }}>
        Observed values compared with predictions
      </div>
      <ChartSvg
        width={width}
        height={height}
        className="mt-1"
        ariaLabel="Actual versus predicted scatter plot"
      >
        <title>Actual versus predicted scatter plot; identity line marks equal values</title>
        <ChartValueGrid ticks={chartTicks(domainLow, domainHigh, tickCount)} left={marginLeft} right={marginLeft + plotWidth} y={yScale} />
        {chartTicks(domainLow, domainHigh, tickCount).map((value, index, all) => (
          <g key={value}>
            <line
              x1={xScale(value)}
              y1={marginTop}
              x2={xScale(value)}
              y2={marginTop + plotHeight}
              stroke={gridColor}
            />
            <text
              x={xScale(value)}
              y={marginTop + plotHeight + 15}
              textAnchor={index === 0 ? "start" : index === all.length - 1 ? "end" : "middle"}
              fontSize={axisFontSize}
              fill={axisTextColor}
            >
              {formatChartNumber(value)}
            </text>
          </g>
        ))}
        <line
          x1={xScale(domainLow)}
          y1={yScale(domainLow)}
          x2={xScale(domainHigh)}
          y2={yScale(domainHigh)}
          stroke={referenceColor}
          strokeDasharray="4,3"
        />
        <title>Identity line: actual equals predicted</title>
        {points.map((point, index) => (
          <circle
            key={index}
            cx={xScale(point.actual)}
            cy={yScale(point.predicted)}
            r={2}
            fill={scatterColor}
            opacity={0.4}
          />
        ))}
        <text
          x={marginLeft + plotWidth / 2}
          y={height - 4}
          textAnchor="middle"
          fontSize={axisFontSize}
          fill={axisTextColor}
        >
          Actual
        </text>
        <text
          x={12}
          y={marginTop + plotHeight / 2}
          textAnchor="middle"
          fontSize={axisFontSize}
          fill={axisTextColor}
          transform={`rotate(-90,12,${marginTop + plotHeight / 2})`}
        >
          Predicted
        </text>
      </ChartSvg>
      <ChartLegend
        compact
        items={[
          { label: "Predictions", color: scatterColor },
          { label: "Identity (actual = predicted)", color: referenceColor, swatch: "dashed" },
        ]}
      />
      <div className="mt-1 text-[12px]" style={{ color: "var(--text-muted)" }}>
        {data.length > 2000
          ? `Showing 2,000 of ${data.length.toLocaleString()} points (sampled)`
          : `${data.length.toLocaleString()} points`}
      </div>
    </div>
  )
}
