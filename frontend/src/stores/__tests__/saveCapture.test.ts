/**
 * A save's version capture, reported (specs/git-integration): the ledger commit to the
 * branch indicator, the history told to refresh, each warning in a toast of its own, and
 * the identity prompt opened once per session when the capture waits on an identity.
 */
import { afterEach, beforeEach, describe, expect, it } from "vitest"
import { makeGitWorkingBranch } from "../../test-utils/factories"
import { dismissIdentityPrompt, resetIdentityPromptForTests } from "../identityPrompt"
import { reportSaveCapture } from "../saveCapture"
import useGitStore from "../useGitStore"
import useToastStore from "../useToastStore"

const toasts = () => useToastStore.getState().toasts.map((toast) => [toast.type, toast.text])

describe("reportSaveCapture", () => {
  beforeEach(() => {
    useGitStore.setState({
      status: makeGitWorkingBranch({ state: "ready", last_save_sha: "old" }),
      modal: null,
      historyNonce: 0,
    })
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
})
