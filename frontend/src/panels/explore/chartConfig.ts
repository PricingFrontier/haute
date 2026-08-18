import type { ExplorePivotMemberKey } from "../../api/types"
import { pivotOutputs } from "./pivotConfig"
import type { ExplorePivotConfig } from "./pivotConfig"

export const CHART_CONFIG_VERSION = 1 as const

// Combo leads as the general category and default (as in Excel), followed
// by the three column layouts. Applying Combo seeds columns with the last
// Value as a line; every mixed arrangement is then composed through the
// per-Value chart-type and axis controls and detects as Combo.
export const CHART_PRESETS = [
  "combo",
  "clustered_columns",
  "stacked_columns",
  "hundred_percent_stacked_columns",
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
export type ChartOrientation = "vertical" | "horizontal"
export type ChartNumberFormat = (typeof CHART_NUMBER_FORMATS)[number]
export type ChartPreset = (typeof CHART_PRESETS)[number]

export type ChartStyle = {
  mark: ChartMark
  axis: ChartAxis
  stack_group: string | null
  stack_normalize: boolean
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

export type ChartSecondaryAxisConfig = ChartAxisConfig & {
  enabled: boolean
}

export type ChartCategory = {
  source: "rows"
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
  orientation: ChartOrientation
  category: ChartCategory
  value_encodings: ChartValueEncoding[]
  series_overrides: ChartSeriesOverride[]
  axes: {
    primary: ChartAxisConfig
    secondary: ChartSecondaryAxisConfig
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
  "orientation",
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
  "stack_normalize",
  "color",
  "data_labels",
  "markers",
])
const ORIENTATIONS = new Set<ChartOrientation>(["vertical", "horizontal"])
const AXIS_KEYS = new Set([
  "title",
  "minimum",
  "maximum",
  "number_format",
])
const CATEGORY_KEYS = new Set([
  "source",
  "include_grand_total",
  "label_rotation",
])
const LEGEND_KEYS = new Set(["visible", "position"])
const MARKS = new Set<ChartMark>(["column", "line", "area"])
const AXES = new Set<ChartAxis>(["primary", "secondary"])
const NUMBER_FORMATS = new Set<string>(CHART_NUMBER_FORMATS)
const LEGEND_POSITIONS = new Set(["top", "right", "bottom", "left"])
const STRICT_HEX_COLOUR = /^#[0-9A-Fa-f]{6}$/
const SERIES_KEY_FIELDS = new Set(["version", "value_id", "column_path"])
const SERIES_MEMBER_FIELDS = new Set(["kind", "value"])
const SERIES_MEMBER_KINDS = new Set<ExplorePivotMemberKey["kind"]>([
  "null",
  "nan",
  "string",
  "integer",
  "date",
  "datetime",
  "time",
  "decimal",
  "boolean",
  "float",
])

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

function hasExactFields(
  value: Record<string, unknown>,
  fields: ReadonlySet<string>,
): boolean {
  const keys = Object.keys(value)
  return keys.length === fields.size && keys.every((key) => fields.has(key))
}

function isCanonicalSeriesMember(
  value: unknown,
): value is ExplorePivotMemberKey {
  if (!isPlainObject(value) || !hasExactFields(value, SERIES_MEMBER_FIELDS)) {
    return false
  }
  if (
    typeof value.kind !== "string"
    || !SERIES_MEMBER_KINDS.has(value.kind as ExplorePivotMemberKey["kind"])
  ) {
    return false
  }
  switch (value.kind) {
    case "null":
    case "nan":
      return value.value === null
    case "boolean":
      return typeof value.value === "boolean"
    case "float":
      return typeof value.value === "number" && Number.isFinite(value.value)
    default:
      return typeof value.value === "string"
  }
}

type CanonicalSeriesKey = {
  version: 1
  value_id: string
  column_path: ExplorePivotMemberKey[]
}

function parseCanonicalSeriesKey(seriesKey: string): CanonicalSeriesKey {
  let raw: unknown
  try {
    raw = JSON.parse(seriesKey)
  } catch {
    throw new Error("Chart series key must be canonical JSON.")
  }
  if (
    !isPlainObject(raw)
    || !hasExactFields(raw, SERIES_KEY_FIELDS)
    || raw.version !== 1
    || !nonEmptyString(raw.value_id)
    || !Array.isArray(raw.column_path)
  ) {
    throw new Error("Chart series key must be a canonical version-1 identity.")
  }
  if (!raw.column_path.every(isCanonicalSeriesMember)) {
    throw new Error("Chart series key has an invalid column member.")
  }
  return {
    version: 1,
    value_id: raw.value_id,
    column_path: raw.column_path,
  }
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
  if (typeof raw.stack_normalize !== "boolean") {
    return `${where} stack_normalize must be a boolean.`
  }
  if (raw.stack_normalize && raw.stack_group === null) {
    return `${where} stack_normalize requires a stack group.`
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

function parseAxis(
  raw: unknown,
  where: string,
  secondary = false,
): ChartAxisConfig | string {
  if (!isPlainObject(raw)) return `${where} must be an object.`
  const known = secondary ? new Set([...AXIS_KEYS, "enabled"]) : AXIS_KEYS
  const futureError = validateFutureFields(raw, known, where)
  if (futureError) return futureError
  if (secondary && typeof raw.enabled !== "boolean") {
    return `${where}.enabled must be a boolean.`
  }
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
    orientation: "vertical",
    category: {
      source: "rows",
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
        enabled: true,
      },
    },
    legend: { visible: true, position: "bottom" },
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
  if (
    typeof raw.orientation !== "string" ||
    !ORIENTATIONS.has(raw.orientation as ChartOrientation)
  ) {
    return `Chart ${position} orientation must be vertical or horizontal.`
  }

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
    try {
      const identity = parseCanonicalSeriesKey(override.series_key)
      override.series_key = exploreChartSeriesKey(
        identity.value_id,
        identity.column_path,
      )
    } catch (error) {
      const message = error instanceof Error ? error.message : "Unknown error."
      return `Chart ${position} override series_key is invalid: ${message}`
    }
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
  const stackIdentities = new Map<
    string,
    { normalize: boolean; axis: ChartAxis }
  >()
  for (const style of [...valueEncodings, ...seriesOverrides]) {
    if (style.stack_group === null) continue
    const existing = stackIdentities.get(style.stack_group)
    if (existing === undefined) {
      stackIdentities.set(style.stack_group, {
        normalize: style.stack_normalize,
        axis: style.axis,
      })
    } else if (
      existing.normalize !== style.stack_normalize ||
      existing.axis !== style.axis
    ) {
      return (
        `Chart ${position} styles sharing stack group ` +
        `"${style.stack_group}" must agree on stack_normalize and axis.`
      )
    }
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
  const parsedSecondary = parseAxis(
    raw.axes.secondary,
    `Chart ${position} secondary axis`,
    true,
  )
  if (typeof parsedSecondary === "string") return parsedSecondary
  const secondary = parsedSecondary as ChartSecondaryAxisConfig
  if (
    !secondary.enabled &&
    [...valueEncodings, ...seriesOverrides].some(
      ({ axis }) => axis === "secondary",
    )
  ) {
    return `Chart ${position} secondary axis is disabled but a style uses it.`
  }

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
    orientation: raw.orientation as ChartOrientation,
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
  for (const [index, raw] of config.charts.entries()) {
    const position = index + 1
    if (!isPlainObject(raw)) {
      return { ok: false, error: `Chart ${position} must be an object.` }
    }
    const chart = parseV1Chart(raw, position)
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
    stack_normalize: false,
    color: null,
    data_labels: false,
    markers: false,
  }
}

/**
 * Seeds the Combo default arrangement for a newly sourced chart: columns
 * with the last Value as an ungrouped primary line (a single Value seeds one
 * plain column), so the gallery opens on its leftmost, default option.
 */
export function seedValueEncodings(
  pivot: ExplorePivotConfig,
): ChartValueEncoding[] {
  const outputs = pivotOutputs(pivot)
  const lastValueIndex = outputs.length - 1
  return outputs.map(({ id: valueId }, index) => {
    const comboLine = outputs.length > 1 && index === lastValueIndex
    return {
      ...defaultEncoding(`encoding_${index + 1}`, valueId),
      ...(comboLine ? { mark: "line" as ChartMark, markers: true } : {}),
    }
  })
}

/**
 * Seeds one explicit default encoding per pivot Value the chart does not yet
 * encode, in pivot order after the existing encodings. Ids are the first
 * unused `encoding_N` against the card-wide nested-id set (encodings and
 * overrides). Returns the input reference unchanged when the chart is
 * already complete; never mutates its arguments.
 */
export function reconcileValueEncodings(
  chart: ExploreChartConfig,
  pivot: ExplorePivotConfig,
): ExploreChartConfig {
  const encodedValueIds = new Set(
    chart.value_encodings.map(({ value_id }) => value_id),
  )
  const missing = pivotOutputs(pivot).filter(({ id }) => !encodedValueIds.has(id))
  if (missing.length === 0) return chart

  const takenIds = new Set([
    ...chart.value_encodings.map(({ id }) => id),
    ...chart.series_overrides.map(({ id }) => id),
  ])
  let suffix = 1
  const nextId = (): string => {
    while (takenIds.has(`encoding_${suffix}`)) suffix += 1
    const id = `encoding_${suffix}`
    takenIds.add(id)
    return id
  }

  return {
    ...cloneLiteral(chart),
    value_encodings: [
      ...chart.value_encodings.map((encoding) => cloneLiteral(encoding)),
      ...missing.map(({ id: valueId }) => defaultEncoding(nextId(), valueId)),
    ],
  }
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

  const outputs = pivotOutputs(pivot)
  const lastValueIndex = outputs.length - 1
  const valueEncodings = outputs.map(({ id: valueId }, index) => {
    const existing = existingByValueId.get(valueId)
    // Combo's starting arrangement is Excel's classic: columns with the
    // last Value as a line (a single Value stays a plain column).
    const comboLine =
      preset === "combo" && outputs.length > 1 && index === lastValueIndex
    const mark: ChartMark = comboLine ? "line" : "column"
    const stackGroup =
      mark === "column" &&
      (preset === "stacked_columns" ||
        preset === "hundred_percent_stacked_columns")
        ? "stack_1"
        : null
    return {
      ...(existing ? cloneLiteral(existing) : {}),
      ...defaultEncoding(nextId(valueId), valueId),
      mark,
      axis: "primary" as ChartAxis,
      stack_group: stackGroup,
      stack_normalize:
        stackGroup !== null && preset === "hundred_percent_stacked_columns",
      markers: mark !== "column",
    }
  })

  // The 100% preset always displays the primary axis as percentages; leaving
  // that preset restores inherit, while any other primary format survives.
  const primaryFormat =
    preset === "hundred_percent_stacked_columns"
      ? "percent"
      : chart.axes.primary.number_format === "percent"
        ? "inherit"
        : chart.axes.primary.number_format

  return {
    ...cloneLiteral(chart),
    value_encodings: valueEncodings,
    series_overrides: [],
    axes: {
      ...cloneLiteral(chart.axes),
      primary: {
        ...cloneLiteral(chart.axes.primary),
        number_format: primaryFormat,
      },
    },
  }
}

/**
 * Enables or disables the secondary axis. Disabling atomically moves every
 * secondary-assigned style to the primary axis (clearing stack membership,
 * because a group never spans axes) in the same returned object, so a
 * disabled-but-used card is unreachable. Enabling changes only the flag.
 * Returns the input reference unchanged when already in the requested state.
 */
export function setSecondaryAxisEnabled(
  chart: ExploreChartConfig,
  enabled: boolean,
): ExploreChartConfig {
  if (chart.axes.secondary.enabled === enabled) return chart
  const moveStyle = <T extends ChartValueEncoding | ChartSeriesOverride>(
    style: T,
  ): T =>
    !enabled && style.axis === "secondary"
      ? {
          ...cloneLiteral(style),
          axis: "primary",
          stack_group: null,
          stack_normalize: false,
        }
      : cloneLiteral(style)
  return {
    ...cloneLiteral(chart),
    value_encodings: chart.value_encodings.map(moveStyle),
    series_overrides: chart.series_overrides.map(moveStyle),
    axes: {
      ...cloneLiteral(chart.axes),
      secondary: { ...cloneLiteral(chart.axes.secondary), enabled },
    },
  }
}

/**
 * Pure detector over the ordered Value encodings'
 * (mark, axis, stack_group, stack_normalize) projection. Ids, colours,
 * markers, and data labels never participate; stack-group names compare only
 * as shared-vs-null, so a renamed group detects identically. Detection is
 * total: anything outside the three column layouts — lines, mixed marks,
 * areas, secondary-axis series, or an empty chart — is the general "combo"
 * category, its normal state rather than an error.
 */
export function detectChartPreset(chart: ExploreChartConfig): ChartPreset {
  const encodings = chart.value_encodings
  if (encodings.length === 0) return "combo"

  const sharedGroup = (styles: readonly ChartValueEncoding[]): boolean =>
    styles.length > 0 &&
    styles[0].stack_group !== null &&
    styles.every(({ stack_group }) => stack_group === styles[0].stack_group)

  if (
    encodings.every(
      (style) =>
        style.mark === "column" &&
        style.axis === "primary" &&
        style.stack_group === null,
    )
  ) {
    return "clustered_columns"
  }
  if (
    encodings.every(
      ({ mark, axis }) => mark === "column" && axis === "primary",
    ) &&
    sharedGroup(encodings)
  ) {
    return encodings.every(({ stack_normalize }) => stack_normalize)
      ? "hundred_percent_stacked_columns"
      : encodings.every(({ stack_normalize }) => !stack_normalize)
        ? "stacked_columns"
        : "combo"
  }
  return "combo"
}

export type ChartStackingMode = "none" | "stacked" | "normalized"

export function chartStackingMode(style: ChartStyle): ChartStackingMode {
  if (style.stack_group === null) return "none"
  return style.stack_normalize ? "normalized" : "stacked"
}

function mapChartStyles(
  chart: ExploreChartConfig,
  mapStyle: <T extends ChartValueEncoding | ChartSeriesOverride>(style: T) => T,
): ExploreChartConfig {
  return {
    ...cloneLiteral(chart),
    value_encodings: chart.value_encodings.map(mapStyle),
    series_overrides: chart.series_overrides.map(mapStyle),
  }
}

function findChartStyle(
  chart: ExploreChartConfig,
  styleId: string,
): ChartValueEncoding | ChartSeriesOverride | undefined {
  return (
    chart.value_encodings.find(({ id }) => id === styleId) ??
    chart.series_overrides.find(({ id }) => id === styleId)
  )
}

function chartStackGroups(chart: ExploreChartConfig): Map<string, ChartStyle> {
  const groups = new Map<string, ChartStyle>()
  for (const style of [...chart.value_encodings, ...chart.series_overrides]) {
    if (style.stack_group !== null && !groups.has(style.stack_group)) {
      groups.set(style.stack_group, style)
    }
  }
  return groups
}

/**
 * Applies a stacking mode with the spec's valid-by-construction transitions:
 * "none" clears only the chosen style; a mode on an ungrouped style joins the
 * chart's sole same-axis group (rewriting that group's mode) or allocates the
 * first unused stack_N; a mode change on a grouped style rewrites every
 * member of its group.
 */
export function setChartStacking(
  chart: ExploreChartConfig,
  styleId: string,
  mode: ChartStackingMode,
): ExploreChartConfig {
  const style = findChartStyle(chart, styleId)
  if (!style) return chart
  if (chartStackingMode(style) === mode) return chart

  if (mode === "none") {
    return mapChartStyles(chart, (candidate) =>
      candidate.id === styleId
        ? { ...cloneLiteral(candidate), stack_group: null, stack_normalize: false }
        : cloneLiteral(candidate),
    )
  }

  const normalize = mode === "normalized"
  if (style.stack_group !== null) {
    const group = style.stack_group
    return mapChartStyles(chart, (candidate) =>
      candidate.stack_group === group
        ? { ...cloneLiteral(candidate), stack_normalize: normalize }
        : cloneLiteral(candidate),
    )
  }

  const groups = chartStackGroups(chart)
  const sameAxisGroups = [...groups.entries()].filter(
    ([, member]) => member.axis === style.axis,
  )
  if (sameAxisGroups.length === 1) {
    const [group] = sameAxisGroups[0]
    return mapChartStyles(chart, (candidate) =>
      candidate.id === styleId
        ? {
            ...cloneLiteral(candidate),
            stack_group: group,
            stack_normalize: normalize,
          }
        : candidate.stack_group === group
          ? { ...cloneLiteral(candidate), stack_normalize: normalize }
          : cloneLiteral(candidate),
    )
  }

  let suffix = 1
  while (groups.has(`stack_${suffix}`)) suffix += 1
  const allocated = `stack_${suffix}`
  return mapChartStyles(chart, (candidate) =>
    candidate.id === styleId
      ? {
          ...cloneLiteral(candidate),
          stack_group: allocated,
          stack_normalize: normalize,
        }
      : cloneLiteral(candidate),
  )
}

/**
 * Commits an axis change; a grouped style leaves its stack group in the same
 * commit because a group never spans value axes.
 */
export function setChartStyleAxis(
  chart: ExploreChartConfig,
  styleId: string,
  axis: ChartAxis,
): ExploreChartConfig {
  const style = findChartStyle(chart, styleId)
  if (!style || style.axis === axis) return chart
  return mapChartStyles(chart, (candidate) =>
    candidate.id === styleId
      ? {
          ...cloneLiteral(candidate),
          axis,
          stack_group: null,
          stack_normalize: false,
        }
      : cloneLiteral(candidate),
  )
}

/**
 * Renames the whole group the identified style belongs to. Renaming onto an
 * existing group commits only as a compatible merge (same axis and stacking
 * mode); otherwise an error string is returned and nothing changes.
 */
export function renameChartStackGroup(
  chart: ExploreChartConfig,
  styleId: string,
  nextGroupRaw: string,
): ExploreChartConfig | string {
  const nextGroup = nextGroupRaw.trim()
  if (!nextGroup) return "Stack group name cannot be blank."
  const style = findChartStyle(chart, styleId)
  if (!style || style.stack_group === null) {
    return "Only a stacked series can rename its stack group."
  }
  const fromGroup = style.stack_group
  if (fromGroup === nextGroup) return chart
  const target = chartStackGroups(chart).get(nextGroup)
  if (
    target &&
    (target.axis !== style.axis ||
      target.stack_normalize !== style.stack_normalize)
  ) {
    return "Stack groups can merge only when their axis and stacking mode match."
  }
  return mapChartStyles(chart, (candidate) =>
    candidate.stack_group === fromGroup
      ? { ...cloneLiteral(candidate), stack_group: nextGroup }
      : cloneLiteral(candidate),
  )
}

/**
 * Decodes a canonical series key to a human label against the current pivot
 * ("column path › … · Value display name"). Malformed input throws rather
 * than degrading to the raw key, so no raw key or internal id is rendered;
 * callers pass keys that already survived parsing.
 */
export function exploreChartSeriesLabel(
  seriesKey: string,
  pivot: ExplorePivotConfig,
): string {
  const { value_id: valueId, column_path: columnPath } = parseCanonicalSeriesKey(seriesKey)
  const value = pivotOutputs(pivot).find(({ id }) => id === valueId)
  const valueName = value ? value.display_name : "a removed Value"
  const members = columnPath.map((member) => {
    if (member.kind === "null") return "(blank)"
    if (member.kind === "nan") return "(NaN)"
    return String(member.value)
  })
  return members.length === 0 ? valueName : `${members.join(" › ")} · ${valueName}`
}

export function exploreChartSeriesKey(
  valueId: string,
  columnPath:
    | { members: readonly ExplorePivotMemberKey[] }
    | readonly ExplorePivotMemberKey[],
): string {
  const members = "members" in columnPath ? columnPath.members : columnPath
  return JSON.stringify({
    version: 1,
    value_id: valueId,
    column_path: members.map(({ kind, value }) => ({ kind, value })),
  })
}
