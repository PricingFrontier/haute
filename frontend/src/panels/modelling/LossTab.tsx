/**
 * Full-size loss curve chart for the ModellingPreview panel.
 *
 * Same logic as LossChart.tsx but rendered at a larger size with
 * axis labels, grid lines, and a legend.
 */
import type { TrainResult } from "../../stores/useNodeResultsStore"
import { CHART_COLORS } from "../../theme/colors"
import {
  ChartEmptyState,
  ChartLegend,
  ChartSvg,
  MODELLING_CHART_AXIS_FONT_SIZE,
  MODELLING_CHART_AXIS_TEXT_COLOR,
  MODELLING_CHART_GRID_COLOR,
} from "./ChartScaffold"

interface LossTabProps {
  result: TrainResult
  width?: number
  height?: number
}

const TRAIN_COLOR = CHART_COLORS.train
const EVAL_COLOR = CHART_COLORS.eval
const BEST_COLOR = CHART_COLORS.best
const EMPTY_VALID_HISTORY_MESSAGE = "No valid loss history data available"

const isFiniteNumber = (value: unknown): value is number =>
  typeof value === "number" && Number.isFinite(value)

export function LossTab({ result, width = 700, height = 280 }: LossTabProps) {
  const lossHistory = result.loss_history
  if (!lossHistory || lossHistory.length < 2) {
    return <ChartEmptyState>No loss history data available</ChartEmptyState>
  }

  // Find train and eval loss keys
  const keys = Object.keys(lossHistory[0]).filter(k => k !== "iteration")
  const trainKey = keys.find(k => k.startsWith("train_"))
  const evalKey = keys.find(k => k.startsWith("eval_"))
  if (!trainKey) {
    return <ChartEmptyState>No loss keys found in history</ChartEmptyState>
  }

  const trainPointCount = lossHistory.filter((entry) => isFiniteNumber(entry[trainKey])).length
  if (trainPointCount < 2) {
    return <ChartEmptyState>{EMPTY_VALID_HISTORY_MESSAGE}</ChartEmptyState>
  }

  const marginLeft = 60
  const marginRight = 16
  const marginTop = 16
  const marginBottom = 36
  const chartW = width - marginLeft - marginRight
  const chartH = height - marginTop - marginBottom

  // Gather all loss values to find y range
  const allVals: number[] = []
  for (const entry of lossHistory) {
    if (isFiniteNumber(entry[trainKey])) allVals.push(entry[trainKey])
    if (evalKey && isFiniteNumber(entry[evalKey])) allVals.push(entry[evalKey])
  }
  if (allVals.length === 0) {
    return <ChartEmptyState>{EMPTY_VALID_HISTORY_MESSAGE}</ChartEmptyState>
  }
  const yMin = allVals.reduce((a, b) => Math.min(a, b), Infinity)
  const yMax = allVals.reduce((a, b) => Math.max(a, b), -Infinity)
  const yRange = yMax - yMin || 1
  // Add 5% padding
  const yPadded = yRange * 0.05
  const yLo = yMin - yPadded
  const yHi = yMax + yPadded
  const ySpan = yHi - yLo

  const xScale = (i: number) => marginLeft + (i / (lossHistory.length - 1)) * chartW
  const yScale = (v: number) => marginTop + chartH - ((v - yLo) / ySpan) * chartH

  const makePath = (key: string) => {
    let hasStarted = false
    const points = lossHistory
      .map((e, i) => {
        if (!isFiniteNumber(e[key])) return null
        const command = hasStarted ? "L" : "M"
        hasStarted = true
        return `${command}${xScale(i).toFixed(1)},${yScale(e[key]).toFixed(1)}`
      })
      .filter(Boolean)
    return points.join(" ")
  }

  // Grid lines (5 horizontal, ~5 vertical)
  const nGridY = 5
  const nGridX = Math.min(5, lossHistory.length - 1)
  const gridYValues = Array.from({ length: nGridY + 1 }, (_, i) => yLo + (i / nGridY) * ySpan)
  const gridXIndices = Array.from({ length: nGridX + 1 }, (_, i) => Math.round((i / nGridX) * (lossHistory.length - 1)))

  // Best iteration vertical line
  const bestIteration = result.best_iteration
  const bestX = isFiniteNumber(bestIteration) ? xScale(Math.min(Math.max(bestIteration, 0), lossHistory.length - 1)) : null

  // Metric name from key (strip "train_" prefix)
  const metricName = trainKey.replace("train_", "")

  return (
    <div>
      <ChartSvg width={width} height={height}>
        {/* Horizontal grid lines + y-axis labels */}
        {gridYValues.map((v, i) => {
          const y = yScale(v)
          return (
            <g key={`gy-${i}`}>
              <line x1={marginLeft} y1={y} x2={marginLeft + chartW} y2={y} stroke={MODELLING_CHART_GRID_COLOR} strokeWidth={1} />
              <text x={marginLeft - 6} y={y + 3} textAnchor="end" fontSize={MODELLING_CHART_AXIS_FONT_SIZE} fill={MODELLING_CHART_AXIS_TEXT_COLOR}>
                {v.toPrecision(3)}
              </text>
            </g>
          )
        })}

        {/* Vertical grid lines + x-axis labels */}
        {gridXIndices.map((idx, i) => {
          const x = xScale(idx)
          const iter = lossHistory[idx]?.iteration ?? idx
          return (
            <g key={`gx-${i}`}>
              <line x1={x} y1={marginTop} x2={x} y2={marginTop + chartH} stroke={MODELLING_CHART_GRID_COLOR} strokeWidth={1} />
              <text x={x} y={marginTop + chartH + 16} textAnchor="middle" fontSize={MODELLING_CHART_AXIS_FONT_SIZE} fill={MODELLING_CHART_AXIS_TEXT_COLOR}>
                {iter}
              </text>
            </g>
          )
        })}

        {/* X-axis label */}
        <text x={marginLeft + chartW / 2} y={height - 4} textAnchor="middle" fontSize={MODELLING_CHART_AXIS_FONT_SIZE} fill={MODELLING_CHART_AXIS_TEXT_COLOR}>
          Iteration
        </text>

        {/* Y-axis label */}
        <text
          x={12}
          y={marginTop + chartH / 2}
          textAnchor="middle"
          fontSize={MODELLING_CHART_AXIS_FONT_SIZE}
          fill={MODELLING_CHART_AXIS_TEXT_COLOR}
          transform={`rotate(-90,12,${marginTop + chartH / 2})`}
        >
          {metricName}
        </text>

        {/* Best iteration line */}
        {bestX != null && (
          <line
            x1={bestX} y1={marginTop} x2={bestX} y2={marginTop + chartH}
            stroke={BEST_COLOR} strokeWidth={1} strokeDasharray="5,3"
          />
        )}

        {/* Loss curves */}
        <path d={makePath(trainKey)} fill="none" stroke={TRAIN_COLOR} strokeWidth={1.5} />
        {evalKey && <path d={makePath(evalKey)} fill="none" stroke={EVAL_COLOR} strokeWidth={1.5} />}
      </ChartSvg>

      {/* Legend */}
      <ChartLegend
        items={[
          { label: "Train", color: TRAIN_COLOR },
          ...(evalKey ? [{ label: "Eval", color: EVAL_COLOR }] : []),
          ...(bestX != null
            ? [{ label: `Best iteration (${bestIteration})`, color: BEST_COLOR, dashed: true }]
            : []),
        ]}
      />
    </div>
  )
}
