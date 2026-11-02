/**
 * The one canonical JSON: JSON with every object's keys in sorted order, so two values
 * that hold the same data compare equal whatever order their keys were written in (the
 * server writes a form's fields in its own order, and the view builds a component in
 * another). It keeps JSON's own value rules: a field holding `undefined` is left out, a
 * non-finite number is `null`, a value's `toJSON` is used, a top-level `undefined` is
 * `null`, and a bigint, which JSON cannot hold, throws. Config hashes and training
 * lineages built on it are kept in browser storage, so its output must not change.
 */
export function canonicalJson(value: unknown): string {
  return JSON.stringify(value ?? null, (_key, entry: unknown) =>
    entry !== null && typeof entry === "object" && !Array.isArray(entry)
      ? Object.fromEntries(Object.entries(entry).sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0)))
      : entry,
  )
}

/** The value `canonicalJson` writes, for embedding in a larger structure that is written whole. */
export function canonicalValue(value: unknown): unknown {
  return JSON.parse(canonicalJson(value))
}
