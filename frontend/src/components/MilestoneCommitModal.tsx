import { useState } from "react"
import { AlertTriangle } from "lucide-react"

import { ApiError, commitMilestone } from "../api/client"
import type { GitMilestoneFork } from "../api/types"
import { parseGitMilestoneFork } from "../types/guards"
import useGitStore from "../stores/useGitStore"
import useToastStore from "../stores/useToastStore"
import { gitErrorMessage } from "../utils/gitError"
import ModalShell from "./ModalShell"

interface MilestoneCommitModalProps {
  onConfirmed: () => void
  onClose: () => void
}

const MAX_MESSAGE_LENGTH = 500

/**
 * Save & commit (S7/S18): record a milestone on the working branch by merging
 * the ledger's accumulated saves. The user supplies a required message (rides
 * the merge commit) and an optional version label (an annotated git tag — the
 * actuarial "_v2.0_FINAL" instinct, given a sound substrate).
 */
export default function MilestoneCommitModal({ onConfirmed, onClose }: MilestoneCommitModalProps) {
  const status = useGitStore((s) => s.status)
  const loadStatus = useGitStore((s) => s.loadStatus)
  const notifyMilestoneCommitted = useGitStore((s) => s.notifyMilestoneCommitted)
  const addToast = useToastStore((s) => s.addToast)

  const [message, setMessage] = useState("")
  const [versionLabel, setVersionLabel] = useState("")
  const [busy, setBusy] = useState(false)
  // U4/D4: set when save&commit would fork the remote — drives the warn +
  // "commit anyway (creates a fork)" confirm instead of a generic error.
  const [fork, setFork] = useState<GitMilestoneFork | null>(null)

  const trimmed = message.trim()
  // The flow only opens this modal once the working branch is ready, but guard
  // defensively: a non-ready status means there's nothing valid to commit onto.
  const ready = !status || status.state === "ready"
  const canSubmit =
    ready && trimmed !== "" && trimmed.length <= MAX_MESSAGE_LENGTH && !busy

  const submit = async (allowFork = false) => {
    if (!canSubmit) return
    setBusy(true)
    try {
      const label = versionLabel.trim() || null
      const result = await commitMilestone(trimmed, label, { allowFork })
      await loadStatus()
      notifyMilestoneCommitted() // refresh an open Git panel + select the new milestone (S38)
      addToast(
        "success",
        label
          ? `Committed milestone ${result.short_sha} (${label})`
          : `Committed milestone ${result.short_sha}`,
      )
      onConfirmed()
    } catch (err: unknown) {
      // U4/D4: a 409 means committing now would fork the remote — surface the
      // warn + "commit anyway" confirm rather than a dead-end error toast.
      if (err instanceof ApiError && err.status === 409) {
        const parsed = parseGitMilestoneFork(
          (err.body as { detail?: unknown } | undefined)?.detail,
        )
        if (parsed) {
          setFork(parsed)
          return
        }
      }
      const detail = gitErrorMessage(err, "unknown error")
      addToast("error", `Could not commit: ${detail}`)
    } finally {
      setBusy(false)
    }
  }

  return (
    <ModalShell
      ariaLabel="Save and commit a milestone"
      onClose={onClose}
      width="w-[460px]"
      testId="milestone-commit-modal"
    >
      <div className="px-4 py-3" style={{ borderBottom: "1px solid var(--border)" }}>
        <h2 className="text-sm font-semibold" style={{ color: "var(--text-primary)" }}>
          Save &amp; commit
        </h2>
        <p className="text-[12px] mt-0.5" style={{ color: "var(--text-muted)" }}>
          Record a milestone on{" "}
          <span className="font-mono">{status?.working_branch ?? "your working branch"}</span> from
          the saves since the last one.
        </p>
      </div>

      <form
        className="p-4 flex flex-col gap-3"
        onSubmit={(e) => {
          e.preventDefault()
          if (!fork) void submit()
        }}
      >
        <div>
          <label
            htmlFor="milestone-message"
            className="text-[11px] font-medium block mb-1"
            style={{ color: "var(--text-muted)" }}
          >
            What changed in this version?
          </label>
          <textarea
            id="milestone-message"
            data-testid="milestone-message"
            value={message}
            onChange={(e) => setMessage(e.target.value)}
            autoFocus
            rows={3}
            placeholder="e.g. New age banding + competitor uplift"
            className="w-full px-3 py-1.5 text-[13px] rounded-md focus:outline-none focus:ring-2 resize-none"
            style={{
              background: "var(--bg-input)",
              border: "1px solid var(--border)",
              color: "var(--text-primary)",
              caretColor: "var(--accent)",
            }}
            aria-invalid={!canSubmit && trimmed !== ""}
            aria-describedby={trimmed.length > MAX_MESSAGE_LENGTH ? "milestone-message-limit" : undefined}
          />
          {trimmed.length > MAX_MESSAGE_LENGTH && (
            <p id="milestone-message-limit" role="alert" className="text-[11px] mt-1" style={{ color: "var(--danger)" }}>
              Milestone messages must be 500 characters or fewer.
            </p>
          )}
        </div>

        <div>
          <label
            htmlFor="milestone-version"
            className="text-[11px] font-medium block mb-1"
            style={{ color: "var(--text-muted)" }}
          >
            Version label (optional)
          </label>
          <input
            id="milestone-version"
            data-testid="milestone-version"
            value={versionLabel}
            onChange={(e) => setVersionLabel(e.target.value)}
            placeholder="e.g. 2.1"
            className="w-full px-3 py-1.5 text-[13px] rounded-md focus:outline-none focus:ring-2"
            style={{
              background: "var(--bg-input)",
              border: "1px solid var(--border)",
              color: "var(--text-primary)",
              caretColor: "var(--accent)",
            }}
          />
        </div>

        {fork ? (
          <div data-testid="milestone-fork-confirm" className="flex flex-col gap-2 pt-1">
            <div
              className="flex items-start gap-1.5 text-[12px]"
              style={{ color: "var(--danger)" }}
            >
              <AlertTriangle size={14} className="mt-0.5 shrink-0" />
              <span>{fork.message}</span>
            </div>
            <div className="flex justify-end gap-2">
              <button
                type="button"
                onClick={() => setFork(null)}
                className="px-3 py-1.5 text-[12px] font-medium rounded-md transition-colors"
                style={{ color: "var(--text-secondary)" }}
              >
                Back
              </button>
              <button
                type="button"
                data-testid="milestone-fork-anyway"
                disabled={busy}
                onClick={() => void submit(true)}
                className="px-4 py-1.5 text-[12px] font-semibold rounded-md transition-colors disabled:opacity-50"
                style={{ background: "var(--danger)", color: "var(--text-on-accent)" }}
              >
                {busy ? "Committing…" : "Commit anyway (creates a fork)"}
              </button>
            </div>
          </div>
        ) : (
          <div className="flex justify-end gap-2 pt-1">
            <button
              type="button"
              onClick={onClose}
              className="px-3 py-1.5 text-[12px] font-medium rounded-md transition-colors"
              style={{ color: "var(--text-secondary)" }}
            >
              Cancel
            </button>
            <button
              type="submit"
              data-testid="milestone-confirm"
              disabled={!canSubmit}
              className="px-4 py-1.5 text-[12px] font-semibold rounded-md transition-colors disabled:opacity-50 disabled:cursor-not-allowed hover:bg-[var(--structure-action-hover)] disabled:hover:bg-[var(--structure-action)]"
              style={{ background: "var(--structure-action)", color: "var(--text-on-accent)" }}
            >
              {busy ? "Committing…" : "Commit"}
            </button>
          </div>
        )}
      </form>
    </ModalShell>
  )
}
