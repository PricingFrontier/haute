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

/**
 * Branch manager (P5b → moved into the Git sidebar panel, S19/S28): working
 * branches as version lines (ledgers implicit). Create / switch / archive (the
 * pair, S32) / delete (the pair, refusing on unmerged saves, §8) / restore.
 * Actions that can't succeed in the current state (e.g. archive/delete a branch
 * with uncommitted changes) are greyed with a reason; failures surface both a
 * toast and a persistent in-panel message. Resolution of conflicts is P6.
 */
export default function BranchManager() {
  const loadStatus = useGitStore((s) => s.loadStatus)
  const addToast = useToastStore((s) => s.addToast)

  const [branches, setBranches] = useState<GitManagedBranch[]>([])
  const [busy, setBusy] = useState<string | null>(null)
  const [newBranch, setNewBranch] = useState("")
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

  // A failed action surfaces BOTH a persistent in-panel message (until dismissed)
  // and a toast — both carrying the backend's "can't … because …" reason.
  const fail = (verb: string, err: unknown) => {
    const detail = err instanceof Error ? err.message : "unknown error"
    setActionError(`Could not ${verb}: ${detail}`)
    addToast("error", `Could not ${verb}: ${detail}`)
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
      fail(verb, err)
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
      reloadOnDone: true, // checkout changed the tree — resync the canvas
    })

  const archive = (b: GitManagedBranch) =>
    run(
      b.name,
      "archive",
      async () => {
        await gitArchiveBranch(b.name)
        addToast("success", `Archived ${b.name}`)
      },
      { reloadOnDone: b.is_current },
    )

  const doDelete = (b: GitManagedBranch) =>
    run(
      b.name,
      "delete",
      async () => {
        await gitDeleteBranch(b.name, b.has_unmerged_saves)
        setConfirmDelete(null)
        addToast("success", `Deleted ${b.name}`)
      },
      { reloadOnDone: b.is_current },
    )

  const restore = (b: GitManagedBranch) =>
    run(b.name, "restore", async () => {
      await restoreBranch(b.name)
      addToast("success", `Restored ${b.name}`)
    })

  const active = branches.filter((b) => !b.is_archived)
  const archived = branches.filter((b) => b.is_archived)
  const anyBusy = busy !== null

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

          {/* Active */}
          <div className="flex flex-col gap-0.5">
            {active.map((b) => (
              <BranchRow
                key={b.name}
                b={b}
                anyBusy={anyBusy}
                onSwitch={() => switchTo(b.name)}
                onArchive={() => archive(b)}
                onDelete={() => setConfirmDelete(b)}
              />
            ))}
          </div>

          {/* Archived */}
          {archived.length > 0 && (
            <div data-testid="branch-manager-archived" className="flex flex-col gap-0.5 pt-1.5" style={{ borderTop: "1px solid var(--border)" }}>
              <span className="text-[10px] font-medium uppercase tracking-wider mb-0.5" style={{ color: "var(--text-muted)" }}>
                Archived
              </span>
              {archived.map((b) => (
                <div key={b.name} data-testid="branch-manager-branch" className="flex items-center gap-1.5 px-1.5 py-1 rounded-md">
                  <Archive size={11} style={{ color: "var(--text-muted)", flexShrink: 0 }} />
                  <span className="flex-1 text-[11px] font-mono truncate" style={{ color: "var(--text-muted)" }}>
                    {b.name}
                  </span>
                  <button
                    data-testid="branch-manager-restore"
                    onClick={() => restore(b)}
                    disabled={anyBusy}
                    title="Restore (un-archive)"
                    className="p-1 rounded transition-colors hover:bg-[var(--bg-hover)] hover:text-[var(--accent)] disabled:opacity-40"
                    style={{ color: "var(--text-muted)" }}
                  >
                    <RotateCcw size={11} />
                  </button>
                  <button
                    data-testid="branch-manager-delete"
                    onClick={() => setConfirmDelete(b)}
                    disabled={anyBusy}
                    title="Delete permanently"
                    className="p-1 rounded transition-colors hover:bg-[var(--danger-soft)] hover:text-[var(--danger)] disabled:opacity-40"
                    style={{ color: "var(--text-muted)" }}
                  >
                    <Trash2 size={11} />
                  </button>
                </div>
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
  onSwitch,
  onArchive,
  onDelete,
}: {
  b: GitManagedBranch
  anyBusy: boolean
  onSwitch: () => void
  onArchive: () => void
  onDelete: () => void
}) {
  // Archive/delete of the *current* branch need a switch-away, which a dirty
  // tree blocks — grey them with a reason rather than letting the action 400.
  const blockedReason = b.has_uncommitted_changes
    ? "Save or discard your changes first"
    : null
  const mutateDisabled = anyBusy || blockedReason !== null

  return (
    <div
      data-testid="branch-manager-branch"
      className="flex items-center gap-1.5 px-1.5 py-1 rounded-md"
      style={{ background: b.is_current ? "var(--accent-soft)" : "transparent" }}
    >
      <GitBranch size={11} style={{ color: b.is_current ? "var(--accent)" : "var(--text-muted)", flexShrink: 0 }} />
      <span className="flex-1 text-[11px] font-mono truncate" style={{ color: b.is_current ? "var(--accent)" : "var(--text-primary)" }}>
        {b.name}
      </span>
      {b.is_current && (
        <span data-testid="branch-manager-current" className="text-[10px] inline-flex items-center gap-0.5 shrink-0" style={{ color: "var(--accent)" }}>
          <Check size={10} /> current
        </span>
      )}
      {blockedReason && (
        <span data-testid="branch-manager-blocked" title={blockedReason} className="text-[10px] shrink-0" style={{ color: "var(--warning)" }}>
          unsaved
        </span>
      )}
      {!b.is_current && (
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
