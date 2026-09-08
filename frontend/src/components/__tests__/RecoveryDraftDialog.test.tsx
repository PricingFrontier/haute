import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import RecoveryDraftDialog from "../RecoveryDraftDialog"
import { makePipelineEditorDocument } from "../../testSupport/pipelineDocumentFixture"
import useGraphStore from "../../stores/useGraphStore"

const {
  listRecoveryDrafts,
  createRecoveryDraft,
  editRecoveryDraft,
  previewRecoveryDraft,
  applyRecoveryDraft,
  restorePreviewRecoveryDraft,
  restoreRecoveryDraft,
} = vi.hoisted(() => ({
  listRecoveryDrafts: vi.fn(),
  createRecoveryDraft: vi.fn(),
  editRecoveryDraft: vi.fn(),
  previewRecoveryDraft: vi.fn(),
  applyRecoveryDraft: vi.fn(),
  restorePreviewRecoveryDraft: vi.fn(),
  restoreRecoveryDraft: vi.fn(),
}))
const { loadPipeline } = vi.hoisted(() => ({ loadPipeline: vi.fn() }))
const {
  estimateTrainingRam,
  estimateOptimiserSolve,
  fetchIoCapabilities,
  outputAssembleDryRun,
  previewNode,
  resolveOutputDestination,
  trainModel,
  solveOptimiser,
  writeOutput,
} = vi.hoisted(() => ({
  estimateTrainingRam: vi.fn(),
  estimateOptimiserSolve: vi.fn(),
  fetchIoCapabilities: vi.fn().mockResolvedValue({ groups: [] }),
  outputAssembleDryRun: vi.fn(),
  previewNode: vi.fn(),
  resolveOutputDestination: vi.fn(),
  trainModel: vi.fn(),
  solveOptimiser: vi.fn(),
  writeOutput: vi.fn(),
}))
const { runDispersionEstimate } = vi.hoisted(() => ({
  runDispersionEstimate: vi.fn(),
}))

vi.mock("../../api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../api/client")>()),
  loadPipeline,
  estimateTrainingRam,
  estimateOptimiserSolve,
  fetchIoCapabilities,
  outputAssembleDryRun,
  previewNode,
  resolveOutputDestination,
  trainModel,
  solveOptimiser,
  writeOutput,
}))

vi.mock("../../api/dispersion", () => ({ runDispersionEstimate }))

vi.mock("../../api/recoveryDrafts", () => ({
  listRecoveryDrafts,
  createRecoveryDraft,
  editRecoveryDraft,
  previewRecoveryDraft,
  applyRecoveryDraft,
  discardRecoveryDraft: vi.fn(),
  restorePreviewRecoveryDraft,
  restoreRecoveryDraft,
}))

const draft = (overrides: Record<string, unknown> = {}) => ({
  draft_id: "draft-1",
  draft_revision: "revision-1",
  source_file: "main.py",
  source_revision: "source-1",
  contract_fingerprint: "contract",
  mode: "recover",
  state: "needs_configuration",
  updated_at: "now",
  reviewed: false,
  nodes: [
    {
      key: "node:1",
      source_file: "main.py",
      recovery_id: "node@1",
      authored_id: "node",
      label: "Broken node",
      node_type: null,
      config: { retained: true },
      changes: [{ path: "retained", outcome: "retained", reason: "valid" }],
      issues: [],
      editable: true,
    },
  ],
  issues: [],
  ...overrides,
})

describe("RecoveryDraftDialog", () => {
  beforeEach(() => {
    loadPipeline.mockResolvedValue(
      makePipelineEditorDocument({ source_file: "main.py", source_revision: "source-1" }),
    )
  })
  afterEach(() => {
    cleanup()
    vi.clearAllMocks()
    vi.restoreAllMocks()
    useGraphStore.getState().resetForTests()
  })

  it("resumes a matching saved draft and saves an explicit full config map", async () => {
    listRecoveryDrafts.mockResolvedValue({ drafts: [draft()] })
    editRecoveryDraft.mockResolvedValue(draft({ draft_revision: "revision-2", reviewed: true }))
    render(
      <RecoveryDraftDialog
        sourceFile="main.py"
        sourceRevision="source-1"
        target={{ sourceFile: "main.py", recoveryId: "node@1" }}
        onClose={vi.fn()}
        onApplied={vi.fn()}
      />,
    )
    await screen.findByRole("button", { name: "Broken node" })
    expect(createRecoveryDraft).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole("checkbox"))
    fireEvent.click(screen.getByRole("button", { name: "Save draft" }))
    await waitFor(() =>
      expect(editRecoveryDraft).toHaveBeenCalledWith("draft-1", {
        draft_revision: "revision-1",
        configs: { "node:1": { retained: true } },
        reviewed: true,
      }),
    )
  })

  it.each([
    {
      nodeType: "output",
      config: { outputMapping: [] },
      inputNames: ["quoted_frame"],
      control: () => screen.getByText("Frames (1)"),
    },
    {
      nodeType: "dataOutput",
      config: {},
      inputNames: ["quoted_frame"],
      control: () => screen.getByLabelText("Provider"),
    },
    {
      nodeType: "edgeJoin",
      config: { how: "cross", on: [], leftOn: [], rightOn: [] },
      inputNames: ["base_frame", "join_frame"],
      control: () => screen.getByText("Dominant Input"),
    },
    {
      nodeType: "modelling",
      config: { algorithm: "glm" },
      inputNames: ["training_frame"],
      control: () => screen.getByLabelText("Selected algorithm"),
    },
    {
      nodeType: "optimiser",
      config: { objective: "objective" },
      inputNames: ["optimiser_frame"],
      control: () => screen.getByText("Mode"),
    },
  ])("mounts the real $nodeType editor in an isolated draft graph", async ({ nodeType, config, inputNames, control }) => {
    listRecoveryDrafts.mockResolvedValue({
      drafts: [
        draft({
          nodes: [{
            ...draft().nodes[0],
            node_type: nodeType,
            config,
            input_names: inputNames,
          }],
        }),
      ],
    })
    render(
      <RecoveryDraftDialog
        sourceFile="main.py"
        sourceRevision="source-1"
        target={{ sourceFile: "main.py", recoveryId: "node@1" }}
        onClose={vi.fn()}
        onApplied={vi.fn()}
      />,
    )
    await screen.findByRole("button", { name: "Broken node" })
    expect(await waitFor(() => control())).toBeTruthy()
    expect(screen.queryByText("The normal editor failed. Use Advanced draft JSON to repair this proposal.")).toBeNull()
    expect(estimateTrainingRam).not.toHaveBeenCalled()
    expect(estimateOptimiserSolve).not.toHaveBeenCalled()
    expect(outputAssembleDryRun).not.toHaveBeenCalled()
    expect(previewNode).not.toHaveBeenCalled()
    expect(writeOutput).not.toHaveBeenCalled()
    expect(trainModel).not.toHaveBeenCalled()
    expect(solveOptimiser).not.toHaveBeenCalled()
    expect(runDispersionEstimate).not.toHaveBeenCalled()
  })

  it("keeps output input frames and draft edits out of the live graph store", async () => {
    const liveNodes = [{
      id: "live-node",
      type: "pipelineNode",
      position: { x: 0, y: 0 },
      data: { label: "Live node", description: "live", nodeType: "polars", config: { retained: true } },
    }]
    const liveEdges = [{ id: "live-edge", source: "live-node", target: "live-target" }]
    const liveHistory = [{ nodes: liveNodes, edges: liveEdges, preamble: "live", submodels: {} }]
    useGraphStore.setState({ nodes: liveNodes, edges: liveEdges, undoStack: liveHistory })
    editRecoveryDraft.mockResolvedValue(draft({ draft_revision: "revision-2", reviewed: true }))
    listRecoveryDrafts.mockResolvedValue({
      drafts: [
        draft({
          nodes: [{
            ...draft().nodes[0],
            node_type: "output",
            config: { outputMapping: [] },
            input_names: ["quoted_frame"],
          }],
        }),
      ],
    })
    render(
      <RecoveryDraftDialog
        sourceFile="main.py"
        sourceRevision="source-1"
        target={{ sourceFile: "main.py", recoveryId: "node@1" }}
        onClose={vi.fn()}
        onApplied={vi.fn()}
      />,
    )
    await screen.findByText("Frames (1)")
    expect(screen.getByText("quoted_frame")).toBeTruthy()
    fireEvent.change(screen.getByTestId("output-format-select"), { target: { value: "json" } })
    fireEvent.click(screen.getByRole("checkbox", { name: "I reviewed the settings and all affected owners." }))
    fireEvent.click(screen.getByRole("button", { name: "Save draft" }))
    await waitFor(() =>
      expect(editRecoveryDraft).toHaveBeenCalledWith(
        "draft-1",
        expect.objectContaining({
          configs: expect.objectContaining({
            "node:1": expect.objectContaining({ outputFormat: "json" }),
          }),
        }),
      ),
    )
    expect(useGraphStore.getState().nodes).toBe(liveNodes)
    expect(useGraphStore.getState().edges).toBe(liveEdges)
    expect(useGraphStore.getState().undoStack).toBe(liveHistory)
  })

  it("hides GLM dispersion estimation while preserving the recovery editor", async () => {
    listRecoveryDrafts.mockResolvedValue({
      drafts: [
        draft({
          nodes: [{
            ...draft().nodes[0],
            node_type: "modelling",
            config: { algorithm: "glm", family: "tweedie" },
            input_names: ["training_frame"],
          }],
        }),
      ],
    })
    render(
      <RecoveryDraftDialog
        sourceFile="main.py"
        sourceRevision="source-1"
        target={{ sourceFile: "main.py", recoveryId: "node@1" }}
        onClose={vi.fn()}
        onApplied={vi.fn()}
      />,
    )
    expect(await screen.findByText("Set variance power (required for Tweedie)")).toBeTruthy()
    expect(screen.queryByRole("button", { name: "Estimate from data" })).toBeNull()
    expect(runDispersionEstimate).not.toHaveBeenCalled()
  })

  it("resolves Edge Join draft input names in recorded base and join order", async () => {
    listRecoveryDrafts.mockResolvedValue({
      drafts: [
        draft({
          nodes: [{
            ...draft().nodes[0],
            node_type: "edgeJoin",
            config: { how: "cross", on: [], leftOn: [], rightOn: [] },
            input_names: ["base_frame", "join_frame"],
          }],
        }),
      ],
    })
    render(
      <RecoveryDraftDialog
        sourceFile="main.py"
        sourceRevision="source-1"
        target={{ sourceFile: "main.py", recoveryId: "node@1" }}
        onClose={vi.fn()}
        onApplied={vi.fn()}
      />,
    )
    expect(await screen.findByText("base_frame")).toBeTruthy()
    expect(screen.getByText("join_frame")).toBeTruthy()
    expect(screen.getByText("Dominant Input").parentElement).toHaveTextContent("base_frame")
    expect(screen.getByText("Joining Input").parentElement).toHaveTextContent("join_frame")
  })

  it("requires a fresh displayed preview and acknowledgement before apply", async () => {
    listRecoveryDrafts.mockResolvedValue({
      drafts: [draft({ state: "ready_to_apply", reviewed: true })],
    })
    previewRecoveryDraft.mockResolvedValue({
      draft: draft({ state: "ready_to_apply", reviewed: true }),
      plan_hash: "a".repeat(64),
      changes: [],
      issues: [],
      predicted_load_status: "ready",
    })
    render(
      <RecoveryDraftDialog
        sourceFile="main.py"
        sourceRevision="source-1"
        target={{ sourceFile: "main.py", recoveryId: "node@1" }}
        onClose={vi.fn()}
        onApplied={vi.fn()}
      />,
    )
    await screen.findByRole("button", { name: "Broken node" })
    expect(screen.getByRole("button", { name: "Apply" })).toBeDisabled()
    fireEvent.click(screen.getByRole("checkbox"))
    expect(screen.getByRole("button", { name: "Apply" })).toBeDisabled()
  })

  it("preserves unsaved canvas work instead of replacing it on apply", async () => {
    const ready = draft({ state: "ready_to_apply", reviewed: true })
    listRecoveryDrafts.mockResolvedValue({ drafts: [ready] })
    previewRecoveryDraft.mockResolvedValue({
      draft: ready,
      plan_hash: "a".repeat(64),
      changes: [],
      issues: [],
    })
    render(
      <RecoveryDraftDialog
        sourceFile="main.py"
        sourceRevision="source-1"
        target={{ sourceFile: "main.py", recoveryId: "node@1" }}
        onClose={vi.fn()}
        onApplied={vi.fn()}
      />,
    )
    await screen.findByRole("button", { name: "Broken node" })
    fireEvent.click(screen.getByRole("button", { name: "Review diff" }))
    await waitFor(() => expect(screen.getByRole("button", { name: "Apply" })).toBeEnabled())
    vi.spyOn(useGraphStore.getState(), "isDirty").mockReturnValue(true)
    fireEvent.click(screen.getByRole("button", { name: "Apply" }))
    expect(await screen.findByRole("alert")).toHaveTextContent("unsaved pipeline changes")
    expect(applyRecoveryDraft).not.toHaveBeenCalled()
  })

  it("restores an applied history record only after its restore preview", async () => {
    const applied = draft({ state: "applied", reviewed: true })
    listRecoveryDrafts.mockResolvedValue({ drafts: [applied] })
    restorePreviewRecoveryDraft.mockResolvedValue({
      draft: applied,
      plan_hash: "b".repeat(64),
      changes: [],
      issues: [],
      predicted_load_status: "ready",
    })
    restoreRecoveryDraft.mockResolvedValue({ draft: applied, applied_artifacts: [], document: {} })
    render(
      <RecoveryDraftDialog
        sourceFile="main.py"
        sourceRevision="current-source"
        target={{ sourceFile: "main.py", recoveryId: "node@1" }}
        onClose={vi.fn()}
        onApplied={vi.fn()}
      />,
    )
    await screen.findByRole("button", { name: "Broken node" })
    fireEvent.click(screen.getByRole("button", { name: "Review restore" }))
    await waitFor(() =>
      expect(restorePreviewRecoveryDraft).toHaveBeenCalledWith("draft-1", "revision-1"),
    )
    fireEvent.click(screen.getByRole("button", { name: "Restore" }))
    await waitFor(() =>
      expect(restoreRecoveryDraft).toHaveBeenCalledWith(
        "draft-1",
        expect.objectContaining({
          draft_revision: "revision-1",
          source_revision: "current-source",
          plan_hash: "b".repeat(64),
        }),
      ),
    )
  })

  it("uses source-qualified grouped targets when recovery ids repeat", async () => {
    listRecoveryDrafts.mockResolvedValue({
      drafts: [draft({ nodes: [{ ...draft().nodes[0], recovery_id: "same" }] })],
    })
    createRecoveryDraft.mockResolvedValue(
      draft({ nodes: [{ ...draft().nodes[0], recovery_id: "same" }] }),
    )
    // Recovery ids are unique within each graph, but a nested submodel graph
    // may legitimately contain the same id as its containing source graph.
    loadPipeline.mockResolvedValue(
      makePipelineEditorDocument({
        source_file: "main.py",
        source_revision: "source-1",
        nodes: [
          {
            id: "same",
            type: "pipelineNode",
            position: { x: 0, y: 0 },
            data: {
              label: "Root",
              nodeType: "polars",
              _authoredId: "root",
              _sourceFile: "main.py",
              _loadAvailability: "blocked",
            },
          },
        ],
        submodels: {
          child: {
            definitionId: "child",
            file: "modules/child.py",
            graph: {
              nodes: [
                {
                  id: "same",
                  type: "pipelineNode",
                  position: { x: 1, y: 0 },
                  data: {
                    label: "Child",
                    nodeType: "polars",
                    _authoredId: "child",
                    _sourceFile: "modules/child.py",
                    _loadAvailability: "unavailable",
                  },
                },
              ],
              edges: [],
              submodels: null,
            },
            inputPorts: [],
            outputPorts: [],
          },
        },
      }),
    )
    render(
      <RecoveryDraftDialog
        sourceFile="main.py"
        sourceRevision="source-1"
        target={{ sourceFile: "main.py", recoveryId: "same" }}
        onClose={vi.fn()}
        onApplied={vi.fn()}
      />,
    )
    await screen.findByText("Add explicit recovery targets")
    const root = screen.getByRole("checkbox", { name: /Root \(blocked\)/ })
    const child = screen.getByRole("checkbox", { name: /Child \(unavailable\)/ })
    expect(root).toBeChecked()
    expect(child).not.toBeChecked()
    fireEvent.click(child)
    fireEvent.click(screen.getByRole("button", { name: "Create grouped draft" }))
    await waitFor(() =>
      expect(createRecoveryDraft).toHaveBeenCalledWith(
        expect.objectContaining({
          targets: [
            { source_file: "main.py", recovery_id: "same" },
            { source_file: "modules/child.py", recovery_id: "same" },
          ],
        }),
      ),
    )
    await screen.findByRole("button", { name: "Create grouped draft" })

    fireEvent.click(child)
    fireEvent.click(screen.getByRole("button", { name: "Create grouped draft" }))
    await waitFor(() =>
      expect(createRecoveryDraft).toHaveBeenLastCalledWith(
        expect.objectContaining({ targets: [{ source_file: "main.py", recovery_id: "same" }] }),
      ),
    )
  })

  it("keeps the last draft configuration when advanced JSON is invalid", async () => {
    listRecoveryDrafts.mockResolvedValue({ drafts: [draft()] })
    loadPipeline.mockResolvedValue(
      makePipelineEditorDocument({ source_file: "main.py", source_revision: "source-1" }),
    )
    render(
      <RecoveryDraftDialog
        sourceFile="main.py"
        sourceRevision="source-1"
        target={{ sourceFile: "main.py", recoveryId: "node@1" }}
        onClose={vi.fn()}
        onApplied={vi.fn()}
      />,
    )
    await screen.findByRole("button", { name: "Broken node" })
    fireEvent.click(screen.getByText("Advanced draft JSON"))
    const textarea = screen.getByRole("textbox", { name: "Advanced draft JSON" })
    fireEvent.change(textarea, { target: { value: "{" } })
    fireEvent.blur(textarea)
    expect(await screen.findByRole("alert")).toHaveTextContent("Advanced JSON was not applied")
    expect(screen.getByRole("button", { name: "Save draft" })).toBeDisabled()
    expect(editRecoveryDraft).not.toHaveBeenCalled()
    fireEvent.click(screen.getByText("Advanced draft JSON"))
    expect(screen.getByRole("button", { name: "Broken node" })).toBeInTheDocument()
  })

  it("starts a fresh reset from applied history without remaining in restore mode", async () => {
    listRecoveryDrafts.mockResolvedValue({ drafts: [draft({ state: "applied", reviewed: true })] })
    createRecoveryDraft.mockResolvedValue(draft({ draft_id: "reset-1", mode: "reset" }))
    render(
      <RecoveryDraftDialog
        sourceFile="main.py"
        sourceRevision="source-1"
        target={{ sourceFile: "main.py", recoveryId: "node@1" }}
        onClose={vi.fn()}
        onApplied={vi.fn()}
      />,
    )
    await screen.findByRole("button", { name: "Review restore" })
    expect(screen.getByRole("button", { name: "Discard draft" })).toBeDisabled()
    fireEvent.click(screen.getByRole("button", { name: "Reset all settings and code" }))
    await screen.findByRole("button", { name: "Review diff" })
    expect(screen.getByRole("button", { name: "Apply" })).toBeDisabled()
  })

  it("does not replace unsaved draft settings when reset is selected", async () => {
    listRecoveryDrafts.mockResolvedValue({ drafts: [draft()] })
    render(
      <RecoveryDraftDialog
        sourceFile="main.py"
        sourceRevision="source-1"
        target={{ sourceFile: "main.py", recoveryId: "node@1" }}
        onClose={vi.fn()}
        onApplied={vi.fn()}
      />,
    )
    await screen.findByRole("button", { name: "Broken node" })
    fireEvent.click(screen.getByRole("checkbox"))
    expect(screen.getByRole("button", { name: "Reset all settings and code" })).toBeDisabled()
  })

  it("rejects advanced JSON that changes the node map shape", async () => {
    listRecoveryDrafts.mockResolvedValue({ drafts: [draft()] })
    render(
      <RecoveryDraftDialog
        sourceFile="main.py"
        sourceRevision="source-1"
        target={{ sourceFile: "main.py", recoveryId: "node@1" }}
        onClose={vi.fn()}
        onApplied={vi.fn()}
      />,
    )
    await screen.findByRole("button", { name: "Broken node" })
    fireEvent.click(screen.getByText("Advanced draft JSON"))
    const textarea = screen.getByRole("textbox", { name: "Advanced draft JSON" })
    fireEvent.change(textarea, { target: { value: '{"node:1":null}' } })
    fireEvent.blur(textarea)
    expect(await screen.findByRole("alert")).toHaveTextContent("Advanced JSON was not applied")
    expect(screen.getByRole("button", { name: "Save draft" })).toBeDisabled()
  })
})
