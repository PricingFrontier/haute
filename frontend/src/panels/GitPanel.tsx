import { useState, useEffect, useCallback } from "react"
import {
  GitFork, GitBranch, Clock, ChevronRight, ChevronDown, Tag, RefreshCw,
} from "lucide-react"
import PanelShell from "./PanelShell"
import useToastStore from "../stores/useToastStore"
import useGitStore from "../stores/useGitStore"
import { getMilestones, getMilestoneSaves, getPendingSaves } from "../api/client"
import type { GitMilestoneEntry, GitLedgerSave, GitFileChange } from "../api/types"

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

  const [milestones, setMilestones] = useState<GitMilestoneEntry[]>([])
  const [pending, setPending] = useState<GitLedgerSave[]>([])
  const [expanded, setExpanded] = useState<ExpandState>({})
  const [loading, setLoading] = useState(false)

  const workingBranch = status?.working_branch ?? null
  const ledgerSha = status?.last_save_sha ?? null

  // ---------------------------------------------------------------------------
  // Data
  // ---------------------------------------------------------------------------

  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      const [ms, ps] = await Promise.all([getMilestones(50), getPendingSaves()])
      setMilestones(ms.entries)
      setPending(ps.saves)
      setExpanded({})
    } catch (err) {
      const detail = err instanceof Error ? err.message : "unknown error"
      addToast("error", `Failed to load version history: ${detail}`)
    } finally {
      setLoading(false)
    }
  }, [addToast])

  useEffect(() => {
    loadStatus()
    refresh()
  }, [loadStatus, refresh])

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

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------

  return (
    <PanelShell
      testId="git-panel"
      title="Version history"
      onClose={onClose}
      icon={<GitFork size={14} style={{ color: "var(--success)" }} />}
    >
      {/* Working-branch header */}
      <div
        className="px-3 py-2.5 flex items-center gap-2 shrink-0"
        style={{ borderBottom: "1px solid var(--border)" }}
      >
        <GitBranch size={12} style={{ color: "var(--accent)", flexShrink: 0 }} />
        {workingBranch ? (
          <>
            <span
              data-testid="git-panel-working-branch"
              className="text-[12px] font-mono truncate flex-1"
              style={{ color: "var(--text-primary)" }}
            >
              {workingBranch}
            </span>
            {ledgerSha && (
              <span
                data-testid="git-panel-ledger-sha"
                className="text-[10px] font-mono shrink-0"
                style={{ color: "var(--text-muted)" }}
              >
                {ledgerSha}
              </span>
            )}
          </>
        ) : (
          <span
            data-testid="git-panel-no-branch"
            className="text-[11px] flex-1"
            style={{ color: "var(--text-muted)" }}
          >
            No working branch — choose one from the toolbar to start versioning.
          </span>
        )}
        <button
          data-testid="git-panel-refresh"
          onClick={refresh}
          disabled={loading}
          title="Refresh"
          className="p-1 rounded shrink-0 transition-colors disabled:opacity-40 hover:bg-[var(--bg-hover)]"
          style={{ color: "var(--text-muted)" }}
        >
          <RefreshCw size={12} className={loading ? "animate-spin" : undefined} />
        </button>
      </div>

      <div className="flex-1 min-h-0 overflow-y-auto">
        {/* Pending (unmilestoned) saves — what the next commit would fold in */}
        {pending.length > 0 && (
          <div
            data-testid="git-panel-pending"
            className="px-3 py-2.5"
            style={{ borderBottom: "1px solid var(--border)", background: "var(--accent-soft-faint)" }}
          >
            <span
              className="text-[10px] font-medium uppercase tracking-wider block mb-1.5"
              style={{ color: "var(--text-muted)" }}
            >
              Unmilestoned saves ({pending.length}) — fold into your next commit
            </span>
            <div className="flex flex-col gap-1.5">
              {pending.map((s) => (
                <SaveRow key={s.sha} save={s} testId="git-panel-pending-save" />
              ))}
            </div>
          </div>
        )}

        {/* Milestone spine */}
        {loading && milestones.length === 0 ? (
          <div data-testid="git-panel-loading" className="px-3 py-6 text-center">
            <span className="text-[11px]" style={{ color: "var(--text-muted)" }}>
              Loading version history…
            </span>
          </div>
        ) : milestones.length === 0 ? (
          <div data-testid="git-panel-empty" className="px-3 py-6 text-center">
            <Clock size={18} className="mx-auto mb-2" style={{ color: "var(--text-muted)" }} />
            <p className="text-[12px]" style={{ color: "var(--text-secondary)" }}>
              No milestones yet.
            </p>
            <p className="text-[11px] mt-1" style={{ color: "var(--text-muted)" }}>
              Use Commit in the toolbar to record one.
            </p>
          </div>
        ) : (
          <div data-testid="git-panel-milestones" className="py-1">
            {milestones.map((m) => {
              const exp = expanded[m.sha]
              const isOpen = exp !== undefined
              return (
                <div key={m.sha} style={{ borderBottom: "1px solid var(--border)" }}>
                  <button
                    data-testid="git-panel-milestone"
                    onClick={() => toggleExpand(m.sha)}
                    className="w-full flex items-start gap-1.5 px-3 py-2 text-left transition-colors hover:bg-[var(--bg-hover)]"
                  >
                    <span className="mt-0.5 shrink-0" style={{ color: "var(--text-muted)" }}>
                      {isOpen ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
                    </span>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-1.5">
                        <span
                          className="text-[12px] truncate"
                          style={{ color: "var(--text-primary)" }}
                        >
                          {m.message}
                        </span>
                        {m.version_label && (
                          <span
                            data-testid="git-panel-milestone-label"
                            className="text-[10px] px-1 py-0.5 rounded font-mono inline-flex items-center gap-0.5 shrink-0"
                            style={{ background: "var(--accent-soft)", color: "var(--accent)" }}
                          >
                            <Tag size={9} />
                            {m.version_label}
                          </span>
                        )}
                      </div>
                      <span className="text-[10px] font-mono" style={{ color: "var(--text-muted)" }}>
                        {m.short_sha} · {timeAgo(m.timestamp)}
                      </span>
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
                            <SaveRow key={s.sha} save={s} testId="git-panel-save" />
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
    </PanelShell>
  )
}

// ---------------------------------------------------------------------------
// One ledger save: message, sha/time, and its rename-aware file changes.
// ---------------------------------------------------------------------------

function SaveRow({ save, testId }: { save: GitLedgerSave; testId: string }) {
  return (
    <div data-testid={testId} className="flex flex-col gap-0.5">
      <span className="text-[11px] truncate" style={{ color: "var(--text-secondary)" }}>
        {save.message}
      </span>
      <span className="text-[10px] font-mono" style={{ color: "var(--text-muted)" }}>
        {save.short_sha} · {timeAgo(save.timestamp)}
      </span>
      {save.files.length > 0 && (
        <div className="flex flex-col gap-0.5 mt-0.5">
          {save.files.map((f) => (
            <FileRow key={`${f.status}:${f.old_path ?? ""}:${f.path}`} file={f} />
          ))}
        </div>
      )}
    </div>
  )
}

function FileRow({ file }: { file: GitFileChange }) {
  return (
    <span
      data-testid="git-panel-file"
      className="text-[10px] font-mono truncate flex items-center gap-1"
      style={{ color: "var(--text-muted)" }}
    >
      <span
        className="inline-block w-3 text-center shrink-0"
        style={{ color: statusColor(file.status) }}
      >
        {file.status}
      </span>
      {file.status === "R" && file.old_path ? (
        <span className="truncate">
          {file.old_path} → {file.path}
        </span>
      ) : (
        <span className="truncate">{file.path}</span>
      )}
    </span>
  )
}

function statusColor(status: string): string {
  if (status === "A") return "var(--success)"
  if (status === "D") return "var(--danger)"
  if (status === "R" || status === "C") return "var(--accent)"
  return "var(--text-secondary)"
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
