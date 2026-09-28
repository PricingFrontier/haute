/**
 * The shared histogram: a titled chart of bars on a zero-based value axis,
 * with x ticks, axis labels, an optional dashed reference line and a legend.
 *
 * Bars sit on a numeric axis at their `center` (Residuals' bins), or as evenly
 * spaced categories labelled by their `label` (the optimiser's scenario grid,
 * whose values need not be evenly spaced). With `interaction` every bar is
 * focusable and states its values through `describe`; the active bar is drawn
 * stronger. A non-finite bar value, or no bars at all, throws.
 */

import type { ReactNode } from "react"
import {
  ChartLegend,
  ChartSvg,
  ChartValueGrid,
  MODELLING_CHART_AXIS_FONT_SIZE as axisFontSize,
  MODELLING_CHART_AXIS_TEXT_COLOR as axisTextColor,
  type ChartLegendItem,
} from "./modelling/ChartScaffold"
import { chartLabelIndices, chartTicks, formatChartTicks } from "../utils/chartHelpers"

export type HistogramBar = {
  key: string
  /** The bar's height on the value axis. */
  value: number
  /** Numeric axis only: the bar's centre. */
  center?: number
  /** Categorical axis only: the bar's tick label. */
  label?: string
}

export type HistogramAxis = { kind: "numeric" } | { kind: "categorical" }

/** A dashed vertical line, drawn (and added to the legend) only when it lies on the axis. */
export type HistogramReference = {
  /** A value on a numeric axis, or a (fractional) bar position on a categorical one. */
  at: number
  color: string
  label: string
  testId?: string
}

export type HistogramInteraction = {
  activeKey: string | null
  onActivate: (key: string) => void
  /** The bar's accessible name: its exact values. */
  describe: (bar: HistogramBar) => string
}

const MARGIN = { left: 68, right: 24, top: 16, bottom: 42 }
const BAR_FILL_RATIO = 0.85
/** The narrowest spacing between two categorical tick labels. */
const CATEGORY_LABEL_WIDTH = 48

export default function HistogramChart({
  title,
  description,
  ariaLabel,
  bars,
  axis,
  width,
  height,
  xLabel,
  yLabel,
  color,
  barTestId,
  reference,
  legend,
  interaction,
}: {
  title: string
  description: string
  ariaLabel: string
  bars: readonly HistogramBar[]
  axis: HistogramAxis
  width: number
  height: number
  xLabel: string
  yLabel: string
  color: string
  barTestId: string
  reference?: HistogramReference | null
  legend: ChartLegendItem[]
  interaction?: HistogramInteraction
}): ReactNode {
  if (bars.length === 0) throw new Error(`${ariaLabel} has no bars`)
  for (const bar of bars) {
    if (!Number.isFinite(bar.value)) throw new Error(`${ariaLabel}: bar ${bar.key} has a non-finite value ${bar.value}`)
  }
  const plotWidth = Math.max(1, width - MARGIN.left - MARGIN.right)
  const plotHeight = Math.max(1, height - MARGIN.top - MARGIN.bottom)
  const centers = axis.kind === "numeric"
    ? bars.map((bar) => {
      if (bar.center === undefined || !Number.isFinite(bar.center)) {
        throw new Error(`${ariaLabel}: bar ${bar.key} has no finite centre on a numeric axis`)
      }
      return bar.center
    })
    : bars.map((_, index) => index)
  const sortedCenters = [...centers].sort((left, right) => left - right)
  const positiveSteps = sortedCenters
    .slice(1)
    .map((value, index) => value - sortedCenters[index])
    .filter((value) => value > 0)
  const binStep = positiveSteps.length ? Math.min(...positiveSteps) : 1
  const [xMin, xMax] = [Math.min(...centers) - binStep / 2, Math.max(...centers) + binStep / 2]
  const xSpan = xMax - xMin || 1
  const yMax = Math.max(0, ...bars.map((bar) => bar.value)) || 1
  const xScale = (value: number) => MARGIN.left + ((value - xMin) / xSpan) * plotWidth
  const yScale = (value: number) => MARGIN.top + plotHeight - (value / yMax) * plotHeight
  const barWidth = Math.min(
    plotWidth,
    Math.abs(xScale(centers[0] + binStep / 2) - xScale(centers[0] - binStep / 2)) * BAR_FILL_RATIO,
  )
  const slotWidth = barWidth / BAR_FILL_RATIO
  const tickY = MARGIN.top + plotHeight + 15
  const showReference = reference != null && reference.at >= xMin && reference.at <= xMax

  return (
    <div>
      <h4 className="text-[15px] font-medium" style={{ color: "var(--text-primary)" }}>
        {title}
      </h4>
      <div className="text-[12px]" style={{ color: "var(--text-muted)" }}>
        {description}
      </div>
      <ChartSvg width={width} height={height} className="mt-1" ariaLabel={ariaLabel}>
        <title>{ariaLabel}</title>
        <ChartValueGrid ticks={chartTicks(0, yMax, 5)} left={MARGIN.left} right={MARGIN.left + plotWidth} y={yScale} />
        {axis.kind === "numeric"
          ? chartTicks(xMin, xMax, width < 400 ? 3 : 5).map((value, index, all) => (
            <text
              key={value}
              x={xScale(value)}
              y={tickY}
              textAnchor={index === 0 ? "start" : index === all.length - 1 ? "end" : "middle"}
              fontSize={axisFontSize}
              fill={axisTextColor}
            >
              {formatChartTicks(all)[index]}
            </text>
          ))
          : (() => {
            const labelled = chartLabelIndices(bars.length, plotWidth, CATEGORY_LABEL_WIDTH)
            return bars.map((bar, index) => labelled.has(index) && (
              <text
                key={bar.key}
                x={xScale(index)}
                y={tickY}
                textAnchor="middle"
                fontSize={axisFontSize}
                fill={axisTextColor}
              >
                {bar.label}
              </text>
            ))
          })()}
        {bars.map((bar, index) => {
          const barHeight = (bar.value / yMax) * plotHeight
          const active = interaction?.activeKey === bar.key
          return (
            <rect
              key={bar.key}
              data-testid={barTestId}
              x={xScale(centers[index]) - barWidth / 2}
              y={MARGIN.top + plotHeight - barHeight}
              width={barWidth}
              height={barHeight}
              fill={color}
              opacity={active ? 0.9 : 0.6}
              rx={1}
            />
          )
        })}
        {showReference && (
          <line
            data-testid={reference.testId}
            x1={xScale(reference.at)}
            y1={MARGIN.top}
            x2={xScale(reference.at)}
            y2={MARGIN.top + plotHeight}
            stroke={reference.color}
            strokeDasharray="4,3"
          />
        )}
        {interaction && bars.map((bar, index) => {
          const activate = () => interaction.onActivate(bar.key)
          return (
            <rect
              key={`focus-${bar.key}`}
              x={xScale(centers[index]) - slotWidth / 2}
              y={MARGIN.top}
              width={slotWidth}
              height={plotHeight}
              fill="transparent"
              role="button"
              tabIndex={0}
              aria-label={interaction.describe(bar)}
              aria-pressed={interaction.activeKey === bar.key}
              onMouseEnter={activate}
              onFocus={activate}
              onClick={activate}
              onKeyDown={(event) => {
                if (event.key === "Enter" || event.key === " ") {
                  event.preventDefault()
                  activate()
                }
              }}
              style={{ outlineOffset: -2 }}
            />
          )
        })}
        <text
          x={MARGIN.left + plotWidth / 2}
          y={height - 4}
          textAnchor="middle"
          fontSize={axisFontSize}
          fill={axisTextColor}
        >
          {xLabel}
        </text>
        <text
          x={12}
          y={MARGIN.top + plotHeight / 2}
          textAnchor="middle"
          fontSize={axisFontSize}
          fill={axisTextColor}
          transform={`rotate(-90,12,${MARGIN.top + plotHeight / 2})`}
        >
          {yLabel}
        </text>
      </ChartSvg>
      <ChartLegend
        compact
        items={showReference ? [...legend, { label: reference.label, color: reference.color, dashed: true }] : legend}
      />
    </div>
  )
}
