/**
 * Tables and charts for additive model terms: a table or bars of a value per
 * level, a step line over value bins, and a table or lines over two axes.
 * TermTableView draws the EBM Terms tab's and the t-boost Tables tab's terms
 * with these. Values are compared with a reference: 0 for link-scale scores,
 * 1 for relativities.
 */
import { useState } from "react"
import { CHART_COLORS } from "../../theme/colors"
import {
  ChartLegend,
  ChartSvg,
  ChartValueGrid,
  ResponsiveChart,
  MODELLING_CHART_AXIS_FONT_SIZE as FONT,
  MODELLING_CHART_AXIS_TEXT_COLOR as TEXT,
  MODELLING_CHART_GRID_COLOR as GRID,
} from "./ChartScaffold"
import {
  chartAxisLabel,
  chartDomain,
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

/** The room an x-axis label takes: its axis's longest label, capped so one long label cannot crowd out the rest. */
function axisLabelWidth(labels: string[]): number {
  return Math.min(168, Math.max(0, ...labels.map((label) => label.length)) * 7 + 16)
}

/** The cells to label along an axis: every k-th, k the fewest slots a label spans, so labels never overlap. */
function labelledCells(count: number, step: number, labelWidth: number): number[] {
  const every = Math.max(1, Math.ceil(labelWidth / Math.max(1, step)))
  return Array.from({ length: Math.ceil(count / every) }, (_, i) => i * every)
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
        const [low, high] = chartDomain([...values, reference])
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
          const [low, high] = chartDomain([...values, reference])
          const step = plotWidth / Math.max(1, values.length)
          const x = (index: number) => left + index * step
          const y = (value: number) => bottom - ((value - low) / (high - low)) * (bottom - top)
          const path = values
            .map((value, i) => `${i ? "L" : "M"}${x(i)},${y(value)} L${x(i + 1)},${y(value)}`)
            .join(" ")
          const labelWidth = axisLabelWidth(labels)
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
              {labelledCells(values.length, step, labelWidth).map((i) => (
                <text key={i} x={x(i) + step / 2} y={bottom + 20} textAnchor="middle" fontSize={FONT} fill={TEXT}>
                  {chartAxisLabel(labels[i], labelWidth)}
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
 * One row per cell of a single axis: its value, coloured by distance from the
 * reference as a surface is, and its training mass when given.
 */
export function LevelTable(props: ShapeProps & { feature: string }) {
  const { title, feature, labels, values, reference, valueLabel, mass, massLabel } = props
  const extent = Math.max(0, ...values.map((value) => Math.abs(value - reference)))
  return (
    <div className="overflow-x-auto">
      <table className="validation-value-table term-table" aria-label={title}>
        <thead>
          <tr>
            <th scope="col">{feature}</th>
            <th scope="col">{valueLabel}</th>
            {mass && <th scope="col">{massLabel ?? "Mass"}</th>}
          </tr>
        </thead>
        <tbody>
          {labels.map((label, i) => (
            <tr key={label} title={`${label}: ${detail(props, i)}`}>
              <th scope="row">{label}</th>
              <td style={{ background: surfaceColour(values[i], reference, extent) }}>
                {formatChartNumber(values[i])}
              </td>
              {mass && <td style={{ color: TEXT }}>{formatChartNumber(mass[i])}</td>}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

/**
 * A two-axis table as lines: the cells of ``across`` along the x axis and one
 * line per cell of ``lines``. Each cell's value is flat over its slot; a
 * continuous axis joins its ranges into a step line, and its first cell, the
 * missing cell, stands apart as a nominal level does.
 */
export function InteractionLines({
  title,
  across,
  lines,
  grid,
  reference,
  valueLabel,
}: {
  title: string
  across: TermChartAxis & { continuous: boolean }
  lines: TermChartAxis
  /** ``grid[i][j]`` is the value at ``across.labels[i]`` and ``lines.labels[j]``. */
  grid: number[][]
  reference: number
  valueLabel: string
}) {
  const [active, setActive] = useState<number | null>(null)
  const colour = (line: number) => CHART_COLORS.optimiserSeries[line % CHART_COLORS.optimiserSeries.length]
  return (
    <>
      <ResponsiveChart>
        {(width) => {
          const left = 68,
            right = 24,
            top = 16,
            bottom = 240,
            height = 272
          const plotWidth = Math.max(1, width - left - right)
          const [low, high] = chartDomain([...grid.flat(), reference])
          const step = plotWidth / Math.max(1, across.labels.length)
          const gap = Math.min(step * 0.15, 12)
          const x = (index: number) => left + index * step
          const y = (value: number) => bottom - ((value - low) / (high - low)) * (bottom - top)
          // A continuous range after the first joins the one before it.
          const joined = (cell: number) => across.continuous && cell > 1
          const startGap = (cell: number) => (joined(cell) ? 0 : gap)
          const endGap = (cell: number) => (across.continuous && cell > 0 ? 0 : gap)
          const path = (line: number) =>
            grid
              .map((row, cell) => {
                const start = `${joined(cell) ? "L" : "M"}${x(cell) + startGap(cell)},${y(row[line])}`
                return `${start} L${x(cell + 1) - endGap(cell)},${y(row[line])}`
              })
              .join(" ")
          const labelWidth = axisLabelWidth(across.labels)
          return (
            <ChartSvg width={width} height={height} ariaLabel={title}>
              <ChartValueGrid ticks={chartTicks(low, high)} left={left} right={width - right} y={y} labelGap={8} />
              {active !== null && (
                <rect x={x(active)} y={top} width={step} height={bottom - top} fill={GRID} opacity={0.5} />
              )}
              <line
                x1={left}
                x2={width - right}
                y1={y(reference)}
                y2={y(reference)}
                stroke={TEXT}
                strokeDasharray="3 3"
              />
              {lines.labels.map((label, line) => (
                <path key={label} d={path(line)} fill="none" stroke={colour(line)} strokeWidth={2} />
              ))}
              {across.labels.map((label, cell) => (
                <rect
                  key={label}
                  x={x(cell)}
                  y={top}
                  width={Math.max(1, step)}
                  height={bottom - top}
                  fill="transparent"
                  onMouseEnter={() => setActive(cell)}
                >
                  <title>{label}</title>
                </rect>
              ))}
              {labelledCells(across.labels.length, step, labelWidth).map((cell) => (
                <text key={cell} x={x(cell) + step / 2} y={bottom + 20} textAnchor="middle" fontSize={FONT} fill={TEXT}>
                  {chartAxisLabel(across.labels[cell], labelWidth)}
                </text>
              ))}
            </ChartSvg>
          )
        }}
      </ResponsiveChart>
      <ChartLegend
        items={lines.labels.map((label, line) => ({ label: `${lines.feature} ${label}`, color: colour(line) }))}
        compact
      />
      <div className="validation-bin-detail" role="status" aria-live="polite">
        {active !== null && (
          <>
            <strong>
              {across.feature} {across.labels[active]}
            </strong>
            {lines.labels.map((label, line) => (
              <span key={label}>
                {`${lines.feature} ${label}: ${valueLabel} ${formatChartNumber(grid[active][line])}`}
              </span>
            ))}
          </>
        )}
      </div>
    </>
  )
}

/** A value table over two axes, ``rows`` down and ``columns`` across, coloured by distance from the reference. */
export function SurfaceTable({
  title,
  rows: rowsAxis,
  columns: columnsAxis,
  grid,
  reference,
  mass,
  massLabel,
}: {
  title: string
  rows: TermChartAxis
  columns: TermChartAxis
  /** ``grid[i][j]`` is the value at ``rows.labels[i]`` and ``columns.labels[j]``. */
  grid: number[][]
  reference: number
  mass?: number[][]
  massLabel?: string
}) {
  const extent = Math.max(0, ...grid.flat().map((value) => Math.abs(value - reference)))
  return (
    <div className="overflow-x-auto">
      <table className="validation-value-table term-table" aria-label={title}>
        <thead>
          <tr>
            <th scope="col">
              {rowsAxis.feature} \ {columnsAxis.feature}
            </th>
            {columnsAxis.labels.map((label) => (
              <th key={label} scope="col" title={label}>
                {chartAxisLabel(label, 168)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {grid.map((row, i) => (
            <tr key={rowsAxis.labels[i]}>
              <th scope="row" title={rowsAxis.labels[i]}>{rowsAxis.labels[i]}</th>
              {row.map((value, j) => (
                <td
                  key={j}
                  title={
                    `${rowsAxis.feature} ${rowsAxis.labels[i]}, ${columnsAxis.feature} `
                    + `${columnsAxis.labels[j]}: ${value}`
                    + (mass ? `; ${massLabel ?? "Mass"}: ${mass[i][j]}` : "")
                  }
                  style={{ background: surfaceColour(value, reference, extent) }}
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
