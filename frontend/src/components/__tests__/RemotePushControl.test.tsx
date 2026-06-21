import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { render, screen, fireEvent, cleanup, waitFor } from "@testing-library/react"
import RemotePushControl from "../RemotePushControl"
import { ApiError } from "../../api/client"

const mockGetGitRemotes = vi.fn()
const mockGitPush = vi.fn()
const mockFastForward = vi.fn()
const mockBranchAway = vi.fn()

// Spread the real module so `ApiError` (used for the 409 rejection path) stays
// the genuine class — only the network calls are stubbed.
vi.mock("../../api/client", async () => {
  const actual = await vi.importActual<typeof import("../../api/client")>("../../api/client")
  return {
    ...actual,
    getGitRemotes: (...a: unknown[]) => mockGetGitRemotes(...a),
    gitPush: (...a: unknown[]) => mockGitPush(...a),
    gitFastForward: (...a: unknown[]) => mockFastForward(...a),
    gitBranchAway: (...a: unknown[]) => mockBranchAway(...a),
  }
})

const remote = (over: Partial<Record<string, unknown>> = {}) => ({
  name: "origin",
  url: "git@example.com:x.git",
  ahead: null,
  behind: null,
  ...over,
})

describe("RemotePushControl", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockGetGitRemotes.mockResolvedValue({ remotes: [], working_branch: "dev" })
    mockGitPush.mockResolvedValue({
      remote: "origin",
      working_branch: "dev",
      ledger_branch: "dev-save",
      pushed_refs: ["dev", "dev-save"],
    })
    mockFastForward.mockResolvedValue({
      remote: "origin",
      working_branch: "dev",
      fast_forwarded: ["dev", "dev-save"],
    })
    mockBranchAway.mockResolvedValue({
      working_branch: "dev",
      set_aside_as: "dev-local-20260621",
    })
  })

  afterEach(cleanup)

  it("shows the no-remotes hint when offline", async () => {
    render(<RemotePushControl pendingSaveCount={0} />)
    await waitFor(() => expect(screen.getByTestId("git-push-no-remotes")).toBeInTheDocument())
  })

  it("auto-selects the sole remote and pushes it", async () => {
    mockGetGitRemotes.mockResolvedValue({ remotes: [remote()], working_branch: "dev" })
    render(<RemotePushControl pendingSaveCount={0} />)
    await waitFor(() => expect(screen.getByTestId("git-push-control")).toBeInTheDocument())
    fireEvent.click(screen.getByTestId("git-push-button"))
    await waitFor(() => expect(mockGitPush).toHaveBeenCalledWith("origin"))
  })

  it("requires a deliberate selection when there are multiple remotes", async () => {
    mockGetGitRemotes.mockResolvedValue({
      remotes: [remote({ name: "origin" }), remote({ name: "backup" })],
      working_branch: "dev",
    })
    render(<RemotePushControl pendingSaveCount={0} />)
    await waitFor(() => expect(screen.getByTestId("git-push-control")).toBeInTheDocument())
    expect(screen.getByTestId("git-push-button")).toBeDisabled()
    fireEvent.change(screen.getByTestId("git-push-remote"), { target: { value: "backup" } })
    fireEvent.click(screen.getByTestId("git-push-button"))
    await waitFor(() => expect(mockGitPush).toHaveBeenCalledWith("backup"))
  })

  it("warns before pushing out-of-version saves and pushes on confirm (overridable)", async () => {
    mockGetGitRemotes.mockResolvedValue({ remotes: [remote()], working_branch: "dev" })
    render(<RemotePushControl pendingSaveCount={3} />)
    await waitFor(() => expect(screen.getByTestId("git-push-control")).toBeInTheDocument())
    fireEvent.click(screen.getByTestId("git-push-button"))
    await waitFor(() => expect(screen.getByTestId("git-push-confirm")).toBeInTheDocument())
    expect(mockGitPush).not.toHaveBeenCalled() // a warning, not auto-push
    fireEvent.click(screen.getByTestId("git-push-confirm-go"))
    await waitFor(() => expect(mockGitPush).toHaveBeenCalledWith("origin"))
  })

  it("does not push when the integrity prompt is cancelled", async () => {
    mockGetGitRemotes.mockResolvedValue({ remotes: [remote()], working_branch: "dev" })
    render(<RemotePushControl pendingSaveCount={1} />)
    await waitFor(() => expect(screen.getByTestId("git-push-control")).toBeInTheDocument())
    fireEvent.click(screen.getByTestId("git-push-button"))
    await waitFor(() => expect(screen.getByTestId("git-push-confirm")).toBeInTheDocument())
    fireEvent.click(screen.getByText("Cancel"))
    await waitFor(() => expect(screen.queryByTestId("git-push-confirm")).not.toBeInTheDocument())
    expect(mockGitPush).not.toHaveBeenCalled()
  })

  it("shows ahead/behind for the selected remote", async () => {
    mockGetGitRemotes.mockResolvedValue({
      remotes: [remote({ ahead: 2, behind: 1 })],
      working_branch: "dev",
    })
    render(<RemotePushControl pendingSaveCount={0} />)
    await waitFor(() => expect(screen.getByTestId("git-push-aheadbehind")).toHaveTextContent("2"))
    expect(screen.getByTestId("git-push-aheadbehind")).toHaveTextContent("1")
  })

  it("shows 'synced' when level with the remote", async () => {
    mockGetGitRemotes.mockResolvedValue({
      remotes: [remote({ ahead: 0, behind: 0 })],
      working_branch: "dev",
    })
    render(<RemotePushControl pendingSaveCount={0} />)
    await waitFor(() => expect(screen.getByTestId("git-push-aheadbehind")).toHaveTextContent("synced"))
  })

  it("distinguishes 'never pushed' (—) from 'couldn't read' (?) — F2 honesty", async () => {
    mockGetGitRemotes.mockResolvedValue({
      remotes: [
        remote({ ahead: null, behind: null, working: { status: "unknown", ahead: null, behind: null } }),
      ],
      working_branch: "dev",
    })
    render(<RemotePushControl pendingSaveCount={0} />)
    await waitFor(() => expect(screen.getByTestId("git-push-aheadbehind")).toHaveTextContent("?"))
  })

  it("surfaces a behind ledger — newer saves on the remote (the two-machine signal)", async () => {
    mockGetGitRemotes.mockResolvedValue({
      remotes: [
        remote({
          ahead: 0,
          behind: 0,
          working: { status: "synced", ahead: 0, behind: 0 },
          ledger: { status: "behind", ahead: 0, behind: 2 },
        }),
      ],
      working_branch: "dev",
    })
    render(<RemotePushControl pendingSaveCount={0} />)
    await waitFor(() =>
      expect(screen.getByTestId("git-push-ledger-status")).toHaveTextContent("2 saves"),
    )
  })

  it("surfaces a forked ledger — diverged save history", async () => {
    mockGetGitRemotes.mockResolvedValue({
      remotes: [
        remote({
          ahead: 0,
          behind: 0,
          working: { status: "synced", ahead: 0, behind: 0 },
          ledger: { status: "diverged", ahead: 1, behind: 2 },
        }),
      ],
      working_branch: "dev",
    })
    render(<RemotePushControl pendingSaveCount={0} />)
    await waitFor(() =>
      expect(screen.getByTestId("git-push-ledger-status")).toHaveTextContent("forked"),
    )
  })

  it("shows the honest fork modal on a 409 rejection — never a dead-end (M7)", async () => {
    mockGetGitRemotes.mockResolvedValue({ remotes: [remote()], working_branch: "dev" })
    const rejection = {
      status: "rejected_diverged",
      remote: "origin",
      working: { status: "diverged", ahead: 1, behind: 2 },
      ledger: { status: "ahead", ahead: 1, behind: 0 },
      message:
        "The working branch on 'origin' changed since you last synced. " +
        "haute never force-pushes — your local work is safe.",
    }
    mockGitPush.mockRejectedValue(
      new ApiError("HTTP 409", 409, JSON.stringify({ detail: rejection }), { detail: rejection }),
    )
    render(<RemotePushControl pendingSaveCount={0} />)
    await waitFor(() => expect(screen.getByTestId("git-push-control")).toBeInTheDocument())
    fireEvent.click(screen.getByTestId("git-push-button"))
    await waitFor(() => expect(screen.getByTestId("git-push-rejected")).toBeInTheDocument())
    expect(screen.getByTestId("git-push-rejected")).toHaveTextContent("never force-pushes")
    // Both legs are listed so the user sees which one forked.
    expect(screen.getAllByTestId("git-push-rejected-leg")).toHaveLength(2)
    fireEvent.click(screen.getByTestId("git-push-rejected-dismiss"))
    await waitFor(() =>
      expect(screen.queryByTestId("git-push-rejected")).not.toBeInTheDocument(),
    )
  })

  it("falls back to a toast when a non-409 push error occurs", async () => {
    mockGetGitRemotes.mockResolvedValue({ remotes: [remote()], working_branch: "dev" })
    mockGitPush.mockRejectedValue(new ApiError("HTTP 500", 500, "server boom"))
    render(<RemotePushControl pendingSaveCount={0} />)
    await waitFor(() => expect(screen.getByTestId("git-push-control")).toBeInTheDocument())
    fireEvent.click(screen.getByTestId("git-push-button"))
    await waitFor(() => expect(mockGitPush).toHaveBeenCalled())
    expect(screen.queryByTestId("git-push-rejected")).not.toBeInTheDocument()
  })

  it("offers Catch up when a leg is behind-clean and fast-forwards on click (D1/D2)", async () => {
    mockGetGitRemotes.mockResolvedValue({
      remotes: [
        remote({
          ahead: 0,
          behind: 1,
          working: { status: "behind", ahead: 0, behind: 1 },
          ledger: { status: "behind", ahead: 0, behind: 1 },
        }),
      ],
      working_branch: "dev",
    })
    render(<RemotePushControl pendingSaveCount={0} />)
    await waitFor(() => expect(screen.getByTestId("git-catch-up-button")).toBeInTheDocument())
    fireEvent.click(screen.getByTestId("git-catch-up-button"))
    await waitFor(() => expect(mockFastForward).toHaveBeenCalledWith("origin"))
  })

  it("does NOT offer Catch up when a leg is diverged (forked, not behind-clean)", async () => {
    mockGetGitRemotes.mockResolvedValue({
      remotes: [
        remote({
          ahead: 1,
          behind: 1,
          working: { status: "diverged", ahead: 1, behind: 1 },
          ledger: { status: "diverged", ahead: 1, behind: 1 },
        }),
      ],
      working_branch: "dev",
    })
    render(<RemotePushControl pendingSaveCount={0} />)
    await waitFor(() => expect(screen.getByTestId("git-push-control")).toBeInTheDocument())
    expect(screen.queryByTestId("git-catch-up-button")).not.toBeInTheDocument()
  })

  it("rejection modal offers Catch up for a behind-only fork", async () => {
    mockGetGitRemotes.mockResolvedValue({ remotes: [remote()], working_branch: "dev" })
    const rejection = {
      status: "rejected_diverged",
      remote: "origin",
      working: { status: "behind", ahead: 0, behind: 2 },
      ledger: { status: "behind", ahead: 0, behind: 2 },
      message: "behind 'origin' — never force-pushes.",
    }
    mockGitPush.mockRejectedValue(
      new ApiError("HTTP 409", 409, JSON.stringify({ detail: rejection }), { detail: rejection }),
    )
    render(<RemotePushControl pendingSaveCount={0} />)
    await waitFor(() => expect(screen.getByTestId("git-push-control")).toBeInTheDocument())
    fireEvent.click(screen.getByTestId("git-push-button"))
    await waitFor(() => expect(screen.getByTestId("git-push-rejected-catch-up")).toBeInTheDocument())
    fireEvent.click(screen.getByTestId("git-push-rejected-catch-up"))
    await waitFor(() => expect(mockFastForward).toHaveBeenCalledWith("origin"))
  })

  it("diverged fork offers Spin off a copy (branch-away), not Catch up (M3)", async () => {
    mockGetGitRemotes.mockResolvedValue({ remotes: [remote()], working_branch: "dev" })
    const rejection = {
      status: "rejected_diverged",
      remote: "origin",
      working: { status: "diverged", ahead: 1, behind: 2 },
      ledger: { status: "diverged", ahead: 1, behind: 2 },
      message: "forked 'origin' — never force-pushes.",
    }
    mockGitPush.mockRejectedValue(
      new ApiError("HTTP 409", 409, JSON.stringify({ detail: rejection }), { detail: rejection }),
    )
    render(<RemotePushControl pendingSaveCount={0} />)
    await waitFor(() => expect(screen.getByTestId("git-push-control")).toBeInTheDocument())
    fireEvent.click(screen.getByTestId("git-push-button"))
    await waitFor(() => expect(screen.getByTestId("git-push-rejected")).toBeInTheDocument())
    expect(screen.queryByTestId("git-push-rejected-catch-up")).not.toBeInTheDocument()
    fireEvent.click(screen.getByTestId("git-push-rejected-branch-away"))
    await waitFor(() => expect(mockBranchAway).toHaveBeenCalledWith("origin"))
  })

  it("shows no ledger chip when the save history is in sync", async () => {
    mockGetGitRemotes.mockResolvedValue({
      remotes: [
        remote({ ahead: 0, behind: 0, ledger: { status: "synced", ahead: 0, behind: 0 } }),
      ],
      working_branch: "dev",
    })
    render(<RemotePushControl pendingSaveCount={0} />)
    await waitFor(() => expect(screen.getByTestId("git-push-aheadbehind")).toBeInTheDocument())
    expect(screen.queryByTestId("git-push-ledger-status")).not.toBeInTheDocument()
  })
})
