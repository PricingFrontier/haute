import "@testing-library/jest-dom/vitest"

// Canary: the test environment's web storage must be jsdom's real Storage.
// Node >= 22.4 can inject its own web-storage globals onto globalThis
// (default-on from Node 25): an inert file-less localStorage stub, and a
// real but process-global sessionStorage that leaks state across test files.
// The jsdom environment skips window keys already present there, so both
// silently shadow the real thing. `instanceof Storage` is the provenance
// check: jsdom always restores
// the `Storage` class itself, so only jsdom-created storages pass, even if a
// future Node stub grows a working `.clear`. Guarded by
// `test.execArgv: ["--no-experimental-webstorage"]` in vitest.config.ts; if
// this throws, that pin (or its successor) has stopped holding.
for (const name of ["localStorage", "sessionStorage"] as const) {
  const storage = globalThis[name]
  if (!(storage instanceof Storage) || typeof storage.clear !== "function") {
    throw new Error(
      `${name} is not jsdom's real Storage — Node's experimental web-storage ` +
        "stub is shadowing it. Check the `--no-experimental-webstorage` pin " +
        "in vitest.config.ts test.execArgv.",
    )
  }
}
localStorage.setItem("__storage_canary__", "ok")
if (localStorage.getItem("__storage_canary__") !== "ok") {
  throw new Error(
    "localStorage set/get round-trip failed — the test environment's Storage " +
      "is not functional. Check the `--no-experimental-webstorage` pin in " +
      "vitest.config.ts test.execArgv.",
  )
}
localStorage.removeItem("__storage_canary__")

Object.defineProperty(globalThis, "__APP_VERSION__", {
  configurable: true,
  value: "999.0.0-test",
})

// jsdom polyfill for React Flow's transform measurement.
// `useUpdateNodeInternals` (added in Bundle 3c for apiInput emit-frame
// re-attachment) triggers an internal re-measure that constructs
// `new window.DOMMatrixReadOnly(...)` (xyflow/system:1804); jsdom
// doesn't implement it, so without this stub the call throws an
// uncaught `TypeError: window.DOMMatrixReadOnly is not a constructor`
// during the requestAnimationFrame callback, failing the suite with no
// specific assertion failure. A no-op constructor is sufficient — the
// tests don't inspect computed transforms.
type GlobalWithMatrix = { DOMMatrixReadOnly?: unknown }
if (typeof (globalThis as GlobalWithMatrix).DOMMatrixReadOnly === "undefined") {
  function DOMMatrixReadOnlyStub(this: unknown, _init?: unknown) {
    // identity-matrix-ish; only constructed for measurement no-ops
  }
  ;(globalThis as GlobalWithMatrix).DOMMatrixReadOnly = DOMMatrixReadOnlyStub as unknown
}

const emptyDomRect = (): DOMRect => new DOMRect(0, 0, 0, 0)

const emptyDomRectList = (): DOMRectList => {
  const rects = [] as unknown as DOMRect[] & { item: (index: number) => DOMRect | null }
  rects.item = () => null
  return rects as unknown as DOMRectList
}

if (!Range.prototype.getClientRects) {
  Range.prototype.getClientRects = emptyDomRectList
}

if (!Range.prototype.getBoundingClientRect) {
  Range.prototype.getBoundingClientRect = emptyDomRect
}
