/** A new operation key, which makes a save repeated after its answer was lost write once. */
export function operationKey(): string {
  const random = typeof crypto !== "undefined" && "randomUUID" in crypto ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(36).slice(2)}`
  return `save-${random}`.slice(0, 64)
}
