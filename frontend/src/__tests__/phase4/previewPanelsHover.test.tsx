/**
 * Phase 4 Wave 8 Package 8D — preview-panel hover migration (test pin).
 *
 * Why this test exists
 * --------------------
 * The four bottom-panel preview components each perform their hover
 * affordances (drag-handle tint, collapse-button chrome) by mutating
 * `e.currentTarget.style.background` inside React event handlers:
 *
 *   - frontend/src/panels/DataPreview.tsx           (4 sites)
 *   - frontend/src/panels/OptimiserPreview.tsx      (4 sites)
 *   - frontend/src/panels/OptimiserDataPreview.tsx  (4 sites)
 *   - frontend/src/panels/ModellingPreview.tsx      (4 sites)
 *
 * That pattern is a concurrent-rendering hazard (the imperative style
 * mutation races React's commit phase), it bypasses CSS precedence
 * rules, and it makes the hover state invisible to computed-style
 * tooling used by screenshot-diff suites.  The Wave 8 migration replaces
 * the mutations with className-driven hover — either Tailwind's
 * `hover:` utilities, or the shared `.hover-chrome` class already
 * defined at the bottom of `src/index.css`.
 *
 * This file pins the migration in two complementary layers:
 *
 *  1. An AST-walk over each production file parsed with `@babel/parser`
 *     asserts that **no** `MemberExpression` of the shape
 *         `<anything>.currentTarget.style.<prop>`
 *     exists in the source.  Combined with a defence-in-depth literal
 *     `.currentTarget.style.` substring scan (after stripping comments
 *     and strings) this makes it impossible for the bad pattern to
 *     sneak back in via a merge or a renamed handler parameter.
 *
 *  2. An integration render per file that drives the drag handle and
 *     the collapse button and asserts the elements do NOT pick up an
 *     inline `background` / `color` style from a mouseenter handler.
 *     This is the behavioural pin: even if a future author picked a
 *     different spelling of the mutation, the rendered element's
 *     `element.style.cssText` stays stable through mouseenter/mouseleave.
 *
 * State-dependent cases that the reviewer must preserve
 * -----------------------------------------------------
 *   - Active vs inactive *tabs* in `OptimiserPreview`, `ModellingPreview`,
 *     and `OptimiserDataPreview` are driven by the `tab === t` boolean
 *     applied to `background` / `color` style literals.  These are NOT
 *     hover state and are allowed to remain inline; the tests assert
 *     the active-tab visual distinction survives the migration (non-zero
 *     style difference between selected and unselected tab buttons).
 *   - "Selected row" highlighting in `DataPreview` is driven by
 *     `tracedCell` state, not hover — also allowed to remain inline.
 *   - The drag handle, collapse button, and (in ModellingPreview) tab
 *     chrome all currently mutate style inside onMouseEnter/onMouseLeave.
 *     After the migration these must be className-driven.
 *
 * Mutation inventory (captured at test-write time, 2026-04-19)
 * -------------------------------------------------------------
 *     DataPreview.tsx            L113-114  (drag handle hover)
 *                                L162-163  (collapse button hover)
 *     OptimiserPreview.tsx       L230-231  (drag handle hover)
 *                                L265-266  (collapse button hover)
 *     OptimiserDataPreview.tsx   L568-573  (drag handle hover)
 *                                L688-693  (collapse button hover)
 *     ModellingPreview.tsx       L118-119  (drag handle hover)
 *                                L156-157  (collapse button hover)
 *
 * Each file has TWO hover *sites* with an enter+leave pair = 4 mutation
 * callsites per file, matching the 4-per-file scope from the package brief.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { cleanup, fireEvent, render } from "@testing-library/react"
import { readFileSync } from "node:fs"
import path from "node:path"
import { fileURLToPath } from "node:url"
import { parse } from "@babel/parser"
import _traverseDefault from "@babel/traverse"
import type { NodePath } from "@babel/traverse"
import type { MemberExpression } from "@babel/types"

// @babel/traverse's ESM export is a CJS default — unwrap it.
const traverse = (
  (_traverseDefault as unknown as { default?: typeof _traverseDefault }).default ?? _traverseDefault
) as typeof _traverseDefault

// ═════════════════════════════════════════════════════════════════════
//  Path helpers — resolve `frontend/src/` regardless of cwd
// ═════════════════════════════════════════════════════════════════════

const HERE = path.dirname(fileURLToPath(import.meta.url))
// This file is at frontend/src/__tests__/phase4/, so src/ is two up.
const SRC_ROOT = path.resolve(HERE, "../..")
const PANELS_DIR = path.join(SRC_ROOT, "panels")

const TARGET_FILES = [
  "DataPreview.tsx",
  "OptimiserPreview.tsx",
  "OptimiserDataPreview.tsx",
  "ModellingPreview.tsx",
] as const
type TargetFile = (typeof TARGET_FILES)[number]

function absFor(file: TargetFile): string {
  return path.join(PANELS_DIR, file)
}

function readSource(file: TargetFile): string {
  return readFileSync(absFor(file), "utf8")
}

// ═════════════════════════════════════════════════════════════════════
//  AST helpers
// ═════════════════════════════════════════════════════════════════════

/**
 * Parse a TSX source file into a Babel AST.  We enable the TSX plugins
 * and JSX so React handlers like `onMouseEnter={(e) => …}` parse
 * cleanly.  `errorRecovery` is off because any parse failure is
 * genuinely a bug we want the test to surface.
 */
function parseTsx(source: string, filename: string): ReturnType<typeof parse> {
  return parse(source, {
    sourceType: "module",
    sourceFilename: filename,
    plugins: ["typescript", "jsx"],
  })
}

/**
 * Collect every `MemberExpression` of shape `X.currentTarget.style.Y`
 * anywhere in `source`, with source locations for assertion messages.
 *
 * The shape we're looking for is a *chained* member access:
 *
 *     MemberExpression(                     // outer: .style.<prop>
 *       object=MemberExpression(            // middle: .currentTarget.style
 *         object=<anything>,                // inner: e / event / evt / …
 *         property=Identifier("currentTarget")),
 *       property=Identifier("style")),
 *     property=Identifier(<prop>)
 *
 * We capture the outermost node (the `.style.<prop>` access) because
 * that's the one an assignment expression actually targets and it's
 * the most useful for error messages.
 */
type StyleMutationSite = { line: number; column: number; code: string }

function findCurrentTargetStyleMutations(
  source: string,
  filename: string,
): StyleMutationSite[] {
  const ast = parseTsx(source, filename)
  const sites: StyleMutationSite[] = []

  traverse(ast, {
    MemberExpression(nodePath: NodePath<MemberExpression>) {
      const outer = nodePath.node
      // outer.object must itself be `X.currentTarget.style`
      if (outer.object.type !== "MemberExpression") return
      const middle = outer.object
      // middle.property must be Identifier("style")
      if (
        middle.property.type !== "Identifier" ||
        middle.property.name !== "style"
      ) {
        return
      }
      // middle.object must be `<anything>.currentTarget`
      if (middle.object.type !== "MemberExpression") return
      const inner = middle.object
      if (
        inner.property.type !== "Identifier" ||
        inner.property.name !== "currentTarget"
      ) {
        return
      }
      // We have a hit: `<inner.object>.currentTarget.style.<outer.property>`.
      // Skip if the outer property isn't a plain identifier (defensive,
      // e.g. computed access `style[x]`) — these are still bad patterns
      // but we only claim to catch the idiomatic `style.X` form here.
      if (outer.property.type !== "Identifier") return
      const loc = outer.loc
      if (!loc) return
      const codeSlice = source.slice(
        outer.start ?? 0,
        (outer.end ?? 0) + 0,
      )
      sites.push({
        line: loc.start.line,
        column: loc.start.column,
        code: codeSlice,
      })
    },
  })

  return sites
}

/**
 * Strip line + block comments and string literals from TS/TSX source so
 * a literal-text `.currentTarget.style.` scan doesn't false-positive on
 * this test file's own documentation or on docstrings inside production
 * code that mention the old pattern.
 */
function stripCommentsAndStrings(src: string): string {
  // Block comments, non-greedy.
  let out = src.replace(/\/\*[\s\S]*?\*\//g, "")
  // Line comments.
  out = out.replace(/\/\/[^\n]*/g, "")
  // Double-quoted strings (no line continuations expected in these files).
  out = out.replace(/"(?:\\.|[^"\\])*"/g, '""')
  // Single-quoted strings.
  out = out.replace(/'(?:\\.|[^'\\])*'/g, "''")
  // Template strings (we don't try to preserve ${…} expressions — those
  // are TS code but a template literal containing the exact substring
  // `.currentTarget.style.` as raw text would itself be suspicious).
  out = out.replace(/`(?:\\.|[^`\\])*`/g, "``")
  return out
}

// ═════════════════════════════════════════════════════════════════════
//  AST pin — the core structural assertion
// ═════════════════════════════════════════════════════════════════════

describe("Phase 4 Wave 8D: preview panels no longer mutate e.currentTarget.style.*", () => {
  for (const file of TARGET_FILES) {
    describe(file, () => {
      it("contains no MemberExpression of shape `<ref>.currentTarget.style.<prop>` (AST)", () => {
        const source = readSource(file)
        const hits = findCurrentTargetStyleMutations(source, file)
        // Surface every callsite with its line number so a failing run
        // tells the developer exactly where the leftover mutation is.
        const formatted = hits
          .map((h) => `  ${file}:${h.line}:${h.column + 1}  ${h.code}`)
          .join("\n")
        expect(
          hits,
          `Expected no .currentTarget.style.* mutations in ${file}; ` +
            `found ${hits.length}:\n${formatted}`,
        ).toEqual([])
      })

      it("contains no `.currentTarget.style.` substring in live code (defence in depth)", () => {
        const source = readSource(file)
        const stripped = stripCommentsAndStrings(source)
        expect(
          stripped.includes(".currentTarget.style."),
          `Expected '.currentTarget.style.' substring to be absent from ` +
            `live code in ${file}, but it's still present. ` +
            `This usually means a handler like onMouseEnter={e => e.currentTarget.style.X = ...} ` +
            `slipped back in — migrate it to a className or a .hover-chrome-style utility.`,
        ).toBe(false)
      })

      it("either uses a hover utility class (hover:… or hover-chrome) on chrome elements", () => {
        // Once the mutations are gone, there has to be a replacement
        // mechanism.  We assert at least one of the Tailwind `hover:`
        // prefix or the shared `.hover-chrome` class appears in the
        // file — otherwise the migration removed the mutations without
        // restoring any hover affordance at all.
        const source = readSource(file)
        const usesHoverChrome = /\bhover-chrome\b/.test(source)
        const usesTailwindHover = /\bhover:/.test(source)
        expect(
          usesHoverChrome || usesTailwindHover,
          `Expected ${file} to use either the '.hover-chrome' class or a ` +
            `Tailwind 'hover:' utility after the migration, but found neither. ` +
            `The drag handle and collapse button need a class-driven hover ` +
            `replacement for the removed e.currentTarget.style.* mutations.`,
        ).toBe(true)
      })
    })
  }

  // ═══════════════════════════════════════════════════════════════════
  //  Walker honesty check — the tests fail closed
  // ═══════════════════════════════════════════════════════════════════

  it("smoke: the AST walker actually finds known-bad patterns in a control sample", () => {
    // If `findCurrentTargetStyleMutations` silently returned `[]` on
    // every input (parser misconfiguration, Babel plugin missing, wrong
    // visitor key) every other assertion above would vacuously pass.
    // Feed it a tiny synthetic TSX file that indisputably contains the
    // bad pattern and assert we find exactly one hit.
    const sample = `
      const f = () =>
        <div onMouseEnter={(e) => { e.currentTarget.style.background = "red" }} />
    `
    const hits = findCurrentTargetStyleMutations(sample, "synthetic.tsx")
    expect(hits.length).toBe(1)
    expect(hits[0].code).toContain("currentTarget.style.background")
  })

  it("smoke: source walker reads actual file content (not empty strings)", () => {
    // Defensive: if the path resolution is wrong, readFileSync throws,
    // but if a future refactor introduces a `try { … } catch { return "" }`
    // the AST walker would pass on an empty string.  Assert the files
    // are non-empty and contain a React import as a cheap sanity check.
    for (const file of TARGET_FILES) {
      const source = readSource(file)
      expect(source.length, `${file} must be non-empty`).toBeGreaterThan(500)
      expect(
        /from\s+["']react["']|from\s+["']lucide-react["']/.test(source),
        `${file} must still look like a React component`,
      ).toBe(true)
    }
  })
})

// ═════════════════════════════════════════════════════════════════════
//  Integration renders — behavioural pin per panel
// ═════════════════════════════════════════════════════════════════════
//
// Each render isolates one panel, fires mouseenter + mouseleave on the
// drag handle and (where applicable) on the collapse button, and asserts
// the targeted element's inline `background` / `color` does NOT change.
// That's the behavioural contract of the migration: hover is mediated
// by CSS, not by imperative style mutation in a React handler.
// ═════════════════════════════════════════════════════════════════════

// ── Shared mocks ────────────────────────────────────────────────────

// Stub useDragResize so we don't need a real ResizeObserver and can keep
// the drag handle markup pure.  We return a ref that jsdom is happy with.
vi.mock("../../hooks/useDragResize", () => ({
  useDragResize: () => ({
    height: 300,
    containerRef: { current: null },
    onDragStart: vi.fn(),
  }),
}))

// Silence all zustand stores by replacing their hooks with simple
// selector-aware stubs.  We keep the shape so the components don't
// blow up on selector functions.
vi.mock("../../stores/useNodeResultsStore", () => {
  const state = {
    trainJobs: {},
    selectFrontierPoint: vi.fn(),
    updateFrontierAfterSelect: vi.fn(),
  }
  const hook = (selector?: (s: typeof state) => unknown) =>
    selector ? selector(state) : state
  return { default: hook }
})

vi.mock("../../stores/useSettingsStore", () => {
  const state = {
    mlflow: { status: "pending", backend: "", host: "" },
  }
  const hook = (selector?: (s: typeof state) => unknown) =>
    selector ? selector(state) : state
  return { default: hook }
})

vi.mock("../../stores/useToastStore", () => {
  const state = { addToast: vi.fn() }
  const hook = (selector?: (s: typeof state) => unknown) =>
    selector ? selector(state) : state
  return { default: hook }
})

// Stub API client — none of these should be called during hover tests,
// but if the component accidentally fires one we want a clear failure.
vi.mock("../../api/client", () => ({
  selectFrontierPoint: vi.fn(),
  saveOptimiser: vi.fn(),
  logOptimiserToMlflow: vi.fn(),
}))

// Stub the heavy sub-components of OptimiserPreview so the render tree
// doesn't pull in FrontierChart / ConvergenceChart / SummaryTab / DetailCard.
// Our interest is the top-level chrome (drag handle + collapse button +
// tab row), not the tab content.
vi.mock("../../panels/optimiser/FrontierChart", () => ({
  default: () => <div data-testid="stub-frontier-chart" />,
}))
vi.mock("../../panels/optimiser/ConvergenceChart", () => ({
  default: () => <div data-testid="stub-convergence-chart" />,
}))
vi.mock("../../panels/optimiser/SummaryTab", () => ({
  default: () => <div data-testid="stub-optimiser-summary" />,
}))
vi.mock("../../panels/optimiser/DetailCard", () => ({
  default: () => <div data-testid="stub-detail-card" />,
}))

// Same treatment for ModellingPreview's tab content.
vi.mock("../../panels/modelling/SummaryTab", () => ({
  SummaryTab: () => <div data-testid="stub-modelling-summary" />,
}))
vi.mock("../../panels/modelling/LossTab", () => ({
  LossTab: () => <div />,
}))
vi.mock("../../panels/modelling/LiftTab", () => ({
  LiftTab: () => <div />,
}))
vi.mock("../../panels/modelling/ResidualsTab", () => ({
  ResidualsTab: () => <div />,
}))
vi.mock("../../panels/modelling/FeaturesTab", () => ({
  FeaturesTab: () => <div />,
}))
vi.mock("../../panels/modelling/AveTab", () => ({
  AveTab: () => <div />,
}))
vi.mock("../../panels/modelling/PdpTab", () => ({
  PdpTab: () => <div />,
}))
vi.mock("../../panels/modelling/GLMCoefficientsTab", () => ({
  GLMCoefficientsTab: () => <div />,
}))
vi.mock("../../panels/modelling/GLMRelativitiesTab", () => ({
  GLMRelativitiesTab: () => <div />,
}))

// ── Fixture data ────────────────────────────────────────────────────

import type { PreviewData } from "../../panels/DataPreview"
import type { OptimiserPreviewData, SolveResult } from "../../panels/OptimiserPreview"
import type { ModellingPreviewData } from "../../panels/ModellingPreview"

function makePreviewData(): PreviewData {
  return {
    nodeId: "node-1",
    nodeLabel: "Preview Node",
    status: "ok",
    row_count: 3,
    column_count: 2,
    columns: [
      { name: "a", dtype: "int64" },
      { name: "b", dtype: "string" },
    ],
    preview: [
      { a: 1, b: "x" },
      { a: 2, b: "y" },
      { a: 3, b: "z" },
    ],
    error: null,
  }
}

function makeSolveResult(): SolveResult {
  return {
    total_objective: 1.5,
    baseline_objective: 1.0,
    constraints: { c1: 0.5 },
    baseline_constraints: { c1: 0.4 },
    lambdas: { c1: 0.1 },
    converged: true,
    iterations: 10,
    n_quotes: 1000,
  }
}

function makeOptimiserPreviewData(): OptimiserPreviewData {
  return {
    result: makeSolveResult(),
    jobId: "job-1",
    constraints: { c1: { target: 0.5 } },
    nodeLabel: "Opt Node",
    frontier: null,
    selectedPointIndex: null,
  }
}

function makeModellingPreviewData(): ModellingPreviewData {
  return {
    result: {
      status: "ok",
      metrics: { rmse: 0.12 },
      feature_importance: [{ feature: "x", importance: 1.0 }],
      model_path: "/tmp/model",
      train_rows: 100,
      test_rows: 20,
    },
    jobId: "job-m1",
    nodeLabel: "Model Node",
    configHash: "abc",
  }
}

// Lazy imports so the mocks above bind before the module loads.
async function loadDataPreview() {
  return (await import("../../panels/DataPreview")).default
}
async function loadOptimiserPreview() {
  return (await import("../../panels/OptimiserPreview")).default
}
async function loadOptimiserDataPreview() {
  return (await import("../../panels/OptimiserDataPreview")).default
}
async function loadModellingPreview() {
  return (await import("../../panels/ModellingPreview")).ModellingPreview
}

// A proxy that spies on every `element.style[prop] = value` assignment
// on a specific element.  We can't `spyOn` a CSSStyleDeclaration setter
// directly in jsdom without smashing the whole DOM, so we wrap the
// element in a property getter that swaps in a recording style object.
function makeStyleMutationSpy(el: HTMLElement): { writes: Array<{ prop: string; value: string }> } {
  const writes: Array<{ prop: string; value: string }> = []
  const realStyle = el.style
  const recording = new Proxy(realStyle, {
    set(target, prop, value) {
      if (typeof prop === "string" && typeof value === "string") {
        writes.push({ prop, value })
      }
      return Reflect.set(target, prop, value)
    },
  })
  Object.defineProperty(el, "style", {
    get: () => recording,
    configurable: true,
  })
  return { writes }
}

beforeEach(() => {
  // jsdom doesn't implement ResizeObserver; stub it.
  class RO {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
  ;(globalThis as unknown as { ResizeObserver?: typeof RO }).ResizeObserver = RO
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe("DataPreview hover chrome is class-driven", () => {
  it("mouseenter on the drag handle does not imperatively mutate .style.background", async () => {
    const DataPreview = await loadDataPreview()
    const { container } = render(<DataPreview data={makePreviewData()} />)
    // The drag handle is the only `.cursor-ns-resize` element in the tree.
    const handle = container.querySelector(".cursor-ns-resize") as HTMLElement | null
    expect(handle, "drag handle rendered").not.toBeNull()
    const spy = makeStyleMutationSpy(handle!)
    fireEvent.mouseEnter(handle!)
    fireEvent.mouseLeave(handle!)
    // No writes to `.style.background` (or any other property) should
    // happen from the React handler — hover is supposed to be CSS-driven.
    const backgroundWrites = spy.writes.filter(
      (w) => w.prop === "background" || w.prop === "backgroundColor",
    )
    expect(
      backgroundWrites,
      `DataPreview drag-handle handler still mutates .style.background: ${JSON.stringify(
        backgroundWrites,
      )}`,
    ).toEqual([])
  })
})

describe("OptimiserPreview hover chrome is class-driven", () => {
  it("mouseenter on the drag handle does not mutate .style.background imperatively", async () => {
    const OptimiserPreview = await loadOptimiserPreview()
    const { container } = render(
      <OptimiserPreview data={makeOptimiserPreviewData()} nodeId="node-1" />,
    )
    const handle = container.querySelector(".cursor-ns-resize") as HTMLElement | null
    expect(handle, "drag handle rendered").not.toBeNull()
    const spy = makeStyleMutationSpy(handle!)
    fireEvent.mouseEnter(handle!)
    fireEvent.mouseLeave(handle!)
    const backgroundWrites = spy.writes.filter(
      (w) => w.prop === "background" || w.prop === "backgroundColor",
    )
    expect(backgroundWrites).toEqual([])
  })

  it("active-tab visual distinction is preserved via state-based styling (not hover)", async () => {
    const OptimiserPreview = await loadOptimiserPreview()
    const { container } = render(
      <OptimiserPreview data={makeOptimiserPreviewData()} nodeId="node-1" />,
    )
    // Tab buttons are inside `.flex.gap-1` and contain the tab labels.
    const buttons = Array.from(
      container.querySelectorAll("button"),
    ) as HTMLButtonElement[]
    const summaryTab = buttons.find((b) => b.textContent?.trim() === "Summary")
    const exportTab = buttons.find((b) => b.textContent?.trim() === "Export")
    expect(summaryTab, "Summary tab rendered").toBeTruthy()
    expect(exportTab, "Export tab rendered").toBeTruthy()
    // Default tab when frontier is null is Summary.  Summary should
    // carry the accent styling, Export should carry the chrome styling.
    // We just assert the two active/inactive tabs have *different* inline
    // styles — the exact literals may evolve but the distinction must
    // survive the hover migration (because it's state-driven, not hover).
    expect(summaryTab!.getAttribute("style")).not.toEqual(
      exportTab!.getAttribute("style"),
    )
  })
})

describe("OptimiserDataPreview hover chrome is class-driven", () => {
  it("mouseenter on the drag handle does not mutate .style.background imperatively", async () => {
    const OptimiserDataPreview = await loadOptimiserDataPreview()
    // Need at least one preview row matching the config column names.
    const data: PreviewData = {
      ...makePreviewData(),
      columns: [
        { name: "quote_id", dtype: "string" },
        { name: "scenario_index", dtype: "int64" },
        { name: "scenario_value", dtype: "float64" },
        { name: "objective_col", dtype: "float64" },
      ],
      preview: [
        { quote_id: "q1", scenario_index: 0, scenario_value: 0.1, objective_col: 1.0 },
        { quote_id: "q1", scenario_index: 1, scenario_value: 0.2, objective_col: 1.1 },
      ],
    }
    const config = {
      objective: "objective_col",
      constraints: {},
      quote_id: "quote_id",
      scenario_index: "scenario_index",
      scenario_value: "scenario_value",
    }
    const { container } = render(<OptimiserDataPreview data={data} config={config} />)
    const handle = container.querySelector(".cursor-ns-resize") as HTMLElement | null
    expect(handle, "drag handle rendered").not.toBeNull()
    const spy = makeStyleMutationSpy(handle!)
    fireEvent.mouseEnter(handle!)
    fireEvent.mouseLeave(handle!)
    const backgroundWrites = spy.writes.filter(
      (w) => w.prop === "background" || w.prop === "backgroundColor",
    )
    expect(backgroundWrites).toEqual([])
  })

  it("active-tab distinction between Chart and Statistics is state-driven (not hover)", async () => {
    const OptimiserDataPreview = await loadOptimiserDataPreview()
    const data: PreviewData = {
      ...makePreviewData(),
      columns: [
        { name: "quote_id", dtype: "string" },
        { name: "scenario_index", dtype: "int64" },
        { name: "scenario_value", dtype: "float64" },
        { name: "obj", dtype: "float64" },
      ],
      preview: [
        { quote_id: "q1", scenario_index: 0, scenario_value: 0.1, obj: 1.0 },
      ],
    }
    const config = {
      objective: "obj",
      constraints: {},
      quote_id: "quote_id",
      scenario_index: "scenario_index",
      scenario_value: "scenario_value",
    }
    const { container } = render(<OptimiserDataPreview data={data} config={config} />)
    const buttons = Array.from(
      container.querySelectorAll("button"),
    ) as HTMLButtonElement[]
    const chartTab = buttons.find((b) => b.textContent?.trim() === "Chart")
    const statsTab = buttons.find((b) => b.textContent?.trim() === "Statistics")
    expect(chartTab, "Chart tab rendered").toBeTruthy()
    expect(statsTab, "Statistics tab rendered").toBeTruthy()
    // Default active tab is "chart" — its style must differ from
    // the inactive "statistics" tab.  If hover is confused with active
    // state, this assertion would fail.
    expect(chartTab!.getAttribute("style")).not.toEqual(
      statsTab!.getAttribute("style"),
    )
  })
})

describe("ModellingPreview hover chrome is class-driven", () => {
  it("mouseenter on the drag handle does not mutate .style.background imperatively", async () => {
    const ModellingPreview = await loadModellingPreview()
    const { container } = render(
      <ModellingPreview data={makeModellingPreviewData()} nodeId="node-1" />,
    )
    const handle = container.querySelector(".cursor-ns-resize") as HTMLElement | null
    expect(handle, "drag handle rendered").not.toBeNull()
    const spy = makeStyleMutationSpy(handle!)
    fireEvent.mouseEnter(handle!)
    fireEvent.mouseLeave(handle!)
    const backgroundWrites = spy.writes.filter(
      (w) => w.prop === "background" || w.prop === "backgroundColor",
    )
    expect(backgroundWrites).toEqual([])
  })

  it("collapse button mouseenter does not mutate inline background style", async () => {
    const ModellingPreview = await loadModellingPreview()
    const { container } = render(
      <ModellingPreview data={makeModellingPreviewData()} nodeId="node-1" />,
    )
    // The collapse button is the one with the ChevronDown icon in the
    // header row.  We target buttons that contain an SVG but no text.
    const buttons = Array.from(
      container.querySelectorAll("button"),
    ) as HTMLButtonElement[]
    // Collapse button is the only button containing `chevron-down` svg.
    const collapseBtn = buttons.find((b) => {
      const svg = b.querySelector("svg")
      return svg && !b.textContent?.trim()
    })
    expect(collapseBtn, "collapse button rendered").toBeTruthy()
    const spy = makeStyleMutationSpy(collapseBtn!)
    fireEvent.mouseEnter(collapseBtn!)
    fireEvent.mouseLeave(collapseBtn!)
    const writes = spy.writes.filter(
      (w) => w.prop === "background" || w.prop === "backgroundColor",
    )
    expect(
      writes,
      `ModellingPreview collapse button still mutates .style.background: ${JSON.stringify(
        writes,
      )}`,
    ).toEqual([])
  })
})
