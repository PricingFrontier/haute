/**
 * Recognises a lazily loaded chunk that failed to load. A page names the chunk
 * files of the build it was served from; a rebuild replaces them, so no retry
 * in that page can load them. Only a reload fetches the new build.
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
