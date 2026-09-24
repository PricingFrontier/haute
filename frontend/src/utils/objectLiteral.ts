/**
 * True for a plain object literal (`{}` or `Object.create(null)`), the only
 * object shape a JSON config holds. Class instances, arrays and other built-ins
 * are rejected, which the looser `isPlainObject` in `types/guards.ts` accepts.
 */
export function isObjectLiteral(value: unknown): value is Record<string, unknown> {
  if (value === null || typeof value !== "object" || Array.isArray(value)) return false
  const prototype = Object.getPrototypeOf(value)
  return prototype === Object.prototype || prototype === null
}
