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
