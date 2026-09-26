/**
 * Relativities display for GLM results.
 *
 * Each term's relativity (exponentiated coefficient for log-link models) on
 * the shared RelativityBars: bars extend left/right from 1.0 (the baseline),
 * with CI whiskers when available.
 */
import { useState, useMemo } from "react"
import type { TrainResult } from "../../stores/useNodeResultsStore"
import { RELATIVITY_ABOVE_COLOR, RELATIVITY_BELOW_COLOR, RelativityBars } from "../RelativityBars"

interface GLMRelativitiesTabProps {
  result: TrainResult
}

type SortMode = "name" | "relativity" | "deviation"

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

      <RelativityBars
        ariaLabel="GLM relativities"
        bars={sorted.map((row, i) => ({
          key: `${row.feature}-${i}`,
          label: row.feature,
          value: row.relativity,
          ciLower: row.ci_lower,
          ciUpper: row.ci_upper,
        }))}
      />

      {/* Legend */}
      <div className="flex gap-4 text-[10px]" style={{ color: "var(--text-muted)" }}>
        <span>Baseline = 1.0 (center line)</span>
        <span style={{ color: RELATIVITY_ABOVE_COLOR }}>&#9632; Above baseline</span>
        <span style={{ color: RELATIVITY_BELOW_COLOR }}>&#9632; Below baseline</span>
        {hasCi && <span>- CI whiskers</span>}
        <span>{rows.length} term{rows.length !== 1 ? "s" : ""}</span>
      </div>
    </div>
  )
}
