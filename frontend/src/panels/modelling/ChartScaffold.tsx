import {
  useLayoutEffect,
  useRef,
  useState,
  type CSSProperties,
  type ReactNode,
  type SVGProps,
} from "react"
import { formatChartNumber, formatChartTicks } from "../../utils/chartHelpers"

export const MODELLING_CHART_GRID_COLOR = "var(--border)"
export const MODELLING_CHART_AXIS_TEXT_COLOR = "var(--text-muted)"
export const MODELLING_CHART_AXIS_FONT_SIZE = 12

const CHART_SURFACE_STYLE = {
  display: "block",
  overflow: "visible",
} satisfies CSSProperties

/** Pixel geometry follows the container; labels never shrink with a viewBox. */
export function ResponsiveChart({
  children,
  width,
  className,
}: {
  children: (width: number) => ReactNode
  width?: number
  className?: string
}) {
  const container = useRef<HTMLDivElement>(null)
  // Initial geometry is replaced during layout, before browser paint. Zero-sized
  // hidden panes retain that geometry until their first positive measurement.
  const [measuredWidth, setMeasuredWidth] = useState(640)
  useLayoutEffect(() => {
    if (width !== undefined) return
    const element = container.current!
    const measure = (nextWidth: number) => {
      if (nextWidth > 0) setMeasuredWidth(Math.floor(nextWidth))
    }
    measure(element.getBoundingClientRect().width)
    // jsdom and static renderers have no layout observer.
    if (typeof ResizeObserver === "undefined") return
    const observer = new ResizeObserver(([entry]) => measure(entry.contentRect.width))
    observer.observe(element)
    return () => observer.disconnect()
  }, [width])
  return (
    <div ref={container} className={`min-w-0 w-full ${className ?? ""}`}>
      {children(width ?? measuredWidth)}
    </div>
  )
}

/** The gap between two charts laid out side by side (Tailwind `gap-6`). */
const TWO_CHART_GAP = 24

/**
 * The result tabs' two-chart layout. When both charts exist and the pane is
 * at least `sideBySideFrom` pixels wide they sit side by side, each half the
 * width less the gap and never narrower than `minChartWidth`; otherwise they
 * take the full width, one under the other. `header` renders above the charts
 * (Lift puts its view switch there when the charts do not fit side by side).
 */
export function TwoChartLayout({
  width,
  ariaLabel,
  bothCharts,
  sideBySideFrom,
  minChartWidth,
  header,
  children,
}: {
  width?: number
  ariaLabel: string
  bothCharts: boolean
  sideBySideFrom: number
  minChartWidth: number
  header?: (sideBySide: boolean) => ReactNode
  children: (layout: { sideBySide: boolean; chartWidth: number }) => ReactNode
}) {
  return (
    <ResponsiveChart width={width}>
      {(containerWidth) => {
        const sideBySide = bothCharts && containerWidth >= sideBySideFrom
        const chartWidth = sideBySide
          ? Math.max(minChartWidth, (containerWidth - TWO_CHART_GAP) / 2)
          : containerWidth
        return (
          <section className="space-y-3" aria-label={ariaLabel}>
            {header?.(sideBySide)}
            <div className={sideBySide ? "grid grid-cols-2 gap-6" : "space-y-6"}>
              {children({ sideBySide, chartWidth })}
            </div>
          </section>
        )
      }}
    </ResponsiveChart>
  )
}

type ChartSvgProps = Omit<SVGProps<SVGSVGElement>, "children" | "height" | "width"> & {
  width: number
  height: number
  children: ReactNode
  ariaLabel?: string
}

export function ChartSvg({
  width,
  height,
  children,
  className,
  ariaLabel,
  role,
  style,
  ...svgProps
}: ChartSvgProps) {
  const resolvedAriaLabel = ariaLabel ?? svgProps["aria-label"]

  return (
    <svg
      {...svgProps}
      aria-label={resolvedAriaLabel}
      className={className}
      height={height}
      role={role ?? (resolvedAriaLabel ? "img" : undefined)}
      style={{ ...CHART_SURFACE_STYLE, ...style }}
      width={width}
    >
      {children}
    </svg>
  )
}

/**
 * A chart's raw values behind a native disclosure, closed by default. The
 * first cell of each row is its header; the other columns are right-aligned
 * values (`validation-value-table` in validation.css).
 */
export function ChartValuesTable({
  summary,
  ariaLabel,
  headers,
  rows,
}: {
  summary: string
  ariaLabel: string
  headers: ReactNode[]
  rows: ReactNode[][]
}) {
  return (
    <details className="validation-values">
      <summary>{summary}</summary>
      <div className="overflow-x-auto">
        <table className="validation-value-table" aria-label={ariaLabel}>
          <thead>
            <tr>
              {headers.map((header, index) => (
                <th key={index} scope="col">
                  {header}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map(([rowHeader, ...values], rowIndex) => (
              <tr key={rowIndex}>
                <th scope="row">{rowHeader}</th>
                {values.map((value, index) => (
                  <td key={index}>{value}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </details>
  )
}

export function ChartEmptyState({ children }: { children: ReactNode }) {
  return (
    <div
      className="flex h-full items-center justify-center text-xs"
      style={{ color: MODELLING_CHART_AXIS_TEXT_COLOR }}
    >
      {children}
    </div>
  )
}

/** A point marker as a chart draws it: filled, a ring, hollow or a cross. */
export type ChartLegendMarker = "dot" | "ring" | "hollow" | "cross"

export type ChartLegendItem = {
  label: ReactNode
  color: string
  swatch?: "line" | "bar" | "dashed"
  /** Draws the swatch as this point marker instead of a line or bar. */
  marker?: ChartLegendMarker
  dashed?: boolean
  opacity?: number
}

function LegendMarker({ marker, color, opacity }: { marker: ChartLegendMarker; color: string; opacity?: number }) {
  return (
    <svg
      width={10}
      height={10}
      viewBox="0 0 10 10"
      aria-hidden="true"
      data-testid="chart-legend-swatch"
      data-marker={marker}
      style={{ opacity, flexShrink: 0 }}
    >
      {marker === "cross" ? (
        <path d="M2 2 L8 8 M8 2 L2 8" stroke={color} strokeWidth={2} strokeLinecap="round" />
      ) : (
        <circle
          cx={5}
          cy={5}
          r={marker === "dot" ? 4 : 3.5}
          fill={marker === "dot" ? color : "none"}
          stroke={marker === "dot" ? "none" : color}
          strokeWidth={marker === "ring" ? 2 : 1.5}
        />
      )}
    </svg>
  )
}

type ChartLegendProps = {
  items: ChartLegendItem[]
  compact?: boolean
}

export function ChartLegend({ items, compact = false }: ChartLegendProps) {
  const className = compact
    ? "flex flex-wrap gap-x-3 gap-y-1 mt-1 text-xs"
    : "flex flex-wrap gap-x-4 gap-y-1 mt-1.5 text-xs"
  return (
    <div className={className} style={{ color: MODELLING_CHART_AXIS_TEXT_COLOR }}>
      {items.map((item, index) => {
        const isDashed = item.swatch === "dashed" || item.dashed
        return (
          <span key={index} className="flex items-center gap-1.5">
            {item.marker ? (
              <LegendMarker marker={item.marker} color={item.color} opacity={item.opacity} />
            ) : (
              <span
                className={
                  item.swatch === "bar"
                    ? "inline-block h-2 w-3 rounded-sm"
                    : "inline-block h-0.5 w-3 rounded"
                }
                data-testid="chart-legend-swatch"
                style={{
                  background: isDashed ? undefined : item.color,
                  borderTop: isDashed ? `1px dashed ${item.color}` : undefined,
                  opacity: item.opacity,
                }}
              />
            )}
            {item.label}
          </span>
        )
      })}
    </div>
  )
}

/**
 * Horizontal gridlines across the plot with each tick's value to the left of
 * the axis: the value axis every validation chart draws. A linear axis's
 * labels are formatted together, so neighbouring ticks never share a label; a
 * log axis labels each decade on its own.
 */
export function ChartValueGrid({
  ticks,
  left,
  right,
  y,
  labelGap = 6,
  scale = "linear",
}: {
  ticks: number[]
  /** The plot's left edge; labels end `labelGap` pixels before it. */
  left: number
  /** The plot's right edge. */
  right: number
  y: (value: number) => number
  labelGap?: number
  scale?: "linear" | "log"
}) {
  const labels = scale === "log" ? ticks.map(formatChartNumber) : formatChartTicks(ticks)
  return (
    <>
      {ticks.map((value, index) => (
        <g key={value} data-testid="chart-value-tick">
          <line x1={left} y1={y(value)} x2={right} y2={y(value)} stroke={MODELLING_CHART_GRID_COLOR} />
          <text
            x={left - labelGap}
            y={y(value) + 4}
            textAnchor="end"
            fontSize={MODELLING_CHART_AXIS_FONT_SIZE}
            fill={MODELLING_CHART_AXIS_TEXT_COLOR}
          >
            {labels[index]}
          </text>
        </g>
      ))}
    </>
  )
}
