/**
 * SVG scatter chart for the optimiser frontier.
 *
 * Renders frontier points as clickable circles with a current-solve
 * diamond-ring marker.  Extracted from OptimiserPreview as part of
 * the god-component split.
 */

import { useMemo } from "react"
import { formatAxisLabel, yTicks } from "../../utils/chartHelpers"

// ─── Chart constants ─────────────────────────────────────────────

const CHART_W = 380
const CHART_H = 220
const CHART_PX = 50
const CHART_PX_RIGHT = 16
const CHART_PY = 16
const CHART_PY_BOTTOM = 28
const INNER_W = CHART_W - CHART_PX - CHART_PX_RIGHT
const INNER_H = CHART_H - CHART_PY - CHART_PY_BOTTOM

// ─── Component ───────────────────────────────────────────────────

interface FrontierChartProps {
  points: Record<string, unknown>[]
  xKey: string
  yKey: string
  xLabel: string
  selectedIdx: number | null
  currentX: number | null
  currentY: number
  onPointClick: (index: number) => void
}

export default function FrontierChart({
  points,
  xKey,
  yKey,
  xLabel,
  selectedIdx,
  currentX,
  currentY,
  onPointClick,
}: FrontierChartProps) {
  const { xScale, yScale, xTicks, yTickVals } = useMemo(() => {
    const xs = points.map(p => p[xKey] as number).filter(v => typeof v === "number" && Number.isFinite(v))
    const ys = points.map(p => p[yKey] as number).filter(v => typeof v === "number" && Number.isFinite(v))

    // Include current solve point in domain calculation
    if (currentX != null && Number.isFinite(currentX)) xs.push(currentX)
    if (Number.isFinite(currentY)) ys.push(currentY)

    let xMin = Math.min(...xs), xMax = Math.max(...xs)
    let yMin = Math.min(...ys), yMax = Math.max(...ys)

    // Add 5% padding
    const xPad = (xMax - xMin) * 0.05 || 0.01
    const yPad = (yMax - yMin) * 0.05 || 0.01
    xMin -= xPad; xMax += xPad
    yMin -= yPad; yMax += yPad

    const xRange = xMax - xMin || 1
    const yRange = yMax - yMin || 1

    return {
      xScale: (v: number) => CHART_PX + ((v - xMin) / xRange) * INNER_W,
      yScale: (v: number) => CHART_PY + INNER_H - ((v - yMin) / yRange) * INNER_H,
      xTicks: yTicks(xMin + xPad, xMax - xPad, 4),
      yTickVals: yTicks(yMin + yPad, yMax - yPad, 4),
    }
  }, [points, xKey, yKey, currentX, currentY])

  return (
    <svg width={CHART_W} height={CHART_H} style={{ background: "var(--bg-input)", borderRadius: 6, border: "1px solid var(--border)" }}>
      {/* Grid lines + Y axis labels */}
      {yTickVals.map(t => (
        <g key={`y-${t}`}>
          <line x1={CHART_PX} y1={yScale(t)} x2={CHART_PX + INNER_W} y2={yScale(t)} stroke="var(--border)" strokeWidth={0.5} />
          <text x={CHART_PX - 4} y={yScale(t) + 3} textAnchor="end" fontSize={9} fill="var(--text-muted)">{formatAxisLabel(t)}</text>
        </g>
      ))}
      {/* X axis labels */}
      {xTicks.map(t => (
        <text key={`x-${t}`} x={xScale(t)} y={CHART_H - CHART_PY_BOTTOM + 14} textAnchor="middle" fontSize={9} fill="var(--text-muted)">{formatAxisLabel(t)}</text>
      ))}
      {/* Axis labels */}
      <text x={CHART_PX + INNER_W / 2} y={CHART_H - 3} textAnchor="middle" fontSize={9} fill="var(--text-muted)">{xLabel}</text>
      <text x={6} y={CHART_PY + INNER_H / 2} textAnchor="middle" fontSize={9} fill="var(--text-muted)" transform={`rotate(-90,6,${CHART_PY + INNER_H / 2})`}>objective</text>

      {/* Frontier points */}
      {points.map((p, i) => {
        const x = p[xKey] as number
        const y = p[yKey] as number
        if (typeof x !== "number" || typeof y !== "number") return null
        const isSel = selectedIdx === i
        return (
          <circle
            key={i}
            cx={xScale(x)}
            cy={yScale(y)}
            r={isSel ? 6 : 4}
            fill={isSel ? "#f59e0b" : "var(--accent)"}
            stroke={isSel ? "#fff" : "none"}
            strokeWidth={isSel ? 2 : 0}
            opacity={isSel ? 1 : 0.7}
            style={{ cursor: "pointer" }}
            onClick={() => onPointClick(i)}
            tabIndex={0}
            role="button"
            aria-label={`Select frontier point ${i + 1}`}
            onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onPointClick(i) } }}
          />
        )
      })}

      {/* Current solve result marker (diamond ring) */}
      {currentX != null && Number.isFinite(currentX) && Number.isFinite(currentY) && (
        <g>
          <circle
            cx={xScale(currentX)}
            cy={yScale(currentY)}
            r={6}
            fill="none"
            stroke="#f59e0b"
            strokeWidth={2}
          />
          <circle
            cx={xScale(currentX)}
            cy={yScale(currentY)}
            r={2.5}
            fill="#f59e0b"
          />
        </g>
      )}
    </svg>
  )
}
