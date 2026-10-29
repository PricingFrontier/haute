import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

const mockRepairBranchPair = vi.fn()
vi.mock("../../api/client", async (importOriginal) => ({
  ...await importOriginal<typeof import("../../api/client")>(),
  repairBranchPair: (...args: unknown[]) => mockRepairBranchPair(...args),
}))

import BrokenPairs from "../BrokenPairs"
import { ApiError } from "../../api/client"
import useGitStore from "../../stores/useGitStore"
import useToastStore from "../../stores/useToastStore"
import type { GitBrokenPair } from "../../api/types"
import { makeGitWorkingBranch } from "../../test-utils/factories"

const ORPHAN: GitBrokenPair = { kind: "orphan-ledger", ledger: "pricing-save", working: "pricing" }
const HALF: GitBrokenPair = { kind: "half-archived", ledger: "old-save", working: "archive/old" }

describe("BrokenPairs", () => {
  const loadStatus = vi.fn()
  const notifyHistoryChanged = vi.fn()

  beforeEach(() => {
    mockRepairBranchPair.mockReset()
    loadStatus.mockReset().mockResolvedValue(null)
    notifyHistoryChanged.mockReset()
    useToastStore.setState({ toasts: [] })
    useGitStore.setState({
      status: makeGitWorkingBranch({ state: "ready", broken_pairs: [ORPHAN, HALF] }),
      loadStatus,
      notifyHistoryChanged,
    })
  })
  afterEach(cleanup)

  it("names each broken pair in words", () => {
    render(<BrokenPairs />)
    expect(screen.getAllByTestId("git-broken-pair").map((row) => row.textContent)).toEqual([
      "Branch 'pricing' is missing, though its saves are kept.Repair",
      "Archiving 'archive/old' stopped part way: its saves were not archived with it.Repair",
    ])
  })

  it("renders nothing when every pair is whole", () => {
    useGitStore.setState({ status: makeGitWorkingBranch({ state: "ready" }) })
    const { container } = render(<BrokenPairs />)
    expect(container.firstChild).toBeNull()
  })

  it("repairs a pair, says so, and reads readiness and history again", async () => {
    mockRepairBranchPair.mockResolvedValue({ working: "pricing", ledger: "pricing-save" })
    render(<BrokenPairs />)

    fireEvent.click(screen.getAllByTestId("git-broken-pair-repair")[0])

    await waitFor(() => expect(notifyHistoryChanged).toHaveBeenCalledTimes(1))
    expect(mockRepairBranchPair).toHaveBeenCalledWith("pricing-save")
    expect(loadStatus).toHaveBeenCalledWith({ refresh: true })
    expect(useToastStore.getState().toasts.map((t) => [t.type, t.text])).toEqual([
      ["success", "Repaired pricing"],
    ])
  })

  it("reports a refused repair with the server's reason", async () => {
    const refusal = "'pricing-save' is not part of a broken branch."
    mockRepairBranchPair.mockRejectedValue(new ApiError(refusal, 400, refusal))
    render(<BrokenPairs />)

    fireEvent.click(screen.getAllByTestId("git-broken-pair-repair")[0])

    await waitFor(() => expect(loadStatus).toHaveBeenCalledWith({ refresh: true }))
    expect(useToastStore.getState().toasts.map((t) => [t.type, t.text])).toEqual([
      ["error", "Could not repair pricing: 'pricing-save' is not part of a broken branch."],
    ])
  })
})
