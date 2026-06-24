/**
 * Non-modal non-canonical highlight logic (PATH_GRAMMAR.md §4 — prefer, don't
 * enforce; warn without interrupting). The grammar decision is single-sourced in
 * `jsonpath.ts`; this module is only the §4 UX trigger on top of
 * {@link isCanonical} / {@link canonicalForm}, shared by BOTH path editors
 * (OUTPUT's OutputEditor, INPUT's ApiInputEditor) so the surface reads
 * identically in each.
 *
 * §4 surface: a valid but non-canonical path commits and **assembles
 * identically** — we never rewrite it (a rewrite can change meaning). Instead
 * each editor **persistently highlights** its non-canonical fields, whether the
 * user typed them or schema inference introduced them (the case the old commit-
 * time modal missed), so the user can see *what* and *where*. It is
 * **informational only** — there is no single fix (a non-identifier key has no
 * safe canonical spelling, and the real fix is often upstream), so no
 * call-to-action and no per-path rewrite button. The bulk normalise-on-import
 * affordance stays deferred (§4).
 *
 * This replaced a per-session-global modal that fired on manual path commit;
 * the modal interrupted, only covered manual entry, and offered a rewrite the
 * spec says not to. None of that machinery (the sessionStorage dismissal, the
 * React context/provider, the modal JSX) survives — a derived, always-on
 * highlight needs neither dismissal nor a commit-boundary notifier.
 */

import { RESERVED_LEAF, canonicalForm, isCanonical, parsePath } from "./jsonpath"

/** What to surface for a non-canonical field. */
export interface NonCanonicalHint {
  /** The safe canonical spelling, shown as an informational hint, or `null` when
   * none exists (the §5 non-identifier designed-out case — the field is still
   * highlighted, just without a canonical form to name). */
  canonical: string | null
}

/**
 * The non-canonical highlight for a committed `path`, or `null` when none should
 * show. Returns non-null iff the path is non-canonical (`!isCanonical`).
 *
 * Call this ONLY for a path that already passed its editor's grammar/root
 * validation — the caller surfaces an invalid path as a grammar error
 * separately. `isCanonical` returns false for BOTH invalid and valid-but-
 * non-canonical paths, so the caller's "valid" guard (its `error === null`) is
 * what distinguishes them. An empty path never highlights.
 */
export function nonCanonicalHint(path: string): NonCanonicalHint | null {
  if (!path) return null
  // INPUT column paths may end in the reserved `$value` sentinel, which is NOT a
  // JSON identifier — the identifier-pure `parsePath` / `isCanonical` /
  // `canonicalForm` all throw or null on it (only `parseDataPath` peels it). Peel
  // a trailing `.$value` the same way before judging canonicality, then re-append
  // it to the canonical form, so a `$value`-leaf path is highlighted on its real
  // body (e.g. `$[:]['claims'][:].$value` → canonical `$[:].claims[:].$value`).
  // OUTPUT paths never carry `$value`, so this is a no-op there (byte-identical).
  const sentinel = path.endsWith(`.${RESERVED_LEAF}`) ? `.${RESERVED_LEAF}` : ""
  const body = sentinel ? path.slice(0, -sentinel.length) : path
  // A body that does not even parse is *invalid*, not non-canonical — its grammar
  // error is the right surface, so never highlight it (defence in depth: callers
  // already gate on validity, but this keeps the predicate honest if one forgets).
  // A body that parses but fails a side-specific gate (the OUTPUT `$[:]` root, the
  // INPUT leaf rule) is still caught by the caller's `error === null` guard.
  try {
    parsePath(body)
  } catch {
    return null
  }
  if (isCanonical(body)) return null
  const canonical = canonicalForm(body)
  return { canonical: canonical === null ? null : canonical + sentinel }
}

/**
 * The informational note for a non-canonical field — wording shared by both path
 * editors. Names the canonical form where one exists; for the §5 non-identifier
 * case there is no safe rewrite, so the note just states the field is
 * non-canonical (the signal to fix the key upstream).
 */
export function nonCanonicalNote(hint: NonCanonicalHint): string {
  return hint.canonical !== null
    ? `Non-canonical path — assembles identically. Canonical form: ${hint.canonical}`
    : "Non-canonical path — assembles identically (no simpler spelling exists for non-identifier keys)."
}
