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
 * This suite pins three properties:
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
 *   3. Every fallback-less `var(--name)` in live `.ts`/`.tsx` source
 *      (inline `style={{ ... }}` objects, CSS-in-JS strings) resolves to
 *      a token declared in index.css.  A dangling reference is worse here
 *      than in CSS: the declaration is invalid at computed-value time, so
 *      the property silently becomes its initial value — `transparent`
 *      backgrounds, `currentColor` borders — with no build error and no
 *      console warning.  Tokens provided at runtime by something other
 *      than index.css (caller-set inline properties, Tailwind's @theme
 *      layer) must carry an inline fallback, same as rule 2.
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
import { readFileSync, readdirSync, statSync } from "node:fs"
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
    // A name truncated by a template interpolation — e.g. the dynamic
    // `var(--diff-${diffStatus})` in PipelineNode.tsx — is a runtime-
    // composed token reference: the full name doesn't exist statically,
    // so declared-ness can't be judged here. Skip it (rules 1/2 still
    // pin the token family's declarations in index.css itself).
    // Anchor at the end of the captured NAME, not openRe.lastIndex —
    // lastIndex sits past the regex's trailing \s* run, and a COMPLETE
    // static name merely followed by whitespace + `${...}` (e.g.
    // `var(--bg ${x})`) must still be reported. m[0]'s only trailing
    // whitespace is that \s* run, so trimEnd() lands exactly at the
    // name end.
    if (css.startsWith("${", m.index + m[0].trimEnd().length)) continue
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
// TS/TSX source scanner (contract property 3)
// ---------------------------------------------------------------------------

const SRC_ROOT = path.resolve(HERE, "..")

/**
 * Enumerate live `.ts`/`.tsx` source files under `frontend/src`, skipping
 * test code (`__tests__/`, `*.test.*`, `*.spec.*`) — test fixtures use
 * deliberately-fake var() names as examples.
 */
function collectSourceFiles(dir: string): string[] {
  const out: string[] = []
  for (const entry of readdirSync(dir)) {
    const full = path.join(dir, entry)
    if (statSync(full).isDirectory()) {
      if (entry === "__tests__") continue
      out.push(...collectSourceFiles(full))
      continue
    }
    if (!/\.(ts|tsx)$/.test(entry)) continue
    if (/\.(test|spec)\.(ts|tsx)$/.test(entry)) continue
    out.push(full)
  }
  return out
}

/**
 * Blank out block comments and whole-line `//` comments so var() examples
 * in doc comments aren't flagged.  Comments are replaced with whitespace
 * (newlines preserved) so reported line numbers stay accurate.  Trailing
 * `//` comments are left in place because the pattern can't be
 * distinguished from `://` inside string URLs — erring on the side of
 * scanning too much, which only ever makes the contract stricter.
 *
 * The block-comment pass is STRING-AWARE: a `/*` inside a '…', "…" or
 * `…` literal (e.g. a glob like "src/*") does not open a comment span.
 * A naive regex pass blanked real code from such a string to the next
 * comment terminator in the file — the bad direction for this contract,
 * since a dangling var(--...) in the blanked span went unreported
 * (false negative). Pinned by the canary test below.
 */
function stripComments(text: string): string {
  let out = ""
  let i = 0
  // Current context: inside a block comment, inside a string/template
  // literal (the quote char), or plain code (null).
  let mode: '"' | "'" | "`" | "/*" | null = null
  while (i < text.length) {
    const ch = text[i]
    if (mode === "/*") {
      if (ch === "*" && text[i + 1] === "/") {
        out += "  "
        i += 2
        mode = null
        continue
      }
      out += ch === "\n" ? "\n" : " "
      i++
      continue
    }
    if (mode !== null) {
      // Inside a string/template literal — copy verbatim, honouring
      // escapes so an escaped quote doesn't end the literal.
      if (ch === "\\") {
        out += text.slice(i, i + 2)
        i += 2
        continue
      }
      if (ch === mode) mode = null
      out += ch
      i++
      continue
    }
    if (ch === "/" && text[i + 1] === "*") {
      out += "  "
      i += 2
      mode = "/*"
      continue
    }
    if (ch === '"' || ch === "'" || ch === "`") {
      mode = ch
      out += ch
      i++
      continue
    }
    out += ch
    i++
  }
  // Whole-line `//` comments — same regex (and same `://` tradeoff) as
  // before; a `//` line inside a template literal remains the one known
  // over-strip, unchanged.
  return out.replace(/^[ \t]*\/\/.*$/gm, "")
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

describe("ts/tsx source — design-token contract", () => {
  it("every fallback-less var(--name) in live source resolves to an index.css token", () => {
    // Regression guard for the bug class where a component references a
    // token that was never declared (or was renamed away): the style is
    // invalid at computed-value time and the property silently falls back
    // to its initial value — e.g. `background` → transparent or
    // `border-color` → currentColor.
    const declared = findTokenDeclarations(CSS)
    const offenders: string[] = []
    const files = collectSourceFiles(SRC_ROOT)
    for (const file of files) {
      const text = stripComments(readFileSync(file, "utf8"))
      const dangling = findVarRefs(text).filter((r) => !r.hasFallback && !declared.has(r.name))
      for (const d of dangling) {
        const rel = path.relative(SRC_ROOT, file).split(path.sep).join(path.posix.sep)
        offenders.push(`  ${rel}:${d.line}  var(${d.name})  — no index.css declaration and no inline fallback`)
      }
    }
    if (offenders.length > 0) {
      throw new Error(
        `Found ${offenders.length} dangling var(--...) reference(s) in ts/tsx source. ` +
          `Declare the token in index.css :root, or add an inline fallback ` +
          `(var(--name, <fallback>)) if the property is caller-supplied or provided by Tailwind:\n` +
          offenders.join("\n"),
      )
    }
    expect(offenders).toEqual([])
  })

  it("skips template-interpolated token names (dynamic refs like var(--diff-${status}))", () => {
    // Regression: the scanner used to truncate `var(--diff-${diffStatus})`
    // to the never-declared name `--diff-` and flag it as dangling.
    const refs = findVarRefs('a { color: var(--diff-${status}); background: var(--real); }')
    expect(refs.map((r) => r.name)).toEqual(["--real"])
    // …but a COMPLETE static name merely followed by whitespace + an
    // interpolation is still reported — only a name TRUNCATED by `${`
    // is dynamic.
    const spaced = findVarRefs('a { color: var(--bg ${x}); }')
    expect(spaced.map((r) => r.name)).toEqual(["--bg"])
  })

  it("stripComments: a '/*' inside a string literal must not blank following code", () => {
    // A string-unaware pass would open a comment span at the `/*` in
    // "src/*" and blank everything up to the next real `*/` — hiding
    // any dangling var(--...) reference in between (a false NEGATIVE,
    // the bad direction for this contract).
    const src = 'const g = "src/*"\nconst c = "var(--canary)"\n/* real comment */\n'
    expect(stripComments(src)).toContain("--canary")
  })

  it("scans a non-trivial source tree (smoke — prevents silent scope loss)", () => {
    // If the walker's filters ever accidentally exclude everything (e.g. a
    // bad rename of SRC_ROOT), the contract test above would pass vacuously.
    const files = collectSourceFiles(SRC_ROOT)
    expect(files.length).toBeGreaterThanOrEqual(50)
    expect(files.some((f) => f.endsWith(".tsx"))).toBe(true)
    // ...and prove the var() scanner itself still extracts references. A
    // broken findVarRefs regex (one that matched nothing) would also make
    // the contract test above pass vacuously — zero refs found, zero
    // offenders — so assert the live tree yields a non-trivial ref count.
    const totalRefs = files.reduce(
      (n, f) => n + findVarRefs(stripComments(readFileSync(f, "utf8"))).length,
      0,
    )
    expect(totalRefs).toBeGreaterThanOrEqual(50)
  })
})
