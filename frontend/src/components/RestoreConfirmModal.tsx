import { useState } from "react"
import { Undo2 } from "lucide-react"

import useProjectDirty from "../hooks/useProjectDirty"
import useGitStore from "../stores/useGitStore"
import ModalShell from "./ModalShell"

interface RestoreConfirmModalProps {
  /** Run the restore. `saveFirst` flushes unsaved edits onto the current working
   *  branch before the restore replaces the files (parking IS saving, S12). */
  onConfirm: (saveFirst: boolean) => void
  onClose: () => void
}

/**
 * The prompt before a restore. A restore rewrites the project's files, and the
 * canvas and the workbench's form with them, so unsaved in-memory edits — which
 * never reached disk, so the engine can't see them — would be lost silently. When
 * the project has unsaved edits, in the canvas or the form, we force the choice:
 * save them onto the current branch first, or discard them. On a clean project
 * it's a simple confirm.
 */
export default function RestoreConfirmModal({ onConfirm, onClose }: RestoreConfirmModalProps) {
  const target = useGitStore((s) => s.restoreTarget)
  const dirty = useProjectDirty()
  const [busy, setBusy] = useState(false)

  if (!target) return null

  // The parent owns the async restore (it has the save) and either reloads on
  // success or closes this modal on error, so we just lock the buttons on click.
  const run = (saveFirst: boolean) => {
    if (busy) return
    setBusy(true)
    onConfirm(saveFirst)
  }

  return (
    <ModalShell
      ariaLabel="Restore a version"
      onClose={() => { if (!busy) onClose() }}
      width="w-[460px]"
      testId="restore-confirm-modal"
    >
      <div className="px-4 py-3" style={{ borderBottom: "1px solid var(--border)" }}>
        <h2
          className="text-sm font-semibold flex items-center gap-1.5"
          style={{ color: "var(--text-primary)" }}
        >
          <Undo2 size={14} /> Restore {target.label}?
        </h2>
        <p className="text-[12px] mt-0.5" style={{ color: "var(--text-muted)" }}>
          Your branch carries on from this version. The saves since then stay in the history.
        </p>
      </div>

      <div className="p-4 flex flex-col gap-3">
        {dirty && (
          <p
            className="text-[12px]"
            style={{ color: "var(--text-secondary)" }}
            data-testid="restore-dirty-warning"
          >
            You have unsaved changes. Save them onto your current branch first, or discard
            them - restoring replaces them.
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
                data-testid="restore-discard"
                onClick={() => run(false)}
                disabled={busy}
                className="px-3 py-1.5 text-[12px] font-medium rounded-md transition-colors disabled:opacity-50"
                style={{ color: "var(--danger-text)", border: "1px solid var(--border)" }}
              >
                Discard &amp; restore
              </button>
              <button
                type="button"
                data-testid="restore-save"
                onClick={() => run(true)}
                disabled={busy}
                className="px-4 py-1.5 text-[12px] font-semibold rounded-md transition-colors disabled:opacity-50 hover:bg-[var(--structure-action-hover)]"
                style={{ background: "var(--structure-action)", color: "var(--text-on-accent)" }}
              >
                {busy ? "Working…" : "Save & restore"}
              </button>
            </>
          ) : (
            <button
              type="button"
              data-testid="restore-confirm"
              onClick={() => run(false)}
              disabled={busy}
              className="px-4 py-1.5 text-[12px] font-semibold rounded-md transition-colors disabled:opacity-50 hover:bg-[var(--structure-action-hover)]"
              style={{ background: "var(--structure-action)", color: "var(--text-on-accent)" }}
            >
              {busy ? "Working…" : "Restore this version"}
            </button>
          )}
        </div>
      </div>
    </ModalShell>
  )
}
