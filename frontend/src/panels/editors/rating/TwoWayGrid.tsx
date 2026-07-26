import { useMemo, useRef, useState } from "react"
import { Copy } from "lucide-react"
import type { RatingTable } from "./ratingTableUtils"
import { tableStats, resolveDefault } from "./ratingTableUtils"
import { ControlledNumberCell } from "./ControlledNumberCell"
import { StatsFooter } from "./StatsFooter"
import { EDITABLE_RELATIVITY_INPUT_STYLE, NON_EDITABLE_LABEL_CELL_STYLE } from "./cellStyles"
import useToastStore from "../../../stores/useToastStore"
import { parsePastedGrid } from "../shared/tableClipboard"

type PastedNumber = { kind: "value"; value: number } | { kind: "blank" } | { kind: "invalid" }

function parsePastedNumber(value: string): PastedNumber {
  const trimmed = value.trim()
  if (!trimmed) return { kind: "blank" }
  const parsed = Number(trimmed)
  return Number.isFinite(parsed) ? { kind: "value", value: parsed } : { kind: "invalid" }
}

function buildTableTsv(
  rowFactor: string,
  colFactor: string,
  rowLabels: string[],
  colLabels: string[],
  valueFor: (row: string, col: string) => number,
): string {
  const header = [`${rowFactor} \\ ${colFactor}`, ...colLabels].join("\t")
  const rows = rowLabels.map(row => [
    row,
    ...colLabels.map(col => String(valueFor(row, col))),
  ].join("\t"))
  return [header, ...rows].join("\n")
}

function formatInvalidPasteLocation(
  rowFactor: string,
  rowLabel: string,
  colFactor: string,
  colLabel: string,
  pastedRowIndex: number,
  pastedColIndex: number,
): string {
  return `pasted row ${pastedRowIndex + 1}, column ${pastedColIndex + 1} (${rowFactor} "${rowLabel}" and ${colFactor} "${colLabel}")`
}

export function TwoWayGrid({ table, bandingLevels, onUpdateEntries, factorOverrides }: {
  table: RatingTable
  bandingLevels: Record<string, string[]>
  onUpdateEntries: (entries: Record<string, string | number>[]) => void
  factorOverrides?: { factors: string[]; sliceKey?: Record<string, string> }
}) {
  const addToast = useToastStore(s => s.addToast)
  const usedFactors = factorOverrides?.factors || table.factors.slice(0, 2)
  const sliceKey = factorOverrides?.sliceKey || {}
  const rowFactor = usedFactors[0]
  const colFactor = usedFactors[1]
  const entries = useMemo(() => table.entries || [], [table.entries])
  const stats = useMemo(() => tableStats(entries), [entries])
  const safeDefault = resolveDefault(table.defaultValue)
  const gridRegionRef = useRef<HTMLDivElement>(null)
  const [selection, setSelection] = useState<{
    anchorRow: number
    anchorCol: number
    focusRow: number
    focusCol: number
  } | null>(null)
  const [isDraggingSelection, setIsDraggingSelection] = useState(false)

  if (!rowFactor || !colFactor) return null

  const rowLabels = bandingLevels[rowFactor] || []
  const colLabels = bandingLevels[colFactor] || []

  const lookup = new Map<string, number>()
  for (const e of entries) {
    const matchSlice = Object.entries(sliceKey).every(([k, v]) => String(e[k]) === v)
    if (!matchSlice) continue
    const key = `${e[rowFactor]}\x1F${e[colFactor]}`
    lookup.set(key, typeof e.value === "number" ? e.value : parseFloat(String(e.value ?? "1")))
  }

  const valueFor = (row: string, col: string) => lookup.get(`${row}\x1F${col}`) ?? safeDefault

  const selectedRange = selection
    ? {
      startRow: Math.min(selection.anchorRow, selection.focusRow),
      endRow: Math.max(selection.anchorRow, selection.focusRow),
      startCol: Math.min(selection.anchorCol, selection.focusCol),
      endCol: Math.max(selection.anchorCol, selection.focusCol),
    }
    : null
  const isCellSelected = (rowIndex: number, colIndex: number) =>
    Boolean(selectedRange &&
      rowIndex >= selectedRange.startRow &&
      rowIndex <= selectedRange.endRow &&
      colIndex >= selectedRange.startCol &&
      colIndex <= selectedRange.endCol)

  const selectedValuesTsv = () => {
    if (!selectedRange) return ""
    return rowLabels
      .slice(selectedRange.startRow, selectedRange.endRow + 1)
      .map(row => colLabels
        .slice(selectedRange.startCol, selectedRange.endCol + 1)
        .map(col => String(valueFor(row, col)))
        .join("\t"))
      .join("\n")
  }

  const applyCellUpdates = (updates: Map<string, number>) => {
    const used = new Set<string>()
    const next = entries.map(e => {
      const matchSlice = Object.entries(sliceKey).every(([k, v]) => String(e[k]) === v)
      if (!matchSlice) return e
      const row = String(e[rowFactor] ?? "")
      const col = String(e[colFactor] ?? "")
      const key = `${row}\x1F${col}`
      if (!updates.has(key)) return e
      used.add(key)
      return { ...e, value: updates.get(key)! }
    })

    for (const [key, value] of updates.entries()) {
      if (used.has(key)) continue
      const [row, col] = key.split("\x1F")
      next.push({ ...sliceKey, [rowFactor]: row, [colFactor]: col, value })
    }

    onUpdateEntries(next)
  }

  const handlePaste = (e: React.ClipboardEvent<HTMLElement>, startRow: number, startCol: number) => {
    const grid = parsePastedGrid(e.clipboardData.getData("text/plain"))
    if (grid.length === 0) return
    e.preventDefault()
    e.stopPropagation()

    const updates = new Map<string, number>()
    const addUpdate = (
      rowLabel: string,
      colLabel: string,
      rawValue: string,
      pastedRowIndex: number,
      pastedColIndex: number,
    ): boolean => {
      const parsed = parsePastedNumber(rawValue)
      if (parsed.kind === "blank") return true
      if (parsed.kind === "invalid") {
        addToast("error", `Could not paste rating table TSV: invalid number at ${formatInvalidPasteLocation(
          rowFactor,
          rowLabel,
          colFactor,
          colLabel,
          pastedRowIndex,
          pastedColIndex,
        )}.`)
        return false
      }
      updates.set(`${rowLabel}\x1F${colLabel}`, parsed.value)
      return true
    }
    const firstRow = grid[0] || []
    const labelledColumns = firstRow.slice(1).some(cell => colLabels.includes(cell.trim()))
    const labelledRows = grid.slice(1).some(row => row[0] && rowLabels.includes(row[0].trim()))

    if (labelledColumns && labelledRows) {
      for (let r = 1; r < grid.length; r++) {
        const rowLabel = grid[r][0]?.trim()
        if (!rowLabel || !rowLabels.includes(rowLabel)) continue
        for (let c = 1; c < grid[r].length; c++) {
          const colLabel = firstRow[c]?.trim()
          if (!colLabel || !colLabels.includes(colLabel)) continue
          if (!addUpdate(rowLabel, colLabel, grid[r][c] ?? "", r, c)) return
        }
      }
    } else {
      for (let r = 0; r < grid.length; r++) {
        const rowLabel = rowLabels[startRow + r]
        if (!rowLabel) continue
        for (let c = 0; c < grid[r].length; c++) {
          const colLabel = colLabels[startCol + c]
          if (!colLabel) continue
          if (!addUpdate(rowLabel, colLabel, grid[r][c] ?? "", r, c)) return
        }
      }
    }

    if (updates.size > 0) applyCellUpdates(updates)
  }

  const updateCell = (row: string, col: string, val: string) => {
    const parsed = parseFloat(val)
    const numVal = val === "" ? safeDefault : (Number.isNaN(parsed) ? safeDefault : parsed)
    const matchRow = (e: Record<string, string | number>) =>
      String(e[rowFactor]) === row && String(e[colFactor]) === col &&
      Object.entries(sliceKey).every(([k, v]) => String(e[k]) === v)

    let found = false
    const next = entries.map(e => {
      if (matchRow(e)) { found = true; return { ...e, value: numVal } }
      return e
    })
    if (!found) {
      next.push({ ...sliceKey, [rowFactor]: row, [colFactor]: col, value: numVal })
    }
    onUpdateEntries(next)
  }

  const copyVisibleTable = () => {
    const text = buildTableTsv(rowFactor, colFactor, rowLabels, colLabels, valueFor)
    void navigator.clipboard.writeText(text).catch((error: unknown) => {
      const detail = error instanceof Error ? error.message : String(error)
      addToast("error", `Could not copy rating table TSV: ${detail}`)
    })
  }

  const handleCopy = (e: React.ClipboardEvent<HTMLElement>) => {
    if (
      e.target instanceof HTMLInputElement &&
      e.target.selectionStart !== null &&
      e.target.selectionEnd !== null &&
      e.target.selectionStart !== e.target.selectionEnd
    ) {
      return
    }
    const text = selectedValuesTsv()
    if (!text) return
    e.preventDefault()
    e.clipboardData.setData("text/plain", text)
  }

  const clearSelection = () => {
    setSelection(null)
    setIsDraggingSelection(false)
  }

  if (rowLabels.length === 0 || colLabels.length === 0) {
    return <div className="px-2 py-3 text-center text-[11px]" style={{ color: 'var(--text-muted)' }}>No banding levels found for selected factors</div>
  }

  return (
    <div className="rounded-lg overflow-hidden" style={{ border: '1px solid var(--border)' }}>
      <div
        ref={gridRegionRef}
        className="rating-editor-grid-region overflow-x-auto"
        data-testid="two-way-grid-scroll-container"
        role="region"
        tabIndex={0}
        aria-label={`${rowFactor} by ${colFactor} rating grid`}
        onPaste={(e) => handlePaste(e, 0, 0)}
        onCopy={handleCopy}
        onKeyDown={(e) => {
          if (e.key === "Escape") clearSelection()
        }}
        onMouseLeave={() => setIsDraggingSelection(false)}
        onMouseUp={() => setIsDraggingSelection(false)}
      >
        <table className="w-full text-[11px]" style={{ borderCollapse: 'separate', borderSpacing: 0 }}>
          <thead>
            <tr style={{ background: 'var(--bg-elevated)' }}>
              <th scope="col" className="text-left px-2.5 py-2 font-bold uppercase tracking-[0.06em] text-[10px] sticky left-0 z-10"
                style={{ ...NON_EDITABLE_LABEL_CELL_STYLE, color: 'var(--text-muted)', borderBottom: '2px solid var(--border)' }}>
                <span style={{ color: 'var(--text-secondary)' }}>{rowFactor}</span>
                <span style={{ color: 'var(--text-muted)', margin: '0 4px' }}>↓</span>
                <span style={{ color: 'var(--text-secondary)' }}>{colFactor}</span>
                <span style={{ color: 'var(--text-muted)', margin: '0 2px' }}>→</span>
              </th>
              {colLabels.map(col => (
                <th key={col} scope="col" className="text-center px-1 py-2 font-bold font-mono text-[10px] uppercase tracking-[0.04em]"
                  style={{ ...NON_EDITABLE_LABEL_CELL_STYLE, minWidth: 64, borderBottom: '2px solid var(--border)' }}>{col}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rowLabels.map((row, ri) => (
              <tr key={row} style={{ background: ri % 2 === 0 ? 'var(--bg-input)' : 'var(--bg-panel)' }}>
                <th scope="row" className="px-2.5 py-1 font-mono text-[11px] font-medium text-left sticky left-0 z-10"
                  style={{
                    ...NON_EDITABLE_LABEL_CELL_STYLE,
                    whiteSpace: 'nowrap',
                    borderBottom: '1px solid var(--border)',
                  }}>{row}</th>
                {colLabels.map((col, ci) => {
                  const val = lookup.get(`${row}\x1F${col}`) ?? safeDefault
                  return (
                    <td
                      key={col}
                      className="rating-editor-value-cell px-0.5 py-0.5"
                      data-selected={isCellSelected(ri, ci) || undefined}
                      aria-selected={isCellSelected(ri, ci) || undefined}
                      onMouseDown={() => {
                        gridRegionRef.current?.focus({ preventScroll: true })
                        setSelection({ anchorRow: ri, anchorCol: ci, focusRow: ri, focusCol: ci })
                        setIsDraggingSelection(true)
                      }}
                      onMouseEnter={() => {
                        if (isDraggingSelection) {
                          setSelection(current => current ? { ...current, focusRow: ri, focusCol: ci } : current)
                        }
                      }}
                      onMouseUp={(e) => {
                        setIsDraggingSelection(false)
                        if (
                          selection?.anchorRow === ri &&
                          selection.anchorCol === ci &&
                          selection.focusRow === ri &&
                          selection.focusCol === ci
                        ) {
                          e.currentTarget.querySelector("input")?.focus()
                        }
                      }}
                      style={{
                        background: isCellSelected(ri, ci) ? 'var(--accent-soft)' : 'transparent',
                        borderBottom: '1px solid var(--border)',
                        borderRight: '1px solid var(--border)',
                      }}
                    >
                      <ControlledNumberCell
                        val={val}
                        onCommit={(v) => updateCell(row, col, v)}
                        onPaste={(e) => handlePaste(e, ri, ci)}
                        aria-label={`Relativity for ${rowFactor} ${row} and ${colFactor} ${col}`}
                        className="rating-editor-number-cell w-full px-1 py-1 rounded-none text-[11px] font-mono text-center cursor-text"
                        style={{
                          ...EDITABLE_RELATIVITY_INPUT_STYLE,
                          minWidth: 56,
                        }} />
                    </td>
                  )
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="flex items-center justify-end px-2 py-1.5" style={{ background: 'var(--bg-elevated)', borderTop: '1px solid var(--border)' }}>
        <button
          type="button"
          onClick={copyVisibleTable}
          aria-label="Copy visible table as TSV"
          title="Copy visible table as TSV"
          className="accent-hover-btn flex size-6 items-center justify-center rounded"
          style={{ color: 'var(--text-secondary)', ['--node-accent' as string]: 'var(--accent)' }}
        >
          <Copy size={13} aria-hidden="true" />
        </button>
      </div>
      <StatsFooter stats={stats} />
    </div>
  )
}

