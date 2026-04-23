import { useState, useMemo, useRef, useCallback, type ComponentType } from "react"
import useClickOutside from "../hooks/useClickOutside"

export interface BreakdownItem {
  node_id: string
  label: string
  value: number
}

interface BreakdownDropdownProps {
  icon: ComponentType<{ size: number }>
  title: string
  items: BreakdownItem[]
  formatValue: (value: number) => string
  /** Width class for the right-hand value column (e.g. "w-12", "w-14"). */
  valueWidth?: string
}

export default function BreakdownDropdown({
  icon: Icon, title, items, formatValue, valueWidth = "w-12",
}: BreakdownDropdownProps) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  const close = useCallback(() => setOpen(false), [])
  useClickOutside(ref, close, open)

  const data = useMemo(() => {
    if (!items.length) return null
    const sorted = [...items].sort((a, b) => b.value - a.value)
    const maxValue = sorted[0]?.value || 1
    const total = sorted.reduce((s, i) => s + i.value, 0)
    return { sorted, maxValue, total }
  }, [items])

  const hasData = data !== null

  return (
    <div ref={ref} className="relative flex items-center gap-1">
      <button
        onClick={() => { if (hasData) setOpen((v) => !v) }}
        className="flex items-center gap-1 px-1 py-0.5 rounded transition-colors hover-breakdown"
        data-hover-enabled={!open && hasData}
        data-open={open}
        style={{
          cursor: hasData ? 'pointer' : 'default',
          opacity: hasData ? 1 : 0.35,
        }}
        title={hasData ? `Toggle ${title.toLowerCase()} breakdown` : title}
      >
        <Icon size={12} />
        <span className="text-[11px] font-mono">{formatValue(data?.total ?? 0)}</span>
      </button>
      {open && data && (
        <div
          className="absolute top-full left-0 mt-1 rounded-lg shadow-2xl z-50 w-[320px] overflow-hidden"
          style={{ background: 'var(--bg-panel)', border: '1px solid var(--border)' }}
        >
          <div className="px-3 py-2 overflow-y-auto" style={{ maxHeight: 240 }}>
            <div className="flex items-center justify-between mb-1.5">
              <span className="text-[10px] font-semibold uppercase tracking-wider" style={{ color: 'var(--text-muted)' }}>
                {title}
              </span>
              <span className="text-[10px] font-mono" style={{ color: 'var(--text-muted)' }}>
                {formatValue(data.total)} total
              </span>
            </div>
            {data.sorted.map((item) => {
              const pct = item.value / data.maxValue
              const barColor = pct > 0.7
                ? 'var(--danger)'
                : pct > 0.3
                  ? 'var(--warning-strong)'
                  : 'var(--success)'
              return (
                <div key={item.node_id} className="flex items-center gap-2 py-0.5">
                  <span
                    className="text-[11px] truncate shrink-0"
                    style={{ width: 100, color: 'var(--text-secondary)' }}
                    title={item.label}
                  >
                    {item.label}
                  </span>
                  <div className="flex-1 h-3 rounded-sm overflow-hidden" style={{ background: 'rgba(255,255,255,.05)' }}>
                    <div
                      className="h-full rounded-sm transition-all"
                      style={{
                        width: `${Math.max(2, pct * 100)}%`,
                        background: barColor,
                        opacity: 0.8,
                      }}
                    />
                  </div>
                  <span className={`text-[10px] font-mono shrink-0 ${valueWidth} text-right`} style={{ color: 'var(--text-muted)' }}>
                    {formatValue(item.value)}
                  </span>
                </div>
              )
            })}
          </div>
        </div>
      )}
    </div>
  )
}
