import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { render, screen, fireEvent, cleanup, waitFor } from "@testing-library/react"
import RemotePushControl from "../RemotePushControl"

const mockGetGitRemotes = vi.fn()
const mockGitPush = vi.fn()

vi.mock("../../api/client", () => ({
  getGitRemotes: (...a: unknown[]) => mockGetGitRemotes(...a),
  gitPush: (...a: unknown[]) => mockGitPush(...a),
}))

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
})
