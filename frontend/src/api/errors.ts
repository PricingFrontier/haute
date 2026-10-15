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

/** The codes a save is refused with when what it was based on changed since it was read. */
const STALE_REVISION_CODES: ReadonlySet<string> = new Set(["stale_document_revision", "stale_quote_revision"])

/**
 * Whether a save was refused because what it was based on changed since it was read: the
 * pipeline's or the form's document, or a quote. Its code is in a structured detail, or
 * leads a flat one (`stale_document_revision: ...`).
 */
export function isStaleRevision(error: unknown): boolean {
  const detail = typeof error === "object" && error !== null && "detail" in error ? (error as { detail?: unknown }).detail : undefined
  const code = apiErrorCode(error) ?? (typeof detail === "string" ? detail.split(":", 1)[0] : null)
  return code !== null && STALE_REVISION_CODES.has(code)
}

/** The HTTP status of a failed request, or null for an error that is not an HTTP response. */
export function apiErrorStatus(error: unknown): number | null {
  if (typeof error !== "object" || error === null || !("status" in error)) return null
  const status = (error as { status?: unknown }).status
  return typeof status === "number" ? status : null
}

/**
 * A 422's list of what the request's models refused, as its first error's place and
 * message: `form.pages.0.width: Input should be ...`. A check of the server's own raises
 * its sentence as a value error, which pydantic prefixes and which is left off here.
 */
function validationMessage(error: unknown): string | null {
  if (typeof error !== "object" || error === null || !("rawDetail" in error)) return null
  const raw = (error as { rawDetail?: unknown }).rawDetail
  if (!Array.isArray(raw) || raw.length === 0) return null
  const first: unknown = raw[0]
  if (typeof first !== "object" || first === null) return null
  const { loc, msg } = first as { loc?: unknown; msg?: unknown }
  if (typeof msg !== "string" || !msg.trim()) return null
  const message = msg.replace(/^Value error, /, "")
  // The request's own part is where the browser put it, not a place in what was sent.
  const place = Array.isArray(loc) ? loc.filter((part) => part !== "body").join(".") : ""
  return place ? `${place}: ${message}` : message
}

/**
 * The message to show for a failed request: the server's detail when it sent
 * one, a thrown error's own message otherwise, and `fallback` when neither is
 * meaningful (an HTTP error without a detail only knows its status). Without a
 * fallback, that status message, or a non-error value's string form, is used.
 * This is the one error-text helper; lint rejects local copies.
 */
export function apiErrorMessage(error: unknown, fallback?: string): string {
  const record = rawDetailRecord(error)
  const message = record?.message
  if (typeof message === "string" && message.trim()) return message
  const detail = executionErrorDetailMessage(error)
  if (detail) return detail
  const refused = validationMessage(error)
  if (refused) return refused
  // An error built without its raw body still carries the stringified detail.
  const stringDetail = typeof error === "object" && error !== null && "detail" in error
    ? (error as { detail?: unknown }).detail
    : undefined
  if (typeof stringDetail === "string" && stringDetail.trim()) return stringDetail
  const isHttpError = typeof error === "object" && error !== null && "status" in error
  if (error instanceof Error && error.message && (!isHttpError || fallback === undefined)) {
    return error.message
  }
  return fallback ?? String(error)
}
