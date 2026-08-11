import type { ExplorePivotMemberKey } from "../../api/types"
import type { ExplorePivotConfig } from "./pivotConfig"

export const CHART_CONFIG_VERSION = 1 as const

export const CHART_PRESETS = [
  "clustered_columns",
  "stacked_columns",
  "lines",
  "column_line",
  "column_line_secondary",
  "stacked_column_line",
] as const

export const CHART_NUMBER_FORMATS = [
  "inherit",
  "number",
  "integer",
  "percent",
  "currency_gbp",
  "currency_usd",
  "currency_eur",
] as const

export type ChartMark = "column" | "line" | "area"
export type ChartAxis = "primary" | "secondary"
export type ChartNumberFormat = (typeof CHART_NUMBER_FORMATS)[number]
export type ChartPreset = (typeof CHART_PRESETS)[number]

export type ChartStyle = {
  mark: ChartMark
  axis: ChartAxis
  stack_group: string | null
  color: string | null
  data_labels: boolean
  markers: boolean
  [key: string]: unknown
}

export type ChartValueEncoding = ChartStyle & {
  id: string
  value_id: string
}

export type ChartSeriesOverride = ChartStyle & {
  id: string
  series_key: string
}

export type ChartAxisConfig = {
  title: string
  minimum: number | null
  maximum: number | null
  number_format: ChartNumberFormat
  [key: string]: unknown
}

export type ChartCategory = {
  source: "rows"
  include_subtotals: boolean
  include_grand_total: boolean
  label_rotation: number
  [key: string]: unknown
}

export type ExploreChartConfig = {
  version: typeof CHART_CONFIG_VERSION
  id: string
  name: string
  enabled: boolean
  pivot_id: string | null
  kind: "combo"
  category: ChartCategory
  value_encodings: ChartValueEncoding[]
  series_overrides: ChartSeriesOverride[]
  axes: {
    primary: ChartAxisConfig
    secondary: ChartAxisConfig
    [key: string]: unknown
  }
  legend: {
    visible: boolean
    position: "top" | "right" | "bottom" | "left"
    [key: string]: unknown
  }
  [key: string]: unknown
}

export type ExploreChartsParseResult =
  | { ok: true; charts: ExploreChartConfig[] }
  | { ok: false; error: string }

const CARD_KEYS = new Set([
  "version",
  "id",
  "name",
  "enabled",
  "pivot_id",
  "kind",
  "category",
  "value_encodings",
  "series_overrides",
  "axes",
  "legend",
])
const STYLE_KEYS = new Set([
  "id",
  "mark",
  "axis",
  "stack_group",
  "color",
  "data_labels",
  "markers",
])
const AXIS_KEYS = new Set([
  "title",
  "minimum",
  "maximum",
  "number_format",
])
const CATEGORY_KEYS = new Set([
  "source",
  "include_subtotals",
  "include_grand_total",
  "label_rotation",
])
const LEGEND_KEYS = new Set(["visible", "position"])
const MARKS = new Set<ChartMark>(["column", "line", "area"])
const AXES = new Set<ChartAxis>(["primary", "secondary"])
const NUMBER_FORMATS = new Set<string>(CHART_NUMBER_FORMATS)
const LEGEND_POSITIONS = new Set(["top", "right", "bottom", "left"])
const STRICT_HEX_COLOUR = /^#[0-9A-Fa-f]{6}$/

function isPlainObject(value: unknown): value is Record<string, unknown> {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    return false
  }
  const prototype = Object.getPrototypeOf(value)
  return prototype === Object.prototype || prototype === null
}

function isSimpleLiteral(value: unknown): boolean {
  if (
    value === null ||
    typeof value === "string" ||
    typeof value === "boolean"
  ) {
    return true
  }
  if (typeof value === "number") return Number.isFinite(value)
  if (Array.isArray(value)) return value.every(isSimpleLiteral)
  if (isPlainObject(value)) return Object.values(value).every(isSimpleLiteral)
  return false
}

function cloneLiteral<T>(value: T): T {
  if (Array.isArray(value)) return value.map(cloneLiteral) as T
  if (isPlainObject(value)) {
    return Object.fromEntries(
      Object.entries(value).map(([key, item]) => [key, cloneLiteral(item)]),
    ) as T
  }
  return value
}

function nonEmptyString(value: unknown): value is string {
  return typeof value === "string" && value.trim().length > 0
}

function validateFutureFields(
  raw: Record<string, unknown>,
  known: ReadonlySet<string>,
  where: string,
): string | null {
  for (const [key, value] of Object.entries(raw)) {
    if (!known.has(key) && !isSimpleLiteral(value)) {
      return `${where} field "${key}" must contain only simple literal values.`
    }
  }
  return null
}

function parseStyle(
  raw: unknown,
  where: string,
  identityKey: "value_id" | "series_key",
): (ChartStyle & Record<string, unknown>) | string {
  if (!isPlainObject(raw)) return `${where} must be an object.`
  const otherIdentityKey = identityKey === "value_id" ? "series_key" : "value_id"
  if (otherIdentityKey in raw) {
    return `${where} contains misplaced identity field ${otherIdentityKey}.`
  }
  const futureError = validateFutureFields(
    raw,
    new Set([...STYLE_KEYS, identityKey]),
    where,
  )
  if (futureError) return futureError
  if (!nonEmptyString(raw.id) || !nonEmptyString(raw[identityKey])) {
    return `${where} requires non-empty id and ${identityKey}.`
  }
  if (typeof raw.mark !== "string" || !MARKS.has(raw.mark as ChartMark)) {
    return `${where} has invalid mark.`
  }
  if (typeof raw.axis !== "string" || !AXES.has(raw.axis as ChartAxis)) {
    return `${where} has invalid axis.`
  }
  if (
    raw.stack_group !== null &&
    !nonEmptyString(raw.stack_group)
  ) {
    return `${where} stack_group must be null or a non-empty string.`
  }
  if (raw.mark !== "column" && raw.stack_group !== null) {
    return `${where} stack_group is valid only for columns.`
  }
  if (
    raw.color !== null &&
    (typeof raw.color !== "string" || !STRICT_HEX_COLOUR.test(raw.color))
  ) {
    return `${where} color must be #RRGGBB or null.`
  }
  if (
    typeof raw.data_labels !== "boolean" ||
    typeof raw.markers !== "boolean"
  ) {
    return `${where} labels and markers must be boolean.`
  }
  return cloneLiteral(raw) as ChartStyle & Record<string, unknown>
}

function parseAxis(raw: unknown, where: string): ChartAxisConfig | string {
  if (!isPlainObject(raw)) return `${where} must be an object.`
  const futureError = validateFutureFields(raw, AXIS_KEYS, where)
  if (futureError) return futureError
  if (typeof raw.title !== "string") return `${where}.title must be a string.`

  const minimum = raw.minimum
  const maximum = raw.maximum
  if (
    minimum !== null &&
    (typeof minimum !== "number" || !Number.isFinite(minimum))
  ) {
    return `${where}.minimum must be a finite number or null.`
  }
  if (
    maximum !== null &&
    (typeof maximum !== "number" || !Number.isFinite(maximum))
  ) {
    return `${where}.maximum must be a finite number or null.`
  }
  if (minimum !== null && maximum !== null && minimum >= maximum) {
    return `${where}.minimum must be less than maximum.`
  }
  if (
    typeof raw.number_format !== "string" ||
    !NUMBER_FORMATS.has(raw.number_format)
  ) {
    return `${where}.number_format is invalid.`
  }
  return cloneLiteral(raw) as ChartAxisConfig
}

function chartDefaults(id: string, name: string): ExploreChartConfig {
  return {
    version: 1,
    id,
    name,
    enabled: true,
    pivot_id: null,
    kind: "combo",
    category: {
      source: "rows",
      include_subtotals: false,
      include_grand_total: false,
      label_rotation: 0,
    },
    value_encodings: [],
    series_overrides: [],
    axes: {
      primary: {
        title: "",
        minimum: null,
        maximum: null,
        number_format: "inherit",
      },
      secondary: {
        title: "",
        minimum: null,
        maximum: null,
        number_format: "inherit",
      },
    },
    legend: { visible: true, position: "bottom" },
  }
}

function migrateChart(
  raw: Record<string, unknown>,
  position: number,
  defaultName: string,
): ExploreChartConfig | string {
  if (!nonEmptyString(raw.id)) {
    return `Chart ${position} id must be a non-empty string.`
  }
  if (typeof raw.enabled !== "boolean") {
    return `Chart ${position} enabled state must be a boolean.`
  }
  const conflicting = Object.keys(raw).filter(
    (key) => key !== "id" && key !== "enabled" && CARD_KEYS.has(key),
  )
  if (conflicting.length > 0) {
    return `Chart ${position} versionless card contains version-1 fields.`
  }
  const futureError = validateFutureFields(
    raw,
    new Set(["id", "enabled"]),
    `Chart ${position}`,
  )
  if (futureError) return futureError
  return {
    ...chartDefaults(raw.id, defaultName),
    ...cloneLiteral(raw),
    version: 1,
    id: raw.id,
    enabled: raw.enabled,
  }
}

function parseV1Chart(
  raw: Record<string, unknown>,
  position: number,
): ExploreChartConfig | string {
  const futureError = validateFutureFields(
    raw,
    CARD_KEYS,
    `Chart ${position}`,
  )
  if (futureError) return futureError
  if (raw.version !== 1) return `Chart ${position} version must be 1.`
  if (!nonEmptyString(raw.id)) {
    return `Chart ${position} id must be a non-empty string.`
  }
  if (!nonEmptyString(raw.name)) {
    return `Chart ${position} name must be a non-empty string.`
  }
  if (typeof raw.enabled !== "boolean") {
    return `Chart ${position} enabled state must be a boolean.`
  }
  if (
    !Object.prototype.hasOwnProperty.call(raw, "pivot_id") ||
    (raw.pivot_id !== null && !nonEmptyString(raw.pivot_id))
  ) {
    return `Chart ${position} pivot_id must be null or a non-empty string.`
  }
  if (raw.kind !== "combo") return `Chart ${position} kind must be combo.`

  if (!isPlainObject(raw.category)) {
    return `Chart ${position} category must be an object.`
  }
  const categoryFutureError = validateFutureFields(
    raw.category,
    CATEGORY_KEYS,
    `Chart ${position} category`,
  )
  if (categoryFutureError) return categoryFutureError
  if (
    raw.category.source !== "rows" ||
    typeof raw.category.include_subtotals !== "boolean" ||
    typeof raw.category.include_grand_total !== "boolean" ||
    typeof raw.category.label_rotation !== "number" ||
    !Number.isInteger(raw.category.label_rotation) ||
    raw.category.label_rotation < -90 ||
    raw.category.label_rotation > 90
  ) {
    return `Chart ${position} category is invalid.`
  }

  if (!Array.isArray(raw.value_encodings)) {
    return `Chart ${position} value_encodings must be a list.`
  }
  if (!Array.isArray(raw.series_overrides)) {
    return `Chart ${position} series_overrides must be a list.`
  }
  const nestedIds = new Set<string>()
  const valueIds = new Set<string>()
  const seriesKeys = new Set<string>()
  const valueEncodings: ChartValueEncoding[] = []
  const seriesOverrides: ChartSeriesOverride[] = []

  for (const rawEncoding of raw.value_encodings) {
    const parsed = parseStyle(
      rawEncoding,
      `Chart ${position} encoding`,
      "value_id",
    )
    if (typeof parsed === "string") return parsed
    const encoding = parsed as ChartValueEncoding
    if (nestedIds.has(encoding.id)) {
      return `Chart ${position} has duplicate encoding id "${encoding.id}".`
    }
    if (valueIds.has(encoding.value_id)) {
      return `Chart ${position} has duplicate value_id "${encoding.value_id}".`
    }
    nestedIds.add(encoding.id)
    valueIds.add(encoding.value_id)
    valueEncodings.push(encoding)
  }
  for (const rawOverride of raw.series_overrides) {
    const parsed = parseStyle(
      rawOverride,
      `Chart ${position} override`,
      "series_key",
    )
    if (typeof parsed === "string") return parsed
    const override = parsed as ChartSeriesOverride
    if (nestedIds.has(override.id)) {
      return `Chart ${position} has duplicate encoding id "${override.id}".`
    }
    if (seriesKeys.has(override.series_key)) {
      return `Chart ${position} has duplicate series_key.`
    }
    nestedIds.add(override.id)
    seriesKeys.add(override.series_key)
    seriesOverrides.push(override)
  }

  if (!isPlainObject(raw.axes)) {
    return `Chart ${position} axes must be an object.`
  }
  const axesFutureError = validateFutureFields(
    raw.axes,
    new Set(["primary", "secondary"]),
    `Chart ${position} axes`,
  )
  if (axesFutureError) return axesFutureError
  const primary = parseAxis(raw.axes.primary, `Chart ${position} primary axis`)
  if (typeof primary === "string") return primary
  const secondary = parseAxis(
    raw.axes.secondary,
    `Chart ${position} secondary axis`,
  )
  if (typeof secondary === "string") return secondary

  if (!isPlainObject(raw.legend)) {
    return `Chart ${position} legend must be an object.`
  }
  const legendFutureError = validateFutureFields(
    raw.legend,
    LEGEND_KEYS,
    `Chart ${position} legend`,
  )
  if (legendFutureError) return legendFutureError
  if (
    typeof raw.legend.visible !== "boolean" ||
    typeof raw.legend.position !== "string" ||
    !LEGEND_POSITIONS.has(raw.legend.position)
  ) {
    return `Chart ${position} legend is invalid.`
  }

  return {
    ...cloneLiteral(raw),
    version: 1,
    id: raw.id,
    name: raw.name,
    enabled: raw.enabled,
    pivot_id: raw.pivot_id,
    kind: "combo",
    category: cloneLiteral(raw.category) as ChartCategory,
    value_encodings: valueEncodings,
    series_overrides: seriesOverrides,
    axes: { ...cloneLiteral(raw.axes), primary, secondary },
    legend: cloneLiteral(raw.legend) as ExploreChartConfig["legend"],
  }
}

export function parseExploreCharts(
  config: Record<string, unknown>,
): ExploreChartsParseResult {
  if (!Object.prototype.hasOwnProperty.call(config, "charts")) {
    return { ok: true, charts: [] }
  }
  if (!Array.isArray(config.charts)) {
    return { ok: false, error: "Explore charts config must be a list." }
  }

  const charts: ExploreChartConfig[] = []
  const ids = new Set<string>()
  const names = new Set<string>()
  const allocatedNames = new Set(
    config.charts.flatMap((raw) =>
      isPlainObject(raw) &&
      Object.prototype.hasOwnProperty.call(raw, "version") &&
      nonEmptyString(raw.name)
        ? [raw.name.trim().toLowerCase()]
        : [],
    ),
  )
  let nextNameSuffix = 1
  for (const [index, raw] of config.charts.entries()) {
    const position = index + 1
    if (!isPlainObject(raw)) {
      return { ok: false, error: `Chart ${position} must be an object.` }
    }
    if (!Object.prototype.hasOwnProperty.call(raw, "id")) {
      return { ok: false, error: `Chart ${position} requires an id.` }
    }
    let chart: ExploreChartConfig | string
    if (Object.prototype.hasOwnProperty.call(raw, "version")) {
      chart = parseV1Chart(raw, position)
    } else {
      while (allocatedNames.has(`chart ${nextNameSuffix}`)) {
        nextNameSuffix += 1
      }
      const defaultName = `Chart ${nextNameSuffix}`
      allocatedNames.add(defaultName.toLowerCase())
      nextNameSuffix += 1
      chart = migrateChart(raw, position, defaultName)
    }
    if (typeof chart === "string") return { ok: false, error: chart }
    if (ids.has(chart.id)) {
      return {
        ok: false,
        error: `Explore charts config contains duplicate chart id "${chart.id}".`,
      }
    }
    const nameKey = chart.name.trim().toLowerCase()
    if (names.has(nameKey)) {
      return {
        ok: false,
        error: `Explore charts config contains duplicate chart name "${chart.name}".`,
      }
    }
    ids.add(chart.id)
    names.add(nameKey)
    charts.push(chart)
  }
  return { ok: true, charts }
}

export function nextExploreChartId(
  charts: readonly ExploreChartConfig[],
): string {
  const ids = new Set(charts.map(({ id }) => id))
  let suffix = 1
  while (ids.has(`chart_${suffix}`)) suffix += 1
  return `chart_${suffix}`
}

export function nextExploreChartName(
  charts: readonly ExploreChartConfig[],
): string {
  const names = new Set(charts.map(({ name }) => name.trim().toLowerCase()))
  let suffix = 1
  while (names.has(`chart ${suffix}`)) suffix += 1
  return `Chart ${suffix}`
}

export function createExploreChart(
  charts: readonly ExploreChartConfig[],
): ExploreChartConfig {
  const id = nextExploreChartId(charts)
  const preferredName = `Chart ${id.slice("chart_".length)}`
  const existingNames = new Set(
    charts.map(({ name }) => name.trim().toLowerCase()),
  )
  return chartDefaults(
    id,
    existingNames.has(preferredName.toLowerCase())
      ? nextExploreChartName(charts)
      : preferredName,
  )
}

export function exploreChartLabel(
  chartOrIndex: ExploreChartConfig | number,
): string {
  return typeof chartOrIndex === "number"
    ? `Chart ${chartOrIndex + 1}`
    : chartOrIndex.name
}

export type ExploreChartSourceResolution =
  | { status: "draft" }
  | { status: "missing"; pivotId: string }
  | { status: "resolved"; pivot: ExplorePivotConfig }

export function resolveExploreChartSource(
  chart: ExploreChartConfig,
  pivots: readonly ExplorePivotConfig[],
): ExploreChartSourceResolution {
  if (chart.pivot_id === null) return { status: "draft" }
  const pivot = pivots.find(({ id }) => id === chart.pivot_id)
  return pivot
    ? { status: "resolved", pivot }
    : { status: "missing", pivotId: chart.pivot_id }
}

export function dependentChartsForPivot(
  charts: readonly ExploreChartConfig[],
  pivotId: string,
): ExploreChartConfig[] {
  return charts.filter(({ pivot_id }) => pivot_id === pivotId)
}

function defaultEncoding(
  id: string,
  valueId: string,
): ChartValueEncoding {
  return {
    id,
    value_id: valueId,
    mark: "column",
    axis: "primary",
    stack_group: null,
    color: null,
    data_labels: false,
    markers: false,
  }
}

export function seedValueEncodings(
  pivot: ExplorePivotConfig,
): ChartValueEncoding[] {
  return pivot.values.map(({ id: valueId }, index) =>
    defaultEncoding(`encoding_${index + 1}`, valueId),
  )
}

export function applyChartPreset(
  chart: ExploreChartConfig,
  preset: ChartPreset,
  pivot: ExplorePivotConfig,
): ExploreChartConfig {
  const existingByValueId = new Map(
    chart.value_encodings.map((encoding) => [encoding.value_id, encoding]),
  )
  const unavailableIds = new Set(
    chart.value_encodings.map((encoding) => encoding.id),
  )
  const assignedIds = new Set<string>()

  const nextId = (valueId: string): string => {
    const existing = existingByValueId.get(valueId)
    if (existing && !assignedIds.has(existing.id)) {
      assignedIds.add(existing.id)
      return existing.id
    }
    let suffix = 1
    while (
      unavailableIds.has(`encoding_${suffix}`) ||
      assignedIds.has(`encoding_${suffix}`)
    ) {
      suffix += 1
    }
    const id = `encoding_${suffix}`
    assignedIds.add(id)
    return id
  }

  const lastValueIndex = pivot.values.length - 1
  const valueEncodings = pivot.values.map(({ id: valueId }, index) => {
    const existing = existingByValueId.get(valueId)
    const comboLine =
      pivot.values.length > 1 &&
      index === lastValueIndex &&
      (preset === "column_line" ||
        preset === "column_line_secondary" ||
        preset === "stacked_column_line")
    const mark: ChartMark = preset === "lines" || comboLine ? "line" : "column"
    const axis: ChartAxis =
      comboLine && preset === "column_line_secondary"
        ? "secondary"
        : "primary"
    const stackGroup =
      mark === "column" &&
      (preset === "stacked_columns" || preset === "stacked_column_line")
        ? "stack_1"
        : null
    return {
      ...(existing ? cloneLiteral(existing) : {}),
      ...defaultEncoding(nextId(valueId), valueId),
      mark,
      axis,
      stack_group: stackGroup,
      markers: mark !== "column",
    }
  })

  return {
    ...cloneLiteral(chart),
    value_encodings: valueEncodings,
    series_overrides: [],
  }
}

type SeriesPathMember = Pick<ExplorePivotMemberKey, "kind" | "value">

export function exploreChartSeriesKey(
  valueId: string,
  columnPath:
    | { members: readonly SeriesPathMember[] }
    | readonly SeriesPathMember[],
): string {
  const members = "members" in columnPath ? columnPath.members : columnPath
  return JSON.stringify({
    version: 1,
    value_id: valueId,
    column_path: members.map(({ kind, value }) => ({ kind, value })),
  })
}
