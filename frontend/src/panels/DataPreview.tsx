import { memo, useState, useCallback, useRef, useEffect, useMemo, type MouseEvent } from "react"
import { X, AlertCircle, CheckCircle2, Table2, Search, Layers } from "lucide-react"
import { getDtypeColor } from "../utils/dtypeColors"
import { formatValue } from "../utils/formatValue"
import ExecutionDiagnosticsSummary from "../components/ExecutionDiagnosticsSummary"
import type { ColumnInfo } from "../types/node"
import type { SchemaWarning, NodeTiming, NodeMemory, ExecutionMetrics } from "../api/types"
import PreviewPanelFrame from "./PreviewPanelFrame"
import { DEFAULT_PREVIEW_PANEL_DIMENSIONS } from "./previewPanelLayout"

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
  /** Per-frame column schema for a multi-frame producer (a multi-table
   * apiInput), keyed by emit-table label. 2+ entries drive the frame-select
   * dropdown; empty/single-entry for single-frame nodes (no dropdown). */
  frame_columns?: Record<string, ColumnInfo[]>
  /** The frame label currently shown. `undefined` = the first frame (the
   * default). Drives the dropdown's selected value. */
  selected_frame?: string
}

interface DataPreviewProps {
  data: PreviewData | null
  onCellClick?: (rowIndex: number, column: string, rowValues?: Record<string, unknown>) => void
  tracedCell?: { rowIndex: number; column: string } | null
  embedded?: boolean
  nodeType?: string | null
  /** Re-request the preview for a specific frame of a multi-frame node. When
   * provided AND the node carries 2+ frames, the top-bar shows a frame-select
   * dropdown. Omitted (or single-frame node) → no dropdown, unchanged UI. */
  onSelectFrame?: (portLabel: string) => void
}


const ROW_HEIGHT = 28
const VIRTUALIZE_THRESHOLD = 50
const OVERSCAN = 10
const ROW_NUMBER_WIDTH = 48
const MAX_COLUMN_WIDTH = 160
const MID_COLUMN_WIDTH = 140
const MIN_COLUMN_WIDTH = 120
const COLUMN_OVERSCAN = 3
const FALLBACK_VIEW_WIDTH = 960
const FALLBACK_VIEW_HEIGHT = DEFAULT_PREVIEW_PANEL_DIMENSIONS.initialHeight
const NULL_VALUE_STYLE = { color: 'var(--text-muted)', fontStyle: 'italic' }
const EMPTY_COLUMNS: ColumnInfo[] = []
const EMPTY_FRAME_LABELS: string[] = []

type ColumnWindow = {
  startIdx: number
  endIdx: number
  leftPad: number
  rightPad: number
  totalWidth: number
}

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

function getColumnWindow(
  columnCount: number,
  scrollLeft: number,
  viewWidth: number,
  columnWidth: number,
): ColumnWindow {
  const totalColumnWidth = columnCount * columnWidth
  const totalWidth = ROW_NUMBER_WIDTH + totalColumnWidth
  const visibleWidth = Math.max(columnWidth, viewWidth - ROW_NUMBER_WIDTH)
  const visibleColumnCount = Math.ceil(visibleWidth / columnWidth)
  const shouldVirtualizeColumns = columnCount > visibleColumnCount + COLUMN_OVERSCAN * 2
  if (!shouldVirtualizeColumns) {
    return {
      startIdx: 0,
      endIdx: columnCount,
      leftPad: 0,
      rightPad: 0,
      totalWidth,
    }
  }

  const dataScrollLeft = Math.max(0, scrollLeft - ROW_NUMBER_WIDTH)
  const windowSize = Math.min(columnCount, visibleColumnCount + COLUMN_OVERSCAN * 2)
  const rawStart = Math.floor(dataScrollLeft / columnWidth)
  const maxStartIdx = Math.max(0, columnCount - windowSize)
  const startIdx = Math.min(maxStartIdx, Math.max(0, rawStart - COLUMN_OVERSCAN))
  const endIdx = Math.min(columnCount, startIdx + windowSize)

  return {
    startIdx,
    endIdx,
    leftPad: startIdx * columnWidth,
    rightPad: Math.max(0, (columnCount - endIdx) * columnWidth),
    totalWidth,
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

export default function DataPreview({ data, onCellClick, tracedCell, embedded = false, nodeType, onSelectFrame }: DataPreviewProps) {
  const [columnSearch, setColumnSearch] = useState("")

  // Frame labels for a multi-frame producer (a multi-table apiInput). The
  // dropdown renders only with 2+ frames and a handler; single-frame /
  // ordinary nodes keep the unchanged top-bar. Object key order is the
  // emit-table order the backend produced, so the first label is the default
  // (first-frame) selection.
  const frameColumns = data?.frame_columns
  const frameLabels = useMemo(
    () => (frameColumns ? Object.keys(frameColumns) : EMPTY_FRAME_LABELS),
    [frameColumns],
  )
  const showFrameSelect = !!onSelectFrame && frameLabels.length >= 2
  const selectedFrame = data?.selected_frame ?? frameLabels[0]

  // Clear search when selected node changes
  const nodeId = data?.nodeId
  // eslint-disable-next-line react-hooks/set-state-in-effect -- derived state reset: clear column search when user selects a different node
  useEffect(() => { setColumnSearch("") }, [nodeId])

  const schemaColumns = data?.columns ?? EMPTY_COLUMNS
  const previewColumnNames = data?.preview_columns
  // A multi-frame producer reports an EMPTY flat `columns` by design — it has no
  // single representative schema; the selected frame's schema (names + dtypes)
  // lives in `frame_columns[selectedFrame]`. Use that as a dtype source so the
  // preview renders the frame's columns. Without it, the `preview_columns` ∩
  // `schemaColumns` join below is empty for a multi-frame node and the table
  // shows row numbers with no columns.
  const frameSchemaColumns =
    (selectedFrame ? data?.frame_columns?.[selectedFrame] : undefined) ?? EMPTY_COLUMNS
  const columns = useMemo(() => {
    if (!previewColumnNames || previewColumnNames.length === 0) {
      return schemaColumns.length > 0 ? schemaColumns : frameSchemaColumns
    }
    // Join `preview_columns` (which columns, in order) against both schema
    // sources for dtypes; the flat `columns` wins where present (single-frame),
    // the frame schema fills in for multi-frame. Never DROP a previewed column:
    // if neither source carries its dtype, render it with an unknown dtype
    // rather than vanishing (the bug that made multi-frame previews show only
    // row numbers).
    const schemaByName = new Map(
      [...frameSchemaColumns, ...schemaColumns].map((column) => [column.name, column]),
    )
    return previewColumnNames.map((name) => schemaByName.get(name) ?? { name, dtype: "" })
  }, [previewColumnNames, schemaColumns, frameSchemaColumns])
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
  // Frame-select dropdown for a multi-frame producer. Renders only when the
  // node carries 2+ frames AND a handler is wired (canvas preview). Selecting
  // a frame re-requests the preview with that frame's port_label.
  const frameSelectControl = showFrameSelect ? (
    <div
      className="flex items-center gap-1 px-1.5 py-0.5 rounded-md"
      style={{ background: 'var(--chrome-hover)', border: '1px solid var(--chrome-border)' }}
      data-testid="data-preview-frame-select"
    >
      <Layers size={11} style={{ color: 'var(--text-muted)' }} />
      <select
        value={selectedFrame}
        onChange={(e) => onSelectFrame?.(e.target.value)}
        aria-label="Select frame to preview"
        className="text-[11px] font-mono bg-transparent focus:outline-none cursor-pointer"
        style={{ color: 'var(--text-primary)' }}
      >
        {frameLabels.map((frameName) => (
          <option key={frameName} value={frameName} style={{ background: 'var(--bg-elevated)', color: 'var(--text-primary)' }}>
            {frameName}
          </option>
        ))}
      </select>
    </div>
  ) : null
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
        const effectiveViewWidth = viewWidth || FALLBACK_VIEW_WIDTH
        const columnWidth = responsiveColumnWidth(effectiveViewWidth)
        const shouldVirtualize = totalRows > VIRTUALIZE_THRESHOLD
        let startIdx = 0
        let endIdx = totalRows
        if (shouldVirtualize) {
          startIdx = Math.max(0, Math.floor(scrollTop / ROW_HEIGHT) - OVERSCAN)
          endIdx = Math.min(totalRows, Math.ceil((scrollTop + effectiveViewHeight) / ROW_HEIGHT) + OVERSCAN)
        }
        const topPad = startIdx * ROW_HEIGHT
        const bottomPad = (totalRows - endIdx) * ROW_HEIGHT
        const columnWindow = getColumnWindow(filteredColumns.length, scrollLeft, effectiveViewWidth, columnWidth)
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
                {visibleColumns.map((col) => (
                  <th
                    key={col.name}
                    className="px-3 py-1.5 text-left whitespace-nowrap overflow-hidden"
                    style={{ borderBottom: '1px solid var(--border)', width: columnWidth, minWidth: columnWidth, maxWidth: columnWidth }}
                  >
                    <div className="font-semibold truncate" style={{ color: 'var(--text-primary)' }}>{col.name}</div>
                    <div className={`text-[11px] font-normal ${getDtypeColor(col.dtype)}`}>
                      {col.dtype}
                    </div>
                  </th>
                ))}
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
                        columnWidth={columnWidth}
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
            <span>{" \u00b7 capped at "}{previewLimit.toLocaleString()}</span>
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
              {data.row_count.toLocaleString()} rows{" \u00b7 "}{data.column_count || columns.length} cols
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
          {/* In embedded mode there is no PreviewPanelFrame header to carry
              the dropdown, so it lives here beside the column search; in the
              framed (canvas) case it sits in the frame's actions slot beside
              expand/collapse instead (see below). */}
          {embedded && frameSelectControl}
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
      actions={frameSelectControl}
      collapsedMeta={data.status === "ok" ? `${data.row_count.toLocaleString()} rows \u00b7 ${data.column_count || columns.length} cols` : undefined}
    >
      {previewSection}
    </PreviewPanelFrame>
  )
}
