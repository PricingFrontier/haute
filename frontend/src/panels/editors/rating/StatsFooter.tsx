export function StatsFooter({ stats }: { stats: { min: number; max: number; avg: number; count: number } | null }) {
  if (!stats) return null
  return (
    <div className="flex items-center gap-3 px-2.5 py-1.5 text-[10px] font-mono rounded-b-lg"
      style={{ background: 'var(--bg-elevated)', borderTop: '1px solid var(--border)', color: 'var(--text-muted)' }}>
      <span>n={stats.count}</span>
      <span style={{ color: 'var(--chart-below)' }}>min {stats.min.toFixed(3)}</span>
      <span style={{ color: 'var(--text-secondary)' }}>avg {stats.avg.toFixed(3)}</span>
      <span style={{ color: 'var(--danger-solid)' }}>max {stats.max.toFixed(3)}</span>
    </div>
  )
}
