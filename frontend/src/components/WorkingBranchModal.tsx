import { useState } from "react"

import { setGitIdentity, setWorkingBranch } from "../api/client"
import useGitStore from "../stores/useGitStore"
import useToastStore from "../stores/useToastStore"
import { apiErrorMessage } from "../api/errors"
import { GitIdentityFields, ModalFormActions, ModalFormHeader, ModalTextInput } from "./ModalForm"
import ModalShell from "./ModalShell"

interface WorkingBranchModalProps {
  /** Called after the working branch has been set and status reloaded. */
  onConfirmed: () => void
  onClose: () => void
}

const CREATE_SENTINEL = "__create__"

/**
 * Startup / save-gate modal (S5, S13, S27): choose the working branch for this
 * clone. Shows the eligible existing branches plus a "create new" option, and —
 * when git commit identity is missing — an inline identity sub-form with a
 * set-globally checkbox (question 3). Confirming records the branch (spawning
 * its ledger) and, when a save was queued behind it, lets that save proceed.
 */
export default function WorkingBranchModal({ onConfirmed, onClose }: WorkingBranchModalProps) {
  const status = useGitStore((s) => s.status)
  const loadStatus = useGitStore((s) => s.loadStatus)
  const addToast = useToastStore((s) => s.addToast)

  const eligible = status?.eligible_branches ?? []
  const identityNeeded = status ? !status.identity_set : false

  // Default selection (S5/S27): the currently checked-out branch when it's
  // eligible, else the first eligible branch, else "create new".
  const current = status?.current_branch ?? ""
  const defaultChoice = eligible.includes(current)
    ? current
    : (eligible[0] ?? CREATE_SENTINEL)
  const [choice, setChoice] = useState<string>(defaultChoice)
  const [newBranch, setNewBranch] = useState("")
  const [userName, setUserName] = useState(status?.user_name ?? "")
  const [userEmail, setUserEmail] = useState(status?.user_email ?? "")
  const [setGlobal, setSetGlobal] = useState(false)
  const [busy, setBusy] = useState(false)

  const creating = choice === CREATE_SENTINEL
  const branchName = creating ? newBranch.trim() : choice
  const identityOk = !identityNeeded || (userName.trim() !== "" && userEmail.trim() !== "")
  const canSubmit = branchName !== "" && identityOk && !busy

  const submit = async () => {
    if (!canSubmit) return
    setBusy(true)
    try {
      if (identityNeeded) {
        await setGitIdentity(userName.trim(), userEmail.trim(), setGlobal)
      }
      await setWorkingBranch(branchName, creating)
      await loadStatus()
      addToast("success", `Working branch set to ${branchName}`)
      onConfirmed()
    } catch (err: unknown) {
      const detail = apiErrorMessage(err, "unknown error")
      addToast("error", `Could not set working branch: ${detail}`)
    } finally {
      setBusy(false)
    }
  }

  const invalidReason = status?.state === "invalid" ? status.errors[0] : null

  return (
    <ModalShell
      ariaLabel="Choose working branch"
      onClose={onClose}
      width="w-[440px]"
      testId="working-branch-modal"
    >
      <ModalFormHeader title="Choose a working branch">
        Saves are recorded against this branch. You can change it later from the Git panel.
      </ModalFormHeader>

      <form
        className="p-4 flex flex-col gap-3"
        onSubmit={(e) => {
          e.preventDefault()
          void submit()
        }}
      >
        {invalidReason && (
          <p
            className="text-[12px] px-2.5 py-1.5 rounded-md"
            style={{ background: "var(--bg-input)", color: "var(--danger)" }}
          >
            {invalidReason}
          </p>
        )}

        <div>
          <label
            htmlFor="working-branch-select"
            className="text-[11px] font-medium block mb-1"
            style={{ color: "var(--text-muted)" }}
          >
            Branch
          </label>
          <select
            id="working-branch-select"
            data-testid="working-branch-select"
            value={choice}
            onChange={(e) => setChoice(e.target.value)}
            className="w-full px-2.5 py-1.5 text-[13px] rounded-md focus:outline-none focus:ring-2"
            style={{
              background: "var(--bg-input)",
              border: "1px solid var(--border)",
              color: "var(--text-primary)",
            }}
          >
            {eligible.map((b) => (
              <option key={b} value={b}>
                {b}
              </option>
            ))}
            <option value={CREATE_SENTINEL}>+ Create new branch…</option>
          </select>
        </div>

        {creating && (
          <div>
            <label
              htmlFor="working-branch-new"
              className="text-[11px] font-medium block mb-1"
              style={{ color: "var(--text-muted)" }}
            >
              New branch name
            </label>
            <ModalTextInput
              id="working-branch-new"
              data-testid="working-branch-new"
              value={newBranch}
              onChange={(e) => setNewBranch(e.target.value)}
              autoFocus
              placeholder="e.g. motor-pricing-2026"
            />
          </div>
        )}

        {identityNeeded && (
          <div
            className="flex flex-col gap-2 pt-2"
            style={{ borderTop: "1px solid var(--border)" }}
          >
            <p className="text-[12px]" style={{ color: "var(--text-secondary)" }}>
              Git needs a name and email to record saves.
            </p>
            <GitIdentityFields
              testIdPrefix="identity"
              name={userName}
              email={userEmail}
              setGlobal={setGlobal}
              onNameChange={setUserName}
              onEmailChange={setUserEmail}
              onSetGlobalChange={setSetGlobal}
            />
          </div>
        )}

        <ModalFormActions
          onCancel={onClose}
          submitLabel="Use this branch"
          busyLabel="Setting…"
          busy={busy}
          disabled={!canSubmit}
          submitTestId="working-branch-confirm"
        />
      </form>
    </ModalShell>
  )
}
