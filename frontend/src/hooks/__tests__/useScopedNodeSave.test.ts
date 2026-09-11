import { act, renderHook, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import type { Node } from "@xyflow/react"
import useDocumentStatusStore from "../../stores/useDocumentStatusStore"
import { makePipelineEditorDocument } from "../../testSupport/pipelineDocumentFixture"
import { useScopedNodeSave } from "../useScopedNodeSave"

const { saveNodeScoped } = vi.hoisted(() => ({ saveNodeScoped: vi.fn() }))
vi.mock("../../api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../api/client")>()),
  saveNodeScoped,
}))

function scopedNode(id: string, label: string): Node {
  return {
    id,
    position: { x: 0, y: 0 },
    data: {
      label,
      nodeType: "polars",
      config: { code: "df = source" },
      _sourceFile: "main.py",
      _recoveryId: id,
      _scopedEditable: true,
    },
  } as unknown as Node
}

function documentWithRevision(revision: string) {
  return makePipelineEditorDocument({ source_revision: revision })
}

describe("useScopedNodeSave", () => {
  const graphRef = { current: { nodes: [scopedNode("a@1", "A"), scopedNode("b@1", "B")], edges: [] } }
  const onUpdateNode = vi.fn(() => ({ ok: true as const }))
  let applyDocument: ReturnType<typeof vi.fn>

  beforeEach(() => {
    vi.clearAllMocks()
    // Adoption replaces the authoritative document, exactly as App does.
    applyDocument = vi.fn((document) => {
      useDocumentStatusStore.getState().loadDocumentStatus(document)
    })
    act(() => {
      useDocumentStatusStore.getState().loadDocumentStatus(documentWithRevision("rev-1"))
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

  it("blocks edits to any node while a save is in flight, across panel lifecycles", async () => {
    let release!: (value: unknown) => void
    saveNodeScoped.mockImplementationOnce(() => new Promise((resolve) => { release = resolve }))
    const hook = render("a@1")

    expect(hook.result.current.handlePanelUpdateNode("a@1", { config: {} }).ok).toBe(true)
    let pending!: Promise<{ ok: boolean; error?: string }>
    act(() => {
      pending = hook.result.current.handleScopedSave()
    })

    // The user closes A's panel and opens B before the response returns: the
    // guard lives in the App-lifetime hook, not the unmounted panel.
    hook.rerender({ selectedNodeId: "b@1" })
    const blocked = hook.result.current.handlePanelUpdateNode("b@1", { config: {} })
    expect(blocked.ok).toBe(false)
    expect(blocked).toMatchObject({ error: expect.stringContaining("in flight") })
    expect(onUpdateNode).toHaveBeenCalledTimes(1)

    await act(async () => {
      release(documentWithRevision("rev-2"))
      await pending
    })
    expect(applyDocument).toHaveBeenCalledTimes(1)
    expect(hook.result.current.handlePanelUpdateNode("b@1", { config: {} }).ok).toBe(true)
  })

  it("completes the two-node sequence without discarding work", async () => {
    saveNodeScoped
      .mockResolvedValueOnce(documentWithRevision("rev-2"))
      .mockResolvedValueOnce(documentWithRevision("rev-3"))
    const hook = render("a@1")

    expect(hook.result.current.handlePanelUpdateNode("a@1", { config: {} }).ok).toBe(true)
    // The deadlock state is unreachable: a second node cannot start editing
    // while the first holds unsaved scoped edits.
    const refused = hook.result.current.handlePanelUpdateNode("b@1", { config: {} })
    expect(refused.ok).toBe(false)
    expect(refused).toMatchObject({ error: expect.stringContaining("Save A") })

    await act(async () => {
      await expect(hook.result.current.handleScopedSave()).resolves.toEqual({ ok: true })
    })
    await waitFor(() =>
      expect(useDocumentStatusStore.getState().sourceRevision).toBe("rev-2"),
    )

    hook.rerender({ selectedNodeId: "b@1" })
    expect(hook.result.current.handlePanelUpdateNode("b@1", { config: {} }).ok).toBe(true)
    await act(async () => {
      await expect(hook.result.current.handleScopedSave()).resolves.toEqual({ ok: true })
    })
    expect(saveNodeScoped).toHaveBeenCalledTimes(2)
    expect(onUpdateNode).toHaveBeenCalledTimes(2)
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
      useDocumentStatusStore.getState().loadDocumentStatus(documentWithRevision("rev-external"))
    })
    let result!: { ok: boolean; error?: string }
    await act(async () => {
      release(documentWithRevision("rev-2"))
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
      release(documentWithRevision("rev-2"))
      await pending
    })
    expect(saveNodeScoped).toHaveBeenCalledTimes(1)
  })
})
