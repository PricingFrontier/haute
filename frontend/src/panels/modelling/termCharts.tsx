/**
 * Charts for additive model terms: a value per level, a step line over value
 * bins, and a surface over two axes. The EBM Terms tab and the t-boost Tables
 * tab draw their shape functions and tables with these. Values are compared
 * with a reference: 0 for link-scale scores, 1 for relativities.
 */
import { useState } from "react"
import { CHART_COLORS } from "../../theme/colors"
import {
  ChartSvg,
  ResponsiveChart,
  MODELLING_CHART_AXIS_FONT_SIZE as FONT,
  MODELLING_CHART_AXIS_TEXT_COLOR as TEXT,
  MODELLING_CHART_GRID_COLOR as GRID,
} from "./ChartScaffold"
import {
  chartAxisLabel,
  chartDomain,
  chartLabelIndices,
  chartTicks,
  formatChartNumber,
  formatChartTicks,
} from "../../utils/chartHelpers"

/** An axis to draw: its feature and one label per cell. */
export interface TermChartAxis {
  feature: string
  labels: string[]
}

interface ShapeProps {
  title: string
  labels: string[]
  values: number[]
  /** The value bars start from and the dashed reference line sits at. */
  reference: number
  /** What a value is called in hover text and detail ("Score", "Relativity"). */
  valueLabel: string
  /** Training mass per cell, shown beside each value when given. */
  mass?: number[]
  massLabel?: string
}

function detail(props: ShapeProps, index: number): string {
  const value = `${props.valueLabel}: ${props.values[index]}`
  if (!props.mass) return value
  return `${value}; ${props.massLabel ?? "Mass"}: ${props.mass[index]}`
}

/** One bar per level, drawn from the reference value. */
export function LevelBars(props: ShapeProps) {
  const { title, labels, values, reference, mass } = props
  return (
    <ResponsiveChart>
      {(width) => {
        const [low, high] = chartDomain([...values, reference], true)
        const barWidth = Math.max(1, width * 0.6 - 76)
        const x = (value: number) => 4 + ((value - low) / (high - low)) * (barWidth - 8)
        return (
          <div role="img" aria-label={title}>
            {labels.map((label, i) => (
              <div
                key={label}
                className="grid items-center gap-3 py-2"
                style={{
                  gridTemplateColumns: "minmax(0, 2fr) minmax(0, 3fr)",
                  borderBottom: "1px solid var(--border)",
                }}
                title={`${label}: ${detail(props, i)}`}
              >
                <span className="break-words text-[13px]" style={{ color: "var(--text-secondary)" }}>
                  {label}
                </span>
                <div className="flex min-w-0 items-center gap-2">
                  <svg width={barWidth} height={28} className="shrink-0" aria-hidden="true">
                    <line x1={x(reference)} x2={x(reference)} y1={2} y2={26} stroke={TEXT} />
                    <rect
                      x={Math.min(x(reference), x(values[i]))}
                      y={6}
                      width={Math.abs(x(values[i]) - x(reference))}
                      height={16}
                      rx={2}
                      fill={values[i] >= reference ? "var(--chart-above)" : "var(--chart-below)"}
                      opacity={0.8}
                    />
                  </svg>
                  <span className="text-xs tabular-nums" title={String(values[i])}>
                    {formatChartNumber(values[i])}
                  </span>
                  {mass && (
                    <span className="text-xs tabular-nums" style={{ color: TEXT }}>
                      {formatChartNumber(mass[i])}
                    </span>
                  )}
                </div>
              </div>
            ))}
          </div>
        )
      }}
    </ResponsiveChart>
  )
}

/**
 * A step line over ordered value bins. The first cell is the missing-value
 * cell, stated on its own above the curve.
 */
export function StepShape(props: ShapeProps) {
  const { title, reference, valueLabel } = props
  const [active, setActive] = useState<number | null>(null)
  const missing = props.values[0]
  const values = props.values.slice(1)
  const labels = props.labels.slice(1)
  const mass = props.mass?.slice(1)
  const shown = { ...props, values, labels, mass }
  return (
    <>
      <div className="mb-2 text-xs" style={{ color: TEXT }}>
        Missing values {valueLabel === "Score" ? "score" : `have ${valueLabel.toLowerCase()}`}{" "}
        {formatChartNumber(missing)}
        {props.mass && ` · ${props.massLabel ?? "Mass"} ${formatChartNumber(props.mass[0])}`}
      </div>
      <ResponsiveChart>
        {(width) => {
          const left = 68,
            right = 24,
            top = 24,
            bottom = 240,
            height = 280
          const plotWidth = Math.max(1, width - left - right)
          const [low, high] = chartDomain([...values, reference], true)
          const step = plotWidth / Math.max(1, values.length)
          const x = (index: number) => left + index * step
          const y = (value: number) => bottom - ((value - low) / (high - low)) * (bottom - top)
          const path = values
            .map((value, i) => `${i ? "L" : "M"}${x(i)},${y(value)} L${x(i + 1)},${y(value)}`)
            .join(" ")
          const indices = chartLabelIndices(values.length, plotWidth, 120)
          return (
            <ChartSvg width={width} height={height} ariaLabel={title}>
              {chartTicks(low, high).map((value, index, all) => (
                <g key={value}>
                  <line x1={left} x2={width - right} y1={y(value)} y2={y(value)} stroke={GRID} />
                  <text x={left - 8} y={y(value) + 4} textAnchor="end" fontSize={FONT} fill={TEXT}>
                    {formatChartTicks(all)[index]}
                  </text>
                </g>
              ))}
              <line
                x1={left}
                x2={width - right}
                y1={y(reference)}
                y2={y(reference)}
                stroke={TEXT}
                strokeDasharray="3 3"
              />
              <path d={path} fill="none" stroke={CHART_COLORS.predicted} strokeWidth={2} />
              {values.map((_, i) => (
                <rect
                  key={i}
                  x={x(i)}
                  y={top}
                  width={Math.max(1, step)}
                  height={bottom - top}
                  fill="transparent"
                  onMouseEnter={() => setActive(i)}
                >
                  <title>{`${labels[i]}: ${detail(shown, i)}`}</title>
                </rect>
              ))}
              {[...indices].map((i) => (
                <text key={i} x={x(i) + step / 2} y={bottom + 20} textAnchor="middle" fontSize={FONT} fill={TEXT}>
                  {chartAxisLabel(labels[i], 120)}
                </text>
              ))}
            </ChartSvg>
          )
        }}
      </ResponsiveChart>
      <div className="validation-bin-detail" role="status" aria-live="polite">
        {active !== null && (
          <>
            <strong>{labels[active]}</strong>
            <span>{detail(shown, active)}</span>
          </>
        )}
      </div>
    </>
  )
}

function surfaceColour(value: number, reference: number, extent: number): string {
  const offset = value - reference
  const share = extent === 0 ? 0 : Math.min(1, Math.abs(offset) / extent)
  const channel = offset >= 0 ? "var(--chart-above)" : "var(--chart-below)"
  return `color-mix(in srgb, ${channel} ${Math.round(share * 100)}%, transparent)`
}

/**
 * A value table over two axes, coloured by distance from the reference. By
 * default the longer axis runs down the table so a fine binning scrolls
 * vertically; ``fixedOrientation`` keeps ``first`` on the rows when the caller
 * lets the user choose them.
 */
export function SurfaceTable({
  title,
  first,
  second,
  grid,
  reference,
  mass,
  massLabel,
  fixedOrientation = false,
}: {
  title: string
  first: TermChartAxis
  second: TermChartAxis
  /** ``grid[i][j]`` is the value at ``first.labels[i]`` and ``second.labels[j]``. */
  grid: number[][]
  reference: number
  mass?: number[][]
  massLabel?: string
  fixedOrientation?: boolean
}) {
  const transpose = !fixedOrientation && second.labels.length > first.labels.length
  const rowsAxis = transpose ? second : first
  const columnsAxis = transpose ? first : second
  const flip = (table: number[][]) =>
    transpose ? second.labels.map((_, j) => table.map((row) => row[j])) : table
  const shown = flip(grid)
  const shownMass = mass ? flip(mass) : undefined
  const extent = Math.max(0, ...shown.flat().map((value) => Math.abs(value - reference)))
  return (
    <div className="overflow-x-auto">
      <table className="validation-value-table" aria-label={title} style={{ fontSize: 11 }}>
        <thead>
          <tr>
            <th scope="col">
              {rowsAxis.feature} \ {columnsAxis.feature}
            </th>
            {columnsAxis.labels.map((label) => (
              <th key={label} scope="col" title={label}>
                {chartAxisLabel(label, 70)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {shown.map((row, i) => (
            <tr key={rowsAxis.labels[i]}>
              <th scope="row">{rowsAxis.labels[i]}</th>
              {row.map((value, j) => (
                <td
                  key={j}
                  title={
                    `${rowsAxis.feature} ${rowsAxis.labels[i]}, ${columnsAxis.feature} `
                    + `${columnsAxis.labels[j]}: ${value}`
                    + (shownMass ? `; ${massLabel ?? "Mass"}: ${shownMass[i][j]}` : "")
                  }
                  style={{ background: surfaceColour(value, reference, extent), textAlign: "right" }}
                  className="tabular-nums"
                >
                  {formatChartNumber(value)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
