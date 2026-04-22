// ─── Rating Table Types & Pure Utilities ──────────────────────────

export type RatingTable = {
  name: string
  factors: string[]
  outputColumn: string
  defaultValue: string | null
  entries: Record<string, string | number>[]
}

export function normaliseRatingTables(config: Record<string, unknown>): RatingTable[] {
  const raw = config.tables as RatingTable[] | undefined
  if (Array.isArray(raw) && raw.length > 0) return raw
  return [{ name: "Table 1", factors: [], outputColumn: "", defaultValue: "1.0", entries: [] }]
}

/** Heatmap color for actuarial relativity values. */
export function relativityColor(value: number): string {
  if (isNaN(value)) return 'transparent'
  const dev = value - 1.0
  const t = Math.min(Math.abs(dev) / 0.5, 1)
  if (dev > 0.005) return `rgba(var(--danger-rgb), ${(t * 0.22).toFixed(3)})`
  if (dev < -0.005) return `rgba(var(--chart-below-rgb), ${(t * 0.22).toFixed(3)})`
  return 'transparent'
}

export function relativityTextColor(value: number): string {
  if (isNaN(value)) return 'var(--text-secondary)'
  const dev = value - 1.0
  if (dev > 0.005) return 'var(--danger-solid)'
  if (dev < -0.005) return 'var(--chart-below)'
  return 'var(--success)'
}

export function tableStats(entries: Record<string, string | number>[]): { min: number; max: number; avg: number; count: number } | null {
  const vals = entries.map(e => typeof e.value === 'number' ? e.value : parseFloat(String(e.value ?? ''))).filter(v => !isNaN(v))
  if (vals.length === 0) return null
  const min = vals.reduce((a, b) => Math.min(a, b), Infinity)
  const max = vals.reduce((a, b) => Math.max(a, b), -Infinity)
  const avg = vals.reduce((s, v) => s + v, 0) / vals.length
  return { min, max, avg, count: vals.length }
}

export function buildCartesianEntries(
  factors: string[],
  bandingLevels: Record<string, string[]>,
  existing: Record<string, string | number>[],
  defaultValue: string | null,
): Record<string, string | number>[] {
  if (factors.length === 0) return []
  const levelArrays = factors.map(f => bandingLevels[f] || [])
  if (levelArrays.some(a => a.length === 0)) return existing

  const existingLookup = new Map<string, number>()
  for (const e of existing) {
    const key = factors.map(f => String(e[f] ?? "")).join("\x1F")
    const v = e.value
    if (v !== undefined && v !== null && v !== "") {
      const parsed = typeof v === "number" ? v : parseFloat(String(v))
      if (!Number.isNaN(parsed)) existingLookup.set(key, parsed)
    }
  }

  const parsedDef = defaultValue != null && String(defaultValue).trim() ? parseFloat(String(defaultValue)) : 1.0
  const defVal = Number.isNaN(parsedDef) ? 1.0 : parsedDef
  const entries: Record<string, string | number>[] = []

  function recurse(depth: number, current: Record<string, string>) {
    if (depth === factors.length) {
      const key = factors.map(f => current[f]).join("\x1F")
      entries.push({ ...current, value: existingLookup.get(key) ?? defVal })
      return
    }
    for (const level of levelArrays[depth]) {
      recurse(depth + 1, { ...current, [factors[depth]]: level })
    }
  }
  recurse(0, {})
  return entries
}

// ─── Shared helpers for rating editors ────────────────────────────

/** Resolve the table's defaultValue to a safe numeric fallback. */
export function resolveDefault(defaultValue: string | number | null | undefined): number {
  const raw = typeof defaultValue === "number" ? defaultValue
    : typeof defaultValue === "string" && defaultValue.trim() ? parseFloat(defaultValue) : 1
  return Number.isNaN(raw) ? 1 : raw
}

