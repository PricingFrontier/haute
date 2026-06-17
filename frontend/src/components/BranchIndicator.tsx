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

  // Nothing to show until we know the git state (non-git project, or pre-load).
  if (status === null) return null

  const ready = status.state === "ready"

  if (!ready) {
    return (
      <button
        type="button"
        data-testid="toolbar-branch-indicator"
        data-branch-state={status.state}
        onClick={() => openModal(status.state === "divergent" ? "divergence" : "select")}
        className="flex items-center gap-1 px-2 py-1 text-[12px] font-medium rounded-md hover-chrome"
        style={{ color: "var(--danger)" }}
        title="No working branch set — click to choose one"
      >
        <GitBranch size={13} aria-hidden="true" />
        Set branch
      </button>
    )
  }

  const shortSha = status.last_save_sha ? status.last_save_sha.slice(0, 7) : null

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
          onClick={openOnCurrent}
          className="text-[11px] font-mono hover:underline"
          style={{ color: "var(--text-muted)" }}
          title={`Last save ${status.last_save_sha} — click to view history`}
        >
          {shortSha}
        </button>
      )}
    </div>
  )
}
