import { BarChart3 } from "lucide-react"

import { NODE_GROUP_COLORS } from "../../theme/colors"
import type { SimpleNode } from "../editors"
import { exploreChartLabel, parseExploreCharts } from "./chartConfig"

type ExploreChartsPaneProps = {
  node: SimpleNode
}

function EmptyCharts({ children }: { children: string }) {
  return (
    <div className="flex flex-1 items-center justify-center p-4">
      <div className="max-w-md text-center">
        <BarChart3
          size={24}
          className="mx-auto mb-2"
          aria-hidden="true"
          style={{ color: NODE_GROUP_COLORS.explore }}
        />
        <div className="text-xs font-semibold" style={{ color: "var(--text-secondary)" }}>
          {children}
        </div>
      </div>
    </div>
  )
}

export default function ExploreChartsPane({ node }: ExploreChartsPaneProps) {
  const parsed = parseExploreCharts(node.data.config ?? {})
  if (!parsed.ok) {
    return (
      <div data-testid="explore-charts-pane" className="flex-1 p-4">
        <div
          role="alert"
          className="rounded-lg px-3 py-2 text-xs leading-relaxed"
          style={{
            color: "var(--danger)",
            background: "var(--danger-soft)",
            border: "1px solid var(--danger-border)",
          }}
        >
          {parsed.error}
        </div>
      </div>
    )
  }

  if (parsed.charts.length === 0) {
    return (
      <div data-testid="explore-charts-pane" className="flex flex-1">
        <EmptyCharts>Add a chart from the Charts settings pane.</EmptyCharts>
      </div>
    )
  }

  const visibleCharts = parsed.charts
    .map((chart, index) => ({ chart, index }))
    .filter(({ chart }) => chart.enabled)

  if (visibleCharts.length === 0) {
    return (
      <div data-testid="explore-charts-pane" className="flex flex-1">
        <EmptyCharts>No charts are currently shown.</EmptyCharts>
      </div>
    )
  }

  return (
    <div data-testid="explore-charts-pane" className="flex-1 overflow-auto p-3">
      <div className="grid gap-3 md:grid-cols-2">
        {visibleCharts.map(({ chart, index }) => {
          const label = exploreChartLabel(index)
          return (
            <section
              key={chart.id}
              role="region"
              aria-label={label}
              data-testid="explore-chart-visualisation"
              className="flex min-h-44 flex-col overflow-hidden rounded-lg"
              style={{ background: "var(--bg-input)", border: "1px solid var(--border)" }}
            >
              <div
                className="flex items-center gap-2 px-3 py-2"
                style={{ borderBottom: "1px solid var(--border)" }}
              >
                <BarChart3 size={14} aria-hidden="true" style={{ color: NODE_GROUP_COLORS.explore }} />
                <h3 className="text-xs font-semibold" style={{ color: "var(--text-primary)" }}>
                  {label}
                </h3>
              </div>
              <div className="flex flex-1 flex-col items-center justify-center gap-3 px-4 py-5">
                <div className="flex h-16 items-end gap-1.5" aria-hidden="true">
                  {[35, 62, 46, 78, 55, 88].map((height, barIndex) => (
                    <span
                      key={barIndex}
                      className="w-3 rounded-t-sm"
                      style={{ height: `${height}%`, background: NODE_GROUP_COLORS.explore, opacity: 0.28 + barIndex * 0.08 }}
                    />
                  ))}
                </div>
                <p className="text-center text-[11px]" style={{ color: "var(--text-muted)" }}>
                  Configure this chart to choose its data and appearance.
                </p>
              </div>
            </section>
          )
        })}
      </div>
    </div>
  )
}
