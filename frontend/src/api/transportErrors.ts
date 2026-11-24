/**
 * The failures of a request that got no answer of Haute's (specs/frontend-shared, "API
 * client"): none at all, or one from something between the page and the server. The
 * transport in `api/client.ts` raises them at its fetch and its JSON reads, and
 * `apiErrorMessage` (`api/errors.ts`) words them; the browser's or the JSON parser's own
 * text stays on `cause`, for the console. They live apart from the client so the message
 * helper can recognise them without importing it.
 */

function formatTimeoutDuration(timeoutMs: number): string {
  if (timeoutMs >= 1000 && timeoutMs % 1000 === 0) {
    const seconds = timeoutMs / 1000
    return `${seconds} second${seconds === 1 ? "" : "s"}`
  }
  return `${timeoutMs} ms`
}

/** A request its own timeout guard aborted: the server did not answer in time. */
export class ApiTimeoutError extends Error {
  timeoutMs: number
  url: string

  constructor(url: string, timeoutMs: number) {
    super(`Haute's server did not answer within ${formatTimeoutDuration(timeoutMs)}.`)
    this.name = "ApiTimeoutError"
    this.timeoutMs = timeoutMs
    this.url = url
  }
}

/** A request that got no answer at all: the connection failed before any came. */
export class ServerUnreachableError extends Error {
  url: string

  constructor(url: string, cause: unknown) {
    super("Haute's server could not be reached.", { cause })
    this.name = "ServerUnreachableError"
    this.url = url
    // The browser's own words, kept for the console: the screen shows this error's.
    console.warn(`[api] ${url}: no answer`, cause)
  }
}

/**
 * An answer that is not Haute's: a page where JSON was due (a proxy's sign-in page, sent
 * with 200), or a gateway's 502, 503 or 504 without a detail of Haute's. `status` is the
 * answer's own.
 */
export class ForeignAnswerError extends Error {
  url: string
  status: number

  constructor(url: string, status: number, cause: unknown) {
    super(
      status >= 400
        ? `Something between this page and Haute's server answered in its place (HTTP ${status}).`
        : "Something between this page and Haute's server answered in its place.",
      { cause },
    )
    this.name = "ForeignAnswerError"
    this.url = url
    this.status = status
    // The proxy's page or the parser's words, kept for the console: the screen shows this error's.
    console.warn(`[api] ${url}: an answer that is not Haute's (HTTP ${status})`, cause)
  }
}

/** Whether *error* is a request that got no answer of Haute's, by any of the three ways. */
export function isNoHauteAnswer(error: unknown): error is ApiTimeoutError | ServerUnreachableError | ForeignAnswerError {
  return error instanceof ApiTimeoutError
    || error instanceof ServerUnreachableError
    || error instanceof ForeignAnswerError
}

/**
 * The parsed JSON of an answer where JSON was due. One that is not JSON did not come from
 * Haute, whose every answer is; one whose body broke off never fully came.
 */
export async function readJsonAnswer(url: string, response: Response): Promise<unknown> {
  try {
    return (await response.json()) as unknown
  } catch (error) {
    if (error instanceof SyntaxError) throw new ForeignAnswerError(url, response.status, error)
    if (typeof error === "object" && error !== null && (error as { name?: unknown }).name === "AbortError") throw error
    throw new ServerUnreachableError(url, error)
  }
}

/** The gateway statuses a proxy answers with in Haute's place. */
const GATEWAY_STATUSES: ReadonlySet<number> = new Set([502, 503, 504])

/** Whether a failed answer with *status* and *body* came from a gateway, not from Haute. */
export function isGatewayAnswer(status: number, body: unknown): boolean {
  if (!GATEWAY_STATUSES.has(status)) return false
  return typeof body !== "object" || body === null || !("detail" in body)
}
