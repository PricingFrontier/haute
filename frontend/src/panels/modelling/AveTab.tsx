/** Actual/expected outcomes and aligned exposure, sharing feature context with PDP. */
import { useState } from "react"
import type { TrainAvePerFeatureRow } from "../../api/types"
import type { TrainResult } from "../../stores/useNodeResultsStore"
import { CHART_COLORS } from "../../theme/colors"
import {
  ChartEmptyState,
  ChartLegend,
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

export function AveTab({
  result,
  featureBrowser,
}: {
  result: TrainResult
  featureBrowser?: SharedFeatureBrowser
}) {
  return (
    <FeatureDiagnosticTab
      result={result}
      rows={result.ave_per_feature}
      featureBrowser={featureBrowser}
      noun="AvE"
      renderChart={(data) => <AveChart key={data.feature} data={data} />}
    />
  )
}

function AveChart({ data }: { data: TrainAvePerFeatureRow }) {
  const [activeBin, setActiveBin] = useState<number | null>(null)
  const { bins } = data
  if (!bins.length) return <ChartEmptyState>No bins for {data.feature}</ChartEmptyState>
  const selected = activeBin === null ? null : bins[activeBin]
  return (
    <>
      <div className="validation-chart-title">
        <div>
          <h4 className="validation-feature-heading">{data.feature}</h4>
          <p className="validation-chart-description">{data.type} · actual vs expected</p>
        </div>
        <ChartLegend
          items={[
            { label: "Actual", color: CHART_COLORS.actual },
            { label: "Expected", color: CHART_COLORS.predicted, swatch: "dashed" },
          ]}
        />
      </div>
      <ResponsiveChart>
        {(width) => {
          const left = 68,
            right = 20,
            top = 32,
            plotBottom = 240,
            height = 292
          const plotWidth = Math.max(1, width - left - right)
          const groupWidth = plotWidth / bins.length
          const x = (i: number) => left + (i + 0.5) * groupWidth
          const [low, high] = chartDomain(
            bins.flatMap((bin) => [bin.avg_actual, bin.avg_predicted]),
          )
          const y = (value: number) =>
            plotBottom - ((value - low) / (high - low)) * (plotBottom - top)
          const indices = chartLabelIndices(bins.length, plotWidth, 96)
          const path = (kind: "avg_actual" | "avg_predicted") =>
            bins.map((bin, i) => `${i ? "L" : "M"}${x(i)},${y(bin[kind])}`).join(" ")
          const maxExposure = Math.max(...bins.map((bin) => bin.exposure), 0)
          return (
            <>
              <ChartSvg
                width={width}
                height={height}
                ariaLabel={`Actual vs expected for ${data.feature}`}
              >
                <text x={left} y={16} fontSize={FONT} fill={TEXT}>
                  Average outcome
                </text>
                <ChartValueGrid ticks={chartTicks(low, high)} left={left} right={width - right} y={y} labelGap={8} />
                {data.type === "numeric" && (
                  <>
                    <path
                      data-series="actual-line"
                      d={path("avg_actual")}
                      fill="none"
                      stroke={CHART_COLORS.actual}
                      strokeWidth={2}
                    />
                    <path
                      d={path("avg_predicted")}
                      fill="none"
                      stroke={CHART_COLORS.predicted}
                      strokeWidth={2}
                      strokeDasharray="6 4"
                    />
                  </>
                )}
                {bins.map((bin, i) => (
                  <g key={i}>
                    <circle cx={x(i)} cy={y(bin.avg_actual)} r={3.5} fill={CHART_COLORS.actual} />
                    <rect
                      x={x(i) - 3}
                      y={y(bin.avg_predicted) - 3}
                      width={6}
                      height={6}
                      fill={CHART_COLORS.predicted}
                    />
                    {indices.has(i) && (
                      <text
                        x={x(i)}
                        y={plotBottom + 20}
                        textAnchor={i === 0 ? "start" : i === bins.length - 1 ? "end" : "middle"}
                        fontSize={FONT}
                        fill={TEXT}
                      >
                        {bin.label.length > 12 ? `${bin.label.slice(0, 11)}…` : bin.label}
                      </text>
                    )}
                    <rect
                      x={left + i * groupWidth}
                      y={top}
                      width={groupWidth}
                      height={plotBottom - top}
                      fill="transparent"
                      role="button"
                      tabIndex={0}
                      aria-label={`${bin.label}. Actual: ${bin.avg_actual}. Expected: ${bin.avg_predicted}. Exposure: ${bin.exposure}`}
                      onMouseEnter={() => setActiveBin(i)}
                      onFocus={() => setActiveBin(i)}
                      onClick={() => setActiveBin(i)}
                      onKeyDown={(event) => {
                        if (event.key === "Enter" || event.key === " ") {
                          event.preventDefault()
                          setActiveBin(i)
                        }
                      }}
                      style={{ outlineOffset: -2 }}
                    />
                  </g>
                ))}
                <text
                  x={left + plotWidth / 2}
                  y={height - 6}
                  textAnchor="middle"
                  fontSize={FONT}
                  fill={TEXT}
                >
                  <title>{data.feature}</title>
                  {chartAxisLabel(data.feature, plotWidth)}
                </text>
                {activeBin !== null && (
                  <line
                    x1={x(activeBin)}
                    x2={x(activeBin)}
                    y1={top}
                    y2={plotBottom}
                    stroke={TEXT}
                    opacity={0.4}
                    pointerEvents="none"
                  />
                )}
              </ChartSvg>
              <ChartSvg width={width} height={86} ariaLabel={`Exposure for ${data.feature}`}>
                <text x={left} y={15} fontSize={FONT} fill={TEXT}>
                  Exposure
                </text>
                <text x={width - right} y={15} textAnchor="end" fontSize={FONT} fill={TEXT}>
                  0–{formatChartNumber(maxExposure)}
                </text>
                {bins.map((bin, i) => {
                  const barHeight = maxExposure > 0 ? (bin.exposure / maxExposure) * 48 : 0
                  return (
                    <rect
                      key={i}
                      x={x(i) - groupWidth * 0.3}
                      y={78 - barHeight}
                      width={groupWidth * 0.6}
                      height={barHeight}
                      rx={2}
                      fill="var(--text-muted)"
                      opacity={activeBin === i ? 0.65 : 0.3}
                    >
                      <title>
                        {bin.label}: {bin.exposure.toLocaleString()} exposure
                      </title>
                    </rect>
                  )
                })}
              </ChartSvg>
            </>
          )
        }}
      </ResponsiveChart>
      <div className="validation-bin-detail" role="status" aria-live="polite">
        {selected ? (
          <>
            <strong>{selected.label}</strong>
            <span>Actual: {selected.avg_actual}</span>
            <span>Expected: {selected.avg_predicted}</span>
            <span>Exposure: {selected.exposure.toLocaleString()}</span>
          </>
        ) : (
          <span>Hover or focus a bin to inspect its values.</span>
        )}
      </div>
      <ChartValuesTable
        summary="View bin values"
        ariaLabel="AvE bin values"
        headers={["Bin", "Actual", "Expected", "Exposure"]}
        rows={bins.map((bin) => [bin.label, bin.avg_actual, bin.avg_predicted, bin.exposure.toLocaleString()])}
      />
    </>
  )
}
