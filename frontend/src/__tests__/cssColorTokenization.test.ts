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
 * This suite pins four properties:
 *
 *   1. NO hex literals (`#RGB`, `#RRGGBB`, `#RRGGBBAA`) appear outside
 *      the `:root` block.  Inside `:root` they are expected — that's where
 *      the token palette is defined.
 *
 *   2. Every `var(--name)` reference in the file resolves to a matching
 *      `--name:` declaration — in this file, or an entry in the
 *      TAILWIND_PROVIDED_TOKENS list of Tailwind theme tokens the app
 *      deliberately relies on.  Tailwind v4 TREE-SHAKES its theme: a
 *      token reaches the emitted `@layer theme { :root ... }` only while
 *      some generated utility uses it, so "declared in theme.css" does
 *      NOT mean "resolves at runtime" — each listed token therefore
 *      carries a usage guard pinning the utility that keeps it emitted.
 *      No dangling references that would silently cascade to the initial
 *      value.
 *      Exception: deliberately caller-supplied properties like
 *        `var(--node-accent, var(--accent))`
 *      — set at runtime via inline `style={{ ... }}` on JSX, so they
 *      cannot be declared in :root.  These are enumerated explicitly in
 *      PARAMETERISED_TOKENS below and must carry an inline fallback.
 *      An undeclared token that is NOT on that list is flagged even when
 *      it carries a fallback: a fallback-shaped exemption is exactly how
 *      a typo'd or never-declared token hides (the component renders via
 *      the fallback and nobody notices the token is dead).
 *
 *   3. Every `var(--name)` in live `.ts`/`.tsx` source (inline
 *      `style={{ ... }}` objects, CSS-in-JS strings) resolves to a token
 *      declared in index.css or listed in TAILWIND_PROVIDED_TOKENS,
 *      under the same PARAMETERISED_TOKENS exception as rule 2.  A dangling reference
 *      is worse here than in CSS: the declaration is invalid at
 *      computed-value time, so the property silently becomes its initial
 *      value — `transparent` backgrounds, `currentColor` borders — with
 *      no build error and no console warning.  Convention (review-policed,
 *      not gate-policed): components prefer the index.css role token that
 *      aliases a Tailwind token (e.g. --font-data) over the raw Tailwind
 *      name — see the adoption pin below.
 *
 *   4. index.css must NOT redeclare a Tailwind theme token in its plain
 *      (unlayered) `:root` block.  Unlayered declarations outrank every
 *      cascade layer, so such a shadow silently re-themes ALL utility
 *      classes built on the token (e.g. shadowing `--font-mono` retunes
 *      every `.font-mono` call site and base `code`/`pre` styling, not
 *      just the intended component).  Deliberate overrides belong in an
 *      `@theme { ... }` block, which Tailwind ingests properly.
 *
 * When this test fails
 * --------------------
 * - "hex literal outside :root" — move the colour to a new token inside
 *   `:root { ... }` and reference it via `var(--token)` at the call site.
 * - "dangling var(--name) reference" — declare `--name: <value>;` in the
 *   `:root` block.  If the token is a Tailwind theme token the app
 *   genuinely uses, add it to TAILWIND_PROVIDED_TOKENS with the utility
 *   regex that keeps it emitted — do NOT declare it in :root (see the
 *   shadow rule below) — and in ts/tsx prefer the index.css role token
 *   that aliases it (e.g. --font-data).  Only if the property is genuinely
 *   caller-supplied (some JSX sets it via inline `style={{ ... }}`) add
 *   it to PARAMETERISED_TOKENS *and* give every reference an inline
 *   fallback.
 * - "index.css shadows a Tailwind theme token" — either rename your token,
 *   or if the override is deliberate move it into an `@theme { ... }`
 *   block; the shadow guard exempts @theme blocks automatically.
 */
import { describe, it, expect } from "vitest"
import { readFileSync, readdirSync, statSync } from "node:fs"
import { createRequire } from "node:module"
import path from "node:path"
import { fileURLToPath } from "node:url"

// ---------------------------------------------------------------------------
// File location
// ---------------------------------------------------------------------------

const HERE = path.dirname(fileURLToPath(import.meta.url))
const CSS_PATH = path.resolve(HERE, "..", "index.css")
const CSS = readFileSync(CSS_PATH, "utf8")
// All CSS parsing below runs on the comment-stripped text so a
// commented-out `--x: y;` can't satisfy the must-resolve rules (or trip
// the staleness guard), and a hex literal or `:root {` inside a comment
// can't misanchor the scanners.  stripComments (hoisted) is shared with
// the ts/tsx scan; its string modes are safe on CSS because quote
// characters OUTSIDE comments only occur in balanced pairs (quoted font
// names, `content: ''`), so string-mode toggling re-synchronises, and
// quotes inside /* */ comments are blanked before string handling
// applies.
const CSS_CODE = stripComments(CSS)

// Tailwind v4's default theme declarations (`--font-mono`,
// `--color-red-500`, `--spacing`, ...).  index.css starts with
// `@import "tailwindcss"`.  IMPORTANT: this is the DECLARED set, not the
// EMITTED set — Tailwind tree-shakes theme variables, emitting a token
// into `@layer theme { :root ... }` only when some generated utility (or
// another emitted variable) references it, and `@theme ... reference`
// blocks are never emitted at all.  Of ~375 declarations here only ~50
// exist in the built bundle.  So this full set is used ONLY for the
// negative guards (shadowing, allowlist staleness), NEVER to satisfy the
// must-resolve rules — that's what TAILWIND_PROVIDED_TOKENS is for.
// A missing file here fails the whole suite loudly (run `npm ci`), which
// is the right failure mode: without it the contract cannot be evaluated.
const TAILWIND_THEME = readFileSync(
  createRequire(import.meta.url).resolve("tailwindcss/theme.css"),
  "utf8",
)

// ---------------------------------------------------------------------------
// Parser — find the :root { ... } block (only looks at the first one, which
// is canonically the single source of truth for tokens in this project).
// ---------------------------------------------------------------------------

/**
 * Return the half-open `[start, end)` byte range of the first `:root { ... }`
 * block's contents (i.e. the range between the `{` and the matching `}`).
 *
 * Uses brace-depth tracking rather than a regex so nested braces (e.g.
 * keyframes inside :root, if ever added) are handled correctly.  Call
 * sites pass the comment-stripped CSS_CODE, so comments cannot misanchor
 * the match; strings are not separately stripped — if the block ever
 * contains one, the walk still errs in the safe direction (treating a
 * `}` inside a string as a close brace is harmless for the contract,
 * which only cares about the OUTER extent of :root).
 */
/**
 * Blank the CONTENTS of every `@theme ... { ... }` block (newlines
 * preserved) so a scan over the result sees no @theme-scoped
 * declarations.  Used by the shadow guard: an @theme block is the
 * SANCTIONED place to override a Tailwind theme token (Tailwind ingests
 * it into the theme layer), so declarations there must not be reported
 * as shadows — otherwise the guard's own failure guidance ("move the
 * override into @theme") would re-trip the guard it remedies.
 */
function stripThemeBlocks(css: string): string {
  let out = css
  const openRe = /@theme[^{]*\{/g
  let m: RegExpExecArray | null
  while ((m = openRe.exec(css)) !== null) {
    let depth = 1
    let end = css.length
    for (let i = m.index + m[0].length; i < css.length; i++) {
      const ch = css[i]
      if (ch === "{") depth++
      else if (ch === "}") {
        depth--
        if (depth === 0) {
          end = i
          break
        }
      }
    }
    const start = m.index + m[0].length
    const blanked = css
      .slice(start, end)
      .replace(/[^\n]/g, " ")
    out = out.slice(0, start) + blanked + out.slice(end)
  }
  return out
}

// ---------------------------------------------------------------------------
// Private primitive tokens
// ---------------------------------------------------------------------------

/**
 * Family PATTERNS for the graded ladder primitives that only the role
 * tokens in index.css's :root block may reference.  A call site (ts/tsx,
 * or a CSS rule body) that reaches for one directly bypasses the role
 * layer — the role tokens exist so a shade retune touches one alias line
 * per role instead of a grep across the tree.
 *
 * Patterns, not a hand-copied name list: the concrete tokens are derived
 * from whatever the ladder currently declares, so a new ladder step is
 * private from birth rather than silently public until someone remembers
 * to update a list.  Extend family by family as the semantic layer rolls
 * out (warning, danger, accent to follow).
 */
const PRIVATE_PRIMITIVE_PATTERNS = [
  // Success family: the graded soft/border steps and the hover step.
  // Deliberately NOT private: the base hue --success (still a direct
  // anchor for ~25 sites) and --success-fill* (main's Commit button pair,
  // unclassified until its own role token lands).
  /--success-(?:soft|border|hover)[\w-]*/,
]

interface PrimitiveMention {
  match: string
  line: number
}

/**
 * Every occurrence of a private-primitive IDENTIFIER in `text` — an
 * identifier scan, not a var() parse, so Tailwind arbitrary-property
 * shorthand (`bg-(--success-soft)`), dynamic template construction
 * (`var(--success-soft-${tier})`, caught via its static prefix), and
 * fallback-carrying references are all found.  A leading word/dash
 * boundary stops `--x--success-soft`-style false joins.
 */
function findPrivatePrimitiveMentions(text: string): PrimitiveMention[] {
  const hits: PrimitiveMention[] = []
  for (const pattern of PRIVATE_PRIMITIVE_PATTERNS) {
    const re = new RegExp(`(?<![-\\w])${pattern.source}`, "g")
    let m: RegExpExecArray | null
    while ((m = re.exec(text)) !== null) {
      const line = text.slice(0, m.index).split("\n").length
      hits.push({ match: m[0], line })
    }
  }
  return hits.sort((a, b) => a.line - b.line)
}

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
 * For our purposes we only need to know whether a NON-EMPTY fallback
 * exists, not its exact value — so a simple look-past-the-name approach
 * is enough: consume everything up to the matching `)` of this var(...)
 * call and check for a depth-1 `,` followed by non-whitespace content.
 */
function findVarRefs(css: string): VarRef[] {
  const refs: VarRef[] = []
  const lineStarts: number[] = [0]
  for (let i = 0; i < css.length; i++) {
    if (css[i] === "\n") lineStarts.push(i + 1)
  }

  // Name charset admits digit-leading names (`--2xl`) — valid CSS idents
  // that a letters-only pattern would silently skip on BOTH the reference
  // and declaration side, making such a token invisible to the contract.
  const openRe = /\bvar\s*\(\s*(--[a-zA-Z0-9_][a-zA-Z0-9_-]*)\s*/g
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
    // Walk forward from the end of the match to find the matching `)`.
    // hasFallback requires a NON-EMPTY fallback: a comma at depth 1
    // followed by at least one non-whitespace character before the
    // matching close.  `var(--x,)` is syntactically valid CSS but
    // substitutes to nothing when --x is unset, leaving the declaration
    // invalid at computed-value time — the exact runtime failure this
    // gate exists to prevent — so an empty fallback must not satisfy
    // the parameterised exemption.
    let depth = 1
    let sawComma = false
    let hasFallback = false
    for (let i = openRe.lastIndex; i < css.length; i++) {
      const ch = css[i]
      if (ch === "(") depth++
      else if (ch === ")") {
        depth--
        if (depth === 0) break
      }
      if (ch === "," && depth === 1 && !sawComma) {
        sawComma = true
        continue
      }
      if (sawComma && /\S/.test(ch)) hasFallback = true
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
 * Custom properties that are DELIBERATELY caller-supplied: some JSX sets
 * them at runtime via inline `style={{ "--name": ... }}`, so they cannot
 * be declared in :root.  References to these are exempt from the
 * must-resolve rule, but ONLY when they carry an inline fallback (the
 * value used until a caller sets the property).
 *
 * Keep this list minimal and literal.  Before adding an entry, verify a
 * live call site actually sets the property inline — an undeclared token
 * that nothing sets belongs in :root, not here.  (The staleness test
 * below fails if an entry gains a :root declaration and becomes
 * redundant.)
 */
const PARAMETERISED_TOKENS = new Set([
  // Accent piped into .accent-hover-btn — per node type in
  // RatingStepEditor, BandingRulesGrid, BreakpointGrid; TwoWayGrid pins
  // the literal default 'var(--accent)'.
  "--node-accent",
  // Focus-ring colour overrides set by editor wrappers via withAlpha(...)
  // — EdgeJoinEditor and _IoFormatEditor pipe their accentColor prop,
  // ExploreOverviewConfig uses NODE_GROUP_COLORS.explore directly.
  "--focus-ring-border",
  "--focus-ring-shadow",
])

/**
 * Tailwind theme tokens the app's var() references deliberately rely on.
 * Because of Tailwind's tree-shaking (see TAILWIND_THEME above), each
 * entry is only safe while some utility keeps it in the emitted bundle —
 * the value is a regex matching that utility in live source, asserted by
 * the emission guard test below.  Keep this list minimal and literal;
 * verify a token really appears in the built CSS before adding it.
 */
const TAILWIND_PROVIDED_TOKENS = new Map<string, RegExp>([
  // The mono face behind the --font-data role token in index.css (trace
  // value text goes through the role token, not this name).  The token
  // stays emitted because `.font-mono` utility classes are used
  // throughout the app — and Tailwind's preflight references it
  // unconditionally via --default-mono-font-family.
  ["--font-mono", /(?<![\w-])font-mono(?![\w-])/],
])

// Full DECLARED Tailwind theme token set — negative guards only (see the
// TAILWIND_THEME comment).  Parsed once from comment-stripped text; ~375
// entries.
const TAILWIND_DECLARED = findTokenDeclarations(stripComments(TAILWIND_THEME))

/** The union that satisfies the must-resolve rules: index.css tokens plus
 *  the explicitly listed Tailwind-provided ones. */
function resolvableTokens(css: string): Set<string> {
  return new Set([...findTokenDeclarations(css), ...TAILWIND_PROVIDED_TOKENS.keys()])
}

/**
 * A reference is dangling when its token is neither in `declared` (at
 * the call sites below: index.css declarations plus the explicit
 * TAILWIND_PROVIDED_TOKENS entries via resolvableTokens() — NEVER the
 * full TAILWIND_DECLARED set, which contains ~322 tree-shaken tokens
 * that don't exist at runtime) nor a PARAMETERISED_TOKENS entry
 * (caller-supplied) carrying a non-empty inline fallback.  Note an allowlisted token WITHOUT a fallback is still
 * dangling — until a caller sets it, the property has no value.  The
 * allowlist is a parameter so the predicate is testable with synthetic
 * tokens.
 */
function isDangling(
  ref: VarRef,
  declared: Set<string>,
  parameterised: Set<string> = PARAMETERISED_TOKENS,
): boolean {
  if (declared.has(ref.name)) return false
  return !(parameterised.has(ref.name) && ref.hasFallback)
}

/**
 * Find every `--name:` declaration in the CSS file (restricted to the
 * :root block in practice, but we scan the whole file so tokens declared
 * inside e.g. `[data-theme="dark"]` blocks would also count).  Returns
 * a Set for O(1) membership checks.
 */
function findTokenDeclarations(css: string): Set<string> {
  const decls = new Set<string>()
  const re = /(^|[\s{;])(--[a-zA-Z0-9_][a-zA-Z0-9_-]*)\s*:/g
  let m: RegExpExecArray | null
  while ((m = re.exec(css)) !== null) {
    decls.add(m[2])
  }
  return decls
}

/**
 * Like findTokenDeclarations, but capturing each token's declared VALUE
 * (whitespace collapsed, trailing `;` required).  Used by the
 * behaviour-preservation pin to assert role-token → primitive aliases.
 * Last declaration wins, matching the CSS cascade within one block.
 */
function findTokenDeclarationValues(css: string): Map<string, string> {
  const values = new Map<string, string>()
  const re = /(^|[\s{;])(--[a-zA-Z_][a-zA-Z0-9_-]*)\s*:\s*([^;}]+);/g
  let m: RegExpExecArray | null
  while ((m = re.exec(css)) !== null) {
    values.set(m[2], m[3].trim().replace(/\s+/g, " "))
  }
  return values
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
 * Enumerate `.css` stylesheets under `frontend/src` (skipping `__tests__/`).
 * index.css is the only one today; scanning the tree keeps any future
 * stylesheet inside the role-layer contract from the moment it is added
 * rather than making the single-file scope a silent assumption.
 */
function collectStylesheets(dir: string): string[] {
  const out: string[] = []
  for (const entry of readdirSync(dir)) {
    const full = path.join(dir, entry)
    if (statSync(full).isDirectory()) {
      if (entry === "__tests__") continue
      out.push(...collectStylesheets(full))
      continue
    }
    if (/\.css$/.test(entry)) out.push(full)
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
 * KNOWN LIMITATION: it is not regex-literal-aware — a REGEX literal
 * whose body contains slash-star (say, a path glob matching "/api/" then
 * star) opens a phantom comment span and blanks following code up to the
 * next star-slash, hiding any dangling var() in that stretch.
 * Distinguishing regex literals from division needs a real tokeniser;
 * none of the scanned tree contains such a literal today.
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
    expect(() => findRootBlockRange(CSS_CODE)).not.toThrow()
  })

  it("no hex literals appear outside the :root block", () => {
    const { start, end } = findRootBlockRange(CSS_CODE)
    const hits = findHexOutsideRoot(CSS_CODE, start, end)
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

  it("every var(--name) reference resolves to a declaration (index.css or a listed Tailwind token, or an allowlisted parameterised token with a fallback)", () => {
    const refs = findVarRefs(CSS_CODE)
    const declared = resolvableTokens(CSS_CODE)
    const dangling = refs.filter((r) => isDangling(r, declared))
    if (dangling.length > 0) {
      const summary = dangling
        .map((d) => `  index.css:${d.line}  var(${d.name})  — undeclared and not an allowlisted parameterised token`)
        .join("\n")
      throw new Error(
        `Found ${dangling.length} dangling var(--...) reference(s). Declare the token in :root — or, ONLY if JSX sets it inline, add it to PARAMETERISED_TOKENS with a fallback at every call site:\n${summary}`,
      )
    }
    expect(dangling).toEqual([])
  })

  it("index.css does not shadow a Tailwind theme token in plain :root (contract property 4)", () => {
    // An unlayered :root declaration outranks `@layer theme`, so
    // redeclaring e.g. `--font-mono` here silently re-themes every
    // `.font-mono` utility call site and Tailwind's base code/pre
    // styling — far beyond whatever component prompted the declaration.
    // Deliberate overrides go in an `@theme { ... }` block instead —
    // stripThemeBlocks excludes those, so following the failure guidance
    // actually turns this guard green.  Checked against Tailwind's full
    // DECLARED set, not just the emitted subset: a today-unemitted token
    // can start being emitted the moment a utility using it appears, at
    // which point an existing plain-:root declaration would begin
    // shadowing it.
    const shadows = [...findTokenDeclarations(stripThemeBlocks(CSS_CODE))].filter((t) =>
      TAILWIND_DECLARED.has(t),
    )
    expect(shadows).toEqual([])
  })

  it("the shadow guard exempts @theme-block overrides but not plain :root ones (unit)", () => {
    const sample =
      "@theme {\n  --font-mono: 'My Mono', monospace;\n}\n:root {\n  --font-sans: serif;\n  --house-token: #fff;\n}"
    const scanned = findTokenDeclarations(stripThemeBlocks(sample))
    // The @theme-scoped override is invisible to the shadow scan...
    expect(scanned.has("--font-mono")).toBe(false)
    // ...while plain :root declarations (shadowing or not) still are.
    expect(scanned.has("--font-sans")).toBe(true)
    expect(scanned.has("--house-token")).toBe(true)
  })

  it("TAILWIND_PROVIDED_TOKENS entries are declared in theme.css and kept emitted by a live utility usage (emission guard)", () => {
    // Two ways an entry can rot: the token disappears from Tailwind's
    // theme on an upgrade, or the last usage of the utility that keeps
    // it in the tree-shaken bundle is refactored away — after which
    // every var() reference to it silently falls back at runtime while
    // theme.css still declares it.
    // Scan surface mirrors Tailwind's own class scanner: ts/tsx source
    // PLUS index.html and index.css (utilities can live in markup or
    // @apply) — so migrating the last usage to either surface doesn't
    // fail the guard spuriously.
    const files = collectSourceFiles(SRC_ROOT)
    const texts = files.map((f) => stripComments(readFileSync(f, "utf8")))
    texts.push(CSS_CODE)
    // HTML comments stripped so a stale <!-- font-mono --> remark can't
    // keep the guard green after the last real usage is gone.
    texts.push(
      readFileSync(path.resolve(HERE, "..", "..", "index.html"), "utf8").replace(
        /<!--[\s\S]*?-->/g,
        " ",
      ),
    )
    const undeclared = [...TAILWIND_PROVIDED_TOKENS.keys()].filter((t) => !TAILWIND_DECLARED.has(t))
    expect(undeclared).toEqual([])
    const unemitted = [...TAILWIND_PROVIDED_TOKENS.entries()]
      .filter(([, usageRe]) => !texts.some((t) => usageRe.test(t)))
      .map(([name]) => name)
    expect(unemitted).toEqual([])
  })

  it("PARAMETERISED_TOKENS entries are not also declared in index.css or Tailwind's theme (staleness guard)", () => {
    // If an allowlisted token gains a declaration, it is no longer
    // caller-supplied-only and the allowlist entry is dead weight that
    // could mask a future regression — remove it.
    const declared = new Set([...findTokenDeclarations(CSS_CODE), ...TAILWIND_DECLARED])
    const stale = [...PARAMETERISED_TOKENS].filter((t) => declared.has(t))
    expect(stale).toEqual([])
  })

  it("a non-trivial palette is declared (smoke — prevents accidental :root deletion)", () => {
    // If someone deletes the :root declarations in a refactor, the other
    // tests would still pass (no hex outside :root, no dangling refs because
    // there are no refs either).  This smoke test pins that a real palette
    // exists — the exact size is not meaningful, only that the file hasn't
    // been gutted.
    const declared = findTokenDeclarations(CSS_CODE)
    // The core token set at time of writing has ~30 entries; require at
    // least 10 so a small refactor doesn't trip this but a deletion does.
    expect(declared.size).toBeGreaterThanOrEqual(10)
  })

  it("the core palette tokens are declared (regression guard)", () => {
    // Pin the names of tokens that the rest of the UI depends on.  If one
    // of these is renamed/removed without a codemod of the call sites, the
    // UI silently falls back to the CSS initial value — this test catches
    // that before it ships.
    const declared = findTokenDeclarations(CSS_CODE)
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

  it("stylesheets do not mention private primitive tokens outside index.css's :root (role-layer gate, CSS side)", () => {
    // :root is where the ladder is declared and where the role-token
    // aliases live; everything else — index.css rule bodies and any other
    // stylesheet under src/ — must go through the role tokens.  Identifier
    // scan, same as the ts/tsx side.
    const offenders: string[] = []
    const { start, end } = findRootBlockRange(CSS_CODE)
    for (const hit of findPrivatePrimitiveMentions(CSS_CODE.slice(0, start) + CSS_CODE.slice(end))) {
      // Line numbers are meaningless on the spliced text; name the region.
      offenders.push(`  index.css (outside :root)  ${hit.match}`)
    }
    for (const file of collectStylesheets(SRC_ROOT)) {
      if (path.resolve(file) === CSS_PATH) continue
      const rel = path.relative(SRC_ROOT, file).split(path.sep).join(path.posix.sep)
      for (const hit of findPrivatePrimitiveMentions(stripComments(readFileSync(file, "utf8")))) {
        offenders.push(`  ${rel}:${hit.line}  ${hit.match}`)
      }
    }
    if (offenders.length > 0) {
      throw new Error(
        `Found ${offenders.length} mention(s) of private primitive tokens outside index.css's :root. ` +
          `Reference the role token that aliases the primitive instead:\n` +
          offenders.join("\n"),
      )
    }
    expect(offenders).toEqual([])
  })

  it("success role tokens alias their expected primitives (behaviour-preservation pin)", () => {
    // The role layer is a pure re-labelling: each role token must resolve
    // to exactly the primitive its call sites rendered with before the
    // migration.  This map IS the review surface for shade changes — a
    // retune must edit it, visibly.  (The near-miss this pins against: a
    // draft of this PR silently normalised three banner shades and broke
    // intra-component tone parity.)
    const expected: Record<string, string> = {
      "--trace-added-text": "var(--color-added, var(--success-hover))",
      "--trace-added-bg": "var(--success-soft-mid)",
      "--trace-chip-matched-text": "var(--success-hover)",
      "--trace-chip-matched-bg": "var(--success-soft-mid)",
      "--delta-positive-text": "var(--success-hover)",
      "--flash-success-text": "var(--success-hover)",
      "--banner-success-bg": "var(--success-soft)",
      "--banner-success-border": "var(--success-border)",
      "--banner-success-text": "var(--success)",
      "--banner-success-data": "var(--success-hover)",
      "--banner-success-action": "var(--success-hover)",
      "--editor-status-success-bg": "var(--success-soft-subtle)",
      "--editor-status-success-border": "var(--success-border)",
      "--editor-status-success-text": "var(--success)",
      "--train-summary-success-bg": "var(--success-soft-subtle)",
      "--train-summary-success-border": "var(--success-soft-strong)",
      "--train-summary-success-text": "var(--success)",
      "--train-complete-bg": "var(--success-soft-faint)",
      "--train-complete-border": "var(--success-border)",
      "--train-complete-text": "var(--success)",
      "--add-pill-text": "var(--success)",
      "--add-pill-border": "var(--success-border)",
      "--add-pill-bg": "var(--success-soft)",
      "--column-confirm-text": "var(--success)",
      "--column-confirm-border": "var(--success-border)",
      "--column-origin-manual-text": "var(--success)",
      "--column-origin-manual-bg": "var(--success-soft)",
      "--cache-ready-text": "var(--success)",
      "--cache-ready-border": "var(--success-border-strong)",
      "--cache-ready-bg": "var(--success-soft)",
      "--branch-restore-text": "var(--success)",
      "--branch-restore-hover-bg": "var(--success-soft)",
      "--diff-added-soft": "var(--success-soft)",
    }
    const declared = findTokenDeclarationValues(CSS_CODE)
    const mismatches = Object.entries(expected)
      .filter(([name, value]) => declared.get(name) !== value)
      .map(([name, value]) => `  ${name}: expected "${value}", declared "${declared.get(name) ?? "(missing)"}"`)
    expect(mismatches).toEqual([])
  })
})

describe("Tailwind-provided tokens", () => {
  it("the shadow guard's declaration scan covers non-:root blocks", () => {
    // Pin the whole-file property the shadow guards rely on: a token
    // declared in an `html, body { ... }` block (or any other unlayered
    // block) is found by findTokenDeclarations exactly like a :root
    // declaration.  Without this canary, a refactor that narrowed the
    // scan to the first :root block would silently re-open the html/body
    // shadow hole.
    const decls = findTokenDeclarations("html, body, #root { --font-mono: x; }")
    expect(decls.has("--font-mono")).toBe(true)
  })

  it("each mapped token survives into the app's compiled output (compiled-emission guard)", async () => {
    // The input-side emission guard above pins the PREMISE of emission
    // (theme.css declaration + live utility usage).  This one pins the
    // OUTPUT: a Tailwind upgrade could keep theme.css textually intact yet
    // stop emitting the custom property (e.g. moving the font block to
    // `@theme inline`, whose values are substituted at build time and
    // never emitted).  The input guard would pass while every
    // var(--font-mono) — and so --font-data — silently became invalid at
    // computed-value time.
    //
    // The compilation target is the APP's real entry (src/index.css), not
    // Tailwind's package entry — the invariant is "our build emits the
    // token", and an index.css edit that dropped the default theme must
    // fail here too.  No utility candidates are passed: emission must hold
    // even for a page that uses no Tailwind utilities, via index.css's own
    // var() reference (or, for --font-mono, Tailwind's preflight).  The
    // assertion runs on comment-stripped output and anchors the token name
    // so neither a stray comment nor a longer token (--font-mono-x) can
    // satisfy it.
    let compile: typeof import("tailwindcss").compile
    try {
      ;({ compile } = await import("tailwindcss"))
    } catch {
      throw new Error("Cannot import tailwindcss — is frontend/node_modules installed? (npm ci --prefix frontend)")
    }
    const twEntry = path.resolve(HERE, "..", "..", "node_modules", "tailwindcss", "index.css")
    const compiled = await compile(CSS, {
      base: path.dirname(CSS_PATH),
      async loadStylesheet(id: string, base: string) {
        // `@import "tailwindcss"` resolves to the package entry; the
        // package's internal imports (theme/preflight/utilities.css)
        // resolve relative to the importing file.
        const file = id === "tailwindcss" ? twEntry : path.resolve(base, id)
        return { path: file, base: path.dirname(file), content: readFileSync(file, "utf8") }
      },
    })
    const output = stripComments(compiled.build([]))
    for (const token of TAILWIND_PROVIDED_TOKENS.keys()) {
      const declRe = new RegExp(`(^|[^-\\w])${token}\\s*:`, "m")
      expect(
        declRe.test(output),
        `${token} is not emitted by the app's compiled CSS — the TAILWIND_PROVIDED_TOKENS premise no longer holds`,
      ).toBe(true)
    }
  })
})

describe("ts/tsx source — design-token contract", () => {
  it("every var(--name) in live source resolves to an index.css token (or is an allowlisted parameterised token with a fallback)", () => {
    // Regression guard for the bug class where a component references a
    // token that was never declared (or was renamed away): the style is
    // invalid at computed-value time and the property silently falls back
    // to its initial value — e.g. `background` → transparent or
    // `border-color` → currentColor.  A fallback does NOT exempt a
    // reference: the `--color-added/...` family shipped undeclared behind
    // fallbacks, rendering only via them, and the old shape-based
    // exemption never noticed.
    const declared = resolvableTokens(CSS_CODE)
    const offenders: string[] = []
    const files = collectSourceFiles(SRC_ROOT)
    for (const file of files) {
      const text = stripComments(readFileSync(file, "utf8"))
      const dangling = findVarRefs(text).filter((r) => isDangling(r, declared))
      for (const d of dangling) {
        const rel = path.relative(SRC_ROOT, file).split(path.sep).join(path.posix.sep)
        offenders.push(`  ${rel}:${d.line}  var(${d.name})  — undeclared and not an allowlisted parameterised token`)
      }
    }
    if (offenders.length > 0) {
      throw new Error(
        `Found ${offenders.length} dangling var(--...) reference(s) in ts/tsx source. ` +
          `Declare the token in index.css :root; for a Tailwind theme token the app uses, ` +
          `add it to TAILWIND_PROVIDED_TOKENS instead (never shadow one in plain :root); ` +
          `or, ONLY if JSX sets the property via inline style, add it to ` +
          `PARAMETERISED_TOKENS with a fallback at every call site:\n` +
          offenders.join("\n"),
      )
    }
    expect(offenders).toEqual([])
  })

  it("an undeclared token is dangling even behind a fallback, unless allowlisted (regression: --color-* family)", () => {
    // Pins the hardened rule directly: the old shape-based exemption let
    // ANY `var(--x, fallback)` pass, which is exactly how an undeclared
    // token hides behind its fallback.  Uses a synthetic allowlist so
    // the assertion doesn't silently change meaning if the real
    // PARAMETERISED_TOKENS entries are ever edited.
    const declared = new Set(["--real"])
    const allow = new Set(["--param"])
    const [typo] = findVarRefs("a { font-family: var(--font-typo, monospace); }")
    expect(isDangling(typo, declared, allow)).toBe(true)
    // Declared token — fine with or without fallback.
    const [real] = findVarRefs("a { color: var(--real); }")
    expect(isDangling(real, declared, allow)).toBe(false)
    // Allowlisted parameterised token — exempt only WITH a fallback.
    const [withFb] = findVarRefs("a { color: var(--param, var(--real)); }")
    expect(isDangling(withFb, declared, allow)).toBe(false)
    const [withoutFb] = findVarRefs("a { color: var(--param); }")
    expect(isDangling(withoutFb, declared, allow)).toBe(true)
    // An EMPTY fallback does not count: `var(--param,)` is valid syntax
    // but substitutes to nothing when the property is unset, leaving the
    // declaration invalid at computed-value time.
    const [emptyFb] = findVarRefs("a { color: var(--param,); }")
    expect(emptyFb.hasFallback).toBe(false)
    expect(isDangling(emptyFb, declared, allow)).toBe(true)
    const [wsFb] = findVarRefs("a { color: var(--param,  ); }")
    expect(wsFb.hasFallback).toBe(false)
    expect(isDangling(wsFb, declared, allow)).toBe(true)
  })

  it("trace-colour aliases point at their intended targets (semantic pinning)", () => {
    // The six aliases were introduced as behaviour-preserving: each MUST
    // track the exact token its call sites historically used as inline
    // fallback.  Without this table a silent swap (added ↔ removed, or
    // re-pointing at a different green) would pass every structural rule.
    const expected: Record<string, string> = {
      "--color-added": "var(--success-hover)",
      "--color-modified": "var(--warning)",
      "--color-removed": "var(--danger-text)",
      "--color-positive": "var(--chart-positive)",
      "--color-negative": "var(--chart-negative)",
      "--color-neutral": "var(--chart-neutral)",
    }
    for (const [name, target] of Object.entries(expected)) {
      const m = new RegExp(`(^|[\\s{;])${name}\\s*:\\s*([^;]+);`).exec(CSS_CODE)
      expect(m, `${name} is not declared in index.css`).toBeTruthy()
      expect((m as RegExpExecArray)[2].trim()).toBe(target)
    }
  })

  it("stripComments: a commented-out declaration does not count as declared (canary)", () => {
    // Mirrors the string-literal canary above, in the other direction:
    // the CSS-side parsers run on stripped text precisely so a
    // commented-out `--x: y;` can't satisfy the must-resolve rules.
    const sample = "/* --ghost: red; */\n:root { --real: blue; }"
    const decls = findTokenDeclarations(stripComments(sample))
    expect(decls.has("--ghost")).toBe(false)
    expect(decls.has("--real")).toBe(true)
  })

  it("every PARAMETERISED_TOKENS entry has a live inline setter in source (honesty guard)", () => {
    // The allowlist's premise is that some JSX actually SETS the property.
    // If the last setter is refactored away, every reference renders
    // permanently via its fallback — the exact bug class this suite
    // exists to catch — so the entry must not stay green on trust.
    // The permitted setter shapes (doc and regex must stay in sync):
    //   1. computed style key:  ["--name" as string]: value  /  ["--name"]: value
    //   2. plain quoted key:    "--name": value   (object-level cast)
    //   3. setProperty call:    el.style.setProperty("--name", value)
    // A quoted-name-followed-by-colon shape is what distinguishes a
    // setter from readers (getPropertyValue("--name")) — those never put
    // a `:` after the closing quote.  A token-list ARRAY (["--name"])
    // has no colon either.  New setter shapes must be added here if
    // introduced.
    const files = collectSourceFiles(SRC_ROOT)
    const texts = files.map((f) => stripComments(readFileSync(f, "utf8")))
    const orphaned = [...PARAMETERISED_TOKENS].filter((name) => {
      const setterRe = new RegExp(
        `\\[\\s*["'\`]${name}["'\`](\\s+as\\s+string)?\\s*\\]\\s*:` +
          `|["'\`]${name}["'\`]\\s*:` +
          `|setProperty\\(\\s*["'\`]${name}["'\`]`,
      )
      return !texts.some((t) => setterRe.test(t))
    })
    expect(orphaned).toEqual([])
  })

  it("the typography role token is adopted in live source (adoption pin)", () => {
    // The three trace-detail sites migrated to var(--font-data) are the
    // role layer's seed adoption.  Nothing else pins them: a revert to a
    // raw font stack (or to var(--font-mono, monospace)) would pass every
    // resolution rule.  Requiring at least one live reference keeps the
    // role token honest — a declared-but-unreferenced role token is a
    // regression, not a tidy-up.
    const files = collectSourceFiles(SRC_ROOT)
    const referenced = files.some((f) =>
      findVarRefs(stripComments(readFileSync(f, "utf8"))).some((r) => r.name === "--font-data"),
    )
    expect(referenced, "no live ts/tsx source references var(--font-data)").toBe(true)
  })

  it("live source does not mention private primitive tokens (role-layer gate)", () => {
    // Without this rule the "private primitives" comment in index.css is
    // prose, not a contract: the first `var(--success-soft)` written at a
    // call site would pass the resolution rule above (the token IS
    // declared) and quietly re-open direct ladder access.  The scan
    // matches the token IDENTIFIERS themselves, not parsed var() calls,
    // so it also catches Tailwind v4 arbitrary-property shorthand
    // (`bg-(--success-soft)`), template-interpolated names whose static
    // prefix is a ladder step (`var(--success-soft-${tier})`), and
    // fallback-carrying refs — routing through the role token is the
    // point, not resolvability.
    const offenders: string[] = []
    for (const file of collectSourceFiles(SRC_ROOT)) {
      const text = stripComments(readFileSync(file, "utf8"))
      for (const hit of findPrivatePrimitiveMentions(text)) {
        const rel = path.relative(SRC_ROOT, file).split(path.sep).join(path.posix.sep)
        offenders.push(`  ${rel}:${hit.line}  ${hit.match}`)
      }
    }
    if (offenders.length > 0) {
      throw new Error(
        `Found ${offenders.length} mention(s) of private primitive tokens in ts/tsx source. ` +
          `Reference the index.css role token that aliases the primitive (or add a new role ` +
          `token there) instead of the ladder step:\n` +
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
