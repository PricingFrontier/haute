/**
 * Logic for the per-session-global "there's a simpler way" non-canonical path
 * warning (PATH_GRAMMAR.md §4 — prefer, don't enforce; warn). The JSX shell
 * (modal + provider) lives in `PathWarningModal.tsx`; this module is the
 * component-free core (storage, the trigger predicate, the context + hook) so
 * fast-refresh stays happy and the predicate is unit-testable in isolation.
 *
 * Shared by BOTH path editors (OUTPUT's OutputEditor, INPUT's ApiInputEditor) so
 * one dismissal silences the warning for the whole session, across both editors
 * (per-session-GLOBAL). The grammar decision is single-sourced in `jsonpath.ts`;
 * this module is only the UX trigger on top of {@link isCanonical} /
 * {@link canonicalForm}.
 *
 * Trigger contract (PATH_GRAMMAR.md §4 / §5):
 *   - The committed path is VALID (the editor already refused invalid commits).
 *   - It is NON-canonical (`!isCanonical`) AND has a SAFE canonical form
 *     (`canonicalForm !== null`). A non-identifier bracket name
 *     (`canonicalForm === null`, the §5 designed-out case) does NOT warn — there
 *     is no safe rewrite to offer.
 *   - The user has not dismissed the warning this session.
 */

import { createContext, useContext } from "react"
import { canonicalForm, isCanonical } from "./jsonpath"

// ─── per-session-GLOBAL dismissal ─────────────────────────────────
//
// One sessionStorage key, shared by both editors: a single "Don't show this
// again this session" silences the warning everywhere until the tab/session is
// gone. sessionStorage (not localStorage) so a fresh session re-surfaces it.

const DISMISS_KEY = "haute.pathCanonicalWarning.dismissed"

/** True once the user has dismissed the non-canonical warning this session. */
export function isPathWarningDismissed(): boolean {
  try {
    return sessionStorage.getItem(DISMISS_KEY) === "1"
  } catch {
    // sessionStorage can throw (privacy mode / disabled storage). Treat as
    // not-dismissed — the warning is advisory, so failing open is harmless.
    return false
  }
}

/** Persist the per-session-global dismissal. */
export function dismissPathWarning(): void {
  try {
    sessionStorage.setItem(DISMISS_KEY, "1")
  } catch {
    // Non-fatal — see isPathWarningDismissed.
  }
}

/**
 * The canonical form to warn about for a just-committed `path`, or `null` when
 * NO warning should fire. Returns non-null iff the path is valid, non-canonical,
 * has a safe canonical rewrite, AND the session-global dismissal is not set.
 *
 * This is the single trigger predicate both editors call on commit.
 */
export function pathWarningTarget(path: string): string | null {
  if (isPathWarningDismissed()) return null
  if (isCanonical(path)) return null
  const canonical = canonicalForm(path)
  if (canonical === null) return null // §5: no safe rewrite — don't warn
  return canonical
}

// ─── editor-scoped wiring ─────────────────────────────────────────
//
// Both path editors nest their path inputs several components deep, so the
// trigger (called at the commit boundary) and the modal (rendered at the editor
// root) are connected via a small context rather than threaded props. The
// provider (PathWarningModal.tsx) owns the single modal instance for its editor.

export type NotifyCommittedPath = (path: string) => void

/** Internal context — the provider sets it, {@link usePathWarning} reads it. */
export const PathWarningContext = createContext<NotifyCommittedPath>(() => {})

/**
 * The commit-boundary hook: returns a `notify(path)` to call AFTER a path
 * commits. The provider's `notify` checks {@link pathWarningTarget} and pops the
 * advisory modal when warranted. A no-op outside a `PathWarningProvider`.
 */
export function usePathWarning(): NotifyCommittedPath {
  return useContext(PathWarningContext)
}
