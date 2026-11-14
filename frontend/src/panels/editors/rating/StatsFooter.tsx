import type { ReactNode } from "react"

/**
 * The line under a rating grid: its count, min, average and max, and on its
 * right any actions the grid offers (the two-way grid's copy), so they share a
 * row. With no actions and no numbers there is no line.
 */
export function StatsFooter({ stats, actions }: {
  stats: { min: number; max: number; avg: number; count: number } | null
  actions?: ReactNode
}) {
  if (!stats && !actions) return null
  return (
    <div className="flex items-center gap-3 px-2.5 py-1.5 text-[10px] font-mono rounded-b-lg"
      style={{ background: 'var(--bg-elevated)', borderTop: '1px solid var(--border)', color: 'var(--text-muted)' }}>
      {stats && (
        <>
          <span>n={stats.count}</span>
          <span style={{ color: 'var(--chart-below)' }}>min {stats.min.toFixed(3)}</span>
          <span style={{ color: 'var(--text-secondary)' }}>avg {stats.avg.toFixed(3)}</span>
          <span style={{ color: 'var(--danger-solid)' }}>max {stats.max.toFixed(3)}</span>
        </>
      )}
      {actions && <div className="ml-auto flex items-center gap-1">{actions}</div>}
    </div>
  )
}
