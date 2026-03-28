import { useState, useCallback, useRef, useEffect, useMemo } from "react"
import { X, ChevronDown, ChevronUp, AlertCircle, CheckCircle2, Table2, Search } from "lucide-react"
import { getDtypeColor } from "../utils/dtypeColors"
import { formatValue } from "../utils/formatValue"
import { useDragResize } from "../hooks/useDragResize"
import type { ColumnInfo } from "../types/node"
import type { SchemaWarning, NodeTiming, NodeMemory } from "../api/types"

export interface PreviewData {
  nodeId: string
  nodeLabel: string
  status: "ok" | "error" | "loading"
  row_count: number
  column_count: number
  columns: ColumnInfo[]
  preview: Record<string, unknown>[]
  error: string | null
  error_line?: number | null
  timing_ms?: number
  memory_bytes?: number
  timings?: NodeTiming[]
  memory?: NodeMemory[]
  schema_warnings?: SchemaWarning[]
}

interface DataPreviewProps {
  data: PreviewData | null
  onCellClick?: (rowIndex: number, column: string, rowValues?: Record<string, unknown>) => void
  tracedCell?: { rowIndex: number; column: string } | null
}


const ROW_HEIGHT = 28
const VIRTUALIZE_THRESHOLD = 50
const OVERSCAN = 10

export default function DataPreview({ data, onCellClick, tracedCell }: DataPreviewProps) {
  const [collapsed, setCollapsed] = useState(false)
  const [columnSearch, setColumnSearch] = useState("")
  const { height, containerRef, onDragStart } = useDragResize({ initialHeight: 256, minHeight: 120, maxHeight: 600 })

  // Clear search when selected node changes
  const nodeId = data?.nodeId
  // eslint-disable-next-line react-hooks/set-state-in-effect -- derived state reset: clear column search when user selects a different node
  useEffect(() => { setColumnSearch("") }, [nodeId])

  const filteredColumns = useMemo(() => {
    if (!data || !columnSearch.trim()) return data?.columns ?? []
    const q = columnSearch.toLowerCase()
    return data.columns.filter((col) => col.name.toLowerCase().includes(q))
  }, [data, columnSearch])
  // Virtual scrolling state
  const scrollRef = useRef<HTMLDivElement>(null)
  const [scrollTop, setScrollTop] = useState(0)
  const [viewHeight, setViewHeight] = useState(0)
  const rafRef = useRef(0)

  const handleTableScroll = useCallback(() => {
    cancelAnimationFrame(rafRef.current)
    rafRef.current = requestAnimationFrame(() => {
      if (scrollRef.current) {
        setScrollTop(scrollRef.current.scrollTop)
      }
    })
  }, [])

  useEffect(() => {
    const el = scrollRef.current
    if (!el) return
    const observer = new ResizeObserver(([entry]) => {
      setViewHeight(entry.contentRect.height)
    })
    observer.observe(el)
    return () => observer.disconnect()
  }, [data])

  // Cancel in-flight RAF on unmount
  useEffect(() => {
    const ref = rafRef
    return () => cancelAnimationFrame(ref.current)
  }, [])

  if (!data) return null

  if (collapsed) {
    return (
      <div className="h-8 flex items-center px-4 shrink-0" style={{ borderTop: '1px solid var(--border)', background: 'var(--bg-panel)' }}>
        <button
          onClick={() => setCollapsed(false)}
          className="flex items-center gap-2 text-xs"
          style={{ color: 'var(--text-secondary)' }}
        >
          <ChevronUp size={14} />
          <Table2 size={14} />
          <span className="font-medium">{data.nodeLabel}</span>
          {data.status === "ok" && (
            <span style={{ color: 'var(--text-muted)' }}>
              {data.row_count.toLocaleString()} rows · {data.column_count} cols
            </span>
          )}
        </button>
      </div>
    )
  }

  return (
    <div ref={containerRef} style={{ height, borderTop: '1px solid var(--border)', background: 'var(--bg-panel)' }} className="flex flex-col shrink-0 relative">
      {/* Drag handle */}
      <div
        onMouseDown={onDragStart}
        className="absolute top-0 left-0 right-0 h-1 cursor-ns-resize z-10 transition-colors"
        style={{ background: 'var(--chrome-border)' }}
        onMouseEnter={(e) => e.currentTarget.style.background = 'var(--accent)'}
        onMouseLeave={(e) => e.currentTarget.style.background = 'var(--chrome-border)'}
      />
      {/* Header bar */}
      <div className="min-h-9 flex items-center flex-wrap px-4 shrink-0 gap-x-2 gap-y-1 py-1.5" style={{ borderBottom: '1px solid var(--border)', background: 'var(--bg-elevated)' }}>
        <Table2 size={14} style={{ color: 'var(--text-muted)' }} />
        <span className="text-xs font-bold" style={{ color: 'var(--text-primary)' }}>{data.nodeLabel}</span>

        {data.status === "ok" && (
          <>
            <CheckCircle2 size={13} className="ml-1" style={{ color: '#22c55e' }} />
            <span className="text-[11px]" style={{ color: 'var(--text-muted)' }}>
              {data.row_count.toLocaleString()} rows · {data.column_count} cols
            </span>
          </>
        )}

        {data.status === "error" && (
          <>
            <AlertCircle size={13} className="ml-1" style={{ color: '#ef4444' }} />
            <span className="text-[11px] truncate" style={{ color: '#ef4444' }}>{data.error}</span>
          </>
        )}

        {data.status === "loading" && (
          <span className="text-[11px] animate-pulse" style={{ color: 'var(--text-muted)' }}>Running...</span>
        )}

        <div className="ml-auto flex items-center gap-1.5">
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
          <button
            onClick={() => setCollapsed(true)}
            className="p-1 rounded transition-colors"
            style={{ color: 'var(--text-muted)' }}
            onMouseEnter={(e) => e.currentTarget.style.background = 'var(--bg-hover)'}
            onMouseLeave={(e) => e.currentTarget.style.background = 'transparent'}
          >
            <ChevronDown size={14} />
          </button>
        </div>
      </div>

      {/* Timing breakdown */}
      {/* Data table */}
      {data.status === "loading" ? (
        <div className="flex-1 flex items-center justify-center">
          <div className="text-xs animate-pulse" style={{ color: 'var(--text-muted)' }}>Executing pipeline...</div>
        </div>
      ) : data.status === "error" ? (
        <div className="flex-1 flex items-center justify-center p-4">
          <div className="text-center">
            <AlertCircle size={24} className="mx-auto mb-2" style={{ color: '#ef4444', opacity: 0.5 }} />
            <div className="text-xs max-w-md" style={{ color: '#ef4444' }}>{data.error}</div>
          </div>
        </div>
      ) : (
        <div ref={scrollRef} className="flex-1 overflow-auto" onScroll={handleTableScroll}>
          {(() => {
            const totalRows = data.preview.length
            const shouldVirtualize = totalRows > VIRTUALIZE_THRESHOLD && viewHeight > 0
            let startIdx = 0
            let endIdx = totalRows
            if (shouldVirtualize) {
              startIdx = Math.max(0, Math.floor(scrollTop / ROW_HEIGHT) - OVERSCAN)
              endIdx = Math.min(totalRows, Math.ceil((scrollTop + viewHeight) / ROW_HEIGHT) + OVERSCAN)
            }
            const topPad = startIdx * ROW_HEIGHT
            const bottomPad = (totalRows - endIdx) * ROW_HEIGHT

            return (
              <table className="w-full text-xs">
                <thead className="sticky top-0 z-10" style={{ background: 'var(--bg-elevated)' }}>
                  <tr>
                    <th className="px-3 py-1.5 text-left text-[11px] font-semibold uppercase tracking-wider w-10"
                      style={{ color: 'var(--text-muted)', borderBottom: '1px solid var(--border)' }}>
                      #
                    </th>
                    {filteredColumns.map((col) => (
                      <th
                        key={col.name}
                        className="px-3 py-1.5 text-left whitespace-nowrap"
                        style={{ borderBottom: '1px solid var(--border)' }}
                      >
                        <div className="font-semibold" style={{ color: 'var(--text-primary)' }}>{col.name}</div>
                        <div className={`text-[11px] font-normal ${getDtypeColor(col.dtype)}`}>
                          {col.dtype}
                        </div>
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {topPad > 0 && <tr style={{ height: topPad }} />}
                  {data.preview.slice(startIdx, endIdx).map((row, vi) => {
                    const i = startIdx + vi
                    return (
                      <tr
                        key={i}
                        style={{ height: ROW_HEIGHT, background: i % 2 === 0 ? 'transparent' : 'rgba(255,255,255,.02)' }}
                      >
                        <td className="px-3 py-1 font-mono" style={{ color: 'var(--text-muted)', borderRight: '1px solid var(--border)' }}>
                          {i + 1}
                        </td>
                        {filteredColumns.map((col) => {
                          const isTraced = tracedCell?.rowIndex === i && tracedCell?.column === col.name
                          return (
                            <td
                              key={col.name}
                              className="px-3 py-1 font-mono whitespace-nowrap max-w-[200px] truncate transition-colors"
                              style={{
                                color: 'var(--text-secondary)',
                                cursor: onCellClick ? 'pointer' : undefined,
                                background: isTraced ? 'var(--accent-soft)' : undefined,
                                boxShadow: isTraced ? 'inset 0 0 0 1.5px var(--accent)' : undefined,
                                borderRadius: isTraced ? '3px' : undefined,
                              }}
                              onClick={() => onCellClick?.(i, col.name, row as Record<string, unknown>)}
                            >
                              <span style={row[col.name] === null ? { color: 'var(--text-muted)', fontStyle: 'italic' } : undefined}>
                                {formatValue(row[col.name])}
                              </span>
                            </td>
                          )
                        })}
                      </tr>
                    )
                  })}
                  {bottomPad > 0 && <tr style={{ height: bottomPad }} />}
                </tbody>
              </table>
            )
          })()}

          {data.row_count > data.preview.length && (
            <div className="px-3 py-1.5 text-[11px] text-center" style={{ color: 'var(--text-muted)', borderTop: '1px solid var(--border)', background: 'var(--bg-elevated)' }}>
              Showing {data.preview.length} of {data.row_count.toLocaleString()} rows
            </div>
          )}
        </div>
      )}
    </div>
  )
}
