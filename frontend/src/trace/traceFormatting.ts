import type React from "react"

import { formatJsonSpecialValue } from "../utils/formatValue"
import {
  formatTraceValue as formatCanonicalTraceValue,
  TRACE_MAX_FRACTION_DIGITS,
} from "../utils/formatTrace"

// ---------------------------------------------------------------------------
// Value formatting helpers shared across CalculationHero and its sub-components.
// Pure functions — no state, no side effects. Extracted from CalculationHero
// during the split so WaterfallChart / ExpressionChain / InputSourceTree can
// reuse them without duplicating logic or threading every helper through props.
// ---------------------------------------------------------------------------

export function formatSmartValue(v: unknown): string {
  return formatCanonicalTraceValue(v)
}

export function formatResultValue(v: unknown, precision?: number): string {
  return formatCanonicalTraceValue(v, precision ?? TRACE_MAX_FRACTION_DIGITS)
}

export function formatResultValueFull(v: unknown): string {
  const special = formatJsonSpecialValue(v)
  if (special !== null) return special
  if (v === null || v === undefined) return "-"
  if (typeof v === "number") return String(v)
  if (typeof v === "string") return v
  if (typeof v === "object") {
    return JSON.stringify(v, (_key, value: unknown) => formatJsonSpecialValue(value) ?? value)
  }
  return String(v)
}

export const formatTraceValue = formatCanonicalTraceValue

export function traceValuePresentation(
  value: unknown,
  context: string,
  maxFractionDigits = TRACE_MAX_FRACTION_DIGITS,
): { display: string; title?: string; ariaLabel?: string } {
  const display = formatCanonicalTraceValue(value, maxFractionDigits)
  if (typeof value !== "number" || !Number.isFinite(value)) return { display }
  const full = String(value)
  if (display === full) return { display }
  return {
    display,
    title: `${context}: ${full}`,
    ariaLabel: `${context}: ${display} (full precision ${full})`,
  }
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

export function formatDisplayExpression(
  expr: string,
  maxLen = 60,
): { text: string; truncated: boolean } {
  const replaced = displayOperators(expr)
  if (replaced.length > maxLen) {
    return { text: replaced.slice(0, maxLen) + "\u2026", truncated: true }
  }
  return { text: replaced, truncated: false }
}

// Shared CSS used throughout the calculation hero for numeric alignment.
export const tabularNums: React.CSSProperties = { fontVariantNumeric: "tabular-nums" }
