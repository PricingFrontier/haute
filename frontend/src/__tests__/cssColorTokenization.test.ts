/**
 * CSS color tokenization contract.
 *
 * The project convention: every colour in `frontend/src/index.css` lives as
 * a CSS custom property (design token) inside the single `:root { ... }`
 * declaration block at the top of the file.  Rule bodies (`.foo { color: ... }`)
 * must only reference colours via `var(--token)` — never as inline hex
 * literals.
 *
 * Why this matters
 * ----------------
 * Inline hex literals in rule bodies:
 *   - Make theming impossible (no single place to swap the palette).
 *   - Duplicate the same colour in a dozen places, so "brand red" drifts
 *     across `#ef4444`, `#ee4444`, `#ef4445` etc.
 *   - Defeat light/dark-mode overrides which work by re-declaring tokens.
 *
 * This suite pins two properties:
 *
 *   1. NO hex literals (`#RGB`, `#RRGGBB`, `#RRGGBBAA`) appear outside
 *      the `:root` block.  Inside `:root` they are expected — that's where
 *      the token palette is defined.
 *
 *   2. Every `var(--name)` reference in the file resolves to a matching
 *      `--name:` declaration somewhere in the same file (i.e. no dangling
 *      references that would silently cascade to the initial value).
 *      Exception: parameterised passes-through like
 *        `var(--node-accent, var(--accent))`
 *      are allowed without a :root declaration when they carry an inline
 *      fallback — those are caller-supplied properties whose fallback is
 *      the real token.  We only flag var() calls with no fallback.
 *
 * When this test fails
 * --------------------
 * - "hex literal outside :root" — move the colour to a new token inside
 *   `:root { ... }` and reference it via `var(--token)` at the call site.
 * - "dangling var(--name) reference" — declare `--name: <value>;` in the
 *   `:root` block, or add an inline fallback at the call site if the
 *   property is deliberately caller-supplied.
 */
import { describe, it, expect } from "vitest"
import { readFileSync } from "node:fs"
import path from "node:path"
import { fileURLToPath } from "node:url"

// ---------------------------------------------------------------------------
// File location
// ---------------------------------------------------------------------------

const HERE = path.dirname(fileURLToPath(import.meta.url))
const CSS_PATH = path.resolve(HERE, "..", "index.css")
const CSS = readFileSync(CSS_PATH, "utf8")

// ---------------------------------------------------------------------------
// Parser — find the :root { ... } block (only looks at the first one, which
// is canonically the single source of truth for tokens in this project).
// ---------------------------------------------------------------------------

/**
 * Return the half-open `[start, end)` byte range of the first `:root { ... }`
 * block's contents (i.e. the range between the `{` and the matching `}`).
 *
 * Uses brace-depth tracking rather than a regex so nested braces (e.g.
 * keyframes inside :root, if ever added) are handled correctly.  Strings
 * and comments are not stripped because the canonical :root block does
 * not contain either — if it ever does, the regex-free approach still
 * errs in the safe direction (treating a `}` inside a string as a close
 * brace is harmless for the contract, which only cares about the OUTER
 * extent of :root).
 */
function findRootBlockRange(css: string): { start: number; end: number } {
  const match = /:root\s*\{/.exec(css)
  if (!match) {
    throw new Error("index.css does not contain a :root { ... } block")
  }
  const openBrace = match.index + match[0].length - 1
  // Walk from just past the `{` and find the matching `}`.
  let depth = 1
  for (let i = openBrace + 1; i < css.length; i++) {
    const ch = css[i]
    if (ch === "{") depth++
    else if (ch === "}") {
      depth--
      if (depth === 0) {
        return { start: openBrace + 1, end: i }
      }
    }
  }
  throw new Error("index.css :root block is missing its closing brace")
}

// ---------------------------------------------------------------------------
// Hex literal scanner
// ---------------------------------------------------------------------------

/**
 * Matches 3-, 4-, 6-, or 8-digit hex literals with a leading `#`, honouring
 * the full CSS spec.  The trailing look-ahead rejects longer alphanumeric
 * runs so we don't misread `#1234567890` as a valid `#12345678` followed by
 * extra digits.
 */
const HEX_RE = /#[0-9a-fA-F]{3,8}(?![0-9a-fA-F])/g

interface HexHit {
  /** Line number (1-based). */
  line: number
  /** The matched literal, e.g. `"#ef4444"`. */
  literal: string
  /** Full line text (trimmed), for reporting. */
  lineText: string
}

/**
 * Enumerate every hex literal in `css` that lies outside the given
 * `[rootStart, rootEnd)` range.  Returns an ordered list for stable
 * test failure messages.
 */
function findHexOutsideRoot(css: string, rootStart: number, rootEnd: number): HexHit[] {
  const hits: HexHit[] = []
  const lines = css.split(/\r?\n/)
  // Precompute line starts (1-based) to translate offsets -> line numbers.
  const lineStarts: number[] = [0]
  for (let i = 0; i < css.length; i++) {
    if (css[i] === "\n") lineStarts.push(i + 1)
  }

  HEX_RE.lastIndex = 0
  let m: RegExpExecArray | null
  while ((m = HEX_RE.exec(css)) !== null) {
    const offset = m.index
    if (offset >= rootStart && offset < rootEnd) continue
    // Find the line by binary search on lineStarts.
    let lo = 0
    let hi = lineStarts.length - 1
    while (lo < hi) {
      const mid = (lo + hi + 1) >> 1
      if (lineStarts[mid] <= offset) lo = mid
      else hi = mid - 1
    }
    const lineIdx = lo
    hits.push({
      line: lineIdx + 1,
      literal: m[0],
      lineText: lines[lineIdx].trim(),
    })
  }
  return hits
}

// ---------------------------------------------------------------------------
// var(--...) scanner
// ---------------------------------------------------------------------------

/**
 * A single `var(--name[, fallback])` reference.  `hasFallback` tells us
 * whether the site provides an inline default — which is required for
 * parameterised custom properties (e.g. `var(--node-accent, var(--accent))`)
 * that are set inline by JSX and therefore cannot be declared in :root.
 */
interface VarRef {
  name: string
  hasFallback: boolean
  line: number
}

/**
 * Matches `var(--name)` and `var(--name, fallback)` — capturing the name
 * and whether a fallback (comma) follows.  The fallback expression is
 * parsed balanced via lookahead; anything after a matched `--name` up to
 * the NEXT `,` or `)` at the same paren depth counts as the fallback.
 *
 * For our purposes we only need to know whether a fallback EXISTS, not
 * its exact value — so a simple look-past-the-name approach is enough:
 * consume everything up to the matching `)` of this var(...) call and
 * check whether a `,` appears at depth 0 within that span.
 */
function findVarRefs(css: string): VarRef[] {
  const refs: VarRef[] = []
  const lineStarts: number[] = [0]
  for (let i = 0; i < css.length; i++) {
    if (css[i] === "\n") lineStarts.push(i + 1)
  }

  const openRe = /\bvar\s*\(\s*(--[a-zA-Z_][a-zA-Z0-9_-]*)\s*/g
  let m: RegExpExecArray | null
  while ((m = openRe.exec(css)) !== null) {
    const name = m[1]
    const offset = m.index
    // Walk forward from the end of the match to find the matching `)`
    // and note whether we pass a comma at depth 0.
    let depth = 1
    let hasFallback = false
    for (let i = openRe.lastIndex; i < css.length; i++) {
      const ch = css[i]
      if (ch === "(") depth++
      else if (ch === ")") {
        depth--
        if (depth === 0) break
      } else if (ch === "," && depth === 1) {
        hasFallback = true
      }
    }
    // Translate offset -> line.
    let lo = 0
    let hi = lineStarts.length - 1
    while (lo < hi) {
      const mid = (lo + hi + 1) >> 1
      if (lineStarts[mid] <= offset) lo = mid
      else hi = mid - 1
    }
    refs.push({ name, hasFallback, line: lo + 1 })
  }
  return refs
}

/**
 * Find every `--name:` declaration in the CSS file (restricted to the
 * :root block in practice, but we scan the whole file so tokens declared
 * inside e.g. `[data-theme="dark"]` blocks would also count).  Returns
 * a Set for O(1) membership checks.
 */
function findTokenDeclarations(css: string): Set<string> {
  const decls = new Set<string>()
  const re = /(^|[\s{;])(--[a-zA-Z_][a-zA-Z0-9_-]*)\s*:/g
  let m: RegExpExecArray | null
  while ((m = re.exec(css)) !== null) {
    decls.add(m[2])
  }
  return decls
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("index.css — design-token contract", () => {
  it("contains a :root { ... } block", () => {
    // Sanity: the rest of the suite assumes this is present.
    expect(() => findRootBlockRange(CSS)).not.toThrow()
  })

  it("no hex literals appear outside the :root block", () => {
    const { start, end } = findRootBlockRange(CSS)
    const hits = findHexOutsideRoot(CSS, start, end)
    if (hits.length > 0) {
      const summary = hits
        .map((h) => `  index.css:${h.line}  ${h.literal}   // ${h.lineText}`)
        .join("\n")
      throw new Error(
        `Found ${hits.length} hex literal(s) outside :root — move each to a CSS custom property and reference via var(--name):\n${summary}`,
      )
    }
    expect(hits).toEqual([])
  })

  it("every var(--name) reference resolves to a :root declaration (or has an inline fallback)", () => {
    const refs = findVarRefs(CSS)
    const declared = findTokenDeclarations(CSS)
    const dangling = refs.filter((r) => !declared.has(r.name) && !r.hasFallback)
    if (dangling.length > 0) {
      const summary = dangling
        .map((d) => `  index.css:${d.line}  var(${d.name})  — no :root declaration and no inline fallback`)
        .join("\n")
      throw new Error(
        `Found ${dangling.length} dangling var(--...) reference(s). Declare the token in :root or provide an inline fallback:\n${summary}`,
      )
    }
    expect(dangling).toEqual([])
  })

  it("a non-trivial palette is declared (smoke — prevents accidental :root deletion)", () => {
    // If someone deletes the :root declarations in a refactor, the other
    // tests would still pass (no hex outside :root, no dangling refs because
    // there are no refs either).  This smoke test pins that a real palette
    // exists — the exact size is not meaningful, only that the file hasn't
    // been gutted.
    const declared = findTokenDeclarations(CSS)
    // The core token set at time of writing has ~30 entries; require at
    // least 10 so a small refactor doesn't trip this but a deletion does.
    expect(declared.size).toBeGreaterThanOrEqual(10)
  })

  it("the core palette tokens are declared (regression guard)", () => {
    // Pin the names of tokens that the rest of the UI depends on.  If one
    // of these is renamed/removed without a codemod of the call sites, the
    // UI silently falls back to the CSS initial value — this test catches
    // that before it ships.
    const declared = findTokenDeclarations(CSS)
    const required = [
      "--bg-base",
      "--bg-canvas",
      "--bg-elevated",
      "--bg-panel",
      "--chrome",
      "--chrome-hover",
      "--chrome-border",
      "--text-primary",
      "--text-secondary",
      "--text-muted",
      "--accent",
      "--accent-soft",
      "--danger",
      "--danger-hover",
      "--danger-solid",
      "--border",
    ]
    const missing = required.filter((t) => !declared.has(t))
    expect(missing).toEqual([])
  })
})
