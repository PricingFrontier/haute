/**
 * Schema table card for the Explore preview's Overview pane.
 *
 * Lists every column in the cached dataset with its dtype, null percentage,
 * distinct count, and an example value. Purely presentational — receives a
 * validated `ExploreCacheReport` via props.
 *
 * Null-% colour ramp:
 *   exactly 0   -> muted (uninteresting, all-populated)
 *   (0, 50]     -> primary
 *   > 50        -> warning-strong (call out high-null columns)
 *   undefined   -> em-dash when row_count === 0
 */

import { useMemo, useState } from "react"
import { ChevronLeft, ChevronRight, Columns, Search } from "lucide-react"
import type { ExploreCacheReport, ExploreColumnStat } from "../../api/types"
import { NODE_GROUP_COLORS } from "../../theme/colors"
import { getDtypeColor } from "../../utils/dtypeColors"

interface SchemaTableCardProps {
  report: ExploreCacheReport
}

const HEADER_CLASS =
  "text-[10px] font-bold uppercase tracking-[0.08em] text-left px-2 py-1.5"
const HEADER_STYLE = {
  color: "var(--text-secondary)",
  background: "var(--bg-elevated)",
  position: "sticky" as const,
  top: 0,
  zIndex: 1,
}

type NullSeverity = "high" | "none" | "normal"

function nullSeverity(nullCount: number, rowCount: number): NullSeverity {
  if (rowCount === 0) return "none"
  const pct = (nullCount / rowCount) * 100
  if (pct > 50) return "high"
  if (pct === 0) return "none"
  return "normal"
}

function safeTestId(name: string): string {
  return name.replace(/[^a-zA-Z0-9_-]/g, "_")
}

const ROW_BORDER_STYLE = { borderBottom: "1px solid var(--border)" } as const
const CELL_BASE_CLASS = "px-2 py-1.5"
const MUTED_STYLE = { color: "var(--text-muted)" } as const
const PRIMARY_STYLE = { color: "var(--text-primary)" } as const
const SCHEMA_PAGE_SIZE = 50

/** Format a null-count / row-count ratio as a 1-dp percentage, or null when undefined. */
function formatNullPct(nullCount: number, rowCount: number): string | null {
  if (rowCount === 0) return null
  const pct = (nullCount / rowCount) * 100
  return `${pct.toFixed(1)}%`
}

/** Map a null-% value to the appropriate text colour token. */
function nullPctStyle(nullCount: number, rowCount: number): { color: string } {
  if (rowCount === 0) return MUTED_STYLE
  const pct = (nullCount / rowCount) * 100
  if (pct > 50) return { color: "var(--warning-strong)" }
  if (pct === 0) return MUTED_STYLE
  return PRIMARY_STYLE
}

function SchemaRow({
  column,
  rowCount,
}: {
  column: ExploreColumnStat
  rowCount: number
}) {
  const nullPct = formatNullPct(column.null_count, rowCount)
  const nullStyle = nullPctStyle(column.null_count, rowCount)
  const severity = nullSeverity(column.null_count, rowCount)

  return (
    <tr data-testid={`explore-schema-row-${safeTestId(column.name)}`} style={ROW_BORDER_STYLE}>
      <td
        data-testid="explore-schema-name"
        className={`${CELL_BASE_CLASS} font-mono max-w-[28ch] truncate`}
        style={PRIMARY_STYLE}
        title={column.name}
      >
        {column.name}
      </td>
      <td className={`${CELL_BASE_CLASS} font-mono ${getDtypeColor(column.dtype)}`}>
        {column.dtype}
      </td>
      <td
        data-testid="explore-schema-null-pct"
        data-null-severity={severity}
        className={CELL_BASE_CLASS}
        style={nullStyle}
      >
        {nullPct ?? "—"}
      </td>
      <td
        className={CELL_BASE_CLASS}
        style={column.distinct_count === null ? MUTED_STYLE : PRIMARY_STYLE}
      >
        {column.distinct_count === null
          ? "—"
          : column.distinct_count.toLocaleString()}
      </td>
      {column.example_value === null ? (
        <td className={CELL_BASE_CLASS} style={MUTED_STYLE}>
          —
        </td>
      ) : (
        <td
          data-testid="explore-schema-example"
          className={`${CELL_BASE_CLASS} font-mono max-w-[32ch] truncate`}
          style={PRIMARY_STYLE}
          title={column.example_value}
        >
          {column.example_value}
        </td>
      )}
    </tr>
  )
}

export default function SchemaTableCard({ report }: SchemaTableCardProps) {
  const accent = NODE_GROUP_COLORS.explore
  const columnCount = report.column_count
  const [query, setQuery] = useState("")
  const [pageIndex, setPageIndex] = useState(0)
  const normalisedQuery = query.trim().toLowerCase()
  const filteredColumns = useMemo(() => {
    if (!normalisedQuery) return report.columns
    return report.columns.filter((column) => {
      const name = column.name.toLowerCase()
      const dtype = column.dtype.toLowerCase()
      const example = column.example_value?.toLowerCase() ?? ""
      return name.includes(normalisedQuery) || dtype.includes(normalisedQuery) || example.includes(normalisedQuery)
    })
  }, [normalisedQuery, report.columns])
  const pageCount = Math.max(Math.ceil(filteredColumns.length / SCHEMA_PAGE_SIZE), 1)
  const currentPageIndex = Math.min(pageIndex, pageCount - 1)
  const firstVisibleIndex = currentPageIndex * SCHEMA_PAGE_SIZE
  const visibleColumns = filteredColumns.slice(firstVisibleIndex, firstVisibleIndex + SCHEMA_PAGE_SIZE)
  const rangeStart = filteredColumns.length === 0 ? 0 : firstVisibleIndex + 1
  const rangeEnd = firstVisibleIndex + visibleColumns.length
  const summarySuffix = `${normalisedQuery ? "matching " : ""}${filteredColumns.length === 1 ? "column" : "columns"}`
  const hasMultiplePages = filteredColumns.length > SCHEMA_PAGE_SIZE

  const handleQueryChange = (value: string) => {
    setQuery(value)
    setPageIndex(0)
  }

  return (
    <div
      data-testid="explore-schema-table-card"
      className="rounded-lg p-3 space-y-3"
      style={{
        background: "var(--bg-elevated)",
        border: "1px solid var(--border)",
      }}
    >
      <div className="flex items-center gap-1.5">
        <Columns size={14} className="shrink-0" style={{ color: accent }} />
        <span
          id="explore-schema-card-heading"
          className="text-[11px] font-bold"
          style={{ color: accent }}
        >
          Schema
        </span>
        <span className="text-[11px]" style={MUTED_STYLE}>
          {columnCount.toLocaleString()} columns
        </span>
      </div>

      {columnCount > SCHEMA_PAGE_SIZE && (
        <div className="flex flex-wrap items-center gap-2" data-testid="explore-schema-controls">
          <label
            className="focus-within:brightness-110 flex min-w-[180px] flex-1 items-center gap-1.5 rounded-md px-2 py-1.5"
            style={{ background: "var(--bg-input)", border: "1px solid var(--border)" }}
          >
            <Search size={12} className="shrink-0" style={MUTED_STYLE} />
            <input
              aria-label="Search schema columns"
              type="search"
              value={query}
              onChange={(event) => handleQueryChange(event.target.value)}
              className="min-w-0 flex-1 bg-transparent text-[11px] outline-none"
              style={PRIMARY_STYLE}
            />
          </label>
          <span className="text-[11px] tabular-nums" style={MUTED_STYLE}>
            Showing {rangeStart.toLocaleString()}-{rangeEnd.toLocaleString()} of{" "}
            {filteredColumns.length.toLocaleString()} {summarySuffix}
          </span>
          {hasMultiplePages && (
            <div className="flex items-center gap-1">
              <button
                type="button"
                aria-label="Previous schema columns"
                title="Previous"
                disabled={currentPageIndex === 0}
                onClick={() => setPageIndex((current) => Math.max(current - 1, 0))}
                className="inline-flex h-6 w-6 items-center justify-center rounded disabled:opacity-40 disabled:cursor-not-allowed"
                style={{ background: "var(--bg-input)", border: "1px solid var(--border)", color: "var(--text-primary)" }}
              >
                <ChevronLeft size={13} />
              </button>
              <button
                type="button"
                aria-label="Next schema columns"
                title="Next"
                disabled={currentPageIndex >= pageCount - 1}
                onClick={() => setPageIndex((current) => Math.min(current + 1, pageCount - 1))}
                className="inline-flex h-6 w-6 items-center justify-center rounded disabled:opacity-40 disabled:cursor-not-allowed"
                style={{ background: "var(--bg-input)", border: "1px solid var(--border)", color: "var(--text-primary)" }}
              >
                <ChevronRight size={13} />
              </button>
            </div>
          )}
        </div>
      )}

      <div className="overflow-y-auto" style={{ maxHeight: 400 }}>
        <table className="w-full text-[11px]" aria-labelledby="explore-schema-card-heading">
          <thead>
            <tr>
              <th className={HEADER_CLASS} style={HEADER_STYLE}>
                Name
              </th>
              <th className={HEADER_CLASS} style={HEADER_STYLE}>
                Type
              </th>
              <th className={HEADER_CLASS} style={HEADER_STYLE}>
                Null %
              </th>
              <th className={HEADER_CLASS} style={HEADER_STYLE}>
                Distinct
              </th>
              <th className={HEADER_CLASS} style={HEADER_STYLE}>
                Example
              </th>
            </tr>
          </thead>
          <tbody>
            {report.columns.length === 0 ? (
              <tr>
                <td
                  colSpan={5}
                  className={CELL_BASE_CLASS}
                  style={MUTED_STYLE}
                  data-testid="explore-schema-empty"
                >
                  (no columns)
                </td>
              </tr>
            ) : visibleColumns.length === 0 ? (
              <tr>
                <td
                  colSpan={5}
                  className={CELL_BASE_CLASS}
                  style={MUTED_STYLE}
                  data-testid="explore-schema-empty"
                >
                  (no matching columns)
                </td>
              </tr>
            ) : (
              visibleColumns.map((column, index) => (
                <SchemaRow
                  key={`${column.name}:${firstVisibleIndex + index}`}
                  column={column}
                  rowCount={report.row_count}
                />
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}
