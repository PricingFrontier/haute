import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { render, screen, fireEvent, cleanup, waitFor, within } from "@testing-library/react"
import RemotePushControl from "../RemotePushControl"
import { ApiError } from "../../api/client"
import useGitStore from "../../stores/useGitStore"
import useGraphStore from "../../stores/useGraphStore"
import useWorkbenchFormStore from "../../stores/useWorkbenchFormStore"
import useWorkbenchStore from "../../stores/useWorkbenchStore"
import { makeGitWorkingBranch } from "../../test-utils/factories"

const mockGetGitRemotes = vi.fn()
const mockGitPush = vi.fn()
const mockFastForward = vi.fn()
const mockBranchAway = vi.fn()
const mockSetCiSecrets = vi.fn()
const mockGetWorkingBranch = vi.fn()
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
    gitSetCiSecrets: (...a: unknown[]) => mockSetCiSecrets(...a),
    // The readiness a catch-up or a spin-off reads again once it succeeded.
    getWorkingBranch: (...a: unknown[]) => mockGetWorkingBranch(...a),
  }
})

vi.mock("../../stores/useToastStore", () => ({
  default: (selector: (s: { addToast: typeof mockAddToast }) => unknown) =>
    selector({ addToast: mockAddToast }),
}))

const remote = (over: Partial<Record<string, unknown>> = {}) => ({
  name: "origin",
  url: "git@example.com:x.git",
  working: null,
  ledger: null,
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
    mockGetWorkingBranch.mockResolvedValue(makeGitWorkingBranch({ working_branch: "dev", state: "ready" }))
  })

  afterEach(cleanup)

  it("shows the no-remotes hint when offline", async () => {
    render(<RemotePushControl pendingSaveCount={0} />)
    await waitFor(() => expect(screen.getByTestId("git-push-no-remotes")).toBeInTheDocument())
  })

  it("auto-selects the sole remote and pushes it", async () => {
    mockGetGitRemotes.mockResolvedValue({ remotes: [remote()], working_branch: "dev" })
    render(<RemotePushControl pendingSaveCount={0} />)
    // `git-push-control` is in the DOM from the FIRST commit, before the
    // remotes load has resolved — so waiting for it can resume the test while
    // the push button is still disabled (no selection yet), and under machine
    // load the click below lands on the disabled button and is silently
    // swallowed (seen once in a full-suite preflight run, 2026-07-09 — same
    // commit-vs-later-scheduler-task race class as the ComparisonView
    // fit-race fix). Wait for the button to be ENABLED instead: that state
    // only exists once the load has committed the auto-selected remote. Same
    // pattern in every test below that clicks or asserts right after render.
    await waitFor(() => expect(screen.getByTestId("git-push-button")).toBeEnabled())
    fireEvent.click(screen.getByTestId("git-push-button"))
    await waitFor(() => expect(mockGitPush).toHaveBeenCalledWith("origin"))
  })

  it("does not push merely from mounting or selecting the sole remote", async () => {
    mockGetGitRemotes.mockResolvedValue({ remotes: [remote()], working_branch: "dev" })
    render(<RemotePushControl pendingSaveCount={0} />)
    await waitFor(() => expect(screen.getByTestId("git-push-button")).toBeEnabled())
    expect(mockGitPush).not.toHaveBeenCalled()
  })

  it("uses the exact bootstrap tooltip and reports a custom default branch", async () => {
    mockGetGitRemotes.mockResolvedValue({ remotes: [remote()], working_branch: "dev" })
    mockGitPush.mockResolvedValue({
      remote: "origin",
      working_branch: "dev",
      ledger_branch: "dev-save",
      pushed_refs: ["trunk", "dev", "dev-save"],
      default_branch: "trunk",
      bootstrapped_default: true,
    })
    render(<RemotePushControl pendingSaveCount={0} />)
    await waitFor(() => expect(screen.getByTestId("git-push-button")).toBeEnabled())
    expect(
      screen.getByRole("tooltip", {
        name: "Push your branch and save history. If the remote is empty, Haute also publishes the default branch as your merge target.",
      }),
    ).toBeInTheDocument()
    fireEvent.click(screen.getByTestId("git-push-button"))
    await waitFor(() =>
      expect(mockAddToast).toHaveBeenCalledWith(
        "success",
        "Published trunk and your branch history to origin",
      ),
    )
  })

  it("keeps the ordinary pushed-ref count toast when default bootstrap was not needed", async () => {
    mockGetGitRemotes.mockResolvedValue({ remotes: [remote()], working_branch: "dev" })
    mockGitPush.mockResolvedValue({
      remote: "origin",
      working_branch: "dev",
      ledger_branch: "dev-save",
      pushed_refs: ["dev", "dev-save"],
      default_branch: "trunk",
      bootstrapped_default: false,
    })
    render(<RemotePushControl pendingSaveCount={0} />)
    await waitFor(() => expect(screen.getByTestId("git-push-button")).toBeEnabled())
    fireEvent.click(screen.getByTestId("git-push-button"))
    await waitFor(() =>
      expect(mockAddToast).toHaveBeenCalledWith("success", "Pushed 2 branches to origin"),
    )
  })

  it("uses the bootstrap toast only for the first of two explicit pushes", async () => {
    mockGetGitRemotes.mockResolvedValue({ remotes: [remote()], working_branch: "dev" })
    mockGitPush
      .mockResolvedValueOnce({
        remote: "origin",
        working_branch: "dev",
        ledger_branch: "dev-save",
        pushed_refs: ["release", "dev", "dev-save"],
        default_branch: "release",
        bootstrapped_default: true,
      })
      .mockResolvedValueOnce({
        remote: "origin",
        working_branch: "dev",
        ledger_branch: "dev-save",
        pushed_refs: ["dev", "dev-save"],
        default_branch: "release",
        bootstrapped_default: false,
      })
    render(<RemotePushControl pendingSaveCount={0} />)
    await waitFor(() => expect(screen.getByTestId("git-push-button")).toBeEnabled())

    fireEvent.click(screen.getByTestId("git-push-button"))
    await waitFor(() =>
      expect(mockAddToast).toHaveBeenCalledWith(
        "success",
        "Published release and your branch history to origin",
      ),
    )

    fireEvent.click(screen.getByTestId("git-push-button"))
    await waitFor(() =>
      expect(mockAddToast).toHaveBeenCalledWith("success", "Pushed 2 branches to origin"),
    )
    expect(mockGitPush).toHaveBeenCalledTimes(2)
  })

  it("requires a deliberate selection when there are multiple remotes", async () => {
    mockGetGitRemotes.mockResolvedValue({
      remotes: [remote({ name: "origin" }), remote({ name: "backup" })],
      working_branch: "dev",
    })
    render(<RemotePushControl pendingSaveCount={0} />)
    // Wait for the loaded options, not the control: pre-load, the disabled
    // assertion passes vacuously and the change below has no "backup" option
    // to select yet.
    await waitFor(() => expect(screen.getByRole("option", { name: /backup/ })).toBeInTheDocument())
    expect(screen.getByTestId("git-push-button")).toBeDisabled()
    fireEvent.change(screen.getByTestId("git-push-remote"), { target: { value: "backup" } })
    fireEvent.click(screen.getByTestId("git-push-button"))
    await waitFor(() => expect(mockGitPush).toHaveBeenCalledWith("backup"))
  })

  it("preserves the pending-save confirmation before an explicit push", async () => {
    mockGetGitRemotes.mockResolvedValue({ remotes: [remote()], working_branch: "dev" })
    render(<RemotePushControl pendingSaveCount={3} />)
    await waitFor(() => expect(screen.getByTestId("git-push-button")).toBeEnabled())
    fireEvent.click(screen.getByTestId("git-push-button"))
    await waitFor(() => expect(screen.getByTestId("git-push-confirm")).toBeInTheDocument())
    expect(mockGitPush).not.toHaveBeenCalled() // a warning, not auto-push
    fireEvent.click(screen.getByTestId("git-push-confirm-go"))
    await waitFor(() => expect(mockGitPush).toHaveBeenCalledWith("origin"))
  })

  it("does not push when the integrity prompt is cancelled", async () => {
    mockGetGitRemotes.mockResolvedValue({ remotes: [remote()], working_branch: "dev" })
    render(<RemotePushControl pendingSaveCount={1} />)
    await waitFor(() => expect(screen.getByTestId("git-push-button")).toBeEnabled())
    fireEvent.click(screen.getByTestId("git-push-button"))
    await waitFor(() => expect(screen.getByTestId("git-push-confirm")).toBeInTheDocument())
    fireEvent.click(screen.getByText("Cancel"))
    await waitFor(() => expect(screen.queryByTestId("git-push-confirm")).not.toBeInTheDocument())
    expect(mockGitPush).not.toHaveBeenCalled()
  })

  it("shows ahead/behind for the selected remote", async () => {
    mockGetGitRemotes.mockResolvedValue({
      remotes: [remote({ working: { status: "diverged", ahead: 2, behind: 1 } })],
      working_branch: "dev",
    })
    render(<RemotePushControl pendingSaveCount={0} />)
    await waitFor(() => expect(screen.getByTestId("git-push-aheadbehind")).toHaveTextContent("2"))
    expect(screen.getByTestId("git-push-aheadbehind")).toHaveTextContent("1")
  })

  it("shows 'synced' when level with the remote", async () => {
    mockGetGitRemotes.mockResolvedValue({
      remotes: [remote({ working: { status: "synced", ahead: 0, behind: 0 } })],
      working_branch: "dev",
    })
    render(<RemotePushControl pendingSaveCount={0} />)
    await waitFor(() => expect(screen.getByTestId("git-push-aheadbehind")).toHaveTextContent("synced"))
  })

  it("distinguishes 'never pushed' (-) from 'couldn't read' (?) - F2 honesty", async () => {
    mockGetGitRemotes.mockResolvedValue({
      remotes: [
        remote({ working: { status: "unknown", ahead: null, behind: null } }),
      ],
      working_branch: "dev",
    })
    render(<RemotePushControl pendingSaveCount={0} />)
    await waitFor(() => expect(screen.getByTestId("git-push-aheadbehind")).toHaveTextContent("?"))
  })

  it("surfaces a behind ledger - newer saves on the remote (the two-machine signal)", async () => {
    mockGetGitRemotes.mockResolvedValue({
      remotes: [
        remote({
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

  it("surfaces a forked ledger - diverged save history", async () => {
    mockGetGitRemotes.mockResolvedValue({
      remotes: [
        remote({
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

  it("shows the honest fork modal on a 409 rejection - never a dead-end (M7)", async () => {
    mockGetGitRemotes.mockResolvedValue({ remotes: [remote()], working_branch: "dev" })
    const rejection = {
      status: "rejected_diverged",
      remote: "origin",
      working: { status: "diverged", ahead: 1, behind: 2 },
      ledger: { status: "ahead", ahead: 1, behind: 0 },
      message:
        "The working branch on 'origin' changed since you last synced. " +
        "haute never force-pushes - your local work is safe.",
      is_rewrite: false,
    }
    mockGitPush.mockRejectedValue(
      new ApiError("HTTP 409", 409, JSON.stringify({ detail: rejection }), { detail: rejection }),
    )
    render(<RemotePushControl pendingSaveCount={0} />)
    await waitFor(() => expect(screen.getByTestId("git-push-button")).toBeEnabled())
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
    await waitFor(() => expect(screen.getByTestId("git-push-button")).toBeEnabled())
    fireEvent.click(screen.getByTestId("git-push-button"))
    await waitFor(() => expect(mockGitPush).toHaveBeenCalled())
    expect(screen.queryByTestId("git-push-rejected")).not.toBeInTheDocument()
  })

  it("offers Catch up when a leg is behind-clean and fast-forwards on click (D1/D2)", async () => {
    mockGetGitRemotes.mockResolvedValue({
      remotes: [
        remote({
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
          working: { status: "diverged", ahead: 1, behind: 1 },
          ledger: { status: "diverged", ahead: 1, behind: 1 },
        }),
      ],
      working_branch: "dev",
    })
    render(<RemotePushControl pendingSaveCount={0} />)
    // Wait for a post-load signal (the ahead/behind badge) — pre-load the
    // absence assertion passes vacuously, before Catch up could ever render.
    await waitFor(() => expect(screen.getByTestId("git-push-aheadbehind")).toBeInTheDocument())
    expect(screen.queryByTestId("git-catch-up-button")).not.toBeInTheDocument()
  })

  it("rejection modal offers Catch up for a behind-only fork", async () => {
    mockGetGitRemotes.mockResolvedValue({ remotes: [remote()], working_branch: "dev" })
    const rejection = {
      status: "rejected_diverged",
      remote: "origin",
      working: { status: "behind", ahead: 0, behind: 2 },
      ledger: { status: "behind", ahead: 0, behind: 2 },
      message: "behind 'origin' - never force-pushes.",
      is_rewrite: false,
    }
    mockGitPush.mockRejectedValue(
      new ApiError("HTTP 409", 409, JSON.stringify({ detail: rejection }), { detail: rejection }),
    )
    render(<RemotePushControl pendingSaveCount={0} />)
    await waitFor(() => expect(screen.getByTestId("git-push-button")).toBeEnabled())
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
      message: "forked 'origin' - never force-pushes.",
      is_rewrite: false,
    }
    mockGitPush.mockRejectedValue(
      new ApiError("HTTP 409", 409, JSON.stringify({ detail: rejection }), { detail: rejection }),
    )
    render(<RemotePushControl pendingSaveCount={0} />)
    await waitFor(() => expect(screen.getByTestId("git-push-button")).toBeEnabled())
    fireEvent.click(screen.getByTestId("git-push-button"))
    await waitFor(() => expect(screen.getByTestId("git-push-rejected")).toBeInTheDocument())
    expect(screen.queryByTestId("git-push-rejected-catch-up")).not.toBeInTheDocument()
    fireEvent.click(screen.getByTestId("git-push-rejected-branch-away"))
    await waitFor(() => expect(mockBranchAway).toHaveBeenCalledWith("origin"))
  })

  it("shows a distinct 'history rewritten' heading for a rewrite fork (X3)", async () => {
    mockGetGitRemotes.mockResolvedValue({ remotes: [remote()], working_branch: "dev" })
    const rejection = {
      status: "rejected_diverged",
      remote: "origin",
      working: { status: "diverged", ahead: 1, behind: 1 },
      ledger: { status: "diverged", ahead: 1, behind: 1 },
      message: "history was rewritten - a person needs to reconcile.",
      is_rewrite: true,
    }
    mockGitPush.mockRejectedValue(
      new ApiError("HTTP 409", 409, JSON.stringify({ detail: rejection }), { detail: rejection }),
    )
    render(<RemotePushControl pendingSaveCount={0} />)
    await waitFor(() => expect(screen.getByTestId("git-push-button")).toBeEnabled())
    fireEvent.click(screen.getByTestId("git-push-button"))
    await waitFor(() =>
      expect(screen.getByTestId("git-push-rejected-heading")).toHaveTextContent("rewritten"),
    )
    // A rewrite is still escapable via branch-away (preserves local work).
    expect(screen.getByTestId("git-push-rejected-branch-away")).toBeInTheDocument()
  })

  it("shows no ledger chip when the save history is in sync", async () => {
    mockGetGitRemotes.mockResolvedValue({
      remotes: [
        remote({ working: { status: "synced", ahead: 0, behind: 0 }, ledger: { status: "synced", ahead: 0, behind: 0 } }),
      ],
      working_branch: "dev",
    })
    render(<RemotePushControl pendingSaveCount={0} />)
    await waitFor(() => expect(screen.getByTestId("git-push-aheadbehind")).toBeInTheDocument())
    expect(screen.queryByTestId("git-push-ledger-status")).not.toBeInTheDocument()
  })

  describe("the deploy's secrets", () => {
    const pushed = (over: Record<string, unknown> = {}) => ({
      remote: "origin",
      working_branch: "dev",
      ledger_branch: "dev-save",
      pushed_refs: ["dev", "dev-save"],
      default_branch: "main",
      bootstrapped_default: false,
      ci_secrets: null,
      ...over,
    })
    const secrets = (over: Record<string, unknown> = {}) => ({
      repository: "acme/motor",
      state: "ready",
      missing: [],
      newly_set: [],
      problem: null,
      ...over,
    })
    const missingTwo = secrets({
      state: "missing",
      missing: ["DATABRICKS_RATING_HOST", "DATABRICKS_RATING_TOKEN"],
    })

    async function pushOnce() {
      await waitFor(() => expect(screen.getByTestId("git-push-button")).toBeEnabled())
      fireEvent.click(screen.getByTestId("git-push-button"))
      await waitFor(() => expect(mockGitPush).toHaveBeenCalled())
    }

    beforeEach(() => {
      mockGetGitRemotes.mockResolvedValue({ remotes: [remote()], working_branch: "dev" })
    })

    it("toasts the secrets a first publication set", async () => {
      mockGitPush.mockResolvedValue(
        pushed({
          bootstrapped_default: true,
          pushed_refs: ["main", "dev", "dev-save"],
          ci_secrets: secrets({ newly_set: ["DATABRICKS_RATING_HOST", "DATABRICKS_RATING_TOKEN"] }),
        }),
      )
      render(<RemotePushControl pendingSaveCount={0} />)
      await pushOnce()

      await waitFor(() =>
        expect(mockAddToast).toHaveBeenCalledWith(
          "success",
          "Set 2 deploy secrets on acme/motor from .env",
        ),
      )
      expect(screen.queryByTestId("git-ci-secrets-missing")).not.toBeInTheDocument()
    })

    it("warns with a first publication's problem and offers Set from .env", async () => {
      mockGitPush.mockResolvedValue(
        pushed({
          bootstrapped_default: true,
          ci_secrets: { ...missingTwo, problem: "Still .env.example's placeholder: X." },
        }),
      )
      render(<RemotePushControl pendingSaveCount={0} />)
      await pushOnce()

      await waitFor(() =>
        expect(mockAddToast).toHaveBeenCalledWith(
          "warning",
          "Couldn't set the deploy's secrets on acme/motor: Still .env.example's placeholder: X.",
        ),
      )
      expect(screen.getByTestId("git-ci-secrets-missing")).toHaveTextContent(
        "2 deploy secrets missing on GitHub",
      )
      expect(
        screen.getByRole("tooltip", { name: "DATABRICKS_RATING_HOST, DATABRICKS_RATING_TOKEN" }),
      ).toBeInTheDocument()
    })

    it("warns when a first publication could not read the secrets", async () => {
      const problem = "The GitHub CLI (gh) is not installed."
      mockGitPush.mockResolvedValue(
        pushed({ bootstrapped_default: true, ci_secrets: secrets({ state: "unknown", problem }) }),
      )
      render(<RemotePushControl pendingSaveCount={0} />)
      await pushOnce()

      await waitFor(() =>
        expect(mockAddToast).toHaveBeenCalledWith(
          "warning",
          `Couldn't set the deploy's secrets on acme/motor: ${problem}`,
        ),
      )
      expect(screen.queryByTestId("git-ci-secrets-missing")).not.toBeInTheDocument()
    })

    it("says nothing when a later Push cannot read the secrets", async () => {
      mockGitPush.mockResolvedValue(
        pushed({ ci_secrets: secrets({ state: "unknown", problem: "HTTP 403: no admin rights" }) }),
      )
      render(<RemotePushControl pendingSaveCount={0} />)
      await pushOnce()

      await waitFor(() =>
        expect(mockAddToast).toHaveBeenCalledWith("success", "Pushed 2 branches to origin"),
      )
      expect(mockAddToast).toHaveBeenCalledTimes(1)
      expect(screen.queryByTestId("git-ci-secrets-missing")).not.toBeInTheDocument()
    })

    it("shows the missing secrets only while their remote is selected", async () => {
      mockGetGitRemotes.mockResolvedValue({
        remotes: [remote({ name: "origin" }), remote({ name: "backup" })],
        working_branch: "dev",
      })
      mockGitPush.mockResolvedValue(pushed({ ci_secrets: missingTwo }))
      render(<RemotePushControl pendingSaveCount={0} />)
      await waitFor(() => expect(screen.getByRole("option", { name: /backup/ })).toBeInTheDocument())
      fireEvent.change(screen.getByTestId("git-push-remote"), { target: { value: "origin" } })
      await pushOnce()

      await waitFor(() => expect(screen.getByTestId("git-ci-secrets-missing")).toBeInTheDocument())
      fireEvent.change(screen.getByTestId("git-push-remote"), { target: { value: "backup" } })
      expect(screen.queryByTestId("git-ci-secrets-missing")).not.toBeInTheDocument()
      fireEvent.change(screen.getByTestId("git-push-remote"), { target: { value: "origin" } })
      expect(screen.getByTestId("git-ci-secrets-missing")).toBeInTheDocument()
    })

    it("Set from .env sets the selected remote's secrets and clears the line", async () => {
      mockGitPush.mockResolvedValue(pushed({ ci_secrets: missingTwo }))
      mockSetCiSecrets.mockResolvedValue(
        secrets({ newly_set: ["DATABRICKS_RATING_HOST", "DATABRICKS_RATING_TOKEN"] }),
      )
      render(<RemotePushControl pendingSaveCount={0} />)
      await pushOnce()
      await waitFor(() => expect(screen.getByTestId("git-ci-secrets-set")).toBeEnabled())

      fireEvent.click(screen.getByTestId("git-ci-secrets-set"))

      await waitFor(() =>
        expect(mockAddToast).toHaveBeenCalledWith(
          "success",
          "Set 2 deploy secrets on acme/motor from .env",
        ),
      )
      expect(mockSetCiSecrets).toHaveBeenCalledWith("origin")
      expect(screen.queryByTestId("git-ci-secrets-missing")).not.toBeInTheDocument()
    })

    it("keeps the line and toasts the error when Set from .env fails", async () => {
      mockGitPush.mockResolvedValue(pushed({ ci_secrets: missingTwo }))
      mockSetCiSecrets.mockRejectedValue(new Error("Still .env.example's placeholder: X."))
      render(<RemotePushControl pendingSaveCount={0} />)
      await pushOnce()
      await waitFor(() => expect(screen.getByTestId("git-ci-secrets-set")).toBeEnabled())

      fireEvent.click(screen.getByTestId("git-ci-secrets-set"))

      await waitFor(() =>
        expect(mockAddToast).toHaveBeenCalledWith(
          "error",
          "Couldn't set the deploy's secrets: Still .env.example's placeholder: X.",
        ),
      )
      expect(screen.getByTestId("git-ci-secrets-set")).toBeEnabled()
      expect(screen.getByTestId("git-ci-secrets-missing")).toBeInTheDocument()
    })

    it("clears the line when a later Push reports none missing", async () => {
      mockGitPush
        .mockResolvedValueOnce(pushed({ ci_secrets: missingTwo }))
        .mockResolvedValueOnce(pushed({ ci_secrets: secrets() }))
      render(<RemotePushControl pendingSaveCount={0} />)
      await pushOnce()
      await waitFor(() => expect(screen.getByTestId("git-ci-secrets-missing")).toBeInTheDocument())

      fireEvent.click(screen.getByTestId("git-push-button"))

      await waitFor(() =>
        expect(screen.queryByTestId("git-ci-secrets-missing")).not.toBeInTheDocument(),
      )
      expect(mockGitPush).toHaveBeenCalledTimes(2)
    })
  })

  // Catch up and Spin off a copy rewrite the project's files from the remote: like a
  // switch, they ask first about unsaved edits in the canvas or the workbench's form.
  describe("unsaved edits before Catch up and Spin off a copy", () => {
    const behind = { status: "behind", ahead: 0, behind: 2 }
    const diverged = { status: "diverged", ahead: 1, behind: 2 }
    const rejectPushWith = (leg: typeof behind) => {
      const rejection = {
        status: "rejected_diverged",
        remote: "origin",
        working: leg,
        ledger: leg,
        message: "origin has changed - haute never force-pushes.",
        is_rewrite: false,
      }
      mockGetGitRemotes.mockResolvedValue({ remotes: [remote()], working_branch: "dev" })
      mockGitPush.mockRejectedValue(
        new ApiError("HTTP 409", 409, JSON.stringify({ detail: rejection }), { detail: rejection }),
      )
    }
    const pushToTheDialog = async () => {
      await waitFor(() => expect(screen.getByTestId("git-push-button")).toBeEnabled())
      fireEvent.click(screen.getByTestId("git-push-button"))
    }
    const CATCH_UP = "Save them before catching up? Catching up replaces the project's files with origin's."
    const SPIN_OFF =
      "Save them before spinning off a copy? Spinning off sets your version aside and replaces the project's files with origin's."

    // Each way in: how it is set up and clicked, the request it sends, its question, and
    // whether the question sits inside the rejection dialog.
    const ways = [
      {
        name: "the standalone Catch up",
        question: CATCH_UP,
        request: mockFastForward,
        inDialog: false,
        setUp: () =>
          mockGetGitRemotes.mockResolvedValue({
            remotes: [remote({ working: behind, ledger: behind })],
            working_branch: "dev",
          }),
        click: async () => {
          fireEvent.click(await screen.findByTestId("git-catch-up-button"))
        },
      },
      {
        name: "the rejection dialog's Catch up",
        question: CATCH_UP,
        request: mockFastForward,
        inDialog: true,
        setUp: () => rejectPushWith(behind),
        click: async () => {
          await pushToTheDialog()
          fireEvent.click(await screen.findByTestId("git-push-rejected-catch-up"))
        },
      },
      {
        name: "the rejection dialog's Spin off a copy",
        question: SPIN_OFF,
        request: mockBranchAway,
        inDialog: true,
        setUp: () => rejectPushWith(diverged),
        click: async () => {
          await pushToTheDialog()
          fireEvent.click(await screen.findByTestId("git-push-rejected-branch-away"))
        },
      },
    ]

    afterEach(() => {
      useGraphStore.setState({ dirty: false })
      useWorkbenchStore.setState({ formDirty: false })
      useWorkbenchFormStore.setState({ reload: useWorkbenchFormStore.getInitialState().reload })
    })

    describe.each(ways)("$name with unsaved canvas edits", ({ question, request, inDialog, setUp, click }) => {
      beforeEach(() => {
        setUp()
        useGraphStore.setState({ dirty: true })
      })

      it("asks first, naming the remote whose files replace the project's, and Cancel sends nothing", async () => {
        render(<RemotePushControl pendingSaveCount={0} onSave={vi.fn()} />)
        await click()

        const confirm = await screen.findByTestId("git-navigation-confirm")
        expect(confirm).toHaveTextContent(`You have unsaved changes. ${question}`)
        if (inDialog) {
          expect(within(screen.getByTestId("git-push-rejected")).getByTestId("git-navigation-confirm")).toBe(confirm)
        }
        fireEvent.click(screen.getByTestId("git-navigation-cancel"))
        expect(screen.queryByTestId("git-navigation-confirm")).not.toBeInTheDocument()
        expect(request).not.toHaveBeenCalled()
      })

      it("Discard sends it, then drops the canvas's and the form's edits and reads the branch again", async () => {
        const reload = vi.fn(async () => {})
        useWorkbenchFormStore.setState({ reload })
        useWorkbenchStore.setState({ formDirty: true })
        const historyNonce = useGitStore.getState().historyNonce
        render(<RemotePushControl pendingSaveCount={0} onSave={vi.fn()} />)
        await click()

        fireEvent.click(await screen.findByTestId("git-navigation-discard"))
        await waitFor(() => expect(request).toHaveBeenCalledWith("origin"))
        await waitFor(() => expect(reload).toHaveBeenCalledOnce())
        expect(useGraphStore.getState().dirty).toBe(false)
        await waitFor(() => expect(useGitStore.getState().historyNonce).toBe(historyNonce + 1))
        expect(mockGetWorkingBranch).toHaveBeenCalled()
      })

      it("Save first saves, and only then sends it", async () => {
        const onSave = vi.fn(async () => {
          expect(request).not.toHaveBeenCalled()
          return true
        })
        render(<RemotePushControl pendingSaveCount={0} onSave={onSave} />)
        await click()

        fireEvent.click(await screen.findByTestId("git-navigation-save"))
        await waitFor(() => expect(request).toHaveBeenCalledWith("origin"))
        expect(onSave).toHaveBeenCalledOnce()
      })

      it("keeps the edits when it fails", async () => {
        request.mockRejectedValueOnce(new Error("ref locked on remote"))
        render(<RemotePushControl pendingSaveCount={0} onSave={vi.fn()} />)
        await click()

        fireEvent.click(await screen.findByTestId("git-navigation-discard"))
        await waitFor(() =>
          expect(mockAddToast).toHaveBeenCalledWith("error", expect.stringContaining("ref locked on remote")),
        )
        expect(useGraphStore.getState().dirty).toBe(true)
      })
    })

    it("catches up at once, asking nothing, while the project is clean", async () => {
      ways[0].setUp()
      render(<RemotePushControl pendingSaveCount={0} onSave={vi.fn()} />)
      await ways[0].click()

      await waitFor(() => expect(mockFastForward).toHaveBeenCalledWith("origin"))
      expect(screen.queryByTestId("git-navigation-confirm")).not.toBeInTheDocument()
    })

    it("closing the rejection dialog drops the question open in it", async () => {
      rejectPushWith(behind)
      useGraphStore.setState({ dirty: true })
      render(<RemotePushControl pendingSaveCount={0} onSave={vi.fn()} />)
      await ways[1].click()
      await screen.findByTestId("git-navigation-confirm")

      fireEvent.keyDown(document, { key: "Escape" })
      await waitFor(() => expect(screen.queryByTestId("git-push-rejected")).not.toBeInTheDocument())
      expect(screen.queryByTestId("git-navigation-confirm")).not.toBeInTheDocument()
      expect(mockFastForward).not.toHaveBeenCalled()
    })

    describe("with the selection moved to another remote", () => {
      const twoRemotes = (leg: typeof behind | null) =>
        mockGetGitRemotes.mockResolvedValue({
          remotes: [
            remote({ working: leg, ledger: leg }),
            remote({ name: "backup", url: "git@example.com:y.git", working: leg, ledger: leg }),
          ],
          working_branch: "dev",
        })
      const select = async (name: string) => {
        await waitFor(() => expect(screen.getByRole("option", { name: /backup/ })).toBeInTheDocument())
        fireEvent.change(screen.getByTestId("git-push-remote"), { target: { value: name } })
      }

      it.each([
        { action: "Catch up", leg: behind, button: "git-push-rejected-catch-up", request: mockFastForward, question: CATCH_UP },
        { action: "Spin off a copy", leg: diverged, button: "git-push-rejected-branch-away", request: mockBranchAway, question: SPIN_OFF },
      ])("the rejection dialog's $action acts on the remote that refused the push", async ({ leg, button, request, question }) => {
        twoRemotes(null)
        let refuse: (err: unknown) => void = () => {}
        mockGitPush.mockReturnValueOnce(new Promise((_, reject) => { refuse = reject }))
        useGraphStore.setState({ dirty: true })
        render(<RemotePushControl pendingSaveCount={0} onSave={vi.fn()} />)
        await select("origin")
        fireEvent.click(screen.getByTestId("git-push-button"))
        await select("backup")
        const rejection = {
          status: "rejected_diverged",
          remote: "origin",
          working: leg,
          ledger: leg,
          message: "origin has changed - haute never force-pushes.",
          is_rewrite: false,
        }
        refuse(new ApiError("HTTP 409", 409, JSON.stringify({ detail: rejection }), { detail: rejection }))

        fireEvent.click(await screen.findByTestId(button))
        expect(await screen.findByTestId("git-navigation-confirm")).toHaveTextContent(question)
        fireEvent.click(screen.getByTestId("git-navigation-discard"))
        await waitFor(() => expect(request).toHaveBeenCalledWith("origin"))
        expect(request).toHaveBeenCalledOnce()
      })

      it("drops the question the standalone Catch up left open about the previous remote", async () => {
        twoRemotes(behind)
        useGraphStore.setState({ dirty: true })
        render(<RemotePushControl pendingSaveCount={0} onSave={vi.fn()} />)
        await select("origin")
        fireEvent.click(await screen.findByTestId("git-catch-up-button"))
        expect(await screen.findByTestId("git-navigation-confirm")).toHaveTextContent(CATCH_UP)

        await select("backup")
        expect(screen.queryByTestId("git-navigation-confirm")).not.toBeInTheDocument()
        expect(mockFastForward).not.toHaveBeenCalled()
      })
    })

    it("starts neither Push nor Catch up while the other is running", async () => {
      ways[0].setUp()
      let pushed: (value: unknown) => void = () => {}
      mockGitPush.mockReturnValueOnce(new Promise((resolve) => { pushed = resolve }))
      mockFastForward.mockReturnValueOnce(new Promise(() => {}))
      render(<RemotePushControl pendingSaveCount={0} />)
      await waitFor(() => expect(screen.getByTestId("git-push-button")).toBeEnabled())

      fireEvent.click(screen.getByTestId("git-push-button"))
      await waitFor(() => expect(screen.getByTestId("git-catch-up-button")).toBeDisabled())
      pushed({ remote: "origin", working_branch: "dev", ledger_branch: "dev-save", pushed_refs: [], default_branch: "main", bootstrapped_default: false })
      await waitFor(() => expect(screen.getByTestId("git-catch-up-button")).toBeEnabled())

      fireEvent.click(screen.getByTestId("git-catch-up-button"))
      await waitFor(() => expect(screen.getByTestId("git-push-button")).toBeDisabled())
    })
  })
})
