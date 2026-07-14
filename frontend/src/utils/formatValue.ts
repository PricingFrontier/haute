type HauteNonFiniteFloat = {
  __haute_type__: "non_finite_float"
  value: "nan" | "inf" | "-inf"
}

function isHauteNonFiniteFloat(value: unknown): value is HauteNonFiniteFloat {
  if (typeof value !== "object" || value === null || Array.isArray(value)) return false
  const obj = value as Record<string, unknown>
  return (
    obj.__haute_type__ === "non_finite_float" &&
    (obj.value === "nan" || obj.value === "inf" || obj.value === "-inf")
  )
}

export function formatJsonSpecialValue(value: unknown): string | null {
  if (!isHauteNonFiniteFloat(value)) return null
  if (value.value === "nan") return "NaN"
  if (value.value === "inf") return "Infinity"
  return "-Infinity"
}

export function formatValue(v: unknown, maxFractionDigits = 4): string {
  const special = formatJsonSpecialValue(v)
  if (special !== null) return special
  if (v === null || v === undefined) return "null"
  if (typeof v === "number") {
    if (Number.isInteger(v)) return v.toLocaleString()
    return v.toLocaleString(undefined, { maximumFractionDigits: maxFractionDigits })
  }
  if (typeof v === "object") {
    // Struct / list / array cells: render as JSON rather than "[object Object]".
    // Nested non-finite-float sentinels become their display strings.
    return JSON.stringify(v, (_key, value: unknown) => formatJsonSpecialValue(value) ?? value)
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

/** Format a null-count / row-count ratio as a 1-dp percentage, or null when the row count is 0. */
export function formatNullPct(nullCount: number, rowCount: number): string | null {
  if (rowCount === 0) return null
  return `${((nullCount / rowCount) * 100).toFixed(1)}%`
}

export function formatElapsed(seconds: number): string {
  if (seconds < 60) return `${seconds.toFixed(0)}s`
  const mins = Math.floor(seconds / 60)
  const secs = Math.floor(seconds % 60)
  return `${mins}m ${secs}s`
}
