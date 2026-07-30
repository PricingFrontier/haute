function editableProjection(
  params: Record<string, unknown>,
  reservedKeys: readonly string[],
): Record<string, unknown> {
  const reserved = new Set(reservedKeys)
  return Object.fromEntries(
    Object.entries(params).filter(([key]) => !reserved.has(key)),
  )
}

export function formatHyperparameters(
  params: Record<string, unknown>,
  defaultParams: Record<string, unknown> = {},
  reservedKeys: readonly string[] = [],
): string {
  const projection = editableProjection(params, reservedKeys)
  const displayed = Object.keys(projection).length > 0 ? projection : defaultParams
  return JSON.stringify(displayed, null, 2)
}

export function parseHyperparameters(
  draft: string,
  reservedKeys: readonly string[],
  reservedKeysHelp: string,
): Record<string, unknown> {
  const parsed: unknown = JSON.parse(draft)
  if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error("Hyperparameters must be a JSON object")
  }

  const projection = parsed as Record<string, unknown>
  for (const key of reservedKeys) {
    if (Object.hasOwn(projection, key)) {
      const help = reservedKeysHelp ? ` ${reservedKeysHelp}` : ""
      throw new Error(`${key} is managed elsewhere.${help}`)
    }
  }
  return projection
}

function isScalarJsonValue(value: unknown): boolean {
  return (
    value === null
    || typeof value === "string"
    || typeof value === "number"
    || typeof value === "boolean"
  )
}

function formatSearchSpaceValue(value: unknown, depth: number): string {
  const indent = "  ".repeat(depth)
  const childIndent = "  ".repeat(depth + 1)

  if (Array.isArray(value)) {
    if (value.every(isScalarJsonValue)) {
      return `[${value.map((item) => JSON.stringify(item)).join(", ")}]`
    }
    return [
      "[",
      value
        .map((item) => `${childIndent}${formatSearchSpaceValue(item, depth + 1)}`)
        .join(",\n"),
      `${indent}]`,
    ].join("\n")
  }

  if (value !== null && typeof value === "object") {
    const entries = Object.entries(value as Record<string, unknown>)
    if (entries.length === 0) return "{}"
    return [
      "{",
      entries
        .map(
          ([key, item]) => (
            `${childIndent}${JSON.stringify(key)}: ${formatSearchSpaceValue(item, depth + 1)}`
          ),
        )
        .join(",\n"),
      `${indent}}`,
    ].join("\n")
  }

  const encoded = JSON.stringify(value)
  if (encoded === undefined) {
    throw new Error("Search space values must be valid JSON")
  }
  return encoded
}

export function formatTuningSearchSpace(
  searchSpace: Record<string, unknown>,
): string {
  return formatSearchSpaceValue(searchSpace, 0)
}

export function parseTuningSearchSpace(
  draft: string,
): Record<string, unknown> {
  const parsed: unknown = JSON.parse(draft)
  if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error("Search space JSON must be an object.")
  }
  return parsed as Record<string, unknown>
}

export function mergeReservedKeys(
  latestParams: Record<string, unknown>,
  projection: Record<string, unknown>,
  reservedKeys: readonly string[],
): Record<string, unknown> {
  const merged = { ...projection }
  for (const key of reservedKeys) {
    if (Object.hasOwn(latestParams, key)) merged[key] = latestParams[key]
  }
  return merged
}
