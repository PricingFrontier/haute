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
  TwoChartLayout,
} from "./ChartScaffold"
import HistogramChart from "../HistogramChart"
import { chartDomain, chartTicks, formatChartTicks } from "../../utils/chartHelpers"

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
    <TwoChartLayout
      width={width}
      ariaLabel="Residual validation charts"
      bothCharts={hasHistogram && hasScatter}
      sideBySideFrom={760}
      minChartWidth={280}
    >
      {({ chartWidth }) => (
        <>
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
        </>
      )}
    </TwoChartLayout>
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
  return (
    <div>
      <HistogramChart
        title="Residuals distribution"
        description="Weighted residual counts by bin"
        ariaLabel="Residuals distribution histogram"
        bars={data.map((bin, index) => ({ key: String(index), value: bin.weighted_count, center: bin.bin_center }))}
        axis={{ kind: "numeric" }}
        width={width}
        height={height}
        xLabel="Residual"
        yLabel="Weighted count"
        color={barColor}
        barTestId="residual-histogram-bar"
        reference={{ at: 0, color: zeroLineColor, label: "Zero residual" }}
        legend={[{ label: "Weighted count", color: barColor, swatch: "bar" }]}
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
              {formatChartTicks(all)[index]}
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
