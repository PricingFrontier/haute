/**
 * SHAP curves: each feature's mean SHAP value per value band or level over the
 * sampled rows, with the 10th to 90th percentile range. Under a log link the
 * values read as relativities, exp of the SHAP statistics, around 1.0.
 */
import { useState } from "react"
import type { TrainShapCurveFeature, TrainShapCurvePoint } from "../../api/types"
import type { TrainResult } from "../../stores/useNodeResultsStore"
import { CHART_COLORS } from "../../theme/colors"
import { chartAxisLabel, chartDomain, chartLabelIndices, chartTicks, formatChartNumber } from "../../utils/chartHelpers"
import { RELATIVITY_ABOVE_COLOR, RELATIVITY_BELOW_COLOR, RelativityBars } from "../RelativityBars"
import {
  ChartLegend,
  ChartSvg,
  ChartValueGrid,
  ChartValuesTable,
  MODELLING_CHART_AXIS_FONT_SIZE as FONT,
  MODELLING_CHART_AXIS_TEXT_COLOR as TEXT,
  ResponsiveChart,
} from "./ChartScaffold"
import { FeatureDiagnosticTab } from "./FeatureDiagnosticTab"
import type { SharedFeatureBrowser } from "./useDiagnosticFeature"

/** How SHAP statistics are shown: as relativities under a log link, else as they are. */
type CurveScale = {
  relativity: boolean
  baseline: number
  show: (shap: number) => number
  axisLabel: string
}

const RELATIVITY_SCALE: CurveScale = {
  relativity: true,
  baseline: 1,
  show: Math.exp,
  axisLabel: "Relativity (exp of mean SHAP)",
}
const SHAP_SCALE: CurveScale = {
  relativity: false,
  baseline: 0,
  show: (shap) => shap,
  axisLabel: "Mean SHAP value (link scale)",
}

// ── Numeric chart geometry (pixels), as PDP's ────────────────────
const LEFT = 68
const RIGHT = 24
const TOP = 32
const BOTTOM = 248
const HEIGHT = 300
/** Room right of the curve for the missing-value point. */
const MISSING_AREA = 72
/** Rows strip width beside a categorical curve's bars. */
const ROWS_STRIP_WIDTH = 120

const RANGE_FILL_OPACITY = 0.16

function curveScale(link: TrainResult["shap_link"]): CurveScale {
  if (link === null) throw new Error("SHAP curves need the result's shap_link to label their scale.")
  return link === "log" ? RELATIVITY_SCALE : SHAP_SCALE
}

function pointLabel(curve: TrainShapCurveFeature, point: TrainShapCurvePoint): string {
  if (point.value === null) return "(missing)"
  if (curve.kind === "categorical" || point.low === null || point.high === null) return String(point.value)
  return point.low === point.high
    ? formatChartNumber(point.low)
    : `${formatChartNumber(point.low)} to ${formatChartNumber(point.high)}`
}

function describePoint(curve: TrainShapCurveFeature, point: TrainShapCurvePoint, scale: CurveScale): string {
  const relativity = scale.relativity ? `, relativity ${formatChartNumber(Math.exp(point.mean_shap))}` : ""
  return (
    `${curve.feature} ${pointLabel(curve, point)}: ${point.rows.toLocaleString()} rows, `
    + `mean SHAP ${formatChartNumber(point.mean_shap)}${relativity}, 10th to 90th percentile `
    + `${formatChartNumber(scale.show(point.p10_shap))} to ${formatChartNumber(scale.show(point.p90_shap))}`
  )
}

export function ShapCurvesTab({
  result,
  featureBrowser,
}: {
  result: TrainResult
  featureBrowser?: SharedFeatureBrowser
}) {
  const scale = curveScale(result.shap_link)
  return (
    <FeatureDiagnosticTab
      result={result}
      rows={result.shap_curves}
      featureBrowser={featureBrowser}
      noun="SHAP curve"
      renderChart={(curve) => <ShapCurve key={curve.feature} curve={curve} scale={scale} />}
    />
  )
}

function ShapCurve({ curve, scale }: { curve: TrainShapCurveFeature; scale: CurveScale }) {
  const [activeIndex, setActiveIndex] = useState<number | null>(null)
  const active = activeIndex === null ? null : curve.points[activeIndex]
  const rows = curve.points.reduce((total, point) => total + point.rows, 0)
  // A numeric curve's missing rows are not a band; a missing level is a level.
  const bands = curve.points.filter((point) => point.value !== null).length
  const groups =
    curve.kind === "numeric"
      ? `${bands} bands${bands < curve.points.length ? " and missing values" : ""}`
      : `${curve.points.length} levels`
  return (
    <>
      <div className="validation-chart-title">
        <div>
          <h4 className="validation-feature-heading">{curve.feature}</h4>
          <p className="validation-chart-description">
            {`${curve.kind} · ${groups} · ${rows.toLocaleString()} sampled rows`}
            {scale.relativity ? " · relativity = exp(mean SHAP)" : ""}
          </p>
        </div>
      </div>
      {curve.kind === "numeric" ? (
        <NumericCurve curve={curve} scale={scale} activeIndex={activeIndex} onActivate={setActiveIndex} />
      ) : (
        <CategoricalCurve curve={curve} scale={scale} activeIndex={activeIndex} onActivate={setActiveIndex} />
      )}
      <div className="validation-bin-detail" role="status" aria-live="polite">
        {active && (
          <>
            <strong>
              {curve.feature}: {pointLabel(curve, active)}
            </strong>
            <span>Rows: {active.rows.toLocaleString()}</span>
            <span>Mean SHAP: {formatChartNumber(active.mean_shap)}</span>
            {scale.relativity && <span>Relativity: {formatChartNumber(Math.exp(active.mean_shap))}</span>}
            <span>
              10th to 90th percentile: {formatChartNumber(scale.show(active.p10_shap))} to{" "}
              {formatChartNumber(scale.show(active.p90_shap))}
            </span>
          </>
        )}
      </div>
      <ChartValuesTable
        summary="View SHAP curve values"
        ariaLabel={`${curve.feature} SHAP curve values`}
        headers={[
          curve.kind === "numeric" ? "Band" : "Level",
          "Rows",
          ...(curve.kind === "numeric" ? ["Mean value"] : []),
          "Mean SHAP",
          "10th pct SHAP",
          "90th pct SHAP",
          ...(scale.relativity ? ["Relativity"] : []),
        ]}
        rows={curve.points.map((point) => [
          pointLabel(curve, point),
          point.rows.toLocaleString(),
          ...(curve.kind === "numeric"
            ? [typeof point.value === "number" ? formatChartNumber(point.value) : "—"]
            : []),
          formatChartNumber(point.mean_shap),
          formatChartNumber(point.p10_shap),
          formatChartNumber(point.p90_shap),
          ...(scale.relativity ? [formatChartNumber(Math.exp(point.mean_shap))] : []),
        ])}
      />
    </>
  )
}

type CurveChartProps = {
  curve: TrainShapCurveFeature
  scale: CurveScale
  activeIndex: number | null
  onActivate: (index: number) => void
}

function NumericCurve({ curve, scale, activeIndex, onActivate }: CurveChartProps) {
  const bands = curve.points
    .map((point, index) => ({ point, index }))
    .filter(({ point }) => typeof point.value === "number")
  const missingIndex = curve.points.findIndex((point) => point.value === null)
  const shown = curve.points.flatMap((point) => [
    scale.show(point.mean_shap),
    scale.show(point.p10_shap),
    scale.show(point.p90_shap),
  ])
  const [paddedLow, high] = chartDomain([...shown, scale.baseline])
  // A relativity is never negative, so its axis stops at zero.
  const low = scale.relativity ? Math.max(0, paddedLow) : paddedLow
  const xValues = bands.flatMap(({ point }) => [point.low as number, point.high as number])

  return (
    <>
      <ChartLegend
        compact
        items={[
          { label: scale.relativity ? "Relativity" : "Mean SHAP", color: CHART_COLORS.predicted },
          {
            label: "10th to 90th percentile",
            color: CHART_COLORS.predicted,
            swatch: "bar",
            opacity: RANGE_FILL_OPACITY * 2,
          },
          { label: `Baseline ${scale.baseline}`, color: TEXT, swatch: "dashed" },
        ]}
      />
      <ResponsiveChart>
        {(width) => {
          const right = RIGHT + (missingIndex >= 0 ? MISSING_AREA : 0)
          const plotWidth = Math.max(1, width - LEFT - right)
          const [xLow, xHigh] = xValues.length ? chartDomain(xValues) : [0, 1]
          const x = (value: number) => LEFT + ((value - xLow) / (xHigh - xLow)) * plotWidth
          const y = (value: number) => BOTTOM - ((value - low) / (high - low)) * (BOTTOM - TOP)
          const labelled = chartLabelIndices(bands.length, plotWidth)
          const line = bands
            .map(({ point }, i) => `${i ? "L" : "M"}${x(point.value as number)},${y(scale.show(point.mean_shap))}`)
            .join(" ")
          const range = [
            ...bands.map(({ point }) => `${x(point.value as number)},${y(scale.show(point.p90_shap))}`),
            ...[...bands].reverse().map(({ point }) => `${x(point.value as number)},${y(scale.show(point.p10_shap))}`),
          ]
          const missingX = width - right + MISSING_AREA / 2
          const marker = (index: number, cx: number) => {
            const point = curve.points[index]
            return (
              <g key={index}>
                <circle
                  cx={cx}
                  cy={y(scale.show(point.mean_shap))}
                  r={index === activeIndex ? 4.5 : 3}
                  fill={CHART_COLORS.predicted}
                />
                <circle
                  data-testid="shap-curve-point"
                  cx={cx}
                  cy={y(scale.show(point.mean_shap))}
                  r={10}
                  fill="transparent"
                  role="button"
                  tabIndex={0}
                  aria-label={describePoint(curve, point, scale)}
                  aria-pressed={index === activeIndex}
                  onMouseEnter={() => onActivate(index)}
                  onFocus={() => onActivate(index)}
                  onClick={() => onActivate(index)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" || event.key === " ") {
                      event.preventDefault()
                      onActivate(index)
                    }
                  }}
                />
              </g>
            )
          }
          return (
            <ChartSvg width={width} height={HEIGHT} ariaLabel={`SHAP curve for ${curve.feature}`}>
              <text x={LEFT} y={16} fontSize={FONT} fill={TEXT}>
                {scale.axisLabel}
              </text>
              <ChartValueGrid ticks={chartTicks(low, high)} left={LEFT} right={width - right} y={y} labelGap={8} />
              <line
                data-testid="shap-curve-baseline"
                x1={LEFT}
                x2={width - RIGHT}
                y1={y(scale.baseline)}
                y2={y(scale.baseline)}
                stroke={TEXT}
                strokeDasharray="4,4"
              />
              {bands.length > 0 && (
                <>
                  <polygon
                    data-testid="shap-curve-range"
                    points={range.join(" ")}
                    fill={CHART_COLORS.predicted}
                    opacity={RANGE_FILL_OPACITY}
                  />
                  <path data-testid="shap-curve-line" d={line} fill="none" stroke={CHART_COLORS.predicted} strokeWidth={2} />
                </>
              )}
              {bands.map(({ point, index }, i) => (
                <g key={index}>
                  {marker(index, x(point.value as number))}
                  {labelled.has(i) && (
                    <text x={x(point.value as number)} y={BOTTOM + 22} textAnchor="middle" fontSize={FONT} fill={TEXT}>
                      {formatChartNumber(point.value as number)}
                    </text>
                  )}
                </g>
              ))}
              {missingIndex >= 0 && (
                <g data-testid="shap-curve-missing">
                  <line x1={width - right + 8} x2={width - right + 8} y1={TOP} y2={BOTTOM} stroke={TEXT} opacity={0.3} />
                  <line
                    x1={missingX}
                    x2={missingX}
                    y1={y(scale.show(curve.points[missingIndex].p10_shap))}
                    y2={y(scale.show(curve.points[missingIndex].p90_shap))}
                    stroke={CHART_COLORS.predicted}
                    strokeWidth={2}
                  />
                  {marker(missingIndex, missingX)}
                  <text x={missingX} y={BOTTOM + 22} textAnchor="middle" fontSize={FONT} fill={TEXT}>
                    (missing)
                  </text>
                </g>
              )}
              <text x={LEFT + plotWidth / 2} y={HEIGHT - 5} textAnchor="middle" fontSize={FONT} fill={TEXT}>
                <title>{curve.feature}</title>
                {chartAxisLabel(curve.feature, plotWidth)}
              </text>
            </ChartSvg>
          )
        }}
      </ResponsiveChart>
    </>
  )
}

function CategoricalCurve({ curve, scale, activeIndex, onActivate }: CurveChartProps) {
  const maxRows = Math.max(...curve.points.map((point) => point.rows))
  return (
    <>
      <ChartLegend
        items={[
          { label: `Above ${scale.baseline}`, color: RELATIVITY_ABOVE_COLOR, swatch: "bar" },
          { label: `Below ${scale.baseline}`, color: RELATIVITY_BELOW_COLOR, swatch: "bar" },
          { label: "Rows", color: "var(--text-muted)", swatch: "bar", opacity: 0.5 },
        ]}
      />
      <RelativityBars
        ariaLabel={`SHAP curve for ${curve.feature}`}
        baseline={scale.baseline}
        // Relativities keep the default floor; additive SHAP values carry the
        // target's units, so any spread fills the track.
        minHalfScale={scale.relativity ? undefined : Number.MIN_VALUE}
        formatValue={formatChartNumber}
        bars={curve.points.map((point, index) => ({
          key: String(index),
          label: pointLabel(curve, point),
          value: scale.show(point.mean_shap),
          ciLower: scale.show(point.p10_shap),
          ciUpper: scale.show(point.p90_shap),
        }))}
        interaction={{
          activeKey: activeIndex === null ? null : String(activeIndex),
          onActivate: (key) => onActivate(Number(key)),
          describe: (bar) => describePoint(curve, curve.points[Number(bar.key)], scale),
        }}
        aside={{
          header: `Rows 0–${maxRows.toLocaleString()}`,
          width: ROWS_STRIP_WIDTH,
          render: (bar, isActive) => (
            <span className="block h-2 w-full">
              <span
                data-testid="shap-curve-rows-bar"
                className="block h-full rounded-sm"
                style={{
                  width: `${(curve.points[Number(bar.key)].rows / maxRows) * 100}%`,
                  background: "var(--text-muted)",
                  opacity: isActive ? 0.65 : 0.3,
                }}
              />
            </span>
          ),
        }}
      />
      {curve.levels_omitted > 0 && (
        <p className="text-[12px]" style={{ color: "var(--text-muted)" }}>
          {curve.levels_omitted.toLocaleString()} less frequent{" "}
          {curve.levels_omitted === 1 ? "level is" : "levels are"} not shown.
        </p>
      )}
    </>
  )
}
