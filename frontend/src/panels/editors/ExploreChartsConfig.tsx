import { useState } from "react"
import { ArrowLeft, BarChart3, Eye, EyeOff, Plus, SlidersHorizontal } from "lucide-react"

import { NODE_GROUP_COLORS } from "../../theme/colors"
import { withAlpha } from "../../utils/color"
import {
  exploreChartLabel,
  nextExploreChartId,
  parseExploreCharts,
} from "../explore/chartConfig"
import type { OnUpdateConfig } from "./_shared"

type ExploreChartsConfigProps = {
  config: Record<string, unknown>
  onUpdate: OnUpdateConfig
}

export default function ExploreChartsConfig({ config, onUpdate }: ExploreChartsConfigProps) {
  const [configuredChartId, setConfiguredChartId] = useState<string | null>(null)
  const parsed = parseExploreCharts(config)

  if (!parsed.ok) {
    return (
      <div data-testid="explore-charts-config" className="px-4 py-3">
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

  const { charts } = parsed
  const configuredIndex = configuredChartId
    ? charts.findIndex((chart) => chart.id === configuredChartId)
    : -1

  if (configuredIndex >= 0) {
    const label = exploreChartLabel(configuredIndex)
    return (
      <div data-testid="explore-charts-config" className="px-4 py-3 flex flex-col gap-4">
        <button
          type="button"
          onClick={() => setConfiguredChartId(null)}
          className="focus-ring self-start inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-xs font-semibold hover:bg-[var(--bg-hover)]"
          style={{
            color: "var(--text-secondary)",
            ["--focus-ring-border" as string]: withAlpha(NODE_GROUP_COLORS.explore, 0.3),
            ["--focus-ring-shadow" as string]: withAlpha(NODE_GROUP_COLORS.explore, 0.1),
          }}
        >
          <ArrowLeft size={13} aria-hidden="true" />
          Back to charts
        </button>

        <div>
          <h3 className="text-sm font-semibold" style={{ color: "var(--text-primary)" }}>
            Configure {label}
          </h3>
          <p className="mt-1 text-[11px] leading-relaxed" style={{ color: "var(--text-muted)" }}>
            Chart settings will be added here.
          </p>
        </div>

        <div
          className="flex min-h-28 flex-col items-center justify-center gap-2 rounded-lg px-4 py-6 text-center"
          style={{ background: "var(--bg-input)", border: "1px dashed var(--border)" }}
        >
          <SlidersHorizontal size={20} aria-hidden="true" style={{ color: NODE_GROUP_COLORS.explore }} />
          <span className="text-[11px]" style={{ color: "var(--text-muted)" }}>
            Axes, series, and styling controls are coming next.
          </span>
        </div>
      </div>
    )
  }

  const addChart = () => {
    onUpdate("charts", [
      ...charts,
      { id: nextExploreChartId(charts), enabled: true },
    ])
  }

  const toggleChart = (chartId: string) => {
    onUpdate(
      "charts",
      charts.map((chart) =>
        chart.id === chartId ? { ...chart, enabled: !chart.enabled } : chart,
      ),
    )
  }

  return (
    <div data-testid="explore-charts-config" className="px-4 py-3 flex flex-col gap-3">
      <div className="flex items-center justify-between gap-3">
        <div>
          <div
            className="text-[11px] font-bold uppercase tracking-[0.08em]"
            style={{ color: "var(--text-secondary)" }}
          >
            Charts
          </div>
          <div className="mt-0.5 text-[10px]" style={{ color: "var(--text-muted)" }}>
            Toggle charts shown in the visualisation area.
          </div>
        </div>
        <button
          type="button"
          onClick={addChart}
          className="focus-ring inline-flex shrink-0 items-center gap-1.5 rounded-md px-2.5 py-1.5 text-xs font-semibold hover:brightness-105"
          style={{
            color: "var(--text-on-accent)",
            background: NODE_GROUP_COLORS.explore,
            ["--focus-ring-border" as string]: withAlpha(NODE_GROUP_COLORS.explore, 0.35),
            ["--focus-ring-shadow" as string]: withAlpha(NODE_GROUP_COLORS.explore, 0.15),
          }}
        >
          <Plus size={13} aria-hidden="true" />
          Add Chart
        </button>
      </div>

      {charts.length === 0 ? (
        <div
          className="rounded-lg px-3 py-5 text-center text-xs"
          style={{ color: "var(--text-muted)", background: "var(--bg-input)", border: "1px dashed var(--border)" }}
        >
          No charts yet. Add one to start building the visualisation area.
        </div>
      ) : (
        <div className="flex flex-col gap-2">
          {charts.map((chart, index) => {
            const label = exploreChartLabel(index)
            return (
              <div
                key={chart.id}
                className="flex overflow-hidden rounded-lg transition-colors"
                style={{
                  background: chart.enabled ? "var(--accent-soft)" : "var(--bg-input)",
                  border: `1px solid ${chart.enabled ? NODE_GROUP_COLORS.explore : "var(--border)"}`,
                }}
              >
                <button
                  type="button"
                  role="checkbox"
                  aria-checked={chart.enabled}
                  aria-label={`Show ${label}`}
                  onClick={() => toggleChart(chart.id)}
                  className="focus-ring flex min-w-0 flex-1 items-center gap-2.5 px-3 py-2 text-left hover:brightness-105"
                  style={{
                    ["--focus-ring-border" as string]: withAlpha(NODE_GROUP_COLORS.explore, 0.3),
                    ["--focus-ring-shadow" as string]: withAlpha(NODE_GROUP_COLORS.explore, 0.1),
                  }}
                >
                  <span
                    className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md"
                    style={{
                      color: chart.enabled ? NODE_GROUP_COLORS.explore : "var(--text-muted)",
                      background: chart.enabled
                        ? withAlpha(NODE_GROUP_COLORS.explore, 0.12)
                        : "var(--bg-hover)",
                    }}
                  >
                    <BarChart3 size={15} aria-hidden="true" />
                  </span>
                  <span className="min-w-0 flex-1">
                    <span
                      className="block truncate text-xs font-semibold"
                      style={{ color: chart.enabled ? NODE_GROUP_COLORS.explore : "var(--text-primary)" }}
                    >
                      {label}
                    </span>
                    <span className="mt-0.5 flex items-center gap-1 text-[10px]" style={{ color: "var(--text-muted)" }}>
                      {chart.enabled ? <Eye size={10} aria-hidden="true" /> : <EyeOff size={10} aria-hidden="true" />}
                      {chart.enabled ? "Shown" : "Hidden"}
                    </span>
                  </span>
                </button>
                <button
                  type="button"
                  aria-label={`Configure ${label}`}
                  onClick={() => setConfiguredChartId(chart.id)}
                  className="focus-ring m-1.5 inline-flex shrink-0 items-center gap-1 rounded-md px-2 py-1 text-[11px] font-semibold hover:bg-[var(--bg-hover)]"
                  style={{
                    color: "var(--text-secondary)",
                    border: "1px solid var(--border)",
                    ["--focus-ring-border" as string]: withAlpha(NODE_GROUP_COLORS.explore, 0.3),
                    ["--focus-ring-shadow" as string]: withAlpha(NODE_GROUP_COLORS.explore, 0.1),
                  }}
                >
                  <SlidersHorizontal size={11} aria-hidden="true" />
                  Configure
                </button>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
