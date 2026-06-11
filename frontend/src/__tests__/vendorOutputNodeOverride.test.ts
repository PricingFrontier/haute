/**
 * Vendor-override contract: `.react-flow__node-output`.
 *
 * Bug class
 * ---------
 * React Flow derives a wrapper class from the node's `type`:
 * `react-flow__node-<type>`.  Our OUTPUT (Quote Response) node registers
 * under the key `"output"` (`src/utils/nodeTypes.ts`), which collides with
 * React Flow's *built-in* node type names (`input` / `default` / `output` /
 * `group`).  The vendor stylesheet (`@xyflow/react/dist/style.css`) styles
 * those built-ins — centred text, a fixed 150px width, padding, border,
 * background — and all of it leaks onto our custom PipelineNode wrapper.
 * OUTPUT is the only one of our type keys that collides.
 *
 * The override block in `src/index.css` (`.react-flow__node-output { ... }`)
 * must therefore neutralise EVERY property the vendor sets on the built-in
 * node types, each with `!important`.  A partial override is exactly how the
 * bug ships: the original fix (commit 6c503df, "fix: left-align Quote Output
 * node title") covered `text-align` only — and was then stranded on an
 * unmerged branch, so the regression went unnoticed.  This suite is the
 * regression lock.
 *
 * jsdom cannot apply real stylesheets, so a mounted-component assertion
 * cannot see the vendor cascade.  The honest jsdom-able boundary is the
 * stylesheet text itself: parse the vendor rule, parse our override, and
 * assert coverage.  A real-browser computed-style check lives in the
 * Playwright smoke tier (`e2e/smoke.spec.ts`).
 *
 * When this test fails
 * --------------------
 * - "vendor property not neutralised" — the `@xyflow/react` pin was bumped
 *   and the new version sets a property on its built-in node types that our
 *   `.react-flow__node-output` block does not override.  Add a matching
 *   `<property>: <neutral value> !important;` line to the block in
 *   `src/index.css`.
 * - "exact-value pin" — someone edited the override block.  `text-align:
 *   start !important` and `width: auto !important` are load-bearing: they
 *   are the two declarations whose absence centred the Quote Response card
 *   and lied to React Flow's node measurement (150px wrapper vs 240px card).
 * - "named-absence" — another selector scoped to `.react-flow__node-output`
 *   re-introduced centring or a fixed width.  Remove it; the wrapper must
 *   shrink-wrap the inner card like every non-colliding node type.
 */
import { describe, it, expect } from "vitest"
import { readFileSync } from "node:fs"
import path from "node:path"
import { fileURLToPath } from "node:url"

// ---------------------------------------------------------------------------
// File locations
// ---------------------------------------------------------------------------

const HERE = path.dirname(fileURLToPath(import.meta.url))
const APP_CSS_PATH = path.resolve(HERE, "..", "index.css")
const VENDOR_CSS_PATH = path.resolve(
  HERE,
  "..",
  "..",
  "node_modules",
  "@xyflow",
  "react",
  "dist",
  "style.css",
)

const APP_CSS = readFileSync(APP_CSS_PATH, "utf8")
const VENDOR_CSS = readFileSync(VENDOR_CSS_PATH, "utf8")

const TARGET_SELECTOR = ".react-flow__node-output"

// ---------------------------------------------------------------------------
// Minimal CSS parsing — leaf rule blocks and their declarations
// ---------------------------------------------------------------------------

interface Declaration {
  /** Property name, lower-cased, e.g. `"text-align"`. */
  property: string
  /** Value with any `!important` suffix stripped, trimmed. */
  value: string
  /** Whether the declaration carried `!important`. */
  important: boolean
}

interface RuleBlock {
  /** Comma-split, trimmed selector list, e.g. `[".a", ".b:hover"]`. */
  selectors: string[]
  declarations: Declaration[]
}

function stripComments(css: string): string {
  return css.replace(/\/\*[\s\S]*?\*\//g, "")
}

/**
 * Enumerate every leaf rule block (`selector list { declarations }`) in the
 * stylesheet.  The `[^{}]` selector/body classes mean nested constructs
 * (`@media`, `@supports`) contribute their inner leaf rules, not the at-rule
 * shell — which is exactly what the contract needs, since the cascade only
 * applies declarations from leaf blocks.
 */
function parseLeafBlocks(css: string): RuleBlock[] {
  const blocks: RuleBlock[] = []
  const blockRe = /([^{}]+)\{([^{}]*)\}/g
  let m: RegExpExecArray | null
  while ((m = blockRe.exec(css)) !== null) {
    const selectors = m[1]
      .split(",")
      .map((s) => s.trim())
      .filter((s) => s.length > 0)
    const declarations: Declaration[] = []
    for (const chunk of m[2].split(";")) {
      const decl = /^\s*([-a-zA-Z]+)\s*:\s*([\s\S]+?)\s*$/.exec(chunk)
      if (!decl) continue
      const important = /!important$/i.test(decl[2])
      declarations.push({
        property: decl[1].toLowerCase(),
        value: decl[2].replace(/\s*!important$/i, "").trim(),
        important,
      })
    }
    blocks.push({ selectors, declarations })
  }
  return blocks
}

/** Blocks whose selector list contains `selector` as a bare entry (exact match). */
function blocksWithBareSelector(blocks: RuleBlock[], selector: string): RuleBlock[] {
  return blocks.filter((b) => b.selectors.includes(selector))
}

/** Blocks whose selector list mentions `selector` anywhere (compound, descendant, ...). */
function blocksMentioningSelector(blocks: RuleBlock[], selector: string): RuleBlock[] {
  return blocks.filter((b) => b.selectors.some((s) => s.includes(selector)))
}

// ---------------------------------------------------------------------------
// The two stylesheets' rule sets for the target selector
// ---------------------------------------------------------------------------

const vendorBlocks = parseLeafBlocks(stripComments(VENDOR_CSS))
const appBlocks = parseLeafBlocks(stripComments(APP_CSS))

/**
 * The vendor rule for the *bare* built-in selector — the one that leaks the
 * centred 150px card onto our wrapper.  (Compound vendor selectors like
 * `.react-flow__node-output.selectable:hover` set only box-shadow, which the
 * override block neutralises by name; they are exercised indirectly via the
 * coverage check whenever the bare block also sets the property.)
 */
const vendorBareBlocks = blocksWithBareSelector(vendorBlocks, TARGET_SELECTOR)

/** Every property name the vendor sets on the bare built-in selector. */
const vendorProperties = [
  ...new Set(vendorBareBlocks.flatMap((b) => b.declarations.map((d) => d.property))),
].sort()

/** Our override blocks: the bare-selector block(s) in index.css. */
const overrideBlocks = blocksWithBareSelector(appBlocks, TARGET_SELECTOR)

/**
 * Does an `!important` declaration of `overrideProp` neutralise the vendor's
 * `vendorProp`?  Shorthand coverage: `background` covers `background-*`;
 * `border` covers `border-*` longhands (width/style/color sides) but NOT
 * `border-radius`, which the `border` shorthand does not reset.
 */
function covers(overrideProp: string, vendorProp: string): boolean {
  if (overrideProp === vendorProp) return true
  if (overrideProp === "background" && vendorProp.startsWith("background-")) return true
  if (
    overrideProp === "border" &&
    vendorProp.startsWith("border-") &&
    !vendorProp.startsWith("border-radius")
  ) {
    return true
  }
  return false
}

const importantOverrideProps = overrideBlocks.flatMap((b) =>
  b.declarations.filter((d) => d.important).map((d) => d.property),
)

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("index.css — vendor override contract for .react-flow__node-output", () => {
  it("the vendor stylesheet still has a bare .react-flow__node-output rule (fixture sanity)", () => {
    // If this fails after an @xyflow/react upgrade, the built-in node-type
    // styling moved — re-locate it and re-anchor this suite before trusting
    // any green result below.
    expect(vendorBareBlocks.length).toBeGreaterThan(0)
    expect(vendorProperties.length).toBeGreaterThan(0)
    // The two declarations this contract exists to defeat:
    expect(vendorProperties).toContain("text-align")
    expect(vendorProperties).toContain("width")
  })

  it("index.css has exactly one bare .react-flow__node-output override block", () => {
    expect(overrideBlocks.length).toBe(1)
  })

  it("every vendor property on the built-in 'output' type is neutralised with !important", () => {
    const uncovered = vendorProperties.filter(
      (vendorProp) => !importantOverrideProps.some((ours) => covers(ours, vendorProp)),
    )
    if (uncovered.length > 0) {
      throw new Error(
        `Vendor CSS leak onto the reserved built-in type name "output": ` +
          `@xyflow/react styles .react-flow__node-output with [${vendorProperties.join(", ")}], ` +
          `but the index.css override block does not neutralise (with !important): ` +
          `[${uncovered.join(", ")}]. Our OUTPUT node's type key "output" collides with ` +
          `React Flow's built-in node type, so each of these leaks onto the Quote Response ` +
          `card's wrapper. Previous regression: fix 6c503df (text-align only) was stranded ` +
          `on an unmerged branch. Add the missing !important declarations to the ` +
          `.react-flow__node-output block in src/index.css.`,
      )
    }
    expect(uncovered).toEqual([])
  })

  it("pins text-align: start !important (the centred-title half of the bug)", () => {
    const decl = overrideBlocks[0].declarations.find((d) => d.property === "text-align")
    expect(decl, "override block has no text-align declaration").toBeDefined()
    expect(decl?.value).toBe("start")
    expect(decl?.important).toBe(true)
  })

  it("pins width: auto !important (the 150px measured-geometry half of the bug)", () => {
    const decl = overrideBlocks[0].declarations.find((d) => d.property === "width")
    expect(decl, "override block has no width declaration").toBeDefined()
    expect(decl?.value).toBe("auto")
    expect(decl?.important).toBe(true)
  })

  it("no other index.css selector re-introduces centring or a fixed width on the wrapper", () => {
    // Named-absence guard: the override block must be the ONLY rule scoped
    // to .react-flow__node-output that touches text-align or width — and a
    // future rule that centres or fixes the wrapper width re-opens the bug
    // even if the override block itself stays correct.
    const others = blocksMentioningSelector(appBlocks, TARGET_SELECTOR).filter(
      (b) => b !== overrideBlocks[0],
    )
    const offenders: string[] = []
    for (const block of others) {
      for (const d of block.declarations) {
        if (d.property === "text-align" && d.value !== "start" && d.value !== "inherit") {
          offenders.push(`${block.selectors.join(", ")} { text-align: ${d.value} }`)
        }
        if (d.property === "width" && d.value !== "auto" && d.value !== "inherit") {
          offenders.push(`${block.selectors.join(", ")} { width: ${d.value} }`)
        }
      }
    }
    expect(offenders).toEqual([])
  })
})
