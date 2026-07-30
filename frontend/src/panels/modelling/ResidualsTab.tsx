/**
 * Residuals tab for the ModellingPreview panel.
 *
 * Two charts side by side (or stacked if narrow):
 * 1. Residuals Histogram — weighted count by residual bin
 * 2. Actual vs Predicted Scatter — up to 2000 points
 */
import { useMemo } from "react"
import type { TrainResult } from "../../stores/useNodeResultsStore"
import { CHART_COLORS } from "../../theme/colors"
import {
  ChartEmptyState,
  ChartSvg,
  MODELLING_CHART_AXIS_FONT_SIZE as AXIS_FONT_SIZE,
  MODELLING_CHART_AXIS_TEXT_COLOR as AXIS_TEXT_COLOR,
  MODELLING_CHART_GRID_COLOR as GRID_COLOR,
} from "./ChartScaffold"

interface ResidualsTabProps {
  result: TrainResult
  width?: number
  height?: number
}

const BAR_COLOR = CHART_COLORS.predicted
const ZERO_LINE_COLOR = CHART_COLORS.residualZero
const SCATTER_COLOR = CHART_COLORS.predicted
const REF_LINE_COLOR = "rgba(255,255,255,.25)"

export function ResidualsTab({ result, width = 340, height = 240 }: ResidualsTabProps) {
  const hasHistogram = result.residuals_histogram && result.residuals_histogram.length > 0
  const hasScatter = result.actual_vs_predicted && result.actual_vs_predicted.length > 0

  if (!hasHistogram && !hasScatter) {
    return <ChartEmptyState>No residuals data available</ChartEmptyState>
  }

  return (
    <div className="flex gap-4 flex-wrap">
      {hasHistogram && (
        <ResidualsHistogram
          data={result.residuals_histogram!}
          stats={result.residuals_stats}
          width={width}
          height={height}
        />
      )}
      {hasScatter && (
        <ActualVsPredictedScatter
          data={result.actual_vs_predicted!}
          width={width}
          height={height}
        />
      )}
    </div>
  )
}

// ─── Residuals Histogram ──────────────────────────────────────────

function ResidualsHistogram({
  data,
  stats,
  width,
  height,
}: {
  data: { bin_center: number; count: number; weighted_count: number }[]
  stats?: Record<string, number>
  width: number
  height: number
}) {
  const marginLeft = 50
  const marginRight = 12
  const marginTop = 12
  const marginBottom = 36
  const chartW = width - marginLeft - marginRight
  const chartH = height - marginTop - marginBottom

  const maxCount = data.map(d => d.weighted_count).reduce((a, b) => Math.max(a, b), -Infinity)
  const xMin = data.map(d => d.bin_center).reduce((a, b) => Math.min(a, b), Infinity)
  const xMax = data.map(d => d.bin_center).reduce((a, b) => Math.max(a, b), -Infinity)
  const xRange = xMax - xMin || 1
  const barW = (chartW / data.length) * 0.85

  const xScale = (v: number) => marginLeft + ((v - xMin) / xRange) * chartW
  const yScale = (v: number) => marginTop + chartH - (v / (maxCount || 1)) * chartH

  // Zero line position
  const zeroX = xMin <= 0 && xMax >= 0 ? xScale(0) : null

  // Grid lines
  const nGridY = 4
  const gridYValues = Array.from({ length: nGridY + 1 }, (_, i) => (i / nGridY) * maxCount)

  return (
    <div>
      <label className="text-[11px] font-bold uppercase tracking-[0.08em]" style={{ color: "var(--text-muted)" }}>
        Residuals Distribution
      </label>
      <ChartSvg width={width} height={height} className="mt-1">
        {/* Horizontal grid lines + y-axis labels */}
        {gridYValues.map((v, i) => {
          const y = yScale(v)
          return (
            <g key={`gy-${i}`}>
              <line x1={marginLeft} y1={y} x2={marginLeft + chartW} y2={y} stroke={GRID_COLOR} strokeWidth={1} />
              <text x={marginLeft - 6} y={y + 3} textAnchor="end" fontSize={AXIS_FONT_SIZE} fill={AXIS_TEXT_COLOR}>
                {v >= 1000 ? `${(v / 1000).toFixed(0)}K` : v.toFixed(0)}
              </text>
            </g>
          )
        })}

        {/* Bars */}
        {data.map((d, i) => {
          const cx = xScale(d.bin_center)
          const barH = (d.weighted_count / (maxCount || 1)) * chartH
          return (
            <rect
              key={i}
              x={cx - barW / 2}
              y={marginTop + chartH - barH}
              width={barW}
              height={barH}
              fill={BAR_COLOR}
              opacity={0.6}
              rx={1}
            />
          )
        })}

        {/* Zero line */}
        {zeroX != null && (
          <line
            x1={zeroX} y1={marginTop} x2={zeroX} y2={marginTop + chartH}
            stroke={ZERO_LINE_COLOR} strokeWidth={1} strokeDasharray="4,3"
          />
        )}

        {/* X-axis label */}
        <text x={marginLeft + chartW / 2} y={height - 4} textAnchor="middle" fontSize={AXIS_FONT_SIZE} fill={AXIS_TEXT_COLOR}>
          Residual
        </text>

        {/* Y-axis label */}
        <text
          x={10}
          y={marginTop + chartH / 2}
          textAnchor="middle"
          fontSize={AXIS_FONT_SIZE}
          fill={AXIS_TEXT_COLOR}
          transform={`rotate(-90,10,${marginTop + chartH / 2})`}
        >
          Weighted count
        </text>
      </ChartSvg>

      {/* Stats annotation */}
      {stats && ["mean", "std", "skew"].some(name => Number.isFinite(stats[name])) && (
        <div className="flex gap-3 mt-1.5 text-[10px] font-mono" style={{ color: "var(--text-muted)" }}>
          {([
            ["mean", "Mean"],
            ["std", "Std"],
            ["skew", "Skew"],
          ] as const).map(([name, label]) => (
            Number.isFinite(stats[name]) && (
              <span key={name}>
                {label}:{" "}
                <span style={{ color: "var(--text-primary)" }}>
                  {stats[name].toFixed(4)}
                </span>
              </span>
            )
          ))}
        </div>
      )}
    </div>
  )
}

// ─── Actual vs Predicted Scatter ──────────────────────────────────

function ActualVsPredictedScatter({
  data,
  width,
  height,
}: {
  data: { actual: number; predicted: number; weight: number }[]
  width: number
  height: number
}) {
  const marginLeft = 50
  const marginRight = 12
  const marginTop = 12
  const marginBottom = 36
  const chartW = width - marginLeft - marginRight
  const chartH = height - marginTop - marginBottom

  // Subsample to max 2000 points
  const points = useMemo(() => {
    if (data.length <= 2000) return data
    const step = data.length / 2000
    const sampled: typeof data = []
    for (let i = 0; i < 2000; i++) {
      sampled.push(data[Math.floor(i * step)])
    }
    return sampled
  }, [data])

  const allVals = points.flatMap(p => [p.actual, p.predicted])
  const vMin = allVals.reduce((a, b) => Math.min(a, b), Infinity)
  const vMax = allVals.reduce((a, b) => Math.max(a, b), -Infinity)
  const vRange = vMax - vMin || 1
  const padding = vRange * 0.05
  const lo = vMin - padding
  const hi = vMax + padding
  const span = hi - lo

  const xScale = (v: number) => marginLeft + ((v - lo) / span) * chartW
  const yScale = (v: number) => marginTop + chartH - ((v - lo) / span) * chartH

  // Grid lines
  const nGrid = 4
  const gridValues = Array.from({ length: nGrid + 1 }, (_, i) => lo + (i / nGrid) * span)

  return (
    <div>
      <label className="text-[11px] font-bold uppercase tracking-[0.08em]" style={{ color: "var(--text-muted)" }}>
        Actual vs Predicted
      </label>
      <ChartSvg width={width} height={height} className="mt-1">
        {/* Grid lines + labels */}
        {gridValues.map((v, i) => {
          const x = xScale(v)
          const y = yScale(v)
          return (
            <g key={`grid-${i}`}>
              <line x1={marginLeft} y1={y} x2={marginLeft + chartW} y2={y} stroke={GRID_COLOR} strokeWidth={1} />
              <line x1={x} y1={marginTop} x2={x} y2={marginTop + chartH} stroke={GRID_COLOR} strokeWidth={1} />
              <text x={marginLeft - 6} y={y + 3} textAnchor="end" fontSize={AXIS_FONT_SIZE} fill={AXIS_TEXT_COLOR}>
                {v.toPrecision(3)}
              </text>
              <text x={x} y={marginTop + chartH + 14} textAnchor="middle" fontSize={AXIS_FONT_SIZE} fill={AXIS_TEXT_COLOR}>
                {v.toPrecision(3)}
              </text>
            </g>
          )
        })}

        {/* 45-degree reference line */}
        <line
          x1={xScale(lo)} y1={yScale(lo)} x2={xScale(hi)} y2={yScale(hi)}
          stroke={REF_LINE_COLOR} strokeWidth={1} strokeDasharray="4,3"
        />

        {/* Scatter points */}
        {points.map((p, i) => (
          <circle
            key={i}
            cx={xScale(p.actual)}
            cy={yScale(p.predicted)}
            r={2}
            fill={SCATTER_COLOR}
            opacity={0.4}
          />
        ))}

        {/* Axis labels */}
        <text x={marginLeft + chartW / 2} y={height - 4} textAnchor="middle" fontSize={AXIS_FONT_SIZE} fill={AXIS_TEXT_COLOR}>
          Actual
        </text>
        <text
          x={10}
          y={marginTop + chartH / 2}
          textAnchor="middle"
          fontSize={AXIS_FONT_SIZE}
          fill={AXIS_TEXT_COLOR}
          transform={`rotate(-90,10,${marginTop + chartH / 2})`}
        >
          Predicted
        </text>
      </ChartSvg>

      <div className="mt-1 text-[10px]" style={{ color: "var(--text-muted)" }}>
        {data.length > 2000
          ? `Showing 2,000 of ${data.length.toLocaleString()} points (sampled)`
          : `${data.length.toLocaleString()} points`}
      </div>
    </div>
  )
}
