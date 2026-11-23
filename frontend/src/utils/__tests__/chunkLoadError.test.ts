import { describe, expect, it } from "vitest"

import { chunkLoadUrl, isChunkLoadError, recordChunkLoadFailures } from "../chunkLoadError"

function vitePreloadError(payload: Error): Event {
  const event = new Event("vite:preloadError", { cancelable: true })
  return Object.assign(event, { payload })
}

describe("isChunkLoadError", () => {
  it.each([
    ["Chromium", "Failed to fetch dynamically imported module: http://127.0.0.1:8200/assets/ModellingPreview-DMczYKmb.js"],
    ["Firefox", "error loading dynamically imported module: http://127.0.0.1:8200/assets/ModellingPreview-DMczYKmb.js"],
    ["Safari", "Importing a module script failed."],
  ])("recognises %s's failed dynamic import", (_browser, message) => {
    expect(isChunkLoadError(new TypeError(message))).toBe(true)
  })

  it.each([
    ["an ordinary error", new Error("Cannot read properties of undefined (reading 'map')")],
    ["an ordinary TypeError", new TypeError("Failed to fetch")],
    ["a message that only mentions a module", new Error("Failed to fetch dynamically imported module: x.js")],
    ["a non-error value", "Failed to fetch dynamically imported module: x.js"],
    ["nothing", null],
  ])("does not treat %s as a chunk-load failure", (_case, error) => {
    expect(isChunkLoadError(error)).toBe(false)
  })

  it("recognises an error Vite reported through vite:preloadError, and leaves the event uncancelled", () => {
    recordChunkLoadFailures()
    const cssFailure = new Error("Unable to preload CSS for /assets/ModellingPreview-DMczYKmb.css")
    expect(isChunkLoadError(cssFailure)).toBe(false)

    const event = vitePreloadError(cssFailure)
    window.dispatchEvent(event)

    expect(isChunkLoadError(cssFailure)).toBe(true)
    // The import still rejects, so the error boundary that catches it says so.
    expect(event.defaultPrevented).toBe(false)
    expect(isChunkLoadError(new Error("Unable to preload CSS for /assets/other.css"))).toBe(false)
  })
})

describe("chunkLoadUrl", () => {
  it("reads the chunk's address from each browser's failure, and from Vite's", () => {
    expect(chunkLoadUrl(new TypeError("Failed to fetch dynamically imported module: http://127.0.0.1:8200/assets/A-1.js")))
      .toBe("http://127.0.0.1:8200/assets/A-1.js")
    expect(chunkLoadUrl(new TypeError("error loading dynamically imported module: http://localhost/assets/B-2.js")))
      .toBe("http://localhost/assets/B-2.js")
    expect(chunkLoadUrl(new Error("Unable to preload CSS for /assets/C-3.css"))).toBe("/assets/C-3.css")
    expect(chunkLoadUrl(new TypeError("Importing a module script failed."))).toBeNull()
  })
})
