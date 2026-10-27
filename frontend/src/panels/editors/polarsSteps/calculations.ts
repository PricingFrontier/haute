/**
 * The calculations an Add column step is computed as: the entries of the
 * "Computed as" select in its General, Number, Text and Dates sections, each
 * named as a card is titled. The type conversions and fill_null are not
 * listed: Change types and Fill missing values are where they are chosen, and
 * a formula still types them.
 */
import type { LucideIcon } from "lucide-react"

import { FUNCTIONS, defaultArgFor, defaultExpr, kindLabel } from "./catalogue"
import { formulaText, typedAsFormula } from "./formula"
import { calculationIcon, STEP_ICONS } from "./stepIcons"
import type { Expr, FunctionName, Operand, Step } from "./types"

export type CalculationSection = "General" | "Number" | "Text" | "Dates"
export type CalculationId = "formula" | "if_then" | "window" | "sum_across" | "join_text" | "value" | FunctionName
/** `name` titles a card; the list shows `qualifier` after it where the name alone would mislead. */
export type Calculation = { id: CalculationId; section: CalculationSection; name: string; qualifier?: string }

export const CALCULATIONS: readonly Calculation[] = [
  { id: "formula", section: "General", name: "Formula" },
  { id: "if_then", section: "General", name: "If-then" },
  { id: "window", section: "General", name: "Window" },
  { id: "sum_across", section: "General", name: "Sum across" },
  { id: "join_text", section: "General", name: "Join text" },
  { id: "value", section: "General", name: "Value" },
  { id: "round", section: "Number", name: "Round" },
  { id: "clip", section: "Number", name: "Clip" },
  { id: "divide", section: "Number", name: "Divide", qualifier: "missing where the divisor is zero" },
  { id: "abs", section: "Number", name: "Absolute value" },
  { id: "floor", section: "Number", name: "Round down" },
  { id: "ceil", section: "Number", name: "Round up" },
  { id: "sqrt", section: "Number", name: "Square root" },
  { id: "log", section: "Number", name: "Natural log" },
  { id: "exp", section: "Number", name: "Exponential" },
  { id: "strip", section: "Text", name: "Trim whitespace" },
  { id: "lower", section: "Text", name: "Lower case" },
  { id: "upper", section: "Text", name: "Upper case" },
  { id: "replace", section: "Text", name: "Replace first match" },
  { id: "replace_all", section: "Text", name: "Replace all matches" },
  { id: "replace_regex", section: "Text", name: "Replace by pattern", qualifier: "regex" },
  { id: "slice", section: "Text", name: "Substring" },
  { id: "split_part", section: "Text", name: "Split and take part" },
  { id: "extract", section: "Text", name: "Extract pattern group", qualifier: "regex" },
  { id: "length", section: "Text", name: "Text length" },
  { id: "offset_by", section: "Dates", name: "Shift date" },
  { id: "total_days", section: "Dates", name: "Duration in days" },
  { id: "year", section: "Dates", name: "Year" },
  { id: "month", section: "Dates", name: "Month" },
  { id: "day", section: "Dates", name: "Day" },
  { id: "weekday", section: "Dates", name: "Weekday", qualifier: "1 = Monday" },
]

/** Functions a formula types that "Computed as" does not list. */
export const UNLISTED_FUNCTIONS: ReadonlySet<FunctionName> = new Set<FunctionName>([
  "cast", "try_cast", "to_date", "to_datetime", "try_to_date", "try_to_datetime", "fill_null",
])

const EXPR_CALCULATIONS = {
  binary: "formula",
  conditional: "if_then",
  window: "window",
  sum_horizontal: "sum_across",
  concat: "join_text",
  operand: "value",
} as const satisfies Record<Exclude<Expr["type"], "function">, CalculationId>

/**
 * The calculation an expression is. A typed formula is the Formula, and so is
 * an unlisted function a formula can show; one it cannot keeps its own entry.
 */
export function calculationOf(expr: Expr, variables: readonly string[] = [], constants: readonly string[] = []): CalculationId {
  if (typedAsFormula(expr)) return "formula"
  if (expr.type !== "function") return EXPR_CALCULATIONS[expr.type]
  if (UNLISTED_FUNCTIONS.has(expr.fn) && formulaText(expr, variables, constants) !== null) return "formula"
  return expr.fn
}

/** A calculation's entry; an unlisted function is named by what it does and listed under General. */
export function calculationEntry(id: CalculationId): Calculation {
  const listed = CALCULATIONS.find((calculation) => calculation.id === id)
  if (listed) return listed
  const label = FUNCTIONS.find((f) => f.value === id)?.label ?? id
  return { id, section: "General", name: label.charAt(0).toUpperCase() + label.slice(1) }
}

/** The entry as the select lists it: its name, then any qualifier in brackets. */
export function calculationLabel(calculation: Calculation): string {
  return calculation.qualifier ? `${calculation.name} (${calculation.qualifier})` : calculation.name
}

/** The value an expression computes from: a function's Of, a window's value, a sum across's or text join's first part, a Value's value. */
export function computedValue(expr: Expr): Operand | undefined {
  switch (expr.type) {
    case "function":
    case "operand":
      return expr.operand
    case "window":
      return expr.of
    case "sum_horizontal":
    case "concat":
      return expr.parts[0]
    default:
      return undefined
  }
}

const sameArgumentKinds = (a: readonly string[], b: readonly string[]) => a.length === b.length && a.every((kind, i) => kind === b[i])

/**
 * The expression a calculation starts as when chosen in place of `previous`.
 * The value previous computes from carries into the new value (a window's
 * only when it is a column or an expression, all a window takes), a function
 * keeps previous's arguments when they are of the same kinds, previous typed
 * as this very function keeps everything but its text, and Formula keeps
 * previous as its formula where a formula can express it. Nothing is wrapped.
 */
export function calculationExpr(id: CalculationId, previous?: Expr, variables: readonly string[] = [], constants: readonly string[] = []): Expr {
  const value = previous ? computedValue(previous) : undefined
  const empty: Operand = { kind: "column", name: "" }
  switch (id) {
    case "formula": {
      const text = previous && previous.type !== "conditional" && previous.type !== "window" && previous.type !== "concat" ? formulaText(previous, variables, constants) : null
      return previous && text !== null ? ({ ...previous, text } as Expr) : defaultExpr("binary")
    }
    case "if_then":
      return defaultExpr("conditional")
    case "window":
      return { type: "window", agg: "sum", of: value?.kind === "column" || value?.kind === "expr" ? value : empty, over: [] }
    case "sum_across":
      return { type: "sum_horizontal", parts: [value ?? empty, empty] }
    case "join_text":
      return { type: "concat", parts: [value ?? empty, empty], separator: " " }
    case "value":
      return { type: "operand", operand: value ?? empty }
  }
  const spec = FUNCTIONS.find((f) => f.value === id)
  if (!spec) throw new Error(`Unknown calculation ${id}`)
  if (previous?.type === "function" && previous.fn === id) {
    const { text: _typed, ...structured } = previous
    return structured
  }
  const previousArgs = previous?.type === "function" ? FUNCTIONS.find((f) => f.value === previous.fn)?.args : undefined
  const args = previous?.type === "function" && previousArgs && sameArgumentKinds(previousArgs, spec.args) ? previous.args : spec.args.map(defaultArgFor)
  return { type: "function", fn: id, operand: value ?? empty, args }
}

/** A card's title: an Add column step's calculation, any other step's kind. */
export function stepTitle(step: Step): string {
  return step.kind === "with_column" ? calculationEntry(calculationOf(step.expr)).name : kindLabel(step.kind)
}

/** A card's icon, chosen as its title is. */
export function stepIcon(step: Step): LucideIcon {
  return step.kind === "with_column" ? calculationIcon(calculationEntry(calculationOf(step.expr))) : STEP_ICONS[step.kind]
}
