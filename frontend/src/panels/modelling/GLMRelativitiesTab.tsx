/**
 * Relativities display for GLM results.
 *
 * Horizontal bar chart showing each term's relativity (exponentiated
 * coefficient for log-link models). Bars extend left/right from 1.0
 * (the baseline). Optional CI whiskers when available.
 */
import { useState, useMemo } from "react"
import type { TrainResult } from "../../stores/useNodeResultsStore"
import { formatFixed } from "../../utils/formatValue"

interface GLMRelativitiesTabProps {
  result: TrainResult
}

type SortMode = "name" | "relativity" | "deviation"

const BAR_ABOVE = "var(--chart-above)"
const BAR_BELOW = "var(--chart-below)"

export function GLMRelativitiesTab({ result }: GLMRelativitiesTabProps) {
  const rows = result.glm_relativities
  const [sortMode, setSortMode] = useState<SortMode>("deviation")

  const sorted = useMemo(() => {
    if (!rows || rows.length === 0) return []
    return [...rows].sort((a, b) => {
      switch (sortMode) {
        case "name": return a.feature.localeCompare(b.feature)
        case "relativity": return b.relativity - a.relativity
        case "deviation": return Math.abs(b.relativity - 1) - Math.abs(a.relativity - 1)
      }
    })
  }, [rows, sortMode])

  if (!rows || rows.length === 0) {
    return (
      <div className="flex items-center justify-center h-full text-xs" style={{ color: "var(--text-muted)" }}>
        No relativity data available
      </div>
    )
  }

  // Scale: find max deviation from 1.0
  const maxDev = Math.max(rows.map(r => Math.abs(r.relativity - 1)).reduce((a, b) => Math.max(a, b), -Infinity), 0.1)
  const hasCi = rows.some(r => r.ci_lower != null && r.ci_upper != null)

  return (
    <div className="space-y-2">
      {/* Sort controls */}
      <div className="flex gap-1">
        {([
          { key: "deviation", label: "By deviation" },
          { key: "relativity", label: "By value" },
          { key: "name", label: "A–Z" },
        ] as const).map(s => (
          <button
            key={s.key}
            onClick={() => setSortMode(s.key)}
            className="px-2 py-0.5 rounded text-[10px] font-medium"
            style={{
              background: sortMode === s.key ? "var(--accent-soft)" : "var(--chrome-hover)",
              color: sortMode === s.key ? "var(--accent)" : "var(--text-muted)",
            }}
          >
            {s.label}
          </button>
        ))}
      </div>

      {/* Bar chart */}
      <div className="overflow-y-auto" style={{ maxHeight: 480 }}>
        <div className="space-y-0.5">
          {sorted.map((row, i) => {
            const dev = typeof row.relativity === 'number' ? row.relativity - 1 : 0
            const pct = (Math.abs(dev) / maxDev) * 50  // 50% of the bar area
            const isAbove = dev >= 0

            return (
              <div key={`${row.feature}-${i}`} className="flex items-center gap-2 text-xs font-mono group">
                {/* Feature name */}
                <span
                  className="truncate shrink-0 text-right"
                  style={{ color: "var(--text-secondary)", width: 140 }}
                  title={row.feature}
                >
                  {row.feature}
                </span>

                {/* Bar area — centered on 1.0 */}
                <div className="flex-1 h-4 relative" style={{ background: "var(--chrome-hover)", borderRadius: 3 }}>
                  {/* Center line (1.0) */}
                  <div
                    className="absolute top-0 bottom-0 w-px"
                    style={{ left: "50%", background: "rgba(255,255,255,.15)" }}
                  />

                  {/* Bar */}
                  <div
                    className="absolute top-0.5 bottom-0.5 rounded-sm"
                    style={{
                      left: isAbove ? "50%" : `${50 - pct}%`,
                      width: `${pct}%`,
                      background: isAbove ? BAR_ABOVE : BAR_BELOW,
                      opacity: 0.7,
                    }}
                  />

                  {/* CI whiskers */}
                  {hasCi && row.ci_lower != null && row.ci_upper != null
                    && Number.isFinite(row.ci_lower) && Number.isFinite(row.ci_upper) && (
                    <>
                      <div
                        className="absolute top-1/2 h-px"
                        style={{
                          left: `${50 + ((row.ci_lower - 1) / maxDev) * 50}%`,
                          width: `${((row.ci_upper - row.ci_lower) / maxDev) * 50}%`,
                          background: "rgba(255,255,255,.3)",
                          transform: "translateY(-50%)",
                        }}
                      />
                    </>
                  )}
                </div>

                {/* Relativity value */}
                <span
                  className="w-14 text-right shrink-0"
                  style={{ color: isAbove ? BAR_ABOVE : BAR_BELOW }}
                >
                  {formatFixed(row.relativity, 3)}
                </span>
              </div>
            )
          })}
        </div>
      </div>

      {/* Legend */}
      <div className="flex gap-4 text-[10px]" style={{ color: "var(--text-muted)" }}>
        <span>Baseline = 1.0 (center line)</span>
        <span style={{ color: BAR_ABOVE }}>&#9632; Above baseline</span>
        <span style={{ color: BAR_BELOW }}>&#9632; Below baseline</span>
        {hasCi && <span>- CI whiskers</span>}
        <span>{rows.length} term{rows.length !== 1 ? "s" : ""}</span>
      </div>
    </div>
  )
}
