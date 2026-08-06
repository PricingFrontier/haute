/**
 * The git-identity save prompt (restored hosted container has no commit
 * identity, so saves land on disk but are never version-captured).
 *
 * Covers the whole loop: the save response opens the modal, dismissal
 * suppresses it for the rest of the session, and a submitted identity posts to
 * the identity endpoint and re-triggers the save that was left uncaptured.
 */
import { cleanup, fireEvent, render, screen, waitFor, renderHook, act } from "@testing-library/react"
import type { Edge, Node } from "@xyflow/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

vi.mock("../../api/client", () => ({
  loadPipeline: vi.fn(() => Promise.resolve({ nodes: [], edges: [] })),
  previewNode: vi.fn(),
  savePipeline: vi.fn(),
  setGitIdentity: vi.fn(() => Promise.resolve({ scope: "local" })),
  getWorkingBranch: vi.fn(() => Promise.resolve(null)),
  ApiError: class ApiError extends Error {
    status = 0
    detail?: string
  },
}))

vi.mock("../../utils/buildGraph", () => ({
  resolveGraphFromRefs: vi.fn(() => ({ nodes: [], edges: [], preamble: "" })),
}))

import { savePipeline, setGitIdentity } from "../../api/client"
import usePipelineAPI from "../../hooks/usePipelineAPI"
import { resetIdentityPromptForTests } from "../../stores/identityPrompt"
import useGitStore from "../../stores/useGitStore"
import useToastStore from "../../stores/useToastStore"
import IdentityPromptModal from "../IdentityPromptModal"

const mockSave = vi.mocked(savePipeline)
const mockSetIdentity = vi.mocked(setGitIdentity)

function makeParams() {
  return {
    selectedNode: null as Node | null,
    graphRef: { current: { nodes: [] as Node[], edges: [] as Edge[] } },
    parentGraphRef: { current: null },
    submodelsRef: { current: {} },
    setNodes: vi.fn(),
    setNodesRaw: vi.fn(),
    setEdgesRaw: vi.fn(),
    setPreamble: vi.fn(),
    preambleRef: { current: "" },
    pipelineNameRef: { current: "test" },
    descriptionRef: { current: "" },
    sourceFileRef: { current: "test.py" },
    nodeIdCounter: { current: 0 },
    activeSubmodelIdentity: null,
    sourceRevisionRef: { current: "revision-test" },
    preservedBlocksRef: { current: [] as string[] },
  }
}

const IDENTITY_WARNING =
  "Changes saved, but version capture needs a git identity. " +
  "Set your name and email to keep version history."

async function saveOnce(identityRequired: boolean) {
  mockSave.mockResolvedValue({
    file: "test.py",
    pipeline_name: "test",
    warnings: identityRequired ? [IDENTITY_WARNING] : [],
    git_sha: null,
    source_revision: "revision-test",
    identity_required: identityRequired,
  })
  const { result } = renderHook(() => usePipelineAPI(makeParams()))
  await waitFor(() => expect(result.current.loading).toBe(false))
  await act(async () => {
    await result.current.handleSave()
  })
}

describe("git identity save prompt", () => {
  beforeEach(() => {
    resetIdentityPromptForTests()
    useGitStore.setState({ modal: null, pendingAction: null })
    useToastStore.setState({ toasts: [], _toastCounter: 0 })
    mockSave.mockReset()
    mockSetIdentity.mockClear()
  })

  afterEach(() => {
    cleanup()
  })

  it("opens on a save response that reports identity_required", async () => {
    await saveOnce(true)
    expect(useGitStore.getState().modal).toBe("identity")
    expect(useToastStore.getState().toasts.some((t) => t.text === IDENTITY_WARNING)).toBe(true)
  })

  it("stays shut when the save captured normally", async () => {
    await saveOnce(false)
    expect(useGitStore.getState().modal).toBeNull()
  })

  it("does not reopen for the rest of the session once dismissed", async () => {
    await saveOnce(true)
    render(<IdentityPromptModal onSaved={vi.fn()} onClose={() => useGitStore.setState({ modal: null })} />)
    fireEvent.click(screen.getByText("Not now"))
    expect(useGitStore.getState().modal).toBeNull()

    // The warning still surfaces on every later save; the modal does not.
    useToastStore.setState({ toasts: [], _toastCounter: 0 })
    await saveOnce(true)
    expect(useGitStore.getState().modal).toBeNull()
    expect(useToastStore.getState().toasts.some((t) => t.text === IDENTITY_WARNING)).toBe(true)
  })

  it("submits the identity and retries the save", async () => {
    const onSaved = vi.fn()
    const onClose = vi.fn()
    render(<IdentityPromptModal onSaved={onSaved} onClose={onClose} />)

    fireEvent.change(screen.getByTestId("identity-prompt-name"), {
      target: { value: "Restored User" },
    })
    fireEvent.change(screen.getByTestId("identity-prompt-email"), {
      target: { value: "restored@example.com" },
    })
    fireEvent.click(screen.getByTestId("identity-prompt-confirm"))

    await waitFor(() => expect(mockSetIdentity).toHaveBeenCalledTimes(1))
    expect(mockSetIdentity).toHaveBeenCalledWith("Restored User", "restored@example.com", false)
    await waitFor(() => expect(onSaved).toHaveBeenCalledTimes(1))
    expect(onClose).toHaveBeenCalledTimes(1)
  })
})
