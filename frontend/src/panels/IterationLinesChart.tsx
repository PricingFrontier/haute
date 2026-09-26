/**
 * The shared by-iteration line chart: aligned series on a real linear or log
 * value axis, with optional dashed reference lines (a bound, say) and a dashed
 * vertical marker at one index. The modelling Loss tab and the optimiser's
 * Convergence tab are its adapters; it draws at the width it is given.
 *
 * Values are drawn as given: a `null` is a gap the line bridges, and anything
 * else that cannot be drawn (a non-finite value, or a value at or below 0 on a
 * log axis) throws, so the adapter decides how such values are shown.
 */
import {
  ChartLegend,
  ChartSvg,
  ChartValueGrid,
  MODELLING_CHART_AXIS_FONT_SIZE,
  MODELLING_CHART_AXIS_TEXT_COLOR,
  MODELLING_CHART_GRID_COLOR,
  type ChartLegendItem,
} from "./modelling/ChartScaffold"
import { chartDomain, chartLabelIndices, chartTicks, decade } from "../utils/chartHelpers"

export type IterationLineSeries = {
  label: string
  color: string
  /** One value per x index; `null` is a gap. */
  values: readonly (number | null)[]
}

export type IterationReferenceLine = { label: string; value: number; color: string }

export type IterationMarker = { index: number; label: string; color: string }

export interface IterationLinesChartProps {
  /** The iteration value each index is labelled with on the x axis. */
  x: readonly number[]
  series: readonly IterationLineSeries[]
  width: number
  height: number
  title: string
  ariaLabel: string
  xLabel: string
  yLabel: string
  yScale?: "linear" | "log"
  referenceLines?: readonly IterationReferenceLine[]
  marker?: IterationMarker | null
  /** Mark every point, for short series; a lone point is always marked. */
  showPoints?: boolean
}

const MARGIN = { left: 60, right: 16, top: 16, bottom: 36 }
const DASH = "5,3"
const LINEAR_TICKS = 5
const MAX_LOG_TICKS = 6

type ValueAxis = { ticks: number[]; position: (value: number) => number }

function requireDrawable(value: number, what: string, log: boolean): void {
  if (!Number.isFinite(value)) throw new Error(`${what} must be finite to draw, got ${value}`)
  if (log && value <= 0) throw new Error(`A log axis cannot draw ${what}: ${value} is not above 0`)
}

/** Decades that hold every value; one value gets a decade either side. */
function logAxis(values: number[], top: number, plotHeight: number): ValueAxis {
  const exponents = values.map(Math.log10)
  let low = Math.floor(Math.min(...exponents) + 1e-9)
  let high = Math.ceil(Math.max(...exponents) - 1e-9)
  if (low === high) {
    low -= 1
    high += 1
  }
  const step = Math.ceil((high - low + 1) / MAX_LOG_TICKS)
  const ticks: number[] = []
  for (let exponent = low; exponent <= high; exponent += step) ticks.push(decade(exponent))
  return {
    ticks,
    position: (value) => top + plotHeight - ((Math.log10(value) - low) / (high - low)) * plotHeight,
  }
}

function linearAxis(values: number[], top: number, plotHeight: number): ValueAxis {
  const [low, high] = chartDomain(values)
  return {
    ticks: chartTicks(low, high, LINEAR_TICKS),
    position: (value) => top + plotHeight - ((value - low) / (high - low)) * plotHeight,
  }
}

export function IterationLinesChart({
  x,
  series,
  width,
  height,
  title,
  ariaLabel,
  xLabel,
  yLabel,
  yScale = "linear",
  referenceLines = [],
  marker = null,
  showPoints = false,
}: IterationLinesChartProps) {
  const log = yScale === "log"
  if (x.length === 0) throw new Error(`${title} has no iterations to draw`)
  const values: number[] = []
  for (const line of series) {
    if (line.values.length !== x.length) {
      throw new Error(`${line.label} has ${line.values.length} values for ${x.length} iterations`)
    }
    for (const value of line.values) {
      if (value === null) continue
      requireDrawable(value, `${line.label}'s value`, log)
      values.push(value)
    }
  }
  for (const reference of referenceLines) {
    requireDrawable(reference.value, `the ${reference.label} line`, log)
    values.push(reference.value)
  }
  if (values.length === 0) throw new Error(`${title} has no values to draw`)
  if (marker && (marker.index < 0 || marker.index >= x.length || !Number.isInteger(marker.index))) {
    throw new Error(`${marker.label} is at index ${marker.index}, outside ${x.length} iterations`)
  }

  const plotWidth = width - MARGIN.left - MARGIN.right
  const plotHeight = height - MARGIN.top - MARGIN.bottom
  const plotBottom = MARGIN.top + plotHeight
  const axis = (log ? logAxis : linearAxis)(values, MARGIN.top, plotHeight)
  const xPosition = (index: number) =>
    x.length === 1 ? MARGIN.left + plotWidth / 2 : MARGIN.left + (index / (x.length - 1)) * plotWidth
  const xTicks = [...chartLabelIndices(x.length, plotWidth, 60)]

  const legend: ChartLegendItem[] = [
    ...series.map((line) => ({ label: line.label, color: line.color })),
    ...referenceLines.map((reference) => ({ label: reference.label, color: reference.color, dashed: true })),
    ...(marker ? [{ label: marker.label, color: marker.color, dashed: true }] : []),
  ]

  return (
    <div>
      <ChartSvg width={width} height={height} ariaLabel={ariaLabel}>
        <title>{title}</title>
        <ChartValueGrid ticks={axis.ticks} left={MARGIN.left} right={MARGIN.left + plotWidth} y={axis.position} />
        {xTicks.map((index) => (
          <g key={index} data-testid="chart-x-tick">
            <line
              x1={xPosition(index)}
              y1={MARGIN.top}
              x2={xPosition(index)}
              y2={plotBottom}
              stroke={MODELLING_CHART_GRID_COLOR}
            />
            <text
              x={xPosition(index)}
              y={plotBottom + 16}
              textAnchor="middle"
              fontSize={MODELLING_CHART_AXIS_FONT_SIZE}
              fill={MODELLING_CHART_AXIS_TEXT_COLOR}
            >
              {x[index]}
            </text>
          </g>
        ))}
        <text
          x={MARGIN.left + plotWidth / 2}
          y={height - 4}
          textAnchor="middle"
          fontSize={MODELLING_CHART_AXIS_FONT_SIZE}
          fill={MODELLING_CHART_AXIS_TEXT_COLOR}
        >
          {xLabel}
        </text>
        <text
          x={12}
          y={MARGIN.top + plotHeight / 2}
          textAnchor="middle"
          fontSize={MODELLING_CHART_AXIS_FONT_SIZE}
          fill={MODELLING_CHART_AXIS_TEXT_COLOR}
          transform={`rotate(-90,12,${MARGIN.top + plotHeight / 2})`}
        >
          {yLabel}
        </text>
        {referenceLines.map((reference) => (
          <line
            key={reference.label}
            data-reference={reference.label}
            x1={MARGIN.left}
            y1={axis.position(reference.value)}
            x2={MARGIN.left + plotWidth}
            y2={axis.position(reference.value)}
            stroke={reference.color}
            strokeWidth={1}
            strokeDasharray={DASH}
          />
        ))}
        {marker && (
          <line
            data-marker={marker.label}
            x1={xPosition(marker.index)}
            y1={MARGIN.top}
            x2={xPosition(marker.index)}
            y2={plotBottom}
            stroke={marker.color}
            strokeWidth={1}
            strokeDasharray={DASH}
          />
        )}
        {series.map((line) => {
          const points = line.values.flatMap((value, index) =>
            value === null ? [] : [{ x: xPosition(index), y: axis.position(value) }],
          )
          return (
            <g key={line.label}>
              <path
                data-series={line.label}
                d={points.map((point, index) => `${index === 0 ? "M" : "L"}${point.x.toFixed(1)},${point.y.toFixed(1)}`).join(" ")}
                fill="none"
                stroke={line.color}
                strokeWidth={1.5}
              />
              {(showPoints || points.length === 1) && points.map((point, index) => (
                <circle key={index} data-series={line.label} cx={point.x} cy={point.y} r={3} fill={line.color} />
              ))}
            </g>
          )
        })}
      </ChartSvg>
      <ChartLegend items={legend} />
    </div>
  )
}
