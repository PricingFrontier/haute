/**
 * Phase 2 Package 3D — Item #83: migrate user-facing `console.warn`/
 * `console.error` errors to user-visible toasts.
 *
 * Why this exists
 * ---------------
 * The pre-migration code swallowed several user-facing failures into
 * `console.warn(...)` only.  A user who clicks "load git history", picks a
 * frontier point, or opens a utility file and hits an API error sees NO
 * feedback in the UI — the failure is invisible unless they happen to have
 * DevTools open.  That violates the fail-loudly principle for the frontend:
 * user-facing errors must be user-visible, not just logged.
 *
 * This suite pins TWO properties:
 *
 *   1. STRUCTURAL: every surviving `console.warn` / `console.error` in
 *      production source either
 *        (a) appears in an allow-list of files where the logging path is
 *            demonstrably debug-only (e.g. the ErrorBoundary catch, which
 *            already renders a fallback UI; or best-effort cleanup like
 *            cancelling a cache fetch); OR
 *        (b) has an explicit WHY comment on the line immediately above
 *            (or trailing on the same line) documenting why a toast is
 *            *not* the right response.
 *      A bare `console.warn(...)` with no context is a regression.
 *
 *   2. BEHAVIORAL: for three representative migration sites, simulating
 *      the error condition results in an error toast landing in
 *      `useToastStore.getState().toasts`.  These act as a trip-wire if a
 *      future refactor silently deletes the toast call and goes back to
 *      console-only logging.
 *
 * When these tests fail
 * ---------------------
 * - Structural failures point you at the exact file:line.  Either add a
 *   WHY comment explaining the choice (and add the file to the allow-list
 *   if it's genuinely debug-only), or migrate the call to an addToast.
 * - Behavioral failures mean a migration was reverted: the toast is
 *   missing even though the error path fired.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { render, fireEvent, cleanup, waitFor } from "@testing-library/react"
import { readFileSync, readdirSync } from "node:fs"
import path from "node:path"
import { fileURLToPath } from "node:url"
import useToastStore from "../../stores/useToastStore"

// ═══════════════════════════════════════════════════════════════════
//  Source walker (shared by all structural tests)
// ═══════════════════════════════════════════════════════════════════

// Resolve `frontend/src/` so the walker works identically regardless
// of where vitest is invoked from.  fileURLToPath + import.meta.url is
// the ESM-safe alternative to __dirname (which TS doesn't expose for
// ESM-configured projects like this one).
const HERE = path.dirname(fileURLToPath(import.meta.url))
const SRC_ROOT = path.resolve(HERE, "../..")

/**
 * Files where any surviving `console.warn`/`console.error` call is KNOWN
 * to be a debug-only path whose UX is already handled elsewhere (inline
 * error banner, status badge, fallback UI, best-effort cleanup, etc.).
 *
 * Adding a file here is an explicit engineering decision.  Each entry
 * should also carry a WHY comment beside it in the source, so a reader
 * can tell at a glance why the file isn't routed through addToast.
 *
 * Paths are relative to `frontend/src/` and use POSIX separators — the
 * walker normalises before matching.
 */
const DEBUG_ONLY_ALLOWLIST: readonly string[] = [
  // Fallback UI is already rendered when this fires; console is for devs
  // diagnosing the crash in their own DevTools.
  "components/ErrorBoundary.tsx",
  // Cache status / progress polling / cancellation are best-effort
  // background operations — "not yet cached" is the common case, not an
  // error the user needs to be notified about.
  "components/CacheFetchButton.tsx",
  // MLflow check is an optional capability probe on app startup; the
  // failure is already surfaced via the `mlflow.status === "error"` badge.
  "stores/useSettingsStore.ts",
  // GPU toggle fallback: invalid draft JSON recovers automatically from
  // the last-known-good params; the user experience is unchanged.
  "panels/modelling/FeatureAndAlgorithmConfig.tsx",
  // Job polling retry path: a retry-warning toast is already emitted
  // after N consecutive failures; per-retry console is debug-only.
  "hooks/useJobPolling.ts",
  // Optimiser artifact path has an inline error banner (loadError) that
  // is already surfaced via the editor's dedicated red box.
  "panels/editors/OptimiserApplyEditor.tsx",
]

type FoundCall = {
  /** POSIX-style path relative to frontend/src/ */
  file: string
  /** 1-based line number of the `console.warn(` / `console.error(` token. */
  line: number
  /** Full source line, trimmed. */
  text: string
  /** The source line immediately above (trimmed). */
  lineAbove: string
}

/**
 * Enumerate every `console.warn(...)` / `console.error(...)` call in a
 * subtree of `frontend/src/`, skipping anything under `__tests__/`.
 *
 * Uses a hand-written recursive walk (not globbing) so the test is
 * deterministic across OSes — the order is stable and test failures
 * always point to a consistent file list.
 */
function findConsoleWarnSites(): FoundCall[] {
  const results: FoundCall[] = []
  const visit = (dir: string) => {
    const entries = readdirSync(dir, { withFileTypes: true })
    for (const ent of entries) {
      const abs = path.join(dir, ent.name)
      if (ent.isDirectory()) {
        // Skip test dirs — we only care about production source.
        if (ent.name === "__tests__") continue
        visit(abs)
        continue
      }
      if (!ent.isFile()) continue
      if (!/\.(ts|tsx)$/.test(ent.name)) continue
      // Ignore .d.ts declaration files and the vitest setup file.
      if (ent.name.endsWith(".d.ts")) continue
      if (ent.name === "setupTests.ts") continue

      const content = readFileSync(abs, "utf8")
      const lines = content.split("\n")
      for (let i = 0; i < lines.length; i++) {
        const line = lines[i]
        if (/console\.(warn|error)\s*\(/.test(line)) {
          const rel = path
            .relative(SRC_ROOT, abs)
            .split(path.sep)
            .join("/")
          results.push({
            file: rel,
            line: i + 1,
            text: line.trim(),
            lineAbove: (lines[i - 1] ?? "").trim(),
          })
        }
      }
    }
  }
  visit(SRC_ROOT)
  return results
}

/** Line-above (or same-line trailing) comment counts as a WHY marker. */
function hasWhyComment(call: FoundCall): boolean {
  // Trailing same-line comment: `console.warn(...) // why ...`
  const afterCall = call.text.replace(/^.*console\.(warn|error)\s*\(/, "")
  if (/\/\/\s*\S/.test(afterCall)) return true
  // Comment on the line immediately above.
  if (/^\s*\/\//.test(call.lineAbove)) return true
  if (/^\s*\/\*/.test(call.lineAbove)) return true
  // Block-comment closer on the line above also counts (e.g. /* ... */)
  if (/\*\/\s*$/.test(call.lineAbove)) return true
  return false
}

// ═══════════════════════════════════════════════════════════════════
//  Structural tests
// ═══════════════════════════════════════════════════════════════════

describe("Phase 2 Package 3D — console.* usage is disciplined (#83)", () => {
  it("enumerates at least one console.warn/error site (smoke)", () => {
    // Sanity: if the walker finds zero sites the rest of the suite is
    // silently passing.  The current codebase has several legitimate
    // debug-only calls (see DEBUG_ONLY_ALLOWLIST), so this must never
    // hit zero without explicit removal of the allow-list entries too.
    const sites = findConsoleWarnSites()
    expect(sites.length).toBeGreaterThan(0)
  })

  it("every surviving console.warn/error has a WHY comment or is in the debug-only allow-list", () => {
    // If this fails, the offending file:line is printed in the diff.
    // Pre-migration this test would fail on every user-facing site
    // (GitPanel loadHistory, OptimiserPreview frontier select, etc.).
    // Post-migration those sites are either deleted (→ toast-only) or
    // carry an inline WHY comment describing why a toast is the wrong
    // response.
    const sites = findConsoleWarnSites()
    const offenders = sites.filter((s) => {
      if (DEBUG_ONLY_ALLOWLIST.includes(s.file)) return false
      return !hasWhyComment(s)
    })
    if (offenders.length > 0) {
      const summary = offenders
        .map((o) => `  ${o.file}:${o.line}  ${o.text}`)
        .join("\n")
      throw new Error(
        `Found ${offenders.length} console.warn/error call(s) with no WHY comment and not in the debug-only allow-list:\n${summary}\n\n` +
          `Either (a) migrate the call to useToastStore.addToast("error", ...) so the user sees the failure, ` +
          `or (b) add a single-line // comment on the line above documenting why console-only is the right choice, ` +
          `or (c) add the file to DEBUG_ONLY_ALLOWLIST in this test if the whole file is debug-only.`,
      )
    }
    expect(offenders).toEqual([])
  })

  it("does NOT log console.warn on paths that ALSO call addToast (redundant console)", () => {
    // Rationale: writing `console.warn(...); addToast(...)` in the same
    // branch doubles the notification channel — the toast is already
    // the user-visible signal, and the console adds noise without
    // benefit.  Post-migration these redundant consoles should be
    // removed so each error path has exactly one surfacing mechanism.
    //
    // We spot the pattern by finding any source line that contains
    // BOTH `console.warn(` and `addToast(` (common in one-liner
    // `.catch((e) => { console.warn(...); addToast(...) })` blocks).
    const sites = findConsoleWarnSites()
    const doubled = sites.filter((s) => /addToast\s*\(/.test(s.text))
    if (doubled.length > 0) {
      const summary = doubled
        .map((o) => `  ${o.file}:${o.line}  ${o.text}`)
        .join("\n")
      throw new Error(
        `Found ${doubled.length} line(s) where console.warn and addToast fire on the same statement — the console is redundant and should be removed:\n${summary}`,
      )
    }
    expect(doubled).toEqual([])
  })

  it("known migration targets route user-facing errors through addToast(...)", () => {
    // Belt-and-braces: for each file that was specifically called out
    // in the package plan, assert the raw source still contains at
    // least one `addToast(` reference.  This prevents a "big bang"
    // regression where someone rips out the toast call entirely and
    // goes back to console-only (which the WHY-comment check above
    // would also notice, but this is a faster localised signal).
    //
    // The anchor is chosen to be stable: it references the toast store
    // API surface, not the wording of any particular message, so future
    // copy edits don't flap the test.
    const mustToast: ReadonlyArray<{ file: string; anchor: string }> = [
      { file: "panels/GitPanel.tsx", anchor: "addToast" },
      { file: "panels/UtilityPanel.tsx", anchor: "addToast" },
    ]
    for (const { file, anchor } of mustToast) {
      const abs = path.join(SRC_ROOT, file)
      const src = readFileSync(abs, "utf8")
      expect(
        src.includes(anchor),
        `${file} should route user-facing errors through ${anchor}(...) — the reference is missing entirely.`,
      ).toBe(true)
    }
  })
})

// ═══════════════════════════════════════════════════════════════════
//  Behavioral tests — shared mocks + helpers
// ═══════════════════════════════════════════════════════════════════

// Mocks are declared at the top level so Vitest's hoisting applies;
// that means the factories run before any `import` inside the file's
// modules resolve.  Each factory uses a `vi.hoisted(() => ...)` block to
// share mock instances with the test bodies below.

const H = vi.hoisted(() => ({
  // OptimiserPreview ----------------------------------------------
  selectFrontierPointAPI: vi.fn(),
  saveOptimiser: vi.fn(),
  logOptimiserToMlflow: vi.fn(),
  storeSelectPoint: vi.fn(),
  storeUpdateAfterSelect: vi.fn(),
  // GitPanel ------------------------------------------------------
  getGitStatus: vi.fn(),
  gitArchiveBranch: vi.fn(),
  gitDeleteBranch: vi.fn(),
  // GitPanel (v1: history + branch manager) -----------------------
  getMilestones: vi.fn(),
  getMilestoneSaves: vi.fn(),
  getPendingSaves: vi.fn(),
  getWorkingBranch: vi.fn(),
  getWorkingBranches: vi.fn(),
  setWorkingBranch: vi.fn(),
  createWorkingBranch: vi.fn(),
  restoreBranch: vi.fn(),
  getGitPrefs: vi.fn(() => Promise.resolve({ skip_switch_confirm: false })),
  setGitPrefs: vi.fn(),
  // UtilityPanel --------------------------------------------------
  listUtilityFiles: vi.fn(),
  readUtilityFile: vi.fn(),
  createUtilityFile: vi.fn(),
  updateUtilityFile: vi.fn(),
  deleteUtilityFile: vi.fn(),
}))

// A single `api/client` mock covering every function any of the three
// panels imports.  Each per-panel describe() sets up only the mocks it
// actually needs — the others stay as default vi.fn() no-ops.
vi.mock("../../api/client", () => {
  class ApiError extends Error {
    status: number
    detail?: string
    constructor(message: string, status: number, detail?: string) {
      super(message)
      this.name = "ApiError"
      this.status = status
      this.detail = detail
    }
  }
  return {
    ApiError,
    // OptimiserPreview
    selectFrontierPoint: (...a: unknown[]) => H.selectFrontierPointAPI(...a),
    saveOptimiser: (...a: unknown[]) => H.saveOptimiser(...a),
    logOptimiserToMlflow: (...a: unknown[]) => H.logOptimiserToMlflow(...a),
    // GitPanel
    getGitStatus: (...a: unknown[]) => H.getGitStatus(...a),
    gitArchiveBranch: (...a: unknown[]) => H.gitArchiveBranch(...a),
    gitDeleteBranch: (...a: unknown[]) => H.gitDeleteBranch(...a),
    getMilestones: (...a: unknown[]) => H.getMilestones(...a),
    getMilestoneSaves: (...a: unknown[]) => H.getMilestoneSaves(...a),
    getPendingSaves: (...a: unknown[]) => H.getPendingSaves(...a),
    getWorkingBranch: (...a: unknown[]) => H.getWorkingBranch(...a),
    getWorkingBranches: (...a: unknown[]) => H.getWorkingBranches(...a),
    setWorkingBranch: (...a: unknown[]) => H.setWorkingBranch(...a),
    createWorkingBranch: (...a: unknown[]) => H.createWorkingBranch(...a),
    restoreBranch: (...a: unknown[]) => H.restoreBranch(...a),
    getGitPrefs: () => H.getGitPrefs(),
    setGitPrefs: (...a: unknown[]) => H.setGitPrefs(...a),
    // UtilityPanel
    listUtilityFiles: (...a: unknown[]) => H.listUtilityFiles(...a),
    readUtilityFile: (...a: unknown[]) => H.readUtilityFile(...a),
    createUtilityFile: (...a: unknown[]) => H.createUtilityFile(...a),
    updateUtilityFile: (...a: unknown[]) => H.updateUtilityFile(...a),
    deleteUtilityFile: (...a: unknown[]) => H.deleteUtilityFile(...a),
  }
})

// OptimiserPreview-only support mocks
vi.mock("../../hooks/useDragResize", () => ({
  useDragResize: () => ({
    height: 320,
    containerRef: { current: null },
    onDragStart: vi.fn(),
  }),
}))
vi.mock("../../stores/useNodeResultsStore", () => ({
  default: (selector: (s: Record<string, unknown>) => unknown) =>
    selector({
      getOptimiserPreview: () => null,
      selectFrontierPoint: H.storeSelectPoint,
      updateFrontierAfterSelect: H.storeUpdateAfterSelect,
    }),
}))
vi.mock("../../stores/useSettingsStore", () => ({
  default: (selector: (s: Record<string, unknown>) => unknown) =>
    selector({ mlflow: { status: "connected", backend: "local", host: "" } }),
}))

// UtilityPanel-only support mocks
vi.mock("../editors/CodeEditor", () => ({
  CodeEditor: ({
    defaultValue,
    onChange,
  }: {
    defaultValue?: string
    onChange?: (v: string) => void
  }) => (
    <textarea
      data-testid="code-editor"
      defaultValue={defaultValue}
      onChange={(e) => onChange?.(e.target.value)}
    />
  ),
}))

// Reset helper used by every behavioral test.
function resetToasts() {
  useToastStore.setState({ toasts: [], _toastCounter: 0 })
}

// ═══════════════════════════════════════════════════════════════════
//  Behavioral tests
// ═══════════════════════════════════════════════════════════════════
//
//  These exercise three representative migration sites end-to-end at
//  the component level: trigger the error condition, then assert
//  `useToastStore.getState().toasts` contains an error-typed entry
//  whose text references the action the user just attempted.
//
//  We use the real useToastStore (not a mock) so the dedup / lifecycle
//  rules from Phase 1H also apply — a toast that's silently deduped
//  would fail this test, which is the correct behaviour: if two
//  consecutive clicks "should" both warn the user and one is
//  suppressed, that's a user-visible regression.

// ── Site 1: OptimiserPreview — frontier point API failure ─────────

import OptimiserPreview, { type OptimiserPreviewData } from "../OptimiserPreview"

describe("OptimiserPreview frontier-point switching stays local", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    resetToasts()
  })
  afterEach(cleanup)

  function makeData(): OptimiserPreviewData {
    return {
      result: {
        total_objective: 1234567,
        baseline_objective: 1200000,
        constraints: { loss_ratio: 0.65 },
        baseline_constraints: { loss_ratio: 0.60 },
        lambdas: { loss_ratio: 0.005 },
        converged: true,
        iterations: 15,
        n_quotes: 50000,
        history: null,
      },
      jobId: "job_123",
      constraints: { loss_ratio: { max: 1.05 } },
      nodeLabel: "My Optimiser",
      frontier: {
        points: Array.from({ length: 5 }, (_, i) => ({
          total_objective: 1200000 + i * 10000,
          total_loss_ratio: 0.55 + i * 0.02,
          lambda_loss_ratio: 0.001 + i * 0.001,
        })),
        n_points: 5,
        points_returned: 5,
        constraint_names: ["loss_ratio"],
        points_limit: 2000,
        points_truncated: false,
      },
      selectedPointIndex: null,
    }
  }

  it("clicking a frontier scatter point does not call the select API or raise a network toast", () => {
    // Frontier point switching is now local; a rejected select API mock
    // should be irrelevant because the component must not call it.
    H.selectFrontierPointAPI.mockRejectedValueOnce(new Error("network error"))

    render(<OptimiserPreview data={makeData()} nodeId="opt_1" allNodes={[]} edges={[]} />)

    const circles = document.querySelectorAll("circle[style*='cursor: pointer']")
    expect(circles.length).toBe(5)
    fireEvent.click(circles[2])

    expect(H.storeSelectPoint).toHaveBeenCalledWith("opt_1", 2)
    expect(H.selectFrontierPointAPI).not.toHaveBeenCalled()
    expect(useToastStore.getState().toasts.filter((t) => t.type === "error")).toHaveLength(0)
  })
})

// ── Site 2: GitPanel — history load failure ───────────────────────

import GitPanel from "../GitPanel"

describe("#83 behavioral: GitPanel history load failure surfaces a toast", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    resetToasts()
    // The v1 GitPanel loads its history (+ branch manager + status) on mount.
    H.getWorkingBranch.mockResolvedValue({
      working_branch: "demo",
      current_branch: "demo",
      state: "ready",
      eligible_branches: [],
      identity_set: true,
      user_name: "x",
      user_email: "x@y",
      last_save_sha: null,
      errors: [],
    })
    H.getWorkingBranches.mockResolvedValue({ current: "demo", branches: [] })
    H.getPendingSaves.mockResolvedValue({ saves: [] })
  })
  afterEach(cleanup)

  it("a rejecting history API raises an ERROR toast on mount", async () => {
    // Failure mode this catches: the pre-migration code swallowed the error and
    // showed an empty history list with no explanation. The v1 panel auto-loads
    // history on mount and routes a failure through addToast("error", …).
    H.getMilestones.mockRejectedValue(new Error("SSO expired"))

    render(<GitPanel onClose={vi.fn()} />)

    await waitFor(() => {
      const toasts = useToastStore.getState().toasts
      const errorToast = toasts.find((t) => t.type === "error")
      expect(
        errorToast,
        `Expected an ERROR toast after getMilestones rejected. toasts=${JSON.stringify(toasts)}`,
      ).toBeDefined()
      expect(errorToast!.text.toLowerCase()).toMatch(/git|history|version/)
    })
  })
})

// ── Site 3: UtilityPanel — file load failure ──────────────────────

import UtilityPanel from "../UtilityPanel"

describe("#83 behavioral: UtilityPanel file load failure surfaces a toast", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    resetToasts()
  })
  afterEach(cleanup)

  it("auto-loading a file with a rejecting readUtilityFile raises an ERROR toast", async () => {
    // Failure mode this catches: the pre-migration code wrote
    //   catch (err) { console.warn("Failed to load utility file", ...); setErrorMsg(...) }
    // The inline errorMsg only shows when this panel is open; if the
    // user navigates away mid-fetch the failure is invisible.
    // Post-migration the catch also routes through addToast.
    H.listUtilityFiles.mockResolvedValue({
      files: [{ name: "helpers.py", module: "helpers" }],
    })
    H.readUtilityFile.mockRejectedValueOnce(new Error("disk read failure"))

    render(<UtilityPanel onClose={vi.fn()} onImportAdded={vi.fn()} />)

    await waitFor(() => {
      const toasts = useToastStore.getState().toasts
      const errorToast = toasts.find((t) => t.type === "error")
      expect(
        errorToast,
        `Expected an ERROR toast after readUtilityFile rejected. toasts=${JSON.stringify(toasts)}`,
      ).toBeDefined()
      expect(errorToast!.text.toLowerCase()).toMatch(/utility|file|load|helpers/)
    })
  })
})
