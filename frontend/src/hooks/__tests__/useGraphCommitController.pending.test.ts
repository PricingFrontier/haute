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

/** Holds every identity request until the test answers it. */
function heldIdentityReplies() {
  const requests: Array<{ resolve: () => void; reject: (error: Error) => void }> = []
  const resolver = vi.fn((nodes: readonly Node[]) => new Promise<Node[]>((resolve, reject) => {
    requests.push({ resolve: () => resolve([...nodes]), reject })
  }))
  return { resolver, requests }
}

type QuoteConfig = {
  path: string
  contract: string
  tables: Array<{ path: string; label: string; emit: boolean; columns: Array<{ name: string; selected: boolean }> }>
}

function quoteInput(): Node {
  return makeNode("api", "apiInput", {
    data: {
      label: "quotes",
      nodeType: "apiInput",
      config: {
        path: "quotes.json",
        contract: "opaque",
        tables: [{
          path: "$[:]",
          label: "policy",
          emit: true,
          columns: ["age", "region", "postcode"].map((name) => ({ name, selected: true })),
        }],
      },
      _sourceHandleInputNames: { policy: "policy" },
    },
  })
}

const deselect = (column: number) => (config: QuoteConfig): QuoteConfig => ({
  ...config,
  tables: config.tables.map((table) => ({
    ...table,
    columns: table.columns.map((entry, index) => (index === column ? { ...entry, selected: false } : entry)),
  })),
})

const selections = (config: QuoteConfig): boolean[] => config.tables[0].columns.map((column) => column.selected)

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
    expect(hook.result.current.pendingNodeData.has("api")).toBe(true)
    const waiting = hook.result.current.waitForPendingCommits()
    if (change === "unmount") hook.unmount()
    else {
      if (change === "document") options.readDocumentIdentity = () => "new-document"
      else options.readOnly = true
      hook.rerender()
      // The draft goes with the context it was made in, before an edit can build on it.
      expect(hook.result.current.pendingNodeData.size).toBe(0)
    }
    await act(async () => {
      resolution.resolve([node])
      await expect(waiting).resolves.toMatchObject({ ok: false })
    })
    expect(options.commitGraph).not.toHaveBeenCalled()
    expect(resolver).toHaveBeenCalledOnce()
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

  describe("request-input edits made while identities resolve", () => {
    function setUp() {
      const node = quoteInput()
      const replies = heldIdentityReplies()
      const options = controllerOptions(node, replies.resolver)
      const hook = renderHook(() => useGraphCommitController(options))
      const committedConfig = () => options.graphRef.current.nodes
        .find((candidate) => candidate.id === "api")!.data.config as QuoteConfig
      // As the panel does: each edit is built on the published draft, else on the committed node.
      const edit = (change: (config: QuoteConfig) => QuoteConfig): unknown => {
        const base = hook.result.current.pendingNodeData.get("api")
          ?? options.graphRef.current.nodes.find((candidate) => candidate.id === "api")!.data
        let result: unknown
        act(() => {
          result = hook.result.current.onUpdateNode("api", { ...base, config: change(base.config as QuoteConfig) })
        })
        return result
      }
      const errors = () => options.addToast.mock.calls
        .filter(([type]) => type === "error")
        .map(([, text]) => text)
      return { replies, options, hook, edit, committedConfig, errors }
    }

    it.each([
      ["in order", [0, 1, 2, 3]],
      ["reversed", [3, 2, 1, 0]],
    ])("commits independent column and top-level edits together, replies %s", async (_order, replyOrder) => {
      const { replies, options, hook, edit, committedConfig, errors } = setUp()
      const expected: QuoteConfig = {
        ...deselect(1)(deselect(0)(committedConfig())),
        path: "renewals.json",
        contract: "strict",
      }

      expect(edit(deselect(0))).toEqual({ ok: true })
      expect(edit(deselect(1))).toEqual({ ok: true })
      expect(edit((config) => ({ ...config, path: "renewals.json" }))).toEqual({ ok: true })
      expect(edit((config) => ({ ...config, contract: "strict" }))).toEqual({ ok: true })
      expect(hook.result.current.pendingNodeData.get("api")?.config).toEqual(expected)
      expect(replies.requests).toHaveLength(4)

      let saved = false
      const waiting = hook.result.current.waitForPendingCommits().then((result) => {
        saved = true
        return result
      })
      await act(async () => {
        for (const index of replyOrder.slice(0, 3)) replies.requests[index].resolve()
      })
      expect(saved).toBe(false)
      await act(async () => {
        replies.requests[replyOrder[3]].resolve()
        await expect(waiting).resolves.toEqual({ ok: true })
      })

      expect(options.commitGraph).toHaveBeenCalledOnce()
      expect(committedConfig()).toEqual(expected)
      expect(hook.result.current.pendingNodeData.size).toBe(0)
      expect(errors()).toEqual([])
    })

    it("keeps the latest of repeated edits to one field, whichever reply lands first", async () => {
      const { replies, hook, edit, committedConfig, errors } = setUp()

      for (const path of ["a.json", "b.json", "c.json"]) edit((config) => ({ ...config, path }))
      await act(async () => {
        replies.requests[1].resolve()
        replies.requests[2].resolve()
        replies.requests[0].resolve()
        await hook.result.current.waitForPendingCommits()
      })

      expect(committedConfig().path).toBe("c.json")
      expect(errors()).toEqual([])
    })

    it("ignores a replaced draft's failure and reports the newest draft's without undoing earlier commits", async () => {
      const { replies, options, hook, edit, committedConfig, errors } = setUp()

      edit(deselect(0))
      await act(async () => { replies.requests[0].resolve() })
      expect(selections(committedConfig())).toEqual([false, true, true])

      // The second request fails, but the third draft carries its edit.
      edit(deselect(1))
      edit((config) => ({ ...config, path: "renewals.json" }))
      await act(async () => {
        replies.requests[1].reject(new Error("identity service unavailable"))
        replies.requests[2].resolve()
        await hook.result.current.waitForPendingCommits()
      })
      expect(selections(committedConfig())).toEqual([false, false, true])
      expect(committedConfig().path).toBe("renewals.json")
      expect(errors()).toEqual([])

      edit((config) => ({ ...config, contract: "strict" }))
      expect(hook.result.current.pendingNodeData.get("api")?.config).toMatchObject({ contract: "strict" })
      const waiting = hook.result.current.waitForPendingCommits()
      await act(async () => {
        replies.requests[3].reject(new Error("identity service unavailable"))
        await expect(waiting).resolves.toEqual({
          ok: false,
          error: "Update node failed: identity service unavailable",
        })
      })
      expect(errors()).toEqual(["Update node failed: identity service unavailable"])
      expect(hook.result.current.pendingNodeData.size).toBe(0)
      expect(committedConfig()).toMatchObject({ contract: "opaque", path: "renewals.json" })
      expect(selections(committedConfig())).toEqual([false, false, true])
      expect(options.commitGraph).toHaveBeenCalledTimes(2)
    })

    it("keeps the draft through a refused update", async () => {
      const { replies, hook, edit, committedConfig } = setUp()

      edit(deselect(0))
      const draft = hook.result.current.pendingNodeData.get("api")!
      let refused: unknown
      act(() => { refused = hook.result.current.onUpdateNode("api", { ...draft, label: "renamed" }) })
      expect(refused).toMatchObject({ ok: false })
      expect(hook.result.current.pendingNodeData.get("api")).toBe(draft)

      await act(async () => {
        replies.requests[0].resolve()
        await hook.result.current.waitForPendingCommits()
      })
      expect(selections(committedConfig())).toEqual([false, true, true])
    })

    it("lets a rename issued after an update supersede it", async () => {
      const { replies, options, hook, edit, committedConfig, errors } = setUp()

      edit(deselect(0))
      let rename!: Promise<unknown>
      act(() => { rename = hook.result.current.onRenameNode("api", "renamed") })
      await act(async () => {
        replies.requests[0].resolve()
        replies.requests[1].resolve()
        await expect(rename).resolves.toEqual({ ok: true })
      })

      expect(errors()).toEqual([
        "Node update was not applied because the document, editing capability, or a newer node edit superseded it.",
      ])
      expect(hook.result.current.pendingNodeData.size).toBe(0)
      expect(options.graphRef.current.nodes[0].data.label).toBe("renamed")
      expect(selections(committedConfig())).toEqual([true, true, true])
    })
  })

  it("drops a Quote Input's connections whose frames went, and says so", async () => {
    const table = (label: string) => ({ path: `$[:].${label}`, label, emit: true, columns: [{ name: "c", selected: true }] })
    const node = makeNode("quotes", "apiInput", {
      data: {
        label: "quotes",
        nodeType: "apiInput",
        config: { path: "quotes.json", tables: [table("policy"), table("drivers")] },
        _sourceHandleInputNames: { policy: "policy", drivers: "drivers" },
      },
    })
    const options = controllerOptions(node, async (nodes) => [...nodes])
    const frame = (id: string, sourceHandle: string, target: string): Edge =>
      ({ id, source: "quotes", target, sourceHandle, targetHandle: null })
    options.graphRef.current.nodes = [node, makeNode("priced"), makeNode("driven")]
    options.graphRef.current.edges = [frame("e_policy", "policy", "priced"), frame("e_drivers", "drivers", "driven")]
    const { result } = renderHook(() => useGraphCommitController(options))

    act(() => {
      expect(result.current.onUpdateNode("quotes", {
        ...node.data,
        config: { path: "quotes.json", tables: [table("policy")] },
      })).toEqual({ ok: true })
    })
    await act(async () => {
      await expect(result.current.waitForPendingCommits()).resolves.toEqual({ ok: true })
    })

    const [, committedEdges] = options.commitGraph.mock.calls[0]
    expect((committedEdges as Edge[]).map((edge) => edge.id)).toEqual(["e_policy"])
    expect(options.addToast).toHaveBeenCalledWith(
      "warning",
      "Disconnected 1 edge from quotes: the source frame no longer exists after your edit.",
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
