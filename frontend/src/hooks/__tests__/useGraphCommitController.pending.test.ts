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
    resolveRenameIdentities: resolveNodeIdentities,
    readNamingContextKey: () => "naming context",
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

  it.each(["document", "readOnly", "unmount"])("refuses an API-input update after %s invalidates it", async (change) => {
    const node = makeNode("api", "apiInput")
    const resolution = deferred<Node[]>()
    const resolver = vi.fn(() => resolution.promise)
    const options = controllerOptions(node, resolver)
    const hook = renderHook(() => useGraphCommitController(options))
    act(() => { expect(hook.result.current.onUpdateNode("api", { ...node.data })).toEqual({ ok: true }) })
    const waiting = hook.result.current.waitForPendingCommits()
    if (change === "unmount") hook.unmount()
    else {
      if (change === "document") options.readDocumentIdentity = () => "new-document"
      else options.readOnly = true
      hook.rerender()
    }
    await act(async () => {
      resolution.resolve([node])
      await expect(waiting).resolves.toMatchObject({ ok: false })
    })
    expect(options.commitGraph).not.toHaveBeenCalled()
    expect(resolver).toHaveBeenCalledOnce()
  })

  it("refuses an API-input update whose isCurrent turns false while its identity resolves", async () => {
    // The workbench's tables were fetched for one document; a newer one was adopted
    // (the same file and revision, so the document identity is unchanged) and its
    // own fetch failed. Releasing the old resolution must change nothing.
    const node = makeNode("api", "apiInput")
    const resolution = deferred<Node[]>()
    const options = controllerOptions(node, () => resolution.promise)
    const { result } = renderHook(() => useGraphCommitController(options))
    let current = true

    act(() => {
      expect(result.current.onUpdateNode("api", { ...node.data }, { isCurrent: () => current })).toEqual({ ok: true })
    })
    const waiting = result.current.waitForPendingCommits()
    current = false
    await act(async () => {
      resolution.resolve([node])
      await expect(waiting).resolves.toMatchObject({ ok: false })
    })

    expect(options.commitGraph).not.toHaveBeenCalled()
    expect(options.setSelectedNode).not.toHaveBeenCalled()
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

  it("drops a Workbench Output's connections whose tables went, and says so", () => {
    const table = (label: string) => ({ path: "$[:]", label, emit: true, columns: [] })
    const node = makeNode("response", "workbenchOutput", {
      data: { label: "response", nodeType: "workbenchOutput", config: { tables: [table("pricing_output"), table("layers")] } },
    })
    const options = controllerOptions(node, async (nodes) => [...nodes])
    const fill = (id: string, source: string, targetHandle: string): Edge =>
      ({ id, source, target: "response", sourceHandle: null, targetHandle })
    options.graphRef.current.nodes = [node, makeNode("priced"), makeNode("layered")]
    options.graphRef.current.edges = [fill("e_priced", "priced", "pricing_output"), fill("e_layered", "layered", "layers")]
    const { result } = renderHook(() => useGraphCommitController(options))

    act(() => {
      expect(result.current.onUpdateNode("response", { ...node.data, config: { tables: [table("pricing_output")] } }))
        .toEqual({ ok: true })
    })

    const [, committedEdges] = options.commitGraph.mock.calls[0]
    expect((committedEdges as Edge[]).map((edge) => edge.id)).toEqual(["e_priced"])
    expect(options.addToast).toHaveBeenCalledWith(
      "warning",
      "Disconnected 1 edge from response: the table it filled no longer exists after your edit.",
    )
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

describe("useNodeRenameSession shape check", () => {
  it.each(["", "   ", "a`b", "line\nbreak", "x".repeat(201)])(
    "refuses %j inline without asking the server, as the Rename dialog does",
    async (label) => {
      const onRenameNode = vi.fn(async () => ({ ok: true as const }))
      const { result } = renderHook(() => useNodeRenameSession("node"))

      act(() => result.current.commit(label, onRenameNode))

      expect(onRenameNode).not.toHaveBeenCalled()
      expect(result.current.error).toEqual(expect.any(String))
    },
  )

  it("sends the trimmed name", async () => {
    const onRenameNode = vi.fn(async () => ({ ok: true as const }))
    const { result } = renderHook(() => useNodeRenameSession("node"))

    await act(async () => result.current.commit("  Claims  ", onRenameNode))

    expect(onRenameNode).toHaveBeenCalledWith("node", "Claims")
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
