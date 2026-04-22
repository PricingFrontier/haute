/**
 * Actual vs Expected (AvE) tab for the ModellingPreview panel.
 *
 * Left panel: feature browser (search + importance bars)
 * Right panel: dual-axis SVG chart for the selected feature
 *   - Bars = exposure (grey, right y-axis)
 *   - Lines = avg_actual (green) + avg_predicted (purple)
 *   - X-axis = bin labels
 */
import { useState, useMemo, useCallback, useEffect } from "react"
import type { TrainResult } from "../../stores/useNodeResultsStore"
import { CHART_COLORS } from "../../theme/colors"
import { FeatureBrowser, type FeatureItem } from "./FeatureBrowser"

interface AveTabProps {
  result: TrainResult
}

const GRID_COLOR = "rgba(255,255,255,.06)"
const AXIS_TEXT_COLOR = "var(--text-muted)"
const AXIS_FONT_SIZE = 10
const ACTUAL_COLOR = CHART_COLORS.actual
const PREDICTED_COLOR = CHART_COLORS.predicted
const EXPOSURE_COLOR = "rgba(255,255,255,.12)"

type AveBin = { label: string; exposure: number; avg_actual: number; avg_predicted: number }
type AveFeature = { feature: string; type: string; bins: AveBin[] }

export function AveTab({ result }: AveTabProps) {
  const aveData = result.ave_per_feature

  // Build feature list sorted by importance
  const featureItems: FeatureItem[] = useMemo(() => {
    if (!aveData || aveData.length === 0) return []
    // Build importance lookup from feature_importance
    const impMap = new Map<string, number>()
    for (const fi of result.feature_importance) {
      impMap.set(fi.feature, fi.importance)
    }
    return aveData.map(f => ({
      feature: f.feature,
      importance: impMap.get(f.feature) ?? 0,
    }))
  }, [aveData, result.feature_importance])

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

  const selectedData: AveFeature | null = useMemo(() => {
    if (!selectedFeature || !aveData) return null
    return aveData.find(f => f.feature === selectedFeature) ?? null
  }, [selectedFeature, aveData])

  if (!aveData || aveData.length === 0) {
    return (
      <div className="flex items-center justify-center h-full text-xs" style={{ color: "var(--text-muted)" }}>
        No AvE data available
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
          <AveChart data={selectedData} />
        ) : (
          <div className="flex items-center justify-center h-full text-xs" style={{ color: "var(--text-muted)" }}>
            Select a feature
          </div>
        )}
      </div>
    </div>
  )
}

// ─── AvE Chart ────────────────────────────────────────────────────

function AveChart({ data }: { data: AveFeature }) {
  const bins = data.bins
  if (bins.length === 0) {
    return (
      <div className="text-xs" style={{ color: "var(--text-muted)" }}>No bins for {data.feature}</div>
    )
  }

  const width = 520
  const height = 240
  const marginLeft = 55
  const marginRight = 55
  const marginTop = 24
  const marginBottom = 50
  const chartW = width - marginLeft - marginRight
  const chartH = height - marginTop - marginBottom

  // Scales
  const allAvgVals = bins.flatMap(b => [b.avg_actual, b.avg_predicted])
  const yMin = allAvgVals.reduce((a, b) => Math.min(a, b), Infinity)
  const yMax = allAvgVals.reduce((a, b) => Math.max(a, b), -Infinity)
  const yPad = (yMax - yMin) * 0.1 || 0.001
  const yLo = yMin - yPad
  const yHi = yMax + yPad
  const ySpan = yHi - yLo

  const maxExposure = bins.map(b => b.exposure).reduce((a, b) => Math.max(a, b), -Infinity)

  const barGroupW = chartW / bins.length
  const barW = barGroupW * 0.6

  const xCenter = (i: number) => marginLeft + i * barGroupW + barGroupW / 2
  const yScale = (v: number) => marginTop + chartH - ((v - yLo) / ySpan) * chartH
  const yExpScale = (v: number) => marginTop + chartH - (v / (maxExposure || 1)) * chartH

  // Grid
  const nGridY = 4
  const gridYValues = Array.from({ length: nGridY + 1 }, (_, i) => yLo + (i / nGridY) * ySpan)

  // Line paths
  const actualPath = bins
    .map((b, i) => `${i === 0 ? "M" : "L"}${xCenter(i).toFixed(1)},${yScale(b.avg_actual).toFixed(1)}`)
    .join(" ")
  const predictedPath = bins
    .map((b, i) => `${i === 0 ? "M" : "L"}${xCenter(i).toFixed(1)},${yScale(b.avg_predicted).toFixed(1)}`)
    .join(" ")

  // Truncate long labels
  const truncLabel = (s: string, maxLen: number) => s.length > maxLen ? s.slice(0, maxLen - 1) + "\u2026" : s

  return (
    <div>
      <div className="text-xs font-medium mb-1" style={{ color: "var(--text-primary)" }}>
        {data.feature}
        <span className="ml-2 text-[10px]" style={{ color: "var(--text-muted)" }}>({data.type})</span>
      </div>
      <svg width={width} height={height} style={{ background: "var(--bg-input)", borderRadius: 6, border: "1px solid var(--border)" }}>
        {/* Horizontal grid lines + left y-axis labels */}
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

        {/* Right y-axis labels (exposure) */}
        {[0, 0.5, 1].map((frac, i) => {
          const expVal = frac * maxExposure
          const y = yExpScale(expVal)
          return (
            <text key={`exp-${i}`} x={width - marginRight + 6} y={y + 3} textAnchor="start" fontSize={AXIS_FONT_SIZE} fill={AXIS_TEXT_COLOR} opacity={0.5}>
              {expVal >= 1000 ? `${(expVal / 1000).toFixed(0)}K` : expVal.toFixed(0)}
            </text>
          )
        })}

        {/* Exposure bars */}
        {bins.map((b, i) => {
          const cx = xCenter(i)
          const barH = (b.exposure / (maxExposure || 1)) * chartH
          return (
            <rect
              key={`exp-${i}`}
              x={cx - barW / 2}
              y={marginTop + chartH - barH}
              width={barW}
              height={barH}
              fill={EXPOSURE_COLOR}
              rx={1}
            />
          )
        })}

        {/* Actual line */}
        <path d={actualPath} fill="none" stroke={ACTUAL_COLOR} strokeWidth={1.5} />
        {bins.map((b, i) => (
          <circle key={`a-${i}`} cx={xCenter(i)} cy={yScale(b.avg_actual)} r={2.5} fill={ACTUAL_COLOR} />
        ))}

        {/* Predicted line */}
        <path d={predictedPath} fill="none" stroke={PREDICTED_COLOR} strokeWidth={1.5} />
        {bins.map((b, i) => (
          <circle key={`p-${i}`} cx={xCenter(i)} cy={yScale(b.avg_predicted)} r={2.5} fill={PREDICTED_COLOR} />
        ))}

        {/* X-axis bin labels */}
        {bins.map((b, i) => {
          const cx = xCenter(i)
          const label = truncLabel(b.label, 10)
          // Rotate labels if there are many bins
          const rotate = bins.length > 6
          return (
            <text
              key={`xl-${i}`}
              x={cx}
              y={marginTop + chartH + (rotate ? 12 : 14)}
              textAnchor={rotate ? "end" : "middle"}
              fontSize={bins.length > 12 ? 8 : AXIS_FONT_SIZE}
              fill={AXIS_TEXT_COLOR}
              transform={rotate ? `rotate(-45,${cx},${marginTop + chartH + 12})` : undefined}
            >
              {label}
            </text>
          )
        })}

        {/* Axis labels */}
        <text
          x={10}
          y={marginTop + chartH / 2}
          textAnchor="middle"
          fontSize={AXIS_FONT_SIZE}
          fill={AXIS_TEXT_COLOR}
          transform={`rotate(-90,10,${marginTop + chartH / 2})`}
        >
          Avg value
        </text>
        <text
          x={width - 8}
          y={marginTop + chartH / 2}
          textAnchor="middle"
          fontSize={AXIS_FONT_SIZE}
          fill={AXIS_TEXT_COLOR}
          opacity={0.5}
          transform={`rotate(90,${width - 8},${marginTop + chartH / 2})`}
        >
          Exposure
        </text>
      </svg>

      {/* Legend */}
      <div className="flex gap-4 mt-1.5 text-[11px]" style={{ color: "var(--text-muted)" }}>
        <span className="flex items-center gap-1.5">
          <span className="inline-block w-3 h-0.5 rounded" style={{ background: ACTUAL_COLOR }} />
          Actual
        </span>
        <span className="flex items-center gap-1.5">
          <span className="inline-block w-3 h-0.5 rounded" style={{ background: PREDICTED_COLOR }} />
          Predicted
        </span>
        <span className="flex items-center gap-1.5">
          <span className="inline-block w-3 h-2 rounded-sm" style={{ background: EXPOSURE_COLOR }} />
          Exposure
        </span>
      </div>
    </div>
  )
}
