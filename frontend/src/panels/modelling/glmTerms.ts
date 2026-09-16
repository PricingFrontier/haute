import { isNumericDtype } from "../../utils/polarsDtypes"
import type { ModellingColumn } from "./featureSelection"

export type TermSpec = { type: string; [key: string]: unknown }
export type Terms = Record<string, TermSpec>
export type TermEntry = { key: string; spec: TermSpec }
export type InteractionSpec = {
  factors: string[]
  specs?: Record<string, TermSpec>
  include_main: boolean
}

export type NativeTermType = "linear" | "categorical" | "bs" | "ns" | "ms" | "target_encoding"

export const NATIVE_TERM_TYPES: readonly { value: NativeTermType; label: string }[] = [
  { value: "linear", label: "Linear" },
  { value: "categorical", label: "Categorical" },
  { value: "bs", label: "B-spline" },
  { value: "ns", label: "Nat. spline" },
  { value: "ms", label: "Monotone spline" },
  { value: "target_encoding", label: "Target enc." },
]

/** Keys the builder edits per type; mirrors the backend HAUTE_SUBSET test. */
export const TERM_TYPE_PROPS: Record<string, readonly string[]> = {
  linear: ["monotonicity"],
  categorical: [],
  bs: ["df", "degree", "monotonicity"],
  ns: ["df"],
  ms: ["df", "degree", "monotonicity"],
  target_encoding: ["prior_weight"],
  expression: ["expr", "monotonicity"],
}

export const EXPRESSION_GRAMMAR =
  "Supported forms: x ** n, x + y, x - y, x * y, x / y (y a column or a number), or a bare column x."

const EXPRESSION_RE = /^\s*([A-Za-z_]\w*)\s*(?:(\*\*|[+\-*/])\s*([A-Za-z_]\w*|\d+(?:\.\d+)?))?\s*$/
const NUMBER_RE = /^\d+(?:\.\d+)?$/

export function expressionIdentifiers(expr: string): string[] | null {
  const match = EXPRESSION_RE.exec(expr ?? "")
  if (!match) return null
  const [, left, , right] = match
  const identifiers = [left]
  if (right !== undefined && !NUMBER_RE.test(right) && right !== left) identifiers.push(right)
  return identifiers
}

/** A term's declared type, or null when the entry is not `{ type: string, ... }`.
 *
 *  Terms can arrive from a hand-edited JSON dict or a config saved by an older
 *  build, so nothing here may assume the entry is well formed — a type-less or
 *  non-object entry reads as a native term of unknown type rather than throwing
 *  and taking the whole editor down with it. */
export function specType(spec: TermSpec): string | null {
  if (typeof spec !== "object" || spec === null || Array.isArray(spec)) return null
  return typeof spec.type === "string" ? spec.type : null
}

export function isExpressionSpec(spec: TermSpec): boolean {
  return specType(spec) === "expression"
}

/** Whether a JSON terms dict entry is shaped like a term spec. */
export function isTermSpecShape(spec: unknown): boolean {
  return specType(spec as TermSpec) !== null
}

export function dtypeDefaultSpec(dtype: string): TermSpec {
  return isNumericDtype(dtype) ? { type: "linear" } : { type: "categorical" }
}

export function anchorColumn(spec: TermSpec, eligibleNames: ReadonlySet<string>): string | null {
  if (!isExpressionSpec(spec)) return null
  const identifiers = expressionIdentifiers(String(spec.expr ?? "")) ?? []
  return identifiers.find((name) => eligibleNames.has(name)) ?? null
}

export function termsByColumn(
  terms: Terms,
  eligibleNames: ReadonlySet<string>,
): { byColumn: Map<string, TermEntry[]>; unresolved: TermEntry[] } {
  const byColumn = new Map<string, TermEntry[]>()
  const unresolved: TermEntry[] = []
  const push = (column: string, entry: TermEntry, first: boolean) => {
    const list = byColumn.get(column) ?? []
    if (first) list.unshift(entry)
    else list.push(entry)
    byColumn.set(column, list)
  }
  for (const [key, spec] of Object.entries(terms)) {
    if (isExpressionSpec(spec)) continue
    push(key, { key, spec }, true)
  }
  for (const [key, spec] of Object.entries(terms)) {
    if (!isExpressionSpec(spec)) continue
    const column = anchorColumn(spec, eligibleNames)
    if (column === null) unresolved.push({ key, spec })
    else push(column, { key, spec }, false)
  }
  return { byColumn, unresolved }
}

export function nativeTermOf(terms: Terms, column: string): TermSpec | null {
  const spec = terms[column]
  if (spec === undefined || spec === null) return null
  return isExpressionSpec(spec) ? null : spec
}

export function uniqueExpressionName(
  column: string,
  terms: Terms,
  eligibleNames: ReadonlySet<string>,
): string {
  const taken = (name: string) => name in terms || eligibleNames.has(name)
  const base = `${column}_sq`
  if (!taken(base)) return base
  let suffix = 2
  while (taken(`${base}${suffix}`)) suffix += 1
  return `${base}${suffix}`
}

export function addTerm(
  terms: Terms,
  column: string,
  dtype: string,
  eligibleNames: ReadonlySet<string>,
): Terms {
  if (nativeTermOf(terms, column) === null) {
    return { ...terms, [column]: dtypeDefaultSpec(dtype) }
  }
  const name = uniqueExpressionName(column, terms, eligibleNames)
  return { ...terms, [name]: { type: "expression", expr: `${column} ** 2` } }
}

export function switchNativeType(terms: Terms, column: string, nextType: NativeTermType): Terms {
  const current = terms[column] ?? { type: "linear" }
  const next: TermSpec = { type: nextType }
  for (const key of TERM_TYPE_PROPS[nextType]) {
    if (current[key] !== undefined) next[key] = current[key]
  }
  if (nextType === "ms" && next.monotonicity === undefined) next.monotonicity = "increasing"
  return { ...terms, [column]: next }
}

export function setTermField(terms: Terms, key: string, field: string, value: unknown): Terms {
  const current = { ...(terms[key] ?? { type: "linear" }) }
  if (value === undefined || value === null || value === "") delete current[field]
  else current[field] = value
  return { ...terms, [key]: current }
}

export type EditResult = { ok: true; terms: Terms } | { ok: false; reason: string }

export function renameExpression(
  terms: Terms,
  key: string,
  nextKey: string,
  eligibleNames: ReadonlySet<string>,
): EditResult {
  const name = nextKey.trim()
  if (name === "") return { ok: false, reason: "Give the expression a name." }
  if (name === key) return { ok: true, terms }
  if (name in terms) return { ok: false, reason: `${name} is already a term.` }
  if (eligibleNames.has(name)) {
    return { ok: false, reason: `${name} is a column; expression names must not be columns.` }
  }
  const next: Terms = {}
  for (const [entryKey, spec] of Object.entries(terms)) next[entryKey === key ? name : entryKey] = spec
  return { ok: true, terms: next }
}

export function setExpression(
  terms: Terms,
  key: string,
  expr: string,
  eligibleNames: ReadonlySet<string>,
): EditResult {
  const identifiers = expressionIdentifiers(expr)
  if (identifiers === null) return { ok: false, reason: EXPRESSION_GRAMMAR }
  const unknown = identifiers.filter((name) => !eligibleNames.has(name))
  if (unknown.length > 0) return { ok: false, reason: `Unknown column: ${unknown.join(", ")}.` }
  if (identifiers.includes(key)) return { ok: false, reason: "An expression cannot be named after a column it reads." }
  return { ok: true, terms: { ...terms, [key]: { ...terms[key], type: "expression", expr: expr.trim() } } }
}

export function removeTerm(terms: Terms, key: string): Terms {
  const { [key]: _removed, ...rest } = terms
  void _removed
  return rest
}

export function fitAllWithDefaults(terms: Terms, eligible: readonly ModellingColumn[]): Terms {
  const next = { ...terms }
  for (const column of eligible) {
    if (nativeTermOf(next, column.name) === null) next[column.name] = dtypeDefaultSpec(column.dtype)
  }
  return next
}

export function filledFactors(interaction: InteractionSpec): string[] {
  return (interaction.factors ?? []).filter((factor) => typeof factor === "string" && factor !== "")
}

export function modelMembership(
  terms: Terms,
  interactions: readonly InteractionSpec[],
  eligibleNames: ReadonlySet<string>,
): { inModel: Set<string>; interactionOnly: Set<string> } {
  const fromTerms = new Set<string>()
  for (const [key, spec] of Object.entries(terms)) {
    if (isExpressionSpec(spec)) {
      for (const name of expressionIdentifiers(String(spec.expr ?? "")) ?? []) {
        if (eligibleNames.has(name)) fromTerms.add(name)
      }
    } else if (eligibleNames.has(key)) fromTerms.add(key)
  }
  const inModel = new Set(fromTerms)
  const interactionOnly = new Set<string>()
  for (const interaction of interactions) {
    for (const factor of filledFactors(interaction)) {
      if (!eligibleNames.has(factor)) continue
      inModel.add(factor)
      if (!fromTerms.has(factor)) interactionOnly.add(factor)
    }
  }
  return { inModel, interactionOnly }
}

// ── Interaction slots ──

export type SlotFit = "main" | "linear" | "categorical" | "bs" | "ns"
export type SlotFitOption = { value: SlotFit; label: string; disabledReason?: string }

export const MONOTONE_SLOT_REASON = "Monotone splines cannot be used inside interactions"
export const TARGET_ENCODED_SLOT_REASON = "Target-encoded features cannot be interacted in RustyStats"

export function slotNeedsExplicitFit(mainSpec: TermSpec | null): boolean {
  if (mainSpec === null) return false
  return mainSpec.type === "ms" || (mainSpec.type === "bs" && Boolean(mainSpec.monotonicity))
}

export function slotColumnBlockedReason(mainSpec: TermSpec | null): string | null {
  return mainSpec?.type === "target_encoding" ? TARGET_ENCODED_SLOT_REASON : null
}

export function slotFitOptions(dtype: string, mainSpec: TermSpec | null): SlotFitOption[] {
  const numeric = isNumericDtype(dtype)
  const mainIsCategorical = mainSpec?.type === "categorical"
  const numericReason = !numeric
    ? "Not available for string columns"
    : mainIsCategorical
      ? "Not available over a categorical main term"
      : undefined
  const mainReason =
    mainSpec === null ? "No main term" : slotNeedsExplicitFit(mainSpec) ? MONOTONE_SLOT_REASON : undefined
  const categoricalReason =
    mainSpec !== null && !mainIsCategorical
      ? `Would re-type the ${mainSpec.type} main term; keep it categorical or pick another fit`
      : undefined
  return [
    { value: "main", label: "As main term", disabledReason: mainReason },
    { value: "linear", label: "Linear", disabledReason: numericReason },
    { value: "categorical", label: "Categorical", disabledReason: categoricalReason },
    { value: "bs", label: "B-spline", disabledReason: numericReason },
    { value: "ns", label: "Nat. spline", disabledReason: numericReason },
  ]
}

export function effectiveSlotSpec(
  dtype: string,
  mainSpec: TermSpec | null,
  override: TermSpec | undefined,
): TermSpec {
  if (override) return override
  if (mainSpec) return mainSpec
  return dtypeDefaultSpec(dtype)
}

export function duplicateInteractionIndexes(interactions: readonly InteractionSpec[]): Set<number> {
  const seen = new Map<string, number>()
  const duplicates = new Set<number>()
  interactions.forEach((interaction, index) => {
    const factors = filledFactors(interaction)
    if (factors.length < 2) return
    const key = [...factors].sort().join("\u0000")
    if (seen.has(key)) duplicates.add(index)
    else seen.set(key, index)
  })
  return duplicates
}

function withoutOverride(interaction: InteractionSpec, column: string): InteractionSpec {
  if (!interaction.specs || !(column in interaction.specs)) return interaction
  const { [column]: _removed, ...rest } = interaction.specs
  void _removed
  const next: InteractionSpec = { ...interaction }
  if (Object.keys(rest).length > 0) next.specs = rest
  else delete next.specs
  return next
}

function replaceAt(
  interactions: readonly InteractionSpec[],
  index: number,
  update: (interaction: InteractionSpec) => InteractionSpec,
): InteractionSpec[] {
  return interactions.map((interaction, i) => (i === index ? update(interaction) : interaction))
}

export function addInteraction(interactions: readonly InteractionSpec[]): InteractionSpec[] {
  return [...interactions, { factors: ["", ""], include_main: true }]
}

export function removeInteraction(interactions: readonly InteractionSpec[], index: number): InteractionSpec[] {
  return interactions.filter((_, i) => i !== index)
}

export function setIncludeMain(
  interactions: readonly InteractionSpec[],
  index: number,
  value: boolean,
): InteractionSpec[] {
  return replaceAt(interactions, index, (interaction) => ({ ...interaction, include_main: value }))
}

export function pickSlotColumn(
  interactions: readonly InteractionSpec[],
  index: number,
  slot: number,
  column: string,
): InteractionSpec[] {
  return replaceAt(interactions, index, (interaction) => {
    const previous = interaction.factors[slot] ?? ""
    const factors = [...interaction.factors]
    factors[slot] = column
    const cleared = previous !== "" && previous !== column ? withoutOverride(interaction, previous) : interaction
    return { ...cleared, factors }
  })
}

export function setSlotOverride(
  interactions: readonly InteractionSpec[],
  index: number,
  column: string,
  override: TermSpec | null,
): InteractionSpec[] {
  return replaceAt(interactions, index, (interaction) => {
    if (override === null) return withoutOverride(interaction, column)
    return { ...interaction, specs: { ...(interaction.specs ?? {}), [column]: override } }
  })
}

export function addSlot(interactions: readonly InteractionSpec[], index: number): InteractionSpec[] {
  return replaceAt(interactions, index, (interaction) => ({
    ...interaction,
    factors: [...interaction.factors, ""],
  }))
}

export function removeSlot(
  interactions: readonly InteractionSpec[],
  index: number,
  slot: number,
): InteractionSpec[] {
  return replaceAt(interactions, index, (interaction) => {
    if (interaction.factors.length <= 2) return interaction
    const removed = interaction.factors[slot] ?? ""
    const factors = interaction.factors.filter((_, i) => i !== slot)
    const cleared = removed !== "" ? withoutOverride(interaction, removed) : interaction
    return { ...cleared, factors }
  })
}
