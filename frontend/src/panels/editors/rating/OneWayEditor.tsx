import { useMemo } from "react"
import type { RatingTable } from "./ratingTableUtils"
import { tableStats, resolveDefault } from "./ratingTableUtils"
import { ControlledNumberCell } from "./ControlledNumberCell"
import { StatsFooter } from "./StatsFooter"
import { EDITABLE_RELATIVITY_INPUT_STYLE, NON_EDITABLE_LABEL_CELL_STYLE } from "./cellStyles"
import { useGridCellSelection } from "./useGridCellSelection"

export function OneWayEditor({ table, bandingLevels, onUpdateEntries }: {
  table: RatingTable
  bandingLevels: Record<string, string[]>
  onUpdateEntries: (entries: Record<string, string | number>[]) => void
}) {
  const factor = table.factors[0]
  const defaultValue = table.default_value
  const entries = useMemo(() => table.entries || [], [table.entries])
  const stats = useMemo(() => tableStats(entries), [entries])
  const { regionRef, regionProps, cellProps, isCellSelected } = useGridCellSelection()

  if (!factor) return null
  const levels = bandingLevels[factor] || []

  const lookup = new Map<string, number>()
  for (const e of entries) {
    const k = String(e[factor] ?? "")
    if (k) lookup.set(k, typeof e.value === "number" ? e.value : parseFloat(String(e.value ?? "1")))
  }

  const safeDefault = resolveDefault(defaultValue)

  /** A level's committed relativity; a cleared cell takes the table's default. */
  const updateCell = (level: string, value: number | null) => {
    const num = value ?? safeDefault
    const next = entries.map(e => String(e[factor]) === level ? { ...e, value: num } : e)
    if (!next.some(e => String(e[factor]) === level)) {
      next.push({ [factor]: level, value: num })
    }
    onUpdateEntries(next)
  }

  return (
    <div
      ref={regionRef}
      className="rating-editor-grid-region rounded-lg overflow-hidden"
      role="region"
      tabIndex={0}
      aria-label={`${factor} rating grid`}
      style={{ border: '1px solid var(--border)' }}
      {...regionProps({ rows: levels, cols: ["Relativity"], cellText: (level) => String(lookup.get(level) ?? safeDefault) })}
    >
      <table className="w-full text-[11px]" style={{ borderCollapse: 'separate', borderSpacing: 0 }}>
        <thead>
          <tr style={{ background: 'var(--bg-elevated)' }}>
            <th scope="col" className="text-left px-2.5 py-2 font-bold uppercase tracking-[0.06em] text-[10px]"
              style={{ color: 'var(--text-muted)', borderBottom: '2px solid var(--border)' }}>{factor}</th>
            {/* The rates as plain numbers, with no formatting that varies with the value. */}
            <th scope="col" className="text-center px-2 py-2 font-bold uppercase tracking-[0.06em] text-[10px]"
              style={{ color: 'var(--text-muted)', borderBottom: '2px solid var(--border)', width: '45%' }}>Relativity</th>
          </tr>
        </thead>
        <tbody>
          {levels.length === 0 ? (
            <tr><td colSpan={2} className="px-2 py-4 text-center" style={{ color: 'var(--text-muted)' }}>No banding levels found</td></tr>
          ) : levels.map((level, ri) => {
            const val = lookup.get(level) ?? safeDefault
            return (
              <tr key={level} style={{
                borderBottom: '1px solid var(--border)',
                background: ri % 2 === 0 ? 'var(--bg-input)' : 'var(--bg-panel)',
              }}>
                <th scope="row" className="px-2.5 py-1.5 font-mono text-[11px] font-medium text-left"
                  style={{ ...NON_EDITABLE_LABEL_CELL_STYLE, borderBottom: '1px solid var(--border)' }}>{level}</th>
                <td
                  className="rating-editor-value-cell px-0.5 py-0.5"
                  {...cellProps(ri, 0)}
                  style={{
                    background: isCellSelected(ri, 0) ? 'var(--accent-soft)' : 'transparent',
                    borderBottom: '1px solid var(--border)',
                  }}
                >
                  <ControlledNumberCell
                    val={val}
                    onCommit={(v) => updateCell(level, v)}
                    aria-label={`Relativity for ${factor} ${level}`}
                    className="rating-editor-number-cell w-full px-1.5 py-1 rounded-none text-[11px] font-mono text-center cursor-text"
                    style={EDITABLE_RELATIVITY_INPUT_STYLE} />
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
      <StatsFooter stats={stats} />
    </div>
  )
}

