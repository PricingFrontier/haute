import { useState } from "react"

import { setGitIdentity } from "../api/client"
import { dismissIdentityPrompt } from "../stores/identityPrompt"
import useGitStore from "../stores/useGitStore"
import useToastStore from "../stores/useToastStore"
import { gitErrorMessage } from "../utils/gitError"
import ConfigCheckbox from "./form/ConfigCheckbox"
import ModalShell from "./ModalShell"

interface IdentityPromptModalProps {
  /** Called after an identity is recorded, to retry the save that was left
   *  uncaptured. */
  onSaved: () => void
  onClose: () => void
}

/**
 * Save-time git identity prompt. A restored hosted container has no commit
 * identity, so saves land on disk but are never version-captured; the save
 * response says so with `identity_required`, and this dialog is how the user
 * fixes it without hunting through the Git panel.
 *
 * Dismissing it records a session-level "don't ask again" (see
 * `stores/identityPrompt`) — the save warning still appears each time, but the
 * dialog does not reappear on every autosave.
 */
export default function IdentityPromptModal({ onSaved, onClose }: IdentityPromptModalProps) {
  const status = useGitStore((s) => s.status)
  const loadStatus = useGitStore((s) => s.loadStatus)
  const addToast = useToastStore((s) => s.addToast)

  const [userName, setUserName] = useState(status?.user_name ?? "")
  const [userEmail, setUserEmail] = useState(status?.user_email ?? "")
  const [setGlobal, setSetGlobal] = useState(false)
  const [busy, setBusy] = useState(false)

  const canSubmit = userName.trim() !== "" && userEmail.trim() !== "" && !busy

  const dismiss = () => {
    dismissIdentityPrompt()
    onClose()
  }

  const submit = async () => {
    if (!canSubmit) return
    setBusy(true)
    try {
      await setGitIdentity(userName.trim(), userEmail.trim(), setGlobal)
      await loadStatus()
      onClose()
      onSaved()
    } catch (err: unknown) {
      addToast("error", `Could not set your git identity: ${gitErrorMessage(err, "unknown error")}`)
    } finally {
      setBusy(false)
    }
  }

  return (
    <ModalShell
      ariaLabel="Set git identity"
      onClose={dismiss}
      width="w-[420px]"
      testId="identity-prompt-modal"
    >
      <div className="px-4 py-3" style={{ borderBottom: "1px solid var(--border)" }}>
        <h2 className="text-sm font-semibold" style={{ color: "var(--text-primary)" }}>
          Set your name and email
        </h2>
        <p className="text-[12px] mt-0.5" style={{ color: "var(--text-muted)" }}>
          Your changes are saved, but version history needs a name and email to record who made
          them.
        </p>
      </div>

      <form
        className="p-4 flex flex-col gap-2"
        onSubmit={(e) => {
          e.preventDefault()
          void submit()
        }}
      >
        <input
          data-testid="identity-prompt-name"
          value={userName}
          onChange={(e) => setUserName(e.target.value)}
          autoFocus
          placeholder="Your name"
          className="w-full px-3 py-1.5 text-[13px] rounded-md focus:outline-none focus:ring-2"
          style={{
            background: "var(--bg-input)",
            border: "1px solid var(--border)",
            color: "var(--text-primary)",
            caretColor: "var(--accent)",
          }}
        />
        <input
          data-testid="identity-prompt-email"
          type="email"
          value={userEmail}
          onChange={(e) => setUserEmail(e.target.value)}
          placeholder="you@example.com"
          className="w-full px-3 py-1.5 text-[13px] rounded-md focus:outline-none focus:ring-2"
          style={{
            background: "var(--bg-input)",
            border: "1px solid var(--border)",
            color: "var(--text-primary)",
            caretColor: "var(--accent)",
          }}
        />
        <ConfigCheckbox
          checked={setGlobal}
          onChange={setSetGlobal}
          label="Use this identity for all my projects (global git config)"
        />

        <div className="flex justify-end gap-2 pt-1">
          <button
            type="button"
            onClick={dismiss}
            className="px-3 py-1.5 text-[12px] font-medium rounded-md transition-colors"
            style={{ color: "var(--text-secondary)" }}
          >
            Not now
          </button>
          <button
            type="submit"
            data-testid="identity-prompt-confirm"
            disabled={!canSubmit}
            className="px-4 py-1.5 text-[12px] font-semibold rounded-md transition-colors disabled:opacity-50 disabled:cursor-not-allowed hover:bg-[var(--structure-action-hover)] disabled:hover:bg-[var(--structure-action)]"
            style={{ background: "var(--structure-action)", color: "var(--text-on-accent)" }}
          >
            {busy ? "Saving…" : "Save and capture version"}
          </button>
        </div>
      </form>
    </ModalShell>
  )
}
