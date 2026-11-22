/**
 * The Factor pane's chart (specs/frontend-modelling-optimiser-ui): one factor's actual by
 * level, with the expected of every ticked model drawn over it, and the exposure below.
 * The models share their rows, so their levels are the same and their actuals agree; a
 * model binned otherwise (a run from other rows) is named and not drawn.
 */
import { useState } from "react"
import { STAGE_DASHES } from "./stageResults"
import type { ModelStage } from "./stageSettings"
import type { TrainAvePerFeatureRow } from "../api/types"
import { CHART_COLORS } from "../theme/colors"
import ChartFocusDetail from "../panels/ChartFocusDetail"
import {
  ChartEmptyState,
  ChartLegend,
  type ChartLegendMarker,
  ChartSvg,
  ChartValueGrid,
  ChartValuesTable,
  MODELLING_CHART_AXIS_FONT_SIZE as FONT,
  MODELLING_CHART_AXIS_TEXT_COLOR as TEXT,
  ResponsiveChart,
} from "../panels/modelling/ChartScaffold"
import { chartAxisLabel, chartDomain, chartLabelIndices, chartTicks, formatChartNumber } from "../utils/chartHelpers"

const STAGE_MARKERS: Record<ModelStage, ChartLegendMarker> = { full: "dot", pruned: "ring", banded: "cross", smoothed: "diamond" }

function StageMark({ stage, x, y, color }: { stage?: ModelStage; x: number; y: number; color: string }) {
  if (stage === "banded") return <path d={`M${x - 3} ${y - 3} l6 6 m-6 0 l6 -6`} stroke={color} strokeWidth={1.5} />
  if (stage === "smoothed") return <path d={`M${x} ${y - 4} l4 4 l-4 4 l-4 -4 Z`} fill={color} />
  if (stage === "pruned") return <circle cx={x} cy={y} r={3.5} fill="var(--bg-panel)" stroke={color} strokeWidth={1.5} />
  return <circle cx={x} cy={y} r={3} fill={color} />
}

/** One model's line: its name and colour, its bins for the factor, and whether it fits the factor. */
export interface ModelAveSeries {
  id: string
  name: string
  color: string
  data: TrainAvePerFeatureRow
  /** The model left the factor out: its expected is its prediction, which does not use it. */
  leftOut: boolean
  /** The chosen model, drawn heavier. */
  chosen: boolean
  stage?: ModelStage
  evaluationIdentity?: string
  preview?: boolean
}

/**
 * Whether two runs binned the factor alike: the same levels over the same exposure. A level's
 * label alone can survive a change of rows (a category keeps its name), its exposure cannot.
 */
const sameLevels = (a: TrainAvePerFeatureRow, b: TrainAvePerFeatureRow) =>
  a.bins.length === b.bins.length
  && a.bins.every((bin, i) => (
    bin.label === b.bins[i].label
    && Math.abs(bin.exposure - b.bins[i].exposure) <= 1e-6 * Math.max(1, Math.abs(bin.exposure))
  ))

export function ModelsAveChart({ feature, series }: { feature: string; series: readonly ModelAveSeries[] }) {
  const [activeBin, setActiveBin] = useState<number | null>(null)
  const reference = series[0]
  const drawn = series.filter((entry) => sameLevels(entry.data, reference.data) && entry.evaluationIdentity === reference.evaluationIdentity)
  const other = series.filter((entry) => !drawn.includes(entry))
  const { bins } = reference.data
  if (!bins.length) return <ChartEmptyState>No bins for {feature}</ChartEmptyState>
  const lineLabel = (entry: ModelAveSeries) => (entry.leftOut ? `${entry.name} (not in model)` : entry.name)
  return (
    <>
      <div className="validation-chart-title">
        <div>
          <h4 className="validation-feature-heading">{feature}</h4>
          <p className="validation-chart-description">{reference.data.type} · actual vs expected</p>
        </div>
        <ChartLegend
          items={[
            { label: "Actual", color: CHART_COLORS.actual },
            ...drawn.map((entry) => ({ label: lineLabel(entry), color: entry.color,
              dashArray: entry.data.type === "numeric" ? (entry.preview ? "1 4" : entry.stage ? STAGE_DASHES[entry.stage] : undefined) : undefined,
              marker: entry.data.type !== "numeric" && entry.stage ? STAGE_MARKERS[entry.stage] : undefined })),
          ]}
        />
      </div>
      {other.length > 0 && (
        <p className="mb-2 text-[12px]" style={{ color: "var(--text-muted)" }}>
          {`Binned on other rows, not drawn: ${other.map((entry) => entry.name).join(", ")}`}
        </p>
      )}
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
          const [low, high] = chartDomain([
            ...bins.map((bin) => bin.avg_actual),
            ...drawn.flatMap((entry) => entry.data.bins.map((bin) => bin.avg_predicted)),
          ])
          const y = (value: number) => plotBottom - ((value - low) / (high - low)) * (plotBottom - top)
          const indices = chartLabelIndices(bins.length, plotWidth, 96)
          const line = (values: readonly number[]) => values.map((value, i) => `${i ? "L" : "M"}${x(i)},${y(value)}`).join(" ")
          const numeric = reference.data.type === "numeric"
          const maxExposure = Math.max(...bins.map((bin) => bin.exposure), 0)
          return (
            <>
              <ChartSvg width={width} height={height} ariaLabel={`Actual vs expected for ${feature}`}>
                <text x={left} y={16} fontSize={FONT} fill={TEXT}>Average outcome</text>
                <ChartValueGrid ticks={chartTicks(low, high)} left={left} right={width - right} y={y} labelGap={8} />
                {numeric && (
                  <path data-series="actual-line" d={line(bins.map((bin) => bin.avg_actual))} fill="none" stroke={CHART_COLORS.actual} strokeWidth={2} />
                )}
                {drawn.map((entry) => (
                  <g key={entry.id} data-series={`model-${entry.id}`} aria-label={lineLabel(entry)}>
                    {numeric && (
                      <path
                        d={line(entry.data.bins.map((bin) => bin.avg_predicted))}
                        fill="none"
                        stroke={entry.color}
                        strokeWidth={entry.chosen ? 2.5 : 1.5}
                        strokeDasharray={entry.preview ? "1 4" : entry.stage ? STAGE_DASHES[entry.stage] : undefined}
                      />
                    )}
                    {entry.data.bins.map((bin, i) => (
                      <StageMark key={i} stage={entry.stage} x={x(i)} y={y(bin.avg_predicted)} color={entry.color} />
                    ))}
                  </g>
                ))}
                {bins.map((bin, i) => (
                  <g key={i}>
                    <circle cx={x(i)} cy={y(bin.avg_actual)} r={3.5} fill={CHART_COLORS.actual} />
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
                      aria-label={`${bin.label}. Actual: ${bin.avg_actual}. ${drawn.map((entry) => `${entry.name}: ${entry.data.bins[i].avg_predicted}`).join(". ")}. Exposure: ${bin.exposure}`}
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
                <text x={left + plotWidth / 2} y={height - 6} textAnchor="middle" fontSize={FONT} fill={TEXT}>
                  <title>{feature}</title>
                  {chartAxisLabel(feature, plotWidth)}
                </text>
                {activeBin !== null && (
                  <line x1={x(activeBin)} x2={x(activeBin)} y1={top} y2={plotBottom} stroke={TEXT} opacity={0.4} pointerEvents="none" />
                )}
              </ChartSvg>
              <ChartSvg width={width} height={86} ariaLabel={`Exposure for ${feature}`}>
                <text x={left} y={15} fontSize={FONT} fill={TEXT}>Exposure</text>
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
                      <title>{bin.label}: {bin.exposure.toLocaleString()} exposure</title>
                    </rect>
                  )
                })}
              </ChartSvg>
            </>
          )
        }}
      </ResponsiveChart>
      <ChartFocusDetail>
        {activeBin !== null && (
          <>
            <strong>{bins[activeBin].label}</strong>
            <span>Actual: {formatChartNumber(bins[activeBin].avg_actual)}</span>
            {drawn.map((entry) => (
              <span key={entry.id}>{entry.name}: {formatChartNumber(entry.data.bins[activeBin].avg_predicted)}</span>
            ))}
            <span>Exposure: {formatChartNumber(bins[activeBin].exposure)}</span>
          </>
        )}
      </ChartFocusDetail>
      <ChartValuesTable
        summary="View bin values"
        ariaLabel="AvE bin values"
        headers={["Bin", "Actual", ...drawn.map((entry) => entry.name), "Exposure"]}
        rows={bins.map((bin, i) => [
          bin.label,
          formatChartNumber(bin.avg_actual),
          ...drawn.map((entry) => formatChartNumber(entry.data.bins[i].avg_predicted)),
          formatChartNumber(bin.exposure),
        ])}
      />
    </>
  )
}
