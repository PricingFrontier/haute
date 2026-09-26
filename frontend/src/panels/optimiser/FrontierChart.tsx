/**
 * The efficient frontier, one slice at a time.
 *
 * Draws the slice's points (objective against the x constraint's achieved
 * total) at the container's width on the shared chart primitives, with 12 px
 * axes named by the objective column and the constraint. Points arrive in
 * ascending x bound with their **global** index, which every label, click and
 * key press uses. Only feasible points (converged, every bound met) are joined:
 * an infeasible point breaks the line. A non-converged point is hollow and a
 * converged-but-breached point a cross; both stay selectable. Points at the
 * same pixel share one focusable marker that says how many it holds. The
 * as-solved anchor is decorative and always drawn, hollow when the solve lies
 * off the displayed slice.
 */

import { useState } from "react"
import { CHART_COLORS } from "../../theme/colors"
import {
  chartAxisLabel,
  chartDomain,
  chartTicks,
  formatChartTicks,
} from "../../utils/chartHelpers"
import ChartFocusDetail from "../ChartFocusDetail"
import {
  ChartLegend,
  ChartSvg,
  ChartValueGrid,
  MODELLING_CHART_AXIS_FONT_SIZE as FONT,
  MODELLING_CHART_AXIS_TEXT_COLOR as TEXT,
  ResponsiveChart,
  type ChartLegendItem,
} from "../modelling/ChartScaffold"
import type { FrontierPointStatus } from "./frontierSlices"

/** The plot's pixel frame inside the chart; the width follows the container. */
export const FRONTIER_CHART_LAYOUT = {
  left: 72,
  right: 20,
  top: 28,
  plotBottom: 232,
  height: 276,
} as const

const POINT_COLOR = "var(--accent)"
const SELECTED_COLOR = CHART_COLORS.objective
const BREACHED_COLOR = "var(--danger)"

const STATUS_WORDS: Record<FrontierPointStatus, string> = {
  feasible: "Feasible",
  not_converged: "Not converged",
  breached: "Breached",
}

export interface FrontierChartPoint {
  /** The point's global index in the frontier. */
  index: number
  /** The x constraint's achieved total. */
  x: number
  y: number
  status: FrontierPointStatus
}

interface FrontierChartProps {
  /** One slice's points in ascending x bound. */
  points: FrontierChartPoint[]
  xLabel: string
  yLabel: string
  selectedIdx: number | null
  /** The solve's position; `onSlice` is whether it was solved at this slice's held bounds. */
  asSolved: { x: number; y: number; onSlice: boolean }
  onPointClick: (index: number) => void
  /** A fixed width instead of the container's (tests and static renders). */
  width?: number
}

function requireFinite(value: number, what: string): number {
  if (!Number.isFinite(value)) throw new Error(`Frontier chart needs a finite ${what}, got ${String(value)}`)
  return value
}

type Bucket = FrontierChartPoint & { cx: number; cy: number; isSelected: boolean; overlapCount: number }

/**
 * One marker per pixel coordinate. The visible member prefers global point 2
 * (the point the canvas-assurance walk selects), then the selected point, then
 * any later point, so a selected duplicate still lights its shared marker.
 */
function bucketPoints(
  points: FrontierChartPoint[],
  xScale: (v: number) => number,
  yScale: (v: number) => number,
  selectedIdx: number | null,
): Bucket[] {
  const buckets = new Map<string, (FrontierChartPoint & { cx: number; cy: number })[]>()
  for (const point of points) {
    const cx = xScale(point.x)
    const cy = yScale(point.y)
    const key = `${cx.toFixed(3)}:${cy.toFixed(3)}`
    const bucket = buckets.get(key)
    if (bucket) bucket.push({ ...point, cx, cy })
    else buckets.set(key, [{ ...point, cx, cy }])
  }
  return Array.from(buckets.values()).map((bucket) => {
    const selected = selectedIdx == null ? undefined : bucket.find((p) => p.index === selectedIdx)
    const visible = bucket.find((p) => p.index === 1) ?? selected ?? bucket.find((p) => p.index > 0) ?? bucket[0]
    return { ...visible, isSelected: selected != null, overlapCount: bucket.length }
  })
}

/** Runs of consecutive feasible points, each drawn as one polyline. */
function feasibleLine(points: FrontierChartPoint[], xScale: (v: number) => number, yScale: (v: number) => number): string {
  const commands: string[] = []
  let run: FrontierChartPoint[] = []
  const flush = () => {
    if (run.length >= 2) {
      run.forEach((p, i) => commands.push(`${i === 0 ? "M" : "L"}${xScale(p.x)},${yScale(p.y)}`))
    }
    run = []
  }
  for (const point of points) {
    if (point.status === "feasible") run.push(point)
    else flush()
  }
  flush()
  return commands.join(" ")
}

function PointMarker({ bucket, onPointClick, onActivate }: {
  bucket: Bucket
  onPointClick: (index: number) => void
  onActivate: (index: number) => void
}) {
  const { index, cx, cy, status, isSelected, overlapCount } = bucket
  const r = isSelected ? 6 : 4
  const notes = [
    status === "feasible" ? null : status === "not_converged" ? "not converged" : "breached",
    overlapCount > 1 ? `${overlapCount} overlapping frontier points` : null,
  ].filter(Boolean)
  const label = `Select frontier point ${index + 1}${notes.length ? ` (${notes.join("; ")})` : ""}`
  const color = isSelected ? SELECTED_COLOR : POINT_COLOR
  const paint = status === "feasible"
    ? { fill: color, stroke: isSelected ? "var(--text-on-accent)" : "none", strokeWidth: isSelected ? 2 : 0 }
    : status === "not_converged"
      ? { fill: "transparent", stroke: color, strokeWidth: isSelected ? 2.5 : 1.5 }
      : { fill: "transparent", stroke: "none", strokeWidth: 0 }
  const arm = r - 1
  return (
    <g>
      {status === "breached" && (
        <path
          data-marker="cross"
          d={`M${cx - arm},${cy - arm} L${cx + arm},${cy + arm} M${cx + arm},${cy - arm} L${cx - arm},${cy + arm}`}
          stroke={isSelected ? SELECTED_COLOR : BREACHED_COLOR}
          strokeWidth={isSelected ? 2.5 : 2}
          strokeLinecap="round"
          pointerEvents="none"
        />
      )}
      <circle
        cx={cx}
        cy={cy}
        r={r}
        {...paint}
        opacity={isSelected ? 1 : 0.85}
        style={{ cursor: "pointer" }}
        onClick={() => onPointClick(index)}
        onMouseEnter={() => onActivate(index)}
        onFocus={() => onActivate(index)}
        tabIndex={0}
        role="button"
        aria-label={label}
        data-status={status}
        data-overlap-count={overlapCount > 1 ? overlapCount : undefined}
        onKeyDown={(event) => {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault()
            onPointClick(index)
          }
        }}
      />
    </g>
  )
}

/** An axis name, truncated to the plot width with the full name as its tooltip. */
function AxisName({ x, y, label, plotWidth, anchor = "start" }: {
  x: number
  y: number
  label: string
  plotWidth: number
  anchor?: "start" | "middle"
}) {
  const shown = chartAxisLabel(label, plotWidth)
  return (
    <text x={x} y={y} textAnchor={anchor} fontSize={FONT} fill={TEXT} data-axis-name={label}>
      {shown === label ? label : (
        <>
          <title>{label}</title>
          {shown}
        </>
      )}
    </text>
  )
}

export default function FrontierChart({
  points,
  xLabel,
  yLabel,
  selectedIdx,
  asSolved,
  onPointClick,
  width,
}: FrontierChartProps) {
  const [activeIdx, setActiveIdx] = useState<number | null>(null)
  if (points.length === 0) throw new Error("Frontier chart needs at least one point")
  for (const point of points) {
    requireFinite(point.x, `x for point ${point.index + 1}`)
    requireFinite(point.y, `objective for point ${point.index + 1}`)
  }
  requireFinite(asSolved.x, "as-solved x")
  requireFinite(asSolved.y, "as-solved objective")

  const xs = [...points.map((p) => p.x), asSolved.x]
  const ys = [...points.map((p) => p.y), asSolved.y]
  const [xLow, xHigh] = chartDomain(xs)
  const [yLow, yHigh] = chartDomain(ys)
  const active = activeIdx === null ? undefined : points.find((p) => p.index === activeIdx)
  const statuses = new Set(points.map((p) => p.status))

  const legend: ChartLegendItem[] = [
    { label: "Frontier point", color: POINT_COLOR, marker: "dot" },
    { label: asSolved.onSlice ? "As solved" : "As solved (different slice)", color: SELECTED_COLOR, marker: "ring" },
    { label: "Selected point", color: SELECTED_COLOR, marker: "dot" },
  ]
  if (statuses.has("not_converged")) legend.push({ label: "Not converged", color: POINT_COLOR, marker: "hollow" })
  if (statuses.has("breached")) legend.push({ label: "Breached", color: BREACHED_COLOR, marker: "cross" })

  return (
    <div>
      <ChartLegend items={legend} compact />
      <ResponsiveChart width={width}>
        {(chartWidth) => {
          const { left, right, top, plotBottom, height } = FRONTIER_CHART_LAYOUT
          const plotRight = chartWidth - right
          const plotWidth = Math.max(1, plotRight - left)
          const xScale = (v: number) => left + ((v - xLow) / (xHigh - xLow)) * plotWidth
          const yScale = (v: number) => plotBottom - ((v - yLow) / (yHigh - yLow)) * (plotBottom - top)
          const xTickCount = Math.max(2, Math.min(5, Math.floor(plotWidth / 90)))
          const xTicks = chartTicks(Math.min(...xs), Math.max(...xs), xTickCount)
          const xLabels = formatChartTicks(xTicks)
          const yTicks = chartTicks(Math.min(...ys), Math.max(...ys), 5)
          const line = feasibleLine(points, xScale, yScale)
          const solvedX = xScale(asSolved.x)
          const solvedY = yScale(asSolved.y)
          return (
            <ChartSvg
              width={chartWidth}
              height={height}
              role="group"
              ariaLabel={`Efficient frontier: ${yLabel} against ${xLabel}`}
            >
              <AxisName x={left} y={14} label={yLabel} plotWidth={plotWidth} />
              <ChartValueGrid ticks={yTicks} left={left} right={plotRight} y={yScale} labelGap={8} />
              {xTicks.map((tick, i) => (
                <text
                  key={`x-${tick}`}
                  x={xScale(tick)}
                  y={plotBottom + 20}
                  textAnchor={xTicks.length > 1 && i === 0 ? "start" : xTicks.length > 1 && i === xTicks.length - 1 ? "end" : "middle"}
                  fontSize={FONT}
                  fill={TEXT}
                >
                  {xLabels[i]}
                </text>
              ))}
              <AxisName x={left + plotWidth / 2} y={height - 6} label={xLabel} plotWidth={plotWidth} anchor="middle" />
              {line && (
                <path
                  data-series="frontier-line"
                  d={line}
                  fill="none"
                  stroke={POINT_COLOR}
                  strokeWidth={1.5}
                  opacity={0.6}
                  pointerEvents="none"
                />
              )}
              {bucketPoints(points, xScale, yScale, selectedIdx).map((bucket) => (
                <PointMarker key={bucket.index} bucket={bucket} onPointClick={onPointClick} onActivate={setActiveIdx} />
              ))}
              <g
                data-testid="frontier-as-solved-marker"
                data-on-slice={asSolved.onSlice ? "true" : "false"}
                aria-hidden="true"
                pointerEvents="none"
                style={{ pointerEvents: "none" }}
              >
                <circle
                  cx={solvedX}
                  cy={solvedY}
                  r={7}
                  fill="none"
                  stroke={SELECTED_COLOR}
                  strokeWidth={2}
                  strokeDasharray={asSolved.onSlice ? undefined : "3 2"}
                  pointerEvents="none"
                />
                {asSolved.onSlice && (
                  <circle cx={solvedX} cy={solvedY} r={2.5} fill={SELECTED_COLOR} pointerEvents="none" />
                )}
              </g>
            </ChartSvg>
          )
        }}
      </ResponsiveChart>
      <ChartFocusDetail placeholder="Hover or focus a frontier point to inspect its values.">
        {active && (
          <>
            <strong>Point {active.index + 1}</strong>
            <span>{yLabel}: {active.y.toLocaleString("en-US", { maximumFractionDigits: 6 })}</span>
            <span>{xLabel}: {active.x.toLocaleString("en-US", { maximumFractionDigits: 6 })}</span>
            <span>{STATUS_WORDS[active.status]}</span>
          </>
        )}
      </ChartFocusDetail>
    </div>
  )
}
