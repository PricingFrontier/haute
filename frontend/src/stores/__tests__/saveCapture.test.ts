/**
 * A save's version capture, reported (specs/git-integration): the ledger commit to the
 * branch indicator, the history told to refresh, each warning in a toast of its own, the
 * files left uncaptured kept until a capture takes them, and the identity prompt opened
 * once per session when the capture waits on an identity.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { ApiError, captureFiles } from "../../api/client"
import { makeGitWorkingBranch } from "../../test-utils/factories"
import { dismissIdentityPrompt, resetIdentityPromptForTests } from "../identityPrompt"
import { captureUncaptured, reportSaveCapture } from "../saveCapture"
import useGitStore from "../useGitStore"
import useToastStore from "../useToastStore"

vi.mock("../../api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../api/client")>()),
  captureFiles: vi.fn(),
}))

const toasts = () => useToastStore.getState().toasts.map((toast) => [toast.type, toast.text])

describe("reportSaveCapture", () => {
  beforeEach(() => {
    useGitStore.setState({
      status: makeGitWorkingBranch({ state: "ready", last_save_sha: "old" }),
      modal: null,
      historyNonce: 0,
      uncaptured: [],
    })
    vi.mocked(captureFiles).mockReset()
    useToastStore.setState({ toasts: [], _toastCounter: 0 })
    resetIdentityPromptForTests()
  })

  afterEach(() => {
    resetIdentityPromptForTests()
    useGitStore.setState({ status: null, modal: null })
  })

  it("shows the ledger commit on the branch indicator and tells the history to refresh", () => {
    reportSaveCapture({ git_sha: "ledger-1", warnings: [], identity_required: false })

    expect(useGitStore.getState().status?.last_save_sha).toBe("ledger-1")
    expect(useGitStore.getState().historyNonce).toBe(1)
    expect(useGitStore.getState().modal).toBeNull()
    expect(toasts()).toEqual([])

    // A save without a working branch has no commit; an answer without the field leaves
    // the indicator as it is.
    reportSaveCapture({ git_sha: null })
    expect(useGitStore.getState().status?.last_save_sha).toBeNull()
    reportSaveCapture({})
    expect(useGitStore.getState().status?.last_save_sha).toBeNull()
    expect(useGitStore.getState().historyNonce).toBe(3)
  })

  it("toasts each warning on its own", () => {
    reportSaveCapture({
      git_sha: null,
      warnings: ["Changes saved; version capture failed: no such branch", "A transform has no code"],
    })

    expect(toasts()).toEqual([
      ["warning", "Changes saved; version capture failed: no such branch"],
      ["warning", "A transform has no code"],
    ])
  })

  it("asks for a git identity when the capture waits on one, once per session", () => {
    reportSaveCapture({ git_sha: null, identity_required: true })
    expect(useGitStore.getState().modal).toBe("identity")

    // Already asking: left as it is.
    reportSaveCapture({ git_sha: null, identity_required: true })
    expect(useGitStore.getState().modal).toBe("identity")

    // Waved away: the warning keeps saying so, the prompt does not come back.
    useGitStore.setState({ modal: null })
    dismissIdentityPrompt()
    reportSaveCapture({ git_sha: null, identity_required: true, warnings: ["needs a git identity"] })
    expect(useGitStore.getState().modal).toBeNull()
    expect(toasts()).toEqual([["warning", "needs a git identity"]])
  })

  it("keeps every file a capture skipped, each once, until a capture takes them", async () => {
    reportSaveCapture({ identity_required: true, uncaptured: ["rating/workbench/form.json"] })
    reportSaveCapture({ identity_required: true, uncaptured: ["rating/files/data/a.csv", "rating/workbench/form.json"] })
    expect(useGitStore.getState().uncaptured).toEqual(["rating/workbench/form.json", "rating/files/data/a.csv"])

    // The identity is set now: one capture takes both, and nothing is kept.
    vi.mocked(captureFiles).mockResolvedValue({ git_sha: "ledger-2", warnings: [], identity_required: false, uncaptured: [] })
    await captureUncaptured()

    expect(captureFiles).toHaveBeenCalledWith(["rating/workbench/form.json", "rating/files/data/a.csv"])
    expect(useGitStore.getState().uncaptured).toEqual([])
    expect(useGitStore.getState().status?.last_save_sha).toBe("ledger-2")

    // Nothing kept, nothing sent.
    await captureUncaptured()
    expect(captureFiles).toHaveBeenCalledTimes(1)
  })

  it("keeps what a capture still skips, and everything when the request fails", async () => {
    reportSaveCapture({ uncaptured: ["rating/workbench/form.json", "rating/pipeline.py"] })
    vi.mocked(captureFiles).mockResolvedValueOnce({
      git_sha: null,
      warnings: ["Changes saved; version capture failed: busy"],
      identity_required: false,
      uncaptured: ["rating/workbench/form.json", "rating/pipeline.py"],
    })
    await captureUncaptured()
    expect(useGitStore.getState().uncaptured).toEqual(["rating/workbench/form.json", "rating/pipeline.py"])

    vi.mocked(captureFiles).mockRejectedValueOnce(new ApiError("HTTP 500", 500, "the server stopped", null, null))
    await captureUncaptured()

    expect(useGitStore.getState().uncaptured).toEqual(["rating/workbench/form.json", "rating/pipeline.py"])
    expect(toasts()).toEqual([
      ["warning", "Changes saved; version capture failed: busy"],
      ["error", "Version capture failed: the server stopped"],
    ])
  })

  it("keeps a file a write reports while the capture is under way", async () => {
    reportSaveCapture({ uncaptured: ["rating/workbench/form.json"] })
    let answer: (value: Awaited<ReturnType<typeof captureFiles>>) => void = () => undefined
    vi.mocked(captureFiles).mockReturnValueOnce(new Promise((resolve) => { answer = resolve }))

    const capturing = captureUncaptured()
    reportSaveCapture({ uncaptured: ["rating/files/data/b.csv"] })
    answer({ git_sha: "ledger-3", warnings: [], identity_required: false, uncaptured: [] })
    await capturing

    expect(useGitStore.getState().uncaptured).toEqual(["rating/files/data/b.csv"])
  })
})
