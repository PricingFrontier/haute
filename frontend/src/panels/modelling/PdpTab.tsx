/**
 * Partial Dependence Plot (PDP) tab for the ModellingPreview panel.
 *
 * Left panel: feature browser (same shared FeatureBrowser component)
 * Right panel: SVG chart
 *   - Numeric features: line chart of avg_prediction vs feature value
 *   - Categorical features: bar chart (one bar per category)
 */
import { useState, useMemo, useCallback, useEffect } from "react"
import type { TrainResult } from "../../stores/useNodeResultsStore"
import { CHART_COLORS } from "../../theme/colors"
import { FeatureBrowser, type FeatureItem } from "./FeatureBrowser"

interface PdpTabProps {
  result: TrainResult
}

const GRID_COLOR = "rgba(255,255,255,.06)"
const AXIS_TEXT_COLOR = "var(--text-muted)"
const AXIS_FONT_SIZE = 10
const LINE_COLOR = CHART_COLORS.predicted
const BAR_COLOR = CHART_COLORS.predicted

type PdpGridPoint = { value: number | string; avg_prediction: number }
type PdpFeature = { feature: string; type: string; grid: PdpGridPoint[] }

export function PdpTab({ result }: PdpTabProps) {
  const pdpData = result.pdp_data

  // Build feature list sorted by importance
  const featureItems: FeatureItem[] = useMemo(() => {
    if (!pdpData || pdpData.length === 0) return []
    const impMap = new Map<string, number>()
    for (const fi of result.feature_importance) {
      impMap.set(fi.feature, fi.importance)
    }
    return pdpData.map(f => ({
      feature: f.feature,
      importance: impMap.get(f.feature) ?? 0,
    }))
  }, [pdpData, result.feature_importance])

  const featureKey = useMemo(() => featureItems.map(f => f.feature).join(","), [featureItems])

  const [selectedFeature, setSelectedFeature] = useState<string | null>(
    featureItems.length > 0 ? featureItems[0].feature : null,
  )

  /* eslint-disable react-hooks/set-state-in-effect -- reset selection on new data */
  useEffect(() => {
    if (featureItems.length > 0) {
      const names = featureKey.split(",")
      if (!selectedFeature || !names.includes(selectedFeature)) {
        setSelectedFeature(names[0])
      }
    }
  }, [featureKey, selectedFeature, featureItems.length])
  /* eslint-enable react-hooks/set-state-in-effect */

  const handleSelect = useCallback((feature: string) => {
    setSelectedFeature(feature)
  }, [])

  const selectedData: PdpFeature | null = useMemo(() => {
    if (!selectedFeature || !pdpData) return null
    return pdpData.find(f => f.feature === selectedFeature) ?? null
  }, [selectedFeature, pdpData])

  if (!pdpData || pdpData.length === 0) {
    return (
      <div className="flex items-center justify-center h-full text-xs" style={{ color: "var(--text-muted)" }}>
        No PDP data available
      </div>
    )
  }

  return (
    <div className="flex h-full" style={{ minHeight: 200 }}>
      <FeatureBrowser
        features={featureItems}
        selected={selectedFeature}
        onSelect={handleSelect}
      />
      <div className="flex-1 px-3 py-1 overflow-auto">
        {selectedData ? (
          <PdpChart data={selectedData} />
        ) : (
          <div className="flex items-center justify-center h-full text-xs" style={{ color: "var(--text-muted)" }}>
            Select a feature
          </div>
        )}
      </div>
    </div>
  )
}

// ─── PDP Chart ────────────────────────────────────────────────────

function PdpChart({ data }: { data: PdpFeature }) {
  const grid = data.grid
  if (grid.length === 0) {
    return (
      <div className="text-xs" style={{ color: "var(--text-muted)" }}>No PDP data for {data.feature}</div>
    )
  }

  const isNumeric = data.type === "numeric"

  return (
    <div>
      <div className="text-xs font-medium mb-1" style={{ color: "var(--text-primary)" }}>
        {data.feature}
        <span className="ml-2 text-[10px]" style={{ color: "var(--text-muted)" }}>({data.type})</span>
      </div>
      {isNumeric ? (
        <PdpLineChart grid={grid} />
      ) : (
        <PdpBarChart grid={grid} />
      )}
    </div>
  )
}

// ─── Numeric PDP: Line Chart ──────────────────────────────────────

function PdpLineChart({ grid }: { grid: PdpGridPoint[] }) {
  const width = 520
  const height = 220
  const marginLeft = 55
  const marginRight = 16
  const marginTop = 16
  const marginBottom = 36
  const chartW = width - marginLeft - marginRight
  const chartH = height - marginTop - marginBottom

  const xVals = grid.map(p => Number(p.value))
  const yVals = grid.map(p => p.avg_prediction)

  const xMin = xVals.reduce((a, b) => Math.min(a, b), Infinity)
  const xMax = xVals.reduce((a, b) => Math.max(a, b), -Infinity)
  const xRange = xMax - xMin || 1
  const yMin = yVals.reduce((a, b) => Math.min(a, b), Infinity)
  const yMax = yVals.reduce((a, b) => Math.max(a, b), -Infinity)
  const yPad = (yMax - yMin) * 0.1 || 0.001
  const yLo = yMin - yPad
  const yHi = yMax + yPad
  const ySpan = yHi - yLo

  const xScale = (v: number) => marginLeft + ((v - xMin) / xRange) * chartW
  const yScale = (v: number) => marginTop + chartH - ((v - yLo) / ySpan) * chartH

  // Grid lines
  const nGridY = 4
  const nGridX = Math.min(5, grid.length - 1)
  const gridYValues = Array.from({ length: nGridY + 1 }, (_, i) => yLo + (i / nGridY) * ySpan)
  const gridXIndices = Array.from({ length: nGridX + 1 }, (_, i) => Math.round((i / nGridX) * (grid.length - 1)))

  // Line path
  const linePath = grid
    .map((p, i) => `${i === 0 ? "M" : "L"}${xScale(Number(p.value)).toFixed(1)},${yScale(p.avg_prediction).toFixed(1)}`)
    .join(" ")

  return (
    <>
      <svg width={width} height={height} style={{ background: "var(--bg-input)", borderRadius: 6, border: "1px solid var(--border)" }}>
        {/* Grid */}
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
        {gridXIndices.map((idx, i) => {
          const v = Number(grid[idx].value)
          const x = xScale(v)
          return (
            <g key={`gx-${i}`}>
              <line x1={x} y1={marginTop} x2={x} y2={marginTop + chartH} stroke={GRID_COLOR} strokeWidth={1} />
              <text x={x} y={marginTop + chartH + 14} textAnchor="middle" fontSize={AXIS_FONT_SIZE} fill={AXIS_TEXT_COLOR}>
                {v.toPrecision(3)}
              </text>
            </g>
          )
        })}

        {/* Line + dots */}
        <path d={linePath} fill="none" stroke={LINE_COLOR} strokeWidth={1.5} />
        {grid.map((p, i) => (
          <circle key={i} cx={xScale(Number(p.value))} cy={yScale(p.avg_prediction)} r={2.5} fill={LINE_COLOR} />
        ))}

        {/* Axis labels */}
        <text x={marginLeft + chartW / 2} y={height - 4} textAnchor="middle" fontSize={AXIS_FONT_SIZE} fill={AXIS_TEXT_COLOR}>
          Feature value
        </text>
        <text
          x={10}
          y={marginTop + chartH / 2}
          textAnchor="middle"
          fontSize={AXIS_FONT_SIZE}
          fill={AXIS_TEXT_COLOR}
          transform={`rotate(-90,10,${marginTop + chartH / 2})`}
        >
          Avg prediction
        </text>
      </svg>
    </>
  )
}

// ─── Categorical PDP: Bar Chart ───────────────────────────────────

function PdpBarChart({ grid }: { grid: PdpGridPoint[] }) {
  const width = 520
  const height = 220
  const marginLeft = 55
  const marginRight = 16
  const marginTop = 16
  const marginBottom = 50
  const chartW = width - marginLeft - marginRight
  const chartH = height - marginTop - marginBottom

  const yVals = grid.map(p => p.avg_prediction)
  const yMin = Math.min(0, yVals.reduce((a, b) => Math.min(a, b), Infinity))
  const yMax = yVals.reduce((a, b) => Math.max(a, b), -Infinity) * 1.1
  const ySpan = yMax - yMin || 1

  const barGroupW = chartW / grid.length
  const barW = barGroupW * 0.6

  const xCenter = (i: number) => marginLeft + i * barGroupW + barGroupW / 2
  const yScale = (v: number) => marginTop + chartH - ((v - yMin) / ySpan) * chartH
  const zeroY = yScale(0)

  // Grid lines
  const nGridY = 4
  const gridYValues = Array.from({ length: nGridY + 1 }, (_, i) => yMin + (i / nGridY) * ySpan)

  const truncLabel = (s: string, maxLen: number) => s.length > maxLen ? s.slice(0, maxLen - 1) + "\u2026" : s

  return (
    <>
      <svg width={width} height={height} style={{ background: "var(--bg-input)", borderRadius: 6, border: "1px solid var(--border)" }}>
        {/* Grid */}
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

        {/* Bars */}
        {grid.map((p, i) => {
          const cx = xCenter(i)
          const barH = Math.abs(p.avg_prediction - 0) / ySpan * chartH
          const barY = p.avg_prediction >= 0 ? yScale(p.avg_prediction) : zeroY
          const label = truncLabel(String(p.value), 10)
          const rotate = grid.length > 5
          return (
            <g key={i}>
              <rect
                x={cx - barW / 2}
                y={barY}
                width={barW}
                height={barH}
                fill={BAR_COLOR}
                opacity={0.7}
                rx={1}
              />
              <text
                x={cx}
                y={marginTop + chartH + (rotate ? 12 : 14)}
                textAnchor={rotate ? "end" : "middle"}
                fontSize={grid.length > 10 ? 8 : AXIS_FONT_SIZE}
                fill={AXIS_TEXT_COLOR}
                transform={rotate ? `rotate(-45,${cx},${marginTop + chartH + 12})` : undefined}
              >
                {label}
              </text>
            </g>
          )
        })}

        {/* Zero line */}
        {yMin < 0 && (
          <line x1={marginLeft} y1={zeroY} x2={marginLeft + chartW} y2={zeroY} stroke="rgba(255,255,255,.15)" strokeWidth={1} />
        )}

        {/* Axis labels */}
        <text
          x={10}
          y={marginTop + chartH / 2}
          textAnchor="middle"
          fontSize={AXIS_FONT_SIZE}
          fill={AXIS_TEXT_COLOR}
          transform={`rotate(-90,10,${marginTop + chartH / 2})`}
        >
          Avg prediction
        </text>
      </svg>
    </>
  )
}
