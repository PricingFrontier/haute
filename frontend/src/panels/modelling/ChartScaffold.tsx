import {
  useLayoutEffect,
  useRef,
  useState,
  type CSSProperties,
  type ReactNode,
  type SVGProps,
} from "react"
import { formatChartNumber } from "../../utils/chartHelpers"

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

export type ChartLegendItem = {
  label: ReactNode
  color: string
  swatch?: "line" | "bar" | "dashed"
  dashed?: boolean
  opacity?: number
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
            {item.label}
          </span>
        )
      })}
    </div>
  )
}

/**
 * Horizontal gridlines across the plot with each tick's value to the left of
 * the axis: the value axis every validation chart draws.
 */
export function ChartValueGrid({
  ticks,
  left,
  right,
  y,
  labelGap = 6,
}: {
  ticks: number[]
  /** The plot's left edge; labels end `labelGap` pixels before it. */
  left: number
  /** The plot's right edge. */
  right: number
  y: (value: number) => number
  labelGap?: number
}) {
  return (
    <>
      {ticks.map((value) => (
        <g key={value} data-testid="chart-value-tick">
          <line x1={left} y1={y(value)} x2={right} y2={y(value)} stroke={MODELLING_CHART_GRID_COLOR} />
          <text
            x={left - labelGap}
            y={y(value) + 4}
            textAnchor="end"
            fontSize={MODELLING_CHART_AXIS_FONT_SIZE}
            fill={MODELLING_CHART_AXIS_TEXT_COLOR}
          >
            {formatChartNumber(value)}
          </text>
        </g>
      ))}
    </>
  )
}
