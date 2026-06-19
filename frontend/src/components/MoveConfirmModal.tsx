import { useState } from "react"
import { RotateCcw } from "lucide-react"

import useGitStore from "../stores/useGitStore"
import useGraphStore from "../stores/useGraphStore"
import ModalShell from "./ModalShell"

interface MoveConfirmModalProps {
  /** Run the move. `saveFirst` flushes unsaved edits onto the current working
   *  branch before checking out the target (parking IS saving, S12). */
  onConfirm: (saveFirst: boolean) => void
  onClose: () => void
}

/**
 * Pre-move prompt (P6 §3.4 / §3.9). Moving is a real checkout that replaces the
 * working canvas, so unsaved in-memory edits — which never reached disk, so the
 * engine's dirty-tree floor can't see them — would be lost silently. When the
 * editor is dirty we force the choice: save them onto the current branch first,
 * or discard them. On a clean canvas it's a simple confirm.
 */
export default function MoveConfirmModal({ onConfirm, onClose }: MoveConfirmModalProps) {
  const target = useGitStore((s) => s.moveTarget)
  const dirty = useGraphStore((s) => s.dirty)
  const [busy, setBusy] = useState(false)

  if (!target) return null

  // The parent owns the async move (it has handleSave) and either reloads on
  // success or closes this modal on error, so we just lock the buttons on click.
  const run = (saveFirst: boolean) => {
    if (busy) return
    setBusy(true)
    onConfirm(saveFirst)
  }

  return (
    <ModalShell
      ariaLabel="Move to a version"
      onClose={onClose}
      width="w-[460px]"
      testId="move-confirm-modal"
    >
      <div className="px-4 py-3" style={{ borderBottom: "1px solid var(--border)" }}>
        <h2
          className="text-sm font-semibold flex items-center gap-1.5"
          style={{ color: "var(--text-primary)" }}
        >
          <RotateCcw size={14} /> Move to {target.label}?
        </h2>
        <p className="text-[12px] mt-0.5" style={{ color: "var(--text-muted)" }}>
          Your canvas will show this version. Saving afterwards starts a new line of work
          from here — your current branch stays where it is.
        </p>
      </div>

      <div className="p-4 flex flex-col gap-3">
        {dirty && (
          <p
            className="text-[12px]"
            style={{ color: "var(--text-secondary)" }}
            data-testid="move-dirty-warning"
          >
            You have unsaved changes. Save them onto your current branch first, or discard
            them — moving can't carry them across.
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
                data-testid="move-discard"
                onClick={() => run(false)}
                disabled={busy}
                className="px-3 py-1.5 text-[12px] font-medium rounded-md transition-colors disabled:opacity-50"
                style={{ color: "var(--danger-text)", border: "1px solid var(--border)" }}
              >
                Discard &amp; move
              </button>
              <button
                type="button"
                data-testid="move-save"
                onClick={() => run(true)}
                disabled={busy}
                className="px-4 py-1.5 text-[12px] font-semibold rounded-md transition-colors disabled:opacity-50 hover:bg-[var(--structure-action-hover)]"
                style={{ background: "var(--structure-action)", color: "var(--text-on-accent)" }}
              >
                {busy ? "Working…" : "Save & move"}
              </button>
            </>
          ) : (
            <button
              type="button"
              data-testid="move-confirm"
              onClick={() => run(false)}
              disabled={busy}
              className="px-4 py-1.5 text-[12px] font-semibold rounded-md transition-colors disabled:opacity-50 hover:bg-[var(--structure-action-hover)]"
              style={{ background: "var(--structure-action)", color: "var(--text-on-accent)" }}
            >
              {busy ? "Working…" : "Move to this version"}
            </button>
          )}
        </div>
      </div>
    </ModalShell>
  )
}
