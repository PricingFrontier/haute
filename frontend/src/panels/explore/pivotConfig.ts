export type ExplorePivotConfig = {
  id: string
  [key: string]: unknown
}

export type ExplorePivotsParseResult =
  | { ok: true; pivots: ExplorePivotConfig[] }
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

export function parseExplorePivots(config: Record<string, unknown>): ExplorePivotsParseResult {
  if (!Object.prototype.hasOwnProperty.call(config, "pivots")) {
    return { ok: true, pivots: [] }
  }

  const rawPivots = config.pivots
  if (!Array.isArray(rawPivots)) {
    return { ok: false, error: "Explore pivots config must be a list." }
  }

  const pivots: ExplorePivotConfig[] = []
  const ids = new Set<string>()
  for (const [index, rawPivot] of rawPivots.entries()) {
    const position = index + 1
    if (!isPlainObject(rawPivot)) {
      return { ok: false, error: `Pivot ${position} must be an object.` }
    }
    if (!Object.prototype.hasOwnProperty.call(rawPivot, "id")) {
      return { ok: false, error: `Pivot ${position} requires an id.` }
    }
    if (typeof rawPivot.id !== "string" || rawPivot.id.trim().length === 0) {
      return { ok: false, error: `Pivot ${position} id must be a non-empty string.` }
    }
    if (ids.has(rawPivot.id)) {
      return {
        ok: false,
        error: `Explore pivots config contains duplicate pivot id "${rawPivot.id}".`,
      }
    }
    for (const [key, value] of Object.entries(rawPivot)) {
      if (key !== "id" && !isSimpleLiteral(value)) {
        return {
          ok: false,
          error: `Pivot ${position} field "${key}" must contain only simple literal values.`,
        }
      }
    }

    ids.add(rawPivot.id)
    pivots.push({ ...rawPivot, id: rawPivot.id })
  }

  return { ok: true, pivots }
}

export function nextExplorePivotId(pivots: readonly ExplorePivotConfig[]): string {
  const ids = new Set(pivots.map((pivot) => pivot.id))
  let suffix = 1
  while (ids.has(`pivot_${suffix}`)) suffix += 1
  return `pivot_${suffix}`
}

export function explorePivotLabel(index: number): string {
  return `Pivot ${index + 1}`
}
