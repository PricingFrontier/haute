import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

vi.mock("../../api/client", async (importOriginal) => {
  const original = await importOriginal<typeof import("../../api/client")>()
  return {
    ...original,
    checkGitUpstream: vi.fn(),
    pullGitUpstream: vi.fn(),
    getWorkingBranch: vi.fn(() => Promise.resolve(null)),
  }
})

import { checkGitUpstream, pullGitUpstream } from "../../api/client"
import type { GitUpstreamStatus } from "../../api/types"
import useGitStore from "../../stores/useGitStore"
import useToastStore from "../../stores/useToastStore"
import UpstreamSyncModal from "../UpstreamSyncModal"

const PARENT = "uc://workspace.default.projects/pricing/demo"

function upstream(overrides: Partial<GitUpstreamStatus>): GitUpstreamStatus {
  return {
    parent_url: PARENT,
    parent_generation: 3,
    working: { status: "synced", ahead: 0, behind: 0 },
    ledger: { status: "synced", ahead: 0, behind: 0 },
    can_fast_forward: false,
    checked_at: "2026-08-05T09:00:00+00:00",
    message: "This copy is up to date with the project it was forked from.",
    ...overrides,
  }
}

describe("UpstreamSyncModal — a fork's relationship to its parent", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useGitStore.setState({ modal: "upstream" })
    useToastStore.setState({ toasts: [] })
  })
  afterEach(cleanup)

  it("reports an up-to-date fork with nothing to do", async () => {
    vi.mocked(checkGitUpstream).mockResolvedValue(upstream({}))
    render(<UpstreamSyncModal onClose={vi.fn()} />)

    await waitFor(() =>
      expect(screen.getByTestId("upstream-sync-message").textContent).toContain("up to date"),
    )
    expect(screen.queryByTestId("upstream-sync-confirm")).toBeNull()
  })

  it("offers the catch-up only when it is a clean fast-forward, and closes after it", async () => {
    vi.mocked(checkGitUpstream).mockResolvedValue(
      upstream({
        working: { status: "behind", ahead: 0, behind: 2 },
        ledger: { status: "behind", ahead: 0, behind: 2 },
        can_fast_forward: true,
        message: "The parent project has 2 changes this copy doesn't have yet.",
      }),
    )
    vi.mocked(pullGitUpstream).mockResolvedValue({
      remote: "upstream",
      working_branch: "pricing-dev",
      fast_forwarded: ["pricing-dev", "pricing-dev-save"],
    })
    const onClose = vi.fn()
    render(<UpstreamSyncModal onClose={onClose} />)

    const button = await screen.findByTestId("upstream-sync-confirm")
    fireEvent.click(button)

    await waitFor(() => expect(onClose).toHaveBeenCalled())
    expect(pullGitUpstream).toHaveBeenCalled()
    const messages = useToastStore.getState().toasts.map((t) => t.text)
    expect(messages.some((m) => m.includes("Caught up"))).toBe(true)
  })

  it("explains a both-moved fork without offering an action that cannot work", async () => {
    vi.mocked(checkGitUpstream).mockResolvedValue(
      upstream({
        working: { status: "diverged", ahead: 1, behind: 2 },
        ledger: { status: "diverged", ahead: 3, behind: 2 },
        can_fast_forward: false,
        message:
          "Both projects have moved since the fork — 3 change(s) here and 2 in the parent.",
      }),
    )
    render(<UpstreamSyncModal onClose={vi.fn()} />)

    await waitFor(() =>
      expect(screen.getByTestId("upstream-sync-message").textContent).toContain(
        "Both projects have moved",
      ),
    )
    expect(screen.queryByTestId("upstream-sync-confirm")).toBeNull()
  })

  it("surfaces a failed check instead of an empty dialog", async () => {
    vi.mocked(checkGitUpstream).mockRejectedValue(new Error("volume unreachable"))
    render(<UpstreamSyncModal onClose={vi.fn()} />)

    await waitFor(() => expect(screen.getByTestId("upstream-sync-error")).toBeTruthy())
    expect(screen.queryByTestId("upstream-sync-checking")).toBeNull()
  })
})
