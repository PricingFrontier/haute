import { useMemo } from "react"
import type { RatingTable } from "./ratingTableUtils"
import { relativityColor, relativityTextColor, tableStats, resolveDefault } from "./ratingTableUtils"
import { ControlledNumberCell } from "./ControlledNumberCell"
import { StatsFooter } from "./StatsFooter"

export function OneWayEditor({ table, bandingLevels, onUpdateEntries }: {
  table: RatingTable
  bandingLevels: Record<string, string[]>
  onUpdateEntries: (entries: Record<string, string | number>[]) => void
}) {
  const factor = table.factors[0]
  const defaultValue = table.defaultValue
  const entries = useMemo(() => table.entries || [], [table.entries])
  const stats = useMemo(() => tableStats(entries), [entries])

  if (!factor) return null
  const levels = bandingLevels[factor] || []

  const lookup = new Map<string, number>()
  for (const e of entries) {
    const k = String(e[factor] ?? "")
    if (k) lookup.set(k, typeof e.value === "number" ? e.value : parseFloat(String(e.value ?? "1")))
  }
  const maxVal = stats ? Math.max(Math.abs(stats.max), Math.abs(stats.min), 1) : 1

  const safeDefault = resolveDefault(defaultValue)

  const updateCell = (level: string, val: string) => {
    const parsed = parseFloat(val)
    const num = val === "" ? safeDefault : (Number.isNaN(parsed) ? safeDefault : parsed)
    const next = entries.map(e => String(e[factor]) === level ? { ...e, value: num } : e)
    if (!next.some(e => String(e[factor]) === level)) {
      next.push({ [factor]: level, value: Number.isNaN(num) ? 0 : num })
    }
    onUpdateEntries(next)
  }

  return (
    <div className="rounded-lg overflow-hidden" style={{ border: '1px solid var(--border)' }}>
      <table className="w-full text-[11px]" style={{ borderCollapse: 'separate', borderSpacing: 0 }}>
        <thead>
          <tr style={{ background: 'var(--bg-elevated)' }}>
            <th className="text-left px-2.5 py-2 font-bold uppercase tracking-[0.06em] text-[10px]"
              style={{ color: 'var(--text-muted)', borderBottom: '2px solid var(--border)' }}>{factor}</th>
            <th className="text-center px-2 py-2 font-bold uppercase tracking-[0.06em] text-[10px]"
              style={{ color: 'var(--text-muted)', borderBottom: '2px solid var(--border)', width: 80 }}>Relativity</th>
            <th className="px-2 py-2 text-[10px]"
              style={{ color: 'var(--text-muted)', borderBottom: '2px solid var(--border)', width: '40%' }}></th>
          </tr>
        </thead>
        <tbody>
          {levels.length === 0 ? (
            <tr><td colSpan={3} className="px-2 py-4 text-center" style={{ color: 'var(--text-muted)' }}>No banding levels found</td></tr>
          ) : levels.map((level, ri) => {
            const val = lookup.get(level) ?? safeDefault
            const barWidth = Math.min((Math.abs(val) / maxVal) * 100, 100)
            return (
              <tr key={level} style={{
                borderBottom: '1px solid var(--border)',
                background: ri % 2 === 0 ? 'var(--bg-input)' : 'var(--bg-surface)',
              }}>
                <td className="px-2.5 py-1.5 font-mono text-[11px] font-medium"
                  style={{ color: 'var(--text-primary)', borderBottom: '1px solid var(--border)' }}>{level}</td>
                <td className="px-0.5 py-0.5" style={{ borderBottom: '1px solid var(--border)' }}>
                  <ControlledNumberCell
                    val={val}
                    onCommit={(v) => updateCell(level, v)}
                    className="w-full px-1.5 py-1 rounded text-[11px] font-mono text-center focus:outline-none focus:ring-1 focus:ring-emerald-500/40"
                    style={{
                      background: relativityColor(val),
                      border: '1px solid var(--border)',
                      color: relativityTextColor(val),
                      fontWeight: 600,
                    }} />
                </td>
                <td className="px-2 py-1.5" style={{ borderBottom: '1px solid var(--border)' }}>
                  <div className="relative h-3 rounded-full overflow-hidden" style={{ background: 'var(--bg-elevated)' }}>
                    <div className="absolute inset-y-0 left-0 rounded-full transition-all"
                      style={{
                        width: `${barWidth}%`,
                        background: val >= 1 ? 'rgba(239, 68, 68, 0.35)' : 'rgba(59, 130, 246, 0.35)',
                      }} />
                  </div>
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

