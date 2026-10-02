/**
 * SVG loss curve chart for model training — shows train/eval loss and best iteration.
 *
 * The axes are fixed from the fit's first row: rounds from 0 to the fit's
 * round budget, and loss from 0 to a little beyond the starting loss, widened
 * only if a later value outgrows it, so the curve draws across them as it
 * trains. A loss can be negative (CatBoost's Poisson), so the loss axis spans
 * zero and every value the fit has reached.
 */
import { CHART_COLORS } from "../../theme/colors"
import { formatChartNumber } from "../../utils/chartHelpers"
import { ChartLegend, ChartSvg } from "./ChartScaffold"
import { lossCurveKeys } from "./lossHistory"

export type LossEntry = { iteration: number; [key: string]: number }

type LossChartProps = {
  lossHistory: LossEntry[]
  /** The fit's round budget, the x-axis maximum; the last row's round when unknown. */
  totalIterations?: number
  bestIteration?: number | null
}

/** Headroom beyond the loss furthest from zero, as a share of the loss range. */
const Y_HEADROOM = 0.1

/** The loss axis: zero and the fit's extremes, padded on each side a loss reaches. */
function lossDomain(low: number, high: number): [number, number] {
  const pad = (high - low) * Y_HEADROOM
  const min = low < 0 ? low - pad : 0
  const max = high > 0 ? high + pad : 0
  return min === max ? [0, 1] : [min, max]
}

export function LossChart({ lossHistory, totalIterations = 0, bestIteration }: LossChartProps) {
  // With a known round budget one row already fixes the axes.
  const curveKeys = lossCurveKeys(lossHistory, totalIterations > 1 ? 1 : 2)
  if (!curveKeys) return null
  const { trainKey, evalKey } = curveKeys
  const keys = evalKey ? [trainKey, evalKey] : [trainKey]

  const w = 280, h = 96, left = 28, right = 6, top = 4, bottom = 14
  const chartW = w - left - right, chartH = h - top - bottom

  // The live history keeps each value's extremes through thinning, so a loss
  // axis drawn from its rows never shrinks during a fit.
  const values = lossHistory.flatMap((entry) =>
    keys.map((key) => entry[key]).filter((value) => value != null && Number.isFinite(value)),
  )
  const [yMin, yMax] = lossDomain(Math.min(0, ...values), Math.max(0, ...values))
  const yRange = yMax - yMin
  const lastIteration = lossHistory[lossHistory.length - 1].iteration
  const xMax = Math.max(totalIterations, lastIteration) || 1

  const xScale = (iteration: number) => left + (iteration / xMax) * chartW
  const yScale = (v: number) => top + chartH - ((v - yMin) / yRange) * chartH

  const makePath = (key: string) => {
    const points = lossHistory
      .filter((e) => e[key] != null)
      .map((e) => `L${xScale(e.iteration).toFixed(1)},${yScale(e[key]).toFixed(1)}`)
    if (points.length > 0) points[0] = "M" + points[0].slice(1)
    return points.join(" ")
  }

  // Best iteration vertical line position (best_iteration counts from zero, rows from one)
  const bestX = bestIteration != null ? xScale(Math.min(bestIteration + 1, xMax)) : null
  const axisColor = "var(--border)"
  const labelColor = "var(--text-muted)"
  const baseline = yScale(0)

  return (
    <div>
      <label className="text-[11px] font-bold uppercase tracking-[0.08em]" style={{ color: "var(--text-muted)" }}>Loss Curve</label>
      <ChartSvg width={w} height={h} className="mt-1">
        <line x1={left} y1={top} x2={left} y2={top + chartH} stroke={axisColor} strokeWidth={1} />
        <line x1={left} y1={baseline} x2={left + chartW} y2={baseline} stroke={axisColor} strokeWidth={1} />
        <text x={left - 3} y={top + 7} textAnchor="end" fontSize={9} fill={labelColor}>
          {formatChartNumber(yMax)}
        </text>
        <text x={left - 3} y={top + chartH + 3} textAnchor="end" fontSize={9} fill={labelColor}>
          {formatChartNumber(yMin)}
        </text>
        <text x={left + chartW} y={h - 2} textAnchor="end" fontSize={9} fill={labelColor}>
          {xMax.toLocaleString()}
        </text>
        <path d={makePath(trainKey)} fill="none" stroke={CHART_COLORS.train} strokeWidth={1.5} />
        {evalKey && <path d={makePath(evalKey)} fill="none" stroke={CHART_COLORS.eval} strokeWidth={1.5} />}
        {bestX != null && <line x1={bestX} y1={top} x2={bestX} y2={top + chartH} stroke={CHART_COLORS.best} strokeWidth={1} strokeDasharray="3,2" />}
      </ChartSvg>
      <ChartLegend
        compact
        items={[
          { label: "Train", color: CHART_COLORS.train },
          ...(evalKey ? [{ label: "Eval", color: CHART_COLORS.eval }] : []),
          ...(bestX != null ? [{ label: "Best iter", color: CHART_COLORS.best, dashed: true }] : []),
        ]}
      />
    </div>
  )
}
