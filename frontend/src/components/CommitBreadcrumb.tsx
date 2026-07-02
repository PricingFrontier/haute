/**
 * Version breadcrumb for a comparison canvas top-bar (S11).
 *
 * Shows a commit relative to the LATEST milestone at it, tying the canvas to the
 * version-control sidepane:
 *
 *   [init] Initial pricing project a1b2c3d › Save progress d4e5f6·2h ago
 *
 * For a milestone (or the root) it collapses to the tagged milestone alone. The
 * commits-between count is NOT shown per side; the caller renders a single
 * historic↔current delta (ComparisonDelta) on the historic pane only.
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

/**
 * The historic↔current commit delta — how many commits separate the two compared
 * versions. Flanked by chevrons (the breadcrumb separator glyph) to read as a span
 * between the commits. Rendered by the caller on the HISTORIC pane only.
 */
export function ComparisonDelta({ count }: { count: number }) {
  return (
    <span
      data-testid="comparison-delta"
      className="flex items-center gap-0.5 text-[11px] font-mono px-1.5 py-0.5 rounded shrink-0"
      style={{ background: "var(--bg-hover)", color: "var(--text-muted)" }}
    >
      <ChevronRight size={11} className="shrink-0" />
      {count} commit{count === 1 ? "" : "s"}
      <ChevronRight size={11} className="shrink-0" />
    </span>
  )
}

export default function CommitBreadcrumb({ context }: { context: GitCommitContext }) {
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
          <ChevronRight size={11} className="shrink-0" style={{ color: "var(--text-muted)" }} />
          <span className="truncate" style={{ color: "var(--text-primary)" }}>
            {context.message}
          </span>
          <span className="font-mono shrink-0" style={{ color: "var(--text-muted)" }}>
            {context.short_sha}·{timeAgo(context.timestamp)}
          </span>
        </>
      )}
    </span>
  )
}
