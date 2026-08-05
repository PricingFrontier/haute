import { GitBranch } from "lucide-react"

import useGitStore from "../stores/useGitStore"
import useUIStore from "../stores/useUIStore"

/**
 * Persistent toolbar indicator (S28): the working branch name, which opens the
 * Git sidebar panel hosting the branch manager and the version history (S19).
 *
 * The indicator shows the branch and nothing else. It used to carry the current
 * save's short SHA beside it, but that click only *highlighted* the newest save
 * in the history — no scroll, and the branch manager it shared the panel with is
 * expanded by default — so it was indistinguishable from clicking the branch
 * name in every case except a newest-save folded inside a collapsed milestone.
 * The commit code belongs to the history panel, which shows it in context.
 */
export default function BranchIndicator() {
  const status = useGitStore((s) => s.status)
  const loading = useGitStore((s) => s.loading)
  const statusError = useGitStore((s) => s.statusError)
  const loadStatus = useGitStore((s) => s.loadStatus)
  const openModal = useGitStore((s) => s.openModal)
  const setPeekBranch = useGitStore((s) => s.setPeekBranch)
  const requestExpandBranches = useGitStore((s) => s.requestExpandBranches)
  const setGitOpen = useUIStore((s) => s.setGitOpen)

  // Open the Git panel, expand the branch manager, and return its history view
  // to the current branch (a no-op when already current, so any open milestone
  // stays expanded, S38).
  const openOnCurrent = () => {
    setPeekBranch(null)
    setGitOpen(true)
    requestExpandBranches()
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
        /* Same button shell as the ready state; the danger colour overrides
           the shared foreground so an unresolved Git state still stands out. */
        className="toolbar-btn flex items-center gap-1 px-2.5 py-1 text-[12px] font-medium rounded-md"
        style={{ color: "var(--danger)" }}
        title={`${stateMeta.label} — click to resolve in the Git panel`}
      >
        <GitBranch size={13} aria-hidden="true" />
        {stateMeta.label}
      </button>
    )
  }

  // The indicator carries the toolbar's shared button styling: it sat in an
  // inset --bg-input well (darker than the chrome, so it read as a field
  // rather than a control) and underlined on hover, while every other action
  // in the bar is a raised button that brightens instead.
  return (
    <div data-testid="toolbar-branch-indicator" data-branch-state="ready" className="flex items-center">
      <button
        type="button"
        data-testid="branch-indicator-name"
        onClick={openOnCurrent}
        className="toolbar-btn flex items-center gap-1 px-2.5 py-1 text-[12px] font-medium font-mono rounded-md max-w-[180px] truncate"
        title={`Working branch: ${status.working_branch} — click to manage branches`}
      >
        <GitBranch size={13} aria-hidden="true" />
        <span className="truncate">{status.working_branch}</span>
      </button>
    </div>
  )
}
