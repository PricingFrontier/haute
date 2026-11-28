import { useEffect, useState } from "react"
import {
  GitBranch, Archive, Trash2, Plus, AlertTriangle,
  RotateCcw, ChevronRight, ChevronDown, ArrowRightLeft, ArrowDownToLine,
} from "lucide-react"

import {
  createWorkingBranch,
  getGitRemotes,
  getGitPrefs,
  gitArchiveBranch,
  gitDeleteBranch,
  restoreBranch,
  setGitPrefs,
  setWorkingBranch,
  undeleteBranch,
} from "../api/client"
import type { GitDeletedBranch, GitManagedBranch } from "../api/types"
import useGitStore, { gitStorePart } from "../stores/useGitStore"
import useDirtyNavigation from "../hooks/useDirtyNavigation"
import { chosenOverParts, discardCanvasEdits, discardFormEdits } from "../hooks/useProjectDirty"
import { useRequestKind } from "../hooks/useRequestKind"
import useToastStore from "../stores/useToastStore"
import { answer, ask, newest, under, type Asked } from "../utils/answers"
import { recordArchive, recordDelete, recordRestore, recordSwitch } from "../utils/vcHistory"
import { apiErrorMessage } from "../api/errors"
import BrokenPairs from "./BrokenPairs"
import Tooltip from "./Tooltip"
import BackdropMenu, { MenuItem } from "./BackdropMenu"
import GitNavigationConfirm from "./GitNavigationConfirm"
import { clearingTheWay } from "../utils/clearingTheWay"

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
 * relocates your in-progress work and switches), then archived, then the
 * deleted ones (collapsed), each restorable. Clicking a name
 * PEEKS its history (onPeek) without switching. Switching prompts a confirm with
 * a "don't ask again" that persists to the local environment.
 */
export default function BranchManager({ selectedBranch, onPeek, onSave }: BranchManagerProps) {
  const loadStatus = useGitStore((s) => s.loadStatus)
  const branches = useGitStore((s) => s.branches)
  const deletedBranches = useGitStore((s) => s.deletedBranches)
  const loadBranches = useGitStore((s) => s.loadBranches)
  const branchesExpandNonce = useGitStore((s) => s.branchesExpandNonce)
  // Undo/redo of a VC operation changes the branch forest from outside this
  // component — the nonce tells us to re-list.
  const historyNonce = useGitStore((s) => s.historyNonce)
  const commitNonce = useGitStore((s) => s.commitNonce)
  const addToast = useToastStore((s) => s.addToast)
  const navigation = useDirtyNavigation(onSave)

  const [newBranch, setNewBranch] = useState("")
  const [busy, setBusy] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const [collapsed, setCollapsed] = useState(false)
  const [createMenuOpen, setCreateMenuOpen] = useState(false)
  const [deletedOpen, setDeletedOpen] = useState(false)
  // The deletion being restored under another name, and that name.
  const [restoreAs, setRestoreAs] = useState<{ id: string; name: string } | null>(null)

  // Dialog state (one open at a time).
  const [confirmDelete, setConfirmDelete] = useState<GitManagedBranch | null>(null)
  const [confirmSwitch, setConfirmSwitch] = useState<GitManagedBranch | null>(null)
  const [confirmMove, setConfirmMove] = useState<string | null>(null)
  const [remoteNames, setRemoteNames] = useState<string[]>([])
  const [dontAskSwitch, setDontAskSwitch] = useState(false)

  // Persisted "don't ask again" for switching (loaded once; whole-environment).
  const [skipSwitchConfirm, setSkipSwitchConfirm] = useState(false)
  const listings = useRequestKind()
  const prefsAsked = useRequestKind()
  const life = useRequestKind()
  // A save/commit can start a refresh while the mount request is still in
  // flight. Only the newest request may publish its branch flags; otherwise an
  // older dirty listing can restore badges that the newer request just cleared.
  // The store keeps that order; here only the newest listing's failure is said.
  useEffect(() => {
    const listing = ask([newest(listings)])
    void listing.answer(loadBranches(), {
      failed: (err) => setActionError(`Failed to load branches: ${apiErrorMessage(err, "error")}`),
    })
    const prefs = ask([newest(prefsAsked)])
    // A preference not read leaves the switch confirmation on.
    void prefs.answer(getGitPrefs(), { ok: (p) => setSkipSwitchConfirm(p.skip_switch_confirm), failed: () => {} })
    return () => {
      listing.release()
      prefs.release()
    }
  }, [loadBranches, listings, prefsAsked])

  // The toolbar branch name asks the manager to expand (S38).
  useEffect(() => {
    if (branchesExpandNonce > 0) setCollapsed(false)
  }, [branchesExpandNonce])

  // Re-list after out-of-component history changes (VC undo/redo, saves and
  // milestone commits). The commit refresh is essential because the preceding
  // save refresh still sees its ledger entries as unmerged.
  useEffect(() => {
    if (historyNonce === 0 && commitNonce === 0) return
    const asked = ask([newest(listings)])
    void asked.answer(loadBranches({ refresh: true }), {
      failed: (err) => setActionError(`Failed to load branches: ${apiErrorMessage(err, "error")}`),
    })
    return () => asked.release()
  }, [historyNonce, commitNonce, loadBranches, listings])

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

  // A branch op and what it tells the app (toasts, history entries, readiness)
  // belong to the git store; the manager's own state (*here*), to the manager.
  const run = async <T,>(
    name: string,
    verb: string,
    request: Promise<T>,
    landed: (result: T, here: Asked, asked: Asked) => unknown,
    opts: { reloadWhen?: (result: T) => boolean; refused?: (err: unknown, here: Asked) => void } = {},
  ) => {
    setActionError(null)
    const here = ask([under(life)])
    const refused = (err: unknown) => {
      opts.refused?.(err, here)
      const detail = apiErrorMessage(err, "unknown error")
      here.apply(() => setActionError(`Could not ${verb}: ${detail}`)) // persistent
      addToast("error", `Could not ${verb}: ${detail}`) // splash
      // A file held open can leave the clone interrupted: show it in the toolbar.
      void loadStatus({ refresh: true })
    }
    try {
      await answer([gitStorePart()], request, {
        ok: (result, asked) => asked.answer(async () => landed(result, here, asked), {
          ok: () => {
            if (opts.reloadWhen?.(result)) {
              reloadApp()
              return
            }
            return asked.answer(loadStatus({ refresh: true }), { // not a read begun before the action
              // A branch op changes the fork forest — nudge the Git panel to refetch
              // its history + graph; BranchManager's nonce effect performs the one
              // shared branch-list refresh.
              ok: () => useGitStore.getState().notifyHistoryChanged(),
            })
          },
          failed: refused,
        }),
        failed: refused,
      }, { inFlight: (flying) => setBusy(flying ? name : null) })
    } finally {
      here.release()
    }
  }

  // -- create -----------------------------------------------------------------
  const doCreate = (name: string, move: boolean) =>
    run(name, move ? "create & move" : "create branch", clearingTheWay((...settled) => createWorkingBranch(name, { move }, ...settled)), (_, here) => {
      addToast("success", move ? `Created ${name} and moved your work` : `Created ${name}`)
      here.apply(() => {
        setNewBranch("")
        setConfirmMove(null)
      })
    }, { reloadWhen: (res) => res.switched === true }) // a move switches you over; a parallel create leaves you put

  const onCreateClick = () => {
    const name = newBranch.trim()
    if (name) void doCreate(name, false)
  }

  const onCreateMoveClick = () => {
    const name = newBranch.trim()
    setCreateMenuOpen(false)
    if (name) setConfirmMove(name)
  }

  // A branch started where a remote's default branch is now: the next change once a pull
  // request has merged. It opens the new branch as a switch does, in the app: unsaved edits
  // are asked about first, and the checkout lands on the canvas through the sync. It reads
  // the remote, which takes time, so what is dropped is what was chosen over when it was
  // asked: an edit made while it ran keeps every unsaved edit.
  const onCreateFromDefaultClick = (remote: string) => {
    const name = newBranch.trim()
    setCreateMenuOpen(false)
    if (!name) return
    navigation.guard(() => {
      const chosenOver = ask(chosenOverParts())
      void run(name, "create branch", clearingTheWay((...settled) => createWorkingBranch(name, { fromRemote: remote }, ...settled)), (_, here, asked) => {
        const drop = chosenOver.current()
        if (drop) discardCanvasEdits()
        return asked.answer(drop ? discardFormEdits() : Promise.resolve(), {
          ok: () => {
            addToast("success", `Created ${name} from ${remote}`)
            here.apply(() => setNewBranch(""))
          },
        })
      }).finally(() => chosenOver.release())
    })
  }

  // The remotes the menu offers to start from, read when it opens. A read that fails
  // offers none: the menu keeps what it always had.
  useEffect(() => {
    if (!createMenuOpen) return
    const asked = ask([under(life)])
    void asked.answer(getGitRemotes(), {
      ok: (res) => setRemoteNames(res.remotes.map((r) => r.name)),
      failed: () => setRemoteNames([]),
    })
    return () => asked.release()
  }, [createMenuOpen, life])

  // -- switch -----------------------------------------------------------------
  // In-app (feedback round 2): no page reload — the checkout lands on the
  // canvas via the websocket sync, and the completed switch is recorded as an
  // undoable history entry.
  const switchNow = (b: GitManagedBranch, dontAsk: boolean) => {
    navigation.guard(() => {
      if (dontAsk) {
        setSkipSwitchConfirm(true)
        // The switch goes ahead; a choice not saved is asked again next time, so say so.
        void answer([gitStorePart()], setGitPrefs({ skip_switch_confirm: true }), {
          failed: (err) => addToast("error", `Couldn't save "Don't ask again" on this machine: ${apiErrorMessage(err, "unknown error")}`),
        })
      }
      setConfirmSwitch(null)
      setDontAskSwitch(false)
      const from = branches.find((x) => x.is_current && !x.is_archived)?.name ?? null
      // What is dropped is what was chosen over when the switch was asked: an edit made while
      // it ran keeps every unsaved edit.
      const chosenOver = ask(chosenOverParts())
      void run(b.name, "switch", clearingTheWay((...settled) => setWorkingBranch(b.name, false, ...settled)), (_, _here, asked) => {
        const drop = chosenOver.current()
        if (drop) discardCanvasEdits()
        return asked.answer(drop ? discardFormEdits() : Promise.resolve(), {
          ok: () => {
            addToast("success", `Switched to ${b.name}`)
            if (from !== null) recordSwitch(from, b.name, onSave)
          },
        })
      }).finally(() => chosenOver.release())
    })
  }

  const onSwitchClick = (b: GitManagedBranch) => {
    if (skipSwitchConfirm) switchNow(b, false)
    else setConfirmSwitch(b)
  }

  // -- archive / delete / restore --------------------------------------------
  const onArchiveClick = (b: GitManagedBranch) => {
    // Files on disk off version history (a CSV saved in Excel) are the server's to name:
    // the archive is refused naming them, and clearingTheWay asks to record or put them back.
    const archive = () => {
      // Archiving the CURRENT branch moves you off it — that flow keeps the
      // full reload (and records no history entry, since a reload clears the
      // stacks anyway). Archiving any other branch is an in-app, undoable op.
      void run(b.name, "archive", clearingTheWay((...settled) => gitArchiveBranch(b.name, ...settled)), (res) => {
        addToast("success", `Archived ${b.name}`)
        if (!b.is_current) recordArchive(b.name, res.archived_as)
      }, { reloadWhen: () => b.is_current })
    }
    if (b.is_current) navigation.guard(archive)
    else archive()
  }

  // The delete names the branch as its confirmation showed it (*b*, taken when the
  // dialog opened): one another page has switched onto or saved to since is refused,
  // and the manager then lists it as it now is.
  const doDelete = (b: GitManagedBranch) => {
    const seen = { tip: b.tip, ledger_tip: b.ledger_tip, is_current: b.is_current, is_checked_out: b.is_checked_out }
    return run(b.name, "delete", gitDeleteBranch(b.name, seen, true), (res, here) => { // dialog already confirmed the loss
      here.apply(() => setConfirmDelete(null))
      addToast("success", `Deleted ${b.name}`)
      // Deletes are trash-preserving server-side, so undo (undelete) is an
      // instant ref restore. Current-branch deletes keep the reload flow.
      if (!b.is_current) recordDelete(b.name, res.trash_id, seen)
    }, {
      reloadWhen: () => b.is_current,
      refused: (_, here) => {
        here.apply(() => setConfirmDelete(null))
        useGitStore.getState().notifyHistoryChanged()
      },
    })
  }

  const restore = (b: GitManagedBranch) =>
    run(b.name, "restore", restoreBranch(b.name), (res) => {
      addToast("success", `Restored ${b.name}`)
      recordRestore(b.name, res.restored_as)
    })

  // A deletion whose name a branch has again restores under another name.
  const undelete = (d: GitDeletedBranch, asName: string | null) =>
    run(d.id, "restore", undeleteBranch(d.id, asName), (res, here) => {
      here.apply(() => setRestoreAs(null))
      addToast("success", `Restored ${res.branch}`)
    }, {
      refused: (err, here) => {
        // The listing hides ledgers, so a name can be taken by one it never
        // showed: the server's refusal opens the name box all the same.
        if (asName === null && apiErrorMessage(err, "").includes("another name")) {
          here.apply(() => setRestoreAs({ id: d.id, name: `${d.branch}-restored` }))
        }
      },
    })
  const nameTaken = (name: string) => branches.some((b) => b.name === name)
  const onUndeleteClick = (d: GitDeletedBranch) => {
    if (nameTaken(d.branch)) setRestoreAs({ id: d.id, name: `${d.branch}-restored` })
    else void undelete(d, null)
  }

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

          {navigation.confirm && <GitNavigationConfirm {...navigation.confirm} />}

          <BrokenPairs />

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
                {remoteNames.map((remote) => (
                  <button
                    key={remote}
                    type="button"
                    data-testid={`branch-manager-create-from-${remote}`}
                    onClick={() => onCreateFromDefaultClick(remote)}
                    className="flex items-center gap-1.5 px-2.5 py-1 text-[11px] w-full text-left whitespace-nowrap hover:bg-[var(--bg-hover)]"
                    style={{ color: "var(--text-primary)" }}
                  >
                    <ArrowDownToLine size={12} style={{ color: "var(--accent)" }} /> Create from {remote}&apos;s default branch
                  </button>
                ))}
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

          {/* Deleted: the trash, newest first, collapsed until asked for */}
          {deletedBranches.length > 0 && (
            <div data-testid="branch-manager-deleted" className="flex flex-col gap-0.5 pt-1.5" style={{ borderTop: "1px solid var(--border)" }}>
              <button
                type="button"
                data-testid="branch-manager-deleted-toggle"
                onClick={() => setDeletedOpen((o) => !o)}
                className="flex items-center gap-1.5 mb-0.5 text-left"
                style={{ color: "var(--text-muted)" }}
              >
                {deletedOpen ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
                <span className="text-[11px] font-medium uppercase tracking-wider">
                  Deleted ({deletedBranches.length})
                </span>
              </button>
              {deletedOpen && deletedBranches.map((d) => (
                <div key={d.id} data-testid="branch-manager-deleted-row" className="flex flex-col gap-1">
                  <div className="flex items-center gap-1.5 px-1.5 py-1">
                    <Trash2 size={12} className="shrink-0" style={{ color: "var(--text-muted)" }} />
                    <span className="flex-1 text-[12px] font-mono truncate min-w-0" style={{ color: "var(--text-muted)" }}>
                      {d.branch}
                    </span>
                    {d.deleted_at && (
                      <span className="text-[10px] shrink-0" style={{ color: "var(--text-muted)" }}>
                        {deletedWhen(d.deleted_at)}
                      </span>
                    )}
                    <Tooltip label={nameTaken(d.branch) ? "Restore under another name" : "Restore this branch"}>
                      <button
                        data-testid="branch-manager-undelete"
                        onClick={() => onUndeleteClick(d)}
                        disabled={anyBusy}
                        className="p-1 rounded transition-colors text-[var(--text-muted)] hover:bg-[var(--branch-restore-hover-bg)] hover:text-[var(--branch-restore-text)] disabled:opacity-40"
                      >
                        <RotateCcw size={12} />
                      </button>
                    </Tooltip>
                  </div>
                  {restoreAs?.id === d.id && (
                    <div className="flex gap-1.5 pl-[19px]">
                      <input
                        data-testid="branch-manager-restore-as-input"
                        aria-label="Restore as"
                        value={restoreAs.name}
                        onChange={(e) => setRestoreAs({ id: d.id, name: e.target.value })}
                        onKeyDown={(e) => {
                          if (e.key === "Enter" && restoreAs.name.trim()) {
                            e.preventDefault()
                            void undelete(d, restoreAs.name.trim())
                          } else if (e.key === "Escape") setRestoreAs(null)
                        }}
                        autoFocus
                        className="flex-1 px-2 py-1 text-[12px] font-mono rounded-md focus:outline-none focus:ring-2 min-w-0"
                        style={{ background: "var(--bg-input)", border: "1px solid var(--border)", color: "var(--text-primary)", caretColor: "var(--accent)" }}
                      />
                      <button
                        data-testid="branch-manager-restore-as-go"
                        onClick={() => void undelete(d, restoreAs.name.trim())}
                        disabled={restoreAs.name.trim() === "" || anyBusy}
                        className="px-2 py-0.5 text-[11px] font-semibold rounded-md shrink-0 disabled:opacity-50 disabled:cursor-not-allowed"
                        style={{ background: "var(--structure-action)", color: "var(--text-on-accent)" }}
                      >
                        Restore
                      </button>
                    </div>
                  )}
                </div>
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
                  onClick={() => navigation.guard(() => { void doCreate(confirmMove, true) })}
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

          {/* Delete confirmation */}
          {confirmDelete && (
            <div
              data-testid="branch-manager-confirm"
              className="flex flex-col gap-2 px-2.5 py-2 rounded-md"
              style={{ background: "var(--danger-soft-faint)", border: "1px solid var(--danger-soft-strong)" }}
            >
              <span className="text-[12px]" style={{ color: "var(--text-primary)" }}>
                Delete <span className="font-mono">{confirmDelete.name}</span>? {deleteOutcome(confirmDelete)}
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
                    if (branch.is_current) navigation.guard(() => { void doDelete(branch) })
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
        <BackdropMenu
          testId="branch-manager-row-menu"
          x={rowMenu.x}
          y={rowMenu.y}
          title={rowMenu.b.name}
          onClose={() => setRowMenu(null)}
        >
          <MenuItem
            testId="branch-manager-row-menu-select"
            icon={GitBranch}
            onClick={() => { onPeek?.(rowMenu.b.name); setRowMenu(null) }}
          >
            Select (view history)
          </MenuItem>
          {!rowMenu.b.is_archived && !rowMenu.b.is_current && (
            <MenuItem
              testId="branch-manager-row-menu-switch"
              icon={ArrowRightLeft}
              onClick={() => { const b = rowMenu.b; setRowMenu(null); onSwitchClick(b) }}
              disabled={anyBusy}
            >
              Switch to this branch
            </MenuItem>
          )}
          {!rowMenu.b.is_archived ? (
            <MenuItem
              testId="branch-manager-row-menu-archive"
              icon={Archive}
              iconColor="var(--warning)"
              onClick={() => { const b = rowMenu.b; setRowMenu(null); onArchiveClick(b) }}
              disabled={anyBusy}
            >
              Archive
            </MenuItem>
          ) : (
            <MenuItem
              testId="branch-manager-row-menu-restore"
              icon={RotateCcw}
              iconColor="var(--branch-restore-text)"
              onClick={() => { const b = rowMenu.b; setRowMenu(null); void restore(b) }}
              disabled={anyBusy}
            >
              Restore
            </MenuItem>
          )}
          <MenuItem
            testId="branch-manager-row-menu-delete"
            icon={Trash2}
            danger
            onClick={() => { const b = rowMenu.b; setRowMenu(null); setConfirmDelete(b) }}
            disabled={anyBusy}
          >
            Delete
          </MenuItem>
        </BackdropMenu>
      )}
    </div>
  )
}

/** When a branch was deleted, as a short local date and time. */
function deletedWhen(iso: string): string {
  const when = new Date(iso)
  if (Number.isNaN(when.getTime())) return ""
  return when.toLocaleString(undefined, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })
}

/**
 * What a delete does, as its dialog says it: the server keeps both of the branch's refs in
 * the trash, so the branch and its saves can be restored, while the current branch's
 * project changes since the last save are discarded with the checkout away.
 */
function deleteOutcome(b: GitManagedBranch): string {
  const kept = b.has_unmerged_saves
    ? "It goes to Deleted, with its saves not yet in a milestone, and can be restored from there."
    : "It goes to Deleted and can be restored from there."
  return b.has_uncommitted_changes ? `${kept} The project's changes since the last save are lost.` : kept
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
        <Tooltip label="Project files changed since the last save">
          <span data-testid="branch-manager-uncommitted" className="text-[10px]" style={{ color: "var(--warning)" }}>
            not saved
          </span>
        </Tooltip>
      )}
      {b.has_unmerged_saves && (
        <Tooltip label="Saves not yet committed to a milestone">
          <span data-testid="branch-manager-unsaved" className="text-[10px]" style={{ color: "var(--text-muted)" }}>
            saves to commit
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
            className="p-1 rounded transition-colors text-[var(--text-muted)] hover:bg-[var(--branch-restore-hover-bg)] hover:text-[var(--branch-restore-text)] disabled:opacity-40"
          >
            <RotateCcw size={12} />
          </button>
        </Tooltip>
      )}
      {/* Archive: on the current branch it requires a milestone when dirty;
          archived branches can't be re-archived. */}
      {!b.is_archived && (
        <Tooltip label="Archive working branch">
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
