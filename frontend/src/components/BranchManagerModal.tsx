import { useCallback, useEffect, useState } from "react"
import { GitBranch, Archive, Trash2, Plus, Check, AlertTriangle } from "lucide-react"

import {
  getWorkingBranches,
  gitArchiveBranch,
  gitDeleteBranch,
  setWorkingBranch,
} from "../api/client"
import type { GitManagedBranch } from "../api/types"
import useGitStore from "../stores/useGitStore"
import useToastStore from "../stores/useToastStore"
import ModalShell from "./ModalShell"

interface BranchManagerModalProps {
  onClose: () => void
}

/**
 * Branch manager (P5b, S28 "click the branch → manager"): working branches as
 * version lines (their ledgers are implicit). Create / switch / archive (the
 * pair, S32) / delete (the pair, refusing on unmerged saves unless confirmed,
 * §8). Display + actions only; conflict resolution lives in P6.
 */
export default function BranchManagerModal({ onClose }: BranchManagerModalProps) {
  const loadStatus = useGitStore((s) => s.loadStatus)
  const addToast = useToastStore((s) => s.addToast)

  const [branches, setBranches] = useState<GitManagedBranch[]>([])
  const [loading, setLoading] = useState(false)
  const [busy, setBusy] = useState<string | null>(null) // branch name under an in-flight action
  const [newBranch, setNewBranch] = useState("")
  const [confirmDelete, setConfirmDelete] = useState<GitManagedBranch | null>(null)

  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      const res = await getWorkingBranches()
      setBranches(res.branches)
    } catch (err) {
      addToast("error", `Failed to load branches: ${err instanceof Error ? err.message : "error"}`)
    } finally {
      setLoading(false)
    }
  }, [addToast])

  useEffect(() => {
    void refresh()
  }, [refresh])

  const create = async () => {
    const name = newBranch.trim()
    if (!name) return
    setBusy(name)
    try {
      await setWorkingBranch(name, true)
      await loadStatus()
      addToast("success", `Created and switched to ${name}`)
      setNewBranch("")
      await refresh()
    } catch (err) {
      addToast("error", `Could not create branch: ${err instanceof Error ? err.message : "error"}`)
    } finally {
      setBusy(null)
    }
  }

  // Switching (and archiving/deleting the *active* pair) checks out a different
  // branch's ledger server-side, changing the working tree. Reload so the canvas
  // matches the new tree rather than silently desyncing (a softer pipeline
  // re-fetch + park-or-discard of volatile state before the move is P6).
  const reloadApp = () => {
    try {
      window.location.reload()
    } catch {
      /* jsdom / non-browser: no-op */
    }
  }

  const switchTo = async (name: string) => {
    setBusy(name)
    try {
      await setWorkingBranch(name, false)
      reloadApp()
    } catch (err) {
      addToast("error", `Could not switch: ${err instanceof Error ? err.message : "error"}`)
      setBusy(null)
    }
  }

  const archive = async (b: GitManagedBranch) => {
    setBusy(b.name)
    try {
      await gitArchiveBranch(b.name)
      addToast("success", `Archived ${b.name}`)
      if (b.is_current) {
        reloadApp() // the active pair was checked out away — resync the canvas
        return
      }
      await loadStatus()
      await refresh()
    } catch (err) {
      addToast("error", `Could not archive: ${err instanceof Error ? err.message : "error"}`)
    } finally {
      setBusy(null)
    }
  }

  const doDelete = async (b: GitManagedBranch) => {
    setBusy(b.name)
    try {
      await gitDeleteBranch(b.name, b.has_unmerged_saves)
      setConfirmDelete(null) // clear only on success — a failed delete stays recoverable
      addToast("success", `Deleted ${b.name}`)
      if (b.is_current) {
        reloadApp()
        return
      }
      await loadStatus()
      await refresh()
    } catch (err) {
      addToast("error", `Could not delete: ${err instanceof Error ? err.message : "error"}`)
    } finally {
      setBusy(null)
    }
  }

  const active = branches.filter((b) => !b.is_archived)
  const archived = branches.filter((b) => b.is_archived)

  return (
    <ModalShell ariaLabel="Branch manager" onClose={onClose} width="w-[480px]" testId="branch-manager-modal">
      <div className="px-4 py-3" style={{ borderBottom: "1px solid var(--border)" }}>
        <h2 className="text-sm font-semibold" style={{ color: "var(--text-primary)" }}>
          Branches
        </h2>
        <p className="text-[12px] mt-0.5" style={{ color: "var(--text-muted)" }}>
          Each branch is a version line. Switching changes which one your saves record against.
        </p>
      </div>

      <div className="p-4 flex flex-col gap-3 max-h-[60vh] overflow-y-auto">
        {/* Create */}
        <form
          className="flex gap-2"
          onSubmit={(e) => {
            e.preventDefault()
            void create()
          }}
        >
          <input
            data-testid="branch-manager-create-input"
            value={newBranch}
            onChange={(e) => setNewBranch(e.target.value)}
            placeholder="New branch name…"
            className="flex-1 px-2.5 py-1.5 text-[13px] rounded-md focus:outline-none focus:ring-2"
            style={{
              background: "var(--bg-input)",
              border: "1px solid var(--border)",
              color: "var(--text-primary)",
              caretColor: "var(--accent)",
            }}
          />
          <button
            type="submit"
            data-testid="branch-manager-create"
            disabled={newBranch.trim() === "" || busy !== null}
            className="px-3 py-1.5 text-[12px] font-semibold rounded-md inline-flex items-center gap-1 disabled:opacity-50 disabled:cursor-not-allowed"
            style={{ background: "var(--structure-action)", color: "var(--text-on-accent)" }}
          >
            <Plus size={13} /> Create
          </button>
        </form>

        {/* Active branches */}
        <div className="flex flex-col gap-1">
          {active.length === 0 && !loading && (
            <span className="text-[12px]" style={{ color: "var(--text-muted)" }}>
              No branches yet.
            </span>
          )}
          {active.map((b) => (
            <BranchRow
              key={b.name}
              b={b}
              disabled={busy !== null}
              onSwitch={() => switchTo(b.name)}
              onArchive={() => archive(b)}
              onDelete={() => setConfirmDelete(b)}
            />
          ))}
        </div>

        {/* Archived */}
        {archived.length > 0 && (
          <div data-testid="branch-manager-archived" className="flex flex-col gap-1 pt-2" style={{ borderTop: "1px solid var(--border)" }}>
            <span className="text-[10px] font-medium uppercase tracking-wider" style={{ color: "var(--text-muted)" }}>
              Archived
            </span>
            {archived.map((b) => (
              <div
                key={b.name}
                data-testid="branch-manager-branch"
                className="flex items-center gap-2 px-2 py-1.5 rounded-md"
              >
                <Archive size={12} style={{ color: "var(--text-muted)", flexShrink: 0 }} />
                <span className="flex-1 text-[12px] font-mono truncate" style={{ color: "var(--text-muted)" }}>
                  {b.name}
                </span>
                <button
                  data-testid="branch-manager-delete"
                  onClick={() => setConfirmDelete(b)}
                  disabled={busy !== null}
                  title="Delete permanently"
                  className="p-1 rounded transition-colors hover:bg-[var(--danger-soft)] hover:text-[var(--danger)] disabled:opacity-40"
                  style={{ color: "var(--text-muted)" }}
                >
                  <Trash2 size={12} />
                </button>
              </div>
            ))}
          </div>
        )}

        {/* Delete confirmation */}
        {confirmDelete && (
          <div
            data-testid="branch-manager-confirm"
            className="flex flex-col gap-2 px-3 py-2.5 rounded-md"
            style={{ background: "var(--danger-soft-faint)", border: "1px solid var(--danger-soft-strong)" }}
          >
            <div className="flex items-start gap-2">
              <AlertTriangle size={13} style={{ color: "var(--danger)", flexShrink: 0, marginTop: 1 }} />
              <span className="text-[12px]" style={{ color: "var(--text-primary)" }}>
                {confirmDelete.has_unmerged_saves
                  ? `"${confirmDelete.name}" has saves not yet committed to a milestone. Deleting loses them. Delete anyway?`
                  : `Permanently delete "${confirmDelete.name}" and its save history?`}
              </span>
            </div>
            <div className="flex justify-end gap-2">
              <button
                onClick={() => setConfirmDelete(null)}
                className="px-3 py-1 text-[12px] font-medium rounded-md"
                style={{ color: "var(--text-secondary)" }}
              >
                Cancel
              </button>
              <button
                data-testid="branch-manager-confirm-delete"
                onClick={() => void doDelete(confirmDelete)}
                className="px-3 py-1 text-[12px] font-semibold rounded-md"
                style={{ background: "var(--danger)", color: "var(--text-on-accent)" }}
              >
                Delete
              </button>
            </div>
          </div>
        )}
      </div>
    </ModalShell>
  )
}

function BranchRow({
  b,
  disabled,
  onSwitch,
  onArchive,
  onDelete,
}: {
  b: GitManagedBranch
  disabled: boolean
  onSwitch: () => void
  onArchive: () => void
  onDelete: () => void
}) {
  return (
    <div
      data-testid="branch-manager-branch"
      className="flex items-center gap-2 px-2 py-1.5 rounded-md"
      style={{ background: b.is_current ? "var(--accent-soft)" : "transparent" }}
    >
      <GitBranch size={12} style={{ color: b.is_current ? "var(--accent)" : "var(--text-muted)", flexShrink: 0 }} />
      <span
        className="flex-1 text-[12px] font-mono truncate"
        style={{ color: b.is_current ? "var(--accent)" : "var(--text-primary)" }}
      >
        {b.name}
      </span>
      {b.is_current && (
        <span data-testid="branch-manager-current" className="text-[10px] inline-flex items-center gap-0.5" style={{ color: "var(--accent)" }}>
          <Check size={10} /> current
        </span>
      )}
      {b.has_unmerged_saves && (
        <span title="Has saves not yet committed to a milestone" className="text-[10px]" style={{ color: "var(--warning)" }}>
          unsaved
        </span>
      )}
      {!b.is_current && (
        <button
          data-testid="branch-manager-switch"
          onClick={onSwitch}
          disabled={disabled}
          className="px-2 py-0.5 text-[11px] font-medium rounded transition-colors hover:bg-[var(--bg-hover)] disabled:opacity-40"
          style={{ color: "var(--text-secondary)" }}
        >
          Switch
        </button>
      )}
      <button
        data-testid="branch-manager-archive"
        onClick={onArchive}
        disabled={disabled}
        title="Archive"
        className="p-1 rounded transition-colors hover:bg-[var(--bg-hover)] disabled:opacity-40"
        style={{ color: "var(--text-muted)" }}
      >
        <Archive size={12} />
      </button>
      <button
        data-testid="branch-manager-delete"
        onClick={onDelete}
        disabled={disabled}
        title="Delete"
        className="p-1 rounded transition-colors hover:bg-[var(--danger-soft)] hover:text-[var(--danger)] disabled:opacity-40"
        style={{ color: "var(--text-muted)" }}
      >
        <Trash2 size={12} />
      </button>
    </div>
  )
}
