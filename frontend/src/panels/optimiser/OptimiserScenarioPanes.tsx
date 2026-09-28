/**
 * The optimiser node's pre-solve scenario data: per-quote curves of the
 * objective and constraint columns against the scenario grid, and their
 * per-scenario statistics across quotes. Shown by the data preview before a
 * solve and as the result workspace's Curves and Statistics panes after one.
 */

import { memo, useMemo } from "react"
import { ChevronLeft, ChevronRight, Search } from "lucide-react"
import { CHART_COLORS } from "../../theme/colors"
import { chartTicks, formatChartTicks } from "../../utils/chartHelpers"
import type { ScenarioStats } from "../optimiserScenarioStats"
import type { OptimiserScenarioData, QuoteRow } from "./useOptimiserScenarioData"

// ─── Colours for series lines (CVD-safe Okabe-Ito subset) ─────────
const SERIES_COLORS = CHART_COLORS.optimiserSeries

// ─── Types ────────────────────────────────────────────────────────


// ─── Shared chart primitives ─────────────────────────────────────

const CHART_PX = 44 // left padding for Y axis labels
const CHART_PX_RIGHT = 12
const CHART_PY = 14
const CHART_PY_BOTTOM = 22
const RIGHT_AXIS_GAP = 52
const MIN_CHART_W = 180

interface SeriesScaleContext {
  yScale: (v: number) => number
  ticks: number[]
  /** The ticks' labels, formatted together. */
  labels: string[]
}

interface ScaleContext {
  xScale: (i: number) => number
  seriesScales: Record<string, SeriesScaleContext>
  chartW: number
  w: number
  h: number
  axisColumns: string[]
  rightAxisGap: number
}

function buildSeriesScale(
  rows: QuoteRow[],
  column: string,
  h: number,
  yPadFraction = 0,
): SeriesScaleContext {
  const chartH = h - CHART_PY - CHART_PY_BOTTOM

  let vMin = Infinity
  let vMax = -Infinity
  for (const r of rows) {
    const v = r.values[column] ?? 0
    if (v < vMin) vMin = v
    if (v > vMax) vMax = v
  }
  if (!Number.isFinite(vMin)) vMin = 0
  if (!Number.isFinite(vMax)) vMax = 1
  const rawRange = vMax - vMin || 1
  const ticks = chartTicks(vMin, vMax, 5)

  const pad = rawRange * yPadFraction
  const adjMin = vMin - pad
  const adjRange = rawRange + pad * 2 || 1

  return {
    yScale: (v: number) => CHART_PY + chartH - ((v - adjMin) / adjRange) * chartH,
    ticks,
    labels: formatChartTicks(ticks),
  }
}

function buildScales(
  rows: QuoteRow[],
  columns: string[],
  w: number,
  h: number,
  yPadFraction = 0,
): ScaleContext {
  const rightAxisCount = Math.max(0, columns.length - 1)
  const maxRightAxisSpace = Math.max(0, w - CHART_PX - CHART_PX_RIGHT - MIN_CHART_W)
  const rightAxisGap = rightAxisCount > 0
    ? Math.min(RIGHT_AXIS_GAP, Math.floor(maxRightAxisSpace / rightAxisCount))
    : 0
  const chartW = w - CHART_PX - CHART_PX_RIGHT - rightAxisGap * rightAxisCount

  const indices = rows.map((r) => r.scenarioIndex)
  const iMin = Math.min(...indices)
  const iMax = Math.max(...indices)
  const iRange = iMax - iMin || 1
  const seriesScales = Object.fromEntries(
    columns.map((col) => [col, buildSeriesScale(rows, col, h, yPadFraction)]),
  )

  return {
    xScale: (i: number) => CHART_PX + ((i - iMin) / iRange) * chartW,
    seriesScales,
    chartW,
    w,
    h,
    axisColumns: columns,
    rightAxisGap,
  }
}

/** Shared X-axis with one colour-coded Y axis per visible series. */
function ChartGrid({
  ctx,
  allSeries,
}: {
  ctx: ScaleContext
  allSeries: string[]
}) {
  const primaryColumn = ctx.axisColumns[0]
  const primaryScale = primaryColumn ? ctx.seriesScales[primaryColumn] : null
  const plotRight = CHART_PX + ctx.chartW
  return (
    <>
      {primaryScale?.ticks.map((t, index) => (
        <g key={`${primaryColumn}-${t}`}>
          <line
            x1={CHART_PX}
            y1={primaryScale.yScale(t)}
            x2={plotRight}
            y2={primaryScale.yScale(t)}
            stroke="var(--border)"
            strokeWidth={0.5}
          />
          <text
            x={CHART_PX - 4}
            y={primaryScale.yScale(t) + 3}
            textAnchor="end"
            fontSize={9}
            fill={SERIES_COLORS[allSeries.indexOf(primaryColumn) % SERIES_COLORS.length]}
          >
            {primaryScale.labels[index]}
          </text>
        </g>
      ))}
      {ctx.axisColumns.map((column, axisIndex) => {
        const scale = ctx.seriesScales[column]
        const color = SERIES_COLORS[allSeries.indexOf(column) % SERIES_COLORS.length]
        if (!scale) return null
        if (axisIndex === 0) {
          return (
            <line
              key={`${column}-axis`}
              data-testid={`axis-${column}`}
              x1={CHART_PX}
              y1={CHART_PY}
              x2={CHART_PX}
              y2={ctx.h - CHART_PY_BOTTOM}
              stroke={color}
              strokeWidth={0.8}
              opacity={0.7}
            />
          )
        }
        const x = plotRight + 12 + (axisIndex - 1) * ctx.rightAxisGap
        return (
          <g key={`${column}-axis`} data-testid={`axis-${column}`}>
            <line
              x1={x}
              y1={CHART_PY}
              x2={x}
              y2={ctx.h - CHART_PY_BOTTOM}
              stroke={color}
              strokeWidth={0.8}
              opacity={0.7}
            />
            {scale.ticks.map((t, index) => (
              <g key={`${column}-${t}`}>
                <line
                  x1={x - 3}
                  y1={scale.yScale(t)}
                  x2={x}
                  y2={scale.yScale(t)}
                  stroke={color}
                  strokeWidth={0.7}
                  opacity={0.7}
                />
                <text
                  x={x + 4}
                  y={scale.yScale(t) + 3}
                  fontSize={9}
                  fill={color}
                >
                  {scale.labels[index]}
                </text>
              </g>
            ))}
          </g>
        )
      })}
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
  const yScale = ctx.seriesScales[column]?.yScale
  if (!yScale) return null
  const path = rows
    .map(
      (r, idx) =>
        `${idx === 0 ? "M" : "L"}${ctx.xScale(r.scenarioIndex).toFixed(1)},${yScale(r.values[column] ?? 0).toFixed(1)}`,
    )
    .join(" ")
  return (
    <g>
      <path d={path} fill="none" stroke={color} strokeWidth={1.5} />
      {rows.map((r, i) => (
        <circle
          key={i}
          cx={ctx.xScale(r.scenarioIndex)}
          cy={yScale(r.values[column] ?? 0)}
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
      <ChartGrid ctx={ctx} allSeries={allSeries} />
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

// ─── Component ────────────────────────────────────────────────────

export function ScenarioDataNotice({
  scenario,
  reason,
}: {
  scenario: OptimiserScenarioData
  reason: "no_objective" | "no_data"
}) {
  return (
    <div className="flex-1 flex items-center px-4 text-xs" style={{ color: "var(--text-muted)" }}>
      {reason === "no_objective" ? (
        "Configure an objective column to see the quote chart."
      ) : (
        <>
          No scenario data in preview. Ensure upstream nodes produce{" "}
          <span className="font-mono">{scenario.quoteIdCol}</span> and{" "}
          <span className="font-mono">{scenario.scenarioIndexCol}</span> columns.
        </>
      )}
    </div>
  )
}

/** Previous/next quote, the position, and the quote ID search. */
export function ScenarioQuoteNavigation({ scenario }: { scenario: OptimiserScenarioData }) {
  const {
    clampedIndex,
    currentQuoteId,
    quoteIds,
    goPrev,
    goNext,
    searchValue,
    setSearchValue,
    handleSearchSubmit,
  } = scenario
  return (
    <>
      <div className="flex items-center gap-1" data-testid="optimiser-quote-navigation">
        <button
          type="button"
          onClick={goPrev}
          disabled={clampedIndex === 0}
          className="p-0.5 rounded transition-colors"
          style={{
            color: clampedIndex === 0 ? "var(--text-muted)" : "var(--text-secondary)",
            opacity: clampedIndex === 0 ? 0.4 : 1,
          }}
          aria-label="Previous quote"
        >
          <ChevronLeft size={14} />
        </button>
        <span
          className="text-[11px] font-mono w-20 truncate text-center"
          style={{ color: "var(--text-primary)" }}
          title={currentQuoteId}
        >
          {currentQuoteId.length > 12 ? currentQuoteId.slice(0, 10) + "..." : currentQuoteId}
        </span>
        <button
          type="button"
          onClick={goNext}
          disabled={clampedIndex >= quoteIds.length - 1}
          className="p-0.5 rounded transition-colors"
          style={{
            color: clampedIndex >= quoteIds.length - 1 ? "var(--text-muted)" : "var(--text-secondary)",
            opacity: clampedIndex >= quoteIds.length - 1 ? 0.4 : 1,
          }}
          aria-label="Next quote"
        >
          <ChevronRight size={14} />
        </button>
        <span className="text-[10px]" style={{ color: "var(--text-muted)" }}>
          {clampedIndex + 1}/{quoteIds.length}
        </span>
      </div>
      <div
        className="flex items-center gap-1 px-1.5 py-0.5 rounded-md"
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
    </>
  )
}

/** One quote's objective and constraint curves across its scenarios, with the series legend. */
export function ScenarioCurvesPane({ scenario }: { scenario: OptimiserScenarioData }) {
  const {
    allSeries,
    checkedSeries,
    toggleSeries,
    objectiveCol,
    currentQuoteId,
    currentRows,
    visibleSeries,
  } = scenario
  return (
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
  )
}

/** Per-scenario summary statistics of the objective and each constraint, across the preview's quotes. */
export function ScenarioStatisticsPane({ scenario }: { scenario: OptimiserScenarioData }) {
  const { allSeries, scenarioStatsBySeries, objectiveCol } = scenario
  return (
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
  )
}
