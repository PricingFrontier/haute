import { useState } from "react"
import type { TrainResult } from "../../stores/useNodeResultsStore"
import { CHART_COLORS, MODEL_COLORS } from "../../theme/colors"
import {
  ChartEmptyState,
  ChartLegend,
  ChartSvg,
  ChartValueGrid,
  ChartValuesTable,
  MODELLING_CHART_AXIS_FONT_SIZE as axisFontSize,
  MODELLING_CHART_AXIS_TEXT_COLOR as axisTextColor,
  MODELLING_CHART_GRID_COLOR as gridColor,
  TwoChartLayout,
} from "./ChartScaffold"
import { chartLabelIndices, chartTicks, formatChartTicks } from "../../utils/chartHelpers"

interface LiftTabProps {
  result: TrainResult
  width?: number
  height?: number
}
type LiftPoint = { decile: number; actual: number; predicted: number; count: number }
type LorenzPoint = { cum_weight_frac: number; cum_actual_frac: number }
const actualColor = CHART_COLORS.actual
const predictedColor = CHART_COLORS.predicted
const referenceColor = "var(--text-muted)"

export function LiftTab({ result, width, height = 280 }: LiftTabProps) {
  const [view, setView] = useState<"lift" | "lorenz">("lift")
  const hasLift = Boolean(result.double_lift?.length)
  const hasLorenz = Boolean(result.lorenz_curve?.length)
  if (!hasLift && !hasLorenz) return <ChartEmptyState>No lift data available</ChartEmptyState>

  const bothCharts = hasLift && hasLorenz
  const selectedView = bothCharts ? view : hasLift ? "lift" : "lorenz"
  return (
    <TwoChartLayout
      width={width}
      ariaLabel="Lift validation charts"
      bothCharts={bothCharts}
      sideBySideFrom={900}
      minChartWidth={260}
      header={(sideBySide) =>
        !sideBySide && bothCharts ? <ViewSwitch view={selectedView} onChange={setView} /> : null
      }
    >
      {({ sideBySide, chartWidth }) => (
        <>
          {hasLift && (sideBySide || selectedView === "lift") && (
            <LiftPanel data={result.double_lift!} width={chartWidth} height={height} />
          )}
          {hasLorenz && (sideBySide || selectedView === "lorenz") && (
            <LorenzPanel
              curve={result.lorenz_curve!}
              perfectCurve={result.lorenz_curve_perfect}
              width={chartWidth}
              height={height}
            />
          )}
        </>
      )}
    </TwoChartLayout>
  )
}

function ViewSwitch({
  view,
  onChange,
}: {
  view: "lift" | "lorenz"
  onChange: (view: "lift" | "lorenz") => void
}) {
  const buttonStyle = (active: boolean) => ({
    background: active ? MODEL_COLORS.accentSoft : "var(--chrome-hover)",
    color: active ? MODEL_COLORS.accent : "var(--text-muted)",
  })
  return (
    <div className="flex gap-1" role="group" aria-label="Lift chart view">
      <button
        type="button"
        aria-pressed={view === "lift"}
        onClick={() => onChange("lift")}
        className="rounded px-2 py-0.5 text-[12px] font-medium"
        style={buttonStyle(view === "lift")}
      >
        Double lift
      </button>
      <button
        type="button"
        aria-pressed={view === "lorenz"}
        onClick={() => onChange("lorenz")}
        className="rounded px-2 py-0.5 text-[12px] font-medium"
        style={buttonStyle(view === "lorenz")}
      >
        Lorenz curve
      </button>
    </div>
  )
}

function LiftPanel({ data, width, height }: { data: LiftPoint[]; width: number; height: number }) {
  return (
    <div>
      <h4 className="text-[15px] font-medium" style={{ color: "var(--text-primary)" }}>
        Double lift
      </h4>
      <DoubleLiftChart data={data} width={width} height={height} />
      <ChartValuesTable
        summary="View lift values"
        ariaLabel="Lift values"
        headers={["Decile", "Actual", "Predicted", "Count"]}
        rows={data.map((row) => [
          row.decile,
          row.actual.toFixed(4),
          <span style={{ color: predictedColor }}>{row.predicted.toFixed(4)}</span>,
          row.count.toLocaleString(),
        ])}
      />
    </div>
  )
}

function DoubleLiftChart({
  data,
  width,
  height,
}: {
  data: LiftPoint[]
  width: number
  height: number
}) {
  const marginLeft = 68,
    marginRight = 24,
    marginTop = 16,
    marginBottom = 46
  const plotWidth = Math.max(1, width - marginLeft - marginRight),
    plotHeight = Math.max(1, height - marginTop - marginBottom)
  const values = data.flatMap((point) => [point.actual, point.predicted])
  const rawMin = Math.min(0, ...values),
    rawMax = Math.max(0, ...values)
  const yMin = rawMin === rawMax ? rawMin - 1 : rawMin * 1.1
  const yMax = rawMin === rawMax ? rawMax + 1 : rawMax * 1.1
  const ySpan = yMax - yMin
  const yScale = (value: number) => marginTop + plotHeight - ((value - yMin) / ySpan) * plotHeight
  const groupWidth = plotWidth / data.length,
    barWidth = groupWidth * 0.35,
    gap = groupWidth * 0.05
  const labelIndices = chartLabelIndices(data.length, plotWidth, 32)
  return (
    <>
      <ChartSvg width={width} height={height} ariaLabel="Double lift chart">
        <title>Double lift: actual and predicted values by prediction decile</title>
        <ChartValueGrid ticks={chartTicks(yMin, yMax, 5)} left={marginLeft} right={marginLeft + plotWidth} y={yScale} />
        {yMin < 0 && (
          <line
            x1={marginLeft}
            y1={yScale(0)}
            x2={marginLeft + plotWidth}
            y2={yScale(0)}
            stroke={referenceColor}
          />
        )}
        {data.map((point, index) => {
          const center = marginLeft + (index + 0.5) * groupWidth
          const actualY = point.actual >= 0 ? yScale(point.actual) : yScale(0)
          const predictedY = point.predicted >= 0 ? yScale(point.predicted) : yScale(0)
          return (
            <g key={point.decile}>
              <title>
                Decile {point.decile}: actual {point.actual}, predicted {point.predicted}, count{" "}
                {point.count}
              </title>
              <rect
                x={center - barWidth - gap / 2}
                y={actualY}
                width={barWidth}
                height={Math.abs(point.actual / ySpan) * plotHeight}
                fill={actualColor}
                opacity={0.7}
                rx={1}
              />
              <rect
                x={center + gap / 2}
                y={predictedY}
                width={barWidth}
                height={Math.abs(point.predicted / ySpan) * plotHeight}
                fill={predictedColor}
                opacity={0.7}
                rx={1}
              />
            </g>
          )
        })}
        {data.map(
          (point, index) =>
            labelIndices.has(index) && (
              <text
                key={point.decile}
                x={marginLeft + (index + 0.5) * groupWidth}
                y={marginTop + plotHeight + 15}
                textAnchor={index === 0 ? "start" : index === data.length - 1 ? "end" : "middle"}
                fontSize={axisFontSize}
                fill={axisTextColor}
              >
                {point.decile}
              </text>
            ),
        )}
        <text
          x={marginLeft + plotWidth / 2}
          y={height - 4}
          textAnchor="middle"
          fontSize={axisFontSize}
          fill={axisTextColor}
        >
          Prediction decile (low to high)
        </text>
        <text
          x={12}
          y={marginTop + plotHeight / 2}
          textAnchor="middle"
          fontSize={axisFontSize}
          fill={axisTextColor}
          transform={`rotate(-90,12,${marginTop + plotHeight / 2})`}
        >
          Average value
        </text>
      </ChartSvg>
      <ChartLegend
        items={[
          { label: "Actual", color: actualColor, swatch: "bar", opacity: 0.7 },
          { label: "Predicted", color: predictedColor, swatch: "bar", opacity: 0.7 },
          ...(yMin < 0 ? [{ label: "Zero", color: referenceColor }] : []),
        ]}
      />
    </>
  )
}

function LorenzPanel({
  curve,
  perfectCurve,
  width,
  height,
}: {
  curve: LorenzPoint[]
  perfectCurve?: LorenzPoint[]
  width: number
  height: number
}) {
  return (
    <div>
      <h4 className="text-[15px] font-medium" style={{ color: "var(--text-primary)" }}>
        Lorenz curve
      </h4>
      <LorenzChart curve={curve} perfectCurve={perfectCurve} width={width} height={height} />
    </div>
  )
}

function LorenzChart({
  curve,
  perfectCurve,
  width,
  height,
}: {
  curve: LorenzPoint[]
  perfectCurve?: LorenzPoint[]
  width: number
  height: number
}) {
  const marginLeft = 68,
    marginRight = 24,
    marginTop = 16,
    marginBottom = 40
  const plotWidth = Math.max(1, width - marginLeft - marginRight),
    plotHeight = Math.max(1, height - marginTop - marginBottom)
  const xScale = (value: number) => marginLeft + value * plotWidth,
    yScale = (value: number) => marginTop + plotHeight - value * plotHeight
  const makePath = (points: LorenzPoint[]) =>
    points
      .map(
        (point, index) =>
          `${index ? "L" : "M"}${xScale(point.cum_weight_frac).toFixed(1)},${yScale(point.cum_actual_frac).toFixed(1)}`,
      )
      .join(" ")
  const gini = computeGini(curve, perfectCurve),
    perfectPath = perfectCurve?.length ? makePath(perfectCurve) : null
  return (
    <>
      <ChartSvg width={width} height={height} ariaLabel="Lorenz curve">
        <title>Lorenz curve with Gini coefficient {gini.toFixed(4)}</title>
        <ChartValueGrid ticks={chartTicks(0, 1, 5)} left={marginLeft} right={marginLeft + plotWidth} y={yScale} />
        {chartTicks(0, 1, 5).map((value, index, all) => (
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
              textAnchor={value === 0 ? "start" : value === 1 ? "end" : "middle"}
              fontSize={axisFontSize}
              fill={axisTextColor}
            >
              {formatChartTicks(all)[index]}
            </text>
          </g>
        ))}
        <path
          d={`M${xScale(0)},${yScale(0)} ${curve.map((point) => `L${xScale(point.cum_weight_frac)},${yScale(point.cum_actual_frac)}`).join(" ")} L${xScale(1)},${yScale(1)} Z`}
          fill={predictedColor}
          opacity={0.08}
        />
        <line
          x1={xScale(0)}
          y1={yScale(0)}
          x2={xScale(1)}
          y2={yScale(1)}
          stroke={referenceColor}
          strokeDasharray="4,3"
        />
        {perfectPath && (
          <path d={perfectPath} fill="none" stroke={actualColor} strokeWidth={1.5} opacity={0.6} />
        )}
        <path d={makePath(curve)} fill="none" stroke={predictedColor} strokeWidth={1.5} />
        <text
          x={marginLeft + 8}
          y={marginTop + 16}
          fontSize={12}
          fontWeight="bold"
          fill={predictedColor}
        >
          Gini = {gini.toFixed(4)}
        </text>
        <text
          x={marginLeft + plotWidth / 2}
          y={height - 4}
          textAnchor="middle"
          fontSize={axisFontSize}
          fill={axisTextColor}
        >
          Cumulative weight fraction
        </text>
        <text
          x={12}
          y={marginTop + plotHeight / 2}
          textAnchor="middle"
          fontSize={axisFontSize}
          fill={axisTextColor}
          transform={`rotate(-90,12,${marginTop + plotHeight / 2})`}
        >
          Cumulative actual fraction
        </text>
      </ChartSvg>
      <ChartLegend
        items={[
          { label: "Model", color: predictedColor },
          ...(perfectPath ? [{ label: "Perfect model", color: actualColor, opacity: 0.6 }] : []),
          { label: "Random", color: referenceColor, swatch: "dashed" },
        ]}
      />
    </>
  )
}

function computeGini(curve: LorenzPoint[], perfectCurve?: LorenzPoint[]) {
  if (curve.length < 2) return 0
  const area = (points: LorenzPoint[]) =>
    points
      .slice(1)
      .reduce(
        (total, point, index) =>
          total +
          ((point.cum_weight_frac - points[index].cum_weight_frac) *
            (point.cum_actual_frac + points[index].cum_actual_frac)) /
            2,
        0,
      )
  const rawGini = 2 * area(curve) - 1
  if (!perfectCurve || perfectCurve.length < 2) return Number.isFinite(rawGini) ? rawGini : 0
  const perfectGini = 2 * area(perfectCurve) - 1
  return Number.isFinite(perfectGini) && perfectGini !== 0 && Number.isFinite(rawGini)
    ? rawGini / perfectGini
    : 0
}
