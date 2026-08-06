import { GitBranch } from "lucide-react"

import useGitStore from "../stores/useGitStore"
import useUIStore from "../stores/useUIStore"

/** The sync/storage chip shown beside the branch indicator once a working
 *  branch is ready (S28 durable-storage surface). Hidden entirely when this
 *  deployment cannot remember a binding ("unsupported", e.g. every local
 *  session) — the whole storage concept doesn't apply there. */
function StorageChip() {
  const status = useGitStore((s) => s.status)
  const openModal = useGitStore((s) => s.openModal)
  const retrySync = useGitStore((s) => s.retrySync)

  if (!status || !status.storage || status.storage === "unsupported") return null

  if (status.storage === "unbound") {
    return (
      <button
        type="button"
        data-testid="storage-indicator"
        data-storage-state="unbound"
        onClick={() => openModal("storage")}
        className="flex items-center text-[11px] font-medium px-1.5 py-0.5 rounded-md hover-chrome"
        style={{ color: "var(--danger)" }}
        title="Work is lost if this app restarts — click to save it to a repository."
      >
        Not stored
      </button>
    )
  }

  const sync = status.sync
  if (!sync || sync.state === "synced") {
    return (
      <span
        data-testid="storage-indicator"
        data-sync-state="synced"
        className="text-[11px] font-medium px-1.5 py-0.5"
        style={{ color: "var(--text-muted)" }}
        title="Saves are stored durably in the bound repository."
      >
        Synced
      </span>
    )
  }

  if (sync.state === "pending") {
    return (
      <span
        data-testid="storage-indicator"
        data-sync-state="pending"
        className="text-[11px] font-medium px-1.5 py-0.5"
        style={{ color: "var(--text-muted)" }}
        title="Saves not yet published to the bound repository."
      >
        {sync.pending} unpublished
      </span>
    )
  }

  return (
    <span
      data-testid="storage-indicator"
      data-sync-state="failed"
      className="flex items-center gap-1 text-[11px] font-medium px-1.5 py-0.5"
      style={{ color: "var(--danger)" }}
      title={sync.message ?? "Sync to the bound repository failed."}
    >
      {sync.message ?? "Sync failed"}
      <button
        type="button"
        data-testid="storage-retry"
        onClick={() => void retrySync()}
        className="underline"
      >
        Retry
      </button>
    </span>
  )
}

/** A quiet marker that this project is a copy of another, opening the
 *  comparison dialog. Deliberately understated: knowing where a fork came from
 *  matters occasionally, and catching up to the parent is never the primary
 *  action of a session. */
function ForkChip() {
  const forkedFrom = useGitStore((s) => s.status?.storage_forked_from ?? null)
  const openModal = useGitStore((s) => s.openModal)

  if (!forkedFrom) return null

  return (
    <button
      type="button"
      data-testid="fork-indicator"
      onClick={() => openModal("upstream")}
      className="text-[11px] font-medium px-1.5 py-0.5 rounded-md hover-chrome"
      style={{ color: "var(--text-muted)" }}
      title={`Forked from ${forkedFrom} — click to compare with it.`}
    >
      Forked
    </button>
  )
}

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
      <StorageChip />
      <ForkChip />
    </div>
  )
}
