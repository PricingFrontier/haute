/**
 * The per-session-global "there's a simpler way" warning modal + its provider
 * for a VALID but NON-canonical path (PATH_GRAMMAR.md §4 — prefer, don't
 * enforce; warn). The component-free logic (storage, trigger predicate, context
 * + hook) lives in `pathCanonicalWarning.ts`; this file is the JSX shell.
 *
 * The path STILL commits regardless — the modal is advisory. The active
 * normalise actions ("Use the simpler form" / "Use it everywhere") are DEFERRED
 * (§4): this modal renders only a single "Got it" acknowledgement button.
 */

import { useCallback, useMemo, useState, type ReactNode } from "react"
import ModalShell from "../../components/ModalShell"
import {
  PathWarningContext,
  dismissPathWarning,
  pathWarningTarget,
  type NotifyCommittedPath,
} from "./pathCanonicalWarning"

export interface NonCanonicalPathModalProps {
  /** The user's committed (valid, non-canonical) path. */
  userPath: string
  /** The safe canonical equivalent (`canonicalForm(userPath)`, ≠ null). */
  canonicalPath: string
  /** Close the modal (acknowledge). */
  onClose: () => void
}

/**
 * The advisory modal: "this works, but there's a simpler way." Shows the user's
 * path and the canonical form side by side, notes they are functionally
 * equivalent, and offers a per-session-global "don't show again" checkbox.
 *
 * The active rewrite actions are DEFERRED — only a "Got it" button is rendered.
 */
export default function NonCanonicalPathModal({
  userPath,
  canonicalPath,
  onClose,
}: NonCanonicalPathModalProps) {
  // Local checkbox state; the global dismissal is written on close so the
  // checkbox only takes effect when the user actually acknowledges.
  const [dontShowAgain, setDontShowAgain] = useState(false)

  const acknowledge = () => {
    if (dontShowAgain) dismissPathWarning()
    onClose()
  }

  return (
    <ModalShell
      ariaLabel="A simpler path form is available"
      onClose={acknowledge}
      width="w-[420px]"
      testId="path-canonical-warning"
    >
      <div className="flex flex-col gap-3 p-4">
        <div className="text-sm font-semibold" style={{ color: "var(--text-primary)" }}>
          This works, but there&apos;s a simpler way
        </div>
        <div className="text-xs leading-snug" style={{ color: "var(--text-muted)" }}>
          Your path is valid and has been saved. There&apos;s a functionally
          equivalent, easier-to-read spelling we&apos;ve standardised on — the two
          point at exactly the same data.
        </div>
        <div className="flex flex-col gap-2">
          <div>
            <div className="text-[10px] uppercase tracking-wide" style={{ color: "var(--text-muted)" }}>
              Your path
            </div>
            <code
              data-testid="path-canonical-warning-user"
              className="block text-[11px] font-mono px-1.5 py-1 rounded break-all"
              style={{ background: "var(--bg-input)", color: "var(--text-primary)" }}
            >
              {userPath}
            </code>
          </div>
          <div>
            <div className="text-[10px] uppercase tracking-wide" style={{ color: "var(--text-muted)" }}>
              Standard form
            </div>
            <code
              data-testid="path-canonical-warning-canonical"
              className="block text-[11px] font-mono px-1.5 py-1 rounded break-all"
              style={{ background: "var(--bg-input)", color: "var(--text-primary)" }}
            >
              {canonicalPath}
            </code>
          </div>
        </div>
        <label className="flex items-center gap-2 text-[11px]" style={{ color: "var(--text-muted)" }}>
          <input
            data-testid="path-canonical-warning-dismiss"
            type="checkbox"
            checked={dontShowAgain}
            onChange={(e) => setDontShowAgain(e.target.checked)}
          />
          Don&apos;t show this again this session
        </label>
        <div className="flex justify-end gap-2">
          {/* Active normalise actions ("Use the simpler form" / "Use it
              everywhere") are DEFERRED (PATH_GRAMMAR.md §4) — stubbed to a
              single acknowledgement for now. */}
          <button
            data-testid="path-canonical-warning-ok"
            onClick={acknowledge}
            className="text-xs font-semibold px-3 py-1 rounded"
            style={{ background: "var(--accent)", color: "var(--text-on-accent)" }}
          >
            Got it
          </button>
        </div>
      </div>
    </ModalShell>
  )
}

/**
 * Wrap an editor subtree so its committed paths can raise the non-canonical
 * warning modal. Renders the single modal instance; provides the commit-boundary
 * `notify` via context (read with `usePathWarning` from `pathCanonicalWarning`).
 */
export function PathWarningProvider({ children }: { children: ReactNode }) {
  const [target, setTarget] = useState<{ userPath: string; canonicalPath: string } | null>(null)

  const notify = useCallback<NotifyCommittedPath>((path) => {
    const canonical = pathWarningTarget(path)
    if (canonical !== null) setTarget({ userPath: path, canonicalPath: canonical })
  }, [])

  const ctx = useMemo(() => notify, [notify])

  return (
    <PathWarningContext.Provider value={ctx}>
      {children}
      {target && (
        <NonCanonicalPathModal
          userPath={target.userPath}
          canonicalPath={target.canonicalPath}
          onClose={() => setTarget(null)}
        />
      )}
    </PathWarningContext.Provider>
  )
}
