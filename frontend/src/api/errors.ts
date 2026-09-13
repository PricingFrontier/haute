import { executionErrorDetailMessage } from "../utils/executionDiagnostics"

function rawDetailRecord(error: unknown): Record<string, unknown> | null {
  if (typeof error !== "object" || error === null || !("rawDetail" in error)) return null
  const raw = (error as { rawDetail?: unknown }).rawDetail
  return typeof raw === "object" && raw !== null && !Array.isArray(raw)
    ? (raw as Record<string, unknown>)
    : null
}

/** The machine-readable `error_code` a route put in its error detail, if any. */
export function apiErrorCode(error: unknown): string | null {
  const code = rawDetailRecord(error)?.error_code
  return typeof code === "string" && code ? code : null
}

/**
 * The message to show for a failed request: the server's detail when it sent
 * one, a thrown error's own message otherwise, and `fallback` when neither is
 * meaningful (an HTTP error without a detail only knows its status).
 */
export function apiErrorMessage(error: unknown, fallback: string): string {
  const record = rawDetailRecord(error)
  const message = record?.message
  if (typeof message === "string" && message.trim()) return message
  const detail = executionErrorDetailMessage(error)
  if (detail) return detail
  // An error built without its raw body still carries the stringified detail.
  const stringDetail = typeof error === "object" && error !== null && "detail" in error
    ? (error as { detail?: unknown }).detail
    : undefined
  if (typeof stringDetail === "string" && stringDetail.trim()) return stringDetail
  const isHttpError = typeof error === "object" && error !== null && "status" in error
  if (!isHttpError && error instanceof Error && error.message) return error.message
  return fallback
}
