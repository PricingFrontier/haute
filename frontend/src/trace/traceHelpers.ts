/**
 * Pure data-transformation helpers + types shared across the trace
 * sub-components (CalculationHero, WaterfallChart, ExpressionChain,
 * InputSourceTree).
 *
 * Kept out of the `.tsx` component files so that `react-refresh/only-export
 * -components` passes — the rule requires a given component file to export
 * only components. Helpers that build or reshape trace data live here.
 */

import { formatDisplayExpression } from "./traceFormatting"

// ---------------------------------------------------------------------------
// Waterfall
// ---------------------------------------------------------------------------

export interface WaterfallStep {
  name: string
  factor: number
  runningValue: number
  prevValue: number
  direction: "positive" | "negative" | "neutral"
  defaultUsed?: boolean
}

export interface WaterfallEntryProp {
  label: string
  operation: string
  value: number
  delta: number
  cumulative: number
  default_used?: boolean
}

export interface WaterfallErrorProp {
  error: string
  error_type?: string
}

/**
 * Build waterfall steps from an expression of the form
 *   `a * b * c * ...` and a matching `inputValues` map.
 *
 * Returns `null` when the expression is not a multiplicative chain of 3+
 * factors or when any referenced factor is missing / non-numeric. This is
 * a "feature not applicable" signal, NOT a data-missing error — the caller
 * falls back to the unified-box renderer.
 */
export function buildWaterfallSteps(
  inputValues: Record<string, unknown>,
  expressionText: string,
): WaterfallStep[] | null {
  const parts = expressionText.split(/\s*\*\s*/)
  // Not applicable: need at least 3 factors for a meaningful waterfall.
  if (parts.length < 3) return null

  const names = parts.map((p) => p.trim())
  const allNumeric = names.every(
    (n) => n in inputValues && typeof inputValues[n] === "number",
  )
  // Not applicable: can't build a numeric waterfall from non-numeric factors.
  if (!allNumeric) return null

  const steps: WaterfallStep[] = []
  let running = inputValues[names[0]] as number

  steps.push({
    name: names[0],
    factor: running,
    runningValue: running,
    prevValue: 0,
    direction: "neutral",
  })

  for (let i = 1; i < names.length; i++) {
    const factor = inputValues[names[i]] as number
    const prev = running
    running = running * factor
    const dir = factor > 1 ? "positive" : factor < 1 ? "negative" : "neutral"
    steps.push({
      name: names[i],
      factor,
      runningValue: running,
      prevValue: prev,
      direction: dir,
    })
  }

  return steps
}

/**
 * Resolve the raw `waterfall` prop (from the backend trace response) into a
 * structured `{ steps, error }` pair so the orchestrator doesn't need to
 * re-implement the type-narrowing logic. Returns `{ steps: null, error: null }`
 * when no waterfall is available and the caller should fall back to the
 * default unified-box layout.
 */
export function resolveWaterfallProp(
  waterfallProp: WaterfallEntryProp[] | WaterfallErrorProp | null | undefined,
): { steps: WaterfallStep[] | null; error: WaterfallErrorProp | null } {
  if (!waterfallProp) return { steps: null, error: null }
  if (!Array.isArray(waterfallProp)) {
    if ("error" in waterfallProp) return { steps: null, error: waterfallProp }
    return { steps: null, error: null }
  }
  if (waterfallProp.length < 3) return { steps: null, error: null }
  const steps = waterfallProp.map((entry, i) => {
    const prevCumulative = i > 0 ? waterfallProp[i - 1].cumulative : 0
    return {
      name: entry.label,
      factor: entry.value,
      runningValue: entry.cumulative,
      prevValue: prevCumulative,
      direction: (entry.delta > 0
        ? "positive"
        : entry.delta < 0
          ? "negative"
          : "neutral") as "positive" | "negative" | "neutral",
      defaultUsed: entry.default_used,
    }
  })
  return { steps, error: null }
}

// ---------------------------------------------------------------------------
// Expression chain
// ---------------------------------------------------------------------------

export interface ExpressionChainEntry {
  expression_text: string
  target_column: string
  substituted_text?: string
  result_value?: unknown
  not_computable_reason?: string | null
  result_source?: string | null
}

export interface ChainBoxEntry {
  column: string
  formulaText: string | null
  substitutedText: string | null
  value: unknown
  source: string | null
  /** Why the value was not computed from the traced row, when it was not. */
  note?: string | null
}

interface EvaluatedValue {
  result_value?: unknown
  not_computable_reason?: string | null
  result_source?: string | null
}

const NOT_COMPUTABLE_KINDS: Record<string, (detail: string) => string> = {
  not_row_local: (detail) => `can depend on other rows (${detail})`,
  unresolved_name: (detail) => `uses ${detail}, which the trace cannot resolve`,
  column_unavailable: (detail) => `column ${detail} is not in the traced row`,
}

const NOT_COMPUTABLE_REASONS: Record<string, string> = {
  traced_row_unavailable: "the traced row could not be recovered",
  expression_not_located: "no formula assigns this column",
}

/** Human-readable text for a backend `not_computable_reason` code. */
export function describeNotComputable(reason: string): string {
  const separator = reason.indexOf(": ")
  if (separator > 0) {
    const describe = NOT_COMPUTABLE_KINDS[reason.slice(0, separator)]
    if (describe) return describe(reason.slice(separator + 2))
  }
  return NOT_COMPUTABLE_REASONS[reason] ?? reason
}

/**
 * The note shown beside a value the evaluator could not compute from the
 * traced row: either the traced run supplied it, or it is not shown at all.
 */
export function notComputableNote(entry: EvaluatedValue): string | null {
  const reason = entry.not_computable_reason
  if (!reason) return null
  const described = describeNotComputable(reason)
  return entry.result_source === "trace_execution"
    ? `From the traced run: ${described}`
    : `Not computed from this row: ${described}`
}

/** The entry's note as an optional field, absent when there is nothing to say. */
function noteOf(entry: EvaluatedValue): { note?: string } {
  const note = notComputableNote(entry)
  return note ? { note } : {}
}

/**
 * The value to show for an evaluated entry. A value the evaluator could not
 * compute is never replaced by the row's own input value, which would present
 * the pre-formula value as the formula's result; nor is a computed null. Only
 * an entry with no result at all falls back.
 */
export function evaluatedValue(entry: EvaluatedValue, fallback: unknown): unknown {
  if (entry.not_computable_reason && entry.result_source !== "trace_execution") return null
  return entry.result_value !== undefined ? entry.result_value : fallback
}

/**
 * Normalise raw expression-chain entries into the render-ready shape used
 * inside the unified calculation box. Skips the target column (its row is
 * rendered by the orchestrator as the final line). Returns an empty array
 * when the chain is absent or trivial (<= 1 entry).
 */
export function buildChainEntries(
  chain: ExpressionChainEntry[] | null | undefined,
  targetColumn: string,
  inputValues: Record<string, unknown>,
): ChainBoxEntry[] {
  if (!chain || chain.length <= 1) return []
  const out: ChainBoxEntry[] = []
  for (const entry of chain) {
    if (entry.target_column === targetColumn) continue
    const formulaText = entry.expression_text
      ? formatDisplayExpression(entry.expression_text).text
      : null
    const substitutedText = entry.substituted_text
      ? entry.substituted_text.replace(/\*/g, "\u00d7").replace(/\//g, "\u00f7")
      : null
    out.push({
      column: entry.target_column,
      formulaText,
      substitutedText,
      value: evaluatedValue(entry, inputValues[entry.target_column]),
      source: null,
      ...noteOf(entry),
    })
  }
  return out
}

// ---------------------------------------------------------------------------
// Input sources
// ---------------------------------------------------------------------------

export interface InputSourceEntry {
  node_name: string
  expression_text?: string
  substituted_text?: string
  result_value?: unknown
  not_computable_reason?: string | null
  result_source?: string | null
  input_sources?: Record<string, InputSourceEntry> | null
}

export interface InputSourceBoxEntry extends ChainBoxEntry {
  subSources: Record<string, InputSourceEntry> | null
}

/**
 * Normalise raw input_sources entries into the render-ready shape used
 * inside the unified calculation box. Skips any column whose name already
 * appears in `alreadyPresent` so expression-chain rows take precedence over
 * duplicate input-source rows for the same column.
 */
export function buildInputSourceEntries(
  inputSources: Record<string, InputSourceEntry> | null | undefined,
  inputValues: Record<string, unknown>,
  alreadyPresent: ReadonlySet<string>,
): InputSourceBoxEntry[] {
  if (!inputSources) return []
  const out: InputSourceBoxEntry[] = []
  for (const [column, src] of Object.entries(inputSources)) {
    if (alreadyPresent.has(column)) continue
    const formulaText = src.expression_text
      ? formatDisplayExpression(src.expression_text).text
      : null
    const substitutedText = src.substituted_text
      ? src.substituted_text.replace(/\*/g, "\u00d7").replace(/\//g, "\u00f7")
      : null
    out.push({
      column,
      formulaText,
      substitutedText,
      value: evaluatedValue(src, inputValues[column]),
      source: src.node_name,
      ...noteOf(src),
      subSources: src.input_sources ?? null,
    })
  }
  return out
}
