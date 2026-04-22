/**
 * Bottom-panel chart view for the optimiser node's input data (pre-solve).
 *
 * Groups preview rows by quote_id and renders per-quote line charts of
 * objective / constraint columns against scenario_index.  The user can
 * navigate between quotes via prev/next arrows or a search input.
 */

import { useState, useMemo, useCallback, useEffect, memo } from "react"
import {
  ChevronDown,
  ChevronUp,
  ChevronLeft,
  ChevronRight,
  Target,
  Search,
} from "lucide-react"
import { useDragResize } from "../hooks/useDragResize"
import { formatAxisLabel, yTicks } from "../utils/chartHelpers"
import type { PreviewData } from "./DataPreview"

// ─── Colours for series lines (CVD-safe Okabe-Ito subset) ─────────
const SERIES_COLORS = [
  "#f59e0b", // amber  – objective (first)
  "#3b82f6", // blue
  "#22c55e", // green
  "#ef4444", // red
  "#a855f7", // purple
  "#06b6d4", // cyan
  "#f97316", // orange
  "#ec4899", // pink
]

// ─── Types ────────────────────────────────────────────────────────

interface OptimiserDataPreviewProps {
  data: PreviewData
  config: Record<string, unknown>
}

type QuoteRow = {
  scenarioIndex: number
  scenarioValue: number
  values: Record<string, number> // objective + constraint column values
}

// ─── Shared chart primitives ─────────────────────────────────────

const CHART_PX = 44 // left padding for Y axis labels
const CHART_PX_RIGHT = 12
const CHART_PY = 14
const CHART_PY_BOTTOM = 22

interface ScaleContext {
  xScale: (i: number) => number
  yScale: (v: number) => number
  ticks: number[]
  chartW: number
  w: number
  h: number
}

function buildScales(
  rows: QuoteRow[],
  columns: string[],
  w: number,
  h: number,
  yPadFraction = 0,
): ScaleContext {
  const chartW = w - CHART_PX - CHART_PX_RIGHT
  const chartH = h - CHART_PY - CHART_PY_BOTTOM

  let vMin = Infinity
  let vMax = -Infinity
  for (const col of columns) {
    for (const r of rows) {
      const v = r.values[col] ?? 0
      if (v < vMin) vMin = v
      if (v > vMax) vMax = v
    }
  }
  if (!Number.isFinite(vMin)) vMin = 0
  if (!Number.isFinite(vMax)) vMax = 1
  const rawRange = vMax - vMin || 1
  const ticks = yTicks(vMin, vMax)

  const pad = rawRange * yPadFraction
  const adjMin = vMin - pad
  const adjRange = rawRange + pad * 2 || 1

  const indices = rows.map((r) => r.scenarioIndex)
  const iMin = Math.min(...indices)
  const iMax = Math.max(...indices)
  const iRange = iMax - iMin || 1

  return {
    xScale: (i: number) => CHART_PX + ((i - iMin) / iRange) * chartW,
    yScale: (v: number) => CHART_PY + chartH - ((v - adjMin) / adjRange) * chartH,
    ticks,
    chartW,
    w,
    h,
  }
}

/** Shared Y-axis grid lines, tick labels, and X-axis label. */
function ChartGrid({ ctx }: { ctx: ScaleContext }) {
  return (
    <>
      {ctx.ticks.map((t) => (
        <g key={t}>
          <line
            x1={CHART_PX}
            y1={ctx.yScale(t)}
            x2={CHART_PX + ctx.chartW}
            y2={ctx.yScale(t)}
            stroke="var(--border)"
            strokeWidth={0.5}
          />
          <text
            x={CHART_PX - 4}
            y={ctx.yScale(t) + 3}
            textAnchor="end"
            fontSize={9}
            fill="var(--text-muted)"
          >
            {formatAxisLabel(t)}
          </text>
        </g>
      ))}
      <text
        x={CHART_PX + ctx.chartW / 2}
        y={ctx.h - 3}
        textAnchor="middle"
        fontSize={9}
        fill="var(--text-muted)"
      >
        scenario index
      </text>
    </>
  )
}

/** SVG path + dots for a single data series. */
function SeriesLine({
  rows,
  column,
  color,
  ctx,
}: {
  rows: QuoteRow[]
  column: string
  color: string
  ctx: ScaleContext
}) {
  const path = rows
    .map(
      (r, idx) =>
        `${idx === 0 ? "M" : "L"}${ctx.xScale(r.scenarioIndex).toFixed(1)},${ctx.yScale(r.values[column] ?? 0).toFixed(1)}`,
    )
    .join(" ")
  return (
    <g>
      <path d={path} fill="none" stroke={color} strokeWidth={1.5} />
      {rows.map((r, i) => (
        <circle
          key={i}
          cx={ctx.xScale(r.scenarioIndex)}
          cy={ctx.yScale(r.values[column] ?? 0)}
          r={2.5}
          fill={color}
        />
      ))}
    </g>
  )
}

// ─── Memoized chart area (avoids rebuilding scales on every parent render) ──

const CHART_W = 520
const CHART_H = 220

const ChartArea = memo(function ChartArea({
  currentRows,
  visibleSeries,
  allSeries,
}: {
  currentRows: QuoteRow[]
  visibleSeries: string[]
  allSeries: string[]
}) {
  const ctx = useMemo(
    () => buildScales(currentRows, visibleSeries, CHART_W, CHART_H, 0.05),
    [currentRows, visibleSeries],
  )
  return (
    <svg
      width={CHART_W}
      height={CHART_H}
      style={{
        background: "var(--bg-input)",
        borderRadius: 6,
        border: "1px solid var(--border)",
      }}
    >
      <ChartGrid ctx={ctx} />
      {visibleSeries.map((col) => (
        <SeriesLine
          key={col}
          rows={currentRows}
          column={col}
          color={SERIES_COLORS[allSeries.indexOf(col) % SERIES_COLORS.length]}
          ctx={ctx}
        />
      ))}
    </svg>
  )
})

// ─── Scenario-level summary statistics ───────────────────────────

type ScenarioStats = {
  scenarioIndex: number
  scenarioValue: number
  count: number
  mean: number
  std: number
  min: number
  p25: number
  median: number
  p75: number
  max: number
}

function computeScenarioStats(
  allQuoteRows: Map<string, QuoteRow[]>,
  scenarioIndices: number[],
  column: string,
): ScenarioStats[] {
  return scenarioIndices.map((si) => {
    const vals: number[] = []
    let scenarioValue = 0
    for (const rows of allQuoteRows.values()) {
      const row = rows.find((r) => r.scenarioIndex === si)
      if (row) {
        vals.push(row.values[column] ?? 0)
        scenarioValue = row.scenarioValue
      }
    }
    vals.sort((a, b) => a - b)
    const n = vals.length
    if (n === 0) {
      return { scenarioIndex: si, scenarioValue, count: 0, mean: 0, std: 0, min: 0, p25: 0, median: 0, p75: 0, max: 0 }
    }
    const sum = vals.reduce((a, b) => a + b, 0)
    const mean = sum / n
    const variance = vals.reduce((a, v) => a + (v - mean) ** 2, 0) / n
    const std = Math.sqrt(variance)
    const percentile = (p: number) => {
      const idx = (p / 100) * (n - 1)
      const lo = Math.floor(idx)
      const hi = Math.ceil(idx)
      if (lo === hi) return vals[lo]
      return vals[lo] + (vals[hi] - vals[lo]) * (idx - lo)
    }
    return {
      scenarioIndex: si,
      scenarioValue,
      count: n,
      mean,
      std,
      min: vals[0],
      p25: percentile(25),
      median: percentile(50),
      p75: percentile(75),
      max: vals[n - 1],
    }
  })
}

function formatStat(v: number): string {
  if (Math.abs(v) >= 1_000_000) return (v / 1_000_000).toFixed(2) + "M"
  if (Math.abs(v) >= 10_000) return (v / 1_000).toFixed(1) + "K"
  if (Math.abs(v) < 0.01 && v !== 0) return v.toExponential(2)
  if (Number.isInteger(v)) return v.toLocaleString()
  return v.toFixed(4)
}

const STAT_COLUMNS = ["Count", "Mean", "Std", "Min", "P25", "Median", "P75", "Max"] as const

function ScenarioStatsTable({
  column,
  color,
  isObjective,
  stats,
}: {
  column: string
  color: string
  isObjective: boolean
  stats: ScenarioStats[]
}) {
  return (
    <div className="mb-4">
      <div className="flex items-center gap-1.5 mb-1.5">
        <span className="inline-block w-2 h-2 rounded-full" style={{ background: color }} />
        <span className="text-[11px] font-mono font-medium" style={{ color: "var(--text-secondary)" }}>
          {column}
          {isObjective && (
            <span className="ml-1 text-[9px] font-sans" style={{ color: "var(--text-muted)" }}>objective</span>
          )}
        </span>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-[11px]">
          <thead>
            <tr>
              <th className="px-2 py-1 text-left font-semibold whitespace-nowrap" style={{ color: "var(--text-muted)", borderBottom: "1px solid var(--border)" }}>
                Scenario
              </th>
              <th className="px-2 py-1 text-left font-semibold whitespace-nowrap" style={{ color: "var(--text-muted)", borderBottom: "1px solid var(--border)" }}>
                Value
              </th>
              {STAT_COLUMNS.map((h) => (
                <th key={h} className="px-2 py-1 text-right font-semibold whitespace-nowrap" style={{ color: "var(--text-muted)", borderBottom: "1px solid var(--border)" }}>
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {stats.map((s, i) => (
              <tr key={s.scenarioIndex} style={{ background: i % 2 === 0 ? "transparent" : "rgba(255,255,255,.02)" }}>
                <td className="px-2 py-0.5 font-mono" style={{ color: "var(--text-primary)" }}>{s.scenarioIndex}</td>
                <td className="px-2 py-0.5 font-mono" style={{ color: "var(--text-muted)" }}>{formatStat(s.scenarioValue)}</td>
                <td className="px-2 py-0.5 font-mono text-right" style={{ color: "var(--text-secondary)" }}>{s.count}</td>
                <td className="px-2 py-0.5 font-mono text-right" style={{ color: "var(--text-primary)" }}>{formatStat(s.mean)}</td>
                <td className="px-2 py-0.5 font-mono text-right" style={{ color: "var(--text-secondary)" }}>{formatStat(s.std)}</td>
                <td className="px-2 py-0.5 font-mono text-right" style={{ color: "var(--text-secondary)" }}>{formatStat(s.min)}</td>
                <td className="px-2 py-0.5 font-mono text-right" style={{ color: "var(--text-secondary)" }}>{formatStat(s.p25)}</td>
                <td className="px-2 py-0.5 font-mono text-right" style={{ color: "var(--text-primary)" }}>{formatStat(s.median)}</td>
                <td className="px-2 py-0.5 font-mono text-right" style={{ color: "var(--text-secondary)" }}>{formatStat(s.p75)}</td>
                <td className="px-2 py-0.5 font-mono text-right" style={{ color: "var(--text-secondary)" }}>{formatStat(s.max)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

// ─── StatusBar ───────────────────────────────────────────────────

function StatusBar({ children }: { children: React.ReactNode }) {
  return (
    <div
      className="h-8 flex items-center px-4 shrink-0"
      style={{
        borderTop: "1px solid var(--border)",
        background: "var(--bg-panel)",
      }}
    >
      {children}
    </div>
  )
}

// ─── Component ────────────────────────────────────────────────────

export default function OptimiserDataPreview({
  data,
  config,
}: OptimiserDataPreviewProps) {
  const [collapsed, setCollapsed] = useState(false)
  const [tab, setTab] = useState<"chart" | "statistics">("chart")
  const { height, containerRef, onDragStart } = useDragResize({
    initialHeight: 320,
    minHeight: 160,
    maxHeight: 600,
  })

  // ── Extract column names from optimiser config ──
  const objectiveCol = (config.objective as string) || ""
  const constraintsMap = useMemo(
    () => (config.constraints ?? {}) as Record<string, Record<string, number>>,
    [config.constraints],
  )
  const constraintCols = useMemo(
    () => Object.keys(constraintsMap),
    [constraintsMap],
  )
  const quoteIdCol = (config.quote_id as string) || "quote_id"
  const scenarioIndexCol =
    (config.scenario_index as string) || "scenario_index"
  const scenarioValueCol =
    (config.scenario_value as string) || "scenario_value"

  // All plottable series: objective first, then constraints
  const allSeries = useMemo(() => {
    const s: string[] = []
    if (objectiveCol) s.push(objectiveCol)
    for (const c of constraintCols) {
      if (c !== objectiveCol) s.push(c)
    }
    return s
  }, [objectiveCol, constraintCols])

  // Stable key for detecting when the actual series list changes
  const allSeriesKey = allSeries.join("\0")

  // ── Checkbox state: which series are visible ──
  const [checkedSeries, setCheckedSeries] = useState<Set<string>>(
    () => new Set(allSeries),
  )
  // Sync checked set when allSeries changes (config edit)
  useEffect(() => {
    setCheckedSeries(new Set(allSeries))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [allSeriesKey])

  const toggleSeries = useCallback((col: string) => {
    setCheckedSeries((prev) => {
      const next = new Set(prev)
      if (next.has(col)) next.delete(col)
      else next.add(col)
      return next
    })
  }, [])

  // ── Group preview rows by quote_id ──
  const { quoteIds, quoteData } = useMemo(() => {
    const map = new Map<string, QuoteRow[]>()
    for (const row of data.preview) {
      const qid = String(row[quoteIdCol] ?? "")
      const si = Number(row[scenarioIndexCol] ?? 0)
      const sv = Number(row[scenarioValueCol] ?? 0)
      const vals: Record<string, number> = {}
      for (const col of allSeries) {
        vals[col] = Number(row[col] ?? 0)
      }
      if (!map.has(qid)) map.set(qid, [])
      map.get(qid)!.push({ scenarioIndex: si, scenarioValue: sv, values: vals })
    }
    // Sort each quote's rows by scenario_index
    for (const rows of map.values()) {
      rows.sort((a, b) => a.scenarioIndex - b.scenarioIndex)
    }
    const ids = [...map.keys()]
    return { quoteIds: ids, quoteData: map }
  }, [data.preview, quoteIdCol, scenarioIndexCol, scenarioValueCol, allSeries])

  // ── Quote navigation ──
  const [currentIndex, setCurrentIndex] = useState(0)
  const [searchValue, setSearchValue] = useState("")

  // Reset index when data changes
  useEffect(() => {
    setCurrentIndex(0)
    setSearchValue("")
  }, [quoteIds.length])

  const clampedIndex = Math.min(currentIndex, Math.max(0, quoteIds.length - 1))
  const currentQuoteId = quoteIds[clampedIndex] ?? ""
  const currentRows = quoteData.get(currentQuoteId) ?? []

  const goPrev = useCallback(
    () => setCurrentIndex((i) => Math.max(0, i - 1)),
    [],
  )
  const goNext = useCallback(
    () => setCurrentIndex((i) => Math.min(quoteIds.length - 1, i + 1)),
    [quoteIds.length],
  )
  const handleSearchSubmit = useCallback(() => {
    const idx = quoteIds.indexOf(searchValue.trim())
    if (idx >= 0) {
      setCurrentIndex(idx)
      setSearchValue("")
    }
  }, [quoteIds, searchValue])

  const visibleSeries = useMemo(
    () => allSeries.filter((s) => checkedSeries.has(s)),
    [allSeries, checkedSeries],
  )

  // ── Scenario-level summary statistics (aggregated across all quotes) ──
  // Deferred: only computed when the statistics tab is active to avoid
  // expensive O(n·log·n × #series) sorting on every render.
  const scenarioIndices = useMemo(() => {
    if (tab !== "statistics") return []
    const idxSet = new Set<number>()
    for (const rows of quoteData.values()) {
      for (const r of rows) idxSet.add(r.scenarioIndex)
    }
    return [...idxSet].sort((a, b) => a - b)
  }, [quoteData, tab])

  const scenarioStatsBySeries = useMemo(() => {
    if (tab !== "statistics") return new Map<string, ScenarioStats[]>()
    const map = new Map<string, ScenarioStats[]>()
    for (const col of allSeries) {
      map.set(col, computeScenarioStats(quoteData, scenarioIndices, col))
    }
    return map
  }, [allSeries, quoteData, scenarioIndices, tab])

  // ── No config / no data guards ──
  if (!objectiveCol) {
    return (
      <StatusBar>
        <span className="text-xs" style={{ color: "var(--text-muted)" }}>
          Configure an objective column to see the quote chart.
        </span>
      </StatusBar>
    )
  }

  if (quoteIds.length === 0 || currentRows.length === 0) {
    return (
      <StatusBar>
        <Target size={14} style={{ color: "var(--warning-strong)" }} className="mr-2" />
        <span className="text-xs" style={{ color: "var(--text-muted)" }}>
          No scenario data in preview. Ensure upstream nodes produce{" "}
          <span className="font-mono">{quoteIdCol}</span> and{" "}
          <span className="font-mono">{scenarioIndexCol}</span> columns.
        </span>
      </StatusBar>
    )
  }

  // ── Collapsed ──
  if (collapsed) {
    return (
      <StatusBar>
        <button
          onClick={() => setCollapsed(false)}
          className="flex items-center gap-2 text-xs"
          style={{ color: "var(--text-secondary)" }}
        >
          <ChevronUp size={14} />
          <Target size={14} />
          <span className="font-medium">{data.nodeLabel}</span>
          <span style={{ color: "var(--text-muted)" }}>
            {quoteIds.length} quotes · {currentRows.length} scenarios
          </span>
        </button>
      </StatusBar>
    )
  }

  // ── Expanded ──
  return (
    <div
      ref={containerRef}
      style={{
        height,
        borderTop: "1px solid var(--border)",
        background: "var(--bg-panel)",
      }}
      className="flex flex-col shrink-0 relative"
    >
      {/* Drag handle */}
      <div
        onMouseDown={onDragStart}
        className="drag-handle-hover absolute top-0 left-0 right-0 h-1 cursor-ns-resize z-10"
      />

      {/* Header */}
      <div
        className="min-h-9 flex items-center flex-wrap px-4 shrink-0 gap-x-2 gap-y-1 py-1.5"
        style={{
          borderBottom: "1px solid var(--border)",
          background: "var(--bg-elevated)",
        }}
      >
        <Target size={14} style={{ color: "var(--warning-strong)" }} />
        <span
          className="text-xs font-bold"
          style={{ color: "var(--text-primary)" }}
        >
          {data.nodeLabel}
        </span>
        <span className="text-[11px]" style={{ color: "var(--text-muted)" }}>
          {quoteIds.length} quotes · {currentRows.length} scenarios
        </span>

        {/* Quote navigation (chart tab only) */}
        {tab === "chart" && <div className="flex items-center gap-1 ml-3">
          <button
            onClick={goPrev}
            disabled={clampedIndex === 0}
            className="p-0.5 rounded transition-colors"
            style={{
              color:
                clampedIndex === 0
                  ? "var(--text-muted)"
                  : "var(--text-secondary)",
              opacity: clampedIndex === 0 ? 0.4 : 1,
            }}
          >
            <ChevronLeft size={14} />
          </button>
          <span
            className="text-[11px] font-mono min-w-[80px] text-center"
            style={{ color: "var(--text-primary)" }}
            title={currentQuoteId}
          >
            {currentQuoteId.length > 12
              ? currentQuoteId.slice(0, 10) + "…"
              : currentQuoteId}
          </span>
          <button
            onClick={goNext}
            disabled={clampedIndex >= quoteIds.length - 1}
            className="p-0.5 rounded transition-colors"
            style={{
              color:
                clampedIndex >= quoteIds.length - 1
                  ? "var(--text-muted)"
                  : "var(--text-secondary)",
              opacity: clampedIndex >= quoteIds.length - 1 ? 0.4 : 1,
            }}
          >
            <ChevronRight size={14} />
          </button>
          <span
            className="text-[10px]"
            style={{ color: "var(--text-muted)" }}
          >
            {clampedIndex + 1}/{quoteIds.length}
          </span>
        </div>}

        {/* Quote search (chart tab only) */}
        {tab === "chart" && (
          <div
            className="flex items-center gap-1 px-1.5 py-0.5 rounded-md ml-1"
            style={{
              background: "var(--chrome-hover)",
              border: "1px solid var(--chrome-border)",
            }}
          >
            <Search size={11} style={{ color: "var(--text-muted)" }} />
            <input
              type="text"
              value={searchValue}
              onChange={(e) => setSearchValue(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") handleSearchSubmit()
              }}
              placeholder="Find quote..."
              className="w-24 text-[11px] font-mono bg-transparent focus:outline-none"
              style={{ color: "var(--text-primary)" }}
            />
          </div>
        )}

        {/* Tab selector */}
        <div className="flex gap-1 ml-3">
          {(["chart", "statistics"] as const).map((t) => (
            <button
              key={t}
              onClick={() => setTab(t)}
              className="px-2 py-0.5 rounded text-[10px] font-medium"
              style={{
                background: tab === t ? "var(--accent-soft)" : "var(--chrome-hover)",
                color: tab === t ? "var(--accent)" : "var(--text-muted)",
              }}
            >
              {t === "chart" ? "Chart" : "Statistics"}
            </button>
          ))}
        </div>

        <div className="ml-auto flex items-center gap-1">
          <button
            onClick={() => setCollapsed(true)}
            className="p-1 rounded transition-colors hover:bg-[var(--bg-hover)]"
            style={{ color: "var(--text-muted)" }}
          >
            <ChevronDown size={14} />
          </button>
        </div>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-auto px-4 py-3">
        {tab === "chart" ? (
          <div className="flex gap-6">
            {/* Legend / series checkboxes */}
            <div className="shrink-0 space-y-1 min-w-[140px]">
              <label
                className="text-[11px] font-bold uppercase tracking-[0.08em]"
                style={{ color: "var(--text-muted)" }}
              >
                Series
              </label>
              {allSeries.map((col, i) => (
                <label
                  key={col}
                  className="flex items-center gap-1.5 text-xs cursor-pointer select-none"
                  style={{ color: "var(--text-secondary)" }}
                >
                  <input
                    type="checkbox"
                    checked={checkedSeries.has(col)}
                    onChange={() => toggleSeries(col)}
                    style={{ accentColor: SERIES_COLORS[i % SERIES_COLORS.length] }}
                  />
                  <span
                    className="inline-block w-2 h-2 rounded-full shrink-0"
                    style={{
                      background: SERIES_COLORS[i % SERIES_COLORS.length],
                      opacity: checkedSeries.has(col) ? 1 : 0.3,
                    }}
                  />
                  <span className="font-mono truncate max-w-[120px]" title={col}>
                    {col}
                    {col === objectiveCol && (
                      <span
                        className="ml-1 text-[9px] font-sans"
                        style={{ color: "var(--text-muted)" }}
                      >
                        obj
                      </span>
                    )}
                  </span>
                </label>
              ))}

              {/* Per-quote summary */}
              <div className="mt-3 pt-2" style={{ borderTop: "1px solid var(--border)" }}>
                <label
                  className="text-[11px] font-bold uppercase tracking-[0.08em]"
                  style={{ color: "var(--text-muted)" }}
                >
                  Quote
                </label>
                <div className="mt-1 text-[11px] font-mono" style={{ color: "var(--text-secondary)" }}>
                  <div className="flex justify-between gap-2">
                    <span style={{ color: "var(--text-muted)" }}>ID</span>
                    <span title={currentQuoteId}>
                      {currentQuoteId.length > 14
                        ? currentQuoteId.slice(0, 12) + "…"
                        : currentQuoteId}
                    </span>
                  </div>
                  <div className="flex justify-between gap-2">
                    <span style={{ color: "var(--text-muted)" }}>Scenarios</span>
                    <span>{currentRows.length}</span>
                  </div>
                </div>
              </div>
            </div>

            {/* Chart area */}
            <div className="flex-1 min-w-0">
              {visibleSeries.length === 0 ? (
                <div
                  className="text-xs py-4 text-center"
                  style={{ color: "var(--text-muted)" }}
                >
                  Select at least one series to plot.
                </div>
              ) : (
                <ChartArea
                  currentRows={currentRows}
                  visibleSeries={visibleSeries}
                  allSeries={allSeries}
                />
              )}
            </div>
          </div>
        ) : (
          /* Statistics tab */
          <div>
            {allSeries.map((col, i) => {
              const stats = scenarioStatsBySeries.get(col)
              if (!stats) return null
              return (
                <ScenarioStatsTable
                  key={col}
                  column={col}
                  color={SERIES_COLORS[i % SERIES_COLORS.length]}
                  isObjective={col === objectiveCol}
                  stats={stats}
                />
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}
