import { useState, useEffect, useCallback, useRef } from "react"
import {
  GitBranch, GitFork, Plus, ChevronDown, Clock, ArrowDownToLine,
  ExternalLink, Archive, Trash2, RotateCcw, AlertTriangle,
} from "lucide-react"
import PanelShell from "./PanelShell"
import useClickOutside from "../hooks/useClickOutside"
import useToastStore from "../stores/useToastStore"
import {
  getGitStatus,
  listGitBranches,
  createGitBranch,
  switchGitBranch,
  gitSave,
  gitSubmit,
  getGitHistory,
  gitRevert,
  gitPull,
  gitArchiveBranch,
  gitDeleteBranch,
} from "../api/client"
import type { GitStatus, GitBranchInfo as GitBranchType, GitHistoryEntry } from "../api/types"

interface GitPanelProps {
  onClose: () => void
}

type View = "main" | "history"

export default function GitPanel({ onClose }: GitPanelProps) {
  const addToast = useToastStore((s) => s.addToast)

  // State
  const [status, setStatus] = useState<GitStatus | null>(null)
  const [branches, setBranches] = useState<GitBranchType[]>([])
  const [history, setHistory] = useState<GitHistoryEntry[]>([])
  const [view, setView] = useState<View>("main")
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  // Branch creation
  const [creating, setCreating] = useState(false)
  const [newBranchDesc, setNewBranchDesc] = useState("")

  // Branch list dropdown
  const [branchDropdownOpen, setBranchDropdownOpen] = useState(false)
  const branchDropdownRef = useRef<HTMLDivElement>(null)
  useClickOutside(branchDropdownRef, () => setBranchDropdownOpen(false), branchDropdownOpen)

  // Confirmation state
  const [confirmAction, setConfirmAction] = useState<{ type: "delete" | "archive" | "revert"; target: string; label: string } | null>(null)

  // ---------------------------------------------------------------------------
  // Data fetching
  // ---------------------------------------------------------------------------

  const refresh = useCallback(async () => {
    try {
      const [s, b] = await Promise.all([getGitStatus(), listGitBranches()])
      setStatus(s)
      setBranches(b.branches)
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load git status")
    }
  }, [])

  useEffect(() => { refresh() }, [refresh])

  const loadHistory = useCallback(async () => {
    try {
      const res = await getGitHistory(30)
      setHistory(res.entries)
    } catch (err) {
      const detail = err instanceof Error ? err.message : "unknown error"
      addToast("error", `Failed to load git history: ${detail}`)
      setHistory([])
    }
  }, [addToast])

  useEffect(() => {
    if (view === "history") loadHistory()
  }, [view, loadHistory])

  // ---------------------------------------------------------------------------
  // Actions
  // ---------------------------------------------------------------------------

  const handleCreateBranch = useCallback(async () => {
    const desc = newBranchDesc.trim()
    if (!desc) return
    setLoading(true)
    setError(null)
    try {
      await createGitBranch(desc)
      setCreating(false)
      setNewBranchDesc("")
      await refresh()
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create branch")
    } finally {
      setLoading(false)
    }
  }, [newBranchDesc, refresh])

  const handleSwitch = useCallback(async (branch: string) => {
    setLoading(true)
    setError(null)
    setBranchDropdownOpen(false)
    try {
      await switchGitBranch(branch)
      await refresh()
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to switch branch")
    } finally {
      setLoading(false)
    }
  }, [refresh])

  const handleSave = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      await gitSave()
      await refresh()
      if (view === "history") await loadHistory()
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save")
    } finally {
      setLoading(false)
    }
  }, [refresh, loadHistory, view])

  const handleSubmit = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const result = await gitSubmit()
      if (result.compare_url) {
        window.open(result.compare_url, "_blank")
      }
      await refresh()
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to submit")
    } finally {
      setLoading(false)
    }
  }, [refresh])

  const handlePull = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const result = await gitPull()
      if (result.conflict) {
        setError(result.conflict_message ?? "Merge conflict detected")
      }
      await refresh()
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to pull")
    } finally {
      setLoading(false)
    }
  }, [refresh])

  const handleRevert = useCallback(async (sha: string) => {
    setLoading(true)
    setError(null)
    setConfirmAction(null)
    try {
      await gitRevert(sha)
      await refresh()
      await loadHistory()
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to revert")
    } finally {
      setLoading(false)
    }
  }, [refresh, loadHistory])

  const handleArchive = useCallback(async (branch: string) => {
    setLoading(true)
    setError(null)
    setConfirmAction(null)
    try {
      await gitArchiveBranch(branch)
      await refresh()
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to archive")
    } finally {
      setLoading(false)
    }
  }, [refresh])

  const handleDelete = useCallback(async (branch: string) => {
    setLoading(true)
    setError(null)
    setConfirmAction(null)
    try {
      await gitDeleteBranch(branch)
      await refresh()
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to delete")
    } finally {
      setLoading(false)
    }
  }, [refresh])

  // ---------------------------------------------------------------------------
  // Derived state
  // ---------------------------------------------------------------------------

  const isOnMain = status?.is_main ?? true
  const isReadOnly = status?.is_read_only ?? true
  const currentBranch = status?.branch ?? "main"
  const changedFiles = status?.changed_files ?? []
  const yourBranches = branches.filter((b) => b.is_yours && !b.is_archived)
  const otherBranches = branches.filter((b) => !b.is_yours && !b.is_archived && !["main", "master", "develop"].includes(b.name))
  const archivedBranches = branches.filter((b) => b.is_archived)
  const protectedBranches = branches.filter((b) => ["main", "master", "develop"].includes(b.name))

  // ---------------------------------------------------------------------------
  // Time formatting
  // ---------------------------------------------------------------------------

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

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------

  return (
    <PanelShell
      testId="git-panel"
      title="Git"
      onClose={onClose}
      icon={<GitFork size={14} style={{ color: 'var(--success)' }} />}
    >
      {/* Error banner */}
      {error && (
        <div className="px-3 py-2 text-[11px] flex items-start gap-2 shrink-0" style={{ color: 'var(--danger)', background: 'var(--danger-soft-subtle)', borderBottom: '1px solid var(--border)' }}>
          <AlertTriangle size={12} className="shrink-0 mt-0.5" />
          <span className="flex-1">{error}</span>
          <button onClick={() => setError(null)} className="opacity-60 hover:opacity-100 shrink-0">✕</button>
        </div>
      )}

      {/* Confirmation dialog */}
      {confirmAction && (
        <div className="px-3 py-3 shrink-0" style={{ background: 'var(--danger-soft-faint)', borderBottom: '1px solid var(--border)' }}>
          <p className="text-[12px] mb-2" style={{ color: 'var(--text-primary)' }}>
            {confirmAction.type === "delete"
              ? `Permanently delete "${confirmAction.label}"? This cannot be undone.`
              : confirmAction.type === "archive"
              ? `Archive "${confirmAction.label}"? You can restore it later.`
              : `Revert to this version? A backup will be created.`}
          </p>
          <div className="flex gap-2">
            <button
              onClick={() => {
                if (confirmAction.type === "delete") handleDelete(confirmAction.target)
                else if (confirmAction.type === "archive") handleArchive(confirmAction.target)
                else handleRevert(confirmAction.target)
              }}
              className="px-3 py-1 text-[12px] font-medium rounded-md"
              style={{ background: confirmAction.type === "delete" ? 'var(--danger)' : 'var(--accent)', color: 'var(--text-on-accent)' }}
            >
              {confirmAction.type === "delete" ? "Delete" : confirmAction.type === "archive" ? "Archive" : "Revert"}
            </button>
            <button
              onClick={() => setConfirmAction(null)}
              className="px-3 py-1 text-[12px] font-medium rounded-md"
              style={{ color: 'var(--text-secondary)', background: 'var(--bg-input)' }}
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {/* Content */}
      <div className="flex-1 min-h-0 overflow-y-auto">
        {/* Branch info + switcher */}
        <div className="px-3 py-3" style={{ borderBottom: '1px solid var(--border)' }}>
          <div className="flex items-center gap-2 mb-2">
            <span className="text-[11px] font-medium" style={{ color: 'var(--text-muted)' }}>Branch</span>
            {isOnMain && (
              <span className="text-[10px] px-1.5 py-0.5 rounded font-mono" style={{ background: 'var(--danger-soft-strong)', color: 'var(--danger-text)' }}>
                read-only
              </span>
            )}
            {!isOnMain && isReadOnly && (
              <span className="text-[10px] px-1.5 py-0.5 rounded font-mono" style={{ background: 'var(--warning-soft-selected)', color: 'var(--warning)' }}>
                read-only
              </span>
            )}
          </div>

          {/* Branch dropdown */}
          <div className="relative" ref={branchDropdownRef}>
            <button
              onClick={() => setBranchDropdownOpen((v) => !v)}
              className="w-full flex items-center gap-1.5 px-2.5 py-1.5 text-[12px] font-mono rounded-md transition-colors"
              style={{
                background: branchDropdownOpen ? 'var(--accent-soft)' : 'var(--bg-input)',
                border: `1px solid ${branchDropdownOpen ? 'var(--accent)' : 'var(--border)'}`,
                color: 'var(--text-primary)',
              }}
            >
              <GitBranch size={12} style={{ color: 'var(--accent)' }} />
              <span className="flex-1 text-left truncate">{currentBranch}</span>
              <ChevronDown size={11} style={{ color: 'var(--text-muted)', transition: 'transform 150ms', transform: branchDropdownOpen ? 'rotate(180deg)' : undefined }} />
            </button>

            {branchDropdownOpen && (
              <div className="absolute top-full left-0 right-0 mt-1 rounded-lg shadow-2xl z-50 overflow-hidden max-h-[300px] overflow-y-auto" style={{ background: 'var(--bg-panel)', border: '1px solid var(--border)' }}>
                {/* Protected branches */}
                {protectedBranches.length > 0 && (
                  <div className="py-1">
                    {protectedBranches.map((b) => (
                      <BranchItem key={b.name} branch={b} currentBranch={currentBranch} onSwitch={handleSwitch} />
                    ))}
                  </div>
                )}

                {/* Your branches */}
                {yourBranches.length > 0 && (
                  <>
                    <div className="px-3 pt-2 pb-1" style={{ borderTop: '1px solid var(--border)' }}>
                      <span className="text-[10px] font-medium uppercase tracking-wider" style={{ color: 'var(--text-muted)' }}>Your branches</span>
                    </div>
                    <div className="py-1">
                      {yourBranches.map((b) => (
                        <BranchItem key={b.name} branch={b} currentBranch={currentBranch} onSwitch={handleSwitch} />
                      ))}
                    </div>
                  </>
                )}

                {/* Others' branches */}
                {otherBranches.length > 0 && (
                  <>
                    <div className="px-3 pt-2 pb-1" style={{ borderTop: '1px solid var(--border)' }}>
                      <span className="text-[10px] font-medium uppercase tracking-wider" style={{ color: 'var(--text-muted)' }}>Other branches</span>
                    </div>
                    <div className="py-1">
                      {otherBranches.map((b) => (
                        <BranchItem key={b.name} branch={b} currentBranch={currentBranch} onSwitch={handleSwitch} />
                      ))}
                    </div>
                  </>
                )}

                {/* Archived */}
                {archivedBranches.length > 0 && (
                  <>
                    <div className="px-3 pt-2 pb-1" style={{ borderTop: '1px solid var(--border)' }}>
                      <span className="text-[10px] font-medium uppercase tracking-wider" style={{ color: 'var(--text-muted)' }}>Archived</span>
                    </div>
                    <div className="py-1">
                      {archivedBranches.map((b) => (
                        <BranchItem key={b.name} branch={b} currentBranch={currentBranch} onSwitch={handleSwitch} />
                      ))}
                    </div>
                  </>
                )}
              </div>
            )}
          </div>
        </div>

        {/* Create branch (when on main) */}
        {isOnMain && (
          <div className="px-3 py-3" style={{ borderBottom: '1px solid var(--border)' }}>
            {creating ? (
              <form onSubmit={(e) => { e.preventDefault(); handleCreateBranch() }} className="flex flex-col gap-2">
                <label className="text-[11px] font-medium" style={{ color: 'var(--text-muted)' }}>
                  What are you working on?
                </label>
                <input
                  autoFocus
                  value={newBranchDesc}
                  onChange={(e) => setNewBranchDesc(e.target.value)}
                  placeholder="Update area factors"
                  className="w-full px-2.5 py-1.5 text-[12px] rounded-md focus:outline-none"
                  style={{ background: 'var(--bg-input)', border: '1px solid var(--accent)', color: 'var(--text-primary)' }}
                  disabled={loading}
                />
                <div className="flex gap-2">
                  <button
                    type="submit"
                    disabled={!newBranchDesc.trim() || loading}
                    className="px-3 py-1 text-[12px] font-medium text-white rounded-md transition-colors disabled:opacity-40"
                    style={{ background: 'var(--accent)' }}
                  >
                    Create branch
                  </button>
                  <button
                    type="button"
                    onClick={() => { setCreating(false); setNewBranchDesc("") }}
                    className="px-3 py-1 text-[12px] font-medium rounded-md"
                    style={{ color: 'var(--text-secondary)' }}
                  >
                    Cancel
                  </button>
                </div>
              </form>
            ) : (
              <button
                onClick={() => setCreating(true)}
                className="w-full flex items-center justify-center gap-2 px-3 py-2 text-[12px] font-medium rounded-md transition-colors hover:bg-[var(--accent-soft-hover)]"
                style={{ color: 'var(--accent)', background: 'var(--accent-soft)' }}
              >
                <Plus size={13} />
                Start editing (create branch)
              </button>
            )}
          </div>
        )}

        {/* Actions (when on an editable branch) */}
        {!isOnMain && !isReadOnly && (
          <>
            {/* Changed files */}
            {changedFiles.length > 0 && (
              <div className="px-3 py-2.5" style={{ borderBottom: '1px solid var(--border)' }}>
                <span className="text-[11px] font-medium block mb-1.5" style={{ color: 'var(--text-muted)' }}>
                  Unsaved changes
                </span>
                <div className="flex flex-col gap-0.5">
                  {changedFiles.slice(0, 8).map((f) => (
                    <span key={f} className="text-[11px] font-mono truncate" style={{ color: 'var(--text-secondary)' }}>
                      {f}
                    </span>
                  ))}
                  {changedFiles.length > 8 && (
                    <span className="text-[11px]" style={{ color: 'var(--text-muted)' }}>
                      +{changedFiles.length - 8} more
                    </span>
                  )}
                </div>
              </div>
            )}

            {/* Save + Submit buttons */}
            <div className="px-3 py-3 flex flex-col gap-2" style={{ borderBottom: '1px solid var(--border)' }}>
              {changedFiles.length > 0 && (
                <button
                  onClick={handleSave}
                  disabled={loading}
                  className="w-full px-3 py-2 text-[12px] font-medium rounded-md transition-colors disabled:opacity-40 hover:bg-[var(--bg-hover)]"
                  style={{ background: 'var(--bg-input)', color: 'var(--text-primary)', border: '1px solid var(--border)' }}
                >
                  Save progress
                </button>
              )}
              <button
                onClick={handleSubmit}
                disabled={loading}
                className="w-full flex items-center justify-center gap-2 px-3 py-2 text-[12px] font-semibold text-white rounded-md transition-colors disabled:opacity-40 hover:bg-[var(--accent-hover)]"
                style={{ background: 'var(--accent)' }}
              >
                <ExternalLink size={12} />
                Submit for review
              </button>
            </div>

            {/* Pull latest */}
            {status?.main_ahead && (
              <div className="px-3 py-2.5" style={{ borderBottom: '1px solid var(--border)' }}>
                <div className="flex items-center gap-2 mb-2">
                  <AlertTriangle size={12} style={{ color: 'var(--warning)' }} />
                  <span className="text-[11px]" style={{ color: 'var(--warning)' }}>
                    Main updated ({status.main_ahead_by} commit{status.main_ahead_by !== 1 ? "s" : ""} ahead)
                  </span>
                </div>
                <button
                  onClick={handlePull}
                  disabled={loading}
                  className="w-full flex items-center justify-center gap-2 px-3 py-1.5 text-[12px] font-medium rounded-md transition-colors disabled:opacity-40 hover:bg-[var(--bg-hover)]"
                  style={{ background: 'var(--bg-input)', color: 'var(--text-primary)', border: '1px solid var(--border)' }}
                >
                  <ArrowDownToLine size={12} />
                  Pull latest
                </button>
              </div>
            )}

            {/* Version history toggle */}
            <div className="px-3 py-2.5" style={{ borderBottom: '1px solid var(--border)' }}>
              <button
                onClick={() => setView(view === "history" ? "main" : "history")}
                className="flex items-center gap-2 text-[12px] font-medium transition-colors hover:text-[var(--accent)]"
                style={{ color: view === "history" ? 'var(--accent)' : 'var(--text-secondary)' }}
              >
                <Clock size={12} />
                {view === "history" ? "Hide version history" : "Version history"}
              </button>
            </div>

            {/* Version history list */}
            {view === "history" && (
              <div className="px-3 py-2">
                {history.length === 0 ? (
                  <span className="text-[11px]" style={{ color: 'var(--text-muted)' }}>No commits yet on this branch.</span>
                ) : (
                  <div className="flex flex-col gap-1">
                    {history.map((entry) => (
                      <div key={entry.sha} className="flex items-start gap-2 py-1.5 group">
                        <div className="flex-1 min-w-0">
                          <span className="text-[11px] block truncate" style={{ color: 'var(--text-primary)' }}>{entry.message}</span>
                          <span className="text-[10px] font-mono" style={{ color: 'var(--text-muted)' }}>
                            {entry.short_sha} · {timeAgo(entry.timestamp)}
                          </span>
                        </div>
                        <button
                          onClick={() => setConfirmAction({ type: "revert", target: entry.sha, label: entry.message })}
                          className="p-1 rounded opacity-0 group-hover:opacity-100 transition-opacity shrink-0 hover:bg-[var(--bg-hover)] hover:text-[var(--accent)]"
                          style={{ color: 'var(--text-muted)' }}
                          title="Revert to this version"
                        >
                          <RotateCcw size={12} />
                        </button>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}

            {/* Branch management */}
            <div className="px-3 py-2.5 flex gap-2" style={{ borderTop: '1px solid var(--border)' }}>
              <button
                onClick={() => setConfirmAction({ type: "archive", target: currentBranch, label: currentBranch.split("/").pop() ?? currentBranch })}
                disabled={loading}
                className="flex items-center gap-1.5 px-2.5 py-1 text-[11px] font-medium rounded-md transition-colors disabled:opacity-40 hover:bg-[var(--bg-hover)] hover:text-[var(--text-secondary)]"
                style={{ color: 'var(--text-muted)' }}
              >
                <Archive size={11} />
                Archive
              </button>
              <button
                onClick={() => setConfirmAction({ type: "delete", target: currentBranch, label: currentBranch.split("/").pop() ?? currentBranch })}
                disabled={loading}
                className="flex items-center gap-1.5 px-2.5 py-1 text-[11px] font-medium rounded-md transition-colors disabled:opacity-40 hover:bg-[var(--danger-soft)] hover:text-[var(--danger)]"
                style={{ color: 'var(--text-muted)' }}
              >
                <Trash2 size={11} />
                Delete
              </button>
            </div>
          </>
        )}

        {/* Read-only branch message (someone else's) */}
        {!isOnMain && isReadOnly && (
          <div className="px-3 py-4 text-center">
            <p className="text-[12px] mb-1" style={{ color: 'var(--text-secondary)' }}>
              Viewing someone else's branch.
            </p>
            <p className="text-[11px]" style={{ color: 'var(--text-muted)' }}>
              Switch to your own branch to make changes.
            </p>
          </div>
        )}
      </div>
    </PanelShell>
  )
}


// ---------------------------------------------------------------------------
// Branch list item
// ---------------------------------------------------------------------------

function BranchItem({
  branch,
  currentBranch,
  onSwitch,
}: {
  branch: GitBranchType
  currentBranch: string
  onSwitch: (name: string) => void
}) {
  const isCurrent = branch.name === currentBranch
  // Extract short display name: "pricing/ralph/update-factors" → "update-factors"
  const parts = branch.name.split("/")
  const displayName = parts.length >= 3 ? parts.slice(2).join("/") : branch.name

  return (
    <button
      onClick={() => { if (!isCurrent) onSwitch(branch.name) }}
      className={`w-full flex items-center gap-2 px-3 py-1.5 text-[12px] font-mono text-left transition-colors ${isCurrent ? "" : "hover:bg-[var(--bg-hover)]"}`}
      style={{
        color: isCurrent ? 'var(--accent)' : 'var(--text-secondary)',
        background: isCurrent ? 'var(--accent-soft)' : 'transparent',
      }}
    >
      <GitBranch size={11} style={{ color: isCurrent ? 'var(--accent)' : 'var(--text-muted)', flexShrink: 0 }} />
      <span className="flex-1 truncate">{displayName}</span>
      {branch.commit_count > 0 && (
        <span className="text-[10px] shrink-0" style={{ color: 'var(--text-muted)' }}>
          {branch.commit_count}
        </span>
      )}
    </button>
  )
}
