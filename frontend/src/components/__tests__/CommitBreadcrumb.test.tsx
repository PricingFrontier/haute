import { describe, it, expect, afterEach } from "vitest"
import { render, screen, cleanup } from "@testing-library/react"

import CommitBreadcrumb from "../CommitBreadcrumb"
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
    ...over,
  }
}

describe("CommitBreadcrumb", () => {
  it("shows an 'init' tag for the root commit and collapses (no distance)", () => {
    render(
      <CommitBreadcrumb
        context={ctx({
          is_root: true,
          message: "Initial pricing project",
          short_sha: "root123",
          nearest_milestone: ref({ message: "Initial pricing project", short_sha: "root123", is_root: true }),
        })}
      />,
    )
    expect(screen.getByTestId("commit-tag-init")).toBeInTheDocument()
    expect(screen.getByTestId("commit-breadcrumb")).toHaveTextContent("Initial pricing project")
    expect(screen.getByTestId("commit-breadcrumb")).not.toHaveTextContent(/commit\)/)
  })

  it("shows a version tag for a milestone (collapsed)", () => {
    render(
      <CommitBreadcrumb
        context={ctx({
          is_milestone: true,
          message: "Add no-claims discount",
          nearest_milestone: ref({ version_label: "v1.2", message: "Add no-claims discount" }),
        })}
      />,
    )
    const tag = screen.getByTestId("commit-tag-version")
    expect(tag).toHaveTextContent("v1.2")
    expect(screen.queryByTestId("commit-tag-init")).not.toBeInTheDocument()
  })

  it("renders the nearest-milestone breadcrumb with distance for a non-milestone commit", () => {
    render(
      <CommitBreadcrumb
        context={ctx({
          message: "Add vehicle factor group",
          short_sha: "veh4567",
          distance: 1,
          nearest_milestone: ref({ message: "Initial pricing project", short_sha: "init123", is_root: true }),
        })}
      />,
    )
    const bc = screen.getByTestId("commit-breadcrumb")
    expect(screen.getByTestId("commit-tag-init")).toBeInTheDocument()
    expect(bc).toHaveTextContent("Initial pricing project")
    expect(bc).toHaveTextContent("init123")
    expect(bc).toHaveTextContent("(1 commit)")
    expect(bc).toHaveTextContent("Add vehicle factor group")
    expect(bc).toHaveTextContent("veh4567")
  })

  it("pluralises the commit count", () => {
    render(<CommitBreadcrumb context={ctx({ distance: 3, nearest_milestone: ref({ version_label: "v2" }) })} />)
    expect(screen.getByTestId("commit-breadcrumb")).toHaveTextContent("(3 commits)")
  })
})
