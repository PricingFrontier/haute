/** Trace formatting utilities for the enhanced trace panel. */

const DATE_RE = /^\d{4}-\d{2}-\d{2}(T[\d:.Z+-]+)?$/

export function formatTraceValue(v: unknown): string {
  if (v === null || v === undefined) return "null"
  if (typeof v === "boolean") return String(v)
  if (typeof v === "number") {
    if (Number.isNaN(v)) return "NaN"
    if (v === Infinity) return "\u221e"
    if (v === -Infinity) return "-\u221e"
    if (Number.isInteger(v)) return v.toLocaleString()
    // Smart-round to avoid IEEE 754 artifacts
    // Use toPrecision(12) then parseFloat to strip trailing noise
    const rounded = parseFloat(v.toPrecision(12))
    return String(rounded)
  }
  if (typeof v === "string") {
    if (DATE_RE.test(v)) return v
    return `"${v}"`
  }
  if (typeof v === "object") return JSON.stringify(v)
  return String(v)
}

export function formatExpression(expr: string | null | undefined, maxLen = 60): string {
  if (!expr) return ""
  let result = expr
    .replace(/ \* /g, " \u00d7 ")
    .replace(/ \/ /g, " \u00f7 ")
  if (result.length > maxLen) {
    result = result.slice(0, maxLen) + "\u2026"
  }
  return result
}

export function formatCalculation(opts: {
  expression?: string | null
  values: Record<string, unknown>
  result: unknown
}): string {
  const { expression, values, result } = opts
  if (!expression) return `= ${formatTraceValue(result)}`

  // Replace column names in the expression with their formatted values
  // Sort by length descending to avoid partial replacement issues
  const colNames = Object.keys(values).sort((a, b) => b.length - a.length)
  let substituted = expression
  for (const col of colNames) {
    const formatted = formatTraceValue(values[col])
    substituted = substituted.replace(new RegExp(`\\b${escapeRegExp(col)}\\b`, "g"), formatted)
  }

  // Apply operator replacement
  substituted = substituted
    .replace(/ \* /g, " \u00d7 ")
    .replace(/ \/ /g, " \u00f7 ")

  return `${substituted} = ${formatTraceValue(result)}`
}

function escapeRegExp(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")
}

export function formatSchemaSummary(opts: {
  added: number
  modified: number
  removed: number
  passed: number
}): string {
  const parts: string[] = []
  if (opts.added > 0) parts.push(`${opts.added} added`)
  if (opts.modified > 0) parts.push(`${opts.modified} modified`)
  if (opts.removed > 0) parts.push(`${opts.removed} removed`)
  if (opts.passed > 0) parts.push(`${opts.passed} passed through`)
  return parts.length > 0 ? parts.join(", ") : "no changes"
}
