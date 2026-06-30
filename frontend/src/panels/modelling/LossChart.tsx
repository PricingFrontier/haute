/**
 * SVG loss curve chart for model training — shows train/eval loss and best iteration.
 * Extracted from ModellingConfig.tsx for readability.
 */
import { CHART_COLORS } from "../../theme/colors"
import { ChartLegend, ChartSvg } from "./ChartScaffold"

export type LossEntry = { iteration: number; [key: string]: number }

type LossChartProps = {
  lossHistory: LossEntry[]
  bestIteration?: number | null
}

export function LossChart({ lossHistory, bestIteration }: LossChartProps) {
  if (!lossHistory || lossHistory.length < 2) return null

  // Find train and eval loss keys
  const keys = Object.keys(lossHistory[0]).filter(k => k !== "iteration")
  const trainKey = keys.find(k => k.startsWith("train_"))
  const evalKey = keys.find(k => k.startsWith("eval_"))
  if (!trainKey) return null

  const w = 280, h = 80, px = 4, py = 4
  const chartW = w - px * 2, chartH = h - py * 2

  // Gather all loss values to find y range
  const allVals: number[] = []
  for (const entry of lossHistory) {
    if (trainKey && entry[trainKey] != null) allVals.push(entry[trainKey])
    if (evalKey && entry[evalKey] != null) allVals.push(entry[evalKey])
  }
  const yMin = allVals.reduce((a, b) => Math.min(a, b), Infinity)
  const yMax = allVals.reduce((a, b) => Math.max(a, b), -Infinity)
  const yRange = yMax - yMin || 1

  const xScale = (i: number) => px + (i / (lossHistory.length - 1)) * chartW
  const yScale = (v: number) => py + chartH - ((v - yMin) / yRange) * chartH

  const makePath = (key: string) => {
    const points = lossHistory
      .map((e, i) => e[key] != null ? `L${xScale(i).toFixed(1)},${yScale(e[key]).toFixed(1)}` : null)
      .filter(Boolean) as string[]
    if (points.length > 0) points[0] = "M" + points[0].slice(1)
    return points.join(" ")
  }

  // Best iteration vertical line position
  const bestX = bestIteration != null ? xScale(Math.min(bestIteration, lossHistory.length - 1)) : null

  return (
    <div>
      <label className="text-[11px] font-bold uppercase tracking-[0.08em]" style={{ color: "var(--text-muted)" }}>Loss Curve</label>
      <ChartSvg width={w} height={h} className="mt-1">
        <path d={makePath(trainKey)} fill="none" stroke={CHART_COLORS.train} strokeWidth={1.5} />
        {evalKey && <path d={makePath(evalKey)} fill="none" stroke={CHART_COLORS.eval} strokeWidth={1.5} />}
        {bestX != null && <line x1={bestX} y1={py} x2={bestX} y2={py + chartH} stroke={CHART_COLORS.best} strokeWidth={1} strokeDasharray="3,2" />}
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
