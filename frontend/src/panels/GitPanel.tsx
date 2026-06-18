import { useState, useEffect, useCallback, useRef } from "react"
import {
  GitFork, GitBranch, Clock, ChevronRight, ChevronDown, RefreshCw, History,
  Pencil, Plus, Minus, ArrowRightLeft, Copy, CornerDownRight, FileText, Eye,
} from "lucide-react"
import PanelShell from "./PanelShell"
import BranchManager from "../components/BranchManager"
import RemotePushControl from "../components/RemotePushControl"
import Tooltip from "../components/Tooltip"
import useToastStore from "../stores/useToastStore"
import useGitStore from "../stores/useGitStore"
import {
  createWorkingBranch, getMilestones, getMilestoneSaves, getPendingSaves, getWorkingBranches,
} from "../api/client"
import type { GitMilestoneEntry, GitLedgerSave, GitFileChange, GitManagedBranch } from "../api/types"

const HASH_TOOLTIP =
  "Commit hash — a unique ID for every save or milestone. Fragment of a much " +
  "longer hexadecimal string."

interface GitPanelProps {
  onClose: () => void
}

// Per-milestone expansion state: undefined = collapsed, "loading" = fetching,
// array = the folded ledger saves.
type ExpandState = Record<string, GitLedgerSave[] | "loading">

export default function GitPanel({ onClose }: GitPanelProps) {
  const addToast = useToastStore((s) => s.addToast)
  const status = useGitStore((s) => s.status)
  const loadStatus = useGitStore((s) => s.loadStatus)
  // Peek state lives in the store so the toolbar indicator can return to the
  // current branch without the panel being open (S38).
  const viewBranch = useGitStore((s) => s.peekBranch)
  const setViewBranch = useGitStore((s) => s.setPeekBranch)
  // Bumped after a save so we re-fetch without a manual refresh; a save must not
  // move the selection. A commit bumps a separate nonce and DOES select (S38).
  const historyNonce = useGitStore((s) => s.historyNonce)
  const commitNonce = useGitStore((s) => s.commitNonce)
  // Bumped when the toolbar commit-SHA is clicked: select the latest save.
  const selectLatestSaveNonce = useGitStore((s) => s.selectLatestSaveNonce)

  const [milestones, setMilestones] = useState<GitMilestoneEntry[]>([])
  const [pending, setPending] = useState<GitLedgerSave[]>([])
  const [expanded, setExpanded] = useState<ExpandState>({})
  const [loading, setLoading] = useState(false)
  // Selected commit sha — a save (clicked) or a milestone (just committed).
  // Highlight only for now (S38). Set on a save-row click or after a milestone
  // commit; cleared when peeking a different branch.
  const [selectedSha, setSelectedSha] = useState<string | null>(null)
  // Working branches keyed by the commit they were spawned from, so a milestone
  // or save can back-link to the branch(es) it spawned (S38).
  const [forkBranches, setForkBranches] = useState<GitManagedBranch[]>([])
  // Right-click "new branch from here" (S38): the anchor is the menu position +
  // fork point; the draft is the naming step once an option is picked.
  const [forkAnchor, setForkAnchor] = useState<{ x: number; y: number; sha: string; canMove: boolean } | null>(null)
  const [forkDraft, setForkDraft] = useState<{ sha: string; move: boolean; name: string; x: number; y: number } | null>(null)
  const [forking, setForking] = useState(false)

  const workingBranch = status?.working_branch ?? null
  const peeking = viewBranch !== null && viewBranch !== workingBranch

  // ---------------------------------------------------------------------------
  // Data
  // ---------------------------------------------------------------------------

  const refresh = useCallback(async (): Promise<
    { milestones: GitMilestoneEntry[]; pending: GitLedgerSave[] } | null
  > => {
    setLoading(true)
    try {
      const [ms, ps, wb] = await Promise.all([
        getMilestones(50, viewBranch),
        getPendingSaves(viewBranch),
        getWorkingBranches(),
      ])
      setMilestones(ms.entries)
      setPending(ps.saves)
      setForkBranches(wb.branches.filter((b) => b.forked_from))
      // NB: don't clear `expanded` here — that would collapse a milestone the
      // user opened on every auto-refresh. Expansion is reset only on a peek
      // change (the effect below), where the milestones genuinely differ.
      return { milestones: ms.entries, pending: ps.saves }
    } catch (err) {
      const detail = err instanceof Error ? err.message : "unknown error"
      addToast("error", `Failed to load version history: ${detail}`)
      return null
    } finally {
      setLoading(false)
    }
  }, [addToast, viewBranch])

  // The commit effect needs the latest peek state without re-subscribing on
  // every peek change (which would re-fire a stale commit selection).
  const peekingRef = useRef(peeking)
  peekingRef.current = peeking

  useEffect(() => {
    loadStatus()
    refresh()
  }, [loadStatus, refresh])

  // Peeking a different branch shows a different history — reset expansion and
  // clear the selection (it referred to the previous branch's save).
  useEffect(() => {
    setExpanded({})
    setSelectedSha(null)
  }, [viewBranch])

  // Auto-refresh after a SAVE elsewhere. The new save is shown by the refetch
  // (a new out-of-version save, or a new milestone at the top of the list,
  // collapsed); the selection is left untouched (S38).
  useEffect(() => {
    if (historyNonce > 0) void refresh()
  }, [historyNonce, refresh])

  // After a milestone COMMIT, refetch and select the new milestone (top of the
  // list, collapsed) so the user sees what they just recorded — but only when
  // viewing the current branch's own history (a commit can't touch a peeked
  // branch, so selecting its top milestone would be wrong), S38.
  useEffect(() => {
    if (commitNonce === 0) return
    void refresh().then((res) => {
      if (res && res.milestones.length > 0 && !peekingRef.current) {
        setSelectedSha(res.milestones[0].sha)
      }
    })
  }, [commitNonce, refresh])

  // Toolbar commit-SHA click → select the LATEST save (the ledger tip): the newest
  // out-of-version save if any, else the newest save inside the latest milestone
  // (expanded so it's visible). Each nonce bump is processed once, even if the
  // panel was just opened by the same click (S38).
  const processedSelectNonce = useRef(0)
  useEffect(() => {
    if (selectLatestSaveNonce === 0 || selectLatestSaveNonce === processedSelectNonce.current) {
      return
    }
    processedSelectNonce.current = selectLatestSaveNonce
    void refresh().then(async (res) => {
      if (!res || peekingRef.current) return
      if (res.pending.length > 0) {
        setSelectedSha(res.pending[0].sha)
        return
      }
      if (res.milestones.length > 0) {
        const top = res.milestones[0]
        try {
          const saves = await getMilestoneSaves(top.sha)
          setExpanded((prev) => ({ ...prev, [top.sha]: saves.saves }))
          if (saves.saves.length > 0) setSelectedSha(saves.saves[0].sha)
        } catch {
          // Best-effort: leave the milestone collapsed if its saves won't load.
        }
      }
    })
  }, [selectLatestSaveNonce, refresh])

  const toggleExpand = useCallback(
    async (sha: string) => {
      if (expanded[sha]) {
        setExpanded((prev) => {
          const next = { ...prev }
          delete next[sha]
          return next
        })
        return
      }
      setExpanded((prev) => ({ ...prev, [sha]: "loading" }))
      try {
        const res = await getMilestoneSaves(sha)
        // Only land the result if this expansion is still the pending one — a
        // collapse (key deleted) or a refresh (map cleared) between request and
        // response must not resurrect a milestone the user moved on from.
        setExpanded((prev) => (prev[sha] === "loading" ? { ...prev, [sha]: res.saves } : prev))
      } catch (err) {
        const detail = err instanceof Error ? err.message : "unknown error"
        addToast("error", `Failed to load the saves in this milestone: ${detail}`)
        setExpanded((prev) => {
          if (prev[sha] !== "loading") return prev
          const next = { ...prev }
          delete next[sha]
          return next
        })
      }
    },
    [expanded, addToast],
  )

  // Fork-from-history is only meaningful on the current branch's own history
  // (the engine forks from the current working branch); disabled while peeking.
  const openForkMenu = (e: React.MouseEvent, sha: string, canMove: boolean) => {
    if (peeking) return
    e.preventDefault()
    setForkAnchor({ x: e.clientX, y: e.clientY, sha, canMove })
  }

  const startFork = (move: boolean) => {
    if (!forkAnchor) return
    setForkDraft({ sha: forkAnchor.sha, move, name: "", x: forkAnchor.x, y: forkAnchor.y })
    setForkAnchor(null)
  }

  const submitFork = async () => {
    if (!forkDraft || !forkDraft.name.trim()) return
    setForking(true)
    try {
      const res = await createWorkingBranch(forkDraft.name.trim(), {
        at: forkDraft.sha,
        move: forkDraft.move,
      })
      addToast(
        "success",
        forkDraft.move
          ? `Created ${res.working_branch} and moved your work`
          : `Created ${res.working_branch}`,
      )
      setForkDraft(null)
      if (res.switched) {
        window.location.reload()
        return
      }
      await refresh()
    } catch (err) {
      const detail = err instanceof Error ? err.message : "unknown error"
      addToast("error", `Could not create branch: ${detail}`)
    } finally {
      setForking(false)
    }
  }

  const forksAt = (sha: string): GitManagedBranch[] =>
    forkBranches.filter((b) => b.forked_from === sha)

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------

  return (
    <PanelShell
      testId="git-panel"
      title="Version Control"
      onClose={onClose}
      maxWidth={768}
      icon={<GitFork size={14} style={{ color: "var(--success)" }} />}
      // Branch + commit are not repeated here — the toolbar indicator beside this
      // panel already shows them (S38).
      actions={
        <Tooltip label="Refresh version history" side="bottom">
          <button
            data-testid="git-panel-refresh"
            onClick={refresh}
            disabled={loading}
            className="p-1 rounded shrink-0 transition-colors disabled:opacity-40 hover:bg-[var(--bg-hover)]"
            style={{ color: "var(--text-muted)" }}
          >
            <RefreshCw size={12} className={loading ? "animate-spin" : undefined} />
          </button>
        </Tooltip>
      }
    >
      <div className="flex-1 min-h-0 overflow-y-auto">
        {/* Deliberate push to a remote (S16/S33). Out-of-version saves drive its
            pre-push integrity prompt; ahead/behind re-fetch after a save/commit. */}
        <div className="pb-2" style={{ borderBottom: "1px solid var(--border)" }}>
          <RemotePushControl
            pendingSaveCount={pending.length}
            refreshNonce={historyNonce + commitNonce}
          />
        </div>

        {/* Branch manager (S19/S28: the Git panel hosts it) */}
        <BranchManager selectedBranch={viewBranch ?? workingBranch} onPeek={setViewBranch} />

        {/* Save history — a distinct, inset section set apart from the branch
            list above (BranchManager already draws the seam border); the inset
            keeps the banner + dividers narrow so the section reads as one
            coherent group (S38). */}
        <div className="px-2 pt-3 pb-2 flex flex-col gap-2">
          <div className="flex items-center gap-1.5 px-1">
            <History size={13} style={{ color: "var(--text-muted)" }} />
            <span className="text-[11px] font-medium uppercase tracking-wider" style={{ color: "var(--text-muted)" }}>
              Save history in branch
            </span>
          </div>

          {/* Peeking-at-another-branch banner */}
          {peeking && (
            <div
              data-testid="git-panel-peeking"
              className="px-2.5 py-1.5 rounded-md flex items-center gap-2 text-[11px]"
              style={{ background: "var(--accent-soft-faint)", border: "1px solid var(--accent-soft-strong)", color: "var(--text-secondary)" }}
            >
              <Eye size={11} style={{ color: "var(--accent)", flexShrink: 0 }} />
              <span className="flex-1 truncate">
                Viewing <span className="font-mono">{viewBranch}</span> (not current)
              </span>
              <button
                data-testid="git-panel-peek-clear"
                onClick={() => setViewBranch(null)}
                className="shrink-0 hover:underline"
                style={{ color: "var(--accent)" }}
              >
                Show current
              </button>
            </div>
          )}

          {/* Out-of-version saves — what the next commit would fold in */}
          {pending.length > 0 && (
            <div
              data-testid="git-panel-pending"
              className="px-2.5 py-2 rounded-md"
              style={{ border: "1px solid var(--border)", background: "var(--accent-soft-faint)" }}
            >
              <span
                className="text-[10px] font-medium uppercase tracking-wider block mb-1.5"
                style={{ color: "var(--text-muted)" }}
              >
                Out-of-version saves ({pending.length}) — to fold into next milestone
              </span>
              <div className="flex flex-col gap-1.5 pl-2">
                {pending.map((s) => (
                  <SaveRow
                    key={s.sha}
                    save={s}
                    testId="git-panel-pending-save"
                    forkLinks={forksAt(s.sha)}
                    onPeek={setViewBranch}
                    selected={selectedSha === s.sha}
                    onSelect={setSelectedSha}
                    onContextMenu={(e) => openForkMenu(e, s.sha, true)}
                  />
                ))}
              </div>
            </div>
          )}

          {/* Milestone spine */}
          {loading && milestones.length === 0 ? (
            <div data-testid="git-panel-loading" className="py-6 text-center">
              <span className="text-[11px]" style={{ color: "var(--text-muted)" }}>
                Loading version history…
              </span>
            </div>
          ) : milestones.length === 0 ? (
            <div data-testid="git-panel-empty" className="py-6 text-center">
              <Clock size={18} className="mx-auto mb-2" style={{ color: "var(--text-muted)" }} />
              <p className="text-[12px]" style={{ color: "var(--text-secondary)" }}>
                No milestones yet.
              </p>
              <p className="text-[11px] mt-1" style={{ color: "var(--text-muted)" }}>
                Use Save &amp; commit in the toolbar to record one.
              </p>
            </div>
          ) : (
            <div
              data-testid="git-panel-milestones"
              className="rounded-md overflow-hidden"
              style={{ border: "1px solid var(--border)" }}
            >
            {milestones.map((m, idx) => {
              const exp = expanded[m.sha]
              const isOpen = exp !== undefined
              return (
                <div
                  key={m.sha}
                  style={{ borderBottom: idx < milestones.length - 1 ? "1px solid var(--border)" : undefined }}
                >
                  <button
                    data-testid="git-panel-milestone"
                    data-selected={selectedSha === m.sha || undefined}
                    onClick={() => toggleExpand(m.sha)}
                    onContextMenu={(e) => openForkMenu(e, m.sha, idx === 0)}
                    className="w-full flex items-start gap-1.5 px-3 py-2 text-left transition-colors hover:bg-[var(--bg-hover)]"
                    style={selectedSha === m.sha ? { background: "var(--accent-soft)" } : undefined}
                  >
                    <span className="mt-0.5 shrink-0" style={{ color: "var(--text-muted)" }}>
                      {isOpen ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
                    </span>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-baseline gap-1.5">
                        {m.version_label && (
                          <span
                            data-testid="git-panel-milestone-label"
                            className="text-[10px] px-1 py-0.5 rounded font-mono shrink-0"
                            style={{ background: "var(--accent-soft)", color: "var(--accent)" }}
                          >
                            {m.version_label}
                          </span>
                        )}
                        <span className="text-[12px] truncate flex-1" style={{ color: "var(--text-primary)" }}>
                          {m.message}
                        </span>
                        <ForkLinks branches={forksAt(m.sha)} onPeek={setViewBranch} />
                        <span className="text-[10px] font-mono shrink-0" style={{ color: "var(--text-secondary)" }}>
                          <Tooltip label={HASH_TOOLTIP} side="bottom">
                            <span>{m.short_sha}</span>
                          </Tooltip>
                          {" · "}{timeAgo(m.timestamp)}
                        </span>
                      </div>
                    </div>
                  </button>

                  {isOpen && (
                    <div className="pl-7 pr-3 pb-2">
                      {exp === "loading" ? (
                        <span className="text-[11px]" style={{ color: "var(--text-muted)" }}>
                          Loading saves…
                        </span>
                      ) : exp.length === 0 ? (
                        <span className="text-[11px]" style={{ color: "var(--text-muted)" }}>
                          No individual saves recorded for this milestone.
                        </span>
                      ) : (
                        <div className="flex flex-col gap-1.5">
                          {exp.map((s) => (
                            <SaveRow
                              key={s.sha}
                              save={s}
                              testId="git-panel-save"
                              forkLinks={forksAt(s.sha)}
                              onPeek={setViewBranch}
                              selected={selectedSha === s.sha}
                              onSelect={setSelectedSha}
                            />
                          ))}
                        </div>
                      )}
                    </div>
                  )}
                </div>
              )
            })}
            </div>
          )}
        </div>
      </div>

      {/* Right-click "new branch from here" menu (S38) */}
      {forkAnchor && (
        <>
          <div className="fixed inset-0 z-40" onClick={() => setForkAnchor(null)} />
          <div
            data-testid="git-panel-fork-menu"
            className="fixed z-50 rounded-md py-1 shadow-lg text-[12px]"
            style={{ left: forkAnchor.x, top: forkAnchor.y, background: "var(--bg-elevated)", border: "1px solid var(--border)" }}
          >
            <button
              data-testid="git-panel-fork-here"
              onClick={() => startFork(false)}
              className="flex items-center gap-1.5 px-3 py-1.5 w-full text-left hover:bg-[var(--bg-hover)]"
              style={{ color: "var(--text-primary)" }}
            >
              <GitBranch size={12} style={{ color: "var(--accent)" }} /> New branch from here
            </button>
            {forkAnchor.canMove && (
              <button
                data-testid="git-panel-fork-move"
                onClick={() => startFork(true)}
                className="flex items-center gap-1.5 px-3 py-1.5 w-full text-left hover:bg-[var(--bg-hover)]"
                style={{ color: "var(--text-primary)" }}
              >
                <ArrowRightLeft size={12} style={{ color: "var(--accent)" }} /> New branch &amp; move work here
              </button>
            )}
          </div>
        </>
      )}

      {/* Naming step for the fork */}
      {forkDraft && (
        <>
          <div className="fixed inset-0 z-40" onClick={() => !forking && setForkDraft(null)} />
          <div
            data-testid="git-panel-fork-dialog"
            className="fixed z-50 rounded-lg p-3 w-[260px] flex flex-col gap-2 shadow-lg"
            style={{ left: forkDraft.x, top: forkDraft.y, background: "var(--bg-panel)", border: "1px solid var(--border)" }}
          >
            <span className="text-[12px] font-medium" style={{ color: "var(--text-primary)" }}>
              {forkDraft.move ? "New branch — move your work here" : "New branch from this point"}
            </span>
            <input
              autoFocus
              data-testid="git-panel-fork-name"
              value={forkDraft.name}
              onChange={(e) => setForkDraft({ ...forkDraft, name: e.target.value })}
              onKeyDown={(e) => { if (e.key === "Enter") void submitFork() }}
              placeholder="New branch name…"
              className="px-2 py-1 text-[12px] rounded-md focus:outline-none focus:ring-2"
              style={{ background: "var(--bg-input)", border: "1px solid var(--border)", color: "var(--text-primary)", caretColor: "var(--accent)" }}
            />
            <div className="flex justify-end gap-2">
              <button onClick={() => setForkDraft(null)} disabled={forking} className="px-2.5 py-1 text-[12px] rounded-md" style={{ color: "var(--text-secondary)" }}>
                Cancel
              </button>
              <button
                data-testid="git-panel-fork-create"
                onClick={() => void submitFork()}
                disabled={forking || forkDraft.name.trim() === ""}
                className="px-2.5 py-1 text-[12px] font-semibold rounded-md disabled:opacity-50"
                style={{ background: "var(--structure-action)", color: "var(--text-on-accent)" }}
              >
                {forkDraft.move ? "Create & Move" : "Create"}
              </button>
            </div>
          </div>
        </>
      )}
    </PanelShell>
  )
}

// ---------------------------------------------------------------------------
// One ledger save: message + sha/time on one line, then rename-aware file changes.
// ---------------------------------------------------------------------------

function SaveRow({
  save,
  testId,
  forkLinks,
  onPeek,
  selected,
  onSelect,
  onContextMenu,
}: {
  save: GitLedgerSave
  testId: string
  forkLinks?: GitManagedBranch[]
  onPeek?: (name: string) => void
  selected?: boolean
  onSelect?: (sha: string) => void
  onContextMenu?: (e: React.MouseEvent) => void
}) {
  return (
    <div
      data-testid={testId}
      data-selected={selected || undefined}
      onClick={onSelect ? () => onSelect(save.sha) : undefined}
      onContextMenu={onContextMenu}
      className={`flex flex-col gap-0.5 rounded px-1 -mx-1 ${onSelect ? "cursor-pointer" : ""}`}
      style={selected ? { background: "var(--accent-soft)", outline: "1px solid var(--accent-soft-strong)" } : undefined}
    >
      <div className="flex items-baseline gap-2">
        <span className="text-[11px] truncate flex-1" style={{ color: "var(--text-primary)" }}>
          {save.message}
        </span>
        {/* Branch chip left of the hash (inline — doesn't push filenames down,
            and aligns with the milestone rows), S38. */}
        {forkLinks && forkLinks.length > 0 && onPeek && (
          <ForkLinks branches={forkLinks} onPeek={onPeek} />
        )}
        <span className="text-[10px] font-mono shrink-0" style={{ color: "var(--text-secondary)" }}>
          <Tooltip label={HASH_TOOLTIP} side="bottom">
            <span>{save.short_sha}</span>
          </Tooltip>
          {" · "}{timeAgo(save.timestamp)}
        </span>
      </div>
      {save.files.length > 0 && (
        <div className="flex flex-col gap-0.5 mt-0.5 pl-1">
          {save.files.map((f) => (
            <FileRow key={`${f.status}:${f.old_path ?? ""}:${f.path}`} file={f} />
          ))}
        </div>
      )}
    </div>
  )
}

// Status code → icon + human label (tooltip) + accent colour.
const STATUS_META: Record<
  string,
  { Icon: typeof Pencil; label: string; color: string }
> = {
  M: { Icon: Pencil, label: "Modified", color: "var(--text-secondary)" },
  A: { Icon: Plus, label: "Added", color: "var(--success)" },
  D: { Icon: Minus, label: "Deleted", color: "var(--danger)" },
  R: { Icon: ArrowRightLeft, label: "Renamed", color: "var(--accent)" },
  C: { Icon: Copy, label: "Copied", color: "var(--accent)" },
}

function FileRow({ file }: { file: GitFileChange }) {
  const meta = STATUS_META[file.status] ?? {
    Icon: FileText,
    label: file.status,
    color: "var(--text-muted)",
  }
  const Icon = meta.Icon
  const isRename = file.status === "R" && file.old_path
  return (
    <div
      data-testid="git-panel-file"
      className="text-[10px] font-mono flex items-start gap-1.5"
      style={{ color: "var(--text-secondary)" }}
    >
      <Tooltip label={meta.label} side="bottom" className="shrink-0 mt-0.5">
        <Icon size={11} style={{ color: meta.color }} aria-label={meta.label} />
      </Tooltip>
      {isRename ? (
        // Old above new so the two paths line up for comparison.
        <span className="flex flex-col min-w-0">
          <span className="truncate" style={{ color: "var(--text-muted)" }}>{file.old_path}</span>
          <span className="truncate inline-flex items-center gap-0.5" style={{ color: "var(--text-secondary)" }}>
            <CornerDownRight size={9} className="shrink-0" />
            {file.path}
          </span>
        </span>
      ) : (
        <span className="truncate">{file.path}</span>
      )}
    </div>
  )
}

// Back-links from a commit to the branch(es) spawned there (S38). Rendered as
// spans (not buttons) so they can live inside the milestone's <button> row;
// clicking PEEKS the branch (view, not switch). stopPropagation keeps a milestone
// row from toggling its expansion when a link is clicked.
function ForkLinks({
  branches,
  onPeek,
}: {
  branches: GitManagedBranch[]
  onPeek: (name: string) => void
}) {
  if (!branches.length) return null
  return (
    <span className="inline-flex flex-wrap items-center gap-1 shrink-0">
      {branches.map((b) => (
        <span
          key={b.name}
          role="button"
          tabIndex={0}
          data-testid="git-panel-fork-link"
          data-archived={b.is_archived || undefined}
          title={b.is_archived ? `View ${b.name} (archived)` : `View ${b.name}`}
          onClick={(e) => { e.stopPropagation(); onPeek(b.name) }}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === " ") { e.stopPropagation(); onPeek(b.name) }
          }}
          // Archived targets are partially greyed — still clearly clickable.
          className="inline-flex items-center gap-0.5 px-1 py-0.5 rounded text-[10px] font-mono max-w-[120px] cursor-pointer hover:underline"
          style={{
            background: "var(--accent-soft-faint)",
            color: "var(--accent)",
            border: "1px solid var(--accent-soft-strong)",
            opacity: b.is_archived ? 0.6 : 1,
          }}
        >
          <GitBranch size={9} className="shrink-0" />
          <span className="truncate">{b.name.split("/").pop() ?? b.name}</span>
        </span>
      ))}
    </span>
  )
}

function timeAgo(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime()
  const mins = Math.floor(diff / 60000)
  if (mins < 1) return "just now"
  if (mins < 60) return `${mins}m ago`
  const hours = Math.floor(mins / 60)
  if (hours < 24) return `${hours}h ago`
  const days = Math.floor(hours / 24)
  return `${days}d ago`
}
