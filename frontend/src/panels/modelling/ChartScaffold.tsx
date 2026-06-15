import type { CSSProperties, ReactNode, SVGProps } from "react"

export const MODELLING_CHART_GRID_COLOR = "rgba(255,255,255,.06)"
export const MODELLING_CHART_AXIS_TEXT_COLOR = "var(--text-muted)"
export const MODELLING_CHART_AXIS_FONT_SIZE = 10

const CHART_SURFACE_STYLE = {
  background: "var(--bg-input)",
  borderRadius: 6,
  border: "1px solid var(--border)",
} satisfies CSSProperties

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
  const className = compact ? "flex gap-3 mt-1 text-[10px]" : "flex gap-4 mt-1.5 text-[11px]"
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
                background: item.color,
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
