/**
 * Version breadcrumb for a comparison canvas top-bar (S11).
 *
 * Renders a commit relative to its nearest ancestor milestone, tying the canvas
 * visually to the version-control sidepane:
 *
 *   [init] Initial pricing project a1b2c3d  ›  (1 commit)  ›  Add vehicle factor d4e5f6·2h
 *
 * For a milestone (or the root) it collapses to just the milestone/version. The
 * version tag matches the VC sidepane chip; the untagged root commit gets a
 * distinct "init" tag.
 */
import { ChevronRight } from "lucide-react"

import type { GitCommitContext, GitCommitRef } from "../api/types"

function timeAgo(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime()
  const mins = Math.floor(diff / 60000)
  if (mins < 1) return "just now"
  if (mins < 60) return `${mins}m`
  const hours = Math.floor(mins / 60)
  if (hours < 24) return `${hours}h`
  return `${Math.floor(hours / 24)}d`
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

const chevron = (
  <ChevronRight size={11} className="shrink-0" style={{ color: "var(--text-muted)" }} />
)

export default function CommitBreadcrumb({ context }: { context: GitCommitContext }) {
  const milestone = context.nearest_milestone
  // A milestone or the root commit collapses to a single entry (it IS its anchor).
  const isAnchor = context.is_milestone || context.is_root

  return (
    <span
      data-testid="commit-breadcrumb"
      className="flex items-center gap-1.5 min-w-0 text-[11px]"
      style={{ color: "var(--text-secondary)" }}
    >
      <VersionTag commit={milestone} />
      <span className="truncate" style={{ color: "var(--text-primary)" }}>
        {milestone.message}
      </span>
      <span className="font-mono shrink-0" style={{ color: "var(--text-muted)" }}>
        {milestone.short_sha}
      </span>
      {!isAnchor && (
        <>
          {chevron}
          <span className="shrink-0" style={{ color: "var(--text-muted)" }}>
            ({context.distance} commit{context.distance === 1 ? "" : "s"})
          </span>
          {chevron}
          <span className="truncate" style={{ color: "var(--text-primary)" }}>
            {context.message}
          </span>
          <span className="font-mono shrink-0" style={{ color: "var(--text-muted)" }}>
            {context.short_sha}·{timeAgo(context.timestamp)}
          </span>
        </>
      )}
      {isAnchor && (
        <span className="font-mono shrink-0" style={{ color: "var(--text-muted)" }}>
          ·{timeAgo(context.timestamp)}
        </span>
      )}
    </span>
  )
}
