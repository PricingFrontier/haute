/**
 * JSON with every object's keys in sorted order, so two values that hold the same data
 * compare equal whatever order their keys were written in: the server writes a form's
 * fields in its own order, and the view builds a component in another.
 */
export function canonicalJson(value: unknown): string {
  return JSON.stringify(value ?? null, (_key, entry: unknown) =>
    entry !== null && typeof entry === "object" && !Array.isArray(entry)
      ? Object.fromEntries(Object.entries(entry).sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0)))
      : entry,
  )
}
