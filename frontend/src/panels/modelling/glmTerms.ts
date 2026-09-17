import type { ModellingColumn } from "./featureSelection"

/**
 * Pure GLM term editing helpers.
 *
 * Mirrors the backend contract in `src/haute/modelling/_glm_terms.py`: dtype
 * classes, the term parameter contract, the expression grammar, and the
 * order-independent interaction resolution. Shared fixtures pin the dtype
 * classes and the grammar to the backend.
 */

export type TermSpec = { type: string; [key: string]: unknown }
export type Terms = Record<string, TermSpec>
export type TermEntry = { key: string; spec: TermSpec }
export type InteractionSpec = {
  factors: string[]
  specs?: Record<string, TermSpec>
  include_main: boolean
  encoding?: "target_encoding" | "frequency_encoding"
  prior_weight?: number | "auto"
  n_permutations?: number
}

export type NativeTermType = "linear" | "categorical" | "bs" | "ns" | "ms" | "target_encoding" | "frequency_encoding"
export type EncodingTermType = "target_encoding" | "frequency_encoding"
export type AdditionalTermType = "expression" | EncodingTermType
export type TermTypeOption<T extends string> = { value: T; label: string }
export type EditResult = { ok: true; terms: Terms } | { ok: false; reason: string }

export const TERM_TYPE_LABELS: Readonly<Record<string, string>> = {
  linear: "Linear",
  categorical: "Categorical",
  bs: "B-spline",
  ns: "Nat. spline",
  ms: "Monotone spline",
  target_encoding: "Target enc.",
  frequency_encoding: "Frequency enc.",
  expression: "Expression",
}

const NATIVE_TYPE_ORDER: readonly NativeTermType[] = ["linear", "categorical", "bs", "ns", "ms", "target_encoding", "frequency_encoding"]
const ADDITIONAL_TYPE_ORDER: readonly AdditionalTermType[] = ["expression", "target_encoding", "frequency_encoding"]
export const SUPPORTED_TERM_TYPES: ReadonlySet<string> = new Set([...NATIVE_TYPE_ORDER, "expression"])

/** Keys each stored term type accepts; mirrors the backend TERM_KEYS. */
export const TERM_TYPE_PROPS: Readonly<Record<string, readonly string[]>> = {
  linear: ["monotonicity"],
  categorical: ["levels", "reference"],
  bs: ["df", "k", "degree", "monotonicity", "knots", "boundary_knots"],
  ns: ["df", "k", "knots", "boundary_knots"],
  ms: ["df", "k", "degree", "monotonicity", "knots", "boundary_knots"],
  target_encoding: ["variable", "prior_weight", "n_permutations"],
  frequency_encoding: ["variable"],
  expression: ["expr", "monotonicity"],
}

/** Keys each product-interaction slot override accepts. */
export const SLOT_OVERRIDE_PROPS: Readonly<Record<string, readonly string[]>> = {
  linear: [],
  categorical: [],
  bs: ["df", "k", "degree", "knots", "boundary_knots"],
  ns: ["df", "k", "knots", "boundary_knots"],
  target_encoding: ["prior_weight", "n_permutations"],
}

export const SPLINE_LIMITS = { maxBasis: 20, minDegree: 1, maxDegree: 5, defaultDegree: 3, maxKnots: 20 } as const
export const MAX_PERMUTATIONS = 100

export function typeLabel(type: string): string {
  return hasOwn(TERM_TYPE_LABELS, type) ? TERM_TYPE_LABELS[type] : type
}

// ── Own-property access ──

/** Own-property lookup, so a column named `constructor` is an ordinary key. */
export function hasOwn(record: object | undefined, key: string): boolean {
  return record !== undefined && Object.prototype.hasOwnProperty.call(record, key)
}

export function termAt(terms: Terms, key: string): TermSpec | undefined {
  return hasOwn(terms, key) ? terms[key] : undefined
}

// ── Column types ──

export type DtypeClass = "continuous" | "integer" | "boolean" | "categorical" | "unsupported"
type SupportedClass = Exclude<DtypeClass, "unsupported">

const DTYPE_CLASS_PATTERNS: readonly [SupportedClass, RegExp][] = [
  ["continuous", /^(?:float(?:16|32|64)|f(?:16|32|64)|decimal(?:\(.*\))?)$/is],
  ["integer", /^(?:u?int(?:8|16|32|64|128)|[iu](?:8|16|32|64|128))$/i],
  ["boolean", /^bool(?:ean)?$/i],
  ["categorical", /^(?:string|str|utf8|categorical(?:\(.*\))?|enum(?:\(.*\))?)$/is],
]

/** Classify a Polars dtype name for GLM fits; mirrors the backend `glm_dtype_class`. */
export function glmDtypeClass(dtype: string): DtypeClass {
  const name = dtype.trim()
  for (const [dtypeClass, pattern] of DTYPE_CLASS_PATTERNS) {
    if (pattern.test(name)) return dtypeClass
  }
  return "unsupported"
}

export const MAIN_FITS_BY_CLASS: Readonly<Record<DtypeClass, ReadonlySet<string>>> = {
  continuous: new Set(["linear", "bs", "ns", "ms"]),
  integer: new Set(["linear", "bs", "ns", "ms", "categorical", "target_encoding", "frequency_encoding"]),
  boolean: new Set(["categorical", "target_encoding", "frequency_encoding"]),
  categorical: new Set(["categorical", "target_encoding", "frequency_encoding"]),
  unsupported: new Set(),
}
export const SLOT_FITS_BY_CLASS: Readonly<Record<DtypeClass, ReadonlySet<string>>> = {
  continuous: new Set(["linear", "bs", "ns"]),
  integer: new Set(["linear", "bs", "ns", "categorical", "target_encoding"]),
  boolean: new Set(["categorical", "target_encoding"]),
  categorical: new Set(["categorical", "target_encoding"]),
  unsupported: new Set(),
}
export const DEFAULT_FIT_BY_CLASS: Readonly<Record<SupportedClass, NativeTermType>> = {
  continuous: "linear",
  integer: "linear",
  boolean: "categorical",
  categorical: "categorical",
}
const EXPRESSION_OPERAND_CLASSES: ReadonlySet<DtypeClass> = new Set(["continuous", "integer"])
const ENCODING_CLASSES: ReadonlySet<DtypeClass> = new Set(["integer", "boolean", "categorical"])
export const JOINT_ENCODING_CLASSES: ReadonlySet<DtypeClass> = ENCODING_CLASSES

export function dtypeDefaultSpec(dtype: string): TermSpec {
  const dtypeClass = glmDtypeClass(dtype)
  return { type: dtypeClass === "unsupported" ? "linear" : DEFAULT_FIT_BY_CLASS[dtypeClass] }
}

// ── Column context ──

export type ColumnContext = {
  byName: ReadonlyMap<string, ModellingColumn>
  roles: ReadonlyMap<string, string>
  eligible: readonly ModellingColumn[]
  eligibleNames: ReadonlySet<string>
  unsupported: readonly ModellingColumn[]
  /** Every upstream column name; generated term names avoid them. */
  upstreamNames: ReadonlySet<string>
}

export function columnContext(columns: readonly ModellingColumn[], roles: ReadonlyMap<string, string>): ColumnContext {
  const byName = new Map(columns.map((column) => [column.name, column]))
  const candidates = columns.filter((column) => !roles.has(column.name))
  const eligible = candidates.filter((column) => glmDtypeClass(column.dtype) !== "unsupported")
  return {
    byName,
    roles,
    eligible,
    eligibleNames: new Set(eligible.map((column) => column.name)),
    unsupported: candidates.filter((column) => glmDtypeClass(column.dtype) === "unsupported"),
    upstreamNames: new Set(columns.map((column) => column.name)),
  }
}

/** Why a column cannot carry GLM fits, or null when it is eligible. */
export function columnIssue(column: string, context: ColumnContext): string | null {
  const role = context.roles.get(column)
  if (role !== undefined) return `${column} is the ${role} column`
  const info = context.byName.get(column)
  if (info === undefined) return `${column} is not in the upstream data`
  if (glmDtypeClass(info.dtype) === "unsupported") return `${column} has dtype ${info.dtype}, which GLM fits do not support`
  return null
}

// ── Expression grammar ──

export const EXPRESSION_GRAMMAR =
  "Supported forms: x ** n, x + y, x - y, x * y, x / y (y a column or a number), or a bare column x. " +
  "Columns must be named with letters, digits, and underscores, starting with a letter or underscore. " +
  "Compute log and other transforms in an upstream Polars node."

const EXPRESSION_RE = /^\s*([\p{L}\p{N}_]+)\s*(?:(\*\*|[+\-*/])\s*([0-9]+\.[0-9]+|[\p{L}\p{N}_]+))?\s*$/u
const IDENTIFIER_RE = /^[\p{L}_][\p{L}\p{N}_]*$/u
const NUMBER_RE = /^[0-9]+(?:\.[0-9]+)?$/
const FLOAT_WORD_RE = /^(?:inf|infinity|nan)$/i

export function isExpressionIdentifier(name: string): boolean {
  return IDENTIFIER_RE.test(name)
}

/** The column identifiers an expression reads, or null when it is outside the grammar. */
export function expressionIdentifiers(expr: string): string[] | null {
  const match = EXPRESSION_RE.exec(typeof expr === "string" ? expr : "")
  if (!match) return null
  const [, left, operator, right] = match
  if (!isExpressionIdentifier(left)) return null
  if (operator === undefined || NUMBER_RE.test(right)) return [left]
  if (!isExpressionIdentifier(right) || FLOAT_WORD_RE.test(right)) return null
  return right === left ? [left] : [left, right]
}

// ── Term classification ──

/** A term's declared type string, or null when the entry is not `{ type: string }`. */
export function specType(spec: unknown): string | null {
  if (typeof spec !== "object" || spec === null || Array.isArray(spec)) return null
  const type = (spec as Record<string, unknown>).type
  return typeof type === "string" ? type : null
}

export function isSupportedSpec(spec: unknown): spec is TermSpec {
  const type = specType(spec)
  return type !== null && SUPPORTED_TERM_TYPES.has(type)
}

export function isExpressionSpec(spec: unknown): boolean {
  return specType(spec) === "expression"
}

export function isEncodingSpec(spec: unknown): spec is TermSpec & { type: EncodingTermType } {
  const type = specType(spec)
  return type === "target_encoding" || type === "frequency_encoding"
}

/** An expression or an encoding keyed by a free name with a `variable` source. */
export function isAdditionalSpec(spec: unknown, key: string): boolean {
  return isExpressionSpec(spec) || (isEncodingSpec(spec) && typeof spec.variable === "string" && spec.variable !== key)
}

/** The column a native or encoding term fits; null for expressions and malformed entries. */
export function termSource(key: string, spec: unknown): string | null {
  if (!isSupportedSpec(spec) || isExpressionSpec(spec)) return null
  if (isEncodingSpec(spec) && typeof spec.variable === "string") return spec.variable
  return key
}

/** Whether a JSON terms dict entry is shaped like a term spec. */
export function isTermSpecShape(spec: unknown): boolean {
  return specType(spec) !== null
}

export function nativeTermOf(terms: Terms, column: string): TermSpec | null {
  const spec = termAt(terms, column)
  if (spec === undefined || !isSupportedSpec(spec) || isAdditionalSpec(spec, column)) return null
  return spec
}

/** Keys of every main-effect term fitting *column*: its native term and encodings naming it. */
export function mainEffectKeys(terms: Terms, column: string): string[] {
  return Object.entries(terms)
    .filter(([key, spec]) => termSource(key, spec) === column)
    .map(([key]) => key)
}

// ── Term placement ──

export type UnresolvedTerm = TermEntry & { reason: string; malformed: boolean }

export function termsByColumn(
  terms: Terms,
  context: ColumnContext,
): { byColumn: Map<string, TermEntry[]>; unresolved: UnresolvedTerm[] } {
  const byColumn = new Map<string, TermEntry[]>()
  const unresolved: UnresolvedTerm[] = []
  const place = (column: string, entry: TermEntry, first: boolean) => {
    const list = byColumn.get(column) ?? []
    if (first) list.unshift(entry)
    else list.push(entry)
    byColumn.set(column, list)
  }
  for (const [key, spec] of Object.entries(terms)) {
    if (!isSupportedSpec(spec)) {
      const type = specType(spec)
      const reason = type === null ? "This entry has no fit type" : `Fit type ${type} is not supported`
      unresolved.push({ key, spec: spec as TermSpec, reason, malformed: true })
      continue
    }
    if (isExpressionSpec(spec)) {
      const identifiers = expressionIdentifiers(String(spec.expr ?? ""))
      if (identifiers === null) {
        unresolved.push({ key, spec, reason: "The expression is outside the supported grammar", malformed: false })
        continue
      }
      const issue = identifiers.map((name) => expressionOperandIssue(name, context)).find((value) => value !== null)
      if (issue) {
        unresolved.push({ key, spec, reason: issue, malformed: false })
        continue
      }
      if (context.upstreamNames.has(key)) {
        unresolved.push({ key, spec, reason: `The name ${key} is an upstream column; rename the expression`, malformed: false })
        continue
      }
      place(identifiers[0], { key, spec }, false)
      continue
    }
    const source = termSource(key, spec)
    if (source === null || source === "") {
      unresolved.push({ key, spec, reason: "The encoding names no source column", malformed: false })
      continue
    }
    const issue = columnIssue(source, context)
    if (issue !== null) {
      unresolved.push({ key, spec, reason: issue, malformed: false })
      continue
    }
    if (source !== key && context.upstreamNames.has(key)) {
      unresolved.push({ key, spec, reason: `The name ${key} is an upstream column; rename the encoding`, malformed: false })
      continue
    }
    place(source, { key, spec }, source === key)
  }
  return { byColumn, unresolved }
}

function expressionOperandIssue(name: string, context: ColumnContext): string | null {
  const issue = columnIssue(name, context)
  if (issue !== null) return issue
  const info = context.byName.get(name)
  const dtypeClass = glmDtypeClass(info?.dtype ?? "")
  return EXPRESSION_OPERAND_CLASSES.has(dtypeClass) ? null : `${name} is a ${dtypeClass} column; expressions need numbers`
}

export function modelMembership(
  terms: Terms,
  interactions: readonly InteractionSpec[],
  context: ColumnContext,
): { inModel: Set<string>; interactionOnly: Set<string> } {
  const fromTerms = new Set<string>()
  for (const [key, spec] of Object.entries(terms)) {
    if (isExpressionSpec(spec)) {
      for (const name of expressionIdentifiers(String(spec.expr ?? "")) ?? []) {
        if (context.eligibleNames.has(name)) fromTerms.add(name)
      }
      continue
    }
    const source = termSource(key, spec)
    if (source !== null && context.eligibleNames.has(source)) fromTerms.add(source)
  }
  const inModel = new Set(fromTerms)
  const interactionOnly = new Set<string>()
  for (const interaction of interactions) {
    for (const factor of filledFactors(interaction)) {
      if (!context.eligibleNames.has(factor)) continue
      inModel.add(factor)
      if (!fromTerms.has(factor)) interactionOnly.add(factor)
    }
  }
  return { inModel, interactionOnly }
}

// ── Type options ──

export function encodingTypesForColumn(terms: Terms, column: string, exceptKey?: string): Set<EncodingTermType> {
  const used = new Set<EncodingTermType>()
  for (const [key, spec] of Object.entries(terms)) {
    if (key === exceptKey || !isEncodingSpec(spec)) continue
    if (termSource(key, spec) === column) used.add(spec.type)
  }
  return used
}

export function nativeTypeOptions(terms: Terms, column: string, dtype: string): TermTypeOption<NativeTermType>[] {
  const allowed = MAIN_FITS_BY_CLASS[glmDtypeClass(dtype)]
  const used = encodingTypesForColumn(terms, column, column)
  const current = specType(termAt(terms, column))
  return NATIVE_TYPE_ORDER.filter((type) =>
    type === current || (allowed.has(type) && !used.has(type as EncodingTermType)),
  ).map((value) => ({ value, label: typeLabel(value) }))
}

function expressionAllowed(column: string, dtype: string): boolean {
  return EXPRESSION_OPERAND_CLASSES.has(glmDtypeClass(dtype)) && isExpressionIdentifier(column)
}

export function additionalTypeOptions(terms: Terms, key: string, column: string, dtype: string): TermTypeOption<AdditionalTermType>[] {
  const used = encodingTypesForColumn(terms, column, key)
  const current = specType(termAt(terms, key))
  const encodings = ENCODING_CLASSES.has(glmDtypeClass(dtype))
  return ADDITIONAL_TYPE_ORDER.filter((type) => {
    if (type === current) return true
    if (type === "expression") return expressionAllowed(column, dtype)
    return encodings && !used.has(type)
  }).map((value) => ({ value, label: typeLabel(value) }))
}

// ── Names ──

function uniqueName(base: string, terms: Terms, taken: ReadonlySet<string>): string {
  const isTaken = (name: string) => hasOwn(terms, name) || taken.has(name)
  if (!isTaken(base)) return base
  let suffix = 2
  while (isTaken(`${base}${suffix}`)) suffix += 1
  return `${base}${suffix}`
}

export function uniqueExpressionName(column: string, terms: Terms, taken: ReadonlySet<string>): string {
  return uniqueName(`${column}_sq`, terms, taken)
}

export function uniqueEncodingName(column: string, type: EncodingTermType, terms: Terms, taken: ReadonlySet<string>): string {
  return uniqueName(`${column}_${type === "target_encoding" ? "te" : "fe"}`, terms, taken)
}

// ── Editor transitions ──

/** Whether Add term has something to add, and why not when it does not. */
export function addTermAvailability(terms: Terms, column: string, dtype: string): { ok: true } | { ok: false; reason: string } {
  if (nativeTermOf(terms, column) === null) return { ok: true }
  if (expressionAllowed(column, dtype)) return { ok: true }
  if (ENCODING_CLASSES.has(glmDtypeClass(dtype)) && encodingTypesForColumn(terms, column).size < 2) return { ok: true }
  if (EXPRESSION_OPERAND_CLASSES.has(glmDtypeClass(dtype)) && !isExpressionIdentifier(column)) {
    return {
      ok: false,
      reason: `${column} cannot appear in an expression: rename it upstream to letters, digits, and underscores.`,
    }
  }
  return { ok: false, reason: `${column} already has every additional fit it supports.` }
}

export function addTerm(terms: Terms, column: string, dtype: string, taken: ReadonlySet<string>): Terms {
  if (nativeTermOf(terms, column) === null && !hasOwn(terms, column)) {
    return { ...terms, [column]: dtypeDefaultSpec(dtype) }
  }
  if (expressionAllowed(column, dtype)) {
    const name = uniqueExpressionName(column, terms, taken)
    return { ...terms, [name]: { type: "expression", expr: `${column} ** 2` } }
  }
  if (ENCODING_CLASSES.has(glmDtypeClass(dtype))) {
    const used = encodingTypesForColumn(terms, column)
    const type = (["target_encoding", "frequency_encoding"] as const).find((candidate) => !used.has(candidate))
    if (type === undefined) return terms
    const name = uniqueEncodingName(column, type, terms, taken)
    return { ...terms, [name]: { type, variable: column } }
  }
  return terms
}

function copySupportedKeys(current: TermSpec | undefined, nextType: string, into: TermSpec): TermSpec {
  if (current === undefined) return into
  for (const key of hasOwn(TERM_TYPE_PROPS, nextType) ? TERM_TYPE_PROPS[nextType] : []) {
    if (key !== "variable" && key !== "expr" && hasOwn(current, key) && current[key] !== undefined) into[key] = current[key]
  }
  return into
}

export function switchNativeType(terms: Terms, column: string, nextType: NativeTermType): Terms {
  const current = termAt(terms, column)
  const next = copySupportedKeys(isSupportedSpec(current) ? current : undefined, nextType, { type: nextType })
  if (isEncodingSpec(current) && typeof current.variable === "string" && (nextType === "target_encoding" || nextType === "frequency_encoding")) {
    next.variable = current.variable
  }
  if (nextType === "ms" && next.monotonicity === undefined) next.monotonicity = "increasing"
  return { ...terms, [column]: next }
}

export function switchAdditionalType(
  terms: Terms,
  key: string,
  column: string,
  dtype: string,
  nextType: AdditionalTermType,
): EditResult {
  const current = termAt(terms, key)
  if (current === undefined) return { ok: false, reason: "This term no longer exists." }
  if (specType(current) === nextType) return { ok: true, terms }
  if (nextType === "expression") {
    if (!expressionAllowed(column, dtype)) {
      return { ok: false, reason: "Expressions need a numeric column named with letters, digits, and underscores." }
    }
    return { ok: true, terms: { ...terms, [key]: { type: "expression", expr: `${column} ** 2` } } }
  }
  if (!ENCODING_CLASSES.has(glmDtypeClass(dtype))) {
    return { ok: false, reason: "Encodings are available for integer, boolean, and categorical features." }
  }
  if (encodingTypesForColumn(terms, column, key).has(nextType)) {
    return { ok: false, reason: `This feature already has ${nextType === "target_encoding" ? "a target" : "a frequency"} encoding.` }
  }
  const next = copySupportedKeys(current, nextType, { type: nextType, variable: column })
  return { ok: true, terms: { ...terms, [key]: next } }
}

/** Write a non-mode field; exclusive smoothing settings and level settings replace each other. */
export function setTermField(terms: Terms, key: string, field: string, value: unknown): Terms {
  const existing = termAt(terms, key)
  const current: TermSpec = { ...(isSupportedSpec(existing) ? existing : { type: "linear" }) }
  const clearing = value === undefined || value === null || value === ""
  if (!clearing) {
    const exclusive: Record<string, readonly string[]> = {
      df: ["k", "knots"],
      k: ["df", "knots"],
      knots: ["df", "k"],
      levels: ["reference"],
      reference: ["levels"],
    }
    for (const other of exclusive[field] ?? []) delete current[other]
  }
  if (clearing) delete current[field]
  else current[field] = value
  return { ...terms, [key]: current }
}

export type SplineMode = "auto" | "fixed"

export function splineMode(spec: TermSpec): SplineMode {
  return spec.df !== undefined || spec.knots !== undefined ? "fixed" : "auto"
}

/** Auto removes df and knots (keeping k); Fixed writes df and removes k and knots. */
export function withSplineMode(spec: TermSpec, mode: SplineMode): TermSpec {
  const next: TermSpec = { ...spec }
  delete next.df
  delete next.knots
  if (mode === "fixed") {
    delete next.k
    const degree = typeof spec.degree === "number" ? spec.degree : SPLINE_LIMITS.defaultDegree
    next.df = spec.type === "bs" ? Math.max(5, degree + 1) : 5
  }
  return next
}

export function setSplineMode(terms: Terms, key: string, mode: SplineMode): Terms {
  const current = termAt(terms, key)
  if (!isSupportedSpec(current)) return terms
  return { ...terms, [key]: withSplineMode(current, mode) }
}

export function renameTerm(terms: Terms, key: string, nextKey: string, taken: ReadonlySet<string>): EditResult {
  const name = nextKey.trim()
  if (name === "") return { ok: false, reason: "Give the term a name." }
  if (name === key) return { ok: true, terms }
  if (hasOwn(terms, name)) return { ok: false, reason: `${name} is already a term.` }
  if (taken.has(name)) return { ok: false, reason: `${name} is a column; term names must not be columns.` }
  const next: Terms = {}
  for (const [entryKey, spec] of Object.entries(terms)) next[entryKey === key ? name : entryKey] = spec
  return { ok: true, terms: next }
}

export function setExpression(terms: Terms, key: string, expr: string, context: ColumnContext): EditResult {
  const identifiers = expressionIdentifiers(expr)
  if (identifiers === null) return { ok: false, reason: EXPRESSION_GRAMMAR }
  const issue = identifiers.map((name) => expressionOperandIssue(name, context)).find((value) => value !== null)
  if (issue) return { ok: false, reason: `${issue}.` }
  if (identifiers.includes(key)) return { ok: false, reason: "An expression cannot be named after a column it reads." }
  const current = termAt(terms, key) ?? { type: "expression" }
  return { ok: true, terms: { ...terms, [key]: { ...current, type: "expression", expr: expr.trim() } } }
}

export function removeTerm(terms: Terms, key: string): Terms {
  const next: Terms = {}
  for (const [entryKey, spec] of Object.entries(terms)) {
    if (entryKey !== key) next[entryKey] = spec
  }
  return next
}

/** Replace a malformed entry keyed by an eligible column with a valid native fit. */
export function repairMalformedTerm(terms: Terms, column: string, type: NativeTermType): Terms {
  return switchNativeType({ ...terms, [column]: { type } }, column, type)
}

export function fitAllWithDefaults(terms: Terms, context: ColumnContext): Terms {
  const next = { ...terms }
  for (const column of context.eligible) {
    if (!hasOwn(next, column.name)) next[column.name] = dtypeDefaultSpec(column.dtype)
  }
  return next
}

// ── Parameter contract ──

function isInteger(value: unknown): value is number {
  return typeof value === "number" && Number.isInteger(value)
}

function isFiniteNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value)
}

export function minimumBasis(type: string, degree: unknown): number {
  const resolved = isInteger(degree) ? degree : SPLINE_LIMITS.defaultDegree
  return type === "bs" ? resolved + 1 : 2
}

/** Parameter problems the backend would refuse; mirrors the backend term contract. */
export function termSpecIssues(spec: TermSpec): string[] {
  const issues: string[] = []
  const type = spec.type
  if (type === "bs" || type === "ns" || type === "ms") {
    const chosen = ["df", "k", "knots"].filter((key) => spec[key] !== undefined)
    if (chosen.length > 1) issues.push(`Set only one of df, k, or knots (found ${chosen.join(", ")}).`)
    if (spec.degree !== undefined && !(isInteger(spec.degree) && spec.degree >= SPLINE_LIMITS.minDegree && spec.degree <= SPLINE_LIMITS.maxDegree)) {
      issues.push(`Degree must be an integer from ${SPLINE_LIMITS.minDegree} to ${SPLINE_LIMITS.maxDegree}.`)
    }
    const minimum = minimumBasis(type, spec.degree)
    for (const key of ["df", "k"]) {
      const value = spec[key]
      if (value !== undefined && !(isInteger(value) && value >= minimum && value <= SPLINE_LIMITS.maxBasis)) {
        issues.push(`${key} must be an integer from ${minimum} to ${SPLINE_LIMITS.maxBasis}.`)
      }
    }
    const knots = spec.knots
    let validKnots: number[] | null = null
    if (knots !== undefined) {
      if (Array.isArray(knots) && knots.length >= 1 && knots.length <= SPLINE_LIMITS.maxKnots && knots.every(isFiniteNumber) && knots.every((value, index) => index === 0 || value > knots[index - 1])) {
        validKnots = knots
      } else {
        issues.push(`Knots must be 1 to ${SPLINE_LIMITS.maxKnots} finite, strictly increasing numbers.`)
      }
    }
    const boundary = spec.boundary_knots
    if (boundary !== undefined) {
      if (!(Array.isArray(boundary) && boundary.length === 2 && boundary.every(isFiniteNumber) && boundary[1] > boundary[0])) {
        issues.push("Boundary knots must be exactly two finite, increasing numbers.")
      } else if (validKnots !== null && !(boundary[0] < validKnots[0] && validKnots[validKnots.length - 1] < boundary[1])) {
        issues.push("Boundary knots must enclose every knot.")
      }
    }
  }
  if (spec.monotonicity !== undefined && spec.monotonicity !== "increasing" && spec.monotonicity !== "decreasing") {
    issues.push("Monotonicity must be increasing or decreasing.")
  }
  if (spec.prior_weight !== undefined && spec.prior_weight !== "auto" && !(isFiniteNumber(spec.prior_weight) && spec.prior_weight >= 0)) {
    issues.push("Prior weight must be Auto or a non-negative number.")
  }
  if (spec.n_permutations !== undefined && !(isInteger(spec.n_permutations) && spec.n_permutations >= 1 && spec.n_permutations <= MAX_PERMUTATIONS)) {
    issues.push(`Permutations must be an integer from 1 to ${MAX_PERMUTATIONS}.`)
  }
  if (spec.levels !== undefined && spec.reference !== undefined) issues.push("Set levels or a reference level, not both.")
  return issues
}

// ── Design summaries ──

function isPenalisedSpline(spec: TermSpec): boolean {
  return spec.df === undefined && spec.knots === undefined
}

/** Splines RustyStats fits as penalised smooths (no df or knots). */
export function penalisedSmoothTerms(terms: Terms, interactions: readonly InteractionSpec[]): string[] {
  const labels = Object.entries(terms)
    .filter(([, spec]) => ["bs", "ns", "ms"].includes(specType(spec) ?? "") && isPenalisedSpline(spec))
    .map(([key]) => key)
  interactions.forEach((interaction, index) => {
    for (const [factor, override] of Object.entries(interaction.specs ?? {})) {
      const type = specType(override)
      if ((type === "bs" || type === "ns") && isPenalisedSpline(override)) {
        labels.push(`Interaction ${index + 1} ${factor}`)
      }
    }
  })
  return labels
}

/** Terms whose monotonicity RustyStats applies as a coefficient constraint. */
export function monotoneConstraintTerms(terms: Terms): string[] {
  return Object.entries(terms)
    .filter(([, spec]) => ["linear", "expression", "bs"].includes(specType(spec) ?? "") && spec.monotonicity !== undefined)
    .map(([key]) => key)
}

// ── Interactions ──

export function filledFactors(interaction: InteractionSpec): string[] {
  const factors = Array.isArray(interaction.factors) ? interaction.factors : []
  return factors.filter((factor) => typeof factor === "string" && factor !== "")
}

const INTERACTION_KEYS = new Set(["factors", "specs", "include_main", "encoding", "prior_weight", "n_permutations"])

/** Why a stored interaction entry cannot be edited, or null when it is well formed. */
export function interactionEntryIssue(value: unknown): string | null {
  if (typeof value !== "object" || value === null || Array.isArray(value)) return "This interaction is not an object."
  const entry = value as Record<string, unknown>
  const unknown = Object.keys(entry).filter((key) => !INTERACTION_KEYS.has(key))
  if (unknown.length > 0) return `This interaction has unknown settings: ${unknown.join(", ")}.`
  if (!Array.isArray(entry.factors) || !entry.factors.every((factor) => typeof factor === "string")) {
    return "This interaction has no list of features."
  }
  if (entry.specs !== undefined && (typeof entry.specs !== "object" || entry.specs === null || Array.isArray(entry.specs))) {
    return "This interaction's fits are not an object."
  }
  for (const [factor, spec] of Object.entries((entry.specs ?? {}) as Record<string, unknown>)) {
    if (specType(spec) === null) return `The fit for ${factor} in this interaction has no fit type.`
  }
  if (entry.include_main !== undefined && typeof entry.include_main !== "boolean") return "Include main effects must be true or false."
  if (entry.encoding !== undefined && entry.encoding !== "target_encoding" && entry.encoding !== "frequency_encoding") {
    return `Unsupported interaction encoding ${String(entry.encoding)}.`
  }
  return null
}

/** A slot's stored override, read as an own property so any column name is safe. */
export function slotOverride(interaction: InteractionSpec, column: string): TermSpec | undefined {
  return hasOwn(interaction.specs, column) ? interaction.specs?.[column] : undefined
}

export type SlotFit = "main" | "linear" | "categorical" | "bs" | "ns" | "target_encoding"
export type SlotFitOption = { value: SlotFit; label: string }

/** Why a single main effect cannot be inherited into an interaction slot, or null. */
export function inheritanceBlock(spec: TermSpec): string | null {
  const type = spec.type
  if (type === "ms") return "Monotone splines cannot be used inside interactions"
  if ((type === "linear" || type === "bs") && spec.monotonicity !== undefined) return "Monotonicity is not applied inside interactions"
  if (type === "categorical" && (spec.levels !== undefined || spec.reference !== undefined)) {
    return "Level restrictions and reference levels are not applied inside interactions"
  }
  if (type === "frequency_encoding") return "Frequency encoding cannot be used inside a product interaction"
  return null
}

function slotSpecFromMain(spec: TermSpec): TermSpec {
  const next: TermSpec = { type: spec.type }
  for (const key of hasOwn(SLOT_OVERRIDE_PROPS, spec.type) ? SLOT_OVERRIDE_PROPS[spec.type] : []) {
    if (hasOwn(spec, key) && spec[key] !== undefined) next[key] = spec[key]
  }
  return next
}

export type SlotResolution =
  | { kind: "override"; spec: TermSpec }
  | { kind: "main"; spec: TermSpec; key: string }
  | { kind: "default"; spec: TermSpec }
  | { kind: "blocked"; reason: string }

/** Resolve a product slot like the backend: override, inheritable main effect, or dtype default. */
export function resolveSlot(terms: Terms, column: string, dtype: string, override: TermSpec | undefined): SlotResolution {
  if (override !== undefined) return { kind: "override", spec: override }
  const mains = mainEffectKeys(terms, column)
  if (mains.length > 1) return { kind: "blocked", reason: "This feature has several main-effect terms; choose a fit for the interaction" }
  if (mains.length === 1) {
    const main = terms[mains[0]]
    const blocked = inheritanceBlock(main)
    if (blocked !== null) return { kind: "blocked", reason: blocked }
    return { kind: "main", spec: slotSpecFromMain(main), key: mains[0] }
  }
  return { kind: "default", spec: dtypeDefaultSpec(dtype) }
}

export function resolvedSlotSpec(terms: Terms, column: string, dtype: string, override: TermSpec | undefined): TermSpec | null {
  const resolution = resolveSlot(terms, column, dtype, override)
  return resolution.kind === "blocked" ? null : resolution.spec
}

/** Product fits a slot may choose, given the column's main effects and its partners' fits. */
export function slotFitOptions(terms: Terms, column: string, dtype: string, partnerSpecs: readonly (TermSpec | null)[]): SlotFitOption[] {
  const dtypeClass = glmDtypeClass(dtype)
  const allowed = SLOT_FITS_BY_CLASS[dtypeClass]
  const native = nativeTermOf(terms, column)
  const nativeCategorical = native?.type === "categorical"
  const partnerTypes = partnerSpecs.map((spec) => spec?.type ?? null)
  const partnerEncoded = partnerTypes.filter((type) => type === "target_encoding").length
  const partnersAllLinear = partnerTypes.every((type) => type === "linear")
  const fits = (type: string): boolean => {
    if (!allowed.has(type)) return false
    if (type === "categorical" && native !== null && !nativeCategorical) return false
    if ((type === "linear" || type === "bs" || type === "ns" || type === "target_encoding") && nativeCategorical) return false
    if (type === "target_encoding") return partnersAllLinear
    if (partnerEncoded > 0) {
      return type === "linear" && partnerEncoded === 1 && partnerTypes.every((other) => other === "linear" || other === "target_encoding")
    }
    return true
  }
  const options: SlotFitOption[] = []
  const inherited = resolveSlot(terms, column, dtype, undefined)
  if (inherited.kind === "main" && fits(inherited.spec.type)) {
    options.push({ value: "main", label: `As main (${typeLabel(inherited.spec.type)})` })
  }
  for (const type of ["linear", "categorical", "bs", "ns", "target_encoding"] as const) {
    if (fits(type)) options.push({ value: type, label: typeLabel(type) })
  }
  return options
}

type InteractionEncoding = "product" | EncodingTermType
const INTERACTION_ENCODINGS: readonly { value: InteractionEncoding; label: string }[] = [
  { value: "product", label: "Product" },
  { value: "target_encoding", label: "Target enc." },
  { value: "frequency_encoding", label: "Frequency enc." },
]

function factorKey(factors: readonly string[]): string {
  return [...factors].sort().join("\u0000")
}

export function interactionEncodingOptions(
  interactions: readonly InteractionSpec[],
  index: number,
  terms: Terms,
  dtypeOf: (column: string) => string,
): { value: InteractionEncoding; label: string }[] {
  const interaction = interactions[index]
  const factors = filledFactors(interaction)
  const complete = factors.length >= 2 && factors.length === interaction.factors.length
  const key = factorKey(factors)
  const used = new Set(
    interactions
      .filter((other, otherIndex) => complete && otherIndex !== index && filledFactors(other).length === factors.length && factorKey(filledFactors(other)) === key)
      .map((other) => other.encoding ?? "product"),
  )
  const productAllowed = factors.every((factor) => {
    const partners = factors.filter((other) => other !== factor).map((other) =>
      resolvedSlotSpec(terms, other, dtypeOf(other), interaction.encoding ? undefined : slotOverride(interaction, other)),
    )
    return slotFitOptions(terms, factor, dtypeOf(factor), partners).length > 0
  })
  const encodingAllowed = factors.every((factor) => JOINT_ENCODING_CLASSES.has(glmDtypeClass(dtypeOf(factor))))
  return INTERACTION_ENCODINGS.filter((option) =>
    option.value === (interaction.encoding ?? "product") ||
    (!used.has(option.value) && (option.value === "product" ? productAllowed : encodingAllowed)),
  )
}

export function duplicateInteractionIndexes(interactions: readonly InteractionSpec[]): Set<number> {
  const seen = new Map<string, number>()
  const duplicates = new Set<number>()
  interactions.forEach((interaction, index) => {
    const factors = filledFactors(interaction)
    if (factors.length < 2) return
    const key = `${interaction.encoding ?? "product"}\u0000${factorKey(factors)}`
    if (seen.has(key)) duplicates.add(index)
    else seen.set(key, index)
  })
  return duplicates
}

export type InteractionDesign = {
  /** Main effects Include main effects adds, by column. */
  materialised: Map<string, { cards: number[]; spec: TermSpec }>
  /** Columns whose target-encoded main effect a product interaction registers. */
  registeredEncodings: Map<string, number[]>
  /** Conflicts the backend would refuse, by card index. */
  cardIssues: Map<number, string[]>
}

/**
 * Simulate the backend's order-independent resolution for the editor: which main
 * effects Include main effects adds, which target encodings interactions register,
 * and which cards conflict. Card-local problems (blocked slots, partner rules) are
 * shown by the slot controls.
 */
export function simulateInteractionDesign(
  terms: Terms,
  interactions: readonly InteractionSpec[],
  dtypeOf: (column: string) => string,
): InteractionDesign {
  const materialiseRequests = new Map<string, { card: number; spec: TermSpec }[]>()
  const registered = new Map<string, number[]>()
  const cardIssues = new Map<number, string[]>()
  const slots: { card: number; factor: string; spec: TermSpec }[] = []
  const addIssue = (card: number, message: string) => {
    const list = cardIssues.get(card) ?? []
    if (!list.includes(message)) list.push(message)
    cardIssues.set(card, list)
  }
  const hasMain = (column: string) => mainEffectKeys(terms, column).length > 0

  interactions.forEach((interaction, card) => {
    if (interactionEntryIssue(interaction) !== null) return
    const factors = filledFactors(interaction)
    if (factors.length < 2) return
    const includeMain = interaction.include_main !== false
    if (interaction.encoding) {
      if (!includeMain) return
      for (const factor of factors) {
        if (!hasMain(factor)) {
          const list = materialiseRequests.get(factor) ?? []
          list.push({ card, spec: dtypeDefaultSpec(dtypeOf(factor)) })
          materialiseRequests.set(factor, list)
        }
      }
      return
    }
    for (const factor of factors) {
      const spec = resolvedSlotSpec(terms, factor, dtypeOf(factor), slotOverride(interaction, factor))
      if (spec === null) continue
      slots.push({ card, factor, spec })
      if (spec.type === "target_encoding") {
        registered.set(factor, [...(registered.get(factor) ?? []), card])
      } else if (includeMain && !hasMain(factor)) {
        const list = materialiseRequests.get(factor) ?? []
        list.push({ card, spec })
        materialiseRequests.set(factor, list)
      }
    }
  })

  const effectiveNative = new Map<string, TermSpec>()
  const materialised = new Map<string, { cards: number[]; spec: TermSpec }>()
  for (const [column, requests] of materialiseRequests) {
    if (registered.has(column) && !hasOwn(terms, column)) {
      effectiveNative.set(column, { type: "target_encoding" })
      continue
    }
    const distinct: TermSpec[] = []
    for (const request of requests) {
      if (!distinct.some((spec) => JSON.stringify(spec) === JSON.stringify(request.spec))) distinct.push(request.spec)
    }
    const cards = [...new Set(requests.map((request) => request.card))]
    if (distinct.length > 1) {
      const fits = distinct.map((spec) => typeLabel(spec.type)).join(", ")
      for (const card of cards) {
        addIssue(card, `Interactions ${cards.map((value) => value + 1).join(", ")} add different main effects for ${column} (${fits}); add a main term for ${column} or give them the same fit`)
      }
      continue
    }
    materialised.set(column, { cards, spec: distinct[0] })
    effectiveNative.set(column, distinct[0])
  }
  for (const column of registered.keys()) {
    if (!hasOwn(terms, column) && !effectiveNative.has(column)) effectiveNative.set(column, { type: "target_encoding" })
  }

  for (const slot of slots) {
    const native = nativeTermOf(terms, slot.factor) ?? effectiveNative.get(slot.factor) ?? null
    if (native === null) continue
    const type = slot.spec.type
    if (type === "categorical" && native.type !== "categorical") {
      addIssue(slot.card, `A categorical fit for ${slot.factor} would re-type its ${typeLabel(native.type)} main effect`)
    } else if ((type === "linear" || type === "bs" || type === "ns") && native.type === "categorical") {
      addIssue(slot.card, `${typeLabel(type)} cannot be applied over the categorical main effect of ${slot.factor}`)
    } else if (type === "target_encoding" && native.type === "categorical") {
      addIssue(slot.card, `Target encoding over the categorical main effect of ${slot.factor} is collinear with its indicators`)
    }
  }
  return { materialised, registeredEncodings: registered, cardIssues }
}

function interactionList(cards: readonly number[]): string {
  return `Interaction${cards.length > 1 ? "s" : ""} ${cards.map((card) => card + 1).join(", ")}`
}

/** How a feature is in the model when it has no term card of its own. */
export function featureTag(
  column: string,
  hasCards: boolean,
  membership: { inModel: ReadonlySet<string>; interactionOnly: ReadonlySet<string> },
  design: InteractionDesign,
): string | null {
  const materialised = design.materialised.get(column)
  if (materialised) return `Main effect from ${interactionList(materialised.cards)} (${typeLabel(materialised.spec.type)})`
  const registered = design.registeredEncodings.get(column)
  if (registered && !hasCards) return `Target encoding from ${interactionList(registered)}`
  if (!membership.inModel.has(column)) return "Not in model"
  if (hasCards) return null
  return membership.interactionOnly.has(column) ? "Interaction only" : "In an expression"
}

function withoutOverride(interaction: InteractionSpec, column: string): InteractionSpec {
  if (!interaction.specs || !hasOwn(interaction.specs, column)) return interaction
  const rest: Record<string, TermSpec> = {}
  for (const [key, spec] of Object.entries(interaction.specs)) {
    if (key !== column) rest[key] = spec
  }
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

export function setIncludeMain(interactions: readonly InteractionSpec[], index: number, value: boolean): InteractionSpec[] {
  return replaceAt(interactions, index, (interaction) => ({ ...interaction, include_main: value }))
}

export function setInteractionEncoding(
  interactions: readonly InteractionSpec[],
  index: number,
  encoding: InteractionSpec["encoding"] | "product",
): InteractionSpec[] {
  return replaceAt(interactions, index, (interaction) => {
    const nextEncoding = encoding === "product" ? undefined : encoding
    if (interaction.encoding === nextEncoding) return interaction
    const next: InteractionSpec = { factors: interaction.factors, include_main: interaction.include_main }
    if (nextEncoding !== undefined) next.encoding = nextEncoding
    return next
  })
}

export function setInteractionField(
  interactions: readonly InteractionSpec[],
  index: number,
  field: "prior_weight" | "n_permutations",
  value: number | undefined,
): InteractionSpec[] {
  return replaceAt(interactions, index, (interaction) => {
    const next = { ...interaction }
    if (value === undefined) delete next[field]
    else next[field] = value
    return next
  })
}

export function pickSlotColumn(interactions: readonly InteractionSpec[], index: number, slot: number, column: string): InteractionSpec[] {
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
  return replaceAt(interactions, index, (interaction) => ({ ...interaction, factors: [...interaction.factors, ""] }))
}

export function removeSlot(interactions: readonly InteractionSpec[], index: number, slot: number): InteractionSpec[] {
  return replaceAt(interactions, index, (interaction) => {
    if (interaction.factors.length <= 2) return interaction
    const removed = interaction.factors[slot] ?? ""
    const factors = interaction.factors.filter((_, i) => i !== slot)
    const cleared = removed !== "" ? withoutOverride(interaction, removed) : interaction
    return { ...cleared, factors }
  })
}
