import { useCallback, useEffect, useState } from "react"
import {
  GitBranch, Archive, Trash2, Plus, AlertTriangle,
  RotateCcw, ChevronRight, ChevronDown, ArrowRightLeft,
} from "lucide-react"

import {
  createWorkingBranch,
  getGitPrefs,
  gitArchiveBranch,
  gitDeleteBranch,
  restoreBranch,
  setGitPrefs,
  setWorkingBranch,
} from "../api/client"
import type { GitManagedBranch } from "../api/types"
import useGitStore from "../stores/useGitStore"
import useGraphStore from "../stores/useGraphStore"
import useToastStore from "../stores/useToastStore"
import { recordArchive, recordDelete, recordRestore, recordSwitch } from "../utils/vcHistory"
import { gitErrorMessage } from "../utils/gitError"
import Tooltip from "./Tooltip"
import GitNavigationConfirm from "./GitNavigationConfirm"

interface BranchManagerProps {
  /** Branch whose history is currently shown in the panel (peek/current). */
  selectedBranch?: string | null
  /** Peek a branch's history without switching to it. */
  onPeek?: (name: string) => void
  onSave?: () => Promise<boolean>
}

/**
 * Branch manager in the Git sidebar panel (S19/S28/S38). The current branch sits
 * in its own box at the top; below it the other lines, then the create field
 * (Create spins off a parallel line at the latest milestone; Create & Move also
 * relocates your in-progress work and switches), then archived. Clicking a name
 * PEEKS its history (onPeek) without switching. Switching prompts a confirm with
 * a "don't ask again" that persists to the local environment.
 */
export default function BranchManager({ selectedBranch, onPeek, onSave }: BranchManagerProps) {
  const loadStatus = useGitStore((s) => s.loadStatus)
  const branches = useGitStore((s) => s.branches)
  const loadBranches = useGitStore((s) => s.loadBranches)
  const openModal = useGitStore((s) => s.openModal)
  const branchesExpandNonce = useGitStore((s) => s.branchesExpandNonce)
  // Undo/redo of a VC operation changes the branch forest from outside this
  // component — the nonce tells us to re-list.
  const historyNonce = useGitStore((s) => s.historyNonce)
  const commitNonce = useGitStore((s) => s.commitNonce)
  const addToast = useToastStore((s) => s.addToast)
  const dirty = useGraphStore((s) => s.dirty)

  const [newBranch, setNewBranch] = useState("")
  const [busy, setBusy] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const [collapsed, setCollapsed] = useState(false)
  const [createMenuOpen, setCreateMenuOpen] = useState(false)

  // Dialog state (one open at a time).
  const [confirmDelete, setConfirmDelete] = useState<GitManagedBranch | null>(null)
  const [confirmSwitch, setConfirmSwitch] = useState<GitManagedBranch | null>(null)
  const [confirmMove, setConfirmMove] = useState<string | null>(null)
  const [archiveDirty, setArchiveDirty] = useState<GitManagedBranch | null>(null)
  const [dontAskSwitch, setDontAskSwitch] = useState(false)
  const [dirtyNavigation, setDirtyNavigation] = useState<(() => void) | null>(null)

  // Persisted "don't ask again" for switching (loaded once; whole-environment).
  const [skipSwitchConfirm, setSkipSwitchConfirm] = useState(false)
  // A save/commit can start a refresh while the mount request is still in
  // flight. Only the newest request may publish its branch flags; otherwise an
  // older dirty listing can restore badges that the newer request just cleared.
  const refresh = useCallback(async () => {
    try {
      await loadBranches()
    } catch (err) {
      setActionError(`Failed to load branches: ${gitErrorMessage(err, "error")}`)
    }
  }, [loadBranches])

  useEffect(() => {
    void refresh()
    void getGitPrefs()
      .then((p) => setSkipSwitchConfirm(p.skip_switch_confirm))
      .catch(() => {})
  }, [refresh])

  // The toolbar branch name asks the manager to expand (S38).
  useEffect(() => {
    if (branchesExpandNonce > 0) setCollapsed(false)
  }, [branchesExpandNonce])

  // Re-list after out-of-component history changes (VC undo/redo, saves and
  // milestone commits). The commit refresh is essential because the preceding
  // save refresh still sees its ledger entries as unmerged.
  useEffect(() => {
    if (historyNonce > 0 || commitNonce > 0) {
      void loadBranches({ refresh: true }).catch((err) => {
        setActionError(`Failed to load branches: ${gitErrorMessage(err, "error")}`)
      })
    }
  }, [historyNonce, commitNonce, loadBranches])

  // Right-click on a branch row: the row's actions as a context menu.
  const [rowMenu, setRowMenu] = useState<{ b: GitManagedBranch; x: number; y: number } | null>(
    null,
  )

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
    fn: () => Promise<boolean | void>,
    opts: { reloadOnDone?: boolean; reloadWhen?: (result: boolean | void) => boolean } = {},
  ) => {
    setBusy(name)
    setActionError(null)
    try {
      const result = await fn()
      if (opts.reloadOnDone || opts.reloadWhen?.(result)) {
        reloadApp()
        return
      }
      await loadStatus()
      // A branch op changes the fork forest — nudge the Git panel to refetch
      // its history + graph; BranchManager's nonce effect performs the one
      // shared branch-list refresh.
      useGitStore.getState().notifyHistoryChanged()
    } catch (err) {
      const detail = gitErrorMessage(err, "unknown error")
      setActionError(`Could not ${verb}: ${detail}`) // persistent
      addToast("error", `Could not ${verb}: ${detail}`) // splash
    } finally {
      setBusy(null)
    }
  }

  // -- create -----------------------------------------------------------------
  const doCreate = (name: string, move: boolean) =>
    run(name, move ? "create & move" : "create branch", async () => {
      const res = await createWorkingBranch(name, { move })
      addToast("success", move ? `Created ${name} and moved your work` : `Created ${name}`)
      setNewBranch("")
      setConfirmMove(null)
      // A move switches you over; a parallel create leaves you put.
      return res.switched
    }, { reloadWhen: (switched) => switched === true })

  const onCreateClick = () => {
    const name = newBranch.trim()
    if (name) void doCreate(name, false)
  }

  const onCreateMoveClick = () => {
    const name = newBranch.trim()
    setCreateMenuOpen(false)
    if (name) setConfirmMove(name)
  }

  // -- switch -----------------------------------------------------------------
  // In-app (feedback round 2): no page reload — the checkout lands on the
  // canvas via the websocket sync, and the completed switch is recorded as an
  // undoable history entry.
  const switchNow = (b: GitManagedBranch, dontAsk: boolean) => {
    guardNavigation(() => {
      if (dontAsk) {
        setSkipSwitchConfirm(true)
        void setGitPrefs({ skip_switch_confirm: true }).catch(() => {})
      }
      setConfirmSwitch(null)
      setDontAskSwitch(false)
      const from = branches.find((x) => x.is_current && !x.is_archived)?.name ?? null
      void run(b.name, "switch", async () => {
        await setWorkingBranch(b.name, false)
        addToast("success", `Switched to ${b.name}`)
        if (from !== null) recordSwitch(from, b.name)
      })
    })
  }

  const onSwitchClick = (b: GitManagedBranch) => {
    if (skipSwitchConfirm) switchNow(b, false)
    else setConfirmSwitch(b)
  }

  const guardNavigation = (proceed: () => void) => {
    if (dirty) setDirtyNavigation(() => proceed)
    else proceed()
  }

  // -- archive / delete / restore --------------------------------------------
  const onArchiveClick = (b: GitManagedBranch) => {
    const archive = () => {
      if (b.has_uncommitted_changes) {
        setArchiveDirty(b)
        return
      }
      // Archiving the CURRENT branch moves you off it — that flow keeps the
      // full reload (and records no history entry, since a reload clears the
      // stacks anyway). Archiving any other branch is an in-app, undoable op.
      void run(b.name, "archive", async () => {
        const res = await gitArchiveBranch(b.name)
        addToast("success", `Archived ${b.name}`)
        if (!b.is_current) recordArchive(b.name, res.archived_as)
      }, { reloadOnDone: b.is_current })
    }
    if (b.is_current) guardNavigation(archive)
    else archive()
  }

  const doDelete = (b: GitManagedBranch) =>
    run(b.name, "delete", async () => {
      await gitDeleteBranch(b.name, true) // dialog already confirmed the loss
      setConfirmDelete(null)
      addToast("success", `Deleted ${b.name}`)
      // Deletes are trash-preserving server-side, so undo (undelete) is an
      // instant ref restore. Current-branch deletes keep the reload flow.
      if (!b.is_current) recordDelete(b.name)
    }, { reloadOnDone: b.is_current })

  const restore = (b: GitManagedBranch) =>
    run(b.name, "restore", async () => {
      const res = await restoreBranch(b.name)
      addToast("success", `Restored ${b.name}`)
      recordRestore(b.name, res.restored_as)
    })

  const current = branches.find((b) => b.is_current && !b.is_archived) ?? null
  const others = branches.filter((b) => !b.is_archived && !b.is_current)
  const archived = branches.filter((b) => b.is_archived)
  const anyBusy = busy !== null
  // The current branch's box is dimmer when you're peeking elsewhere, so the
  // selected (peeked) branch's bright outline reads as the clearer focus (S38).
  const currentSelected = current != null && (selectedBranch ?? null) === current.name

  const rowProps = (b: GitManagedBranch) => ({
    b,
    anyBusy,
    selected: (selectedBranch ?? null) === b.name,
    onPeek: () => onPeek?.(b.name),
    onSwitch: () => onSwitchClick(b),
    onArchive: () => onArchiveClick(b),
    onRestore: () => restore(b),
    onDelete: () => setConfirmDelete(b),
    onContextMenu: (e: React.MouseEvent) => {
      e.preventDefault()
      setRowMenu({ b, x: e.clientX, y: e.clientY })
    },
  })

  return (
    <div data-testid="branch-manager" style={{ borderBottom: "1px solid var(--border)" }}>
      <button
        onClick={() => setCollapsed((c) => !c)}
        className="w-full flex items-center gap-1.5 px-3 py-2 text-left"
        style={{ color: "var(--text-muted)" }}
      >
        {collapsed ? <ChevronRight size={13} /> : <ChevronDown size={13} />}
        <span className="text-[11px] font-medium uppercase tracking-wider">Branches</span>
      </button>

      {!collapsed && (
        <div className="px-3 pb-3 flex flex-col gap-2">
          {actionError && (
            <div
              data-testid="branch-manager-error"
              className="flex items-start gap-2 px-2.5 py-1.5 rounded-md text-[12px]"
              style={{ background: "var(--danger-soft-faint)", color: "var(--danger)", border: "1px solid var(--danger-soft-strong)" }}
            >
              <AlertTriangle size={13} className="shrink-0 mt-0.5" />
              <span className="flex-1">{actionError}</span>
              <button onClick={() => setActionError(null)} className="opacity-60 hover:opacity-100 shrink-0" title="Dismiss">
                ✕
              </button>
            </div>
          )}

          {dirtyNavigation && (
            <GitNavigationConfirm
              onCancel={() => setDirtyNavigation(null)}
              onDiscard={() => { const proceed = dirtyNavigation; setDirtyNavigation(null); proceed() }}
              onSave={async () => {
                try {
                  if (!await (onSave?.() ?? Promise.resolve(false))) return
                  const proceed = dirtyNavigation
                  setDirtyNavigation(null)
                  proceed?.()
                } catch { /* Saving failed; leave the choice visible. */ }
              }}
            />
          )}

          {/* Current branch — boxed, at the top. Dimmer when not the peeked one. */}
          {current && (
            <div
              className="rounded-md"
              style={{
                border: `1px solid ${currentSelected ? "var(--accent)" : "var(--accent-soft-strong)"}`,
                background: currentSelected ? "var(--accent-soft)" : "var(--accent-soft-faint)",
              }}
            >
              <BranchRow {...rowProps(current)} />
            </div>
          )}

          {/* Other working branches */}
          {others.length > 0 && (
            <div className="flex flex-col gap-0.5">
              {others.map((b) => (
                <BranchRow key={b.name} {...rowProps(b)} />
              ))}
            </div>
          )}

          {/* Create (below the existing lines) — split button: Create / & Move */}
          <div className="relative flex gap-1.5">
            <input
              data-testid="branch-manager-create-input"
              value={newBranch}
              onChange={(e) => setNewBranch(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault()
                  onCreateClick()
                }
              }}
              placeholder="New branch…"
              className="flex-1 px-2 py-1 text-[12px] rounded-md focus:outline-none focus:ring-2 min-w-0"
              style={{ background: "var(--bg-input)", border: "1px solid var(--border)", color: "var(--text-primary)", caretColor: "var(--accent)" }}
            />
            <div className="flex shrink-0">
              <button
                type="button"
                data-testid="branch-manager-create"
                onClick={onCreateClick}
                disabled={newBranch.trim() === "" || anyBusy}
                title="Create a parallel branch at your latest milestone (you stay here)"
                className="px-2 py-0.5 text-[11px] font-semibold rounded-l-md inline-flex items-center gap-1 disabled:opacity-50 disabled:cursor-not-allowed"
                style={{ background: "var(--structure-action)", color: "var(--text-on-accent)" }}
              >
                <Plus size={12} /> Create
              </button>
              <button
                type="button"
                data-testid="branch-manager-create-menu"
                onClick={() => setCreateMenuOpen((o) => !o)}
                disabled={newBranch.trim() === "" || anyBusy}
                title="More create options"
                className="px-1 py-0.5 rounded-r-md inline-flex items-center disabled:opacity-50 disabled:cursor-not-allowed"
                style={{ background: "var(--structure-action-hover)", color: "var(--text-on-accent)", borderLeft: "1px solid var(--accent-soft)" }}
              >
                <ChevronDown size={12} />
              </button>
            </div>
            {createMenuOpen && newBranch.trim() !== "" && (
              <div
                data-testid="branch-manager-create-options"
                className="absolute right-0 top-full mt-1 z-10 rounded-md py-1 shadow-lg"
                style={{ background: "var(--bg-elevated)", border: "1px solid var(--border)" }}
              >
                <button
                  type="button"
                  data-testid="branch-manager-create-move"
                  onClick={onCreateMoveClick}
                  className="flex items-center gap-1.5 px-2.5 py-1 text-[11px] w-full text-left hover:bg-[var(--bg-hover)]"
                  style={{ color: "var(--text-primary)" }}
                >
                  <ArrowRightLeft size={12} style={{ color: "var(--accent)" }} /> Create &amp; Move
                </button>
              </div>
            )}
          </div>

          {/* Archived */}
          {archived.length > 0 && (
            <div data-testid="branch-manager-archived" className="flex flex-col gap-0.5 pt-1.5" style={{ borderTop: "1px solid var(--border)" }}>
              {/* pl-[19px] = chevron (13) + gap (6) so this lines up with the
                  BRANCHES header text above, for symmetry (S38). */}
              <span className="text-[11px] font-medium uppercase tracking-wider mb-0.5 pl-[19px]" style={{ color: "var(--text-muted)" }}>
                Archived
              </span>
              {archived.map((b) => (
                <BranchRow key={b.name} {...rowProps(b)} />
              ))}
            </div>
          )}

          {/* Create & Move confirmation (relocates work + switches) */}
          {confirmMove && (
            <div
              data-testid="branch-manager-confirm-move"
              className="flex flex-col gap-2 px-2.5 py-2 rounded-md"
              style={{ background: "var(--accent-soft-faint)", border: "1px solid var(--accent-soft-strong)" }}
            >
              <span className="text-[12px]" style={{ color: "var(--text-primary)" }}>
                Move your in-progress work onto <span className="font-mono">{confirmMove}</span> and switch to it?
                {current ? <> <span className="font-mono">{current.name}</span> rewinds to its latest milestone.</> : null}
              </span>
              <div className="flex justify-end gap-2">
                <button onClick={() => setConfirmMove(null)} className="px-2.5 py-1 text-[12px] font-medium rounded-md" style={{ color: "var(--text-secondary)" }}>
                  Cancel
                </button>
                <button
                  data-testid="branch-manager-confirm-move-go"
                  onClick={() => guardNavigation(() => { void doCreate(confirmMove, true) })}
                  className="px-2.5 py-1 text-[12px] font-semibold rounded-md"
                  style={{ background: "var(--structure-action)", color: "var(--text-on-accent)" }}
                >
                  Create &amp; Move
                </button>
              </div>
            </div>
          )}

          {/* Switch confirmation (with don't-ask-again, persisted) */}
          {confirmSwitch && (
            <div
              data-testid="branch-manager-confirm-switch"
              className="flex flex-col gap-2 px-2.5 py-2 rounded-md"
              style={{ background: "var(--accent-soft-faint)", border: "1px solid var(--accent-soft-strong)" }}
            >
              <span className="text-[12px]" style={{ color: "var(--text-primary)" }}>
                Switch to <span className="font-mono">{confirmSwitch.name}</span>? The canvas loads that
                branch&apos;s pipeline (undoable from the toolbar).
              </span>
              <label className="flex items-center gap-1.5 text-[11px]" style={{ color: "var(--text-secondary)" }}>
                <input
                  data-testid="branch-manager-dont-ask"
                  type="checkbox"
                  checked={dontAskSwitch}
                  onChange={(e) => setDontAskSwitch(e.target.checked)}
                />
                Don&apos;t ask again on this machine
              </label>
              <div className="flex justify-end gap-2">
                <button onClick={() => { setConfirmSwitch(null); setDontAskSwitch(false) }} className="px-2.5 py-1 text-[12px] font-medium rounded-md" style={{ color: "var(--text-secondary)" }}>
                  Cancel
                </button>
                <button
                  data-testid="branch-manager-confirm-switch-go"
                  onClick={() => switchNow(confirmSwitch, dontAskSwitch)}
                  className="px-2.5 py-1 text-[12px] font-semibold rounded-md"
                  style={{ background: "var(--structure-action)", color: "var(--text-on-accent)" }}
                >
                  Switch
                </button>
              </div>
            </div>
          )}

          {/* Archive-current needs a clean tree first (S38) */}
          {archiveDirty && (
            <div
              data-testid="branch-manager-archive-dirty"
              className="flex flex-col gap-2 px-2.5 py-2 rounded-md"
              style={{ background: "var(--warning-soft, var(--accent-soft-faint))", border: "1px solid var(--warning-border)" }}
            >
              <span className="text-[12px]" style={{ color: "var(--text-primary)" }}>
                <span className="font-mono">{archiveDirty.name}</span> has uncommitted tracked
                project changes. Create a milestone to capture them before archiving. In-memory
                canvas edits are protected separately and never ride into an archive.
              </span>
              <div className="flex justify-end gap-2">
                <button onClick={() => setArchiveDirty(null)} className="px-2.5 py-1 text-[12px] font-medium rounded-md" style={{ color: "var(--text-secondary)" }}>
                  Cancel
                </button>
                <button
                  data-testid="branch-manager-archive-dirty-commit"
                  onClick={() => { setArchiveDirty(null); openModal("milestone") }}
                  className="px-2.5 py-1 text-[12px] font-semibold rounded-md"
                  style={{ background: "var(--structure-action)", color: "var(--text-on-accent)" }}
                >
                  Commit a milestone…
                </button>
              </div>
            </div>
          )}

          {/* Delete confirmation */}
          {confirmDelete && (
            <div
              data-testid="branch-manager-confirm"
              className="flex flex-col gap-2 px-2.5 py-2 rounded-md"
              style={{ background: "var(--danger-soft-faint)", border: "1px solid var(--danger-soft-strong)" }}
            >
              <span className="text-[12px]" style={{ color: "var(--text-primary)" }}>
                Permanently delete <span className="font-mono">{confirmDelete.name}</span> and its history?
                {deleteLoss(confirmDelete) && (
                  <> This also discards {deleteLoss(confirmDelete)}.</>
                )}
                {confirmDelete.is_current && <> You&apos;ll be returned to the branch chooser.</>}
              </span>
              <div className="flex justify-end gap-2">
                <button onClick={() => setConfirmDelete(null)} className="px-2.5 py-1 text-[12px] font-medium rounded-md" style={{ color: "var(--text-secondary)" }}>
                  Cancel
                </button>
                <button
                  data-testid="branch-manager-confirm-delete"
                  onClick={() => {
                    const branch = confirmDelete
                    if (branch.is_current) guardNavigation(() => { void doDelete(branch) })
                    else void doDelete(branch)
                  }}
                  className="px-2.5 py-1 text-[12px] font-semibold rounded-md"
                  style={{ background: "var(--danger)", color: "var(--text-on-accent)" }}
                >
                  Delete
                </button>
              </div>
            </div>
          )}
        </div>
      )}

      {/* Right-click menu on a branch row: select / switch / archive / delete
          (restore replaces archive on archived rows). Actions route through
          the same handlers as the row buttons, dialogs included. */}
      {rowMenu && (
        <>
          <div
            className="fixed inset-0 z-40"
            onClick={() => setRowMenu(null)}
            onContextMenu={(e) => { e.preventDefault(); setRowMenu(null) }}
          />
          <div
            data-testid="branch-manager-row-menu"
            className="fixed z-50 rounded-md py-1 shadow-lg text-[12px]"
            style={{ left: rowMenu.x, top: rowMenu.y, background: "var(--bg-elevated)", border: "1px solid var(--border)" }}
          >
            <div className="px-3 py-1 font-mono text-[10px] max-w-[220px] truncate" style={{ color: "var(--text-muted)" }}>
              {rowMenu.b.name}
            </div>
            <button
              data-testid="branch-manager-row-menu-select"
              onClick={() => { onPeek?.(rowMenu.b.name); setRowMenu(null) }}
              className="flex items-center gap-1.5 px-3 py-1.5 w-full text-left hover:bg-[var(--bg-hover)]"
              style={{ color: "var(--text-primary)" }}
            >
              <GitBranch size={12} style={{ color: "var(--accent)" }} /> Select (view history)
            </button>
            {!rowMenu.b.is_archived && !rowMenu.b.is_current && (
              <button
                data-testid="branch-manager-row-menu-switch"
                onClick={() => { const b = rowMenu.b; setRowMenu(null); onSwitchClick(b) }}
                disabled={anyBusy}
                className="flex items-center gap-1.5 px-3 py-1.5 w-full text-left hover:bg-[var(--bg-hover)] disabled:opacity-40"
                style={{ color: "var(--text-primary)" }}
              >
                <ArrowRightLeft size={12} style={{ color: "var(--accent)" }} /> Switch to this branch
              </button>
            )}
            {!rowMenu.b.is_archived ? (
              <button
                data-testid="branch-manager-row-menu-archive"
                onClick={() => { const b = rowMenu.b; setRowMenu(null); onArchiveClick(b) }}
                disabled={anyBusy}
                className="flex items-center gap-1.5 px-3 py-1.5 w-full text-left hover:bg-[var(--bg-hover)] disabled:opacity-40"
                style={{ color: "var(--text-primary)" }}
              >
                <Archive size={12} style={{ color: "var(--warning)" }} /> Archive
              </button>
            ) : (
              <button
                data-testid="branch-manager-row-menu-restore"
                onClick={() => { const b = rowMenu.b; setRowMenu(null); void restore(b) }}
                disabled={anyBusy}
                className="flex items-center gap-1.5 px-3 py-1.5 w-full text-left hover:bg-[var(--bg-hover)] disabled:opacity-40"
                style={{ color: "var(--text-primary)" }}
              >
                <RotateCcw size={12} style={{ color: "var(--success)" }} /> Restore
              </button>
            )}
            <button
              data-testid="branch-manager-row-menu-delete"
              onClick={() => { const b = rowMenu.b; setRowMenu(null); setConfirmDelete(b) }}
              disabled={anyBusy}
              className="flex items-center gap-1.5 px-3 py-1.5 w-full text-left hover:bg-[var(--bg-hover)] disabled:opacity-40"
              style={{ color: "var(--danger)" }}
            >
              <Trash2 size={12} /> Delete
            </button>
          </div>
        </>
      )}
    </div>
  )
}

/** Human phrase for what a delete throws away beyond the lineage itself. */
function deleteLoss(b: GitManagedBranch): string | null {
  const parts: string[] = []
  if (b.has_uncommitted_changes) parts.push("your uncommitted edits")
  if (b.has_unmerged_saves) parts.push("saves not yet in a milestone")
  return parts.length ? parts.join(" and ") : null
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
  onContextMenu,
}: {
  b: GitManagedBranch
  anyBusy: boolean
  selected: boolean
  onPeek: () => void
  onSwitch: () => void
  onArchive: () => void
  onRestore: () => void
  onDelete: () => void
  onContextMenu: (e: React.MouseEvent) => void
}) {
  // Archive of the *current* branch needs a clean tree; when dirty the action
  // routes through a save dialog instead of being blocked. Delete is always
  // allowed (it discards the tree by intent, behind a confirm) — never greyed.
  const archiveBlocked = !b.is_current && b.has_uncommitted_changes // others can't be dirty, defensive
  const archiveDisabled = anyBusy || archiveBlocked

  return (
    <div
      data-testid="branch-manager-branch"
      className="flex items-center gap-1.5 px-1.5 py-1 rounded-md"
      onContextMenu={onContextMenu}
      // The current branch shows selection via its box; other rows get the
      // bright outline so a peeked non-current branch reads clearly (S38).
      style={{ outline: selected && !b.is_current ? "1px solid var(--accent)" : undefined }}
    >
      {/* Consistent leading branch/archive icon (the box edge marks "current"). */}
      <span className="inline-flex items-center shrink-0">
        {b.is_archived ? (
          <Archive size={12} style={{ color: "var(--text-muted)" }} />
        ) : (
          <GitBranch size={12} style={{ color: b.is_current ? "var(--accent)" : "var(--text-muted)" }} />
        )}
      </span>

      <button
        onClick={onPeek}
        title="View this branch's history"
        className="flex-1 text-[12px] font-mono truncate text-left hover:underline min-w-0"
        style={{ color: b.is_current ? "var(--accent)" : b.is_archived ? "var(--text-muted)" : "var(--text-primary)" }}
      >
        {b.name}
      </button>

      {/* State indicators (distinct) */}
      {b.has_uncommitted_changes && (
        <Tooltip label="Uncommitted changes in the working tree">
          <span data-testid="branch-manager-uncommitted" className="text-[10px]" style={{ color: "var(--warning)" }}>
            uncommitted
          </span>
        </Tooltip>
      )}
      {b.has_unmerged_saves && (
        <Tooltip label="Saves not yet committed to a milestone">
          <span data-testid="branch-manager-unsaved" className="text-[10px]" style={{ color: "var(--text-muted)" }}>
            unsaved
          </span>
        </Tooltip>
      )}

      {/* Leading action slot: a 'current' pill or the Switch button, matched in
          width so the uncommitted/unsaved indicators align at the right (S38). */}
      {b.is_current ? (
        <span
          data-testid="branch-manager-current"
          className="w-14 text-center shrink-0 text-[10px] py-0.5 rounded"
          style={{ background: "var(--accent-soft)", color: "var(--accent)" }}
        >
          current
        </span>
      ) : !b.is_archived ? (
        <Tooltip label="Switch to this working branch">
          <button
            data-testid="branch-manager-switch"
            onClick={onSwitch}
            disabled={anyBusy}
            className="w-14 text-center py-0.5 text-[10px] font-medium rounded transition-colors hover:bg-[var(--bg-hover)] disabled:opacity-40"
            style={{ color: "var(--text-secondary)" }}
          >
            Switch
          </button>
        </Tooltip>
      ) : null}
      {b.is_archived && (
        <Tooltip label="Restore (un-archive) this branch">
          <button
            data-testid="branch-manager-restore"
            onClick={onRestore}
            disabled={anyBusy}
            className="p-1 rounded transition-colors text-[var(--text-muted)] hover:bg-[var(--success-soft)] hover:text-[var(--success)] disabled:opacity-40"
          >
            <RotateCcw size={12} />
          </button>
        </Tooltip>
      )}
      {/* Archive: on the current branch it requires a milestone when dirty;
          archived branches can't be re-archived. */}
      {!b.is_archived && (
        <Tooltip label={b.is_current && b.has_uncommitted_changes ? "Commit changes before archiving" : "Archive working branch"}>
          <button
            data-testid="branch-manager-archive"
            onClick={onArchive}
            disabled={archiveDisabled}
            className="p-1 rounded transition-colors text-[var(--text-muted)] hover:bg-[var(--warning-soft,var(--bg-hover))] hover:text-[var(--warning)] disabled:opacity-40"
          >
            <Archive size={12} />
          </button>
        </Tooltip>
      )}
      {!b.is_archived && (
        <Tooltip label="Delete this working branch and its history">
          <button
            data-testid="branch-manager-delete"
            onClick={onDelete}
            disabled={anyBusy}
            className="p-1 rounded transition-colors text-[var(--text-muted)] hover:bg-[var(--danger-soft)] hover:text-[var(--danger)] disabled:opacity-40"
          >
            <Trash2 size={12} />
          </button>
        </Tooltip>
      )}
    </div>
  )
}
