import { act, cleanup, fireEvent, render, renderHook, screen } from "@testing-library/react"
import type { Edge, Node } from "@xyflow/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import CommittedTextField from "../../components/form/CommittedTextField"
import useGraphStore, { resetGraphStoreForTests } from "../../stores/useGraphStore"
import useGraphCommitController from "../useGraphCommitController"

function options() {
  return {
    readDocumentIdentity: () => "document",
    readOnly: false,
    reservedApiInputFrameLabels: new Set<string>(),
    resolveNodeIdentities: vi.fn(async (nodes: readonly Node[]) => [...nodes]),
    resolveRenameIdentities: vi.fn(async (nodes: readonly Node[]) => [...nodes]),
    readNamingContextKey: () => "naming context",
    commitGraph: vi.fn((nodes: Node[], edges: Edge[], submodels: Record<string, unknown>) =>
      useGraphStore.getState().setNodesAndEdgesAndSubmodels(nodes, edges, submodels)),
    setSelectedNode: vi.fn(),
    addToast: vi.fn(),
  }
}

describe("the save barrier", () => {
  beforeEach(() => resetGraphStoreForTests())
  afterEach(cleanup)

  it("commits a value still being typed, never left, before a save reads the graph", async () => {
    const onCommit = vi.fn()
    const hook = renderHook(() => useGraphCommitController(options()))
    render(<CommittedTextField aria-label="Suffix" value="_left" onCommit={onCommit} />)
    fireEvent.change(screen.getByLabelText("Suffix"), { target: { value: "_right" } })

    await act(async () => {
      await expect(hook.result.current.settleEdits()).resolves.toEqual({ ok: true })
    })

    expect(onCommit).toHaveBeenCalledExactlyOnceWith("_right")
  })
})
