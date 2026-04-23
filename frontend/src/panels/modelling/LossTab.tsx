/**
 * Full-size loss curve chart for the ModellingPreview panel.
 *
 * Same logic as LossChart.tsx but rendered at a larger size with
 * axis labels, grid lines, and a legend.
 */
import type { TrainResult } from "../../stores/useNodeResultsStore"
import { CHART_COLORS } from "../../theme/colors"

interface LossTabProps {
  result: TrainResult
  width?: number
  height?: number
}

const GRID_COLOR = "rgba(255,255,255,.06)"
const AXIS_TEXT_COLOR = "var(--text-muted)"
const AXIS_FONT_SIZE = 10
const TRAIN_COLOR = CHART_COLORS.train
const EVAL_COLOR = CHART_COLORS.eval
const BEST_COLOR = CHART_COLORS.best

export function LossTab({ result, width = 700, height = 280 }: LossTabProps) {
  const lossHistory = result.loss_history
  if (!lossHistory || lossHistory.length < 2) {
    return (
      <div className="flex items-center justify-center h-full text-xs" style={{ color: "var(--text-muted)" }}>
        No loss history data available
      </div>
    )
  }

  // Find train and eval loss keys
  const keys = Object.keys(lossHistory[0]).filter(k => k !== "iteration")
  const trainKey = keys.find(k => k.startsWith("train_"))
  const evalKey = keys.find(k => k.startsWith("eval_"))
  if (!trainKey) {
    return (
      <div className="flex items-center justify-center h-full text-xs" style={{ color: "var(--text-muted)" }}>
        No loss keys found in history
      </div>
    )
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
    if (entry[trainKey] != null) allVals.push(entry[trainKey])
    if (evalKey && entry[evalKey] != null) allVals.push(entry[evalKey])
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
    const points = lossHistory
      .map((e, i) => e[key] != null ? `${i === 0 ? "M" : "L"}${xScale(i).toFixed(1)},${yScale(e[key]).toFixed(1)}` : null)
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
  const bestX = bestIteration != null ? xScale(Math.min(bestIteration, lossHistory.length - 1)) : null

  // Metric name from key (strip "train_" prefix)
  const metricName = trainKey.replace("train_", "")

  return (
    <div>
      <svg width={width} height={height} style={{ background: "var(--bg-input)", borderRadius: 6, border: "1px solid var(--border)" }}>
        {/* Horizontal grid lines + y-axis labels */}
        {gridYValues.map((v, i) => {
          const y = yScale(v)
          return (
            <g key={`gy-${i}`}>
              <line x1={marginLeft} y1={y} x2={marginLeft + chartW} y2={y} stroke={GRID_COLOR} strokeWidth={1} />
              <text x={marginLeft - 6} y={y + 3} textAnchor="end" fontSize={AXIS_FONT_SIZE} fill={AXIS_TEXT_COLOR}>
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
              <line x1={x} y1={marginTop} x2={x} y2={marginTop + chartH} stroke={GRID_COLOR} strokeWidth={1} />
              <text x={x} y={marginTop + chartH + 16} textAnchor="middle" fontSize={AXIS_FONT_SIZE} fill={AXIS_TEXT_COLOR}>
                {iter}
              </text>
            </g>
          )
        })}

        {/* X-axis label */}
        <text x={marginLeft + chartW / 2} y={height - 4} textAnchor="middle" fontSize={AXIS_FONT_SIZE} fill={AXIS_TEXT_COLOR}>
          Iteration
        </text>

        {/* Y-axis label */}
        <text
          x={12}
          y={marginTop + chartH / 2}
          textAnchor="middle"
          fontSize={AXIS_FONT_SIZE}
          fill={AXIS_TEXT_COLOR}
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
      </svg>

      {/* Legend */}
      <div className="flex gap-4 mt-1.5 text-[11px]" style={{ color: "var(--text-muted)" }}>
        <span className="flex items-center gap-1.5">
          <span className="inline-block w-3 h-0.5 rounded" style={{ background: TRAIN_COLOR }} />
          Train
        </span>
        {evalKey && (
          <span className="flex items-center gap-1.5">
            <span className="inline-block w-3 h-0.5 rounded" style={{ background: EVAL_COLOR }} />
            Eval
          </span>
        )}
        {bestX != null && (
          <span className="flex items-center gap-1.5">
            <span className="inline-block w-3 h-0.5 rounded" style={{ background: BEST_COLOR, borderTop: "1px dashed" }} />
            Best iteration ({bestIteration})
          </span>
        )}
      </div>
    </div>
  )
}
