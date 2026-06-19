import { describe, it, expect, afterEach } from "vitest"
import { render, screen, cleanup } from "@testing-library/react"

import CommitBreadcrumb, { ComparisonDelta } from "../CommitBreadcrumb"
import type { GitCommitContext, GitCommitRef } from "../../api/types"

afterEach(cleanup)

function ref(over: Partial<GitCommitRef> = {}): GitCommitRef {
  return { sha: "x", short_sha: "abc1234", message: "msg", version_label: null, is_root: false, ...over }
}
function ctx(over: Partial<GitCommitContext> = {}): GitCommitContext {
  return {
    sha: "x",
    short_sha: "abc1234",
    message: "msg",
    timestamp: new Date().toISOString(),
    is_root: false,
    is_milestone: false,
    version_label: null,
    nearest_milestone: ref(),
    distance: 0,
    delta_from_base: null,
    ...over,
  }
}

describe("CommitBreadcrumb", () => {
  it("shows an 'init' tag for the root commit and collapses to the anchor alone", () => {
    render(
      <CommitBreadcrumb
        context={ctx({
          is_root: true,
          message: "Initial pricing project",
          short_sha: "selfsha", // the commit's own sha — collapsed anchors don't render it
          nearest_milestone: ref({ message: "Initial pricing project", short_sha: "root123", is_root: true }),
        })}
      />,
    )
    const bc = screen.getByTestId("commit-breadcrumb")
    expect(screen.getByTestId("commit-tag-init")).toBeInTheDocument()
    expect(bc).toHaveTextContent("Initial pricing project")
    expect(bc).toHaveTextContent("root123")
    // Collapsed: only the anchor renders — no separate save sha.
    expect(bc).not.toHaveTextContent("selfsha")
  })

  it("shows a version tag for a milestone and collapses to the anchor alone", () => {
    render(
      <CommitBreadcrumb
        context={ctx({
          is_milestone: true,
          message: "Add no-claims discount",
          short_sha: "selfsha",
          nearest_milestone: ref({ version_label: "v1.2", message: "Add no-claims discount", short_sha: "miles00" }),
        })}
      />,
    )
    const bc = screen.getByTestId("commit-breadcrumb")
    expect(screen.getByTestId("commit-tag-version")).toHaveTextContent("v1.2")
    expect(screen.queryByTestId("commit-tag-init")).not.toBeInTheDocument()
    expect(bc).toHaveTextContent("miles00")
    expect(bc).not.toHaveTextContent("selfsha")
  })

  it("renders the nearest-milestone → commit breadcrumb for a non-milestone save", () => {
    render(
      <CommitBreadcrumb
        context={ctx({
          message: "Add vehicle factor group",
          short_sha: "veh4567",
          timestamp: new Date(Date.now() - 2 * 3600_000).toISOString(),
          nearest_milestone: ref({ message: "Initial pricing project", short_sha: "init123", is_root: true }),
        })}
      />,
    )
    const bc = screen.getByTestId("commit-breadcrumb")
    expect(screen.getByTestId("commit-tag-init")).toBeInTheDocument()
    expect(bc).toHaveTextContent("Initial pricing project")
    expect(bc).toHaveTextContent("init123")
    expect(bc).toHaveTextContent("Add vehicle factor group")
    expect(bc).toHaveTextContent("veh4567")
    expect(bc).toHaveTextContent("ago")
    // The per-side commits-between count and absolute ordinal were wound back —
    // no "(N commit…)" clutter in the breadcrumb itself.
    expect(bc).not.toHaveTextContent(/commit\)/)
  })
})

describe("ComparisonDelta", () => {
  it("renders the historic↔current span, pluralising the count", () => {
    render(<ComparisonDelta count={3} />)
    expect(screen.getByTestId("comparison-delta")).toHaveTextContent("3 commits")
  })

  it("uses the singular for a one-commit span", () => {
    render(<ComparisonDelta count={1} />)
    expect(screen.getByTestId("comparison-delta")).toHaveTextContent("1 commit")
    expect(screen.getByTestId("comparison-delta")).not.toHaveTextContent("1 commits")
  })
})
