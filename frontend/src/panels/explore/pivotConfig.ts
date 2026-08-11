export const PIVOT_CONFIG_VERSION = 1 as const

export const PIVOT_AGGREGATIONS = [
  "sum",
  "count",
  "average",
  "min",
  "max",
  "median",
  "distinct_count",
] as const

export type PivotAggregation = (typeof PIVOT_AGGREGATIONS)[number]
export type PivotMemberKind =
  | "null"
  | "string"
  | "boolean"
  | "integer"
  | "float"
  | "nan"
  | "date"
  | "datetime"
  | "time"
  | "decimal"

export type PivotMember = {
  kind: PivotMemberKind
  value: string | number | boolean | null
  [key: string]: unknown
}

export type PivotFilterPlacement = {
  id: string
  field: string
  members: PivotMember[]
  [key: string]: unknown
}

export type PivotAxisPlacement = {
  id: string
  field: string
  [key: string]: unknown
}

export type PivotValuePlacement = {
  id: string
  field: string
  aggregation: PivotAggregation
  display_name: string
  [key: string]: unknown
}

export type PivotOptions = {
  row_grand_totals: boolean
  column_grand_totals: boolean
  [key: string]: unknown
}

export type ExplorePivotConfig = {
  version: typeof PIVOT_CONFIG_VERSION
  id: string
  name: string
  enabled: boolean
  filters: PivotFilterPlacement[]
  columns: PivotAxisPlacement[]
  rows: PivotAxisPlacement[]
  values: PivotValuePlacement[]
  options: PivotOptions
  [key: string]: unknown
}

export type ExplorePivotsParseResult =
  | { ok: true; pivots: ExplorePivotConfig[] }
  | { ok: false; error: string }

const MEMBER_KINDS = new Set<PivotMemberKind>([
  "null",
  "string",
  "boolean",
  "integer",
  "float",
  "nan",
  "date",
  "datetime",
  "time",
  "decimal",
])
const AGGREGATIONS = new Set<string>(PIVOT_AGGREGATIONS)
const DECIMAL_PATTERN = /^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:E[+-]?[0-9]+)?$/
const DATE_PATTERN = /^(\d{4})-(\d{2})-(\d{2})$/
const TIME_PATTERN = /^(\d{2}):(\d{2}):(\d{2})(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})?$/
const CARD_KEYS = new Set([
  "version",
  "id",
  "name",
  "enabled",
  "filters",
  "columns",
  "rows",
  "values",
  "options",
])

function isPlainObject(value: unknown): value is Record<string, unknown> {
  if (value === null || typeof value !== "object" || Array.isArray(value)) return false
  const prototype = Object.getPrototypeOf(value)
  return prototype === Object.prototype || prototype === null
}

function isSimpleLiteral(value: unknown): boolean {
  if (value === null || typeof value === "string" || typeof value === "boolean") return true
  if (typeof value === "number") return Number.isFinite(value)
  if (Array.isArray(value)) return value.every(isSimpleLiteral)
  if (isPlainObject(value)) return Object.values(value).every(isSimpleLiteral)
  return false
}

function cloneLiteral<T>(value: T): T {
  if (Array.isArray(value)) return value.map(cloneLiteral) as T
  if (isPlainObject(value)) {
    return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, cloneLiteral(item)])) as T
  }
  return value
}

function nonEmptyString(value: unknown): value is string {
  return typeof value === "string" && value.trim().length > 0
}

function isValidDate(value: string): boolean {
  const match = DATE_PATTERN.exec(value)
  if (match === null) return false
  const year = Number(match[1])
  const month = Number(match[2])
  const day = Number(match[3])
  if (year < 1) return false
  const candidate = new Date(0)
  candidate.setUTCFullYear(year, month - 1, day)
  candidate.setUTCHours(0, 0, 0, 0)
  return (
    candidate.getUTCFullYear() === year &&
    candidate.getUTCMonth() === month - 1 &&
    candidate.getUTCDate() === day
  )
}

function isValidTime(value: string): boolean {
  const match = TIME_PATTERN.exec(value)
  if (match === null) return false
  const hour = Number(match[1])
  const minute = Number(match[2])
  const second = Number(match[3])
  if (hour > 23 || minute > 59 || second > 59) return false
  const offset = /([+-])(\d{2}):(\d{2})$/.exec(value)
  return offset === null || (Number(offset[2]) <= 23 && Number(offset[3]) <= 59)
}

function isValidDateTime(value: string): boolean {
  const separator = value.indexOf("T")
  return (
    separator > 0 &&
    isValidDate(value.slice(0, separator)) &&
    isValidTime(value.slice(separator + 1))
  )
}

function memberValueMatchesKind(kind: PivotMemberKind, value: unknown): boolean {
  switch (kind) {
    case "null":
    case "nan":
      return value === null
    case "string":
      return typeof value === "string"
    case "boolean":
      return typeof value === "boolean"
    case "integer":
      return typeof value === "string" && /^-?(?:0|[1-9][0-9]*)$/.test(value)
    case "float":
      return typeof value === "number" && Number.isFinite(value)
    case "decimal":
      return typeof value === "string" && DECIMAL_PATTERN.test(value)
    case "date":
      return typeof value === "string" && isValidDate(value)
    case "datetime":
      return typeof value === "string" && isValidDateTime(value)
    case "time":
      return typeof value === "string" && isValidTime(value)
  }
}

function validateFutureFields(
  raw: Record<string, unknown>,
  known: ReadonlySet<string>,
  position: number,
  scope: string,
): string | null {
  for (const [key, value] of Object.entries(raw)) {
    if (!known.has(key) && !isSimpleLiteral(value)) {
      return `Pivot ${position} ${scope} field "${key}" must contain only simple literal values.`
    }
  }
  return null
}

function parseMember(raw: unknown, position: number): PivotMember | string {
  if (!isPlainObject(raw)) return `Pivot ${position} filter members must be objects.`
  const futureError = validateFutureFields(raw, new Set(["kind", "value"]), position, "member")
  if (futureError) return futureError
  if (typeof raw.kind !== "string" || !MEMBER_KINDS.has(raw.kind as PivotMemberKind)) {
    return `Pivot ${position} member has an unsupported kind.`
  }
  const kind = raw.kind as PivotMemberKind
  const value = raw.value
  if (!memberValueMatchesKind(kind, value)) {
    return `Pivot ${position} member value does not match kind "${kind}".`
  }
  return cloneLiteral(raw) as PivotMember
}

function parseAxisZone(
  raw: unknown,
  zone: "filters" | "columns" | "rows",
  position: number,
  placementIds: Set<string>,
): PivotFilterPlacement[] | PivotAxisPlacement[] | string {
  if (!Array.isArray(raw)) return `Pivot ${position} ${zone} must be a list.`
  const fields = new Set<string>()
  const placements: Array<PivotFilterPlacement | PivotAxisPlacement> = []
  for (const entry of raw) {
    if (!isPlainObject(entry)) return `Pivot ${position} ${zone} entries must be objects.`
    const known = zone === "filters" ? new Set(["id", "field", "members"]) : new Set(["id", "field"])
    const futureError = validateFutureFields(entry, known, position, `${zone} placement`)
    if (futureError) return futureError
    if (!nonEmptyString(entry.id)) return `Pivot ${position} placement id must be a non-empty string.`
    if (!nonEmptyString(entry.field)) return `Pivot ${position} placement field must be a non-empty string.`
    if (placementIds.has(entry.id)) return `Pivot ${position} has duplicate placement id "${entry.id}".`
    if (fields.has(entry.field)) return `Pivot ${position} ${zone} has duplicate field "${entry.field}".`
    placementIds.add(entry.id)
    fields.add(entry.field)
    if (zone === "filters") {
      if (!Array.isArray(entry.members)) return `Pivot ${position} filter members must be a list.`
      const members: PivotMember[] = []
      for (const rawMember of entry.members) {
        const member = parseMember(rawMember, position)
        if (typeof member === "string") return member
        members.push(member)
      }
      placements.push({ ...cloneLiteral(entry), id: entry.id, field: entry.field, members })
    } else {
      placements.push({ ...cloneLiteral(entry), id: entry.id, field: entry.field })
    }
  }
  return placements as PivotFilterPlacement[] | PivotAxisPlacement[]
}

function parseValues(
  raw: unknown,
  position: number,
  placementIds: Set<string>,
): PivotValuePlacement[] | string {
  if (!Array.isArray(raw)) return `Pivot ${position} values must be a list.`
  const values: PivotValuePlacement[] = []
  for (const entry of raw) {
    if (!isPlainObject(entry)) return `Pivot ${position} value entries must be objects.`
    const futureError = validateFutureFields(
      entry,
      new Set(["id", "field", "aggregation", "display_name"]),
      position,
      "value",
    )
    if (futureError) return futureError
    if (!nonEmptyString(entry.id)) return `Pivot ${position} placement id must be a non-empty string.`
    if (!nonEmptyString(entry.field)) return `Pivot ${position} placement field must be a non-empty string.`
    if (placementIds.has(entry.id)) return `Pivot ${position} has duplicate placement id "${entry.id}".`
    if (typeof entry.aggregation !== "string" || !AGGREGATIONS.has(entry.aggregation)) {
      return `Pivot ${position} value has an unsupported aggregation.`
    }
    if (!nonEmptyString(entry.display_name)) return `Pivot ${position} value display name must be non-empty.`
    placementIds.add(entry.id)
    values.push({
      ...cloneLiteral(entry),
      id: entry.id,
      field: entry.field,
      aggregation: entry.aggregation as PivotAggregation,
      display_name: entry.display_name,
    })
  }
  return values
}

function migratePivot(raw: Record<string, unknown>, position: number): ExplorePivotConfig | string {
  if (!nonEmptyString(raw.id)) return `Pivot ${position} id must be a non-empty string.`
  const conflicting = Object.keys(raw).filter((key) => key !== "id" && CARD_KEYS.has(key))
  if (conflicting.length > 0) return `Pivot ${position} versionless card contains version-1 fields.`
  const futureError = validateFutureFields(raw, new Set(["id"]), position, "card")
  if (futureError) return futureError
  return {
    ...cloneLiteral(raw),
    version: 1,
    id: raw.id,
    name: `Pivot ${position}`,
    enabled: true,
    filters: [],
    columns: [],
    rows: [],
    values: [],
    options: { row_grand_totals: true, column_grand_totals: true },
  }
}

function parseV1Pivot(raw: Record<string, unknown>, position: number): ExplorePivotConfig | string {
  const futureError = validateFutureFields(raw, CARD_KEYS, position, "card")
  if (futureError) return futureError
  if (raw.version !== 1) return `Pivot ${position} version must be 1.`
  if (!nonEmptyString(raw.id)) return `Pivot ${position} id must be a non-empty string.`
  if (!nonEmptyString(raw.name)) return `Pivot ${position} name must be a non-empty string.`
  if (typeof raw.enabled !== "boolean") return `Pivot ${position} enabled state must be a boolean.`
  const placementIds = new Set<string>()
  const filters = parseAxisZone(raw.filters, "filters", position, placementIds)
  if (typeof filters === "string") return filters
  const columns = parseAxisZone(raw.columns, "columns", position, placementIds)
  if (typeof columns === "string") return columns
  const rows = parseAxisZone(raw.rows, "rows", position, placementIds)
  if (typeof rows === "string") return rows
  const values = parseValues(raw.values, position, placementIds)
  if (typeof values === "string") return values
  if (!isPlainObject(raw.options)) return `Pivot ${position} options must be an object.`
  const optionError = validateFutureFields(
    raw.options,
    new Set(["row_grand_totals", "column_grand_totals"]),
    position,
    "options",
  )
  if (optionError) return optionError
  if (typeof raw.options.row_grand_totals !== "boolean") {
    return `Pivot ${position} options.row_grand_totals must be a boolean.`
  }
  if (typeof raw.options.column_grand_totals !== "boolean") {
    return `Pivot ${position} options.column_grand_totals must be a boolean.`
  }
  return {
    ...cloneLiteral(raw),
    version: 1,
    id: raw.id,
    name: raw.name,
    enabled: raw.enabled,
    filters: filters as PivotFilterPlacement[],
    columns: columns as PivotAxisPlacement[],
    rows: rows as PivotAxisPlacement[],
    values,
    options: cloneLiteral(raw.options) as PivotOptions,
  }
}

export function parseExplorePivots(config: Record<string, unknown>): ExplorePivotsParseResult {
  if (!Object.prototype.hasOwnProperty.call(config, "pivots")) return { ok: true, pivots: [] }
  if (!Array.isArray(config.pivots)) return { ok: false, error: "Explore pivots config must be a list." }

  const pivots: ExplorePivotConfig[] = []
  const ids = new Set<string>()
  const names = new Set<string>()
  for (const [index, raw] of config.pivots.entries()) {
    const position = index + 1
    if (!isPlainObject(raw)) return { ok: false, error: `Pivot ${position} must be an object.` }
    if (!Object.prototype.hasOwnProperty.call(raw, "id")) {
      return { ok: false, error: `Pivot ${position} requires an id.` }
    }
    const pivot = Object.prototype.hasOwnProperty.call(raw, "version")
      ? parseV1Pivot(raw, position)
      : migratePivot(raw, position)
    if (typeof pivot === "string") return { ok: false, error: pivot }
    if (ids.has(pivot.id)) {
      return { ok: false, error: `Explore pivots config contains duplicate pivot id "${pivot.id}".` }
    }
    const nameKey = pivot.name.trim().toLowerCase()
    if (names.has(nameKey)) {
      return { ok: false, error: `Explore pivots config contains duplicate pivot name "${pivot.name}".` }
    }
    ids.add(pivot.id)
    names.add(nameKey)
    pivots.push(pivot)
  }
  return { ok: true, pivots }
}

export function nextExplorePivotId(pivots: readonly ExplorePivotConfig[]): string {
  const ids = new Set(pivots.map((pivot) => pivot.id))
  let suffix = 1
  while (ids.has(`pivot_${suffix}`)) suffix += 1
  return `pivot_${suffix}`
}

export function nextExplorePivotName(pivots: readonly ExplorePivotConfig[]): string {
  const names = new Set(pivots.map((pivot) => pivot.name.trim().toLowerCase()))
  let suffix = 1
  while (names.has(`pivot ${suffix}`)) suffix += 1
  return `Pivot ${suffix}`
}

export function createExplorePivot(pivots: readonly ExplorePivotConfig[]): ExplorePivotConfig {
  const id = nextExplorePivotId(pivots)
  const suffix = id.slice("pivot_".length)
  const preferredName = `Pivot ${suffix}`
  const existingNames = new Set(pivots.map((pivot) => pivot.name.trim().toLowerCase()))
  return {
    version: 1,
    id,
    name: existingNames.has(preferredName.toLowerCase())
      ? nextExplorePivotName(pivots)
      : preferredName,
    enabled: true,
    filters: [],
    columns: [],
    rows: [],
    values: [],
    options: { row_grand_totals: true, column_grand_totals: true },
  }
}

export function nextPivotPlacementId(pivot: ExplorePivotConfig, prefix: string): string {
  const ids = new Set([
    ...pivot.filters.map((placement) => placement.id),
    ...pivot.columns.map((placement) => placement.id),
    ...pivot.rows.map((placement) => placement.id),
    ...pivot.values.map((placement) => placement.id),
  ])
  let suffix = 1
  while (ids.has(`${prefix}_${suffix}`)) suffix += 1
  return `${prefix}_${suffix}`
}

export function explorePivotLabel(pivotOrIndex: ExplorePivotConfig | number): string {
  return typeof pivotOrIndex === "number" ? `Pivot ${pivotOrIndex + 1}` : pivotOrIndex.name
}

export function pivotCalculationIdentity(pivot: ExplorePivotConfig): string {
  const filters = pivot.filters.map((filter) => ({
    field: filter.field,
    members: filter.members
      .map((member) => ({ kind: member.kind, value: member.value }))
      .sort((left, right) => JSON.stringify(left).localeCompare(JSON.stringify(right))),
  }))
  return JSON.stringify({
    filters,
    columns: pivot.columns.map((placement) => placement.field),
    rows: pivot.rows.map((placement) => placement.field),
    values: pivot.values.map((value) => ({
      id: value.id,
      field: value.field,
      aggregation: value.aggregation,
    })),
    options: {
      row_grand_totals: pivot.options.row_grand_totals,
      column_grand_totals: pivot.options.column_grand_totals,
    },
  })
}

export function isNumericPivotDtype(dtype: string): boolean {
  return /^(?:u?int|float|decimal)/i.test(dtype.trim())
}

export function pivotAggregationsForDtype(dtype: string): readonly PivotAggregation[] {
  return isNumericPivotDtype(dtype)
    ? PIVOT_AGGREGATIONS
    : (["count", "distinct_count", "min", "max"] as const)
}

export function defaultPivotAggregation(dtype: string): PivotAggregation {
  return isNumericPivotDtype(dtype) ? "sum" : "count"
}

export const PIVOT_AGGREGATION_LABELS: Readonly<Record<PivotAggregation, string>> = {
  sum: "Sum",
  count: "Count",
  average: "Average",
  min: "Min",
  max: "Max",
  median: "Median",
  distinct_count: "Distinct count",
}
