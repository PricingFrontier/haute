import { useState } from "react"
import { RotateCcw, Undo2 } from "lucide-react"

import useProjectDirty from "../hooks/useProjectDirty"
import useGitStore from "../stores/useGitStore"
import type { GitVersionAction } from "../stores/useGitStore"
import ModalShell from "./ModalShell"

interface MoveConfirmModalProps {
  /** Which action the prompt gates: a move (the default) or a restore. */
  kind?: GitVersionAction
  /** Run the action. `saveFirst` flushes unsaved edits onto the current working
   *  branch before the action replaces the files (parking IS saving, S12). */
  onConfirm: (saveFirst: boolean) => void
  onClose: () => void
}

const WORDING = {
  move: {
    ariaLabel: "Move to a version",
    title: (label: string) => `Move to ${label}?`,
    description:
      "Your canvas will show this version. Saving afterwards starts a new line of work "
      + "from here - your current branch stays where it is.",
    dirty:
      "You have unsaved changes. Save them onto your current branch first, or discard "
      + "them - moving can't carry them across.",
    discard: "Discard & move",
    save: "Save & move",
    confirm: "Move to this version",
  },
  restore: {
    ariaLabel: "Restore a version",
    title: (label: string) => `Restore ${label}?`,
    description:
      "Your branch carries on from this version. The saves since then stay in the history.",
    dirty:
      "You have unsaved changes. Save them onto your current branch first, or discard "
      + "them - restoring replaces them.",
    discard: "Discard & restore",
    save: "Save & restore",
    confirm: "Restore this version",
  },
} as const

/**
 * The prompt before a move or a restore (P6 §3.4 / §3.9). Both replace the working
 * canvas and the workbench's form, so unsaved in-memory edits — which never reached
 * disk, so the engine can't see them — would be lost silently. When the project has
 * unsaved edits, in the canvas or the form, we force the choice: save them onto the
 * current branch first, or discard them. On a clean project it's a simple confirm.
 */
export default function MoveConfirmModal({ kind = "move", onConfirm, onClose }: MoveConfirmModalProps) {
  const target = useGitStore((s) => (kind === "move" ? s.moveTarget : s.restoreTarget))
  const dirty = useProjectDirty()
  const [busy, setBusy] = useState(false)

  if (!target) return null

  const wording = WORDING[kind]
  const Icon = kind === "move" ? RotateCcw : Undo2

  // The parent owns the async action (it has handleSave) and either reloads on
  // success or closes this modal on error, so we just lock the buttons on click.
  const run = (saveFirst: boolean) => {
    if (busy) return
    setBusy(true)
    onConfirm(saveFirst)
  }

  return (
    <ModalShell
      ariaLabel={wording.ariaLabel}
      onClose={() => { if (!busy) onClose() }}
      width="w-[460px]"
      testId={`${kind}-confirm-modal`}
    >
      <div className="px-4 py-3" style={{ borderBottom: "1px solid var(--border)" }}>
        <h2
          className="text-sm font-semibold flex items-center gap-1.5"
          style={{ color: "var(--text-primary)" }}
        >
          <Icon size={14} /> {wording.title(target.label)}
        </h2>
        <p className="text-[12px] mt-0.5" style={{ color: "var(--text-muted)" }}>
          {wording.description}
        </p>
      </div>

      <div className="p-4 flex flex-col gap-3">
        {dirty && (
          <p
            className="text-[12px]"
            style={{ color: "var(--text-secondary)" }}
            data-testid={`${kind}-dirty-warning`}
          >
            {wording.dirty}
          </p>
        )}

        <div className="flex justify-end gap-2">
          <button
            type="button"
            onClick={onClose}
            disabled={busy}
            className="px-3 py-1.5 text-[12px] font-medium rounded-md transition-colors disabled:opacity-50"
            style={{ color: "var(--text-secondary)" }}
          >
            Cancel
          </button>
          {dirty ? (
            <>
              <button
                type="button"
                data-testid={`${kind}-discard`}
                onClick={() => run(false)}
                disabled={busy}
                className="px-3 py-1.5 text-[12px] font-medium rounded-md transition-colors disabled:opacity-50"
                style={{ color: "var(--danger-text)", border: "1px solid var(--border)" }}
              >
                {wording.discard}
              </button>
              <button
                type="button"
                data-testid={`${kind}-save`}
                onClick={() => run(true)}
                disabled={busy}
                className="px-4 py-1.5 text-[12px] font-semibold rounded-md transition-colors disabled:opacity-50 hover:bg-[var(--structure-action-hover)]"
                style={{ background: "var(--structure-action)", color: "var(--text-on-accent)" }}
              >
                {busy ? "Working…" : wording.save}
              </button>
            </>
          ) : (
            <button
              type="button"
              data-testid={`${kind}-confirm`}
              onClick={() => run(false)}
              disabled={busy}
              className="px-4 py-1.5 text-[12px] font-semibold rounded-md transition-colors disabled:opacity-50 hover:bg-[var(--structure-action-hover)]"
              style={{ background: "var(--structure-action)", color: "var(--text-on-accent)" }}
            >
              {busy ? "Working…" : wording.confirm}
            </button>
          )}
        </div>
      </div>
    </ModalShell>
  )
}
