/**
 * Version breadcrumb for a comparison canvas top-bar (S11).
 *
 * Shows a commit relative to the LATEST milestone at it, tying the canvas to the
 * version-control sidepane:
 *
 *   [init] Initial pricing project a1b2c3d  ——  (12 commits) › Save progress d4e5f6·2h ago (13th commit)
 *
 * For a milestone (or the root) it collapses to the tagged milestone alone. The
 * "commits-between" distance is right-aligned in the side-by-side layout; in the
 * stacked layout it is omitted here (the caller shows it bottom-left of the pane,
 * `showDistance={false}`). The absolute ordinal ("(Nth commit)") trails the age.
 */
import { ChevronRight } from "lucide-react"

import type { GitCommitContext, GitCommitRef } from "../api/types"

function timeAgo(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime()
  const mins = Math.floor(diff / 60000)
  if (mins < 1) return "just now"
  if (mins < 60) return `${mins}m ago`
  const hours = Math.floor(mins / 60)
  if (hours < 24) return `${hours}h ago`
  return `${Math.floor(hours / 24)}d ago`
}

/** "1st", "2nd", "3rd", "13th"… */
function ordinal(n: number): string {
  const s = ["th", "st", "nd", "rd"]
  const v = n % 100
  return `${n}${s[(v - 20) % 10] ?? s[v] ?? s[0]}`
}

/** The version tag for a milestone/root, matching the VC sidepane styling. */
function VersionTag({ commit }: { commit: GitCommitRef }) {
  if (commit.is_root) {
    return (
      <span
        data-testid="commit-tag-init"
        className="text-[10px] px-1 py-0.5 rounded font-mono shrink-0"
        style={{ background: "var(--bg-hover)", color: "var(--text-secondary)", border: "1px solid var(--border)" }}
      >
        init
      </span>
    )
  }
  if (commit.version_label) {
    return (
      <span
        data-testid="commit-tag-version"
        className="text-[10px] px-1 py-0.5 rounded font-mono shrink-0"
        style={{ background: "var(--accent-soft)", color: "var(--accent)" }}
      >
        {commit.version_label}
      </span>
    )
  }
  return null
}

/** The "(N commits)" distance pill — extracted so the stacked layout can place
 *  it bottom-left of the pane. */
export function CommitDistance({ distance }: { distance: number }) {
  return (
    <span
      data-testid="commit-distance"
      className="text-[11px] font-mono px-1.5 py-0.5 rounded shrink-0"
      style={{ background: "var(--bg-hover)", color: "var(--text-muted)" }}
    >
      ({distance} commit{distance === 1 ? "" : "s"})
    </span>
  )
}

export default function CommitBreadcrumb({
  context,
  showDistance = true,
}: {
  context: GitCommitContext
  showDistance?: boolean
}) {
  const milestone = context.nearest_milestone
  // A milestone or the root commit collapses to a single tagged entry.
  const isAnchor = context.is_milestone || context.is_root

  return (
    <span
      data-testid="commit-breadcrumb"
      className="flex items-center gap-1.5 min-w-0 flex-1 text-[11px]"
      style={{ color: "var(--text-secondary)" }}
    >
      <VersionTag commit={milestone} />
      <span className="truncate" style={{ color: "var(--text-primary)" }}>
        {milestone.message}
      </span>
      <span className="font-mono shrink-0" style={{ color: "var(--text-muted)" }}>
        {milestone.short_sha}
      </span>
      {isAnchor ? (
        <span className="font-mono shrink-0" style={{ color: "var(--text-muted)" }}>
          ·{timeAgo(context.timestamp)}
        </span>
      ) : (
        <>
          {/* Side-by-side: distance right-aligned (spacer) then the save commit. */}
          {showDistance && <span className="flex-1" />}
          {showDistance && <CommitDistance distance={context.distance} />}
          <ChevronRight size={11} className="shrink-0" style={{ color: "var(--text-muted)" }} />
          <span className="truncate" style={{ color: "var(--text-primary)" }}>
            {context.message}
          </span>
          <span className="font-mono shrink-0" style={{ color: "var(--text-muted)" }}>
            {context.short_sha}·{timeAgo(context.timestamp)} ({ordinal(context.ordinal)} commit)
          </span>
        </>
      )}
    </span>
  )
}
