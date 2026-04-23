/**
 * Lift tab for the ModellingPreview panel.
 *
 * Two sub-views toggled by a mini switch:
 * 1. Double Lift Chart — SVG bar chart (actual vs predicted by decile) + raw table
 * 2. Lorenz Curve — SVG line chart with Gini annotation
 */
import { useState } from "react"
import type { TrainResult } from "../../stores/useNodeResultsStore"
import { CHART_COLORS } from "../../theme/colors"

interface LiftTabProps {
  result: TrainResult
  width?: number
  height?: number
}

const GRID_COLOR = "rgba(255,255,255,.06)"
const AXIS_TEXT_COLOR = "var(--text-muted)"
const AXIS_FONT_SIZE = 10
const ACTUAL_COLOR = CHART_COLORS.actual
const PREDICTED_COLOR = CHART_COLORS.predicted

export function LiftTab({ result, width = 700, height = 260 }: LiftTabProps) {
  const [view, setView] = useState<"lift" | "lorenz">("lift")

  const hasLift = result.double_lift && result.double_lift.length > 0
  const hasLorenz = result.lorenz_curve && result.lorenz_curve.length > 0

  if (!hasLift && !hasLorenz) {
    return (
      <div className="flex items-center justify-center h-full text-xs" style={{ color: "var(--text-muted)" }}>
        No lift data available
      </div>
    )
  }

  return (
    <div className="space-y-2">
      {/* View toggle */}
      {hasLift && hasLorenz && (
        <div className="flex gap-1">
          <button
            onClick={() => setView("lift")}
            className="px-2 py-0.5 rounded text-[10px] font-medium"
            style={{
              background: view === "lift" ? "var(--accent-soft)" : "var(--chrome-hover)",
              color: view === "lift" ? "var(--accent)" : "var(--text-muted)",
            }}
          >
            Double Lift
          </button>
          <button
            onClick={() => setView("lorenz")}
            className="px-2 py-0.5 rounded text-[10px] font-medium"
            style={{
              background: view === "lorenz" ? "var(--accent-soft)" : "var(--chrome-hover)",
              color: view === "lorenz" ? "var(--accent)" : "var(--text-muted)",
            }}
          >
            Lorenz Curve
          </button>
        </div>
      )}

      {view === "lift" && hasLift && <DoubleLiftChart data={result.double_lift!} width={width} height={height} />}
      {view === "lorenz" && hasLorenz && (
        <LorenzChart
          curve={result.lorenz_curve!}
          perfectCurve={result.lorenz_curve_perfect}
          width={width}
          height={height}
        />
      )}
    </div>
  )
}

// ─── Double Lift Chart ────────────────────────────────────────────

function DoubleLiftChart({
  data,
  width,
  height,
}: {
  data: { decile: number; actual: number; predicted: number; count: number }[]
  width: number
  height: number
}) {
  const marginLeft = 60
  const marginRight = 16
  const marginTop = 16
  const marginBottom = 40
  const chartW = width - marginLeft - marginRight
  const chartH = height - marginTop - marginBottom

  const allVals = data.flatMap(d => [d.actual, d.predicted])
  const yMax = allVals.reduce((a, b) => Math.max(a, b), -Infinity) * 1.1
  const yMin = Math.min(0, allVals.reduce((a, b) => Math.min(a, b), Infinity) * 1.1)
  const ySpan = yMax - yMin || 1

  const nDeciles = data.length
  const groupW = chartW / nDeciles
  const barW = groupW * 0.35
  const gap = groupW * 0.05

  const yScale = (v: number) => marginTop + chartH - ((v - yMin) / ySpan) * chartH
  const zeroY = yScale(0)

  // Grid lines
  const nGridY = 4
  const gridYValues = Array.from({ length: nGridY + 1 }, (_, i) => yMin + (i / nGridY) * ySpan)

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

        {/* Zero line */}
        {yMin < 0 && (
          <line x1={marginLeft} y1={zeroY} x2={marginLeft + chartW} y2={zeroY} stroke="rgba(255,255,255,.15)" strokeWidth={1} />
        )}

        {/* Bars */}
        {data.map((d, i) => {
          const groupX = marginLeft + i * groupW
          const barCenter = groupX + groupW / 2

          const actualH = Math.abs(d.actual - 0) / ySpan * chartH
          const actualY = d.actual >= 0 ? yScale(d.actual) : zeroY
          const predictedH = Math.abs(d.predicted - 0) / ySpan * chartH
          const predictedY = d.predicted >= 0 ? yScale(d.predicted) : zeroY

          return (
            <g key={d.decile}>
              {/* Actual bar */}
              <rect
                x={barCenter - barW - gap / 2}
                y={actualY}
                width={barW}
                height={actualH}
                fill={ACTUAL_COLOR}
                opacity={0.7}
                rx={1}
              />
              {/* Predicted bar */}
              <rect
                x={barCenter + gap / 2}
                y={predictedY}
                width={barW}
                height={predictedH}
                fill={PREDICTED_COLOR}
                opacity={0.7}
                rx={1}
              />
              {/* Decile label */}
              <text x={barCenter} y={marginTop + chartH + 14} textAnchor="middle" fontSize={AXIS_FONT_SIZE} fill={AXIS_TEXT_COLOR}>
                {d.decile}
              </text>
              {/* Count label */}
              <text x={barCenter} y={marginTop + chartH + 26} textAnchor="middle" fontSize={8} fill={AXIS_TEXT_COLOR} opacity={0.6}>
                n={d.count}
              </text>
            </g>
          )
        })}

        {/* X-axis label */}
        <text x={marginLeft + chartW / 2} y={height - 2} textAnchor="middle" fontSize={AXIS_FONT_SIZE} fill={AXIS_TEXT_COLOR}>
          Decile
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
          Average value
        </text>
      </svg>

      {/* Legend */}
      <div className="flex gap-4 mt-1.5 text-[11px]" style={{ color: "var(--text-muted)" }}>
        <span className="flex items-center gap-1.5">
          <span className="inline-block w-3 h-2 rounded-sm" style={{ background: ACTUAL_COLOR, opacity: 0.7 }} />
          Actual
        </span>
        <span className="flex items-center gap-1.5">
          <span className="inline-block w-3 h-2 rounded-sm" style={{ background: PREDICTED_COLOR, opacity: 0.7 }} />
          Predicted
        </span>
      </div>

      {/* Raw table */}
      <div className="mt-3">
        <label className="text-[11px] font-bold uppercase tracking-[0.08em]" style={{ color: "var(--text-muted)" }}>
          Lift Table
        </label>
        <div className="mt-1 text-[11px] font-mono" style={{ color: "var(--text-secondary)" }}>
          <div className="grid grid-cols-4 gap-1 pb-0.5 mb-0.5" style={{ borderBottom: "1px solid var(--border)" }}>
            <span style={{ color: "var(--text-muted)" }}>Decile</span>
            <span style={{ color: "var(--text-muted)" }}>Actual</span>
            <span style={{ color: "var(--text-muted)" }}>Predicted</span>
            <span style={{ color: "var(--text-muted)" }}>Count</span>
          </div>
          {data.map(row => (
            <div key={row.decile} className="grid grid-cols-4 gap-1">
              <span>{row.decile}</span>
              <span style={{ color: "var(--text-primary)" }}>{row.actual.toFixed(4)}</span>
              <span style={{ color: PREDICTED_COLOR }}>{row.predicted.toFixed(4)}</span>
              <span>{row.count.toLocaleString()}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

// ─── Lorenz Curve ─────────────────────────────────────────────────

function LorenzChart({
  curve,
  perfectCurve,
  width,
  height,
}: {
  curve: { cum_weight_frac: number; cum_actual_frac: number }[]
  perfectCurve?: { cum_weight_frac: number; cum_actual_frac: number }[]
  width: number
  height: number
}) {
  const marginLeft = 55
  const marginRight = 16
  const marginTop = 16
  const marginBottom = 36
  const chartW = width - marginLeft - marginRight
  const chartH = height - marginTop - marginBottom

  const xScale = (v: number) => marginLeft + v * chartW
  const yScale = (v: number) => marginTop + chartH - v * chartH

  // Compute Gini coefficient (2 * area between diagonal and curve)
  const gini = computeGini(curve, perfectCurve)

  // Build SVG paths
  const modelPath = curve
    .map((p, i) => `${i === 0 ? "M" : "L"}${xScale(p.cum_weight_frac).toFixed(1)},${yScale(p.cum_actual_frac).toFixed(1)}`)
    .join(" ")

  const perfectPath = perfectCurve
    ? perfectCurve
        .map((p, i) => `${i === 0 ? "M" : "L"}${xScale(p.cum_weight_frac).toFixed(1)},${yScale(p.cum_actual_frac).toFixed(1)}`)
        .join(" ")
    : null

  // Shaded area between diagonal and model curve
  const shadedPath = [
    `M${xScale(0).toFixed(1)},${yScale(0).toFixed(1)}`,
    ...curve.map(p => `L${xScale(p.cum_weight_frac).toFixed(1)},${yScale(p.cum_actual_frac).toFixed(1)}`),
    `L${xScale(1).toFixed(1)},${yScale(1).toFixed(1)}`,
    "Z",
  ].join(" ")

  // Grid lines
  const gridValues = [0, 0.25, 0.5, 0.75, 1.0]

  return (
    <div>
      <svg width={width} height={height} style={{ background: "var(--bg-input)", borderRadius: 6, border: "1px solid var(--border)" }}>
        {/* Grid lines */}
        {gridValues.map(v => (
          <g key={`grid-${v}`}>
            <line x1={marginLeft} y1={yScale(v)} x2={marginLeft + chartW} y2={yScale(v)} stroke={GRID_COLOR} strokeWidth={1} />
            <line x1={xScale(v)} y1={marginTop} x2={xScale(v)} y2={marginTop + chartH} stroke={GRID_COLOR} strokeWidth={1} />
            <text x={marginLeft - 6} y={yScale(v) + 3} textAnchor="end" fontSize={AXIS_FONT_SIZE} fill={AXIS_TEXT_COLOR}>
              {v.toFixed(2)}
            </text>
            <text x={xScale(v)} y={marginTop + chartH + 14} textAnchor="middle" fontSize={AXIS_FONT_SIZE} fill={AXIS_TEXT_COLOR}>
              {v.toFixed(2)}
            </text>
          </g>
        ))}

        {/* Shaded area */}
        <path d={shadedPath} fill={PREDICTED_COLOR} opacity={0.08} />

        {/* Diagonal (random model) */}
        <line
          x1={xScale(0)} y1={yScale(0)} x2={xScale(1)} y2={yScale(1)}
          stroke="rgba(255,255,255,.25)" strokeWidth={1} strokeDasharray="4,3"
        />

        {/* Perfect model curve */}
        {perfectPath && (
          <path d={perfectPath} fill="none" stroke={ACTUAL_COLOR} strokeWidth={1.5} opacity={0.6} />
        )}

        {/* Model curve */}
        <path d={modelPath} fill="none" stroke={PREDICTED_COLOR} strokeWidth={1.5} />

        {/* Gini annotation */}
        <text x={marginLeft + 8} y={marginTop + 16} fontSize={11} fontWeight="bold" fill={PREDICTED_COLOR}>
          Gini = {gini.toFixed(4)}
        </text>

        {/* Axis labels */}
        <text x={marginLeft + chartW / 2} y={height - 4} textAnchor="middle" fontSize={AXIS_FONT_SIZE} fill={AXIS_TEXT_COLOR}>
          Cumulative weight fraction
        </text>
        <text
          x={10}
          y={marginTop + chartH / 2}
          textAnchor="middle"
          fontSize={AXIS_FONT_SIZE}
          fill={AXIS_TEXT_COLOR}
          transform={`rotate(-90,10,${marginTop + chartH / 2})`}
        >
          Cumulative actual fraction
        </text>
      </svg>

      {/* Legend */}
      <div className="flex gap-4 mt-1.5 text-[11px]" style={{ color: "var(--text-muted)" }}>
        <span className="flex items-center gap-1.5">
          <span className="inline-block w-3 h-0.5 rounded" style={{ background: PREDICTED_COLOR }} />
          Model
        </span>
        {perfectPath && (
          <span className="flex items-center gap-1.5">
            <span className="inline-block w-3 h-0.5 rounded" style={{ background: ACTUAL_COLOR, opacity: 0.6 }} />
            Perfect model
          </span>
        )}
        <span className="flex items-center gap-1.5">
          <span className="inline-block w-3 h-0.5 rounded" style={{ background: "rgba(255,255,255,.25)", borderTop: "1px dashed rgba(255,255,255,.25)" }} />
          Random
        </span>
      </div>
    </div>
  )
}

/** Compute normalized Gini coefficient using trapezoidal rule. */
function computeGini(
  curve: { cum_weight_frac: number; cum_actual_frac: number }[],
  perfectCurve?: { cum_weight_frac: number; cum_actual_frac: number }[],
): number {
  if (curve.length < 2) return 0

  const trapArea = (pts: { cum_weight_frac: number; cum_actual_frac: number }[]) => {
    let area = 0
    for (let i = 1; i < pts.length; i++) {
      const dx = pts[i].cum_weight_frac - pts[i - 1].cum_weight_frac
      const avgY = (pts[i].cum_actual_frac + pts[i - 1].cum_actual_frac) / 2
      area += dx * avgY
    }
    return area
  }

  const rawGini = 2 * trapArea(curve) - 1
  if (perfectCurve && perfectCurve.length >= 2) {
    const perfectGini = 2 * trapArea(perfectCurve) - 1
    return perfectGini !== 0 ? rawGini / perfectGini : 0
  }
  return rawGini
}
