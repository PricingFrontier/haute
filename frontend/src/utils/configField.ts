/**
 * Type-safe accessor for node config values.
 * Replaces the repetitive `(config.key as T) || default` pattern.
 *
 * Uses nullish coalescing (`??`) — only falls back on null/undefined.
 * For string fields where empty string should also fall back, callers
 * can chain: `configField(config, "key", "") || "actualDefault"`.
 *
 * Primitive literal types are widened (e.g. `"online"` → `string`) so
 * downstream comparisons like `mode === "ratebook"` don't trigger TS2367.
 */
type Widen<T> = T extends string ? string : T extends number ? number : T extends boolean ? boolean : T

export function configField<T>(config: Record<string, unknown>, key: string, fallback: T): Widen<T> {
  const val = config[key]
  return (val ?? fallback) as Widen<T>
}

