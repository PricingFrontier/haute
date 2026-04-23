/**
 * Phase 2 Wave 5 Package 5A — item #70: delete the trivial `ConfigInput`
 * and `ConfigSelect` wrappers.
 *
 * Why this test exists
 * --------------------
 * `docs/CODEBASE_REVIEW.md` item #70 calls out
 * `frontend/src/components/form/ConfigInput.tsx` and
 * `frontend/src/components/form/ConfigSelect.tsx` as 60 / 75-line wrappers
 * that add no real value beyond:
 *   - generating an `id` for htmlFor linkage
 *   - rendering an `EditorLabel` when a `label` prop is present
 *   - spreading an `aria-label` default
 *   - mutating inline `style.borderColor` / `style.boxShadow` in
 *     `onFocus` / `onBlur` handlers (which is itself separately flagged
 *     by item #71 as a concurrent-rendering hazard)
 *
 * Inventory done at test-write time (2026-04-19):
 *   - Production consumers of `<ConfigInput>` / `<ConfigSelect>`: **ZERO**
 *     (verified by grepping `\bConfigInput\b` and `\bConfigSelect\b`
 *      across `frontend/src/`; the only matches are the wrappers
 *      themselves, the barrel `components/form/index.ts` re-exports,
 *      and the wrappers' own unit tests).
 *
 * Because there are no live callers, the end state for #70 is the
 * simplest possible: delete both wrapper modules, delete their unit
 * tests, and drop the `ConfigInput` / `ConfigSelect` names from the
 * barrel. We do NOT need a shared className constant at this point —
 * adding one now would be speculative (no consumer exists to share it
 * with), and the focus-style problem identified in the review is being
 * handled separately under item #71 with Tailwind state classes.
 *
 * If a consumer is re-introduced later that legitimately needs the same
 * input styling, that future PR is the natural home for the shared
 * className helper — not this removal.
 *
 * What this test pins
 * -------------------
 *   1. Both wrapper modules are gone from `components/form/`.
 *   2. The barrel `components/form/index.ts` no longer exports the two
 *      wrapper names.
 *   3. The wrappers' existing unit tests under
 *      `__tests__/components/form/` are deleted (they're the only other
 *      references and would otherwise fail to resolve an import).
 *   4. No file anywhere under `frontend/src/` imports either wrapper
 *      (by module path OR by identifier in a JSX tag).
 *
 * The walker uses a hand-written recursive `readdirSync` walk identical
 * in shape to the one in
 * `frontend/src/panels/__tests__/errorToastMigration.test.tsx` so the
 * test behaves identically on Windows, macOS, and Linux and across
 * invocation directories.
 *
 * A note on accessibility
 * -----------------------
 * The wrappers provided:
 *   - `aria-label={label}` on the `<input>` / `<select>`
 *   - `htmlFor` linkage between the rendered `<EditorLabel>` and the
 *     control, using `useId()` for the id.
 *
 * Because zero consumers exist, there is nothing to migrate: removing
 * the wrappers cannot regress any call site's a11y. If a future consumer
 * needs a label-linked input, the pattern it should follow (plain
 * `<EditorLabel htmlFor={id}>…</EditorLabel>` + plain `<input id={id}>`)
 * is already what the wrappers did internally, and `EditorLabel` stays
 * exported from the barrel.
 */
import { describe, it, expect } from "vitest"
import { existsSync, readFileSync, readdirSync, statSync } from "node:fs"
import path from "node:path"
import { fileURLToPath } from "node:url"

// ═══════════════════════════════════════════════════════════════════
//  Path helpers — resolve `frontend/src/` regardless of cwd
// ═══════════════════════════════════════════════════════════════════

const HERE = path.dirname(fileURLToPath(import.meta.url))
// This file lives at `frontend/src/__tests__/components/`, so `src/`
// is two levels up.
const SRC_ROOT = path.resolve(HERE, "../..")
const FORM_DIR = path.join(SRC_ROOT, "components", "form")
const BARREL_PATH = path.join(FORM_DIR, "index.ts")
const CONFIG_INPUT_PATH = path.join(FORM_DIR, "ConfigInput.tsx")
const CONFIG_SELECT_PATH = path.join(FORM_DIR, "ConfigSelect.tsx")
const CONFIG_INPUT_TEST_PATH = path.join(
  SRC_ROOT,
  "__tests__",
  "components",
  "form",
  "ConfigInput.test.tsx",
)
const CONFIG_SELECT_TEST_PATH = path.join(
  SRC_ROOT,
  "__tests__",
  "components",
  "form",
  "ConfigSelect.test.tsx",
)

// ═══════════════════════════════════════════════════════════════════
//  Source walker
// ═══════════════════════════════════════════════════════════════════

type SrcFile = {
  /** POSIX-style path relative to frontend/src/ (for stable error messages). */
  rel: string
  /** Absolute path for reading. */
  abs: string
}

/**
 * Enumerate every `.ts` / `.tsx` file under `frontend/src/`.
 *
 * Unlike the `errorToastMigration` walker, we DO include `__tests__/`
 * here — the point of #70 is that the wrappers are gone EVERYWHERE,
 * including test files (if a stray test still imports `ConfigInput`
 * that's a bug the walker must catch).  The only thing we skip is
 * this very file, since it necessarily mentions the identifiers by
 * name in comments/string literals as part of the assertion.
 */
function walkFrontendSrc(): SrcFile[] {
  const results: SrcFile[] = []
  const selfAbs = path.resolve(HERE, "configInputRemoval.test.ts")

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

/**
 * Strip line and block comments from a TS/TSX source blob before
 * scanning for identifier usage.
 *
 * We do NOT try to parse JSX properly — a full AST walk would be more
 * robust but adds a parser dependency (acorn/@babel/parser) just to
 * prove a negative.  The regex approach is sufficient because:
 *   (a) the two identifiers are capitalised and specific enough that
 *       false positives in real code are vanishingly unlikely;
 *   (b) string literals containing `ConfigInput` would be flagged as
 *       a potential miss — acceptable since they're also suspicious;
 *   (c) this test only has to enforce a NEGATIVE assertion (the
 *       identifiers should not appear) — any false positive can be
 *       addressed by the dev deleting the offending reference.
 *
 * Comments are stripped so an explanatory comment mentioning
 * `ConfigInput` in passing (e.g. "used to wrap ConfigInput before #70")
 * does not fail the pin.
 */
function stripComments(src: string): string {
  // Remove block comments (non-greedy).
  let out = src.replace(/\/\*[\s\S]*?\*\//g, "")
  // Remove line comments.
  out = out.replace(/\/\/[^\n]*/g, "")
  return out
}

/**
 * True iff `src` references the identifier `name` in live code
 * (imports, JSX tags, or plain references).  Comments and string
 * literals that happen to contain the name are tolerated.
 */
function referencesIdentifier(src: string, name: string): boolean {
  const stripped = stripComments(src)
  // Word-boundary match so `ConfigInputRemoval` (hypothetical future
  // symbol) wouldn't falsely hit `ConfigInput`.
  const pattern = new RegExp(`\\b${name}\\b`)
  return pattern.test(stripped)
}

// ═══════════════════════════════════════════════════════════════════
//  Smoke checks — the walker is actually walking
// ═══════════════════════════════════════════════════════════════════

describe("Phase 2 Wave 5 Package 5A — ConfigInput/ConfigSelect removed (#70)", () => {
  it("SRC_ROOT resolves to an existing directory with known child dirs (walker smoke)", () => {
    // If SRC_ROOT is wrong (e.g. the test file moves), every other
    // assertion silently passes.  This smoke check keeps the suite
    // honest.
    expect(existsSync(SRC_ROOT)).toBe(true)
    expect(statSync(SRC_ROOT).isDirectory()).toBe(true)
    // `panels/` and `components/` are stable top-level dirs we know
    // the frontend has.  If either disappears the walker is walking
    // the wrong tree.
    expect(existsSync(path.join(SRC_ROOT, "panels"))).toBe(true)
    expect(existsSync(path.join(SRC_ROOT, "components"))).toBe(true)
  })

  it("walker enumerates a non-trivial number of .ts/.tsx files (walker smoke)", () => {
    const files = walkFrontendSrc()
    // The frontend has well over a hundred source files today.  A
    // threshold of 50 is a conservative floor — a walker that returns
    // near zero is broken, and passing at zero would mask a missing
    // import.
    expect(
      files.length,
      `walkFrontendSrc() returned only ${files.length} files; expected >=50. Is SRC_ROOT wrong?`,
    ).toBeGreaterThanOrEqual(50)
  })

  // ═══════════════════════════════════════════════════════════════════
  //  Wrapper-file deletion
  // ═══════════════════════════════════════════════════════════════════

  it("components/form/ConfigInput.tsx no longer exists", () => {
    // Item #70 requires removing the wrapper entirely.  An empty stub
    // would still leave a dead module on disk, so we pin "does not
    // exist" outright.
    expect(
      existsSync(CONFIG_INPUT_PATH),
      `Expected ${CONFIG_INPUT_PATH} to be deleted as part of item #70; the wrapper still exists.`,
    ).toBe(false)
  })

  it("components/form/ConfigSelect.tsx no longer exists", () => {
    expect(
      existsSync(CONFIG_SELECT_PATH),
      `Expected ${CONFIG_SELECT_PATH} to be deleted as part of item #70; the wrapper still exists.`,
    ).toBe(false)
  })

  // ═══════════════════════════════════════════════════════════════════
  //  Companion unit-test deletion
  // ═══════════════════════════════════════════════════════════════════
  //
  //  The two pre-existing suites under `__tests__/components/form/`
  //  import the wrappers directly:
  //
  //    __tests__/components/form/ConfigInput.test.tsx
  //      → import ConfigInput from "../../../components/form/ConfigInput"
  //    __tests__/components/form/ConfigSelect.test.tsx
  //      → import ConfigSelect from "../../../components/form/ConfigSelect"
  //
  //  Leaving them in place after the wrappers are gone would break the
  //  whole `vitest run` with unresolved-module errors.  They have no
  //  other value once the code under test is deleted, so #70 must also
  //  remove them.  This pin catches a dev who deletes the wrappers but
  //  forgets to sweep the corresponding tests.

  it("__tests__/components/form/ConfigInput.test.tsx no longer exists", () => {
    expect(
      existsSync(CONFIG_INPUT_TEST_PATH),
      `Expected ${CONFIG_INPUT_TEST_PATH} to be deleted — its only subject (ConfigInput) is gone.`,
    ).toBe(false)
  })

  it("__tests__/components/form/ConfigSelect.test.tsx no longer exists", () => {
    expect(
      existsSync(CONFIG_SELECT_TEST_PATH),
      `Expected ${CONFIG_SELECT_TEST_PATH} to be deleted — its only subject (ConfigSelect) is gone.`,
    ).toBe(false)
  })

  // ═══════════════════════════════════════════════════════════════════
  //  Barrel re-exports
  // ═══════════════════════════════════════════════════════════════════
  //
  //  `components/form/index.ts` currently contains:
  //    export { default as ConfigInput } from "./ConfigInput"
  //    export { default as ConfigSelect } from "./ConfigSelect"
  //    export { default as ConfigCheckbox } from "./ConfigCheckbox"
  //    export { default as EditorLabel } from "./EditorLabel"
  //
  //  After #70 the first two lines must be gone.  `ConfigCheckbox` and
  //  `EditorLabel` stay — they have real consumers.

  it("components/form/index.ts still exists (EditorLabel/ConfigCheckbox survive)", () => {
    // Deleting the barrel entirely would break the five existing
    // editors that import `EditorLabel` from it.  #70 is scoped to the
    // two wrappers only.
    expect(existsSync(BARREL_PATH)).toBe(true)
  })

  it("components/form/index.ts does NOT re-export ConfigInput", () => {
    const barrel = readFileSync(BARREL_PATH, "utf8")
    const stripped = stripComments(barrel)
    // Catch any re-export form: `default as ConfigInput`, `* as …`,
    // `export { ConfigInput }`, or raw `from "./ConfigInput"`.
    expect(
      /\bConfigInput\b/.test(stripped),
      `Expected ${BARREL_PATH} to stop re-exporting ConfigInput; the name still appears.`,
    ).toBe(false)
    expect(
      /from\s+["']\.\/ConfigInput["']/.test(stripped),
      `Expected ${BARREL_PATH} to stop importing "./ConfigInput".`,
    ).toBe(false)
  })

  it("components/form/index.ts does NOT re-export ConfigSelect", () => {
    const barrel = readFileSync(BARREL_PATH, "utf8")
    const stripped = stripComments(barrel)
    expect(
      /\bConfigSelect\b/.test(stripped),
      `Expected ${BARREL_PATH} to stop re-exporting ConfigSelect; the name still appears.`,
    ).toBe(false)
    expect(
      /from\s+["']\.\/ConfigSelect["']/.test(stripped),
      `Expected ${BARREL_PATH} to stop importing "./ConfigSelect".`,
    ).toBe(false)
  })

  it("components/form/index.ts still re-exports EditorLabel and ConfigCheckbox", () => {
    // Defensive: make sure the dev didn't over-delete the barrel.
    // Five call-sites currently rely on `import { EditorLabel } from
    // "../../components/form"` via this barrel — losing that would
    // break SinkEditor, OutputEditor, ColumnsTab, GroupedColumnsTab,
    // and SubmodelEditor.
    const barrel = readFileSync(BARREL_PATH, "utf8")
    const stripped = stripComments(barrel)
    expect(
      /\bEditorLabel\b/.test(stripped),
      `EditorLabel must remain exported from ${BARREL_PATH}.`,
    ).toBe(true)
    expect(
      /\bConfigCheckbox\b/.test(stripped),
      `ConfigCheckbox must remain exported from ${BARREL_PATH}.`,
    ).toBe(true)
  })

  // ═══════════════════════════════════════════════════════════════════
  //  No file imports or references either wrapper
  // ═══════════════════════════════════════════════════════════════════

  it("no file under frontend/src/ imports ConfigInput by module path", () => {
    // Catches:
    //   import ConfigInput from "…/components/form/ConfigInput"
    //   import { ConfigInput } from "…/components/form"
    //   import { ConfigInput } from "../form"
    //   const x = await import("…/components/form/ConfigInput")
    const offenders: string[] = []
    const pathPattern = /["']([^"']*\/)?ConfigInput(\.tsx?)?["']/
    for (const f of walkFrontendSrc()) {
      const src = readFileSync(f.abs, "utf8")
      const stripped = stripComments(src)
      if (pathPattern.test(stripped)) {
        offenders.push(f.rel)
      }
    }
    expect(
      offenders,
      `These files still reference a "ConfigInput" module path:\n  ${offenders.join("\n  ")}`,
    ).toEqual([])
  })

  it("no file under frontend/src/ imports ConfigSelect by module path", () => {
    const offenders: string[] = []
    const pathPattern = /["']([^"']*\/)?ConfigSelect(\.tsx?)?["']/
    for (const f of walkFrontendSrc()) {
      const src = readFileSync(f.abs, "utf8")
      const stripped = stripComments(src)
      if (pathPattern.test(stripped)) {
        offenders.push(f.rel)
      }
    }
    expect(
      offenders,
      `These files still reference a "ConfigSelect" module path:\n  ${offenders.join("\n  ")}`,
    ).toEqual([])
  })

  it("no file under frontend/src/ references the ConfigInput identifier in live code", () => {
    // Stronger than the module-path check above: catches a dev who
    // re-introduces the wrapper under a new name/path but keeps the
    // identifier (e.g. `import ConfigInput from "./legacy/…"`).
    const offenders: string[] = []
    for (const f of walkFrontendSrc()) {
      const src = readFileSync(f.abs, "utf8")
      if (referencesIdentifier(src, "ConfigInput")) {
        offenders.push(f.rel)
      }
    }
    expect(
      offenders,
      `These files still reference the ConfigInput identifier (in live code, not comments):\n  ${offenders.join("\n  ")}`,
    ).toEqual([])
  })

  it("no file under frontend/src/ references the ConfigSelect identifier in live code", () => {
    const offenders: string[] = []
    for (const f of walkFrontendSrc()) {
      const src = readFileSync(f.abs, "utf8")
      if (referencesIdentifier(src, "ConfigSelect")) {
        offenders.push(f.rel)
      }
    }
    expect(
      offenders,
      `These files still reference the ConfigSelect identifier (in live code, not comments):\n  ${offenders.join("\n  ")}`,
    ).toEqual([])
  })

  // ═══════════════════════════════════════════════════════════════════
  //  No shared className constant was introduced (explicit non-goal)
  // ═══════════════════════════════════════════════════════════════════
  //
  //  The review item suggested "a shared className constant (if the
  //  styling is worth sharing)".  Because there are zero live
  //  consumers, #70 should NOT introduce a shared constant — doing so
  //  would add a new abstraction with no caller.  If a dev adds one
  //  anyway, they should do it as part of a package that also has a
  //  consumer; otherwise we're substituting one dead module for
  //  another.
  //
  //  We don't pin the NON-existence of a shared constant (that would
  //  be over-specifying the solution shape), but we DO document here
  //  that none is needed today.  If this test ever needs updating to
  //  accommodate a legitimate shared helper, update the comment first
  //  to record the justifying consumer.

  it("(doc-only) no shared input-style constant is required by #70", () => {
    // This test is intentionally a no-op assertion whose purpose is to
    // carry the rationale in close proximity to the other #70 pins —
    // future readers grep for "item #70" and land here.
    expect(true).toBe(true)
  })
})
