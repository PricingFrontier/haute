export function formatValue(v: unknown, maxFractionDigits = 4): string {
  if (v === null || v === undefined) return "null"
  if (typeof v === "number") {
    if (Number.isInteger(v)) return v.toLocaleString()
    return v.toLocaleString(undefined, { maximumFractionDigits: maxFractionDigits })
  }
  return String(v)
}

export function formatValueCompact(v: unknown): string {
  const s = formatValue(v)
  return s.length > 20 ? s.slice(0, 18) + "\u2026" : s
}

/** Format large numbers compactly: 1234567 → "1.23M", 12345 → "12.3K". */
export function formatNumber(n: number): string {
  if (Math.abs(n) >= 1_000_000) return (n / 1_000_000).toFixed(2) + "M"
  if (Math.abs(n) >= 1_000) return (n / 1_000).toFixed(1) + "K"
  if (Number.isInteger(n)) return String(n)
  return n.toFixed(4)
}

/** Safely format a number with fixed decimal places. Returns 'N/A' for non-numeric/non-finite values. */
export function formatFixed(value: unknown, digits: number): string {
  return typeof value === 'number' && Number.isFinite(value)
    ? value.toFixed(digits)
    : 'N/A'
}

export function formatElapsed(seconds: number): string {
  if (seconds < 60) return `${seconds.toFixed(0)}s`
  const mins = Math.floor(seconds / 60)
  const secs = Math.floor(seconds % 60)
  return `${mins}m ${secs}s`
}
