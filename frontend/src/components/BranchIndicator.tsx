import { GitBranch } from "lucide-react"

import useGitStore from "../stores/useGitStore"
import useUIStore from "../stores/useUIStore"

/**
 * Persistent toolbar indicator (S28): the working branch + the current save
 * SHA. Both the name and the SHA open the Git sidebar panel, which hosts the
 * branch manager and the version history (S19).
 *
 * When no working branch is configured (or status is divergent/invalid) it
 * renders a muted "Set branch" prompt that opens the selection modal — the
 * standing, non-interrupting counterpart to the startup modal.
 */
export default function BranchIndicator() {
  const status = useGitStore((s) => s.status)
  const loading = useGitStore((s) => s.loading)
  const statusError = useGitStore((s) => s.statusError)
  const loadStatus = useGitStore((s) => s.loadStatus)
  const openModal = useGitStore((s) => s.openModal)
  const setPeekBranch = useGitStore((s) => s.setPeekBranch)
  const requestExpandBranches = useGitStore((s) => s.requestExpandBranches)
  const requestSelectLatestSave = useGitStore((s) => s.requestSelectLatestSave)
  const requestSelectSave = useGitStore((s) => s.requestSelectSave)
  // While comparing, the indicator points at the inspected version (not the
  // latest save) and selecting it picks that version in the history (S11).
  const comparison = useGitStore((s) => s.comparison)
  const setGitOpen = useUIStore((s) => s.setGitOpen)

  // Open the Git panel, expand the branch manager, and return its history view
  // to the current branch (a no-op when already current, so any open milestone
  // stays expanded, S38).
  const openOnCurrent = () => {
    setPeekBranch(null)
    setGitOpen(true)
    requestExpandBranches()
  }

  // The commit-SHA indicator points at the latest save (the ledger tip): open the
  // panel on the current branch and select that save in the history (S38). While
  // comparing it points at the inspected version instead and selects that (S11).
  const openOnLatestSave = () => {
    setPeekBranch(null)
    setGitOpen(true)
    if (comparison) {
      requestSelectSave(comparison.sha)
    } else {
      requestSelectLatestSave()
    }
  }

  if (statusError) {
    return (
      <div data-testid="toolbar-branch-indicator" data-branch-state="error">
        <span>Git unavailable: {statusError}</span>
        <button
          type="button"
          data-testid="branch-indicator-retry"
          onClick={() => void loadStatus()}
        >
          Retry
        </button>
      </div>
    )
  }

  if (status === null) {
    if (loading) {
      return (
        <span data-testid="toolbar-branch-indicator" data-branch-state="checking">
          Checking Git…
        </span>
      )
    }
    return null
  }

  const ready = status.state === "ready"

  if (!ready) {
    if (status.state === "git-unavailable") {
      return (
        <span
          data-testid="toolbar-branch-indicator"
          data-branch-state="git-unavailable"
          title="No git executable is available in this environment."
        >
          Git unavailable
        </span>
      )
    }
    if (status.state === "no-repository") {
      return (
        <span
          data-testid="toolbar-branch-indicator"
          data-branch-state="no-repository"
          title="Run git init in this project to enable version control."
        >
          Git not initialised
        </span>
      )
    }
    const stateMeta = status.state === "unset"
      ? { label: "Set branch", modal: "select" as const }
      : status.state === "divergent"
        ? { label: "Branch changed externally", modal: "divergence" as const }
        : status.state === "detached"
          ? { label: `Detached at ${status.head_sha?.slice(0, 7) ?? "unknown"}`, modal: "select" as const }
          : { label: "Git needs attention", modal: "select" as const }
    return (
      <button
        type="button"
        data-testid="toolbar-branch-indicator"
        data-branch-state={status.state}
        onClick={() => openModal(stateMeta.modal)}
        className="flex items-center gap-1 px-2 py-1 text-[12px] font-medium rounded-md hover-chrome"
        style={{ color: "var(--danger)" }}
        title={`${stateMeta.label} — click to resolve in the Git panel`}
      >
        <GitBranch size={13} aria-hidden="true" />
        {stateMeta.label}
      </button>
    )
  }

  // While comparing, the indicator shows the inspected version's sha (S11).
  const displaySha = comparison?.sha ?? status.last_save_sha
  const shortSha = displaySha ? displaySha.slice(0, 7) : null

  return (
    <div
      data-testid="toolbar-branch-indicator"
      data-branch-state="ready"
      className="flex items-center gap-1.5 px-2 py-1 rounded-md"
      style={{ background: "var(--bg-input)" }}
    >
      <button
        type="button"
        data-testid="branch-indicator-name"
        onClick={openOnCurrent}
        className="flex items-center gap-1 text-[12px] font-medium font-mono max-w-[180px] truncate hover:underline"
        style={{ color: "var(--text-primary)" }}
        title={`Working branch: ${status.working_branch} — click to manage branches`}
      >
        <GitBranch size={13} aria-hidden="true" />
        <span className="truncate">{status.working_branch}</span>
      </button>
      {shortSha && (
        <button
          type="button"
          data-testid="branch-indicator-sha"
          data-comparing={comparison ? true : undefined}
          onClick={openOnLatestSave}
          className="text-[11px] font-mono hover:underline"
          style={{ color: comparison ? "var(--accent)" : "var(--text-muted)" }}
          title={
            comparison
              ? `Viewing ${comparison.label} (${displaySha}) — click to select it in the history`
              : `Last save ${status.last_save_sha} — click to select it in the history`
          }
        >
          {shortSha}
        </button>
      )}
    </div>
  )
}
