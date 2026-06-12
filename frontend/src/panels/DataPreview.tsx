import { memo, useState, useCallback, useRef, useEffect, useMemo, type MouseEvent } from "react"
import { X, AlertCircle, CheckCircle2, Table2, Search } from "lucide-react"
import { getDtypeColor } from "../utils/dtypeColors"
import { formatValue } from "../utils/formatValue"
import ExecutionDiagnosticsSummary from "../components/ExecutionDiagnosticsSummary"
import type { ColumnInfo } from "../types/node"
import type { SchemaWarning, NodeTiming, NodeMemory, ExecutionMetrics } from "../api/types"
import PreviewPanelFrame from "./PreviewPanelFrame"
import { DEFAULT_PREVIEW_PANEL_DIMENSIONS } from "./previewPanelLayout"
import {
  buildColumnOffsets,
  getColumnWindowVariable,
  clampColumnWidth,
  ROW_NUMBER_WIDTH,
} from "./dataPreviewColumns"
import useUIStore from "../stores/useUIStore"

export interface PreviewData {
  nodeId: string
  nodeLabel: string
  status: "ok" | "error" | "loading"
  row_count: number
  column_count: number
  columns: ColumnInfo[]
  preview: Record<string, unknown>[]
  preview_columns?: string[]
  preview_row_count?: number
  preview_row_limit?: number | null
  preview_truncated?: boolean
  error: string | null
  error_line?: number | null
  timing_ms?: number
  memory_bytes?: number
  timings?: NodeTiming[]
  memory?: NodeMemory[]
  schema_warnings?: SchemaWarning[]
  execution_metrics?: ExecutionMetrics | null
}

interface DataPreviewProps {
  data: PreviewData | null
  onCellClick?: (rowIndex: number, column: string, rowValues?: Record<string, unknown>) => void
  tracedCell?: { rowIndex: number; column: string } | null
  embedded?: boolean
  nodeType?: string | null
}


const ROW_HEIGHT = 28
const VIRTUALIZE_THRESHOLD = 50
const OVERSCAN = 10
const MAX_COLUMN_WIDTH = 160
const MID_COLUMN_WIDTH = 140
const MIN_COLUMN_WIDTH = 120
const FALLBACK_VIEW_WIDTH = 960
const FALLBACK_VIEW_HEIGHT = DEFAULT_PREVIEW_PANEL_DIMENSIONS.initialHeight
const NULL_VALUE_STYLE = { color: 'var(--text-muted)', fontStyle: 'italic' }
const EMPTY_COLUMNS: ColumnInfo[] = []
const EMPTY_COLUMN_WIDTHS: Record<string, number> = {}
// A drag of less than this many px total movement commits nothing (treat as
// an accidental click on the handle).
const RESIZE_COMMIT_THRESHOLD_PX = 3

type ColumnSearchEntry = {
  column: ColumnInfo
  normalizedName: string
}

function normalizeColumnSearch(value: string): string {
  return value.trim().toLowerCase()
}

function buildColumnSearchIndex(columns: ColumnInfo[]): ColumnSearchEntry[] {
  return columns.map((column) => ({
    column,
    normalizedName: column.name.toLowerCase(),
  }))
}

function filterColumnsBySearchIndex(index: ColumnSearchEntry[], normalizedQuery: string): ColumnInfo[] {
  const matches: ColumnInfo[] = []
  for (const entry of index) {
    if (entry.normalizedName.includes(normalizedQuery)) matches.push(entry.column)
  }
  return matches
}

function responsiveColumnWidth(viewWidth: number): number {
  if (viewWidth < 720) return MIN_COLUMN_WIDTH
  if (viewWidth < 900) return MID_COLUMN_WIDTH
  return MAX_COLUMN_WIDTH
}

/**
 * Force the col-resize cursor and suppress text selection for the duration
 * of a header drag. Returns a restore function for mouseup/unmount.
 * (Module-level on purpose: document mutation is out of bounds inside
 * render-created closures under the React compiler's immutability rule.)
 */
function setBodyDragCursor(): () => void {
  const previousCursor = document.body.style.cursor
  const previousUserSelect = document.body.style.userSelect
  document.body.style.cursor = "col-resize"
  document.body.style.userSelect = "none"
  return () => {
    document.body.style.cursor = previousCursor
    document.body.style.userSelect = previousUserSelect
  }
}

type DataCellProps = {
  rowIndex: number
  column: string
  value: unknown
  isTraced: boolean
  clickable: boolean
  columnWidth: number
}

const DataCell = memo(function DataCell({
  rowIndex,
  column,
  value,
  isTraced,
  clickable,
  columnWidth,
}: DataCellProps) {
  return (
    <td
      data-row-index={rowIndex}
      data-column={column}
      className="px-3 py-1 font-mono whitespace-nowrap truncate transition-colors"
      style={{
        color: 'var(--text-secondary)',
        cursor: clickable ? 'pointer' : undefined,
        background: isTraced ? 'var(--accent-soft)' : undefined,
        boxShadow: isTraced ? 'inset 0 0 0 1.5px var(--accent)' : undefined,
        borderRadius: isTraced ? '3px' : undefined,
        width: columnWidth,
        minWidth: columnWidth,
        maxWidth: columnWidth,
      }}
    >
      <span style={value === null ? NULL_VALUE_STYLE : undefined}>
        {formatValue(value)}
      </span>
    </td>
  )
})

export default function DataPreview({ data, onCellClick, tracedCell, embedded = false, nodeType }: DataPreviewProps) {
  const [columnSearch, setColumnSearch] = useState("")

  // Clear search when selected node changes
  const nodeId = data?.nodeId
  // eslint-disable-next-line react-hooks/set-state-in-effect -- derived state reset: clear column search when user selects a different node
  useEffect(() => { setColumnSearch("") }, [nodeId])

  const schemaColumns = data?.columns ?? EMPTY_COLUMNS
  const previewColumnNames = data?.preview_columns
  const columns = useMemo(() => {
    if (!previewColumnNames || previewColumnNames.length === 0) return schemaColumns
    const schemaByName = new Map(schemaColumns.map((column) => [column.name, column]))
    return previewColumnNames.map((name) => schemaByName.get(name)).filter((column): column is ColumnInfo => !!column)
  }, [previewColumnNames, schemaColumns])
  const columnSearchIndex = useMemo(() => buildColumnSearchIndex(columns), [columns])
  const normalizedColumnSearch = useMemo(() => normalizeColumnSearch(columnSearch), [columnSearch])
  const filteredColumns = useMemo(() => {
    if (!normalizedColumnSearch) return columns
    return filterColumnsBySearchIndex(columnSearchIndex, normalizedColumnSearch)
  }, [columns, columnSearchIndex, normalizedColumnSearch])
  // Virtual scrolling state
  const scrollRef = useRef<HTMLDivElement>(null)
  const [scrollElement, setScrollElement] = useState<HTMLDivElement | null>(null)
  const [scrollTop, setScrollTop] = useState(0)
  const [scrollLeft, setScrollLeft] = useState(0)
  const [viewHeight, setViewHeight] = useState(0)
  const [viewWidth, setViewWidth] = useState(0)
  const rafRef = useRef(0)

  // ── Column resize ──────────────────────────────────────────────────────
  // Per-node, per-column width overrides (px) — session-only view state in
  // useUIStore, never persisted into the graph or save payload.
  const columnWidthOverrides = useUIStore(
    (s) => (nodeId ? s.previewColumnWidths[nodeId] : undefined) ?? EMPTY_COLUMN_WIDTHS,
  )
  const setPreviewColumnWidth = useUIStore((s) => s.setPreviewColumnWidth)
  const clearPreviewColumnWidth = useUIStore((s) => s.clearPreviewColumnWidth)
  // Live width while a handle is being dragged (rAF-batched). Committed to
  // the store on mouseup; null when no drag is active.
  const [dragWidth, setDragWidth] = useState<{ column: string; width: number } | null>(null)
  const resizeCleanupRef = useRef<(() => void) | null>(null)
  // Abort any in-flight drag on unmount (removes document listeners).
  useEffect(() => {
    return () => {
      resizeCleanupRef.current?.()
    }
  }, [])

  const effectiveViewWidth = viewWidth || FALLBACK_VIEW_WIDTH
  const defaultColumnWidth = responsiveColumnWidth(effectiveViewWidth)
  const effectiveColumnWidth = (name: string): number => {
    if (dragWidth !== null && dragWidth.column === name) return dragWidth.width
    return columnWidthOverrides[name] ?? defaultColumnWidth
  }

  // Note: the default width is derived inside the memo from the primitive
  // `viewWidth` state (not from `defaultColumnWidth` above) so the React
  // compiler can preserve this manual memoization.
  const columnOffsets = useMemo(
    () =>
      buildColumnOffsets(
        filteredColumns,
        responsiveColumnWidth(viewWidth || FALLBACK_VIEW_WIDTH),
        columnWidthOverrides,
        dragWidth,
      ),
    [filteredColumns, viewWidth, columnWidthOverrides, dragWidth],
  )

  const handleResizeMouseDown = (e: MouseEvent<HTMLDivElement>) => {
    const column = e.currentTarget.dataset.column
    if (!column || !nodeId) return
    e.preventDefault()
    e.stopPropagation()
    const startX = e.clientX
    const startWidth = columnWidthOverrides[column] ?? defaultColumnWidth
    let latestWidth = startWidth
    let moved = false
    let raf = 0
    const restoreBodyStyles = setBodyDragCursor()

    const onMove = (ev: globalThis.MouseEvent) => {
      const deltaX = ev.clientX - startX
      if (Math.abs(deltaX) >= RESIZE_COMMIT_THRESHOLD_PX) moved = true
      latestWidth = clampColumnWidth(startWidth + deltaX)
      // rAF-batched live update (same pattern as handleTableScroll): the
      // column visibly tracks the cursor; React re-derives the window since
      // every later column's spacer offsets shift.
      cancelAnimationFrame(raf)
      raf = requestAnimationFrame(() => setDragWidth({ column, width: latestWidth }))
    }
    const finish = (commit: boolean) => {
      cancelAnimationFrame(raf)
      document.removeEventListener("mousemove", onMove)
      document.removeEventListener("mouseup", onUp)
      restoreBodyStyles()
      resizeCleanupRef.current = null
      if (commit && moved) setPreviewColumnWidth(nodeId, column, latestWidth)
      setDragWidth(null)
    }
    const onUp = () => finish(true)
    document.addEventListener("mousemove", onMove)
    document.addEventListener("mouseup", onUp)
    resizeCleanupRef.current = () => finish(false)
  }

  const handleResizeDoubleClick = (e: MouseEvent<HTMLDivElement>) => {
    const column = e.currentTarget.dataset.column
    if (!column || !nodeId) return
    e.preventDefault()
    e.stopPropagation()
    clearPreviewColumnWidth(nodeId, column)
  }

  const setScrollContainer = useCallback((node: HTMLDivElement | null) => {
    scrollRef.current = node
    setScrollElement(node)
    if (node) {
      setViewHeight(node.clientHeight)
      setViewWidth(node.clientWidth)
    }
  }, [])

  const handleTableScroll = useCallback(() => {
    const el = scrollRef.current
    if (!el) return
    const nextScrollTop = el.scrollTop
    const nextScrollLeft = el.scrollLeft
    cancelAnimationFrame(rafRef.current)
    rafRef.current = requestAnimationFrame(() => {
      setScrollTop(nextScrollTop)
      setScrollLeft(nextScrollLeft)
    })
  }, [])

  const handleCellClick = useCallback((event: MouseEvent<HTMLTableSectionElement>) => {
    if (!data || !onCellClick) return
    const target = event.target instanceof HTMLElement ? event.target : null
    const cell = target?.closest<HTMLTableCellElement>("td[data-row-index][data-column]")
    if (!cell || !event.currentTarget.contains(cell)) return

    const rowIndex = Number(cell.dataset.rowIndex)
    const column = cell.dataset.column
    if (!Number.isInteger(rowIndex) || !column) return

    const row = data.preview[rowIndex]
    if (!row) return
    onCellClick(rowIndex, column, row as Record<string, unknown>)
  }, [data, onCellClick])

  useEffect(() => {
    const el = scrollElement
    if (!el) return
    const observer = new ResizeObserver(([entry]) => {
      setViewHeight(entry.contentRect.height)
      setViewWidth(entry.contentRect.width)
    })
    observer.observe(el)
    return () => observer.disconnect()
  }, [scrollElement])

  // Cancel in-flight RAF on unmount
  useEffect(() => {
    const ref = rafRef
    return () => cancelAnimationFrame(ref.current)
  }, [])

  if (!data) return null

  const returnedRows = data.preview_row_count ?? data.preview.length
  const previewLimit = data.preview_row_limit ?? returnedRows
  const showPreviewFooter = data.row_count > returnedRows || data.preview_truncated
  const columnSearchControl = (
    <div className="flex items-center gap-1 px-1.5 py-0.5 rounded-md" style={{ background: 'var(--chrome-hover)', border: '1px solid var(--chrome-border)' }}>
      <Search size={11} style={{ color: 'var(--text-muted)' }} />
      <input
        type="text"
        value={columnSearch}
        onChange={(e) => setColumnSearch(e.target.value)}
        placeholder="Search columns..."
        className="w-28 text-[11px] font-mono bg-transparent focus:outline-none"
        style={{ color: 'var(--text-primary)' }}
      />
      {columnSearch && (
        <button onClick={() => setColumnSearch("")} className="shrink-0" style={{ color: 'var(--text-muted)' }}>
          <X size={10} />
        </button>
      )}
    </div>
  )
  const previewContent = data.status === "loading" ? (
    <div className="flex-1 flex items-center justify-center">
      <div className="text-xs animate-pulse" style={{ color: 'var(--text-muted)' }}>Executing pipeline...</div>
    </div>
  ) : data.status === "error" ? (
    <div className="flex-1 flex items-center justify-center p-4">
      <div className="text-center">
        <AlertCircle size={24} className="mx-auto mb-2" style={{ color: 'var(--danger)', opacity: 0.5 }} />
        <div className="text-xs max-w-md" style={{ color: 'var(--danger)' }}>{data.error}</div>
      </div>
    </div>
  ) : (
    <div ref={setScrollContainer} data-testid="data-preview-scroll" className="flex-1 overflow-auto" onScroll={handleTableScroll}>
      {(() => {
        const totalRows = data.preview.length
        const effectiveViewHeight = viewHeight || Math.max(ROW_HEIGHT, FALLBACK_VIEW_HEIGHT - 64)
        const shouldVirtualize = totalRows > VIRTUALIZE_THRESHOLD
        let startIdx = 0
        let endIdx = totalRows
        if (shouldVirtualize) {
          startIdx = Math.max(0, Math.floor(scrollTop / ROW_HEIGHT) - OVERSCAN)
          endIdx = Math.min(totalRows, Math.ceil((scrollTop + effectiveViewHeight) / ROW_HEIGHT) + OVERSCAN)
        }
        const topPad = startIdx * ROW_HEIGHT
        const bottomPad = (totalRows - endIdx) * ROW_HEIGHT
        const columnWindow = getColumnWindowVariable(columnOffsets, filteredColumns.length, scrollLeft, effectiveViewWidth)
        const visibleColumns = filteredColumns.slice(columnWindow.startIdx, columnWindow.endIdx)
        const renderedColumnSlots =
          1 +
          (columnWindow.leftPad > 0 ? 1 : 0) +
          visibleColumns.length +
          (columnWindow.rightPad > 0 ? 1 : 0)
        const spacerStyle = { padding: 0, borderBottom: '1px solid var(--border)' }

        return (
          <table className="text-xs table-fixed" style={{ width: columnWindow.totalWidth }}>
            <thead className="sticky top-0 z-10" style={{ background: 'var(--bg-elevated)' }}>
              <tr>
                <th className="px-3 py-1.5 text-left text-[11px] font-semibold uppercase tracking-wider"
                  style={{ color: 'var(--text-muted)', borderBottom: '1px solid var(--border)', width: ROW_NUMBER_WIDTH, minWidth: ROW_NUMBER_WIDTH }}>
                  #
                </th>
                {columnWindow.leftPad > 0 && (
                  <th aria-hidden="true" style={{ ...spacerStyle, width: columnWindow.leftPad, minWidth: columnWindow.leftPad }} />
                )}
                {visibleColumns.map((col) => {
                  const colWidth = effectiveColumnWidth(col.name)
                  const isDraggingCol = dragWidth?.column === col.name
                  return (
                    <th
                      key={col.name}
                      className="relative px-3 py-1.5 text-left whitespace-nowrap"
                      style={{ borderBottom: '1px solid var(--border)', width: colWidth, minWidth: colWidth, maxWidth: colWidth }}
                    >
                      {/* overflow-hidden lives on this inner wrapper (not the
                          <th>) so the resize handle's 4px overhang isn't clipped */}
                      <div className="overflow-hidden">
                        <div className="font-semibold truncate" style={{ color: 'var(--text-primary)' }}>{col.name}</div>
                        <div className={`text-[11px] font-normal ${getDtypeColor(col.dtype)}`}>
                          {col.dtype}
                        </div>
                      </div>
                      <div
                        data-testid={`data-preview-col-resize-${col.name}`}
                        data-column={col.name}
                        title={"Drag to resize · double-click to reset"}
                        onMouseDown={handleResizeMouseDown}
                        onDoubleClick={handleResizeDoubleClick}
                        className="absolute top-0 -right-[4px] w-[9px] h-full cursor-col-resize select-none z-20 group/resize"
                      >
                        <div
                          className={`w-[2px] h-full mx-auto transition-opacity ${isDraggingCol ? "opacity-100" : "opacity-0 group-hover/resize:opacity-60"}`}
                          style={{ background: 'var(--accent)' }}
                        />
                      </div>
                    </th>
                  )
                })}
                {columnWindow.rightPad > 0 && (
                  <th aria-hidden="true" style={{ ...spacerStyle, width: columnWindow.rightPad, minWidth: columnWindow.rightPad }} />
                )}
              </tr>
            </thead>
            <tbody onClick={handleCellClick}>
              {topPad > 0 && (
                <tr style={{ height: topPad }}>
                  <td colSpan={renderedColumnSlots} style={{ padding: 0 }} />
                </tr>
              )}
              {data.preview.slice(startIdx, endIdx).map((row, vi) => {
                const i = startIdx + vi
                return (
                  <tr
                    key={i}
                    style={{ height: ROW_HEIGHT, background: i % 2 === 0 ? 'transparent' : 'rgba(255,255,255,.02)' }}
                  >
                    <td className="px-3 py-1 font-mono" style={{ color: 'var(--text-muted)', borderRight: '1px solid var(--border)', width: ROW_NUMBER_WIDTH, minWidth: ROW_NUMBER_WIDTH }}>
                      {i + 1}
                    </td>
                    {columnWindow.leftPad > 0 && (
                      <td aria-hidden="true" style={{ padding: 0, width: columnWindow.leftPad, minWidth: columnWindow.leftPad }} />
                    )}
                    {visibleColumns.map((col) => (
                      <DataCell
                        key={col.name}
                        rowIndex={i}
                        column={col.name}
                        value={row[col.name]}
                        isTraced={tracedCell?.rowIndex === i && tracedCell?.column === col.name}
                        clickable={!!onCellClick}
                        columnWidth={effectiveColumnWidth(col.name)}
                      />
                    ))}
                    {columnWindow.rightPad > 0 && (
                      <td aria-hidden="true" style={{ padding: 0, width: columnWindow.rightPad, minWidth: columnWindow.rightPad }} />
                    )}
                  </tr>
                )
              })}
              {bottomPad > 0 && (
                <tr style={{ height: bottomPad }}>
                  <td colSpan={renderedColumnSlots} style={{ padding: 0 }} />
                </tr>
              )}
            </tbody>
          </table>
        )
      })()}

      {showPreviewFooter && (
        <div className="px-3 py-1.5 text-[11px] text-center" style={{ color: 'var(--text-muted)', borderTop: '1px solid var(--border)', background: 'var(--bg-elevated)' }}>
          Showing {returnedRows.toLocaleString()} of {data.row_count.toLocaleString()} rows
          {data.preview_truncated && (
            <span>{" · capped at "}{previewLimit.toLocaleString()}</span>
          )}
        </div>
      )}
    </div>
  )

  const previewSection = (
    <>
      <div className="min-h-9 flex items-center flex-wrap px-3 shrink-0 gap-x-2 gap-y-1 py-1.5" style={{ borderBottom: '1px solid var(--border)', background: 'var(--bg-elevated)' }}>
        {/* In embedded mode the outer PreviewPanelFrame already shows the node label/icon,
            so we drop the redundant "Preview" title and just keep the additive count + search. */}
        {!embedded && (
          <>
            <Table2 size={14} style={{ color: 'var(--text-muted)' }} />
            <span className="text-xs font-bold" style={{ color: 'var(--text-primary)' }}>Preview</span>
          </>
        )}
        {data.status === "ok" && (
          <>
            <CheckCircle2 size={13} className={embedded ? undefined : "ml-1"} style={{ color: 'var(--success)' }} />
            <span className="text-[11px]" style={{ color: 'var(--text-muted)' }}>
              {data.row_count.toLocaleString()} rows{" · "}{data.column_count} cols
            </span>
          </>
        )}
        {data.status === "error" && (
          <>
            <AlertCircle size={13} className={embedded ? undefined : "ml-1"} style={{ color: 'var(--danger)' }} />
            <span className="text-[11px] truncate" style={{ color: 'var(--danger)' }}>{data.error}</span>
          </>
        )}
        {data.status === "loading" && (
          <span className="text-[11px] animate-pulse" style={{ color: 'var(--text-muted)' }}>Running...</span>
        )}
        <div className="ml-auto flex items-center gap-1.5">
          {columnSearchControl}
        </div>
      </div>
      <ExecutionDiagnosticsSummary metrics={data.execution_metrics} />
      {previewContent}
    </>
  )

  if (embedded) {
    return (
      <div className="flex-1 min-h-0 flex flex-col" data-testid="data-preview-embedded">
        {previewSection}
      </div>
    )
  }

  return (
    <PreviewPanelFrame
      nodeLabel={data.nodeLabel}
      nodeType={nodeType}
      collapsedMeta={data.status === "ok" ? `${data.row_count.toLocaleString()} rows · ${data.column_count} cols` : undefined}
    >
      {previewSection}
    </PreviewPanelFrame>
  )
}
