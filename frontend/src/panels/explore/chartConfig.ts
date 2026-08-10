export type ExploreChartConfig = {
  id: string
  enabled: boolean
  [key: string]: unknown
}

export type ExploreChartsParseResult =
  | { ok: true; charts: ExploreChartConfig[] }
  | { ok: false; error: string }

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

export function parseExploreCharts(config: Record<string, unknown>): ExploreChartsParseResult {
  if (!Object.prototype.hasOwnProperty.call(config, "charts")) {
    return { ok: true, charts: [] }
  }

  const rawCharts = config.charts
  if (!Array.isArray(rawCharts)) {
    return { ok: false, error: "Explore charts config must be a list." }
  }

  const charts: ExploreChartConfig[] = []
  const ids = new Set<string>()
  for (const [index, rawChart] of rawCharts.entries()) {
    const position = index + 1
    if (!isPlainObject(rawChart)) {
      return { ok: false, error: `Chart ${position} must be an object.` }
    }
    if (!Object.prototype.hasOwnProperty.call(rawChart, "id")) {
      return { ok: false, error: `Chart ${position} requires an id.` }
    }
    if (typeof rawChart.id !== "string" || rawChart.id.trim().length === 0) {
      return { ok: false, error: `Chart ${position} id must be a non-empty string.` }
    }
    if (ids.has(rawChart.id)) {
      return {
        ok: false,
        error: `Explore charts config contains duplicate chart id "${rawChart.id}".`,
      }
    }
    if (!Object.prototype.hasOwnProperty.call(rawChart, "enabled")) {
      return { ok: false, error: `Chart ${position} requires an enabled state.` }
    }
    if (typeof rawChart.enabled !== "boolean") {
      return { ok: false, error: `Chart ${position} enabled state must be a boolean.` }
    }
    for (const [key, value] of Object.entries(rawChart)) {
      if (key !== "id" && key !== "enabled" && !isSimpleLiteral(value)) {
        return {
          ok: false,
          error: `Chart ${position} field "${key}" must contain only simple literal values.`,
        }
      }
    }

    ids.add(rawChart.id)
    charts.push({ ...rawChart, id: rawChart.id, enabled: rawChart.enabled })
  }

  return { ok: true, charts }
}

export function nextExploreChartId(charts: readonly ExploreChartConfig[]): string {
  const ids = new Set(charts.map((chart) => chart.id))
  let suffix = 1
  while (ids.has(`chart_${suffix}`)) suffix += 1
  return `chart_${suffix}`
}

export function exploreChartLabel(index: number): string {
  return `Chart ${index + 1}`
}
