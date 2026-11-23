/**
 * Recognises a lazily loaded chunk that failed to load, and asks why. A page names the
 * chunk files of the build it was served from; a rebuild replaces them. A file that could
 * not be fetched stays failed for the page's life, the browser keeping the failure of the
 * module and of each dependency it preloaded, so whether the file is gone or the server
 * did not answer, only a reload loads it.
 */

// The TypeError each browser raises when a dynamic import cannot be fetched.
const DYNAMIC_IMPORT_FAILURES: readonly RegExp[] = [
  /^Failed to fetch dynamically imported module: /, // Chromium
  /^error loading dynamically imported module: /, // Firefox
  /^Importing a module script failed\.$/, // Safari
]

// Errors Vite's preload helper reported through `vite:preloadError`: a chunk,
// or a stylesheet it needs, that failed to load.
const reportedPreloadFailures = new WeakSet<Error>()

/** Whether *error* is a lazily loaded chunk that failed to load, and nothing else. */
export function isChunkLoadError(error: unknown): boolean {
  if (!(error instanceof Error)) return false
  if (reportedPreloadFailures.has(error)) return true
  return (
    error instanceof TypeError
    && DYNAMIC_IMPORT_FAILURES.some((pattern) => pattern.test(error.message))
  )
}

/** The address of the chunk that failed to load, when its failure names one (Safari's does not). */
export function chunkLoadUrl(error: Error): string | null {
  const match = /(?:https?:\/\/|\/)\S+$/.exec(error.message.trim())
  return match ? match[0] : null
}

/** Why a chunk did not load: a file the server no longer has, or a server that did not answer. */
export type ChunkLoadVerdict = "gone" | "unanswered"

/**
 * Ask the server for the chunk that failed, once it has. It no longer has the file (404)
 * when it was rebuilt, since the page names the files of the build it was served from; it
 * did not answer when the file is there now or nothing answers yet. A failure that names no
 * file is taken for a rebuild once the server answers. The question is a static file's, not
 * an API call, so it is the browser's own fetch.
 */
export async function chunkLoadVerdict(error: Error): Promise<ChunkLoadVerdict> {
  const url = chunkLoadUrl(error)
  let status: number
  try {
    status = (await fetch(url ?? "/", { cache: "no-store" })).status
  } catch {
    // A request that got no answer is exactly the verdict asked for.
    return "unanswered"
  }
  return url === null || status === 404 ? "gone" : "unanswered"
}

/**
 * Records each failure Vite reports through `vite:preloadError`. The event is
 * not cancelled, so the import still rejects and the error boundary that
 * catches it is the one place that offers a reload.
 */
export function recordChunkLoadFailures(target: Window = window): void {
  target.addEventListener("vite:preloadError", (event) => {
    reportedPreloadFailures.add(event.payload)
  })
}
