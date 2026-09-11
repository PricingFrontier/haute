import { act, renderHook, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import type { Mock } from "vitest"
import type { Node } from "@xyflow/react"
import useDocumentStatusStore from "../../stores/useDocumentStatusStore"
import { makePipelineEditorDocument } from "../../testSupport/pipelineDocumentFixture"
import type { OnUpdateConfigResult } from "../../panels/editors"
import type { HauteNodeData } from "../../types/node"
import type { PipelineEditorDocument, RecoveryNode } from "../../types/pipelineDocument"
import { useScopedNodeSave } from "../useScopedNodeSave"

const { saveNodeScoped } = vi.hoisted(() => ({ saveNodeScoped: vi.fn() }))
vi.mock("../../api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../api/client")>()),
  saveNodeScoped,
}))

const NODE_LABELS: Record<string, string> = { "a@1": "A", "b@1": "B" }

function recoveryNode(id: string, config: Record<string, unknown>): RecoveryNode {
  return {
    recovery_id: id,
    authored_id: id.split("@")[0],
    label: NODE_LABELS[id],
    decorator_name: "polars",
    node_type: "polars",
    description: "",
    availability: "ready",
    display_position: { x: 0, y: 0 },
    config: structuredClone(config) as RecoveryNode["config"],
    config_reference: null,
    function_name: NODE_LABELS[id],
    default_input_name: NODE_LABELS[id],
    source_handle_input_names: {},
    source_file: "main.py",
    source_span: null,
    diagnostic_ids: [],
    blocking_path: [],
    scoped_editable: true,
  }
}

describe("useScopedNodeSave", () => {
  // The simulated backend's persisted per-node configurations: saves write
  // here, and every authoritative document is built from this state alone.
  let serverConfigs: Map<string, Record<string, unknown>>
  let revisionCounter: number
  let graphRef: { current: { nodes: Node[]; edges: never[] } }
  let onUpdateNode: Mock<(id: string, data: Record<string, unknown>) => OnUpdateConfigResult>
  let applyDocument: Mock<(document: PipelineEditorDocument, savedNodeId: string) => void>

  function serverDocument(revision: string): PipelineEditorDocument {
    return makePipelineEditorDocument({
      source_revision: revision,
      recoveryNodes: [...serverConfigs.entries()].map(([id, config]) => recoveryNode(id, config)),
    })
  }

  function canvasFromDocument(document: PipelineEditorDocument): Node[] {
    return document.nodes.map(
      (node) =>
        ({
          id: node.recovery_id,
          position: { ...node.display_position },
          data: {
            label: node.label,
            nodeType: node.node_type,
            config: structuredClone(node.config ?? {}),
            _sourceFile: node.source_file,
            _recoveryId: node.recovery_id,
            _scopedEditable: node.scoped_editable,
          },
        }) as unknown as Node,
    )
  }

  beforeEach(() => {
    vi.clearAllMocks()
    revisionCounter = 1
    serverConfigs = new Map([
      ["a@1", { code: "authored-a" }],
      ["b@1", { code: "authored-b" }],
    ])
    const initial = serverDocument("rev-1")
    graphRef = { current: { nodes: canvasFromDocument(initial), edges: [] } }
    // Accepted edits mutate the live graph, exactly as the commit controller
    // does — the hook must send the edited values, not the authored ones.
    onUpdateNode = vi.fn((id: string, data: Record<string, unknown>) => {
      graphRef.current.nodes = graphRef.current.nodes.map((node) =>
        node.id === id ? ({ ...node, data } as Node) : node,
      )
      return { ok: true as const }
    })
    // The backend persists the submitted configuration and returns a full
    // authoritative document built from its own state.
    saveNodeScoped.mockImplementation(
      async (args: { targetRecoveryId: string; config: Record<string, unknown> }) => {
        serverConfigs.set(args.targetRecoveryId, structuredClone(args.config))
        revisionCounter += 1
        return serverDocument(`rev-${revisionCounter}`)
      },
    )
    // Adoption replaces the whole canvas from the response's nodes — if the
    // response omitted or reset another node, the canvas now shows it.
    applyDocument = vi.fn((document: PipelineEditorDocument) => {
      graphRef.current.nodes = canvasFromDocument(document)
      useDocumentStatusStore.getState().loadDocumentStatus(document)
    })
    act(() => {
      useDocumentStatusStore.getState().loadDocumentStatus(initial)
    })
  })

  afterEach(() => {
    act(() => {
      useDocumentStatusStore.getState().reset()
    })
  })

  function render(selectedNodeId: string) {
    return renderHook(
      (props: { selectedNodeId: string }) =>
        useScopedNodeSave({
          scopedEditingActive: true,
          selectedNodeId: props.selectedNodeId,
          graphRef,
          onUpdateNode,
          applyDocument,
        }),
      { initialProps: { selectedNodeId } },
    )
  }

  function nodeConfig(id: string): Record<string, unknown> {
    const node = graphRef.current.nodes.find((item) => item.id === id)
    return ((node?.data as HauteNodeData)?.config ?? {}) as Record<string, unknown>
  }

  function editedData(id: string, code: string): Record<string, unknown> {
    const node = graphRef.current.nodes.find((item) => item.id === id)
    return { ...(node?.data as HauteNodeData), config: { code } }
  }

  it("blocks edits to any node while a save is in flight, across panel lifecycles", async () => {
    let release!: (value: unknown) => void
    saveNodeScoped.mockImplementationOnce(() => new Promise((resolve) => { release = resolve }))
    const hook = render("a@1")

    expect(hook.result.current.handlePanelUpdateNode("a@1", editedData("a@1", "a-edit")).ok).toBe(
      true,
    )
    let pending!: Promise<{ ok: boolean; error?: string }>
    act(() => {
      pending = hook.result.current.handleScopedSave()
    })

    // The user closes A's panel and opens B before the response returns: the
    // guard lives in the App-lifetime hook, not the unmounted panel.
    hook.rerender({ selectedNodeId: "b@1" })
    const blocked = hook.result.current.handlePanelUpdateNode("b@1", editedData("b@1", "b-edit"))
    expect(blocked.ok).toBe(false)
    expect(blocked).toMatchObject({ error: expect.stringContaining("in flight") })
    expect(nodeConfig("b@1")).toEqual({ code: "authored-b" })

    await act(async () => {
      release(serverDocument("rev-2"))
      await pending
    })
    expect(hook.result.current.handlePanelUpdateNode("b@1", editedData("b@1", "b-edit")).ok).toBe(
      true,
    )
  })

  it("completes the two-node sequence saving each node's edited values", async () => {
    const hook = render("a@1")

    expect(hook.result.current.handlePanelUpdateNode("a@1", editedData("a@1", "a-edit-1")).ok).toBe(
      true,
    )
    // The deadlock state is unreachable: a second node cannot start editing
    // while the first holds unsaved scoped edits — and its value is untouched.
    const refused = hook.result.current.handlePanelUpdateNode("b@1", editedData("b@1", "b-edit-2"))
    expect(refused.ok).toBe(false)
    expect(refused).toMatchObject({ error: expect.stringContaining("Save A") })
    expect(nodeConfig("b@1")).toEqual({ code: "authored-b" })

    await act(async () => {
      await expect(hook.result.current.handleScopedSave()).resolves.toEqual({ ok: true })
    })
    // The request carried A's edited value; the canvas was rebuilt from the
    // authoritative response, which persisted it and preserved B.
    expect(saveNodeScoped).toHaveBeenNthCalledWith(
      1,
      expect.objectContaining({ targetRecoveryId: "a@1", config: { code: "a-edit-1" } }),
    )
    expect(nodeConfig("a@1")).toEqual({ code: "a-edit-1" })
    expect(nodeConfig("b@1")).toEqual({ code: "authored-b" })
    await waitFor(() => expect(useDocumentStatusStore.getState().sourceRevision).toBe("rev-2"))

    hook.rerender({ selectedNodeId: "b@1" })
    expect(hook.result.current.handlePanelUpdateNode("b@1", editedData("b@1", "b-edit-2")).ok).toBe(
      true,
    )
    await act(async () => {
      await expect(hook.result.current.handleScopedSave()).resolves.toEqual({ ok: true })
    })
    expect(saveNodeScoped).toHaveBeenNthCalledWith(
      2,
      expect.objectContaining({ targetRecoveryId: "b@1", config: { code: "b-edit-2" } }),
    )
    // The final canvas is exactly the simulated backend's persisted state:
    // both saves survived whole-document replacement, nothing was discarded
    // and no node was lost.
    expect(graphRef.current.nodes.map((node) => node.id).sort()).toEqual(["a@1", "b@1"])
    expect(nodeConfig("a@1")).toEqual({ code: "a-edit-1" })
    expect(nodeConfig("b@1")).toEqual({ code: "b-edit-2" })
    expect(serverConfigs.get("a@1")).toEqual({ code: "a-edit-1" })
    expect(serverConfigs.get("b@1")).toEqual({ code: "b-edit-2" })
  })

  it("releases edit tracking when an unchanged document is re-adopted", async () => {
    const hook = render("a@1")
    expect(hook.result.current.handlePanelUpdateNode("a@1", editedData("a@1", "a-edit")).ok).toBe(
      true,
    )
    expect(
      hook.result.current.handlePanelUpdateNode("b@1", editedData("b@1", "b-edit")).ok,
    ).toBe(false)

    // Reloading the pipeline re-adopts the SAME revision (local edits are
    // discarded); tracking must release even though the revision is unchanged.
    act(() => {
      useDocumentStatusStore.getState().loadDocumentStatus(serverDocument("rev-1"))
    })
    await waitFor(() =>
      expect(hook.result.current.handlePanelUpdateNode("b@1", editedData("b@1", "b-edit")).ok).toBe(
        true,
      ),
    )
  })

  it("discards a late response when the document identity moved during the flight", async () => {
    let release!: (value: unknown) => void
    saveNodeScoped.mockImplementationOnce(() => new Promise((resolve) => { release = resolve }))
    const hook = render("a@1")
    let pending!: Promise<{ ok: boolean; error?: string }>
    act(() => {
      pending = hook.result.current.handleScopedSave()
    })
    // Something else replaced the document while the request was in flight.
    act(() => {
      useDocumentStatusStore.getState().loadDocumentStatus(serverDocument("rev-external"))
    })
    let result!: { ok: boolean; error?: string }
    await act(async () => {
      release(serverDocument("rev-9"))
      result = await pending
    })
    expect(result.ok).toBe(false)
    expect(result.error).toContain("The document changed while saving")
    expect(applyDocument).not.toHaveBeenCalled()
  })

  it("refuses a second save while one is in flight", async () => {
    let release!: (value: unknown) => void
    saveNodeScoped.mockImplementationOnce(() => new Promise((resolve) => { release = resolve }))
    const hook = render("a@1")
    let pending!: Promise<{ ok: boolean; error?: string }>
    act(() => {
      pending = hook.result.current.handleScopedSave()
    })
    await expect(hook.result.current.handleScopedSave()).resolves.toMatchObject({
      ok: false,
      error: expect.stringContaining("already in flight"),
    })
    await act(async () => {
      release(serverDocument("rev-2"))
      await pending
    })
    expect(saveNodeScoped).toHaveBeenCalledTimes(1)
  })
})
