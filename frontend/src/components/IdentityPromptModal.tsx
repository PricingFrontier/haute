import { useState } from "react"

import { setGitIdentity } from "../api/client"
import { dismissIdentityPrompt } from "../stores/identityPrompt"
import { useRequestKind } from "../hooks/useRequestKind"
import { captureUncaptured } from "../stores/saveCapture"
import useGitStore, { gitStorePart } from "../stores/useGitStore"
import useToastStore from "../stores/useToastStore"
import { apiErrorMessage } from "../api/errors"
import { answer, ask, under } from "../utils/answers"
import { GitIdentityFields, ModalFormActions, ModalFormHeader } from "./ModalForm"
import ModalShell from "./ModalShell"

interface IdentityPromptModalProps {
  onClose: () => void
}

/**
 * Save-time git identity prompt. A restored hosted container has no commit
 * identity, so saves land on disk but are never version-captured; the save
 * response says so with `identity_required`, and this dialog is how the user
 * fixes it without hunting through the Git panel. Once an identity is recorded,
 * every file a write left uncaptured is captured (`captureUncaptured`).
 *
 * Dismissing it records a session-level "don't ask again" (see
 * `stores/identityPrompt`) — the save warning still appears each time, but the
 * dialog does not reappear on every autosave.
 */
export default function IdentityPromptModal({ onClose }: IdentityPromptModalProps) {
  const status = useGitStore((s) => s.status)
  const loadStatus = useGitStore((s) => s.loadStatus)
  const addToast = useToastStore((s) => s.addToast)

  const [userName, setUserName] = useState(status?.user_name ?? "")
  const [userEmail, setUserEmail] = useState(status?.user_email ?? "")
  const [setGlobal, setSetGlobal] = useState(false)
  const [busy, setBusy] = useState(false)
  const life = useRequestKind()

  const canSubmit = userName.trim() !== "" && userEmail.trim() !== "" && !busy

  const dismiss = () => {
    dismissIdentityPrompt()
    onClose()
  }

  const submit = async () => {
    if (!canSubmit) return
    // The identity and the captures it lets go ahead belong to the git store; the close, to
    // this dialog.
    const here = ask([under(life)])
    try {
      await answer([gitStorePart()], setGitIdentity(userName.trim(), userEmail.trim(), setGlobal), {
        ok: (_, asked) => asked.answer(loadStatus({ refresh: true }), { // not a read begun before the action
          ok: () => {
            here.apply(onClose)
            void captureUncaptured()
          },
        }),
        failed: (err) => addToast("error", `Could not set your git identity: ${apiErrorMessage(err, "unknown error")}`),
      }, { inFlight: setBusy })
    } finally {
      here.release()
    }
  }

  return (
    <ModalShell
      ariaLabel="Set git identity"
      onClose={dismiss}
      width="w-[420px]"
      testId="identity-prompt-modal"
    >
      <ModalFormHeader title="Set your name and email">
        Your changes are saved, but version history needs a name and email to record who made
        them.
      </ModalFormHeader>

      <form
        className="p-4 flex flex-col gap-2"
        onSubmit={(e) => {
          e.preventDefault()
          void submit()
        }}
      >
        <GitIdentityFields
          testIdPrefix="identity-prompt"
          name={userName}
          email={userEmail}
          setGlobal={setGlobal}
          onNameChange={setUserName}
          onEmailChange={setUserEmail}
          onSetGlobalChange={setSetGlobal}
          autoFocus
        />

        <ModalFormActions
          cancelLabel="Not now"
          onCancel={dismiss}
          submitLabel="Save and capture version"
          busyLabel="Saving…"
          busy={busy}
          disabled={!canSubmit}
          submitTestId="identity-prompt-confirm"
        />
      </form>
    </ModalShell>
  )
}
