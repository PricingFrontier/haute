/** Trace formatting utilities for the enhanced trace panel. */

import { formatJsonSpecialValue } from "./formatValue"

const DATE_RE = /^\d{4}-\d{2}-\d{2}(T[\d:.Z+-]+)?$/
export const TRACE_NUMBER_LOCALE = "en-GB"
export const TRACE_MAX_FRACTION_DIGITS = 4

export function formatTraceValue(
  v: unknown,
  maxFractionDigits = TRACE_MAX_FRACTION_DIGITS,
): string {
  const special = formatJsonSpecialValue(v)
  if (special !== null) return special
  if (v === null || v === undefined) return "-"
  if (typeof v === "boolean") return String(v)
  if (typeof v === "number") {
    if (Number.isNaN(v)) return "NaN"
    if (v === Infinity) return "Infinity"
    if (v === -Infinity) return "-Infinity"
    const formatted = v.toLocaleString(TRACE_NUMBER_LOCALE, {
      maximumFractionDigits: maxFractionDigits,
    })
    // Avoid making a small but meaningful non-zero value look like zero.
    return v !== 0 && Number(formatted.replace(/,/g, "")) === 0
      ? String(v)
      : formatted
  }
  if (typeof v === "string") {
    if (DATE_RE.test(v)) return v
    return v
  }
  if (typeof v === "object") {
    return JSON.stringify(v, (_key, value: unknown) => formatJsonSpecialValue(value) ?? value)
  }
  return String(v)
}

const OPERATOR_GLYPHS: Record<string, string> = { "*": "\u00d7", "/": "\u00f7" }

/** Whether the text before `index` ends an operand, so an operator there is binary. */
function followsOperand(text: string, index: number): boolean {
  const before = text.slice(0, index).trimEnd()
  return /[\p{L}\p{N}_)\]}"'.]$/u.test(before)
}

/**
 * A formula with its arithmetic operators shown as the trace writes them: a
 * binary `*` as \u00d7 and `/` as \u00f7. Quoted text, `**`, `//` and an unpacking `*`
 * stay as written, so a literal such as `"a/b"` reads as the code wrote it.
 */
export function displayOperators(text: string): string {
  let out = ""
  let quote: string | null = null
  for (let i = 0; i < text.length; i++) {
    const ch = text[i]
    if (quote !== null) {
      out += ch
      if (ch === "\\" && i + 1 < text.length) {
        i += 1
        out += text[i]
      } else if (ch === quote) {
        quote = null
      }
      continue
    }
    if (ch === '"' || ch === "'") {
      quote = ch
      out += ch
      continue
    }
    const glyph = OPERATOR_GLYPHS[ch]
    if (glyph === undefined) {
      out += ch
      continue
    }
    if (text[i + 1] === ch) {
      out += ch + ch
      i += 1
      continue
    }
    out += followsOperand(text, i) ? glyph : ch
  }
  return out
}

export function formatExpression(expr: string | null | undefined, maxLen = 60): string {
  if (!expr) return ""
  let result = displayOperators(expr)
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

  substituted = displayOperators(substituted)

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
