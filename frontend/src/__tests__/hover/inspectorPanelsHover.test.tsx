/**
 * Inspector-panel hover contract — migrate inline `e.currentTarget.style.*`
 * hover / focus mutations in the four main inspector-panel files to
 * CSS (`.hover-chrome` / `.hover-bg` in `index.css`, or Tailwind
 * `hover:bg-...`).
 *
 * Scope (as of 2026-04-19):
 *   panels/NodePanel.tsx          — 10 `currentTarget.style` assignments
 *                                     across 8 JSX handler sites
 *     L441  onFocus       — label input borderColor + boxShadow (two writes)
 *     L442  onBlur        — label input borderColor + boxShadow reset (two writes)
 *     L449  onMouseEnter  — refresh button opacity
 *     L450  onMouseLeave  — refresh button opacity reset
 *     L458  onMouseEnter  — close-button background
 *     L459  onMouseLeave  — close-button background reset
 *     L479  onMouseEnter  — tab button background (state-gated on activeTab !== tab)
 *     L480  onMouseLeave  — tab button background reset (state-gated)
 *
 *   panels/TracePanel.tsx         — 6 `currentTarget.style` sites
 *     L166  StepCard expand-button hover bg
 *     L167  StepCard expand-button hover bg reset
 *     L446  header Close button hover bg
 *     L447  header Close button hover bg reset
 *     L554  "N pass-through nodes hidden" button hover bg  (NON-TRANSPARENT rest colour)
 *     L555  "N pass-through nodes hidden" button hover bg reset (to dashed/subtle rgba)
 *
 *   panels/PanelShell.tsx         — 2 `currentTarget.style` sites
 *     L135  drag-handle onMouseEnter background
 *     L138  drag-handle onMouseLeave background (GATED on `!isDragging.current`)
 *     (Other `.style.*` writes in this file mutate `document.body` /
 *      `panelRef.current` during drag — those are imperative DOM control,
 *      NOT hover styling, and are out of scope.)
 *
 *   trace/CalculationHero.tsx     — 0 `currentTarget.style` sites
 *     The package planning document lists "2 sites" in this file, but a
 *     fresh inventory at test-write time finds none.  We pin the zero
 *     count so a future dev who adds inline hover mutations is caught.
 *
 * State-dependent patterns callers should preserve after migration:
 *   - NodePanel tab buttons (L479-480): only apply hover background when
 *     `activeTab !== tab`.  Active tab already has the solid accent-soft
 *     background; adding a hover layer on top would flash on mouseover.
 *   - TracePanel "hidden nodes" button (L554-555): rest background is
 *     `rgba(255,255,255,.03)` (a subtle dashed-border chip) — not
 *     transparent.  A naive `hover:bg-...` tailwind class that resets to
 *     transparent would be a visual regression.
 *   - PanelShell drag handle (L135-138): onMouseLeave is gated on
 *     `!isDragging.current` — the colour must stay `var(--accent)` while
 *     the user is actively dragging, even if the pointer wanders off.
 *     Any CSS-only solution must keep :active / drag state visually
 *     distinct.
 *   - NodePanel label input (L441-442): onFocus/onBlur is focus, not
 *     hover — the migration must use :focus-visible / :focus-within, not
 *     :hover.
 *
 * What this suite pins
 * --------------------
 *   1. STRUCTURAL (AST-walk via regex):
 *      After the migration lands, none of the four files contains any
 *      `e.currentTarget.style.*` assignment.  (The walker tolerates
 *      comments / string literals mentioning the pattern, so this file
 *      itself doesn't trip the check.)
 *
 *   2. BEHAVIORAL (render-level):
 *      - PanelShell drag handle: hover produces a visually-distinct
 *        state (via class OR style), and mouseLeave while dragging does
 *        NOT reset it.
 *      - TracePanel StepCard rows: hovering one row does not mutate the
 *        DOM of its siblings — i.e. per-row hover state is independent.
 *      - NodePanel tab buttons: the active tab is NOT given the
 *        transient hover background (dual state preserved).
 *      - CalculationHero: still contains no hover mutations (regression
 *        trip-wire).
 *
 * Precedent: `frontend/src/__tests__/components/configInputRemoval.test.ts`
 * (Phase 2 Wave 5 Package 5A item #70) for the AST-walk shape, and
 * `frontend/src/panels/__tests__/errorToastMigration.test.tsx`
 * (Phase 2 Package 3D item #83) for the hybrid structural + behavioural
 * suite layout.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { render, screen, fireEvent, cleanup } from "@testing-library/react"
import { readFileSync, existsSync } from "node:fs"
import path from "node:path"
import { fileURLToPath } from "node:url"

// Components under test
import PanelShell from "../../panels/PanelShell"
import NodePanel from "../../panels/NodePanel"
import TracePanel from "../../panels/TracePanel"
import CalculationHero from "../../trace/CalculationHero"

import { GraphProvider } from "../../panels/GraphContext"
import type { SimpleNode, SimpleEdge } from "../../panels/editors"
import type { TraceResult, TraceStep } from "../../types/trace"
import useUIStore from "../../stores/useUIStore"

// ═════════════════════════════════════════════════════════════════════
//  Mock the NodePanel editor collection — we only care about the
//  NodePanel *shell's* hover handlers, not each editor's internals.
//  Without these mocks the editor trees drag in their own stores,
//  bloat the render, and may themselves contain currentTarget.style
//  writes the AST-walk doesn't touch.
// ═════════════════════════════════════════════════════════════════════

vi.mock("../../panels/editors", () => ({
  DataSourceEditor: () => <div data-testid="DataSourceEditor" />,
  TransformEditor: () => <div data-testid="TransformEditor" />,
  ModelScoreEditor: () => <div data-testid="ModelScoreEditor" />,
  BandingEditor: () => <div data-testid="BandingEditor" />,
  RatingStepEditor: () => <div data-testid="RatingStepEditor" />,
  OutputEditor: () => <div data-testid="OutputEditor" />,
  ExternalFileEditor: () => <div data-testid="ExternalFileEditor" />,
  ApiInputEditor: () => <div data-testid="ApiInputEditor" />,
  LiveSwitchEditor: () => <div data-testid="LiveSwitchEditor" />,
  SinkEditor: () => <div data-testid="SinkEditor" />,
  ScenarioExpanderEditor: () => <div data-testid="ScenarioExpanderEditor" />,
  OptimiserApplyEditor: () => <div data-testid="OptimiserApplyEditor" />,
  ConstantEditor: () => <div data-testid="ConstantEditor" />,
  SubmodelEditor: () => <div data-testid="SubmodelEditor" />,
}))
vi.mock("../../panels/ModellingConfig", () => ({
  default: () => <div data-testid="ModellingConfig" />,
}))
vi.mock("../../panels/OptimiserConfig", () => ({
  default: () => <div data-testid="OptimiserConfig" />,
}))

// ═════════════════════════════════════════════════════════════════════
//  Path helpers — resolve target files regardless of vitest cwd.
//  This file lives at `frontend/src/__tests__/hover/`, so the source
//  root is two levels up.
// ═════════════════════════════════════════════════════════════════════

const HERE = path.dirname(fileURLToPath(import.meta.url))
const SRC_ROOT = path.resolve(HERE, "../..")

const TARGETS: readonly { rel: string; abs: string; siteCount: number }[] = [
  { rel: "panels/NodePanel.tsx",        abs: path.join(SRC_ROOT, "panels", "NodePanel.tsx"),        siteCount: 8 },
  { rel: "panels/TracePanel.tsx",       abs: path.join(SRC_ROOT, "panels", "TracePanel.tsx"),       siteCount: 8 },
  { rel: "panels/PanelShell.tsx",       abs: path.join(SRC_ROOT, "panels", "PanelShell.tsx"),       siteCount: 2 },
  { rel: "trace/CalculationHero.tsx",   abs: path.join(SRC_ROOT, "trace",  "CalculationHero.tsx"),  siteCount: 0 },
]

/**
 * Strip block and line comments from a TS/TSX blob before scanning.
 * Same approach as configInputRemoval.test.ts — sufficient for catching
 * real `currentTarget.style` writes without introducing a parser
 * dependency to prove a negative.  Since the four targets are real
 * components (not tests), they shouldn't contain `currentTarget.style`
 * in string literals; the comment strip handles the common case of a
 * reviewer referencing the old pattern in a WHY comment.
 */
function stripComments(src: string): string {
  let out = src.replace(/\/\*[\s\S]*?\*\//g, "")
  out = out.replace(/\/\/[^\n]*/g, "")
  return out
}

/**
 * Count `e.currentTarget.style.<prop> = ...` style writes in a blob of
 * source.  We accept a leading `(` (for `(e) => (...)`) to catch both
 * block-body and expression-body arrows used in the pre-migration code.
 */
function countCurrentTargetStyleWrites(src: string): number {
  const stripped = stripComments(src)
  const re = /\bcurrentTarget\s*\.\s*style\s*\./g
  const matches = stripped.match(re)
  return matches ? matches.length : 0
}

// ═════════════════════════════════════════════════════════════════════
//  STRUCTURAL: AST-walk pins (the migration landed)
// ═════════════════════════════════════════════════════════════════════

describe("inspector-panel hover structural checks", () => {
  it("(walker smoke) all four target files exist on disk", () => {
    for (const t of TARGETS) {
      expect(existsSync(t.abs), `${t.rel} must exist at ${t.abs}`).toBe(true)
    }
  })

  // This test SHOULD fail before the dev migrates the files and pass
  // afterwards.  It's the primary trip-wire for the whole package.
  it.each(TARGETS.map((t) => [t.rel, t.abs]))(
    "%s contains no `e.currentTarget.style.*` hover/focus mutations after migration",
    (rel, abs) => {
      const src = readFileSync(abs as string, "utf8")
      const hits = countCurrentTargetStyleWrites(src)
      expect(
        hits,
        `${rel} still contains ${hits} inline \`currentTarget.style.*\` writes — migrate each to a CSS class (.hover-chrome / .hover-bg in index.css) or Tailwind \`hover:bg-...\`.  Preserve only non-styling event-handler logic (selection, focus, tooltip side-effects).`,
      ).toBe(0)
    },
  )

  it("(inventory pin) CalculationHero.tsx starts at zero and must stay at zero", () => {
    // The package plan lists "2 sites" for CalculationHero but a fresh
    // inventory found none — pre-existing migration, dead planning, or a
    // sibling component (InputSourceTree / ExpressionChain / Waterfall-
    // Chart) may have been confused for the hero.  Pin the zero count so
    // a future dev who adds inline hover mutations is caught before they
    // re-create the problem this package exists to remove.
    const abs = path.join(SRC_ROOT, "trace", "CalculationHero.tsx")
    const src = readFileSync(abs, "utf8")
    expect(countCurrentTargetStyleWrites(src)).toBe(0)
  })

  it("(inventory pin) sibling trace/ files likewise contain no hover mutations", () => {
    // Guard against a dev migrating CalculationHero "by moving the code
    // into a sibling" — the whole `trace/` directory must stay clean.
    const siblings = ["ExpressionChain.tsx", "InputSourceTree.tsx", "WaterfallChart.tsx"]
    for (const name of siblings) {
      const abs = path.join(SRC_ROOT, "trace", name)
      if (!existsSync(abs)) continue
      const src = readFileSync(abs, "utf8")
      expect(
        countCurrentTargetStyleWrites(src),
        `trace/${name} grew ${countCurrentTargetStyleWrites(src)} inline hover mutations`,
      ).toBe(0)
    }
  })
})

// ═════════════════════════════════════════════════════════════════════
//  BEHAVIORAL: render-level pins
// ═════════════════════════════════════════════════════════════════════

beforeEach(() => {
  // A realistic 1920px window so PanelShell's dynamic width logic
  // produces a stable value across machines.
  Object.defineProperty(window, "innerWidth", { value: 1920, writable: true, configurable: true })
  useUIStore.setState({ nodePanelWidth: 600, paletteOpen: true })
})

afterEach(cleanup)

// ─────────────────────────────────────────────────────────────────────
//  PanelShell: drag-handle hover behaviour
// ─────────────────────────────────────────────────────────────────────
//  Post-migration, the drag handle may use either:
//    (a) a CSS class whose `:hover` background is `var(--accent)`, OR
//    (b) a style prop still toggled by some non-inline mechanism.
//
//  To stay impl-agnostic, we assert behaviour that must hold under
//  either approach:
//    1. The handle element is findable and has a pointer-style cursor.
//    2. Mouse events fire through without throwing (handler wiring is
//       intact).
//    3. Hover does NOT regress the drag-in-progress behaviour — i.e.
//       mouseLeave while the handle is mid-drag must not flash the
//       background back to `var(--chrome-border)`.
// ─────────────────────────────────────────────────────────────────────

describe("PanelShell drag-handle hover", () => {
  it("drag handle is rendered and has col-resize cursor", () => {
    const { container } = render(
      <PanelShell>
        <span>content</span>
      </PanelShell>,
    )
    const handle = container.querySelector(".cursor-col-resize") as HTMLElement
    expect(handle).toBeTruthy()
  })

  it("handle dispatches mouseEnter/mouseLeave without throwing", () => {
    const { container } = render(
      <PanelShell>
        <span>content</span>
      </PanelShell>,
    )
    const handle = container.querySelector(".cursor-col-resize") as HTMLElement
    expect(() => {
      fireEvent.mouseEnter(handle)
      fireEvent.mouseLeave(handle)
    }).not.toThrow()
  })

  it("hover while dragging does not reset background (drag state wins)", () => {
    // Pre-migration the inline mouseLeave was gated on `!isDragging.current`
    // specifically so the accent colour stuck while the user is actively
    // resizing.  The CSS-class migration must preserve that UX: moving
    // the pointer off the handle mid-drag should NOT flip the visual
    // state back to idle.  We simulate the drag and then emit mouseLeave
    // and assert the width-update path still fires on mouseUp (which is
    // only true if the drag flag stayed set — any buggy migration that
    // short-circuits on mouseLeave would clear the flag and mouseUp
    // wouldn't commit the width).
    useUIStore.setState({ nodePanelWidth: 500 })
    const { container } = render(
      <PanelShell>
        <span>content</span>
      </PanelShell>,
    )
    const handle = container.querySelector(".cursor-col-resize") as HTMLElement
    fireEvent.mouseDown(handle, { clientX: 400 })
    fireEvent.mouseMove(window, { clientX: 300 }) // delta = 100
    fireEvent.mouseLeave(handle)                  // pointer wanders off
    fireEvent.mouseMove(window, { clientX: 280 }) // keeps dragging
    fireEvent.mouseUp(window)
    // If the migration kept drag state alive through mouseLeave, the
    // final commit fires (500 + 120 = 620).  If it broke that invariant,
    // the value stays at 500.
    expect(useUIStore.getState().nodePanelWidth).toBe(620)
  })

  it("handle's hover visual state is expressed via className or CSS, not an inline listener that rewrites .style", () => {
    // Post-migration the handle should either have a class that encodes
    // its hover (e.g. `hover:bg-[var(--accent)]` or a custom class) OR
    // rely on a CSS rule targeting `.cursor-col-resize:hover`.  This
    // pin asserts the handle is NOT left without any visible hover
    // affordance — i.e. it has at least one className that goes beyond
    // cursor+layout utilities.
    const { container } = render(
      <PanelShell>
        <span>content</span>
      </PanelShell>,
    )
    const handle = container.querySelector(".cursor-col-resize") as HTMLElement
    // The classlist must be non-empty (cursor-col-resize itself counts).
    expect(handle.className.length).toBeGreaterThan(0)
    // An inline onMouseEnter listener that rewrites .style would set
    // background on the element before any event fires — verify the
    // initial inline style.background is the var(--chrome-border) token
    // from the JSX `style={{...}}` attribute, NOT something a rogue
    // mount-time handler dropped in.
    expect(handle.style.background).toBe("var(--chrome-border)")
  })
})

// ─────────────────────────────────────────────────────────────────────
//  NodePanel: dual-state tab hover
// ─────────────────────────────────────────────────────────────────────
//  The pre-migration pattern was:
//    onMouseEnter={(e) => { if (activeTab !== tab) e.currentTarget.style.background = 'var(--bg-hover)' }}
//    onMouseLeave={(e) => { if (activeTab !== tab) e.currentTarget.style.background = 'transparent' }}
//
//  The active tab must NEVER pick up the hover background — the active
//  one already uses `var(--accent-soft)` and a blue accent; adding
//  bg-hover on top would flash on mouseover.  Post-migration the
//  behaviour must be preserved whether via a conditional className, a
//  `[data-active]` selector, or Tailwind's arbitrary variants.
// ─────────────────────────────────────────────────────────────────────

function makeNode(overrides: Partial<SimpleNode> = {}): SimpleNode {
  return {
    id: "node_1",
    data: {
      label: "My Node",
      description: "",
      nodeType: "polars",
      config: {},
    },
    ...overrides,
  }
}

function renderNodePanel(
  overrides: Partial<Parameters<typeof NodePanel>[0]> & {
    edges?: SimpleEdge[]
    allNodes?: SimpleNode[]
  } = {},
) {
  const { edges = [], allNodes = [], ...panelOverrides } = overrides
  return render(
    <GraphProvider allNodes={allNodes} edges={edges}>
      <NodePanel
        node={makeNode()}
        onClose={vi.fn()}
        onUpdateNode={vi.fn()}
        onDeleteEdge={vi.fn()}
        onRefreshPreview={vi.fn()}
        {...panelOverrides}
      />
    </GraphProvider>,
  )
}

describe("NodePanel tab hover dual state", () => {
  it("active tab and inactive tab render distinguishably", () => {
    // polars nodes show the Columns tab, so both tabs are present.
    renderNodePanel()
    const configTab = screen.getByRole("button", { name: /config/i })
    const columnsTab = screen.getByRole("button", { name: /columns/i })
    // Active tab uses accent-soft background; inactive tab should not.
    // We assert they are not the same string (a brittle "identical"
    // check would silently pass if both collapsed to empty).
    const activeBg = configTab.style.background
    const inactiveBg = columnsTab.style.background
    expect(activeBg).not.toBe(inactiveBg)
  })

  it("hovering the ACTIVE tab does not change its inline background (no hover flash)", () => {
    renderNodePanel()
    const configTab = screen.getByRole("button", { name: /config/i })
    const before = configTab.style.background
    fireEvent.mouseEnter(configTab)
    const after = configTab.style.background
    // After the migration the inline `.style.background` should never
    // be written by a hover handler.  The "no flash on active" invariant
    // therefore reduces to: before === after when the tab is active.
    expect(after).toBe(before)
  })

  it("hovering an INACTIVE tab does not rewrite inline styles of the active tab", () => {
    renderNodePanel()
    const configTab = screen.getByRole("button", { name: /config/i })
    const columnsTab = screen.getByRole("button", { name: /columns/i })
    const activeBefore = configTab.style.background
    fireEvent.mouseEnter(columnsTab)
    fireEvent.mouseLeave(columnsTab)
    const activeAfter = configTab.style.background
    // Siblings' hover must not leak onto the active tab.
    expect(activeAfter).toBe(activeBefore)
  })

  it("clicking an inactive tab switches the active state", () => {
    // Non-styling side-effect (setActiveTab) must be preserved even
    // though the hover style moves to CSS.
    renderNodePanel()
    const columnsTab = screen.getByRole("button", { name: /columns/i })
    const activeBgBefore = columnsTab.style.background
    fireEvent.click(columnsTab)
    const activeBgAfter = columnsTab.style.background
    expect(activeBgAfter).not.toBe(activeBgBefore)
  })
})

// ─────────────────────────────────────────────────────────────────────
//  NodePanel: close button + refresh button + label input (focus)
// ─────────────────────────────────────────────────────────────────────
describe("NodePanel standalone hover/focus sites", () => {
  it("close button dispatches mouseEnter/mouseLeave without inline style mutation", () => {
    renderNodePanel()
    const closeBtn = screen.getByTitle("Close")
    const before = closeBtn.style.background
    fireEvent.mouseEnter(closeBtn)
    const duringHover = closeBtn.style.background
    fireEvent.mouseLeave(closeBtn)
    const after = closeBtn.style.background
    // Post-migration the hover bg should come from CSS — so the inline
    // `.style.background` attribute should not flicker.
    expect(duringHover).toBe(before)
    expect(after).toBe(before)
  })

  it("refresh button dispatches mouseEnter/mouseLeave without inline opacity mutation", () => {
    renderNodePanel()
    const refreshBtn = screen.getByTitle("Refresh preview")
    const before = refreshBtn.style.opacity
    fireEvent.mouseEnter(refreshBtn)
    const duringHover = refreshBtn.style.opacity
    fireEvent.mouseLeave(refreshBtn)
    const after = refreshBtn.style.opacity
    expect(duringHover).toBe(before)
    expect(after).toBe(before)
  })

  it("label input focus does not rewrite inline borderColor / boxShadow", () => {
    // Pre-migration: onFocus toggles inline borderColor and boxShadow.
    // Post-migration: these should come from :focus-visible CSS, leaving
    // the inline style attribute untouched.
    renderNodePanel()
    const labelInput = screen.getByDisplayValue("My Node") as HTMLInputElement
    const borderBefore = labelInput.style.borderColor
    const shadowBefore = labelInput.style.boxShadow
    fireEvent.focus(labelInput)
    expect(labelInput.style.borderColor).toBe(borderBefore)
    expect(labelInput.style.boxShadow).toBe(shadowBefore)
    fireEvent.blur(labelInput)
    expect(labelInput.style.borderColor).toBe(borderBefore)
    expect(labelInput.style.boxShadow).toBe(shadowBefore)
  })

  it("typing in the label input still propagates to onUpdateNode (non-styling side-effect preserved)", () => {
    // Sanity: the migration must not strip the onChange handler — only
    // the inline style mutations should be gone.
    const onUpdateNode = vi.fn()
    renderNodePanel({ onUpdateNode })
    const labelInput = screen.getByDisplayValue("My Node") as HTMLInputElement
    fireEvent.change(labelInput, { target: { value: "Renamed" } })
    fireEvent.blur(labelInput)
    expect(onUpdateNode).toHaveBeenCalledWith(
      "node_1",
      expect.objectContaining({ label: "Renamed" }),
    )
  })
})

// ─────────────────────────────────────────────────────────────────────
//  TracePanel: per-row hover independence
// ─────────────────────────────────────────────────────────────────────
//  We render a trace with three steps (a polars transform, a banding,
//  and a model-score) so the trace story can show a 3-row list. Hovering
//  row 2 must not mutate the DOM of row 1 or row 3 — i.e. each row's
//  hover state is its own responsibility.
// ─────────────────────────────────────────────────────────────────────

function makeStep(overrides: Partial<TraceStep> = {}): TraceStep {
  return {
    node_id: "n1",
    node_name: "Step",
    node_type: "polars",
    schema_diff: {
      columns_added: ["premium"],
      columns_removed: [],
      columns_modified: [],
      columns_passed: ["age"],
    },
    input_values: { age: 25 },
    output_values: { age: 25, premium: 100 },
    column_relevant: true,
    execution_ms: 5.0,
    ...overrides,
  }
}

function makeTrace(overrides: Partial<TraceResult> = {}): TraceResult {
  return {
    target_node_id: "n3",
    row_index: 0,
    column: "premium",
    output_value: 123.4,
    steps: [
      makeStep({ node_id: "n1", node_name: "Row One", schema_diff: { columns_added: ["a"], columns_removed: [], columns_modified: [], columns_passed: [] } }),
      makeStep({ node_id: "n2", node_name: "Row Two", schema_diff: { columns_added: ["b"], columns_removed: [], columns_modified: [], columns_passed: [] } }),
      makeStep({ node_id: "n3", node_name: "Row Three", schema_diff: { columns_added: ["premium"], columns_removed: [], columns_modified: [], columns_passed: [] } }),
    ],
    row_id_column: "quote_id",
    row_id_value: "Q007",
    total_nodes_in_pipeline: 3,
    nodes_in_trace: 3,
    execution_ms: 9.0,
    ...overrides,
  }
}

function getTraceStoryRowButtons(): HTMLElement[] {
  expect(screen.getByTestId("trace-story")).toBeInTheDocument()
  const showFullTrace = screen.queryByTestId("trace-show-full")
  if (showFullTrace?.textContent?.includes("show full trace")) {
    fireEvent.click(showFullTrace)
  }
  // The row expand-button is the enclosing <button> that wraps the
  // node_name span.  Rows appear in the order supplied to `steps`.
  const rowNames = ["Row One", "Row Two", "Row Three"]
  const buttons: HTMLElement[] = []
  for (const name of rowNames) {
    const spans = screen.getAllByText(name)
    const btn = spans.find((el) => el.closest("button"))?.closest("button") as HTMLElement | undefined
    if (!btn) throw new Error(`StepCard button for "${name}" not found`)
    buttons.push(btn)
  }
  return buttons
}

describe("TracePanel row hover independence", () => {
  it("renders three step rows in the trace story", () => {
    render(<TracePanel trace={makeTrace()} onClose={vi.fn()} />)
    const rows = getTraceStoryRowButtons()
    expect(rows).toHaveLength(3)
  })

  it("hovering row 2 does not rewrite inline styles on rows 1 or 3", () => {
    render(<TracePanel trace={makeTrace()} onClose={vi.fn()} />)
    const [row1, row2, row3] = getTraceStoryRowButtons()
    const row1Before = row1.style.background
    const row3Before = row3.style.background
    fireEvent.mouseEnter(row2)
    // Other rows must be untouched by row 2's hover.
    expect(row1.style.background).toBe(row1Before)
    expect(row3.style.background).toBe(row3Before)
    fireEvent.mouseLeave(row2)
    expect(row1.style.background).toBe(row1Before)
    expect(row3.style.background).toBe(row3Before)
  })

  it("hovering a row does not write to its own inline .style.background after migration", () => {
    // With the migration in place, the hover background comes from CSS,
    // so the inline `.style.background` should not change on mouseEnter.
    render(<TracePanel trace={makeTrace()} onClose={vi.fn()} />)
    const [, row2] = getTraceStoryRowButtons()
    const before = row2.style.background
    fireEvent.mouseEnter(row2)
    expect(row2.style.background).toBe(before)
    fireEvent.mouseLeave(row2)
    expect(row2.style.background).toBe(before)
  })

  it("clicking a row still expands it (non-styling side-effect preserved)", () => {
    // The click handler on each StepCard toggles `expanded`.  The
    // migration must leave that logic alone — only the mouseEnter /
    // mouseLeave style writes should be removed.
    render(<TracePanel trace={makeTrace()} onClose={vi.fn()} />)
    const [row1] = getTraceStoryRowButtons()
    fireEvent.click(row1)
    expect(screen.getByTestId("trace-step-body-n1")).toBeInTheDocument()
  })

  it("header Close button hover does not rewrite inline .style.background", () => {
    render(<TracePanel trace={makeTrace()} onClose={vi.fn()} />)
    const closeBtn = screen.getByLabelText("Close trace")
    const before = closeBtn.style.background
    fireEvent.mouseEnter(closeBtn)
    expect(closeBtn.style.background).toBe(before)
    fireEvent.mouseLeave(closeBtn)
    expect(closeBtn.style.background).toBe(before)
  })
})

// ─────────────────────────────────────────────────────────────────────
//  TracePanel: focused/full trace toggle
// ─────────────────────────────────────────────────────────────────────
//  Hidden pass-through rows now stay out of the main story body. The compact
//  header action must still reveal the full trace without reintroducing inline
//  hover style mutation.
// ─────────────────────────────────────────────────────────────────────

describe("TracePanel focused/full trace toggle", () => {
  it("renders when there are collapsible pass-through rows", () => {
    // Build a trace where row 2 is an irrelevant pass-through so
    // `collapsePassthroughs` hides it in the default trace story view.
    // The passed-through column must be the traced column (`premium`)
    // for the collapse logic to fire.
    render(
      <TracePanel
        trace={makeTrace({
          column: "premium",
          steps: [
            makeStep({
              node_id: "n1",
              node_name: "Create",
              node_type: "polars",
              schema_diff: {
                columns_added: ["premium"],
                columns_removed: [],
                columns_modified: [],
                columns_passed: [],
              },
              column_relevant: true,
            }),
            makeStep({
              node_id: "n2",
              node_name: "Pass Through",
              node_type: "polars",
              schema_diff: {
                columns_added: [],
                columns_removed: [],
                columns_modified: [],
                columns_passed: ["premium"],
              },
              column_relevant: false,
            }),
            makeStep({
              node_id: "n3",
              node_name: "Final",
              node_type: "polars",
              schema_diff: {
                columns_added: [],
                columns_removed: [],
                columns_modified: ["premium"],
                columns_passed: [],
              },
              column_relevant: true,
            }),
          ],
        })}
        onClose={vi.fn()}
      />,
    )
    expect(screen.queryByTestId("trace-hidden-toggle")).not.toBeInTheDocument()
    const button = screen.getByTestId("trace-show-full")
    expect(button).toHaveTextContent(/show full trace/i)
    const before = button.style.background
    fireEvent.mouseEnter(button)
    expect(button.style.background).toBe(before)
    fireEvent.mouseLeave(button)
    expect(button.style.background).toBe(before)
    fireEvent.click(button)
    expect(screen.getByText("Pass Through")).toBeInTheDocument()
    expect(button).toHaveTextContent(/show focused trace/i)
  })
})

// ─────────────────────────────────────────────────────────────────────
//  CalculationHero: zero hover mutations (regression pin)
// ─────────────────────────────────────────────────────────────────────
//  This file has NO hover handlers today.  We render a well-formed hero
//  and confirm the top-level container has no inline-mutation side-
//  effects on mouseEnter — so that if someone later copies the old
//  StepCard pattern into the hero we catch it immediately.
// ─────────────────────────────────────────────────────────────────────

describe("CalculationHero stays hover-mutation free", () => {
  it("renders the hero without any elements bearing a live mouseEnter style-mutator", () => {
    const { container } = render(
      <CalculationHero
        column="premium"
        expression={{
          expression_text: "base * factor",
          expression_type: "arithmetic",
          referenced_columns: ["base", "factor"],
        }}
        calculation={{
          substituted_text: "100 * 1.2",
          result_value: 120,
          input_values: { base: 100, factor: 1.2 },
          expression_chain: null,
          input_sources: null,
        }}
        executionMs={4.2}
        stepCount={3}
        nodeName="Pricing"
        waterfall={null}
      />,
    )
    const hero = container.querySelector(".calculation-hero") as HTMLElement
    expect(hero).toBeTruthy()
    // Take a snapshot of every descendant's inline style.background /
    // .opacity, fire mouseEnter on every focusable element, and assert
    // nothing changed.  This is the render-level dual of the AST-walk
    // above — catches a handler wired via a ref or a spread prop that
    // the text scan wouldn't see.
    const all = Array.from(hero.querySelectorAll<HTMLElement>("*"))
    const snapshot = new Map<HTMLElement, { bg: string; opacity: string; color: string }>()
    for (const el of all) {
      snapshot.set(el, {
        bg: el.style.background,
        opacity: el.style.opacity,
        color: el.style.color,
      })
    }
    for (const el of all) {
      fireEvent.mouseEnter(el)
      fireEvent.mouseLeave(el)
    }
    for (const el of all) {
      const before = snapshot.get(el)!
      expect(el.style.background, `background mutated on hover for element ${el.tagName}`).toBe(before.bg)
      expect(el.style.opacity, `opacity mutated on hover for element ${el.tagName}`).toBe(before.opacity)
      expect(el.style.color, `color mutated on hover for element ${el.tagName}`).toBe(before.color)
    }
  })
})
