/**
 * The Features tab's SHAP beeswarm: one row per feature, one dot per sampled
 * row placed by its SHAP value and coloured by the row's feature value. The
 * dots are laid out once per width and drawn in a memoised layer with one
 * delegated pointer handler, so pointing at a dot re-renders only the overlay
 * and the detail line; each feature row is a single tab stop.
 */
import { memo, useCallback, useMemo, useState, type KeyboardEvent, type MouseEvent } from "react"
import type { TrainShapBeeswarmFeature } from "../../api/types"
import { chartDomain, chartTicks, chartTickSpan, formatChartNumber, formatChartTicks } from "../../utils/chartHelpers"
import {
  ChartLegend,
  ChartSvg,
  ChartValuesTable,
  ResponsiveChart,
  ValueColorBar,
} from "./ChartScaffold"
import { VALUE_NEUTRAL_COLOR, beeswarmOffsets, valuePositionColor } from "./beeswarm"

interface ShapBeeswarmProps {
  features: TrainShapBeeswarmFeature[]
  /** A fixed pixel width; the pane's measured width when omitted. */
  width?: number
}

type Active = { kind: "dot"; feature: number; row: number } | { kind: "feature"; feature: number } | null

type PlacedDot = { feature: number; row: number; x: number; y: number; fill: string }

// ── Layout (pixels) ──────────────────────────────────────────────
const MARGIN_TOP = 16
const MARGIN_BOTTOM = 46
const ROW_GAP = 30
/** How far a dot may sit above or below its row line. */
const ROW_HALF_HEIGHT = 12
const LABEL_GAP = 12
/** The feature labels take this share of the width, within these bounds. */
const LABEL_AREA_SHARE = 0.22
const MIN_LABEL_AREA = 88
const MAX_LABEL_AREA = 160
const MARGIN_RIGHT = 64
const COLOR_BAR_INSET = 36
/** The approximate advance of a 12px feature label character. */
const LABEL_CHAR_WIDTH = 7

// ── Dots ─────────────────────────────────────────────────────────
const DOT_RADIUS = 2.4
const DOT_OPACITY = 0.7
/** Dots within one bucket of the SHAP axis stack into lanes, scaled to fit the row. */
const DOT_STACKING = { bucketWidth: DOT_RADIUS * 2, laneStep: DOT_RADIUS * 1.5, halfHeight: ROW_HALF_HEIGHT }

// ── Theme tokens (the ratebook beeswarm's palette) ───────────────
const PLOT_BG = "var(--chart-impact-plot-bg)"
const LABEL_COLOR = "var(--chart-impact-label)"
const MUTED_COLOR = "var(--chart-impact-muted)"
const GRID_COLOR = "var(--chart-impact-grid)"
const AXIS_COLOR = "var(--chart-impact-axis)"
const VALUE_GRADIENT_ID = "shap-beeswarm-value-gradient"

const NO_ORDER_NOTE = "No value order (categorical level or missing value)"

/** The y of feature row `index`'s line. */
function rowY(index: number): number {
  return MARGIN_TOP + ROW_GAP / 2 + index * ROW_GAP
}

function clippedText(value: string, maxLength: number): string {
  return value.length <= maxLength ? value : `${value.slice(0, maxLength - 1)}…`
}

function median(values: readonly number[]): number {
  const sorted = [...values].sort((a, b) => a - b)
  const middle = Math.floor(sorted.length / 2)
  return sorted.length % 2 === 1 ? sorted[middle] : (sorted[middle - 1] + sorted[middle]) / 2
}

function shapRange(feature: TrainShapBeeswarmFeature): [number, number] {
  return [Math.min(...feature.shap_values), Math.max(...feature.shap_values)]
}

function displayValue(value: number | string | null): string {
  if (value === null) return "(missing)"
  return typeof value === "number" ? formatChartNumber(value) : value
}

/** What a dot's colour says, in words. */
function colourMeaning(feature: TrainShapBeeswarmFeature, row: number): string {
  if (feature.kind === "categorical") return "no value order (categorical level)"
  const rank = feature.value_ranks[row]
  if (feature.values[row] === null) return "no value order (missing value)"
  if (rank === null) return "no value order (one value only)"
  return `value rank ${rank.toFixed(2)} of low 0 to high 1`
}

function describeFeature(feature: TrainShapBeeswarmFeature): string {
  const [low, high] = shapRange(feature)
  return `${feature.feature}: ${feature.shap_values.length.toLocaleString()} rows, SHAP from ${formatChartNumber(low)} to ${formatChartNumber(high)}`
}

export default function ShapBeeswarm({ features, width }: ShapBeeswarmProps) {
  const [active, setActive] = useState<Active>(null)
  const hasUnordered = features.some((feature) => feature.value_ranks.some((rank) => rank === null))
  if (features.length === 0) return null

  const detailFeature = active ? features[active.feature] : null

  return (
    <section className="space-y-2" aria-label="SHAP beeswarm">
      <ResponsiveChart width={width}>
        {(chartWidth) => (
          <BeeswarmSvg features={features} width={chartWidth} active={active} onActivate={setActive} />
        )}
      </ResponsiveChart>

      {hasUnordered && (
        <ChartLegend compact items={[{ label: NO_ORDER_NOTE, color: VALUE_NEUTRAL_COLOR, swatch: "bar" }]} />
      )}

      <div className="validation-bin-detail" role="status" aria-live="polite">
        {active && detailFeature ? (
          active.kind === "dot" ? (
            <>
              <strong>{detailFeature.feature}</strong>
              <span>Value: {displayValue(detailFeature.values[active.row])}</span>
              <span>SHAP: {formatChartNumber(detailFeature.shap_values[active.row])}</span>
              <span>Colour: {colourMeaning(detailFeature, active.row)}</span>
            </>
          ) : (
            <>
              <strong>{detailFeature.feature}</strong>
              <span>Rows: {detailFeature.shap_values.length.toLocaleString()}</span>
              <span>
                SHAP: {formatChartNumber(shapRange(detailFeature)[0])} to{" "}
                {formatChartNumber(shapRange(detailFeature)[1])}
              </span>
            </>
          )
        ) : (
          <span>Point at a dot to inspect its row, or focus a feature for its range.</span>
        )}
      </div>

      <ChartValuesTable
        summary="View SHAP values"
        ariaLabel="SHAP beeswarm values"
        headers={["Feature", "Rows", "Min SHAP", "Median SHAP", "Max SHAP"]}
        rows={features.map((feature) => {
          const [low, high] = shapRange(feature)
          return [
            feature.feature,
            feature.shap_values.length.toLocaleString(),
            formatChartNumber(low),
            formatChartNumber(median(feature.shap_values)),
            formatChartNumber(high),
          ]
        })}
      />
    </section>
  )
}

function BeeswarmSvg({
  features,
  width,
  active,
  onActivate,
}: {
  features: TrainShapBeeswarmFeature[]
  width: number
  active: Active
  onActivate: (active: Active) => void
}) {
  const marginLeft = Math.min(MAX_LABEL_AREA, Math.max(MIN_LABEL_AREA, width * LABEL_AREA_SHARE))
  const plotRight = Math.max(marginLeft + 1, width - MARGIN_RIGHT)
  const chartW = plotRight - marginLeft
  const height = MARGIN_TOP + features.length * ROW_GAP + MARGIN_BOTTOM
  const labelChars = Math.max(4, Math.floor((marginLeft - LABEL_GAP) / LABEL_CHAR_WIDTH))

  const { xScale, ticks, dots } = useMemo(() => {
    const allShap = features.flatMap((feature) => feature.shap_values)
    const [low, high] = chartDomain(allShap, true)
    const scale = (value: number) => marginLeft + ((value - low) / (high - low)) * chartW
    const [tickLow, tickHigh] = chartTickSpan([...allShap, 0])
    const placed: PlacedDot[] = features.flatMap((feature, featureIndex) => {
      const xs = feature.shap_values.map(scale)
      const offsets = beeswarmOffsets(xs, DOT_STACKING)
      return xs.map((x, row) => ({
        feature: featureIndex,
        row,
        x,
        y: rowY(featureIndex) + offsets[row],
        fill: valuePositionColor(feature.value_ranks[row]),
      }))
    })
    return { xScale: scale, ticks: chartTicks(tickLow, tickHigh, 5), dots: placed }
  }, [features, marginLeft, chartW])

  const pointAt = useCallback(
    (event: MouseEvent<SVGGElement>) => {
      const target = event.target as SVGElement
      const feature = target.dataset.feature
      const row = target.dataset.row
      if (feature === undefined || row === undefined) return
      onActivate({ kind: "dot", feature: Number(feature), row: Number(row) })
    },
    [onActivate],
  )

  const tickLabels = formatChartTicks(ticks)
  const zeroX = xScale(0)
  const pointed = active?.kind === "dot" ? dots.find((dot) => dot.feature === active.feature && dot.row === active.row) : undefined

  return (
    <ChartSvg
      data-testid="shap-beeswarm"
      width={width}
      height={height}
      style={{ background: PLOT_BG, borderRadius: 4, border: "1px solid var(--border)" }}
    >
      {ticks.map((tick, index) => (
        <g key={tick}>
          <line
            x1={xScale(tick)}
            y1={MARGIN_TOP}
            x2={xScale(tick)}
            y2={height - MARGIN_BOTTOM}
            stroke={GRID_COLOR}
            strokeDasharray="1,5"
          />
          <text x={xScale(tick)} y={height - MARGIN_BOTTOM + 16} textAnchor="middle" fontSize={11} fill={MUTED_COLOR}>
            {tickLabels[index]}
          </text>
        </g>
      ))}
      <line
        x1={zeroX}
        y1={MARGIN_TOP - 4}
        x2={zeroX}
        y2={height - MARGIN_BOTTOM + 4}
        stroke={AXIS_COLOR}
        strokeWidth={1.5}
      />

      {features.map((feature, index) => {
        const isActive = active?.feature === index && active.kind === "feature"
        const activate = () => onActivate({ kind: "feature", feature: index })
        return (
          <g
            key={feature.feature}
            data-testid="shap-beeswarm-feature"
            role="button"
            tabIndex={0}
            aria-label={describeFeature(feature)}
            aria-pressed={isActive}
            className="focus-ring"
            onFocus={activate}
            onClick={activate}
            onKeyDown={(event: KeyboardEvent<SVGGElement>) => {
              if (event.key === "Enter" || event.key === " ") {
                event.preventDefault()
                activate()
              }
            }}
          >
            <rect
              x={0}
              y={rowY(index) - ROW_GAP / 2}
              width={plotRight}
              height={ROW_GAP}
              fill={isActive ? GRID_COLOR : "transparent"}
              opacity={isActive ? 0.45 : 1}
            />
            <line x1={marginLeft} y1={rowY(index)} x2={plotRight} y2={rowY(index)} stroke={GRID_COLOR} />
            <text
              x={marginLeft - LABEL_GAP}
              y={rowY(index) + 4}
              textAnchor="end"
              fontSize={12}
              fontWeight={600}
              fill={LABEL_COLOR}
            >
              <title>{feature.feature}</title>
              {clippedText(feature.feature, labelChars)}
            </text>
          </g>
        )
      })}

      <BeeswarmDots dots={dots} onPoint={pointAt} />

      {pointed && (
        <circle
          data-testid="shap-beeswarm-pointed"
          cx={pointed.x}
          cy={pointed.y}
          r={DOT_RADIUS + 1.8}
          fill={pointed.fill}
          stroke={LABEL_COLOR}
          strokeWidth={1.5}
          pointerEvents="none"
        />
      )}

      <text x={marginLeft + chartW / 2} y={height - 8} textAnchor="middle" fontSize={12} fill={MUTED_COLOR}>
        SHAP value (link scale)
      </text>

      <ValueColorBar
        gradientId={VALUE_GRADIENT_ID}
        x={width - COLOR_BAR_INSET}
        top={MARGIN_TOP}
        bottom={height - MARGIN_BOTTOM}
        title="Feature value"
        captionColor={MUTED_COLOR}
        testId="shap-beeswarm-colour-bar"
      />
    </ChartSvg>
  )
}

/** Every dot, drawn once per layout; pointing is read from the dot's data attributes. */
const BeeswarmDots = memo(function BeeswarmDots({
  dots,
  onPoint,
}: {
  dots: PlacedDot[]
  onPoint: (event: MouseEvent<SVGGElement>) => void
}) {
  return (
    <g data-testid="shap-beeswarm-dots" onMouseOver={onPoint}>
      {dots.map((dot) => (
        <circle
          key={`${dot.feature}:${dot.row}`}
          data-feature={dot.feature}
          data-row={dot.row}
          cx={dot.x}
          cy={dot.y}
          r={DOT_RADIUS}
          fill={dot.fill}
          opacity={DOT_OPACITY}
        />
      ))}
    </g>
  )
})
