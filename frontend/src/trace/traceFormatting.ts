import type React from "react"

import { formatJsonSpecialValue } from "../utils/formatValue"

// ---------------------------------------------------------------------------
// Value formatting helpers shared across CalculationHero and its sub-components.
// Pure functions — no state, no side effects. Extracted from CalculationHero
// during the split so WaterfallChart / ExpressionChain / InputSourceTree can
// reuse them without duplicating logic or threading every helper through props.
// ---------------------------------------------------------------------------

export function formatSmartValue(v: unknown): string {
  const special = formatJsonSpecialValue(v)
  if (special !== null) return special
  if (v === null || v === undefined) return "null"
  if (typeof v !== "number") return String(v)
  if (Number.isNaN(v)) return "NaN"
  if (!Number.isFinite(v)) return String(v)
  if (Number.isInteger(v)) return v.toLocaleString("en-US")
  const abs = Math.abs(v)
  if (abs < 10 && abs > 0) {
    return v.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 4 })
  }
  return v.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

export function formatResultValue(v: unknown, precision?: number): string {
  const special = formatJsonSpecialValue(v)
  if (special !== null) return special
  if (v === null || v === undefined) return "null"
  if (typeof v === "number") {
    if (Number.isNaN(v)) return "NaN"
    if (!Number.isFinite(v)) return String(v)
    if (Number.isInteger(v)) return v.toLocaleString("en-US")
    if (precision !== undefined) {
      return v.toLocaleString("en-US", { minimumFractionDigits: 0, maximumFractionDigits: precision })
    }
    return String(v)
  }
  if (typeof v === "string") return `"${v}"`
  if (typeof v === "object") return JSON.stringify(v)
  return String(v)
}

export function formatResultValueFull(v: unknown): string {
  return formatResultValue(v)
}

export function formatResultValue2dp(v: unknown): string {
  return formatResultValue(v, 2)
}

export function formatDisplayExpression(
  expr: string,
  maxLen = 60,
): { text: string; truncated: boolean } {
  const replaced = expr.replace(/\*/g, "\u00d7").replace(/\//g, "\u00f7")
  if (replaced.length > maxLen) {
    return { text: replaced.slice(0, maxLen) + "\u2026", truncated: true }
  }
  return { text: replaced, truncated: false }
}

// Shared CSS used throughout the calculation hero for numeric alignment.
export const tabularNums: React.CSSProperties = { fontVariantNumeric: "tabular-nums" }
