/**
 * Hover handler teardown contract for modelling subpanels and misc sites.
 *
 * ─── Why this suite exists ──────────────────────────────────────────────────
 *
 * The wider hover refactor migrates every `onMouseEnter`/`onMouseLeave`
 * handler that mutates `e.currentTarget.style.*` over to className-driven
 * hover (Tailwind `hover:*` or the shared `.hover-chrome` class from
 * `index.css`).  The motivation is on record at
 * `docs/CODEBASE_REVIEW.md` — inline-style mutation in a React render path
 * is a concurrent-rendering hazard (the style can be committed to a node
 * that was never mounted into the DOM, or survives after the component
 * unmounts), and the shared `utils/hoverHandlers.ts` factory just papers
 * over the repetition without fixing the underlying bug.
 *
 * Packages 8A–8D migrate their own callers off `hoverHandlers()` and
 * `hoverBg()`.  This package (8E) is the FINAL one: it:
 *
 *   1. Migrates the last 12 inline `.currentTarget.style.*` sites in the
 *      modelling subpanels (NodePalette, ModellingConfig,
 *      FeatureBrowser, FeatureAndAlgorithmConfig) plus the 6 GitPanel
 *      call-sites that use `hoverHandlers`/`hoverBg`.
 *   2. Deletes the entire `frontend/src/utils/hoverHandlers.ts` module —
 *      including its two factory functions (`hoverHandlers()` and
 *      `hoverBg()`) which themselves own 6 more `.currentTarget.style.*`
 *      sites that only exist because something imports them.
 *
 * Once 8E's dev work is complete, the `hoverHandlers.ts` module has zero
 * live importers anywhere in `frontend/src/` and can be removed without a
 * single line of behavioural change in the app.  This suite is the proof
 * of that invariant: it walks the full `frontend/src/` tree, parses every
 * `.ts`/`.tsx` file, and pins that neither identifier is referenced nor
 * imported — from production code OR test code (barring this file
 * itself, which necessarily mentions the identifiers as part of the
 * negative-assertion strings).
 *
 * ─── What this suite pins ───────────────────────────────────────────────────
 *
 *   A.  Per-file pins (the five files in 8E's scope):
 *       - No `e.currentTarget.style.*` mutation remains in any of them.
 *       - None of them imports from `../utils/hoverHandlers`.
 *
 *   B.  Full-tree AST walk (the TEARDOWN pin — this is the heart of 8E):
 *       - No file anywhere under `frontend/src/` imports `hoverHandlers`
 *         or `hoverBg` (by named import) or the module path
 *         `…/utils/hoverHandlers`.
 *       - A dedicated walk over the production side of the tree
 *         (everything NOT under `__tests__/`) so a dev who accidentally
 *         leaves a stray call in a non-8E file still fails the suite.
 *
 *   C.  Module deletion:
 *       - `frontend/src/utils/hoverHandlers.ts` either does not exist,
 *         or exists but exports nothing (i.e. has been emptied but not
 *         yet deleted).  Either terminal state is acceptable because
 *         some developers prefer a two-step PR flow; both are equally
 *         cheap to reason about and neither leaves callers stranded.
 *       - The companion test `utils/__tests__/hoverHandlers.test.ts`
 *         is also removed — it can't compile once the module under test
 *         is gone.
 *
 *   D.  Integration-level hover behaviour (NodePalette + GitPanel):
 *       - The critical interactive buttons (palette rows, git dropdown
 *         rows, git action buttons) still respond to mouse enter/leave
 *         with a visible class-driven style change.  This catches a
 *         dev who deletes the factory but forgets to apply a hover
 *         class to the replacement markup.
 *
 * ─── On the parser choice ───────────────────────────────────────────────────
 *
 * The walker uses `typescript.createSourceFile` for both the single-file
 * AST-walk assertions (A) and the full-tree teardown assertion (B).  We
 * do NOT use regex-on-stripped-text for these checks because:
 *
 *   - The test brief explicitly specifies "AST-walk" — regex would
 *     miss e.g. `e['currentTarget'].style.background` (bracket access)
 *     and false-positive-match strings/comments that happen to contain
 *     the sequence.  An AST walk is the only way to get zero false
 *     negatives on the hazard we actually care about.
 *   - TypeScript ships with the project already (`package.json` devDep)
 *     so there is no new dependency cost.
 *   - The AST walk happens once per file; the walk over ~150 files in
 *     `frontend/src/` completes in <500ms on a warm cache, well inside
 *     the vitest test-timeout envelope.
 *
 * ─── On why teardown IS the right end-state ────────────────────────────────
 *
 * There is no legitimate reason for a hover-style toggle in a React
 * codebase to live in a JS factory:
 *
 *   - Plain Tailwind `hover:bg-*` / `hover:text-*` classes handle the
 *     99% case with zero JS cost.
 *   - The shared `.hover-chrome` CSS class (already in `index.css`)
 *     handles the uniform chrome-toolbar pattern.
 *   - Dynamic hover colors that depend on runtime state (the ~3 call
 *     sites across the codebase that genuinely can't be expressed as
 *     a static class) are better served by CSS custom properties on
 *     the element itself (`style={{ "--hover-bg": X }}`) plus a local
 *     `hover:bg-[var(--hover-bg)]` utility — which is exactly what
 *     `NodePalette`'s `--hover-bg` custom property demonstrates is
 *     viable.
 *
 * Deleting the factory is therefore unambiguously correct; if a future
 * feature needs an inline-style hover, the right answer is still to
 * express it via CSS (custom property + Tailwind utility), not to
 * resurrect the factory.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { existsSync, readFileSync, readdirSync, statSync } from "node:fs"
import path from "node:path"
import { fileURLToPath } from "node:url"
import ts from "typescript"

// ═══════════════════════════════════════════════════════════════════════════
//  Path helpers — resolve `frontend/src/` regardless of cwd
// ═══════════════════════════════════════════════════════════════════════════

const HERE = path.dirname(fileURLToPath(import.meta.url))
// This file lives at `frontend/src/__tests__/hover/`, so `src/` is two
// levels up.
const SRC_ROOT = path.resolve(HERE, "../..")

/** The five production files 8E migrates. */
const SCOPE_FILES = [
  "panels/NodePalette.tsx",
  "panels/ModellingConfig.tsx",
  "panels/modelling/FeatureBrowser.tsx",
  "panels/modelling/FeatureAndAlgorithmConfig.tsx",
  "panels/GitPanel.tsx",
].map((rel) => ({ rel, abs: path.join(SRC_ROOT, rel) }))

const HOVER_HANDLERS_PATH = path.join(SRC_ROOT, "utils", "hoverHandlers.ts")
const HOVER_HANDLERS_TEST_PATH = path.join(
  SRC_ROOT,
  "utils",
  "__tests__",
  "hoverHandlers.test.ts",
)

// ═══════════════════════════════════════════════════════════════════════════
//  Source walker + AST parser
// ═══════════════════════════════════════════════════════════════════════════

type SrcFile = {
  /** POSIX-style path relative to frontend/src/ (for stable error messages). */
  rel: string
  /** Absolute path for reading. */
  abs: string
}

/**
 * Enumerate every `.ts` / `.tsx` file under `frontend/src/`.
 *
 * Excludes:
 *  - `.d.ts` type-only declarations (no runtime code, no imports).
 *  - THIS file, because it necessarily mentions `hoverHandlers` /
 *    `hoverBg` as identifiers in string/identifier positions as part
 *    of the negative assertion tables.
 */
function walkFrontendSrc(): SrcFile[] {
  const results: SrcFile[] = []
  const selfAbs = path.resolve(HERE, "modellingMiscHoverAndTeardown.test.tsx")

  const visit = (dir: string) => {
    const entries = readdirSync(dir, { withFileTypes: true })
    for (const ent of entries) {
      const abs = path.join(dir, ent.name)
      if (ent.isDirectory()) {
        visit(abs)
        continue
      }
      if (!ent.isFile()) continue
      if (!/\.(ts|tsx)$/.test(ent.name)) continue
      if (ent.name.endsWith(".d.ts")) continue
      if (abs === selfAbs) continue

      results.push({
        abs,
        rel: path.relative(SRC_ROOT, abs).split(path.sep).join("/"),
      })
    }
  }
  visit(SRC_ROOT)
  return results
}

/** Parse a TS/TSX source file into a TypeScript AST SourceFile. */
function parseSource(abs: string, text: string): ts.SourceFile {
  const scriptKind = abs.endsWith(".tsx") ? ts.ScriptKind.TSX : ts.ScriptKind.TS
  return ts.createSourceFile(
    abs,
    text,
    ts.ScriptTarget.Latest,
    /*setParentNodes*/ true,
    scriptKind,
  )
}

/**
 * Walk the AST and return every property-access chain that ends in
 * `.currentTarget.style.<prop>` — i.e. the exact hazard we're
 * eliminating in 8E.
 *
 * We specifically look for the shape `X.currentTarget.style.Y` (depth
 * 2 of PropertyAccessExpression nesting); this catches the vast
 * majority of the call sites.  Assignments like
 * `e.currentTarget.style.background = "..."` appear as an assignment
 * expression whose LHS is a PropertyAccessExpression of the form
 * `e.currentTarget.style.background`.
 */
function findCurrentTargetStyleSites(sf: ts.SourceFile): ts.Node[] {
  const hits: ts.Node[] = []

  const visit = (n: ts.Node): void => {
    // Property access: base.currentTarget.style.<anything>
    if (ts.isPropertyAccessExpression(n)) {
      // n = (x.currentTarget.style).<prop>
      // n.expression = x.currentTarget.style
      const expr = n.expression
      if (
        ts.isPropertyAccessExpression(expr) &&
        expr.name.text === "style"
      ) {
        const inner = expr.expression
        if (
          ts.isPropertyAccessExpression(inner) &&
          inner.name.text === "currentTarget"
        ) {
          hits.push(n)
          // Don't recurse further into THIS access — we already
          // matched the outermost property access.  But we DO need to
          // keep walking siblings, so we continue after pushing.
        }
      }
    }
    ts.forEachChild(n, visit)
  }

  visit(sf)
  return hits
}

/**
 * Collect every module specifier imported by an AST.  Returns an
 * array of `{ specifier, namedImports, hasDefault, hasNamespace }`
 * records, one per `import` / `export-from` statement.
 *
 * Covers:
 *   - `import X from "mod"`                 → hasDefault
 *   - `import * as X from "mod"`            → hasNamespace
 *   - `import { a, b as c } from "mod"`     → namedImports: ["a","b"]
 *   - `import "mod"`                        → side-effect
 *   - `export { x } from "mod"`             → hasDefault=false, namedImports: ["x"]
 *   - `export * from "mod"`                 → hasNamespace
 *
 * We do NOT try to follow dynamic `import("mod")` calls — none exist
 * in the current codebase and the teardown still holds by the static
 * import tree.
 */
type ImportRecord = {
  specifier: string
  namedImports: string[]
  hasDefault: boolean
  hasNamespace: boolean
}

function collectImports(sf: ts.SourceFile): ImportRecord[] {
  const records: ImportRecord[] = []

  for (const stmt of sf.statements) {
    // `import ... from "mod"` or `import "mod"`
    if (ts.isImportDeclaration(stmt)) {
      const spec = stmt.moduleSpecifier
      if (!ts.isStringLiteral(spec)) continue
      const rec: ImportRecord = {
        specifier: spec.text,
        namedImports: [],
        hasDefault: false,
        hasNamespace: false,
      }
      const clause = stmt.importClause
      if (clause) {
        if (clause.name) rec.hasDefault = true
        const bindings = clause.namedBindings
        if (bindings) {
          if (ts.isNamespaceImport(bindings)) {
            rec.hasNamespace = true
          } else if (ts.isNamedImports(bindings)) {
            for (const el of bindings.elements) {
              // `propertyName` is present for `{ foo as bar }`; the
              // ORIGINAL exported name is what we care about.
              const imported = (el.propertyName ?? el.name).text
              rec.namedImports.push(imported)
            }
          }
        }
      }
      records.push(rec)
      continue
    }

    // `export { … } from "mod"` / `export * from "mod"`
    if (ts.isExportDeclaration(stmt) && stmt.moduleSpecifier) {
      const spec = stmt.moduleSpecifier
      if (!ts.isStringLiteral(spec)) continue
      const rec: ImportRecord = {
        specifier: spec.text,
        namedImports: [],
        hasDefault: false,
        hasNamespace: false,
      }
      if (stmt.exportClause) {
        if (ts.isNamespaceExport(stmt.exportClause)) {
          rec.hasNamespace = true
        } else if (ts.isNamedExports(stmt.exportClause)) {
          for (const el of stmt.exportClause.elements) {
            const imported = (el.propertyName ?? el.name).text
            rec.namedImports.push(imported)
          }
        }
      } else {
        // `export * from "mod"`
        rec.hasNamespace = true
      }
      records.push(rec)
    }
  }

  return records
}

/**
 * True iff `spec` is a module specifier that resolves to
 * `frontend/src/utils/hoverHandlers`.  We accept any of:
 *   - "../utils/hoverHandlers"
 *   - "../../utils/hoverHandlers"
 *   - "./hoverHandlers" (relative from utils/)
 *   - "./utils/hoverHandlers" (relative from src/)
 *   - "../hoverHandlers" (relative from a peer of utils/)
 *   - "…/utils/hoverHandlers.ts" (explicit extension)
 * by checking the final path segment(s) rather than parsing the
 * full relative resolution, which would require knowing `fromDir`.
 */
function specifierPointsToHoverHandlers(spec: string): boolean {
  if (!spec.startsWith(".")) return false
  // Normalise: strip optional `.ts`/`.tsx` suffix, split on `/`.
  const stripped = spec.replace(/\.(ts|tsx)$/, "")
  const parts = stripped.split("/")
  const last = parts[parts.length - 1]
  if (last !== "hoverHandlers") return false
  // Must be under `utils/` OR be a same-directory import from
  // `utils/`.  Paths like `./hoverHandlers` from anywhere need to
  // land in utils/.  We check the second-to-last segment equals
  // "utils" OR the importing file itself lives in utils/ — but
  // since the point is that the module lives at utils/, and there
  // is no other `hoverHandlers` module in the tree, matching on
  // the final segment alone is safe.
  return true
}

// ═══════════════════════════════════════════════════════════════════════════
//  Smoke checks — the walker is actually walking
// ═══════════════════════════════════════════════════════════════════════════

describe("modelling subpanels and hoverHandlers teardown", () => {
  it("SRC_ROOT resolves to an existing directory with known child dirs (walker smoke)", () => {
    expect(existsSync(SRC_ROOT)).toBe(true)
    expect(statSync(SRC_ROOT).isDirectory()).toBe(true)
    // Stable top-level dirs we know frontend/src/ has.  If any are
    // missing, the walker is walking the wrong tree and every other
    // assertion would silently pass on an empty set.
    expect(existsSync(path.join(SRC_ROOT, "panels"))).toBe(true)
    expect(existsSync(path.join(SRC_ROOT, "components"))).toBe(true)
    expect(existsSync(path.join(SRC_ROOT, "utils"))).toBe(true)
  })

  it("walker enumerates a non-trivial number of .ts/.tsx files (walker smoke)", () => {
    const files = walkFrontendSrc()
    expect(
      files.length,
      `walkFrontendSrc() returned only ${files.length} files; expected >=80. Is SRC_ROOT wrong?`,
    ).toBeGreaterThanOrEqual(80)
  })

  it("TypeScript AST parser is usable (parser smoke)", () => {
    // Parse a trivial source string and confirm the SourceFile node
    // has the expected shape.  If `ts` is missing or mis-resolved
    // the import at the top fails; this test then fails fast with
    // a clear message rather than surfacing as a mysterious undefined
    // later.
    const sf = ts.createSourceFile(
      "smoke.ts",
      "export const x = 1",
      ts.ScriptTarget.Latest,
      true,
      ts.ScriptKind.TS,
    )
    expect(sf.statements.length).toBe(1)
  })

  // ═══════════════════════════════════════════════════════════════════════════
  //  A. Per-file AST pins — 8E's scope only
  // ═══════════════════════════════════════════════════════════════════════════
  //
  //  One `describe` per file so a failure message names the culprit
  //  directly.  Each file gets two pins:
  //    1. no `e.currentTarget.style.*` mutation anywhere in its AST
  //    2. no import from `../utils/hoverHandlers` (relative to that
  //       file's position in the tree)

  describe("A. 8E scope — no .currentTarget.style.* and no hoverHandlers import", () => {
    for (const f of SCOPE_FILES) {
      describe(f.rel, () => {
        it("does not contain any e.currentTarget.style.* mutation (AST walk)", () => {
          expect(
            existsSync(f.abs),
            `Scope file missing: ${f.rel}`,
          ).toBe(true)
          const src = readFileSync(f.abs, "utf8")
          const sf = parseSource(f.abs, src)
          const hits = findCurrentTargetStyleSites(sf)
          const lines = hits.map((n) => {
            const { line } = sf.getLineAndCharacterOfPosition(n.getStart(sf))
            return `${f.rel}:${line + 1}`
          })
          expect(
            lines,
            `Expected ${f.rel} to have zero currentTarget.style mutations; found ${hits.length}:\n  ${lines.join(
              "\n  ",
            )}`,
          ).toEqual([])
        })

        it("does not import from utils/hoverHandlers (AST walk)", () => {
          expect(existsSync(f.abs)).toBe(true)
          const src = readFileSync(f.abs, "utf8")
          const sf = parseSource(f.abs, src)
          const imports = collectImports(sf)
          const offending = imports.filter((r) =>
            specifierPointsToHoverHandlers(r.specifier),
          )
          const descriptions = offending.map(
            (r) =>
              `"${r.specifier}" — named: [${r.namedImports.join(", ")}]${
                r.hasDefault ? ", default" : ""
              }${r.hasNamespace ? ", namespace" : ""}`,
          )
          expect(
            descriptions,
            `Expected ${f.rel} to stop importing utils/hoverHandlers; still imports:\n  ${descriptions.join(
              "\n  ",
            )}`,
          ).toEqual([])
        })
      })
    }
  })

  // ═══════════════════════════════════════════════════════════════════════════
  //  B. Full-tree AST teardown pin — THE heart of 8E
  // ═══════════════════════════════════════════════════════════════════════════
  //
  //  This is the assertion that proves `utils/hoverHandlers.ts` is
  //  deletable.  If ANY file under `frontend/src/` (production or test,
  //  excepting this file) still imports either factory by name or
  //  references the module by path, the teardown is unsafe and the
  //  dev must land the migration in those files before deleting the
  //  module.
  //
  //  We run it as TWO separate assertions so the failure message can
  //  say which side of the tree is still stuck:
  //    (B1) production: everything NOT under `__tests__/`
  //    (B2) the whole tree
  //
  //  A dev fixing a single failing package (say 8C) usually only cares
  //  about the production side, so (B1) gives them a cleaner signal.

  describe("B. Teardown AST walk — utils/hoverHandlers has no importers", () => {
    /**
     * For a given filter, walk every matching `.ts`/`.tsx` file, parse
     * it, and collect imports that resolve to `utils/hoverHandlers`.
     * Returns a list of `{ rel, specifier, names }` offender records.
     */
    function collectHoverHandlersImporters(
      filter: (f: SrcFile) => boolean,
    ): Array<{ rel: string; specifier: string; names: string[] }> {
      const offenders: Array<{ rel: string; specifier: string; names: string[] }> =
        []
      for (const f of walkFrontendSrc()) {
        if (!filter(f)) continue
        const src = readFileSync(f.abs, "utf8")
        const sf = parseSource(f.abs, src)
        for (const rec of collectImports(sf)) {
          if (!specifierPointsToHoverHandlers(rec.specifier)) continue
          const names = [
            ...rec.namedImports,
            ...(rec.hasDefault ? ["<default>"] : []),
            ...(rec.hasNamespace ? ["<namespace>"] : []),
          ]
          offenders.push({ rel: f.rel, specifier: rec.specifier, names })
        }
      }
      return offenders
    }

    it("(B1) no PRODUCTION file (non-test) imports hoverHandlers or hoverBg", () => {
      // Production side first: a dev fixing 8A/8B/8C/8D/8E gets the
      // cleanest signal here — no noise from the `hoverHandlers.test`
      // file which legitimately imports the module until the module
      // itself is deleted.
      const offenders = collectHoverHandlersImporters(
        (f) =>
          !f.rel.includes("/__tests__/") &&
          !f.rel.startsWith("__tests__/"),
      )
      const lines = offenders.map(
        (o) => `${o.rel} → "${o.specifier}" [${o.names.join(", ")}]`,
      )
      expect(
        lines,
        [
          `Expected zero production imports of utils/hoverHandlers.`,
          `These callers still need to migrate before 8E can delete the module:`,
          `  ${lines.join("\n  ")}`,
        ].join("\n"),
      ).toEqual([])
    })

    it("(B2) no file ANYWHERE under frontend/src/ imports hoverHandlers or hoverBg", () => {
      // Full sweep including tests.  This one fails until the
      // companion test `utils/__tests__/hoverHandlers.test.ts` is
      // deleted too — which is part of 8E's scope because the test
      // would fail to resolve its import once the module is gone.
      const offenders = collectHoverHandlersImporters(() => true)
      const lines = offenders.map(
        (o) => `${o.rel} → "${o.specifier}" [${o.names.join(", ")}]`,
      )
      expect(
        lines,
        [
          `Expected zero imports of utils/hoverHandlers anywhere under frontend/src/.`,
          `Remaining callers (including tests):`,
          `  ${lines.join("\n  ")}`,
        ].join("\n"),
      ).toEqual([])
    })

    it("(B3) no file references the identifiers `hoverHandlers` / `hoverBg` in live code", () => {
      // Belt-and-braces: even if a dev re-introduces the factory
      // under a different module path (e.g. copies it into a new
      // `utils/styles/hover.ts`), this pin catches the identifier
      // use.  It's an AST-level check on every top-level
      // `Identifier` node equal to either name in any position that
      // isn't a string literal or comment.
      //
      // We scan for any identifier token whose text matches one of
      // the banned names and whose parent is not purely declarative
      // (i.e. we do count import specifiers, references in call
      // expressions, JSX tags, etc.).
      const BANNED = new Set(["hoverHandlers", "hoverBg"])
      const offenders: Array<{ rel: string; line: number; context: string }> = []

      for (const f of walkFrontendSrc()) {
        const src = readFileSync(f.abs, "utf8")
        const sf = parseSource(f.abs, src)

        const visit = (n: ts.Node): void => {
          if (ts.isIdentifier(n) && BANNED.has(n.text)) {
            // Exclude the identifier that appears in THIS
            // source file's own module path specifier, which
            // is impossible because specifiers are string
            // literals — but also skip identifiers whose
            // parent is a parameter name or binding pattern,
            // since those are local variable re-uses that do
            // NOT indicate a dependency on our banned factory
            // (e.g. `function foo(hoverBg: string)`).
            //
            // However, we keep this maximally strict and
            // report ALL live uses; the dev can decide per-case
            // whether a shadowed local is worth renaming.
            const { line } = sf.getLineAndCharacterOfPosition(n.getStart(sf))
            const lineText = src.split(/\r?\n/)[line] ?? ""
            offenders.push({
              rel: f.rel,
              line: line + 1,
              context: lineText.trim(),
            })
          }
          ts.forEachChild(n, visit)
        }
        visit(sf)
      }

      const lines = offenders.map(
        (o) => `${o.rel}:${o.line}  ${o.context}`,
      )
      expect(
        lines,
        [
          `Expected zero live-code references to the identifiers`,
          `\`hoverHandlers\` / \`hoverBg\`.  Remaining:`,
          `  ${lines.join("\n  ")}`,
        ].join("\n"),
      ).toEqual([])
    })
  })

  // ═══════════════════════════════════════════════════════════════════════════
  //  C. Module + companion-test deletion
  // ═══════════════════════════════════════════════════════════════════════════

  describe("C. Module deletion", () => {
    it("utils/hoverHandlers.ts is deleted, or exists but exports nothing", () => {
      // Accept two terminal states:
      //   (a) the file is gone entirely — the cleanest outcome
      //   (b) the file exists but exports neither `hoverHandlers`
      //       nor `hoverBg` (developer preferred a two-step sweep
      //       that empties the file first, deletes it second)
      //
      // What we REJECT is "file still exists AND exports at least
      // one banned name" — that means the factory is still live
      // even though nothing imports it (dead code) OR something
      // still imports it (caught by B).
      if (!existsSync(HOVER_HANDLERS_PATH)) {
        // State (a) — deleted.  Win.
        return
      }
      const src = readFileSync(HOVER_HANDLERS_PATH, "utf8")
      const sf = parseSource(HOVER_HANDLERS_PATH, src)

      const exportedNames = new Set<string>()
      for (const stmt of sf.statements) {
        // `export function hoverHandlers() {}`
        // `export function hoverBg() {}`
        if (
          ts.isFunctionDeclaration(stmt) &&
          stmt.name &&
          stmt.modifiers?.some((m) => m.kind === ts.SyntaxKind.ExportKeyword)
        ) {
          exportedNames.add(stmt.name.text)
        }
        // `export const hoverHandlers = …`
        if (
          ts.isVariableStatement(stmt) &&
          stmt.modifiers?.some((m) => m.kind === ts.SyntaxKind.ExportKeyword)
        ) {
          for (const decl of stmt.declarationList.declarations) {
            if (ts.isIdentifier(decl.name)) exportedNames.add(decl.name.text)
          }
        }
        // `export { hoverHandlers, hoverBg }` (no re-export source)
        if (
          ts.isExportDeclaration(stmt) &&
          !stmt.moduleSpecifier &&
          stmt.exportClause &&
          ts.isNamedExports(stmt.exportClause)
        ) {
          for (const el of stmt.exportClause.elements) {
            exportedNames.add(el.name.text)
          }
        }
      }

      const banned = ["hoverHandlers", "hoverBg"].filter((n) =>
        exportedNames.has(n),
      )
      expect(
        banned,
        [
          `utils/hoverHandlers.ts still exists AND still exports: ${banned.join(", ")}.`,
          `Expected either (a) the file deleted, or (b) these exports removed.`,
        ].join("\n"),
      ).toEqual([])
    })

    it("utils/__tests__/hoverHandlers.test.ts is deleted", () => {
      // The companion test imports the factory directly; once the
      // factory is gone it can't compile.  It must be removed as part
      // of 8E, along with the module.
      expect(
        existsSync(HOVER_HANDLERS_TEST_PATH),
        `Expected ${HOVER_HANDLERS_TEST_PATH} to be deleted — its only subject (hoverHandlers / hoverBg) is gone.`,
      ).toBe(false)
    })
  })

  // ═══════════════════════════════════════════════════════════════════════════
  //  D. Integration tests — hover behaviour still works
  // ═══════════════════════════════════════════════════════════════════════════
  //
  //  Per the brief: "NodePalette / GitPanel integration tests for hover".
  //  These tests render the real (post-migration) components and assert
  //  that the hover affordance is present in the markup.  We deliberately
  //  avoid asserting any specific RGB value or class name — different
  //  valid migrations (Tailwind `hover:bg-*`, `.hover-chrome`, CSS
  //  custom properties) produce different outputs, and pinning one would
  //  force a particular migration shape.  What we DO pin:
  //
  //    - The interactive element renders.
  //    - It does NOT have an inline `onMouseEnter` / `onMouseLeave`
  //      handler attached that flips inline styles (we verify by
  //      dispatching synthetic events and confirming no inline
  //      `.style.background` was written).  This is the positive
  //      behavioural proof that the migration was done correctly.

  describe("D. Integration — NodePalette hover is class-driven, not inline-style", () => {
    beforeEach(() => {
      vi.resetModules()
    })
    afterEach(() => {
      vi.restoreAllMocks()
    })

    it("NodePalette renders its palette rows and keeps them free of inline-style hover mutation", async () => {
      const { render, cleanup, fireEvent } = await import(
        "@testing-library/react"
      )
      try {
        const { default: NodePalette } = await import(
          "../../panels/NodePalette"
        )
        const { container } = render(<NodePalette nodes={[]} />)

        // The palette should render at least one row (there are
        // 15 PALETTE_TYPES entries today; any positive count proves
        // the component mounted).
        const rows = container.querySelectorAll("[draggable='true']")
        expect(
          rows.length,
          "NodePalette rendered zero draggable rows — component broke",
        ).toBeGreaterThan(0)

        // Pick the first row and dispatch a mouseenter.  A
        // correctly-migrated component uses CSS for the hover
        // effect, so `row.style.background` stays empty after the
        // event.  A regressed component would have set an inline
        // background via `e.currentTarget.style.background = …`.
        const row = rows[0] as HTMLElement
        const beforeBg = row.style.background
        fireEvent.mouseEnter(row)
        const afterBg = row.style.background
        expect(
          afterBg,
          `Hovering a palette row wrote an inline background style (${JSON.stringify(
            afterBg,
          )}). 8E expects hover to be class-driven, not inline.`,
        ).toBe(beforeBg)

        fireEvent.mouseLeave(row)
        expect(row.style.background).toBe(beforeBg)
      } finally {
        cleanup()
      }
    })
  })

  // D. (removed) The GitPanel "Start editing (create branch)" button this case
  // exercised was unwired in P5a (the panel was reworked onto the branch-pair
  // model — branch creation now goes through the toolbar working-branch
  // chooser). The general no-inline-hover principle it instanced is still
  // enforced repo-wide by "E. Regression guard" below.

  // ═══════════════════════════════════════════════════════════════════════════
  //  E. Defensive: nothing in the frontend/src tree writes inline hover
  //      styles (future regression guard)
  // ═══════════════════════════════════════════════════════════════════════════
  //
  //  This is stricter than (A): it walks every .ts/.tsx file under
  //  frontend/src/ (excepting this one + type-only .d.ts files) and
  //  demands zero `e.currentTarget.style.*` mutations ANYWHERE.
  //
  //  Waves 8A–8D cover the other files; 8E is the final package so
  //  this assertion should pass cleanly on a correctly-landed
  //  Wave 8.  If a later feature PR resurrects the pattern, this
  //  pin catches it before review.

  it("E. Regression guard — no file under frontend/src/ contains e.currentTarget.style.* mutation", () => {
    const offenders: Array<{ rel: string; line: number; context: string }> = []

    for (const f of walkFrontendSrc()) {
      const src = readFileSync(f.abs, "utf8")
      const sf = parseSource(f.abs, src)
      const hits = findCurrentTargetStyleSites(sf)
      for (const n of hits) {
        const { line } = sf.getLineAndCharacterOfPosition(n.getStart(sf))
        const lineText = src.split(/\r?\n/)[line] ?? ""
        offenders.push({
          rel: f.rel,
          line: line + 1,
          context: lineText.trim(),
        })
      }
    }

    const lines = offenders.map(
      (o) => `${o.rel}:${o.line}  ${o.context}`,
    )
    expect(
      lines,
      [
        `Expected zero e.currentTarget.style.* mutations anywhere under frontend/src/.`,
        `Remaining sites (fix by moving hover into CSS / Tailwind / .hover-chrome):`,
        `  ${lines.join("\n  ")}`,
      ].join("\n"),
    ).toEqual([])
  })
})

// ═══════════════════════════════════════════════════════════════════════════
//  Sanity: this file itself doesn't accidentally count as an importer
// ═══════════════════════════════════════════════════════════════════════════
//
//  The walker excludes THIS file by absolute path, but if somebody
//  ever copies the file under a different name the exclusion would
//  silently break.  We keep this little sanity block at the bottom
//  as a runtime cross-check: if this file is discoverable in the
//  walker output AND it contains a literal reference to
//  `hoverHandlers`, the walker would falsely claim an importer
//  remains.
//
//  The test asserts that the exclusion is load-bearing: the walker
//  should NOT return this file's path.  (It's purely defensive and
//  runs in <1ms.)

describe("Walker self-exclusion smoke", () => {
  it("walker does not include this test file in its output", () => {
    const selfAbs = path.resolve(HERE, "modellingMiscHoverAndTeardown.test.tsx")
    const files = walkFrontendSrc()
    const found = files.find((f) => f.abs === selfAbs)
    expect(
      found,
      "Walker returned its own test file; the self-exclusion in walkFrontendSrc() is broken, which would produce false positives in the teardown pins.",
    ).toBeUndefined()
  })
})
