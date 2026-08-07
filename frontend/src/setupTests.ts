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
beforeEach(async () => {
  useToastStore.setState({ toasts: [], _toastCounter: 0 })
  // Same rationale for the branch-loader single-flight: a test that mocks
  // getWorkingBranches as never-settling would otherwise starve loadBranches()
  // in every later-shuffled test. The loader's settle paths are identity-
  // guarded, which is what makes a global reset safe. The import MUST be
  // dynamic: the loader depends on api/client, which test files mock — a
  // static import here would pin a real-client loader instance into the
  // module registry before any vi.mock registers, and tests would then
  // exercise the wrong singleton (real fetches, unreset state).
  const { resetGitBranchLoaderForTests } = await import("./stores/gitBranchLoader")
  resetGitBranchLoaderForTests()
  // And for the status single-flight, which now also arms a real
  // stalled-request watchdog timer per issued request: without a global
  // reset, a never-settling mocked getWorkingBranch from one test stays
  // in-flight across the rest of its file, and in a long file run the
  // watchdog fires mid-unrelated-test and writes into the global store.
  // Reset detaches the request, so a later firing timer is identity-guarded
  // into a no-op.
  const { resetGitStatusRequestForTests } = await import("./stores/useGitStore")
  resetGitStatusRequestForTests()
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
