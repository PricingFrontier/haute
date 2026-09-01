import { act, cleanup, renderHook } from "@testing-library/react"
import type { Edge, Node } from "@xyflow/react"
import { afterEach, describe, expect, it, vi } from "vitest"

import useGraphCommitController from "../useGraphCommitController"
import { useNodeRenameSession } from "../../panels/useNodePanelSession"
import { makeNode } from "../../test-utils/factories"

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((resolvePromise) => {
    resolve = resolvePromise
  })
  return { promise, resolve }
}

function controllerOptions(node: Node, resolveNodeIdentities: (nodes: readonly Node[]) => Promise<Node[]>) {
  return {
    graphRef: { current: { nodes: [node], edges: [] as Edge[] } },
    submodelsRef: { current: {} },
    readDocumentIdentity: () => "document",
    readOnly: false,
    reservedApiInputFrameLabels: new Set<string>(),
    resolveNodeIdentities,
    commitGraph: vi.fn(),
    setSelectedNode: vi.fn(),
    addToast: vi.fn(),
  }
}

describe("useGraphCommitController pending commits", () => {
  afterEach(() => {
    cleanup()
    vi.clearAllMocks()
  })

  it("registers API-input updates synchronously and waits for their commit", async () => {
    const node = makeNode("api", "apiInput")
    const resolution = deferred<Node[]>()
    const options = controllerOptions(node, () => resolution.promise)
    const { result } = renderHook(() => useGraphCommitController(options))

    act(() => {
      expect(result.current.onUpdateNode("api", { ...node.data })).toEqual({ ok: true })
    })
    let settled = false
    const waiting = result.current.waitForPendingCommits().then((value) => {
      settled = true
      return value
    })
    await Promise.resolve()
    expect(settled).toBe(false)

    resolution.resolve([node])

    await expect(waiting).resolves.toEqual({ ok: true })
    expect(options.commitGraph).toHaveBeenCalledOnce()
  })

  it("registers rename failures synchronously and returns the failure to savers", async () => {
    const node = makeNode("node")
    const resolution = deferred<Node[]>()
    const { result } = renderHook(() => useGraphCommitController(
      controllerOptions(node, () => resolution.promise),
    ))

    let rename: Promise<unknown>
    act(() => {
      rename = result.current.onRenameNode("node", "Renamed")
    })
    const waiting = result.current.waitForPendingCommits()
    resolution.resolve([])

    await expect(rename!).resolves.toMatchObject({ ok: false })
    await expect(waiting).resolves.toMatchObject({ ok: false })
  })
})

describe("useNodeRenameSession", () => {
  afterEach(cleanup)

  it("calls the rename handler in the committing event turn", () => {
    const onRenameNode = vi.fn(() => Promise.resolve({ ok: true as const }))
    const { result } = renderHook(() => useNodeRenameSession("node"))

    act(() => {
      result.current.commit("Renamed", onRenameNode)
      expect(onRenameNode).toHaveBeenCalledWith("node", "Renamed")
    })
  })
})
