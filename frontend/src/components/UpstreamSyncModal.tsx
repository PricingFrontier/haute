import { useEffect, useState } from "react"

import type { GitUpstreamStatus } from "../api/types"
import { useRequestKind } from "../hooks/useRequestKind"
import useGitStore, { gitStorePart } from "../stores/useGitStore"
import useToastStore from "../stores/useToastStore"
import { apiErrorMessage } from "../api/errors"
import { answer, ask, newest, under } from "../utils/answers"
import ModalShell from "./ModalShell"

interface UpstreamSyncModalProps {
  onClose: () => void
}

/**
 * A fork's relationship to the project it was forked from: how far apart they
 * are, and a catch-up when that catch-up is a clean fast-forward.
 *
 * Three states, because there are only three honest answers. Up to date is a
 * statement, not an action. Behind offers the catch-up — a pure ref advance,
 * so nothing of the user's can be lost by it. Both-moved is a plain
 * explanation with no button: merging divergent history is out of scope by
 * design, and offering an action that cannot work would be worse than saying
 * so.
 *
 * The check is deliberately made here rather than on the readiness poll: it
 * downloads the parent's whole stored bundle.
 */
export default function UpstreamSyncModal({ onClose }: UpstreamSyncModalProps) {
  const checkUpstream = useGitStore((s) => s.checkUpstream)
  const pullUpstream = useGitStore((s) => s.pullUpstream)
  const addToast = useToastStore((s) => s.addToast)

  const [status, setStatus] = useState<GitUpstreamStatus | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [checking, setChecking] = useState(true)
  const [busy, setBusy] = useState(false)
  const checks = useRequestKind()
  const life = useRequestKind()

  // State is written from the answer's handlers, never synchronously in the
  // effect body — the check is a network round trip, so "checking" is the
  // component's initial state rather than something an effect switches on.
  useEffect(() => {
    const asked = ask([newest(checks)])
    void asked.answer(checkUpstream(), {
      ok: (result) => {
        setStatus(result)
        setChecking(false)
      },
      failed: (err) => {
        setError(apiErrorMessage(err, "Could not compare this project with its parent"))
        setChecking(false)
      },
    })
    return () => asked.release()
  }, [checkUpstream, checks])

  const catchUp = async () => {
    if (busy) return
    setError(null)
    // The catch-up and its toast belong to the git store; the dialog's error and its close,
    // to the dialog.
    const here = ask([under(life)])
    try {
      await answer([gitStorePart()], pullUpstream(), {
        ok: () => {
          addToast("success", "Caught up with the parent project.")
          here.apply(onClose)
        },
        failed: (err) => here.apply(() => setError(apiErrorMessage(err, "Could not catch up with the parent project"))),
      }, { inFlight: setBusy })
    } finally {
      here.release()
    }
  }

  return (
    <ModalShell
      ariaLabel="Compare with the parent project"
      onClose={onClose}
      width="w-[440px]"
      testId="upstream-sync-modal"
    >
      <div className="px-4 py-3" style={{ borderBottom: "1px solid var(--border)" }}>
        <h2 className="text-sm font-semibold" style={{ color: "var(--text-primary)" }}>
          The project this one was forked from
        </h2>
        <p className="text-[12px] mt-0.5" style={{ color: "var(--text-muted)" }}>
          Changes travel one way - from the parent into this copy. Your own work is never
          published back to it.
        </p>
      </div>

      <div className="p-4 flex flex-col gap-3">
        {checking ? (
          <p
            className="text-[12px] px-2.5 py-1.5 rounded-md"
            style={{ background: "var(--bg-input)", color: "var(--text-muted)" }}
            data-testid="upstream-sync-checking"
          >
            Comparing with the parent project…
          </p>
        ) : null}

        {error && (
          <p
            className="text-[12px] px-2.5 py-1.5 rounded-md"
            style={{ background: "var(--bg-input)", color: "var(--danger)" }}
            data-testid="upstream-sync-error"
          >
            {error}
          </p>
        )}

        {status && (
          <>
            <p
              className="text-[12px] px-2.5 py-1.5 rounded-md"
              style={{ background: "var(--bg-input)", color: "var(--text-primary)" }}
              data-testid="upstream-sync-message"
            >
              {status.message}
            </p>
            <p className="text-[11px]" style={{ color: "var(--text-muted)" }}>
              Compared against generation {status.parent_generation} of {status.parent_url}.
            </p>
          </>
        )}

        <div className="flex justify-end gap-2 pt-1">
          <button
            type="button"
            onClick={onClose}
            className="px-3 py-1.5 text-[12px] font-medium rounded-md transition-colors"
            style={{ color: "var(--text-secondary)" }}
          >
            Close
          </button>
          {status?.can_fast_forward && (
            <button
              type="button"
              data-testid="upstream-sync-confirm"
              disabled={busy}
              onClick={() => void catchUp()}
              className="px-4 py-1.5 text-[12px] font-semibold rounded-md transition-colors disabled:opacity-50 disabled:cursor-not-allowed hover:bg-[var(--structure-action-hover)] disabled:hover:bg-[var(--structure-action)]"
              style={{ background: "var(--structure-action)", color: "var(--text-on-accent)" }}
            >
              {busy ? "Catching up…" : "Catch up"}
            </button>
          )}
        </div>
      </div>
    </ModalShell>
  )
}
