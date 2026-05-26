import "@testing-library/jest-dom/vitest"

// jsdom polyfill for React Flow's transform measurement.
// `useUpdateNodeInternals` (added in Bundle 3c for apiInput emit-port
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
