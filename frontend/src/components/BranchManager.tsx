import { useCallback, useEffect, useState } from "react"
import {
  GitBranch, Archive, Trash2, Plus, Check, AlertTriangle, RotateCcw, ChevronRight, ChevronDown,
} from "lucide-react"

import {
  getWorkingBranches,
  gitArchiveBranch,
  gitDeleteBranch,
  restoreBranch,
  setWorkingBranch,
} from "../api/client"
import type { GitManagedBranch } from "../api/types"
import useGitStore from "../stores/useGitStore"
import useToastStore from "../stores/useToastStore"

interface BranchManagerProps {
  /** Branch whose history is currently shown in the panel (peek/current). */
  selectedBranch?: string | null
  /** Peek a branch's history without switching to it. */
  onPeek?: (name: string) => void
}

/**
 * Branch manager in the Git sidebar panel (S19/S28). The current branch sits in
 * its own box above the create field; clicking any branch PEEKS its history
 * (onPeek) without switching. Create / switch / archive (the pair, S32) / delete
 * (the pair, §8) / restore. Actions blocked by the current state (e.g. archive/
 * delete a branch with uncommitted changes) are greyed with a reason; failures
 * raise a persistent in-panel message + a toast.
 */
export default function BranchManager({ selectedBranch, onPeek }: BranchManagerProps) {
  const loadStatus = useGitStore((s) => s.loadStatus)
  const addToast = useToastStore((s) => s.addToast)

  const [branches, setBranches] = useState<GitManagedBranch[]>([])
  const [newBranch, setNewBranch] = useState("")
  const [busy, setBusy] = useState<string | null>(null)
  const [confirmDelete, setConfirmDelete] = useState<GitManagedBranch | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const [collapsed, setCollapsed] = useState(false)

  const refresh = useCallback(async () => {
    try {
      const res = await getWorkingBranches()
      setBranches(res.branches)
    } catch (err) {
      setActionError(`Failed to load branches: ${err instanceof Error ? err.message : "error"}`)
    }
  }, [])

  useEffect(() => {
    void refresh()
  }, [refresh])

  const reloadApp = () => {
    try {
      window.location.reload()
    } catch {
      /* jsdom / non-browser: no-op */
    }
  }

  const run = async (
    name: string,
    verb: string,
    fn: () => Promise<void>,
    opts: { reloadOnDone?: boolean } = {},
  ) => {
    setBusy(name)
    setActionError(null)
    try {
      await fn()
      if (opts.reloadOnDone) {
        reloadApp()
        return
      }
      await loadStatus()
      await refresh()
    } catch (err) {
      const detail = err instanceof Error ? err.message : "unknown error"
      setActionError(`Could not ${verb}: ${detail}`) // persistent
      addToast("error", `Could not ${verb}: ${detail}`) // splash
    } finally {
      setBusy(null)
    }
  }

  const create = () => {
    const name = newBranch.trim()
    if (!name) return
    void run(name, "create branch", async () => {
      await setWorkingBranch(name, true)
      addToast("success", `Created and switched to ${name}`)
      setNewBranch("")
    })
  }

  const switchTo = (name: string) =>
    run(name, "switch", () => setWorkingBranch(name, false).then(() => undefined), {
      reloadOnDone: true,
    })

  const archive = (b: GitManagedBranch) =>
    run(b.name, "archive", async () => {
      await gitArchiveBranch(b.name)
      addToast("success", `Archived ${b.name}`)
    }, { reloadOnDone: b.is_current })

  const doDelete = (b: GitManagedBranch) =>
    run(b.name, "delete", async () => {
      await gitDeleteBranch(b.name, b.has_unmerged_saves)
      setConfirmDelete(null)
      addToast("success", `Deleted ${b.name}`)
    }, { reloadOnDone: b.is_current })

  const restore = (b: GitManagedBranch) =>
    run(b.name, "restore", async () => {
      await restoreBranch(b.name)
      addToast("success", `Restored ${b.name}`)
    })

  const current = branches.find((b) => b.is_current && !b.is_archived) ?? null
  const others = branches.filter((b) => !b.is_archived && !b.is_current)
  const archived = branches.filter((b) => b.is_archived)
  const anyBusy = busy !== null

  const rowProps = (b: GitManagedBranch) => ({
    b,
    anyBusy,
    selected: (selectedBranch ?? null) === b.name,
    onPeek: () => onPeek?.(b.name),
    onSwitch: () => switchTo(b.name),
    onArchive: () => archive(b),
    onRestore: () => restore(b),
    onDelete: () => setConfirmDelete(b),
  })

  return (
    <div data-testid="branch-manager" style={{ borderBottom: "1px solid var(--border)" }}>
      <button
        onClick={() => setCollapsed((c) => !c)}
        className="w-full flex items-center gap-1.5 px-3 py-2 text-left"
        style={{ color: "var(--text-muted)" }}
      >
        {collapsed ? <ChevronRight size={12} /> : <ChevronDown size={12} />}
        <span className="text-[10px] font-medium uppercase tracking-wider">Branches</span>
      </button>

      {!collapsed && (
        <div className="px-3 pb-3 flex flex-col gap-2">
          {actionError && (
            <div
              data-testid="branch-manager-error"
              className="flex items-start gap-2 px-2.5 py-1.5 rounded-md text-[11px]"
              style={{ background: "var(--danger-soft-faint)", color: "var(--danger)", border: "1px solid var(--danger-soft-strong)" }}
            >
              <AlertTriangle size={12} className="shrink-0 mt-0.5" />
              <span className="flex-1">{actionError}</span>
              <button onClick={() => setActionError(null)} className="opacity-60 hover:opacity-100 shrink-0" title="Dismiss">
                ✕
              </button>
            </div>
          )}

          {/* Current branch — boxed, above the create field */}
          {current && (
            <div
              className="rounded-md"
              style={{ border: "1px solid var(--accent)", background: "var(--accent-soft)" }}
            >
              <BranchRow {...rowProps(current)} />
            </div>
          )}

          {/* Create */}
          <form
            className="flex gap-1.5"
            onSubmit={(e) => {
              e.preventDefault()
              create()
            }}
          >
            <input
              data-testid="branch-manager-create-input"
              value={newBranch}
              onChange={(e) => setNewBranch(e.target.value)}
              placeholder="New branch…"
              className="flex-1 px-2 py-1 text-[12px] rounded-md focus:outline-none focus:ring-2 min-w-0"
              style={{ background: "var(--bg-input)", border: "1px solid var(--border)", color: "var(--text-primary)", caretColor: "var(--accent)" }}
            />
            <button
              type="submit"
              data-testid="branch-manager-create"
              disabled={newBranch.trim() === "" || anyBusy}
              className="px-2 py-1 text-[11px] font-semibold rounded-md inline-flex items-center gap-1 disabled:opacity-50 disabled:cursor-not-allowed shrink-0"
              style={{ background: "var(--structure-action)", color: "var(--text-on-accent)" }}
            >
              <Plus size={12} /> Create
            </button>
          </form>

          {/* Other working branches */}
          {others.length > 0 && (
            <div className="flex flex-col gap-0.5">
              {others.map((b) => (
                <BranchRow key={b.name} {...rowProps(b)} />
              ))}
            </div>
          )}

          {/* Archived */}
          {archived.length > 0 && (
            <div data-testid="branch-manager-archived" className="flex flex-col gap-0.5 pt-1.5" style={{ borderTop: "1px solid var(--border)" }}>
              <span className="text-[10px] font-medium uppercase tracking-wider mb-0.5" style={{ color: "var(--text-muted)" }}>
                Archived
              </span>
              {archived.map((b) => (
                <BranchRow key={b.name} {...rowProps(b)} />
              ))}
            </div>
          )}

          {/* Delete confirmation */}
          {confirmDelete && (
            <div
              data-testid="branch-manager-confirm"
              className="flex flex-col gap-2 px-2.5 py-2 rounded-md"
              style={{ background: "var(--danger-soft-faint)", border: "1px solid var(--danger-soft-strong)" }}
            >
              <span className="text-[11px]" style={{ color: "var(--text-primary)" }}>
                {confirmDelete.has_unmerged_saves
                  ? `"${confirmDelete.name}" has saves not yet committed to a milestone. Deleting loses them. Delete anyway?`
                  : `Permanently delete "${confirmDelete.name}" and its history?`}
              </span>
              <div className="flex justify-end gap-2">
                <button onClick={() => setConfirmDelete(null)} className="px-2.5 py-1 text-[11px] font-medium rounded-md" style={{ color: "var(--text-secondary)" }}>
                  Cancel
                </button>
                <button
                  data-testid="branch-manager-confirm-delete"
                  onClick={() => doDelete(confirmDelete)}
                  className="px-2.5 py-1 text-[11px] font-semibold rounded-md"
                  style={{ background: "var(--danger)", color: "var(--text-on-accent)" }}
                >
                  Delete
                </button>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function BranchRow({
  b,
  anyBusy,
  selected,
  onPeek,
  onSwitch,
  onArchive,
  onRestore,
  onDelete,
}: {
  b: GitManagedBranch
  anyBusy: boolean
  selected: boolean
  onPeek: () => void
  onSwitch: () => void
  onArchive: () => void
  onRestore: () => void
  onDelete: () => void
}) {
  // Archive/delete of the *current* branch need a switch-away, which a dirty
  // tree blocks — grey them with a reason rather than letting the action 400.
  const blockedReason = b.has_uncommitted_changes ? "Save or discard your changes first" : null
  const mutateDisabled = anyBusy || blockedReason !== null

  return (
    <div
      data-testid="branch-manager-branch"
      className="flex items-center gap-1.5 px-1.5 py-1 rounded-md"
      style={{ outline: selected ? "1px solid var(--accent)" : undefined }}
    >
      {/* Leading marker: 'current' to the LEFT of the name; else a branch/archive icon */}
      {b.is_current ? (
        <span data-testid="branch-manager-current" className="inline-flex items-center gap-0.5 shrink-0 text-[10px]" style={{ color: "var(--accent)" }}>
          <Check size={11} /> current
        </span>
      ) : b.is_archived ? (
        <Archive size={11} style={{ color: "var(--text-muted)", flexShrink: 0 }} />
      ) : (
        <GitBranch size={11} style={{ color: "var(--text-muted)", flexShrink: 0 }} />
      )}

      <button
        onClick={onPeek}
        title="View this branch's history"
        className="flex-1 text-[11px] font-mono truncate text-left hover:underline min-w-0"
        style={{ color: b.is_current ? "var(--accent)" : b.is_archived ? "var(--text-muted)" : "var(--text-primary)" }}
      >
        {b.name}
      </button>

      {/* State indicators (distinct) */}
      {b.has_uncommitted_changes && (
        <span data-testid="branch-manager-uncommitted" title="Uncommitted changes in the working tree" className="text-[10px] shrink-0" style={{ color: "var(--warning)" }}>
          uncommitted
        </span>
      )}
      {b.has_unmerged_saves && (
        <span data-testid="branch-manager-unsaved" title="Saves not yet committed to a milestone" className="text-[10px] shrink-0" style={{ color: "var(--text-muted)" }}>
          unsaved
        </span>
      )}

      {/* Actions */}
      {!b.is_current && !b.is_archived && (
        <button
          data-testid="branch-manager-switch"
          onClick={onSwitch}
          disabled={anyBusy}
          className="px-1.5 py-0.5 text-[10px] font-medium rounded transition-colors hover:bg-[var(--bg-hover)] disabled:opacity-40"
          style={{ color: "var(--text-secondary)" }}
        >
          Switch
        </button>
      )}
      {b.is_archived && (
        <button
          data-testid="branch-manager-restore"
          onClick={onRestore}
          disabled={anyBusy}
          title="Restore (un-archive)"
          className="p-1 rounded transition-colors hover:bg-[var(--bg-hover)] hover:text-[var(--accent)] disabled:opacity-40"
          style={{ color: "var(--text-muted)" }}
        >
          <RotateCcw size={11} />
        </button>
      )}
      {/* No archive on the current branch (switch away first); archived can't re-archive */}
      {!b.is_current && !b.is_archived && (
        <button
          data-testid="branch-manager-archive"
          onClick={onArchive}
          disabled={mutateDisabled}
          title={blockedReason ?? "Archive"}
          className="p-1 rounded transition-colors hover:bg-[var(--bg-hover)] disabled:opacity-40"
          style={{ color: "var(--text-muted)" }}
        >
          <Archive size={11} />
        </button>
      )}
      <button
        data-testid="branch-manager-delete"
        onClick={onDelete}
        disabled={mutateDisabled}
        title={blockedReason ?? "Delete"}
        className="p-1 rounded transition-colors hover:bg-[var(--danger-soft)] hover:text-[var(--danger)] disabled:opacity-40"
        style={{ color: "var(--text-muted)" }}
      >
        <Trash2 size={11} />
      </button>
    </div>
  )
}
