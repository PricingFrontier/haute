import "@testing-library/jest-dom/vitest"
import { beforeEach } from "vitest"

import useToastStore from "./stores/useToastStore"

// The toast store is a module-level singleton read by nearly every hook and
// component test. Reset it globally so a file that forgets its own copy can't
// leak toasts into whichever tests the nightly shuffle runs after it. Files
// that mock the store module are unaffected — setup-file imports resolve
// before a test file's hoisted vi.mock() factories run, so this import is
// always the real store. File-level beforeEach hooks run after this one, so
// deliberate seeding wins.
beforeEach(() => {
  useToastStore.setState({ toasts: [], _toastCounter: 0 })
})

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
