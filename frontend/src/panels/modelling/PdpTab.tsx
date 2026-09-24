/** Partial dependence with the same feature context as actual/expected diagnostics. */
import { useState } from "react"
import type { PdpFeatureRow } from "../../api/types"
import type { TrainResult } from "../../stores/useNodeResultsStore"
import { CHART_COLORS } from "../../theme/colors"
import {
  ChartEmptyState,
  ChartSvg,
  ChartValueGrid,
  ChartValuesTable,
  MODELLING_CHART_AXIS_FONT_SIZE as FONT,
  MODELLING_CHART_AXIS_TEXT_COLOR as TEXT,
  ResponsiveChart,
} from "./ChartScaffold"
import {
  chartAxisLabel,
  chartDomain,
  chartLabelIndices,
  chartTicks,
  formatChartNumber,
} from "../../utils/chartHelpers"
import { FeatureDiagnosticTab } from "./FeatureDiagnosticTab"
import type { SharedFeatureBrowser } from "./useDiagnosticFeature"

const levelLabel = (value: string | number | null) => (value === null ? "(missing)" : String(value))

export function PdpTab({
  result,
  featureBrowser,
}: {
  result: TrainResult
  featureBrowser?: SharedFeatureBrowser
}) {
  return (
    <FeatureDiagnosticTab
      result={result}
      rows={result.pdp_data}
      featureBrowser={featureBrowser}
      noun="PDP"
      renderChart={(data) => <PdpChart key={data.feature} data={data} />}
    />
  )
}

function PdpChart({ data }: { data: PdpFeatureRow }) {
  const [activePoint, setActivePoint] = useState<number | null>(null)
  if (data.error || data.error_type)
    return (
      <div
        role="alert"
        aria-label="PDP diagnostic failed"
        className="rounded-md px-4 py-3 text-[13px]"
        style={{
          background: "var(--warning-soft-subtle)",
          border: "1px solid var(--warning-border)",
        }}
      >
        <div className="font-medium" style={{ color: "var(--text-primary)" }}>
          PDP unavailable for {data.feature}
        </div>
        {data.error_type && (
          <div className="mt-2 font-mono text-xs" style={{ color: "var(--warning)" }}>
            {data.error_type}
          </div>
        )}
        {data.error && (
          <div className="mt-1 break-words whitespace-pre-wrap" style={{ color: "var(--warning)" }}>
            {data.error}
          </div>
        )}
      </div>
    )
  if (!data.grid.length) return <ChartEmptyState>No PDP data for {data.feature}</ChartEmptyState>
  const selected = activePoint === null ? null : data.grid[activePoint]
  return (
    <>
      <div className="validation-chart-title">
        <div>
          <h4 className="validation-feature-heading">{data.feature}</h4>
          <p className="validation-chart-description">{data.type} · partial dependence</p>
        </div>
      </div>
      <ResponsiveChart>
        {(width) => {
          const left = 68,
            right = 24,
            top = 32,
            bottom = 248,
            height = 300
          const plotWidth = Math.max(1, width - left - right)
          const values = data.grid.map((point) => point.avg_prediction)
          if (data.type !== "numeric") {
            const [low, high] = chartDomain(values, true)
            const barWidth = Math.max(1, width * 0.6 - 76)
            const x = (value: number) => 4 + ((value - low) / (high - low)) * (barWidth - 8)
            return (
              <div role="img" aria-label={`Partial dependence for ${data.feature}`}>
                <div className="mb-3 text-xs" style={{ color: TEXT }}>
                  Average prediction · baseline at zero
                </div>
                {data.grid.map((point, i) => (
                  <div
                    key={i}
                    className="grid items-center gap-3 py-2"
                    style={{
                      gridTemplateColumns: "minmax(0, 2fr) minmax(0, 3fr)",
                      borderBottom: "1px solid var(--border)",
                    }}
                  >
                    <span
                      className="break-words text-[13px]"
                      style={{ color: "var(--text-secondary)" }}
                    >
                      {levelLabel(point.value)}
                    </span>
                    <div className="flex min-w-0 items-center gap-2">
                      <svg width={barWidth} height={28} className="shrink-0" aria-hidden="true">
                        <line x1={x(0)} x2={x(0)} y1={2} y2={26} stroke={TEXT} />
                        <rect
                          x={Math.min(x(0), x(point.avg_prediction))}
                          y={6}
                          width={Math.abs(x(point.avg_prediction) - x(0))}
                          height={16}
                          rx={2}
                          fill={CHART_COLORS.predicted}
                          opacity={0.8}
                        />
                      </svg>
                      <span className="text-xs tabular-nums" title={String(point.avg_prediction)}>
                        {formatChartNumber(point.avg_prediction)}
                      </span>
                    </div>
                  </div>
                ))}
              </div>
            )
          }
          const [low, high] = chartDomain(values)
          const xValues = data.grid.map((point) => Number(point.value))
          const [xLow, xHigh] = chartDomain(xValues)
          const x = (value: number) => left + ((value - xLow) / (xHigh - xLow)) * plotWidth
          const y = (value: number) => bottom - ((value - low) / (high - low)) * (bottom - top)
          const indices = chartLabelIndices(data.grid.length, plotWidth)
          const path = data.grid
            .map(
              (point, i) => `${i ? "L" : "M"}${x(Number(point.value))},${y(point.avg_prediction)}`,
            )
            .join(" ")
          return (
            <ChartSvg
              width={width}
              height={height}
              ariaLabel={`Partial dependence for ${data.feature}`}
            >
              <text x={left} y={16} fontSize={FONT} fill={TEXT}>
                Average prediction
              </text>
              <ChartValueGrid ticks={chartTicks(low, high)} left={left} right={width - right} y={y} labelGap={8} />
              <path d={path} fill="none" stroke={CHART_COLORS.predicted} strokeWidth={2} />
              {data.grid.map((point, i) => (
                <g key={i}>
                  <circle
                    cx={x(Number(point.value))}
                    cy={y(point.avg_prediction)}
                    r={3}
                    fill={CHART_COLORS.predicted}
                  />
                  <circle
                    cx={x(Number(point.value))}
                    cy={y(point.avg_prediction)}
                    r={10}
                    fill="transparent"
                    role="button"
                    tabIndex={0}
                    aria-label={`${data.feature}: ${levelLabel(point.value)}. Average prediction: ${point.avg_prediction}`}
                    onMouseEnter={() => setActivePoint(i)}
                    onFocus={() => setActivePoint(i)}
                    onClick={() => setActivePoint(i)}
                    onKeyDown={(event) => {
                      if (event.key === "Enter" || event.key === " ") {
                        event.preventDefault()
                        setActivePoint(i)
                      }
                    }}
                  />
                  {indices.has(i) && (
                    <text
                      x={x(Number(point.value))}
                      y={bottom + 22}
                      textAnchor="middle"
                      fontSize={FONT}
                      fill={TEXT}
                    >
                      {formatChartNumber(Number(point.value))}
                    </text>
                  )}
                </g>
              ))}
              <text
                x={left + plotWidth / 2}
                y={height - 5}
                textAnchor="middle"
                fontSize={FONT}
                fill={TEXT}
              >
                <title>{data.feature}</title>
                {chartAxisLabel(data.feature, plotWidth)}
              </text>
            </ChartSvg>
          )
        }}
      </ResponsiveChart>
      {data.type === "numeric" && (
        <div className="validation-bin-detail" role="status" aria-live="polite">
          {selected ? (
            <>
              <strong>
                {data.feature}: {levelLabel(selected.value)}
              </strong>
              <span>Average prediction: {selected.avg_prediction}</span>
            </>
          ) : (
            <span>Hover or focus a point to inspect its prediction.</span>
          )}
        </div>
      )}
      <ChartValuesTable
        summary="View prediction values"
        ariaLabel="PDP values"
        headers={[data.feature, "Average prediction"]}
        rows={data.grid.map((point) => [levelLabel(point.value), point.avg_prediction])}
      />
    </>
  )
}
