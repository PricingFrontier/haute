/** Prefix matching for the column-name completion lists. */

const MAX_COMPLETIONS = 8

/**
 * Names starting with `prefix` (case-insensitive) in their given order, the
 * exact match and `exclude` left out; every name when the prefix is empty.
 */
export function completionMatches(names: readonly string[], prefix: string, exclude: readonly string[] = []): string[] {
  const lower = prefix.toLowerCase()
  const seen = new Set<string>()
  const matches: string[] = []
  for (const name of names) {
    if (seen.has(name) || exclude.includes(name) || name === prefix || !name.toLowerCase().startsWith(lower)) continue
    seen.add(name)
    matches.push(name)
    if (matches.length === MAX_COMPLETIONS) break
  }
  return matches
}
