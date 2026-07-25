// ─── Rating Table Types & Pure Utilities ──────────────────────────

type PrimitiveRatingFactorKind =
  | "Int8" | "Int16" | "Int32" | "Int64" | "Int128"
  | "UInt8" | "UInt16" | "UInt32" | "UInt64"
  | "Float32" | "Float64" | "Boolean" | "String" | "Categorical"
  | "Date" | "Time" | "Null"

export type RatingFactorDtype =
  | { kind: PrimitiveRatingFactorKind }
  | { kind: "Datetime"; timeUnit: "ms" | "us" | "ns"; timeZone: string | null }
  | { kind: "Duration"; timeUnit: "ms" | "us" | "ns" }
  | { kind: "Decimal"; precision: number | null; scale: number }
  | { kind: "Enum"; categories: string[] }

export type RatingTable = {
  name: string
  factors: string[]
  factorDtypes?: Record<string, RatingFactorDtype>
  outputColumn: string
  defaultValue: string | null
  entries: Record<string, string | number>[]
}

export type RatingFactorColumn = {
  name: string
  dtype: string
}

export type RatingTableStatus = {
  state: "healthy" | "problem"
  issues: string[]
}

function defaultRatingTable(idx: number): RatingTable {
  return { name: `Table ${idx + 1}`, factors: [], outputColumn: "", defaultValue: "1.0", entries: [] }
}

function isEntry(value: unknown): value is Record<string, string | number> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value)
}

const primitiveRatingKinds = new Set<PrimitiveRatingFactorKind>([
  "Int8", "Int16", "Int32", "Int64", "Int128",
  "UInt8", "UInt16", "UInt32", "UInt64",
  "Float32", "Float64", "Boolean", "String", "Categorical",
  "Date", "Time", "Null",
])

function isPlainRecord(value: unknown): value is Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false
  const prototype = Object.getPrototypeOf(value)
  return prototype === Object.prototype || prototype === null
}

function hasExactKeys(value: Record<string, unknown>, keys: string[]): boolean {
  const actual = Object.keys(value)
  return actual.length === keys.length && keys.every(key => Object.hasOwn(value, key))
}

function isRatingTimeUnit(value: unknown): value is "ms" | "us" | "ns" {
  return value === "ms" || value === "us" || value === "ns"
}

function normaliseRatingFactorDtype(value: unknown): RatingFactorDtype | undefined {
  if (!isPlainRecord(value) || typeof value.kind !== "string") return undefined

  const kind = value.kind
  if (primitiveRatingKinds.has(kind as PrimitiveRatingFactorKind)) {
    return hasExactKeys(value, ["kind"])
      ? { kind: kind as PrimitiveRatingFactorKind }
      : undefined
  }
  if (kind === "Datetime") {
    if (
      !hasExactKeys(value, ["kind", "timeUnit", "timeZone"])
      || !isRatingTimeUnit(value.timeUnit)
      || (value.timeZone !== null && typeof value.timeZone !== "string")
    ) return undefined
    return { kind, timeUnit: value.timeUnit, timeZone: value.timeZone }
  }
  if (kind === "Duration") {
    if (!hasExactKeys(value, ["kind", "timeUnit"]) || !isRatingTimeUnit(value.timeUnit)) return undefined
    return { kind, timeUnit: value.timeUnit }
  }
  if (kind === "Decimal") {
    if (
      !hasExactKeys(value, ["kind", "precision", "scale"])
      || (value.precision !== null
        && (typeof value.precision !== "number" || !Number.isInteger(value.precision)))
      || typeof value.scale !== "number"
      || !Number.isInteger(value.scale)
    ) return undefined
    return { kind, precision: value.precision, scale: value.scale }
  }
  if (kind === "Enum") {
    if (
      !hasExactKeys(value, ["kind", "categories"])
      || !Array.isArray(value.categories)
      || !value.categories.every(category => typeof category === "string")
      || new Set(value.categories).size !== value.categories.length
    ) return undefined
    return { kind, categories: [...value.categories] }
  }
  return undefined
}

function normaliseFactorDtypes(raw: unknown, factors: string[]): Record<string, RatingFactorDtype> | undefined {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return undefined

  const selectedFactors = new Set(factors)
  const factorDtypes: Record<string, RatingFactorDtype> = {}
  for (const [factor, descriptor] of Object.entries(raw)) {
    if (!selectedFactors.has(factor)) continue
    const normalised = normaliseRatingFactorDtype(descriptor)
    if (!normalised) continue
    factorDtypes[factor] = normalised
  }
  return Object.keys(factorDtypes).length > 0 ? factorDtypes : undefined
}

function normaliseRatingTable(raw: unknown, idx: number): RatingTable {
  const fallback = defaultRatingTable(idx)
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return fallback

  const table = raw as Record<string, unknown>
  const outputColumn = typeof table.outputColumn === "string" ? table.outputColumn : ""
  const factors = Array.isArray(table.factors)
    ? table.factors.filter((factor): factor is string => typeof factor === "string")
    : []
  const factorDtypes = normaliseFactorDtypes(table.factorDtypes, factors)
  return {
    name: outputColumn.trim() || fallback.name,
    factors,
    ...(factorDtypes ? { factorDtypes } : {}),
    outputColumn,
    defaultValue: typeof table.defaultValue === "string" || table.defaultValue === null
      ? table.defaultValue
      : typeof table.defaultValue === "number"
        ? String(table.defaultValue)
        : fallback.defaultValue,
    entries: Array.isArray(table.entries) ? table.entries.filter(isEntry) : [],
  }
}

export function normaliseRatingTables(config: Record<string, unknown>): RatingTable[] {
  const raw = config.tables
  if (Array.isArray(raw) && raw.length > 0) return raw.map(normaliseRatingTable)
  return [defaultRatingTable(0)]
}

export function ratingTableStatus(
  table: RatingTable,
  idx: number,
  tables: RatingTable[],
): RatingTableStatus {
  const issues: string[] = []
  const outputColumn = table.outputColumn.trim()

  if (!outputColumn) {
    issues.push("Output column is required")
  } else if (tables.some((other, otherIdx) => otherIdx !== idx && other.outputColumn.trim() === outputColumn)) {
    issues.push("Output column name must be unique")
  }

  if (table.factors.length === 0) issues.push("Add at least one factor")
  if ((table.entries || []).length === 0) issues.push("Add at least one rating entry")

  return {
    state: issues.length > 0 ? "problem" : "healthy",
    issues,
  }
}

/** Heatmap color for actuarial relativity values. */
export function relativityColor(value: number): string {
  if (isNaN(value)) return 'transparent'
  const dev = value - 1.0
  const t = Math.min(Math.abs(dev) / 0.5, 1)
  if (dev > 0.005) return `rgba(var(--danger-rgb), ${(t * 0.22).toFixed(3)})`
  if (dev < -0.005) return `rgba(var(--chart-below-rgb), ${(t * 0.22).toFixed(3)})`
  return 'transparent'
}

export function relativityTextColor(value: number): string {
  if (isNaN(value)) return 'var(--text-secondary)'
  const dev = value - 1.0
  if (dev > 0.005) return 'var(--danger-solid)'
  if (dev < -0.005) return 'var(--chart-below)'
  return 'var(--success)'
}

export function tableStats(entries: Record<string, string | number>[]): { min: number; max: number; avg: number; count: number } | null {
  const vals = entries.map(e => typeof e.value === 'number' ? e.value : parseFloat(String(e.value ?? ''))).filter(v => !isNaN(v))
  if (vals.length === 0) return null
  const min = vals.reduce((a, b) => Math.min(a, b), Infinity)
  const max = vals.reduce((a, b) => Math.max(a, b), -Infinity)
  const avg = vals.reduce((s, v) => s + v, 0) / vals.length
  return { min, max, avg, count: vals.length }
}

export function buildCartesianEntries(
  factors: string[],
  bandingLevels: Record<string, string[]>,
  existing: Record<string, string | number>[],
  defaultValue: string | null,
): Record<string, string | number>[] {
  if (factors.length === 0) return []
  const levelArrays = factors.map(f => bandingLevels[f] || [])
  if (levelArrays.some(a => a.length === 0)) return existing

  const existingLookup = new Map<string, number>()
  for (const e of existing) {
    const key = factors.map(f => String(e[f] ?? "")).join("\x1F")
    const v = e.value
    if (v !== undefined && v !== null && v !== "") {
      const parsed = typeof v === "number" ? v : parseFloat(String(v))
      if (!Number.isNaN(parsed)) existingLookup.set(key, parsed)
    }
  }

  const parsedDef = defaultValue != null && String(defaultValue).trim() ? parseFloat(String(defaultValue)) : 1.0
  const defVal = Number.isNaN(parsedDef) ? 1.0 : parsedDef
  const entries: Record<string, string | number>[] = []

  function recurse(depth: number, current: Record<string, string>) {
    if (depth === factors.length) {
      const key = factors.map(f => current[f]).join("\x1F")
      entries.push({ ...current, value: existingLookup.get(key) ?? defVal })
      return
    }
    for (const level of levelArrays[depth]) {
      recurse(depth + 1, { ...current, [factors[depth]]: level })
    }
  }
  recurse(0, {})
  return entries
}

// ─── Shared helpers for rating editors ────────────────────────────

/** True when a dtype can provide finite categorical rating levels from preview rows. */
function isStringLikeDtype(dtype: string): boolean {
  const normalized = dtype.trim().toLowerCase()
  return normalized === "str" ||
    normalized.includes("string") ||
    normalized.includes("utf8") ||
    normalized.includes("categorical") ||
    normalized.includes("enum")
}

function previewCandidateColumns(
  previewRows: Record<string, unknown>[],
  upstreamColumns?: RatingFactorColumn[],
): string[] {
  if (upstreamColumns && upstreamColumns.length > 0) {
    return upstreamColumns
      .filter(col => isStringLikeDtype(col.dtype))
      .map(col => col.name)
  }

  const seen = new Set<string>()
  const names: string[] = []
  for (const row of previewRows) {
    for (const name of Object.keys(row)) {
      if (seen.has(name)) continue
      seen.add(name)
      names.push(name)
    }
  }
  return names
}

export function extractPreviewCategoricalLevels(
  previewRows: Record<string, unknown>[] | undefined,
  upstreamColumns?: RatingFactorColumn[],
): Record<string, string[]> {
  if (!previewRows?.length) return {}

  const names = previewCandidateColumns(previewRows, upstreamColumns)
  const levelSets = new Map<string, Set<string>>(names.map(name => [name, new Set<string>()]))
  const invalidColumns = new Set<string>()

  for (const row of previewRows) {
    for (const name of names) {
      const value = row[name]
      if (value === null || value === undefined) continue
      if (typeof value !== "string") {
        invalidColumns.add(name)
        continue
      }
      if (value.trim() === "") continue
      levelSets.get(name)?.add(value)
    }
  }

  const levels: Record<string, string[]> = {}
  for (const name of names) {
    const values = levelSets.get(name)
    if (!values || values.size === 0 || invalidColumns.has(name)) continue
    levels[name] = [...values]
  }
  return levels
}

export function mergeFactorLevels(
  baseLevels: Record<string, string[]>,
  extraLevels: Record<string, string[]>,
): Record<string, string[]> {
  const merged: Record<string, string[]> = {}

  for (const [name, levels] of Object.entries(baseLevels)) {
    merged[name] = [...levels]
  }

  for (const [name, levels] of Object.entries(extraLevels)) {
    if (!merged[name]) {
      merged[name] = [...levels]
      continue
    }
    const existing = new Set(merged[name])
    for (const level of levels) {
      if (existing.has(level)) continue
      merged[name].push(level)
      existing.add(level)
    }
  }

  return merged
}

export function extractTableEntryFactorLevels(
  tables: RatingTable[],
): Record<string, string[]> {
  const levelSets: Record<string, Set<string>> = {}

  for (const table of tables) {
    for (const factor of table.factors) {
      if (!levelSets[factor]) levelSets[factor] = new Set()
    }
    for (const entry of table.entries || []) {
      for (const factor of table.factors) {
        const value = entry[factor]
        if (value === null || value === undefined) continue
        const level = String(value)
        if (!level) continue
        levelSets[factor].add(level)
      }
    }
  }

  const levels: Record<string, string[]> = {}
  for (const [factor, values] of Object.entries(levelSets)) {
    if (values.size > 0) levels[factor] = [...values]
  }
  return levels
}

/** Resolve the table's defaultValue to a safe numeric fallback. */
export function resolveDefault(defaultValue: string | number | null | undefined): number {
  const raw = typeof defaultValue === "number" ? defaultValue
    : typeof defaultValue === "string" && defaultValue.trim() ? parseFloat(defaultValue) : 1
  return Number.isNaN(raw) ? 1 : raw
}
