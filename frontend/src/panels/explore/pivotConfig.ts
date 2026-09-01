import { isNumericDtype } from "../../utils/polarsDtypes"
import { portableKey } from "../../utils/portableKey"

export const PIVOT_CONFIG_VERSION = 1 as const
export const PIVOT_DECIMAL_PLACES_MAX = 10
export const PIVOT_NUMBER_FORMATS = [
  "general",
  "number",
  "percent",
  "currency_gbp",
  "currency_usd",
  "currency_eur",
] as const

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
export type PivotDecimalPlaces = number | null
export type PivotNumberFormat = (typeof PIVOT_NUMBER_FORMATS)[number]
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
  sort?: "ascending" | "descending"
  number_format: PivotNumberFormat
  decimal_places: PivotDecimalPlaces
  use_grouping: boolean
  [key: string]: unknown
}

export type PivotValuePlacement = {
  id: string
  field: string
  aggregation: PivotAggregation
  reference: string
  display_name: string
  sort_rows?: "none" | "ascending" | "descending"
  color_scale?: "none" | "low_red_high_green" | "low_green_high_red"
  color_scale_split_by: string | null
  number_format: PivotNumberFormat
  decimal_places: PivotDecimalPlaces
  use_grouping: boolean
  [key: string]: unknown
}

export type PivotFormulaPlacement = {
  id: string
  reference: string
  display_name: string
  expression: string
  number_format: PivotNumberFormat
  decimal_places: PivotDecimalPlaces
  use_grouping: boolean
  [key: string]: unknown
}

export type PivotOutputPlacement = PivotValuePlacement | PivotFormulaPlacement

export type PivotOptions = {
  row_grand_totals: boolean
  column_grand_totals: boolean
  sort_by?: string | null
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
  formulas: PivotFormulaPlacement[]
  /** Canonical mixed display/calculation order for Values and selected formulas. */
  value_order: string[]
  options: PivotOptions
  [key: string]: unknown
}

export type ExplorePivotsParseResult =
  | {
      ok: true
      /** Runtime pivots with selected shared formulas resolved to full definitions. */
      pivots: ExplorePivotConfig[]
      /** Explore-level calculated-field library, shared by every pivot. */
      formulas: PivotFormulaPlacement[]
    }
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
const NUMBER_FORMATS = new Set<string>(PIVOT_NUMBER_FORMATS)
const AXIS_SORTS = new Set<PivotAxisPlacement["sort"]>(["ascending", "descending"])
const VALUE_SORTS = new Set<PivotValuePlacement["sort_rows"]>(["none", "ascending", "descending"])
const COLOR_SCALES = new Set<PivotValuePlacement["color_scale"]>([
  "none",
  "low_red_high_green",
  "low_green_high_red",
])
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
  "formulas",
  "value_order",
  "options",
])
const FORMULA_REFERENCE_PATTERN = /^[A-Za-z_][A-Za-z0-9_]*$/
const RESERVED_FORMULA_REFERENCE_PREFIX = "__haute_"

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

function isValidFormulaReference(value: unknown): value is string {
  return (
    typeof value === "string" &&
    FORMULA_REFERENCE_PATTERN.test(value) &&
    !value.startsWith(RESERVED_FORMULA_REFERENCE_PREFIX)
  )
}

function referenceStem(value: string, fallback: string): string {
  let stem = portableKey(value).toLowerCase()
  while (stem.startsWith("_")) stem = stem.slice(1)
  while (stem.endsWith("_")) stem = stem.slice(0, -1)
  const containsIdentifierCharacter = [...value].some((character) =>
    /^[a-zA-Z0-9_]$/.test(character),
  )
  if (!stem || (stem === "untitled" && !containsIdentifierCharacter)) stem = fallback
  if (stem.startsWith(RESERVED_FORMULA_REFERENCE_PREFIX)) stem = `${fallback}_${stem}`
  return stem
}

function uniqueReference(base: string, references: ReadonlySet<string>): string {
  if (!references.has(base)) return base
  let suffix = 2
  while (references.has(`${base}_${suffix}`)) suffix += 1
  return `${base}_${suffix}`
}

function aggregationReferenceToken(aggregation: PivotAggregation): string {
  return aggregation === "average" ? "mean" : aggregation
}

function valueReferenceStem(field: string, aggregation: PivotAggregation): string {
  return `${referenceStem(field, "value")}_${aggregationReferenceToken(aggregation)}`
}

function isCanonicalValueReference(
  reference: string,
  field: string,
  aggregation: PivotAggregation,
): boolean {
  const stem = valueReferenceStem(field, aggregation)
  return reference === stem || new RegExp(`^${stem}_(?:[2-9]|[1-9][0-9]+)$`).test(reference)
}

export function pivotFormulaReference(displayName: string): string {
  return referenceStem(displayName, "formula")
}

function parseDecimalPlaces(
  value: unknown,
  position: number,
  scope: string,
): PivotDecimalPlaces | string {
  if (value === undefined || value === null) return null
  if (
    typeof value !== "number" ||
    !Number.isInteger(value) ||
    value < 0 ||
    value > PIVOT_DECIMAL_PLACES_MAX
  ) {
    return `Pivot ${position} ${scope} decimal places must be null or an integer from 0 to ${PIVOT_DECIMAL_PLACES_MAX}.`
  }
  return value
}

type ParsedNumberFormatting = {
  number_format: PivotNumberFormat
  decimal_places: PivotDecimalPlaces
  use_grouping: boolean
}

function parseNumberFormatting(
  entry: Record<string, unknown>,
  position: number,
  scope: string,
): ParsedNumberFormatting | string {
  if (entry.number_format === undefined) {
    return `Pivot ${position} ${scope} number format is required.`
  }
  if (entry.decimal_places === undefined) {
    return `Pivot ${position} ${scope} decimal places is required.`
  }
  if (entry.use_grouping === undefined) {
    return `Pivot ${position} ${scope} grouping is required.`
  }
  const decimalPlaces = parseDecimalPlaces(
    entry.decimal_places,
    position,
    scope,
  )
  if (typeof decimalPlaces === "string") return decimalPlaces

  const numberFormat = entry.number_format
  if (typeof numberFormat !== "string" || !NUMBER_FORMATS.has(numberFormat)) {
    return `Pivot ${position} ${scope} has an unsupported number format.`
  }
  const useGrouping = entry.use_grouping
  if (typeof useGrouping !== "boolean") {
    return `Pivot ${position} ${scope} grouping must be a boolean.`
  }
  return {
    number_format: numberFormat as PivotNumberFormat,
    decimal_places: decimalPlaces,
    use_grouping: useGrouping,
  }
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
    const known = zone === "filters"
      ? new Set(["id", "field", "members"])
      : zone === "rows"
        ? new Set([
            "id",
            "field",
            "sort",
            "number_format",
            "decimal_places",
            "use_grouping",
          ])
        : new Set([
            "id",
            "field",
            "number_format",
            "decimal_places",
            "use_grouping",
          ])
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
    } else if (zone === "rows") {
      const formatting = parseNumberFormatting(entry, position, `${zone} placement`)
      if (typeof formatting === "string") return formatting
      if (entry.sort !== undefined && (typeof entry.sort !== "string" || !AXIS_SORTS.has(entry.sort as PivotAxisPlacement["sort"]))) {
        return `Pivot ${position} ${zone} placement has an unsupported sort.`
      }
      placements.push({
        ...cloneLiteral(entry),
        id: entry.id,
        field: entry.field,
        sort: (entry.sort ?? "ascending") as PivotAxisPlacement["sort"],
        ...formatting,
      })
    } else {
      const formatting = parseNumberFormatting(entry, position, `${zone} placement`)
      if (typeof formatting === "string") return formatting
      placements.push({
        ...cloneLiteral(entry),
        id: entry.id,
        field: entry.field,
        ...formatting,
      })
    }
  }
  return placements as PivotFilterPlacement[] | PivotAxisPlacement[]
}

function parseValues(
  raw: unknown,
  position: number,
  placementIds: Set<string>,
  references: Set<string>,
): PivotValuePlacement[] | string {
  if (!Array.isArray(raw)) return `Pivot ${position} values must be a list.`
  const values: PivotValuePlacement[] = []
  for (const entry of raw) {
    if (!isPlainObject(entry)) return `Pivot ${position} value entries must be objects.`
    const futureError = validateFutureFields(
      entry,
      new Set([
        "id",
        "field",
        "aggregation",
        "reference",
        "display_name",
        "sort_rows",
        "color_scale",
        "color_scale_split_by",
        "number_format",
        "decimal_places",
        "use_grouping",
      ]),
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
    const aggregation = entry.aggregation as PivotAggregation
    const reference = entry.reference
    if (!isValidFormulaReference(reference)) {
      return `Pivot ${position} Value reference must be a valid non-reserved identifier.`
    }
    if (!isCanonicalValueReference(reference, entry.field, aggregation)) {
      return `Pivot ${position} Value reference must use the field-first alias ${valueReferenceStem(entry.field, aggregation)} (with a numeric collision suffix when needed).`
    }
    if (references.has(reference)) {
      return `Pivot ${position} has duplicate output reference "${reference}".`
    }
    if (!nonEmptyString(entry.display_name)) return `Pivot ${position} value display name must be non-empty.`
    if (entry.sort_rows !== undefined && (typeof entry.sort_rows !== "string" || !VALUE_SORTS.has(entry.sort_rows as PivotValuePlacement["sort_rows"]))) {
      return `Pivot ${position} value has an unsupported row sort.`
    }
    if (entry.color_scale !== undefined && (typeof entry.color_scale !== "string" || !COLOR_SCALES.has(entry.color_scale as PivotValuePlacement["color_scale"]))) {
      return `Pivot ${position} value has an unsupported color scale.`
    }
    if (entry.color_scale_split_by === undefined) {
      return `Pivot ${position} value color scale split is required.`
    }
    if (
      entry.color_scale_split_by !== null &&
      typeof entry.color_scale_split_by !== "string"
    ) {
      return `Pivot ${position} value color scale split must be a string or null.`
    }
    const formatting = parseNumberFormatting(entry, position, "value")
    if (typeof formatting === "string") return formatting
    placementIds.add(entry.id)
    references.add(reference)
    values.push({
      ...cloneLiteral(entry),
      id: entry.id,
      field: entry.field,
      aggregation,
      reference,
      display_name: entry.display_name,
      sort_rows: (entry.sort_rows ?? "none") as PivotValuePlacement["sort_rows"],
      color_scale: (entry.color_scale ?? "none") as PivotValuePlacement["color_scale"],
      color_scale_split_by: entry.color_scale_split_by,
      ...formatting,
    })
  }
  return values
}

const FORMULA_KEYS = new Set([
  "id",
  "reference",
  "display_name",
  "expression",
  "number_format",
  "decimal_places",
  "use_grouping",
])

function parseFormulaDefinition(
  entry: Record<string, unknown>,
  position: number,
): PivotFormulaPlacement | string {
  const futureError = validateFutureFields(
    entry,
    FORMULA_KEYS,
    position,
    "formula",
  )
  if (futureError) return futureError
  if (!nonEmptyString(entry.id)) {
    return `Pivot ${position} formula id must be a non-empty string.`
  }
  if (!nonEmptyString(entry.display_name)) {
    return `Pivot ${position} formula display name must be non-empty.`
  }
  if (!nonEmptyString(entry.expression)) {
    return `Pivot ${position} formula expression must be non-empty.`
  }
  const reference = entry.reference
  if (!isValidFormulaReference(reference)) {
    return `Pivot ${position} formula reference must be a valid non-reserved identifier.`
  }
  const formatting = parseNumberFormatting(entry, position, "formula")
  if (typeof formatting === "string") return formatting
  return {
    ...cloneLiteral(entry),
    id: entry.id,
    reference,
    display_name: entry.display_name,
    expression: entry.expression,
    ...formatting,
  }
}

type SharedFormulaState = {
  formulas: PivotFormulaPlacement[]
  byId: Map<string, PivotFormulaPlacement>
  references: Set<string>
}

function parseSharedFormulaLibrary(raw: unknown): SharedFormulaState | string {
  if (raw === undefined) {
    return {
      formulas: [],
      byId: new Map(),
      references: new Set(),
    }
  }
  if (!Array.isArray(raw)) return "Explore pivot_formulas config must be a list."
  const state: SharedFormulaState = {
    formulas: [],
    byId: new Map(),
    references: new Set(),
  }
  for (const [index, entry] of raw.entries()) {
    if (!isPlainObject(entry)) return "Explore shared formula entries must be objects."
    const formula = parseFormulaDefinition(entry, index + 1)
    if (typeof formula === "string") return formula
    if (state.byId.has(formula.id)) {
      return `Explore pivot_formulas contains duplicate formula id "${formula.id}".`
    }
    if (state.references.has(formula.reference)) {
      return `Explore pivot_formulas contains duplicate formula reference "${formula.reference}".`
    }
    state.formulas.push(formula)
    state.byId.set(formula.id, formula)
    state.references.add(formula.reference)
  }
  return state
}

function parseFormulaSelections(
  raw: unknown,
  position: number,
  placementIds: Set<string>,
  references: Set<string>,
  shared: SharedFormulaState,
): PivotFormulaPlacement[] | string {
  if (!Array.isArray(raw)) return `Pivot ${position} formulas must be a list.`
  const formulas: PivotFormulaPlacement[] = []
  const selectedIds = new Set<string>()
  for (const entry of raw) {
    if (typeof entry !== "string" || !entry.trim()) {
      return `Pivot ${position} formulas must contain shared formula ids.`
    }
    const formula = shared.byId.get(entry)
    if (!formula) {
      return `Pivot ${position} selects unknown shared formula "${entry}".`
    }
    if (selectedIds.has(formula.id)) {
      return `Pivot ${position} selects formula "${formula.id}" more than once.`
    }
    if (placementIds.has(formula.id)) {
      return `Pivot ${position} has duplicate placement id "${formula.id}".`
    }
    if (references.has(formula.reference)) {
      return `Pivot ${position} has duplicate output reference "${formula.reference}".`
    }
    selectedIds.add(formula.id)
    placementIds.add(formula.id)
    references.add(formula.reference)
    formulas.push(formula)
  }
  return formulas
}

function parseValueOrder(
  raw: unknown,
  position: number,
  values: PivotValuePlacement[],
  formulas: PivotFormulaPlacement[],
): string[] | string {
  const expectedIds = [
    ...values.map((value) => value.id),
    ...formulas.map((formula) => formula.id),
  ]
  if (raw === undefined) {
    return `Pivot ${position} value order is required.`
  }
  if (!Array.isArray(raw) || raw.some((id) => !nonEmptyString(id))) {
    return `Pivot ${position} value order must be a list of output placement ids.`
  }
  const expected = new Set(expectedIds)
  const seen = new Set<string>()
  for (const id of raw) {
    if (seen.has(id)) return `Pivot ${position} value order contains duplicate output id "${id}".`
    if (!expected.has(id)) return `Pivot ${position} value order references unknown output id "${id}".`
    seen.add(id)
  }
  if (seen.size !== expected.size) {
    return `Pivot ${position} value order must contain every Value and selected formula exactly once.`
  }
  return [...raw]
}

function parseV1Pivot(
  raw: Record<string, unknown>,
  position: number,
  sharedFormulas: SharedFormulaState,
): ExplorePivotConfig | string {
  const futureError = validateFutureFields(raw, CARD_KEYS, position, "card")
  if (futureError) return futureError
  if (raw.version !== 1) return `Pivot ${position} version must be 1.`
  if (!nonEmptyString(raw.id)) return `Pivot ${position} id must be a non-empty string.`
  if (!nonEmptyString(raw.name)) return `Pivot ${position} name must be a non-empty string.`
  if (typeof raw.enabled !== "boolean") return `Pivot ${position} enabled state must be a boolean.`
  const placementIds = new Set<string>()
  const references = new Set<string>()
  const filters = parseAxisZone(raw.filters, "filters", position, placementIds)
  if (typeof filters === "string") return filters
  const columns = parseAxisZone(raw.columns, "columns", position, placementIds)
  if (typeof columns === "string") return columns
  const rows = parseAxisZone(raw.rows, "rows", position, placementIds)
  if (typeof rows === "string") return rows
  const values = parseValues(raw.values, position, placementIds, references)
  if (typeof values === "string") return values
  const formulas = parseFormulaSelections(
    raw.formulas,
    position,
    placementIds,
    references,
    sharedFormulas,
  )
  if (typeof formulas === "string") return formulas
  const valueOrder = parseValueOrder(raw.value_order, position, values, formulas)
  if (typeof valueOrder === "string") return valueOrder
  if (values.filter((value) => value.sort_rows !== "none").length > 1) {
    return `Pivot ${position} may have only one active Value row sort.`
  }
  const conditionalSplitIds = new Set([
    ...(columns as PivotAxisPlacement[]).map((column) => column.id),
    ...(rows as PivotAxisPlacement[]).map((row) => row.id),
  ])
  for (const value of values) {
    const splitBy = value.color_scale_split_by
    if (splitBy === null || splitBy === undefined) continue
    if (value.color_scale === "none") {
      return `Pivot ${position} value color scale split requires an active color scale.`
    }
    if (!conditionalSplitIds.has(splitBy)) {
      return `Pivot ${position} value color scale split must reference a placed Row or Column placement.`
    }
  }
  if (!isPlainObject(raw.options)) return `Pivot ${position} options must be an object.`
  const optionError = validateFutureFields(
    raw.options,
    new Set(["row_grand_totals", "column_grand_totals", "sort_by"]),
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
  const activeValueSorts = values.filter((value) => value.sort_rows !== "none")
  const sortBy = raw.options.sort_by === undefined
    ? activeValueSorts[0]?.id ?? null
    : raw.options.sort_by
  if (sortBy !== null && !nonEmptyString(sortBy)) {
    return `Pivot ${position} options.sort_by must be null or a placement id.`
  }
  const sortRow = typeof sortBy === "string" ? (rows as PivotAxisPlacement[]).find((row) => row.id === sortBy) : undefined
  const sortValue = typeof sortBy === "string" ? values.find((value) => value.id === sortBy) : undefined
  if (sortBy !== null && !sortRow && !sortValue) {
    return `Pivot ${position} options.sort_by must reference a placed Row or Value placement.`
  }
  if (sortValue && (sortValue.sort_rows === "none" || activeValueSorts.length !== 1 || activeValueSorts[0].id !== sortValue.id)) {
    return `Pivot ${position} options.sort_by must match the sole active Value row sort.`
  }
  if ((sortRow || sortBy === null) && activeValueSorts.length > 0) {
    return `Pivot ${position} active Value row sort requires options.sort_by to reference that Value.`
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
    formulas,
    value_order: valueOrder,
    options: { ...cloneLiteral(raw.options) as PivotOptions, sort_by: sortBy },
  }
}

export function parseExplorePivots(config: Record<string, unknown>): ExplorePivotsParseResult {
  const sharedFormulas = parseSharedFormulaLibrary(config.pivot_formulas)
  if (typeof sharedFormulas === "string") return { ok: false, error: sharedFormulas }
  if (!Object.prototype.hasOwnProperty.call(config, "pivots")) {
    return { ok: true, pivots: [], formulas: sharedFormulas.formulas }
  }
  if (!Array.isArray(config.pivots)) return { ok: false, error: "Explore pivots config must be a list." }

  const pivots: ExplorePivotConfig[] = []
  const ids = new Set<string>()
  const names = new Set<string>()
  for (const [index, raw] of config.pivots.entries()) {
    const position = index + 1
    if (!isPlainObject(raw)) return { ok: false, error: `Pivot ${position} must be an object.` }
    const pivot = parseV1Pivot(raw, position, sharedFormulas)
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
  return { ok: true, pivots, formulas: sharedFormulas.formulas }
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
    formulas: [],
    value_order: [],
    options: { row_grand_totals: true, column_grand_totals: true, sort_by: null },
  }
}

export function nextPivotPlacementId(pivot: ExplorePivotConfig, prefix: string): string {
  const ids = new Set([
    ...pivot.filters.map((placement) => placement.id),
    ...pivot.columns.map((placement) => placement.id),
    ...pivot.rows.map((placement) => placement.id),
    ...pivot.values.map((placement) => placement.id),
    ...pivotFormulas(pivot).map((formula) => formula.id),
  ])
  let suffix = 1
  while (ids.has(`${prefix}_${suffix}`)) suffix += 1
  return `${prefix}_${suffix}`
}

export function explorePivotLabel(pivotOrIndex: ExplorePivotConfig | number): string {
  return typeof pivotOrIndex === "number" ? `Pivot ${pivotOrIndex + 1}` : pivotOrIndex.name
}

export function pivotFormulas(pivot: ExplorePivotConfig): PivotFormulaPlacement[] {
  return pivot.formulas
}

export function pivotOutputs(pivot: ExplorePivotConfig): PivotOutputPlacement[] {
  const outputs = new Map<string, PivotOutputPlacement>([
    ...pivot.values.map((value) => [value.id, value] as const),
    ...pivotFormulas(pivot).map((formula) => [formula.id, formula] as const),
  ])
  if (pivot.value_order.length !== outputs.size) {
    throw new Error("Pivot value_order must contain every Value and selected formula exactly once.")
  }
  const ordered = pivot.value_order.map((id) => {
    const output = outputs.get(id)
    if (!output) throw new Error(`Pivot value_order references unknown output id "${id}".`)
    outputs.delete(id)
    return output
  })
  if (outputs.size !== 0) {
    throw new Error("Pivot value_order contains duplicate output ids.")
  }
  return ordered
}

/** True when the Pivot has an output selected for calculation and display. */
export function isPivotConfigured(pivot: ExplorePivotConfig): boolean {
  return pivotOutputs(pivot).length > 0
}

export function isPivotFormulaPlacement(
  placement: PivotOutputPlacement,
): placement is PivotFormulaPlacement {
  return !("aggregation" in placement)
}

export function pivotValueReference(value: PivotValuePlacement): string {
  return value.reference
}

export function nextPivotValueReference(
  pivot: ExplorePivotConfig,
  field: string,
  aggregation: PivotAggregation,
  excludingValueId?: string,
): string {
  const references = new Set([
    ...pivot.values
      .filter((value) => value.id !== excludingValueId)
      .map(pivotValueReference),
    ...pivotFormulas(pivot).map((formula) => formula.reference),
  ])
  return uniqueReference(
    valueReferenceStem(field, aggregation),
    references,
  )
}

export function nextPivotFormulaId(
  formulasOrPivot: readonly PivotFormulaPlacement[] | ExplorePivotConfig,
): string {
  const formulas = Array.isArray(formulasOrPivot)
    ? formulasOrPivot
    : pivotFormulas(formulasOrPivot as ExplorePivotConfig)
  const ids = new Set(formulas.map(({ id }) => id))
  let suffix = 1
  while (ids.has(`formula_${suffix}`)) suffix += 1
  return `formula_${suffix}`
}

export function createPivotFormula(
  formulasOrPivot: readonly PivotFormulaPlacement[] | ExplorePivotConfig,
  displayName?: string,
  expression?: string,
): PivotFormulaPlacement {
  const formulas = Array.isArray(formulasOrPivot)
    ? formulasOrPivot
    : pivotFormulas(formulasOrPivot as ExplorePivotConfig)
  const id = nextPivotFormulaId(formulas)
  const name = displayName?.trim() || `Formula ${id.slice("formula_".length)}`
  const references = new Set(formulas.map((formula) => formula.reference))
  const reference = uniqueReference(pivotFormulaReference(name), references)
  return {
    id,
    reference,
    display_name: name,
    expression: expression?.trim() || "pl.lit(None)",
    number_format: "general",
    decimal_places: null,
    use_grouping: true,
  }
}

export type PersistedExplorePivotConfig = Omit<ExplorePivotConfig, "formulas"> & {
  formulas: string[]
}

/** Persist only shared formula ids; full definitions live once in pivot_formulas. */
export function serializeExplorePivot(
  pivot: ExplorePivotConfig,
): PersistedExplorePivotConfig {
  return {
    ...cloneLiteral(pivot),
    values: pivot.values.map((value) => ({
      ...cloneLiteral(value),
      reference: pivotValueReference(value),
    })),
    formulas: pivotFormulas(pivot).map((formula) => formula.id),
    value_order: [...pivot.value_order],
  }
}

export function pivotCalculationIdentity(pivot: ExplorePivotConfig): string {
  // Mirror the backend calculation key: exact members are deduplicated and
  // ordered by their canonical serialisation (code-unit order, never locale
  // collation), so equivalent filter selections share one identity.
  const filters = pivot.filters.map((filter) => {
    const byIdentity = new Map(
      filter.members.map((member) => [
        JSON.stringify({ kind: member.kind, value: member.value }),
        { kind: member.kind, value: member.value },
      ]),
    )
    return {
      field: filter.field,
      members: [...byIdentity.entries()]
        .sort(([left], [right]) => (left < right ? -1 : left > right ? 1 : 0))
        .map(([, member]) => member),
    }
  })
  return JSON.stringify({
    filters,
    columns: pivot.columns.map((placement) => placement.field),
    rows: pivot.rows.map((placement) => ({
      field: placement.field,
      sort: pivot.options.sort_by === placement.id ? placement.sort ?? "ascending" : "ascending",
    })),
    values: pivot.values.map((value) => ({
      id: value.id,
      field: value.field,
      aggregation: value.aggregation,
      reference: pivotValueReference(value),
      sort_rows: pivot.options.sort_by === value.id ? value.sort_rows ?? "none" : "none",
    })),
    formulas: pivotFormulas(pivot).map((formula) => ({
      id: formula.id,
      reference: formula.reference,
      expression: formula.expression,
    })),
    value_order: [...pivot.value_order],
    options: {
      row_grand_totals: pivot.options.row_grand_totals,
      column_grand_totals: pivot.options.column_grand_totals,
    },
  })
}

/** The retained-result fields freshness depends on; structurally satisfied by
 * the node-results store's cached pivot entries. */
export type PivotResultFreshnessEntry = {
  result: { dataframe_cache_key: string } | null
  calculationIdentity: string
}

/**
 * One shared definition of "this retained Pivot result is current": it exists,
 * it was calculated from the current Explore dataframe cache generation, and
 * its calculation identity matches the current configuration.
 */
export function isPivotResultFresh(
  entry: PivotResultFreshnessEntry | null | undefined,
  dataframeCacheKey: string | null | undefined,
  calculationIdentity: string,
): boolean {
  return Boolean(
    entry?.result
      && dataframeCacheKey
      && entry.result.dataframe_cache_key === dataframeCacheKey
      && entry.calculationIdentity === calculationIdentity,
  )
}

export function isNumericPivotDtype(dtype: string): boolean {
  return isNumericDtype(dtype)
}

export function pivotAggregationsForDtype(dtype: string): readonly PivotAggregation[] {
  if (isNumericPivotDtype(dtype)) return PIVOT_AGGREGATIONS
  if (/^(?:list|array|struct|object)(?:\(|\{|$)/i.test(dtype.trim())) return ["count"]
  return ["count", "distinct_count", "min", "max"]
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
