import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { render, screen, fireEvent, cleanup, waitFor } from "@testing-library/react"
import RemotePushControl from "../RemotePushControl"
import { ApiError } from "../../api/client"

const mockGetGitRemotes = vi.fn()
const mockGitPush = vi.fn()
const mockFastForward = vi.fn()
const mockBranchAway = vi.fn()
const mockAddToast = vi.fn()

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

// Capture toast calls so the error paths can be asserted on directly.
vi.mock("../../stores/useToastStore", () => ({
  default: (selector: (s: { addToast: typeof mockAddToast }) => unknown) =>
    selector({ addToast: mockAddToast }),
}))

const remote = (over: Partial<Record<string, unknown>> = {}) => ({
  name: "origin",
  url: "git@example.com:x.git",
  ahead: null,
  behind: null,
  ...over,
})

describe("RemotePushControl — error paths and catch-up matrix", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockGetGitRemotes.mockResolvedValue({ remotes: [], working_branch: "dev" })
    mockGitPush.mockResolvedValue({
      remote: "origin",
      working_branch: "dev",
      ledger_branch: "dev-save",
      pushed_refs: ["dev", "dev-save"],
      default_branch: "main",
      bootstrapped_default: false,
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

  // ── catch-up (fast-forward) rejection / throw ────────────────────────────
  it("toasts 'Couldn't catch up' with the Error message when fast-forward throws", async () => {
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
    mockFastForward.mockRejectedValue(new Error("ref locked on remote"))
    render(<RemotePushControl pendingSaveCount={0} />)
    await waitFor(() => expect(screen.getByTestId("git-catch-up-button")).toBeInTheDocument())
    fireEvent.click(screen.getByTestId("git-catch-up-button"))
    await waitFor(() =>
      expect(mockAddToast).toHaveBeenCalledWith("error", "Couldn't catch up: ref locked on remote"),
    )
  })

  it("falls back to 'unknown error' when fast-forward rejects with a non-Error", async () => {
    mockGetGitRemotes.mockResolvedValue({
      remotes: [
        remote({
          ahead: 0,
          behind: 1,
          working: { status: "behind", ahead: 0, behind: 1 },
          ledger: { status: "synced", ahead: 0, behind: 0 },
        }),
      ],
      working_branch: "dev",
    })
    mockFastForward.mockRejectedValue("boom")
    render(<RemotePushControl pendingSaveCount={0} />)
    await waitFor(() => expect(screen.getByTestId("git-catch-up-button")).toBeInTheDocument())
    fireEvent.click(screen.getByTestId("git-catch-up-button"))
    await waitFor(() =>
      expect(mockAddToast).toHaveBeenCalledWith("error", "Couldn't catch up: unknown error"),
    )
  })

  // ── branch-away (spin off a copy) rejection / throw ──────────────────────
  it("toasts 'Couldn't spin off a copy' with the Error message when branch-away throws", async () => {
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
    mockBranchAway.mockRejectedValue(new Error("worktree dirty"))
    render(<RemotePushControl pendingSaveCount={0} />)
    await waitFor(() => expect(screen.getByTestId("git-push-control")).toBeInTheDocument())
    fireEvent.click(screen.getByTestId("git-push-button"))
    await waitFor(() =>
      expect(screen.getByTestId("git-push-rejected-branch-away")).toBeInTheDocument(),
    )
    fireEvent.click(screen.getByTestId("git-push-rejected-branch-away"))
    await waitFor(() =>
      expect(mockAddToast).toHaveBeenCalledWith(
        "error",
        "Couldn't spin off a copy: worktree dirty",
      ),
    )
  })

  it("falls back to 'unknown error' when branch-away rejects with a non-Error", async () => {
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
    mockBranchAway.mockRejectedValue({ code: "EBUSY" })
    render(<RemotePushControl pendingSaveCount={0} />)
    await waitFor(() => expect(screen.getByTestId("git-push-control")).toBeInTheDocument())
    fireEvent.click(screen.getByTestId("git-push-button"))
    await waitFor(() =>
      expect(screen.getByTestId("git-push-rejected-branch-away")).toBeInTheDocument(),
    )
    fireEvent.click(screen.getByTestId("git-push-rejected-branch-away"))
    await waitFor(() =>
      expect(mockAddToast).toHaveBeenCalledWith(
        "error",
        "Couldn't spin off a copy: unknown error",
      ),
    )
  })

  // ── push 409 with an UNPARSEABLE body → plain toast fallback ─────────────
  it("falls back to a plain push-failed toast when a 409 body isn't a parseable rejection", async () => {
    mockGetGitRemotes.mockResolvedValue({ remotes: [remote()], working_branch: "dev" })
    // status !== "rejected_diverged" so parseGitPushRejection returns null.
    const garbage = { detail: { status: "something_else", oops: true } }
    mockGitPush.mockRejectedValue(
      new ApiError("HTTP 409 conflict", 409, JSON.stringify(garbage), garbage),
    )
    render(<RemotePushControl pendingSaveCount={0} />)
    await waitFor(() => expect(screen.getByTestId("git-push-control")).toBeInTheDocument())
    fireEvent.click(screen.getByTestId("git-push-button"))
    await waitFor(() =>
      expect(mockAddToast).toHaveBeenCalledWith("error", "Push failed: HTTP 409 conflict"),
    )
    // No honest fork modal — the body couldn't be parsed.
    expect(screen.queryByTestId("git-push-rejected")).not.toBeInTheDocument()
  })

  it("falls back to a plain push-failed toast when a 409 carries no detail body", async () => {
    mockGetGitRemotes.mockResolvedValue({ remotes: [remote()], working_branch: "dev" })
    mockGitPush.mockRejectedValue(new ApiError("HTTP 409 conflict", 409, "no body"))
    render(<RemotePushControl pendingSaveCount={0} />)
    await waitFor(() => expect(screen.getByTestId("git-push-control")).toBeInTheDocument())
    fireEvent.click(screen.getByTestId("git-push-button"))
    await waitFor(() =>
      expect(mockAddToast).toHaveBeenCalledWith("error", "Push failed: HTTP 409 conflict"),
    )
    expect(screen.queryByTestId("git-push-rejected")).not.toBeInTheDocument()
  })

  // ── canCatchUp mixed leg-state matrix ────────────────────────────────────
  // Behind-only on either leg, no leg ahead/diverged → "Catch up" offered.
  it("offers Catch up when only the working leg is behind (ledger synced)", async () => {
    mockGetGitRemotes.mockResolvedValue({
      remotes: [
        remote({
          ahead: 0,
          behind: 1,
          working: { status: "behind", ahead: 0, behind: 1 },
          ledger: { status: "synced", ahead: 0, behind: 0 },
        }),
      ],
      working_branch: "dev",
    })
    render(<RemotePushControl pendingSaveCount={0} />)
    await waitFor(() => expect(screen.getByTestId("git-catch-up-button")).toBeInTheDocument())
  })

  it("offers Catch up when only the ledger leg is behind (working synced)", async () => {
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
    await waitFor(() => expect(screen.getByTestId("git-catch-up-button")).toBeInTheDocument())
  })

  // A leg ahead (local work the remote lacks) blocks the clean fast-forward,
  // even when the other leg is behind → no Catch up button.
  it("does NOT offer Catch up when the working leg is ahead while the ledger is behind", async () => {
    mockGetGitRemotes.mockResolvedValue({
      remotes: [
        remote({
          ahead: 1,
          behind: 0,
          working: { status: "ahead", ahead: 1, behind: 0 },
          ledger: { status: "behind", ahead: 0, behind: 2 },
        }),
      ],
      working_branch: "dev",
    })
    render(<RemotePushControl pendingSaveCount={0} />)
    await waitFor(() => expect(screen.getByTestId("git-push-control")).toBeInTheDocument())
    expect(screen.queryByTestId("git-catch-up-button")).not.toBeInTheDocument()
  })

  it("does NOT offer Catch up when the ledger leg is diverged while the working is behind", async () => {
    mockGetGitRemotes.mockResolvedValue({
      remotes: [
        remote({
          ahead: 0,
          behind: 1,
          working: { status: "behind", ahead: 0, behind: 1 },
          ledger: { status: "diverged", ahead: 1, behind: 1 },
        }),
      ],
      working_branch: "dev",
    })
    render(<RemotePushControl pendingSaveCount={0} />)
    await waitFor(() => expect(screen.getByTestId("git-push-control")).toBeInTheDocument())
    expect(screen.queryByTestId("git-catch-up-button")).not.toBeInTheDocument()
  })

  // No leg behind (both synced / ahead-only) → nothing to catch up to.
  it("does NOT offer Catch up when no leg is behind (both synced)", async () => {
    mockGetGitRemotes.mockResolvedValue({
      remotes: [
        remote({
          ahead: 0,
          behind: 0,
          working: { status: "synced", ahead: 0, behind: 0 },
          ledger: { status: "synced", ahead: 0, behind: 0 },
        }),
      ],
      working_branch: "dev",
    })
    render(<RemotePushControl pendingSaveCount={0} />)
    await waitFor(() => expect(screen.getByTestId("git-push-aheadbehind")).toBeInTheDocument())
    expect(screen.queryByTestId("git-catch-up-button")).not.toBeInTheDocument()
  })
})
