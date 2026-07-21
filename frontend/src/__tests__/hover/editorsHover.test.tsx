/**
 * Editor hover contract — pin the migration of the node-editor
 * hover affordances away from inline `onMouseEnter` / `onMouseLeave`
 * handlers that mutate `e.currentTarget.style.*`, and away from the
 * `utils/hoverHandlers.ts` factory helpers.
 *
 * Target end state
 * ----------------
 * Hover visuals on chrome-style buttons, icon buttons, clickable
 * rows, and focus-ring inputs under `frontend/src/panels/editors/*`
 * are driven by plain CSS `:hover` / `:focus` selectors (the
 * `.hover-chrome` utility class in `index.css`, similar `.hover-bg`
 * helpers, Tailwind `hover:bg-*` / `focus:*` variants, or any other
 * pure-CSS affordance).  The JS event handlers that write to
 * `element.style.*` on hover / focus go away entirely.
 *
 * Why this matters
 * ----------------
 * Inline `style` mutation inside `onMouseEnter` et al. is a
 * well-known concurrent-rendering hazard (React 18 / 19 may commit
 * stale inline style after a re-render, because the last value
 * written imperatively has no place in the VDOM and is therefore
 * lost on next reconcile).  It also adds per-event JS cost for
 * visuals that the browser is perfectly capable of producing for
 * free via the `:hover` pseudo-class.  The same issue applies to
 * the `hoverHandlers` / `hoverBg` factory helpers in
 * `frontend/src/utils/hoverHandlers.ts` — they only centralise the
 * identical mutation, they don't remove the hazard.
 *
 * Scope — exactly these seven files
 * -----------------------------------------------------
 *   frontend/src/panels/editors/_shared.tsx
 *   frontend/src/panels/editors/RatingStepEditor.tsx
 *   frontend/src/panels/editors/SinkEditor.tsx
 *   frontend/src/panels/editors/GroupedColumnsTab.tsx
 *   frontend/src/panels/editors/ConstantEditor.tsx
 *   frontend/src/panels/editors/DataSourceEditor.tsx
 *   frontend/src/panels/editors/_DatabricksSelector.tsx
 *
 * Inventory taken at test-write time (2026-04-19):
 *
 *   _shared.tsx                 — 4 sites (2 pairs of enter/leave):
 *     ~L199-200  FileBrowser row  background toggle
 *     ~L711-712  InputSourcesBar  delete-X icon color toggle
 *
 *   RatingStepEditor.tsx        — 6 sites (3 pairs):
 *     ~L189-190  add-table "+"   borderColor + color toggle
 *     ~L228-229  factor remove-X color toggle
 *     ~L270-271  "↻ Rebuild"     borderColor + color toggle
 *
 *   SinkEditor.tsx              — 4 sites (2 pairs):
 *     ~L78-79    path input       focus-ring borderColor + boxShadow
 *     ~L88-89    Write button     opacity toggle on hover
 *
 *   GroupedColumnsTab.tsx       — 4 sites (2 pairs):
 *     ~L413-414  group-header row   background toggle
 *     ~L477-478  pattern row        background toggle
 *
 *   ConstantEditor.tsx          — 4 sites (2 pairs):
 *     ~L69-70    remove-row-X     color toggle
 *     ~L87-88    "Add value" btn  background toggle
 *
 *   DataSourceEditor.tsx        — 2 sites (1 pair):
 *     ~L104-105  SQL textarea     focus-ring borderColor + boxShadow
 *
 *   _DatabricksSelector.tsx     — 2 sites (1 pair):
 *     ~L76-77    warehouse input  focus-ring borderColor + boxShadow
 *
 *   Total: 26 style-mutation sites across 7 files.
 *
 * None of the seven files currently imports from
 * `utils/hoverHandlers.ts` — every hover is spelled inline.  The
 * hoverHandlers module is still pinned here because the dev fix
 * must not substitute one hazard (inline writes) for another
 * (centralised writes via a factory) — the factory calls would also
 * be banned from these files after migration.
 *
 * What this test pins
 * -------------------
 *   1. For each of the 7 files, the AST contains zero
 *      `*.currentTarget.style.*` member expressions — i.e. no code
 *      path writes to an element's inline style from an event
 *      handler.
 *   2. For each of the 7 files, no `ImportDeclaration` resolves to
 *      `utils/hoverHandlers` (any relative spelling) and the
 *      identifiers `hoverHandlers` / `hoverBg` are not named in
 *      import specifiers.
 *   3. The `_shared.tsx` hover-affordance component
 *      (`InputSourcesBar`, whose delete-X button currently has a
 *      red-on-hover color toggle) renders, accepts a mouse-over
 *      gesture, and does not mutate its own inline style — instead
 *      the post-hover button must either have a className conveying
 *      the affordance OR an empty inline `style.color`, proving
 *      the color is coming from CSS rather than JS.
 *   4. Representative integration tests for `RatingStepEditor`
 *      (the largest target, 6 sites in 3 distinct button shapes):
 *        a. the "+" add-table button: hovering it does NOT mutate
 *           `borderColor` / `color` via JS.
 *        b. the per-factor remove-X button (visible when a factor
 *           is chosen): hovering it does NOT mutate `color` via JS.
 *        c. the "↻ Rebuild" button (visible when factors selected):
 *           hovering it does NOT mutate `borderColor` / `color`.
 *
 * The AST walk uses `@babel/parser` with `typescript` + `jsx`
 * plugins so the checks survive any syntactic rearrangement the
 * dev makes (template strings, optional chaining, destructured
 * event params, arrow bodies vs. block bodies, etc.).  A
 * comment-stripping regex would be too weak — we explicitly want
 * to flag live code only.
 */
import { describe, it, expect, afterEach, vi } from "vitest"
import { readFileSync, existsSync } from "node:fs"
import path from "node:path"
import { fileURLToPath } from "node:url"
import { parse } from "@babel/parser"
import type { Node } from "@babel/types"
import { render, screen, fireEvent, cleanup } from "@testing-library/react"

import { InputSourcesBar } from "../../panels/editors/_shared"
import RatingStepEditor from "../../panels/editors/RatingStepEditor"
import { GraphProvider } from "../../panels/GraphContext"
import type { SimpleNode, SimpleEdge } from "../../panels/editors/_shared"

// ═══════════════════════════════════════════════════════════════════
//  Path helpers — resolve `frontend/src/` regardless of cwd
// ═══════════════════════════════════════════════════════════════════

const HERE = path.dirname(fileURLToPath(import.meta.url))
// This file lives at `frontend/src/__tests__/hover/`; `src/` is
// two levels up.
const SRC_ROOT = path.resolve(HERE, "../..")
const EDITORS_DIR = path.join(SRC_ROOT, "panels", "editors")

/** The seven editor files in scope. */
const TARGET_FILES = [
  "_shared.tsx",
  "RatingStepEditor.tsx",
  "SinkEditor.tsx",
  "GroupedColumnsTab.tsx",
  "ConstantEditor.tsx",
  "DataSourceEditor.tsx",
  "_DatabricksSelector.tsx",
] as const

type TargetFile = typeof TARGET_FILES[number]

// ═══════════════════════════════════════════════════════════════════
//  AST walker — parse TS/TSX and yield every Node via a visit fn
// ═══════════════════════════════════════════════════════════════════

/**
 * Parse `src` as a TS+JSX module and yield every AST node to
 * `visit`.  Uses `@babel/parser` which is already a transitive
 * dep of the project's Vite toolchain.
 *
 * We use a hand-rolled traversal instead of `@babel/traverse` to
 * avoid pulling in the scope-analysis overhead; all we need is a
 * structural walk (parents -> children).
 */
function walkAst(src: string, visit: (node: Node) => void): void {
  const ast = parse(src, {
    sourceType: "module",
    plugins: ["typescript", "jsx"],
    // Permissive: do not bail out on weird but valid code shapes
    // the editor files may legitimately use (e.g. `as const`,
    // non-null assertions, decorators-in-theory, etc.).
    errorRecovery: true,
  })

  const visited = new WeakSet<object>()
  const walk = (node: unknown): void => {
    if (!node || typeof node !== "object") return
    if (Array.isArray(node)) {
      for (const child of node) walk(child)
      return
    }
    // Duck-type Babel AST nodes: they all have a string `type` field.
    const maybeNode = node as { type?: unknown }
    if (typeof maybeNode.type !== "string") return
    const astNode = node as Node
    if (visited.has(astNode)) return
    visited.add(astNode)
    visit(astNode)
    // Recurse into every enumerable property — simpler than a hand
    // maintained "children" table and robust to new AST node types.
    for (const key of Object.keys(astNode)) {
      if (key === "loc" || key === "start" || key === "end" || key === "range") continue
      walk((astNode as unknown as Record<string, unknown>)[key])
    }
  }
  walk(ast.program)
}

/**
 * Read one of the seven target files as a string.  Fails loudly if
 * the file is missing — a moved target file would otherwise silently
 * pass the "zero mutations" assertion.
 */
function readTarget(name: TargetFile): string {
  const abs = path.join(EDITORS_DIR, name)
  if (!existsSync(abs)) {
    throw new Error(
      `[editorsHover] target file missing: ${abs} — did the dev move or delete it?`,
    )
  }
  return readFileSync(abs, "utf8")
}

/**
 * Locate every MemberExpression that matches `*.currentTarget.style.*`
 * in `src`.  Returns the 1-based line numbers so failure messages
 * point the dev at concrete sites, not just a count.
 *
 * The match is structural: we look for a two-step chain
 *   MemberExpression { object: MemberExpression { property: Identifier("currentTarget") },
 *                      property: Identifier("style") }
 * which catches all of:
 *   e.currentTarget.style.background = ...
 *   e.currentTarget.style.borderColor = ...
 *   evt.currentTarget.style.color = ...
 *   (event as React.MouseEvent).currentTarget.style.opacity = ...
 * regardless of the event parameter name or any intervening casts.
 */
function findCurrentTargetStyleLines(src: string): number[] {
  const lines: number[] = []
  walkAst(src, (node) => {
    if (node.type !== "MemberExpression") return
    // Outer must be `.style` of some object
    if (node.property.type !== "Identifier" || node.property.name !== "style") return
    const obj = node.object
    // Inner must be `*.currentTarget`
    if (obj.type !== "MemberExpression") return
    if (obj.property.type !== "Identifier" || obj.property.name !== "currentTarget") return
    if (node.loc) lines.push(node.loc.start.line)
  })
  return lines
}

/**
 * Find every `import ... from "<path>"` whose source string resolves
 * to `utils/hoverHandlers` (any relative spelling, any extension,
 * `.js` / `.ts` / omitted).  Also flags `import("...")` dynamic
 * imports of the same.
 *
 * Returned entries are objects with the raw source path and the
 * 1-based line number of the import, so the dev can jump to the
 * offender.
 */
function findHoverHandlersImports(
  src: string,
): Array<{ source: string; line: number }> {
  const hits: Array<{ source: string; line: number }> = []
  // Match any relative path that ends in `utils/hoverHandlers` with
  // optional `.ts` / `.tsx` / `.js` extension.
  const sourcePattern = /(^|\/)utils\/hoverHandlers(\.(ts|tsx|js|jsx))?$/

  walkAst(src, (node) => {
    if (node.type === "ImportDeclaration") {
      const s = node.source.value
      if (typeof s === "string" && sourcePattern.test(s)) {
        hits.push({ source: s, line: node.loc?.start.line ?? -1 })
      }
      return
    }
    // Dynamic `import("...")`
    if (
      node.type === "CallExpression" &&
      node.callee.type === "Import" &&
      node.arguments.length === 1 &&
      node.arguments[0].type === "StringLiteral"
    ) {
      const s = node.arguments[0].value
      if (sourcePattern.test(s)) {
        hits.push({ source: s, line: node.loc?.start.line ?? -1 })
      }
    }
    // `require("...")` — belt-and-braces, these files shouldn't use
    // CommonJS but pinning it is cheap.
    if (
      node.type === "CallExpression" &&
      node.callee.type === "Identifier" &&
      node.callee.name === "require" &&
      node.arguments.length === 1 &&
      node.arguments[0].type === "StringLiteral"
    ) {
      const s = node.arguments[0].value
      if (sourcePattern.test(s)) {
        hits.push({ source: s, line: node.loc?.start.line ?? -1 })
      }
    }
  })
  return hits
}

/**
 * Find any identifier specifier in an import that names
 * `hoverHandlers` or `hoverBg`.  This is a belt-and-braces check
 * on top of `findHoverHandlersImports`: even if the dev renames
 * the module path, importing either named export is still a
 * regression of the migration goal.
 */
function findHoverIdentifierImports(
  src: string,
): Array<{ name: string; line: number }> {
  const banned = new Set(["hoverHandlers", "hoverBg"])
  const hits: Array<{ name: string; line: number }> = []

  walkAst(src, (node) => {
    if (node.type !== "ImportDeclaration") return
    for (const spec of node.specifiers) {
      let local: string | null = null
      if (spec.type === "ImportSpecifier") {
        // Prefer the imported name over local alias; a renamed
        // import is still importing the banned export.
        const imported = spec.imported
        local =
          imported.type === "Identifier"
            ? imported.name
            : imported.type === "StringLiteral"
              ? imported.value
              : spec.local.name
      } else if (
        spec.type === "ImportDefaultSpecifier" ||
        spec.type === "ImportNamespaceSpecifier"
      ) {
        local = spec.local.name
      }
      if (local && banned.has(local)) {
        hits.push({ name: local, line: node.loc?.start.line ?? -1 })
      }
    }
  })
  return hits
}

// ═══════════════════════════════════════════════════════════════════
//  Suite scaffolding
// ═══════════════════════════════════════════════════════════════════

afterEach(cleanup)

describe("editor hover migration (AST)", () => {
  // ───────────── Smoke: walker actually reaches the files ─────────

  it("target files all exist and are parseable as TS+JSX", () => {
    // If a filename drifts, every mutation-assertion below silently
    // passes vacuously.  Pin existence + parseability so a rename
    // surfaces immediately.
    for (const name of TARGET_FILES) {
      const abs = path.join(EDITORS_DIR, name)
      expect(
        existsSync(abs),
        `Expected target file to exist: ${abs}`,
      ).toBe(true)
      const src = readFileSync(abs, "utf8")
      // Will throw on parse failure.
      expect(() =>
        parse(src, {
          sourceType: "module",
          plugins: ["typescript", "jsx"],
          errorRecovery: true,
        }),
      ).not.toThrow()
    }
  })

  it("walkAst visits a MemberExpression in a known-good fixture (walker smoke)", () => {
    // Defense in depth: if the walker silently skipped MemberExpressions
    // (e.g. a bug in the recursion), the mutation assertions would
    // pass on any code at all.  Feed it a trivially-true sample.
    const sample = `const x = a.b.c`
    let sawMember = false
    walkAst(sample, (n) => { if (n.type === "MemberExpression") sawMember = true })
    expect(sawMember).toBe(true)
  })

  it("findCurrentTargetStyleLines flags an inline hover mutation (walker smoke)", () => {
    // Defense in depth: verify the query catches the shape we care
    // about on a tiny inline fixture, so a no-match result on a
    // target file is a credible "clean" signal rather than a
    // broken matcher.
    const sample = `
      const f = (e) => { e.currentTarget.style.background = 'red' }
    `
    expect(findCurrentTargetStyleLines(sample).length).toBeGreaterThan(0)

    // Negative control: the query must NOT match unrelated style
    // access patterns like reading `getComputedStyle` or setting
    // style on `e.target` (which is semantically different from
    // `e.currentTarget` and not the target of this migration).
    const noise = `
      const f = (e) => {
        const bg = getComputedStyle(e.currentTarget).background
        e.target.style.background = 'red'
      }
    `
    expect(findCurrentTargetStyleLines(noise)).toEqual([])
  })

  it("findHoverHandlersImports flags an import of utils/hoverHandlers (walker smoke)", () => {
    const sampleRelative = `import { hoverHandlers } from "../../utils/hoverHandlers"`
    expect(findHoverHandlersImports(sampleRelative).length).toBeGreaterThan(0)

    const sampleWithExt = `import { hoverBg } from "../../../utils/hoverHandlers.ts"`
    expect(findHoverHandlersImports(sampleWithExt).length).toBeGreaterThan(0)

    const unrelated = `import { something } from "./utils/other"`
    expect(findHoverHandlersImports(unrelated)).toEqual([])
  })

  // ───────────── Mutation pins, one assertion per target ──────────

  for (const name of TARGET_FILES) {
    it(`${name}: contains no *.currentTarget.style.* writes`, () => {
      const src = readTarget(name)
      const lines = findCurrentTargetStyleLines(src)
      expect(
        lines,
        `${name} still mutates currentTarget.style at line(s): ${lines.join(", ")}. ` +
          `Migrate to a CSS :hover / :focus rule (e.g. .hover-chrome in index.css) ` +
          `or a Tailwind variant instead.`,
      ).toEqual([])
    })
  }

  // ───────────── Import pins, one assertion per target ────────────

  for (const name of TARGET_FILES) {
    it(`${name}: does not import from utils/hoverHandlers`, () => {
      const src = readTarget(name)
      const hits = findHoverHandlersImports(src)
      expect(
        hits,
        `${name} still imports from utils/hoverHandlers at line(s): ` +
          `${hits.map((h) => `${h.line} (${h.source})`).join(", ")}. ` +
          `The factory helpers are the same hazard in centralised form — ` +
          `remove the import and spell the hover in CSS instead.`,
      ).toEqual([])
    })

    it(`${name}: does not import the hoverHandlers/hoverBg identifiers`, () => {
      const src = readTarget(name)
      const hits = findHoverIdentifierImports(src)
      expect(
        hits,
        `${name} imports banned identifier(s) ` +
          `${hits.map((h) => `${h.name} (line ${h.line})`).join(", ")}; ` +
          `both hoverHandlers and hoverBg must not appear in import specifiers.`,
      ).toEqual([])
    })
  }
})

// ═══════════════════════════════════════════════════════════════════
//  Integration — render and hover behaviour must not write style
// ═══════════════════════════════════════════════════════════════════
//
//  These tests are deliberately behavioural rather than structural:
//  they render real components, fire a hover, and verify that the
//  inline style on the target element is unchanged.  If the dev
//  left the `currentTarget.style.*` write in place (and the AST
//  check somehow misses it), these would catch the behaviour
//  regression.
//
//  Conversely, if the dev successfully migrates to CSS
//  `.hover-chrome` / `.hover-bg` / Tailwind `hover:*`, the browser
//  applies the visual entirely through the `:hover` pseudo-class
//  without touching `element.style.*` — the inline style attribute
//  remains empty on those properties.
//
//  jsdom does NOT implement the `:hover` pseudo-class itself, so
//  we cannot assert "after hover, computed background is X".  The
//  reliable invariant is: "after hover, the inline style attribute
//  has no entry for the properties the old code used to write."

// Spy on warnings — the stale inline-style bug surfaces as React
// diff warnings in some versions.  Unrelated to the main assert
// but useful to catch collateral damage.
const consoleError = vi.spyOn(console, "error").mockImplementation(() => {})
afterEach(() => { consoleError.mockClear() })

/** Render InputSourcesBar with a single input source that has a
 *  delete button — the hover-affordance site in `_shared.tsx`.  */
function renderInputSourcesBarWithDelete() {
  return render(
    <InputSourcesBar
      inputSources={[
        { sourceNodeId: "test-source", name: "df", sourceLabel: "Source · df", edgeId: "e1" },
      ]}
      onDeleteInput={() => {}}
    />,
  )
}

/** Render RatingStepEditor wrapped in GraphProvider so hooks resolve. */
function renderRatingStep(
  props: Parameters<typeof RatingStepEditor>[0],
  opts: { allNodes?: SimpleNode[]; edges?: SimpleEdge[] } = {},
) {
  return render(
    <GraphProvider allNodes={opts.allNodes ?? []} edges={opts.edges ?? []}>
      <RatingStepEditor {...props} />
    </GraphProvider>,
  )
}

/** Banding node helper copied from the existing RatingStepEditor
 *  test — mirrors how production data flows into the editor. */
function makeBandingNode(outputColumn: string, assignments: string[]): SimpleNode {
  return {
    id: `banding_${outputColumn}`,
    data: {
      label: `Banding ${outputColumn}`,
      description: "",
      nodeType: "banding",
      config: {
        factors: [{
          banding: "continuous",
          column: outputColumn,
          outputColumn,
          rules: assignments.map((a) => ({
            op1: ">", val1: "0", op2: "", val2: "", assignment: a,
          })),
        }],
      },
    },
  }
}

describe("_shared.tsx hover integration", () => {
  it("InputSourcesBar renders the delete-X button for a wired input", () => {
    renderInputSourcesBarWithDelete()
    const btn = screen.getByTitle("Remove connection from Source · df")
    expect(btn).toBeTruthy()
    // The button owns the icon — smoke check it mounted.
    expect(btn.querySelector("svg")).toBeTruthy()
  })

  it("InputSourcesBar delete-X does NOT write inline style on hover", () => {
    // Historic behaviour (pre-migration): onMouseEnter writes
    //   e.currentTarget.style.color = 'var(--danger)'
    // onMouseLeave writes it back to var(--text-muted).
    //
    // Target behaviour (post-migration): CSS :hover handles the
    // color, inline `style.color` is never touched by JS — so
    // `btn.style.color` stays at its SSR/render-time initial value
    // across both enter and leave.
    renderInputSourcesBarWithDelete()
    const btn = screen.getByTitle("Remove connection from Source · df")

    // Snapshot the initial inline style.color (whatever the
    // render chose — we don't care what it is, only that it
    // doesn't change across hover).
    const initialColor = btn.style.color

    fireEvent.mouseEnter(btn)
    expect(
      btn.style.color,
      `hovering the delete-X flipped inline style.color from ` +
        `"${initialColor}" to "${btn.style.color}". The migration requires ` +
        `the red-on-hover to come from CSS :hover, not a JS onMouseEnter ` +
        `that writes to element.style.color.`,
    ).toBe(initialColor)

    fireEvent.mouseLeave(btn)
    expect(btn.style.color).toBe(initialColor)
  })

  it("InputSourcesBar delete-X click path is still wired (regression guard)", () => {
    // Make sure the migration didn't also accidentally delete the
    // onClick handler — the button must still fire its callback.
    const onDelete = vi.fn()
    render(
      <InputSourcesBar
        inputSources={[{ sourceNodeId: "test-source", name: "df", sourceLabel: "Source · df", edgeId: "e1" }]}
        onDeleteInput={onDelete}
      />,
    )
    const btn = screen.getByTitle("Remove connection from Source · df")
    fireEvent.click(btn)
    expect(onDelete).toHaveBeenCalledWith("e1")
  })
})

describe("RatingStepEditor hover integration", () => {
  // The RatingStepEditor has 3 distinct hover-affordance button
  // shapes, each with its own currentTarget.style mutation pair.
  // One test per shape keeps failure messages localized.

  const DEFAULT = {
    config: {} as Record<string, unknown>,
    onUpdate: () => ({ ok: true as const }),
    inputSources: [],
    onDeleteInput: undefined,
    accentColor: "#14b8a6",
  }

  const BANDING_NODES: SimpleNode[] = [
    makeBandingNode("age_band", ["young", "mid", "old"]),
    makeBandingNode("region", ["north", "south"]),
  ]

  it("add-table '+' button: hover does not mutate borderColor/color", () => {
    renderRatingStep({ ...DEFAULT }, { allNodes: [] })
    // The "+" button is identified by its dashed border — same
    // way the existing test suite finds it (see
    // __tests__/editors/RatingStepEditor.test.tsx).
    const allButtons = screen.getAllByRole("button")
    const addBtn = allButtons.find(
      (b) => b.querySelector("svg") && b.style.border?.includes("dashed"),
    )
    expect(addBtn, "expected to find the add-table '+' button").toBeTruthy()

    const initBorder = addBtn!.style.borderColor
    const initColor = addBtn!.style.color

    fireEvent.mouseEnter(addBtn!)
    expect(
      addBtn!.style.borderColor,
      `add-table '+' hover wrote style.borderColor ("${initBorder}" -> "${addBtn!.style.borderColor}"). ` +
        `Move the border-color change into a CSS :hover rule.`,
    ).toBe(initBorder)
    expect(
      addBtn!.style.color,
      `add-table '+' hover wrote style.color ("${initColor}" -> "${addBtn!.style.color}"). ` +
        `Move the color change into a CSS :hover rule.`,
    ).toBe(initColor)

    fireEvent.mouseLeave(addBtn!)
    expect(addBtn!.style.borderColor).toBe(initBorder)
    expect(addBtn!.style.color).toBe(initColor)
  })

  it("per-factor remove-X button: hover does not mutate color", () => {
    // A factor-row remove-X only renders when a factor is
    // selected.  Seed `config.tables` with one factor so the row
    // is present.
    const config = {
      tables: [{
        name: "T1",
        factors: ["age_band"],
        outputColumn: "af",
        defaultValue: "1.0",
        entries: [],
      }],
    }
    renderRatingStep(
      { ...DEFAULT, config },
      { allNodes: BANDING_NODES },
    )

    // Inside the factor-row container (a div with a <select>),
    // find the sibling button that contains the X icon.
    const buttons = screen.getAllByRole("button")
    // The factor remove-X button is small (p-1 padding), has
    // an SVG, and is NOT the add-table '+' (which has a dashed
    // border).  Filter defensively.
    const removeBtn = buttons.find((b) => {
      if (!b.querySelector("svg")) return false
      if (b.style.border?.includes("dashed")) return false
      // Must not be the "↻ Rebuild" button, which has text content
      // starting with "↻".
      if ((b.textContent || "").includes("↻")) return false
      // Must not be the formula "Remove table" button — those only
      // appear when >1 tables.
      if (b.getAttribute("aria-label") === "Remove table") return false
      // Size filter: remove-X buttons have no text content.
      return (b.textContent || "").trim() === ""
    })
    expect(
      removeBtn,
      "expected to find the per-factor remove-X button after seeding factors=['age_band']",
    ).toBeTruthy()

    const initColor = removeBtn!.style.color
    fireEvent.mouseEnter(removeBtn!)
    expect(
      removeBtn!.style.color,
      `factor remove-X hover wrote style.color ("${initColor}" -> "${removeBtn!.style.color}"). ` +
        `Move the red-on-hover into a CSS :hover rule.`,
    ).toBe(initColor)
    fireEvent.mouseLeave(removeBtn!)
    expect(removeBtn!.style.color).toBe(initColor)
  })

  it("'↻ Rebuild' button: hover does not mutate borderColor/color", () => {
    // Rebuild button renders only when factorCount > 0.
    const config = {
      tables: [{
        name: "T1",
        factors: ["age_band"],
        outputColumn: "af",
        defaultValue: "1.0",
        entries: [],
      }],
    }
    renderRatingStep(
      { ...DEFAULT, config },
      { allNodes: BANDING_NODES },
    )

    const rebuildBtn = screen.getByText(/Rebuild from factor levels/).closest("button")
    expect(rebuildBtn, "expected to find the '↻ Rebuild' button").toBeTruthy()

    const initBorder = rebuildBtn!.style.borderColor
    const initColor = rebuildBtn!.style.color

    fireEvent.mouseEnter(rebuildBtn!)
    expect(
      rebuildBtn!.style.borderColor,
      `Rebuild hover wrote style.borderColor ("${initBorder}" -> "${rebuildBtn!.style.borderColor}"). ` +
        `Move the border-color change into a CSS :hover rule.`,
    ).toBe(initBorder)
    expect(
      rebuildBtn!.style.color,
      `Rebuild hover wrote style.color ("${initColor}" -> "${rebuildBtn!.style.color}"). ` +
        `Move the color change into a CSS :hover rule.`,
    ).toBe(initColor)

    fireEvent.mouseLeave(rebuildBtn!)
    expect(rebuildBtn!.style.borderColor).toBe(initBorder)
    expect(rebuildBtn!.style.color).toBe(initColor)
  })
})
