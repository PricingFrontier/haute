/** Return the most useful human-readable detail from a Git API failure. */
export function gitErrorMessage(error: unknown, fallback = "Git operation failed"): string {
  if (
    typeof error === "object"
    && error !== null
    && "detail" in error
    && typeof error.detail === "string"
    && error.detail.trim()
  ) {
    const detail = error.detail.trim()
    try {
      const parsed = JSON.parse(detail) as unknown
      if (typeof parsed === "string") return parsed
    } catch {
      return detail
    }
  }
  if (error instanceof Error && error.message.trim()) return error.message
  return fallback
}
