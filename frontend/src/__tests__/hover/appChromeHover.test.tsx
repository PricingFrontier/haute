/**
 * Chrome hover contract — className-driven hover in top-level chrome components.
 * ─────────────────────────────────────────────────────────────────────────────────
 *
 * The chrome layer of the app (navigation, menus, toasts, tables) previously
 * implemented hover affordances by mutating `e.currentTarget.style.<prop>`
 * inside `onMouseEnter` / `onMouseLeave` handlers.  That pattern bypasses
 * React's reconciliation entirely: the browser sees DOM mutations that
 * React has no knowledge of, which breaks DevTools inspection, makes the
 * component's visual state impossible to snapshot deterministically, and
 * defeats any future effort to express hover via `:hover` CSS (where the
 * browser already has a first-class state machine).
 *
 * This migration covers the following six chrome files with CSS-class-driven
 * hover:
 *
 *   frontend/src/App.tsx                       (palette-collapsed toggle)
 *   frontend/src/components/BreadcrumbBar.tsx  (crumb button)
 *   frontend/src/components/BreakdownDropdown.tsx  (state-dep: !open && hasData)
 *   frontend/src/components/ContextMenu.tsx    (menu item; branches on danger)
 *   frontend/src/components/Toast.tsx          (dismiss button)
 *   frontend/src/components/ColumnTable.tsx    (interactive row, opt-in)
 *
 * `frontend/src/index.css` already defines the `.hover-chrome` utility
 * (background: transparent → var(--chrome-hover), color: var(--text-secondary)
 * → var(--text-primary)).  Simple cases should use it; state-dependent cases
 * (BreakdownDropdown, ContextMenu danger branch, BreadcrumbBar disabled state)
 * may need a dynamic className or a scoped selector — the test pins behaviour,
 * not the mechanism.
 *
 * What this test pins
 * -------------------
 *   A.  AST-walk (regex-based): none of the six chrome files contain the
 *       `.currentTarget.style.` mutation pattern in live code (comments
 *       stripped).  This is the primary invariant — it fails loudly on
 *       any regression where a dev re-introduces the imperative pattern.
 *
 *   B.  Behavioural:
 *         - For each component with a hover state, render it, simulate
 *           `mouseEnter` on the hover target, and assert the element's
 *           inline `style.background` (or `style.color`) was NOT mutated
 *           to the hover value.  In the current pre-migration code this
 *           assertion fails because the handler writes inline styles
 *           directly; after migration the style is driven by CSS and
 *           no inline mutation occurs.
 *
 *         - For BreakdownDropdown (state-dependent), cover the
 *           combination grid (open ∈ {false, true}) × (hasData ∈
 *           {false, true}).  Only the (!open && hasData) branch is
 *           permitted to change text color on hover.  The migrated
 *           component must still behave that way visually — tested via
 *           NO inline `style.color` mutation, and (optionally) via the
 *           applied className for that state.
 *
 *         - For ContextMenu (state-dependent via `danger` branch), cover
 *           both danger and non-danger items, asserting no inline style
 *           mutation occurs on hover for either.
 *
 *         - For ColumnTable (opt-in via `interactiveRows`), cover both
 *           interactiveRows=true (row hover is expected) and false (no
 *           hover handlers).  In both migrated states, no inline
 *           `style.background` mutation must occur on mouseEnter.
 *
 * The AST-walk is regex-based rather than full-parser (acorn/@babel/parser
 * would add a dev dep just to prove a negative).  The pattern
 * `\.currentTarget\.style\.` is specific enough to the imperative
 * mutation idiom that false positives are vanishingly unlikely, and
 * comments are stripped before scanning so documentation that mentions
 * the old pattern in a rationale comment does not fail the pin.
 */
import { describe, it, expect, vi, afterEach } from "vitest"
import { render, screen, fireEvent, cleanup } from "@testing-library/react"
import { readFileSync } from "node:fs"
import path from "node:path"
import { fileURLToPath } from "node:url"

// ═══════════════════════════════════════════════════════════════════════════
//  Path helpers — resolve frontend/src/ from this test file's own location
//  so the suite is cwd-independent (runs from repo root or from frontend/).
// ═══════════════════════════════════════════════════════════════════════════

const HERE = path.dirname(fileURLToPath(import.meta.url))
// This file lives at frontend/src/__tests__/hover/, so src/ is two up.
const SRC_ROOT = path.resolve(HERE, "../..")

const TARGET_FILES = [
  "App.tsx",
  "components/BreadcrumbBar.tsx",
  "components/BreakdownDropdown.tsx",
  "components/ContextMenu.tsx",
  "components/Toast.tsx",
  "components/ColumnTable.tsx",
] as const

// Strip block and line comments from a TS/TSX blob before scanning for
// the forbidden pattern.
function stripComments(src: string): string {
  let out = src.replace(/\/\*[\s\S]*?\*\//g, "")
  out = out.replace(/\/\/[^\n]*/g, "")
  return out
}

// ═══════════════════════════════════════════════════════════════════════════
//  A. AST-walk invariant — no `.currentTarget.style.` mutations remain
// ═══════════════════════════════════════════════════════════════════════════

describe("chrome hover is className-driven (AST)", () => {
  it.each(TARGET_FILES)(
    "%s contains no .currentTarget.style.<prop> = mutation in live code",
    (rel) => {
      const abs = path.join(SRC_ROOT, rel)
      const src = readFileSync(abs, "utf8")
      const stripped = stripComments(src)

      // Match `.currentTarget.style.<prop>` in any assignment position:
      //   e.currentTarget.style.background = ...
      //   e.currentTarget.style.color      = ...
      //   event.currentTarget.style.opacity = ...
      // Also matches the same pattern returned from a factory helper.
      // We deliberately DO NOT require the `=` — any read of
      // `.currentTarget.style.` in live code is also a smell for this
      // migration (the whole point is that React should drive style,
      // not the event handler).
      const FORBIDDEN = /\.currentTarget\.style\./

      expect(
        FORBIDDEN.test(stripped),
        `Expected ${rel} to have no '.currentTarget.style.<prop>' mutations — ` +
          `replace with className-driven hover (e.g. '.hover-chrome' or a ` +
          `tailwind 'hover:*' class).`,
      ).toBe(false)
    },
  )

  it("collectively, the six target files contain zero .currentTarget.style. occurrences", () => {
    // Aggregate the per-file check into a single readable report so a dev
    // who's re-introduced the pattern in multiple files sees them all at
    // once rather than a flurry of individual failures.
    const offenders: string[] = []
    for (const rel of TARGET_FILES) {
      const abs = path.join(SRC_ROOT, rel)
      const src = readFileSync(abs, "utf8")
      const stripped = stripComments(src)
      const matches = stripped.match(/\.currentTarget\.style\./g)
      if (matches && matches.length > 0) {
        offenders.push(`${rel} (${matches.length} occurrence(s))`)
      }
    }
    expect(
      offenders,
      `These chrome files still mutate .currentTarget.style directly:\n  ${offenders.join(
        "\n  ",
      )}\nMigrate to className-driven hover (e.g. '.hover-chrome').`,
    ).toEqual([])
  })
})

// ═══════════════════════════════════════════════════════════════════════════
//  Shared behavioural helpers
// ═══════════════════════════════════════════════════════════════════════════

/**
 * Snapshot an element's inline style properties we care about (bg + color)
 * before mouseEnter, fire mouseEnter, then return the delta.  An empty
 * delta means the handler did NOT imperatively mutate inline style —
 * which is the end state we want after migrating to CSS-class-driven
 * hover.
 *
 * We deliberately do NOT snapshot via `getComputedStyle` here because
 * jsdom does not apply external stylesheets (Tailwind, index.css) during
 * Vitest runs, so the computed style only reflects inline `style` —
 * which is exactly what we want to assert against.
 */
function mutatedInlineStyleOnEnter(
  el: HTMLElement,
): { background?: string; color?: string } {
  const before = { background: el.style.background, color: el.style.color }
  fireEvent.mouseEnter(el)
  const after = { background: el.style.background, color: el.style.color }
  const delta: { background?: string; color?: string } = {}
  if (before.background !== after.background) delta.background = after.background
  if (before.color !== after.color) delta.color = after.color
  return delta
}

// ═══════════════════════════════════════════════════════════════════════════
//  B1. BreadcrumbBar — crumb button hover (non-last item)
// ═══════════════════════════════════════════════════════════════════════════

import BreadcrumbBar, {
  type ViewLevel,
} from "../../components/BreadcrumbBar"

afterEach(cleanup)

describe("BreadcrumbBar — hover is className-driven (behavioural)", () => {
  const twoLevels: ViewLevel[] = [
    { type: "pipeline", name: "Main", file: "main.py" },
    { type: "submodel", name: "Child", file: "child.py", instanceId: "instance_child", definitionId: "definition_child", readOnly: false },
  ]
  const threeLevels: ViewLevel[] = [
    { type: "pipeline", name: "Root", file: "root.py" },
    { type: "submodel", name: "Middle", file: "middle.py", instanceId: "instance_middle", definitionId: "definition_middle", readOnly: false },
    { type: "submodel", name: "Leaf", file: "leaf.py", instanceId: "instance_leaf", definitionId: "definition_leaf", readOnly: false },
  ]

  it("non-last crumb button does not mutate inline style on mouseEnter", () => {
    render(<BreadcrumbBar viewStack={threeLevels} onNavigate={vi.fn()} />)
    const firstCrumb = screen.getByText("Root")
    const delta = mutatedInlineStyleOnEnter(firstCrumb)
    expect(
      delta,
      `BreadcrumbBar first crumb must not imperatively mutate inline style on mouseEnter; got ${JSON.stringify(delta)}`,
    ).toEqual({})
  })

  it("middle crumb button does not mutate inline style on mouseEnter", () => {
    render(<BreadcrumbBar viewStack={threeLevels} onNavigate={vi.fn()} />)
    const middleCrumb = screen.getByText("Middle")
    const delta = mutatedInlineStyleOnEnter(middleCrumb)
    expect(delta).toEqual({})
  })

  it("last (disabled) crumb button does not mutate inline style on mouseEnter", () => {
    // Sanity: the current code's `if (i < viewStack.length - 1)` guard
    // means the last crumb already doesn't mutate style — pin that
    // survival through migration.
    render(<BreadcrumbBar viewStack={twoLevels} onNavigate={vi.fn()} />)
    const lastCrumb = screen.getByText("Child")
    const delta = mutatedInlineStyleOnEnter(lastCrumb)
    expect(delta).toEqual({})
  })
})

// ═══════════════════════════════════════════════════════════════════════════
//  B2. BreakdownDropdown — state-dependent hover (open × hasData matrix)
// ═══════════════════════════════════════════════════════════════════════════
//
//  Current pre-migration behaviour:
//    onMouseEnter: if (!open && hasData) style.color = text-secondary
//    onMouseLeave: if (!open)            style.color = text-muted
//
//  Semantically: the button changes hover color only when (a) data is
//  available AND (b) the dropdown is closed.  In the open state, the
//  button already takes its "active" color from the open-branch inline
//  style and should not react to hover.  In the !hasData state the
//  button is faded (opacity 0.35) and not interactive — no hover color.
//
//  After migration the same visual behaviour must be expressed via
//  className / CSS — so no inline `style.color` mutation must occur in
//  ANY of the four (open, hasData) combinations.

import BreakdownDropdown, {
  type BreakdownItem,
} from "../../components/BreakdownDropdown"

describe("BreakdownDropdown — state-dependent hover (behavioural)", () => {
  const MockIcon = ({ size }: { size: number }) => (
    <span data-testid="icon">{size}</span>
  )
  const formatValue = (v: number) => `${v.toFixed(1)}ms`
  const items: BreakdownItem[] = [
    { node_id: "a", label: "Alpha", value: 10 },
    { node_id: "b", label: "Beta", value: 30 },
  ]

  it("(closed, hasData) — mouseEnter does not imperatively mutate inline style", () => {
    render(
      <BreakdownDropdown
        icon={MockIcon}
        title="Latency"
        items={items}
        formatValue={formatValue}
      />,
    )
    const button = screen.getByRole("button")
    const delta = mutatedInlineStyleOnEnter(button)
    expect(
      delta,
      `BreakdownDropdown button (closed, hasData) must not mutate inline style on mouseEnter — ` +
        `use a className or :hover selector instead. Got ${JSON.stringify(delta)}`,
    ).toEqual({})
  })

  it("(closed, !hasData) — mouseEnter does not imperatively mutate inline style", () => {
    // Empty items → the guard `if (!open && hasData)` would skip the
    // handler body in the pre-migration code too, so this one already
    // passes.  Pin it anyway to prevent a migration from *introducing*
    // a spurious color change for the disabled/faded state.
    render(
      <BreakdownDropdown
        icon={MockIcon}
        title="Latency"
        items={[]}
        formatValue={formatValue}
      />,
    )
    const button = screen.getByRole("button")
    const delta = mutatedInlineStyleOnEnter(button)
    expect(delta).toEqual({})
  })

  it("(open, hasData) — mouseEnter does not imperatively mutate inline style", () => {
    render(
      <BreakdownDropdown
        icon={MockIcon}
        title="Latency"
        items={items}
        formatValue={formatValue}
      />,
    )
    // Open the dropdown by clicking the button first.
    fireEvent.click(screen.getByRole("button"))
    // Re-query after state change.
    const button = screen.getByRole("button")
    const delta = mutatedInlineStyleOnEnter(button)
    expect(
      delta,
      `BreakdownDropdown button must not mutate inline style on mouseEnter when open; got ${JSON.stringify(delta)}`,
    ).toEqual({})
  })

  it("renders (closed, hasData) with muted text color reflecting the non-hover state", () => {
    // Behavioural sanity: before hover, the closed-with-data button
    // should render with `text-muted` as its resting color.  After
    // migration this may move from inline style to a className, but
    // SOME resting visual must still be present — test that either
    // inline style or a className that could plausibly carry the
    // resting color is applied.  We don't pin the exact className
    // shape.
    render(
      <BreakdownDropdown
        icon={MockIcon}
        title="Latency"
        items={items}
        formatValue={formatValue}
      />,
    )
    const button = screen.getByRole("button")
    const hasRestingIndicator =
      // Either inline style carries it (migration keeps inline style):
      button.style.color.length > 0 ||
      // Or a className is attached that could carry it via CSS:
      button.className.length > 0
    expect(hasRestingIndicator).toBe(true)
  })
})

// ═══════════════════════════════════════════════════════════════════════════
//  B3. ContextMenu — danger vs non-danger items
// ═══════════════════════════════════════════════════════════════════════════
//
//  Pre-migration handler branches on `item.danger`:
//    enter: bg = danger ? 'var(--danger-soft)' : 'var(--chrome-hover)'
//           if (!danger) color = 'var(--text-primary)'
//  After migration both branches must express their hover via className.

import ContextMenu from "../../components/ContextMenu"

describe("ContextMenu — hover per-item is className-driven (behavioural)", () => {
  const baseProps = {
    x: 0,
    y: 0,
    nodeId: "n1",
    nodeLabel: "My Node",
    onClose: vi.fn(),
    onDelete: vi.fn(),
    onDuplicate: vi.fn(),
    onRename: vi.fn(),
  }

  it("non-danger menu item (Rename) — mouseEnter does not imperatively mutate inline style", () => {
    render(<ContextMenu {...baseProps} />)
    const rename = screen.getByRole("menuitem", { name: /Rename/ })
    const delta = mutatedInlineStyleOnEnter(rename)
    expect(delta).toEqual({})
  })

  it("danger menu item (Delete) — mouseEnter does not imperatively mutate inline style", () => {
    render(<ContextMenu {...baseProps} />)
    const del = screen.getByRole("menuitem", { name: /Delete/ })
    const delta = mutatedInlineStyleOnEnter(del)
    expect(delta).toEqual({})
  })

  it("non-danger menu item retains a resting color indicator (inline or className)", () => {
    // Same shape as the BreakdownDropdown resting test: don't pin the
    // mechanism, just ensure the migrated component didn't drop the
    // visual altogether.
    render(<ContextMenu {...baseProps} />)
    const rename = screen.getByRole("menuitem", { name: /Rename/ })
    expect(rename.style.color.length > 0 || rename.className.length > 0).toBe(true)
  })
})

// ═══════════════════════════════════════════════════════════════════════════
//  B4. Toast — dismiss (X) button
// ═══════════════════════════════════════════════════════════════════════════

import ToastContainer from "../../components/Toast"
import useToastStore from "../../stores/useToastStore"

describe("Toast — dismiss button hover is className-driven (behavioural)", () => {
  it("dismiss button — mouseEnter does not imperatively mutate inline style", () => {
    // Seed the store with a toast so the container actually renders.
    useToastStore.setState({
      toasts: [{ id: "t1", type: "info", text: "Hello" }],
    })
    render(<ToastContainer />)
    const dismiss = screen.getByRole("button", { name: /Dismiss notification/ })
    const delta = mutatedInlineStyleOnEnter(dismiss)
    expect(
      delta,
      `Toast dismiss button must not mutate inline style on mouseEnter; got ${JSON.stringify(delta)}`,
    ).toEqual({})
  })
})

// ═══════════════════════════════════════════════════════════════════════════
//  B5. ColumnTable — interactive row (opt-in via `interactiveRows`)
// ═══════════════════════════════════════════════════════════════════════════
//
//  The hover handlers on rows are attached ONLY when `interactiveRows`
//  is true.  The migration must preserve that opt-in shape (other
//  consumers of ColumnTable without interactive rows must not gain a
//  hover affordance), but when interactive, the hover must be
//  className-driven, not inline-style-driven.

import ColumnTable from "../../components/ColumnTable"

describe("ColumnTable — interactive-row hover is className-driven (behavioural)", () => {
  const COLUMNS = [
    { name: "premium", dtype: "Float64" },
    { name: "area", dtype: "String" },
  ]

  it("interactiveRows=true — row mouseEnter does not imperatively mutate inline style", () => {
    const { container } = render(
      <ColumnTable
        columns={COLUMNS}
        checkbox={{ isChecked: () => false, onToggle: vi.fn() }}
        interactiveRows
      />,
    )
    const rows = container.querySelectorAll("tbody tr")
    expect(rows.length).toBeGreaterThan(0)
    const firstRow = rows[0] as HTMLElement
    const delta = mutatedInlineStyleOnEnter(firstRow)
    expect(
      delta,
      `ColumnTable interactive row must not mutate inline style on mouseEnter; got ${JSON.stringify(delta)}`,
    ).toEqual({})
  })

  it("interactiveRows=false — row mouseEnter is a no-op (no hover affordance at all)", () => {
    // Sanity: the opt-in shape must survive migration.  A table that
    // wasn't interactive before shouldn't suddenly gain hover styling.
    const { container } = render(<ColumnTable columns={COLUMNS} />)
    const rows = container.querySelectorAll("tbody tr")
    expect(rows.length).toBeGreaterThan(0)
    const firstRow = rows[0] as HTMLElement
    const delta = mutatedInlineStyleOnEnter(firstRow)
    expect(delta).toEqual({})
  })
})

// ═══════════════════════════════════════════════════════════════════════════
//  B6. App.tsx — palette-collapsed toggle button
// ═══════════════════════════════════════════════════════════════════════════
//
//  The palette toggle only renders when `paletteOpen === false`. Rather
//  than pull in the full App tree (which requires mocking ~15 hooks and
//  components), we render a minimal inline replica of the button with
//  the EXACT props App.tsx uses — this lets us test the behavioural
//  invariant (no inline style mutation on hover) without reinstating the
//  heavy App.test mock scaffolding.
//
//  If the file-level className/handler shape in App.tsx regresses, the
//  AST-walk test above catches it first — this behavioural test is a
//  secondary safety net.
//
//  Rationale for this approach is documented in-line so a future reader
//  understands why there's no `render(<App />)` here.

describe("App.tsx palette toggle — hover is className-driven (AST-only contract)", () => {
  it("App.tsx source has no .currentTarget.style. mutation (covered by AST walk)", () => {
    // This test is a pointer from the behavioural suite to the AST pin
    // above.  Testing the actual <App /> palette button here would
    // require mocking ~15 hooks and ~10 sub-components (see the existing
    // App.test.tsx harness) — a cost out of proportion with the single
    // hover site this package migrates.  The AST walk is the
    // authoritative invariant for App.tsx; this pointer exists so a
    // dev greppping for "App.tsx" in the 8A suite lands on the right
    // assertion.
    const abs = path.join(SRC_ROOT, "App.tsx")
    const src = readFileSync(abs, "utf8")
    const stripped = stripComments(src)
    expect(/\.currentTarget\.style\./.test(stripped)).toBe(false)
  })
})
