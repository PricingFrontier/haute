/**
 * The Features tab's SHAP beeswarm: one row per feature, one dot per sampled
 * row placed by its SHAP value and coloured by the row's feature value. The
 * dots are painted on a canvas between the SVG that carries the axes, feature
 * rows and colour bar, and an SVG overlay that rings the pointed-at dot, so
 * thousands of dots stay responsive. Each feature row is a single tab stop.
 */
import { useLayoutEffect, useMemo, useRef, useState, type KeyboardEvent, type MouseEvent } from "react"
import type { TrainShapBeeswarmFeature } from "../../api/types"
import { formatChartNumber, formatChartTicks } from "../../utils/chartHelpers"
import { ChartLegend, ChartSvg, ChartValuesTable, ResponsiveChart, ValueColorBar } from "./ChartScaffold"
import { VALUE_COLOR_TOKENS, VALUE_NEUTRAL_COLOR, parseHexColor, valuePositionRgb } from "./beeswarm"
import {
  SHAP_BEESWARM_GEOMETRY as G,
  layoutShapBeeswarm,
  nearestDot,
  shapBeeswarmRowY,
  type ShapBeeswarmLayout,
} from "./shapBeeswarmLayout"

interface ShapBeeswarmProps {
  features: TrainShapBeeswarmFeature[]
  /** A fixed pixel width; the pane's measured width when omitted. */
  width?: number
}

type Active = { kind: "dot"; feature: number; row: number } | { kind: "feature"; feature: number } | null

const DOT_OPACITY = 0.6
/** The approximate advance of a 12px feature label character. */
const LABEL_CHAR_WIDTH = 7

// ── Theme tokens (the ratebook beeswarm's palette) ───────────────
const PLOT_BG = "var(--chart-impact-plot-bg)"
const LABEL_COLOR = "var(--chart-impact-label)"
const MUTED_COLOR = "var(--chart-impact-muted)"
const GRID_COLOR = "var(--chart-impact-grid)"
const AXIS_COLOR = "var(--chart-impact-axis)"
const VALUE_GRADIENT_ID = "shap-beeswarm-value-gradient"

const NO_ORDER_NOTE = "No value order (categorical level or missing value)"

/** The dot canvas and the pointed-dot ring sit over the chart and let the pointer through. */
const OVERLAY_STYLE = { position: "absolute", left: 0, top: 0, pointerEvents: "none" } as const

function clippedText(value: string, maxLength: number): string {
  return value.length <= maxLength ? value : `${value.slice(0, maxLength - 1)}…`
}

function median(values: readonly number[]): number {
  const sorted = [...values].sort((a, b) => a - b)
  const middle = Math.floor(sorted.length / 2)
  return sorted.length % 2 === 1 ? sorted[middle] : (sorted[middle - 1] + sorted[middle]) / 2
}

function shapRange(feature: TrainShapBeeswarmFeature): [number, number] {
  let low = Infinity
  let high = -Infinity
  for (const value of feature.shap_values) {
    low = Math.min(low, value)
    high = Math.max(high, value)
  }
  return [low, high]
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

/** Paint every dot, batched by colour, at the device's pixel ratio. */
function paintDots(
  canvas: HTMLCanvasElement,
  layout: ShapBeeswarmLayout,
  features: readonly TrainShapBeeswarmFeature[],
) {
  const ratio = window.devicePixelRatio || 1
  canvas.width = Math.round(layout.width * ratio)
  canvas.height = Math.round(layout.height * ratio)
  const context = canvas.getContext("2d")
  if (!context) throw new Error("A 2D canvas context is unavailable for the SHAP beeswarm.")

  const tokens = getComputedStyle(document.documentElement)
  const low = parseHexColor(tokens.getPropertyValue(VALUE_COLOR_TOKENS.low))
  const high = parseHexColor(tokens.getPropertyValue(VALUE_COLOR_TOKENS.high))
  const neutral = parseHexColor(tokens.getPropertyValue(VALUE_COLOR_TOKENS.neutral))
  // Colours 0..100 are the whole-percent rank mixes; 101 is the neutral colour.
  const colours = [
    ...Array.from({ length: 101 }, (_, percent) => valuePositionRgb(percent / 100, low, high)),
    `rgb(${neutral[0]}, ${neutral[1]}, ${neutral[2]})`,
  ]
  const batches: number[][] = colours.map(() => [])
  for (let index = 0; index < layout.x.length; index += 1) {
    const rank = features[layout.feature[index]].value_ranks[layout.row[index]]
    batches[rank === null ? 101 : Math.round(rank * 100)].push(index)
  }

  context.setTransform(ratio, 0, 0, ratio, 0, 0)
  context.clearRect(0, 0, layout.width, layout.height)
  context.globalAlpha = DOT_OPACITY
  batches.forEach((batch, colour) => {
    if (batch.length === 0) return
    context.fillStyle = colours[colour]
    context.beginPath()
    for (const index of batch) {
      context.moveTo(layout.x[index] + G.dotRadius, layout.y[index])
      context.arc(layout.x[index], layout.y[index], G.dotRadius, 0, Math.PI * 2)
    }
    context.fill()
  })
}

export default function ShapBeeswarm({ features, width }: ShapBeeswarmProps) {
  const [active, setActive] = useState<Active>(null)
  if (features.length === 0) return null

  const hasUnordered = features.some((feature) => feature.value_ranks.some((rank) => rank === null))
  const detailFeature = active ? features[active.feature] : null

  return (
    <section className="space-y-2" aria-label="SHAP beeswarm">
      <ResponsiveChart width={width}>
        {(chartWidth) => (
          <BeeswarmChart features={features} width={chartWidth} active={active} onActivate={setActive} />
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

function BeeswarmChart({
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
  const layout = useMemo(() => layoutShapBeeswarm(features, width), [features, width])
  const canvas = useRef<HTMLCanvasElement>(null)
  useLayoutEffect(() => paintDots(canvas.current!, layout, features), [layout, features])

  const { height, marginLeft, plotRight, ticks, xScale } = layout
  const labelChars = Math.max(4, Math.floor((marginLeft - G.labelGap) / LABEL_CHAR_WIDTH))
  const tickLabels = formatChartTicks(ticks)
  const zeroX = xScale(0)
  const pointedIndex =
    active?.kind === "dot"
      ? layout.feature.findIndex(
          (feature, index) => feature === active.feature && layout.row[index] === active.row,
        )
      : -1

  const pointAt = (event: MouseEvent<HTMLDivElement>) => {
    const box = event.currentTarget.getBoundingClientRect()
    const index = nearestDot(layout, event.clientX - box.left, event.clientY - box.top)
    if (index === null || index === pointedIndex) return
    onActivate({ kind: "dot", feature: layout.feature[index], row: layout.row[index] })
  }

  return (
    <div data-testid="shap-beeswarm" style={{ position: "relative", width, height }} onMouseMove={pointAt}>
      <ChartSvg
        width={width}
        height={height}
        ariaLabel={`SHAP beeswarm of ${features.length} features`}
        style={{ background: PLOT_BG, borderRadius: 4, border: "1px solid var(--border)" }}
      >
        {ticks.map((tick, index) => (
          <g key={tick}>
            <line
              x1={xScale(tick)}
              y1={G.marginTop}
              x2={xScale(tick)}
              y2={height - G.marginBottom}
              stroke={GRID_COLOR}
              strokeDasharray="1,5"
            />
            <text
              x={xScale(tick)}
              y={height - G.marginBottom + 16}
              textAnchor="middle"
              fontSize={11}
              fill={MUTED_COLOR}
            >
              {tickLabels[index]}
            </text>
          </g>
        ))}
        <line
          x1={zeroX}
          y1={G.marginTop - 4}
          x2={zeroX}
          y2={height - G.marginBottom + 4}
          stroke={AXIS_COLOR}
          strokeWidth={1.5}
        />

        {features.map((feature, index) => {
          const isActive = active?.kind === "feature" && active.feature === index
          const activate = () => onActivate({ kind: "feature", feature: index })
          const lineY = shapBeeswarmRowY(index)
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
              onKeyDown={(event: KeyboardEvent<SVGGElement>) => {
                if (event.key === "Enter" || event.key === " ") {
                  event.preventDefault()
                  activate()
                }
              }}
            >
              <rect
                x={0}
                y={lineY - G.rowGap / 2}
                width={plotRight}
                height={G.rowGap}
                fill={isActive ? GRID_COLOR : "transparent"}
                opacity={isActive ? 0.45 : 1}
              />
              <line x1={marginLeft} y1={lineY} x2={plotRight} y2={lineY} stroke={GRID_COLOR} />
              <text
                x={marginLeft - G.labelGap}
                y={lineY + 4}
                textAnchor="end"
                fontSize={12}
                fontWeight={600}
                fill={LABEL_COLOR}
                style={{ cursor: "pointer" }}
                onClick={activate}
              >
                <title>{feature.feature}</title>
                {clippedText(feature.feature, labelChars)}
              </text>
            </g>
          )
        })}

        <text
          x={(marginLeft + plotRight) / 2}
          y={height - 8}
          textAnchor="middle"
          fontSize={12}
          fill={MUTED_COLOR}
        >
          SHAP value (link scale)
        </text>

        <ValueColorBar
          gradientId={VALUE_GRADIENT_ID}
          x={width - G.colorBarInset}
          top={G.marginTop}
          bottom={height - G.marginBottom}
          title="Feature value"
          captionColor={MUTED_COLOR}
          testId="shap-beeswarm-colour-bar"
        />
      </ChartSvg>

      <canvas
        ref={canvas}
        data-testid="shap-beeswarm-dots"
        aria-hidden="true"
        style={{ ...OVERLAY_STYLE, width, height }}
      />

      {pointedIndex >= 0 && (
        <svg
          aria-hidden="true"
          style={OVERLAY_STYLE}
          width={width}
          height={height}
        >
          <circle
            data-testid="shap-beeswarm-pointed"
            cx={layout.x[pointedIndex]}
            cy={layout.y[pointedIndex]}
            r={G.dotRadius + 2.5}
            fill="none"
            stroke={LABEL_COLOR}
            strokeWidth={1.5}
          />
        </svg>
      )}
    </div>
  )
}
