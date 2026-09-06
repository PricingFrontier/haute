import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { renderHook, cleanup, act } from "@testing-library/react"
import type { Node, Edge } from "@xyflow/react"
import useNodeHandlers from "../useNodeHandlers"
import useToastStore from "../../stores/useToastStore"
import useNodeResultsStore from "../../stores/useNodeResultsStore"
import { makeNode } from "../../test-utils/factories"
import { getLayoutedElements } from "../../utils/layout"

vi.mock("../../utils/layout", () => ({
  getLayoutedElements: vi.fn(async (nodes: Node[]) => nodes),
}))

function makeParams() {
  return {
    graphRef: { current: { nodes: [] as Node[], edges: [] as Edge[] } },
    nodeIdCounter: { current: 10 },
    lastSelectedNodeRef: { current: null as Node | null },
    setNodes: vi.fn(),
    setNodesAndEdges: vi.fn(),
    setSelectedNode: vi.fn(),
    setPreviewData: vi.fn(),
    fitView: vi.fn(),
    submodels: {} as Record<string, unknown>,
    resolveNodeIdentities: vi.fn(async (nodes: readonly Node[]) => [...nodes]),
  }
}

describe("useNodeHandlers", () => {
  beforeEach(() => {
    useNodeResultsStore.setState({ previews: {}, columnCache: {} })
    vi.mocked(getLayoutedElements).mockReset()
    vi.mocked(getLayoutedElements).mockImplementation(async (nodes: Node[]) => nodes)
  })

  afterEach(() => {
    cleanup()
    vi.restoreAllMocks()
  })

  it("handleDeleteNode removes node and connected edges in ONE atomic step", () => {
    const params = makeParams()
    const n1 = makeNode("n1")
    const n2 = makeNode("n2")
    const edges = [{ id: "e1", source: "n1", target: "n2" } as Edge]
    params.graphRef.current = { nodes: [n1, n2], edges }
    const { result } = renderHook(() => useNodeHandlers(params))
    act(() => {
      result.current.handleDeleteNode("n1")
    })
    // Undo-atomicity: exactly one combined setNodesAndEdges call — never a
    // separate setNodes then setEdges (that would be two undo snapshots).
    expect(params.setNodesAndEdges).toHaveBeenCalledOnce()
    expect(params.setNodes).not.toHaveBeenCalled()
    const [nodesUpdater, edgesUpdater] = params.setNodesAndEdges.mock.calls[0] as [
      (nds: Node[]) => Node[],
      (eds: Edge[]) => Edge[],
    ]
    expect(nodesUpdater([n1, n2])).toEqual([n2])
    expect(edgesUpdater(edges)).toEqual([])
  })

  it("uses a committed shared deletion without a raw graph setter while cleaning selection", () => {
    const n1 = makeNode("n1")
    const params = makeParams()
    params.graphRef.current = { nodes: [n1], edges: [] }
    const commitSharedNodeDeletion = vi.fn(() => "committed" as const)
    const { result } = renderHook(() => useNodeHandlers({
      ...params,
      commitSharedNodeDeletion,
    }))
    act(() => result.current.handleDeleteNode("n1"))
    expect(params.setNodesAndEdges).not.toHaveBeenCalled()
    expect(params.setSelectedNode).toHaveBeenCalledOnce()
    expect(params.setPreviewData).toHaveBeenCalledOnce()
  })

  it("leaves graph and cleanup untouched when shared deletion is blocked", () => {
    const params = makeParams()
    params.graphRef.current = { nodes: [makeNode("n1")], edges: [] }
    const commitSharedNodeDeletion = vi.fn(() => "blocked" as const)
    const { result } = renderHook(() => useNodeHandlers({
      ...params,
      commitSharedNodeDeletion,
    }))
    act(() => result.current.handleDeleteNode("n1"))
    expect(params.setNodesAndEdges).not.toHaveBeenCalled()
    expect(params.setSelectedNode).not.toHaveBeenCalled()
    expect(params.setPreviewData).not.toHaveBeenCalled()
  })

  it("defers cleanup of a pending shared deletion until the identity commit lands", () => {
    const params = makeParams()
    params.graphRef.current = { nodes: [makeNode("n1")], edges: [] }
    let settle: ((committed: boolean) => void) | undefined
    const commitSharedNodeDeletion = vi.fn(
      (
        _ids: ReadonlySet<string>,
        _edges?: ReadonlySet<string>,
        _changes?: unknown,
        onSettled?: (committed: boolean) => void,
      ) => {
        settle = onSettled
        return "pending" as const
      },
    )
    const { result } = renderHook(() => useNodeHandlers({
      ...params,
      commitSharedNodeDeletion,
    }))
    act(() => result.current.handleDeleteNode("n1"))
    // Nothing is cleaned up while parent identities are still resolving.
    expect(params.setNodesAndEdges).not.toHaveBeenCalled()
    expect(params.setSelectedNode).not.toHaveBeenCalled()
    expect(params.setPreviewData).not.toHaveBeenCalled()

    act(() => settle?.(true))
    expect(params.setNodesAndEdges).not.toHaveBeenCalled()
    expect(params.setSelectedNode).toHaveBeenCalledOnce()
    expect(params.setPreviewData).toHaveBeenCalledOnce()
  })

  it("keeps a pending shared deletion's state when the identity commit fails", () => {
    const params = makeParams()
    params.graphRef.current = { nodes: [makeNode("n1")], edges: [] }
    let settle: ((committed: boolean) => void) | undefined
    const commitSharedNodeDeletion = vi.fn(
      (
        _ids: ReadonlySet<string>,
        _edges?: ReadonlySet<string>,
        _changes?: unknown,
        onSettled?: (committed: boolean) => void,
      ) => {
        settle = onSettled
        return "pending" as const
      },
    )
    const { result } = renderHook(() => useNodeHandlers({
      ...params,
      commitSharedNodeDeletion,
    }))
    act(() => result.current.handleDeleteNode("n1"))
    act(() => settle?.(false))
    expect(params.setNodesAndEdges).not.toHaveBeenCalled()
    expect(params.setSelectedNode).not.toHaveBeenCalled()
    expect(params.setPreviewData).not.toHaveBeenCalled()
  })

  it("refuses raw deletion of a submodel definition owner", () => {
    const params = makeParams()
    const owner = makeNode("scoring", "submodel", {
      data: {
        label: "scoring",
        nodeType: "submodel",
        config: { definitionId: "definition_scoring", alias: "scoring" },
      },
    })
    params.graphRef.current = { nodes: [owner], edges: [] }
    const { result } = renderHook(() => useNodeHandlers(params))

    act(() => {
      result.current.handleDeleteNode(owner.id)
    })

    expect(params.setNodesAndEdges).not.toHaveBeenCalled()
    expect(useToastStore.getState().toasts.at(-1)?.text).toMatch(/Dissolve Submodel/)
  })

  it("refuses raw deletion of a submodel occurrence with malformed identity", () => {
    const params = makeParams()
    const malformed = makeNode("broken", "submodel", {
      data: {
        label: "Scoring",
        nodeType: "submodel",
        config: {},
      },
    })
    params.graphRef.current = { nodes: [malformed], edges: [] }
    const { result } = renderHook(() => useNodeHandlers(params))

    act(() => {
      result.current.handleDeleteNode(malformed.id)
    })

    expect(params.setNodesAndEdges).not.toHaveBeenCalled()
    expect(useToastStore.getState().toasts.at(-1)?.text).toMatch(/Dissolve Submodel/)
  })

  it("deletes a submodel instance copy and its edges directly", () => {
    const params = makeParams()
    const copy = makeNode("scoring_2", "submodel", {
      data: {
        label: "scoring_2",
        nodeType: "submodel",
        config: {
          definitionId: "definition_scoring",
          alias: "scoring_2",
          instanceOf: "scoring",
        },
      },
    })
    const upstream = makeNode("upstream", "polars")
    params.graphRef.current = {
      nodes: [upstream, copy],
      edges: [
        { id: "bind", source: "upstream", target: copy.id, targetHandle: "in__policy" } as Edge,
      ],
    }
    const { result } = renderHook(() => useNodeHandlers(params))

    act(() => {
      result.current.handleDeleteNode(copy.id)
    })

    expect(params.setNodesAndEdges).toHaveBeenCalledOnce()
    const [nodesUpdater, edgesUpdater] = params.setNodesAndEdges.mock.calls[0]
    expect((nodesUpdater as (n: Node[]) => Node[])(params.graphRef.current.nodes)
      .map((node) => node.id)).toEqual(["upstream"])
    expect((edgesUpdater as (e: Edge[]) => Edge[])(params.graphRef.current.edges)).toEqual([])
    expect(useToastStore.getState().toasts).toEqual([])
  })

  it("handleDeleteNode clears selected node if it was selected", () => {
    const params = makeParams()
    const n1 = makeNode("n1")
    params.graphRef.current = { nodes: [n1], edges: [] }
    const { result } = renderHook(() => useNodeHandlers(params))
    act(() => {
      result.current.handleDeleteNode("n1")
    })
    // setSelectedNode is called with a function that returns null when prev.id === id
    const updater = params.setSelectedNode.mock.calls[0][0] as (prev: Node | null) => Node | null
    expect(updater(n1)).toBeNull()
  })

  it("handleDeleteNode preserves selected node if different", () => {
    const params = makeParams()
    const n1 = makeNode("n1")
    const n2 = makeNode("n2")
    params.graphRef.current = { nodes: [n1, n2], edges: [] }
    const { result } = renderHook(() => useNodeHandlers(params))
    act(() => {
      result.current.handleDeleteNode("n1")
    })
    const updater = params.setSelectedNode.mock.calls[0][0] as (prev: Node | null) => Node | null
    expect(updater(n2)).toBe(n2)
  })

  it("handleDuplicateNode creates a copy with offset position", async () => {
    const params = makeParams()
    params.resolveNodeIdentities = vi.fn(async ([candidate]) => [{
      ...candidate!,
      data: { ...candidate!.data, _functionName: "authoritative_copy" },
    }])
    const n1 = makeNode("n1")
    n1.position = { x: 100, y: 200 }
    params.graphRef.current = { nodes: [n1], edges: [] }
    const { result } = renderHook(() => useNodeHandlers(params))
    await act(async () => {
      await result.current.handleDuplicateNode("n1")
    })
    expect(params.setNodes).toHaveBeenCalledOnce()
    expect(params.nodeIdCounter.current).toBe(11)
    expect(params.setSelectedNode).toHaveBeenCalledOnce()
    const newNode = params.setSelectedNode.mock.calls[0][0] as Node
    expect(newNode.position).toEqual({ x: 140, y: 240 })
    expect(newNode.data.label).toContain("copy")
    expect(newNode.data._functionName).toBe("authoritative_copy")
  })

  it("leaves duplicate creation untouched when identity resolution rejects", async () => {
    const params = makeParams()
    params.graphRef.current = { nodes: [makeNode("n1")], edges: [] }
    params.resolveNodeIdentities = vi.fn(async () => { throw new Error("identity service unavailable") })
    const { result } = renderHook(() => useNodeHandlers(params))

    await act(async () => { await result.current.handleDuplicateNode("n1") })

    expect(params.setNodes).not.toHaveBeenCalled()
    expect(params.setSelectedNode).not.toHaveBeenCalled()
    expect(useToastStore.getState().toasts.at(-1)?.text).toMatch(/Create node failed: identity service unavailable/)
  })

  it("rejects malformed duplicate identity output atomically", async () => {
    const params = makeParams()
    params.graphRef.current = { nodes: [makeNode("n1")], edges: [] }
    params.resolveNodeIdentities = vi.fn(async () => [makeNode("wrong")])
    const { result } = renderHook(() => useNodeHandlers(params))

    await act(async () => { await result.current.handleDuplicateNode("n1") })

    expect(params.setNodes).not.toHaveBeenCalled()
    expect(params.setSelectedNode).not.toHaveBeenCalled()
    expect(useToastStore.getState().toasts.at(-1)?.text).toMatch(/invalid node/)
  })

  it("rejects a stale duplicate identity result after graph replacement", async () => {
    let resolve!: (nodes: Node[]) => void
    const params = makeParams()
    params.graphRef.current = { nodes: [makeNode("n1")], edges: [] }
    params.resolveNodeIdentities = vi.fn(() => new Promise<Node[]>((done) => { resolve = done }))
    const { result } = renderHook(() => useNodeHandlers(params))

    const pending = result.current.handleDuplicateNode("n1")
    params.graphRef.current = { nodes: [makeNode("replacement")], edges: [] }
    await act(async () => { resolve([makeNode("polars_11")]); await pending })

    expect(params.setNodes).not.toHaveBeenCalled()
    expect(params.setSelectedNode).not.toHaveBeenCalled()
    expect(useToastStore.getState().toasts.at(-1)?.text).toMatch(/graph changed/i)
  })

  it("refuses generic duplication of a submodel occurrence", () => {
    const params = makeParams()
    const submodel = makeNode("scoring", "submodel", {
      data: {
        label: "scoring",
        nodeType: "submodel",
        config: { definitionId: "definition_scoring", alias: "scoring" },
      },
    })
    params.graphRef.current = { nodes: [submodel], edges: [] }
    const { result } = renderHook(() => useNodeHandlers(params))

    act(() => {
      result.current.handleDuplicateNode(submodel.id)
    })

    expect(params.setNodes).not.toHaveBeenCalled()
    expect(useToastStore.getState().toasts.at(-1)?.text).toMatch(/Create Instance/)
  })

  it("handleDuplicateNode does nothing for singleton node types", () => {
    const params = makeParams()
    const apiNode = makeNode("api1")
    apiNode.data = { ...apiNode.data, nodeType: "apiInput" }
    params.graphRef.current = { nodes: [apiNode], edges: [] }
    const { result } = renderHook(() => useNodeHandlers(params))
    act(() => {
      result.current.handleDuplicateNode("api1")
    })
    expect(params.setNodes).not.toHaveBeenCalled()
  })

  it("handleDuplicateNode does nothing for output node types", () => {
    const params = makeParams()
    const outputNode = makeNode("out1")
    outputNode.data = { ...outputNode.data, nodeType: "output" }
    params.graphRef.current = { nodes: [outputNode], edges: [] }
    const { result } = renderHook(() => useNodeHandlers(params))
    act(() => {
      result.current.handleDuplicateNode("out1")
    })
    expect(params.setNodes).not.toHaveBeenCalled()
  })

  it("handleDuplicateNode does nothing if node not found", () => {
    const params = makeParams()
    params.graphRef.current = { nodes: [], edges: [] }
    const { result } = renderHook(() => useNodeHandlers(params))
    act(() => {
      result.current.handleDuplicateNode("nonexistent")
    })
    expect(params.setNodes).not.toHaveBeenCalled()
  })

  it("handleCreateInstance creates an instance node with toast", async () => {
    const params = makeParams()
    params.resolveNodeIdentities = vi.fn(async ([candidate]) => [{
      ...candidate!,
      data: { ...candidate!.data, _functionName: "authoritative_instance" },
    }])
    const n1 = makeNode("n1")
    params.graphRef.current = { nodes: [n1], edges: [] }
    const { result } = renderHook(() => useNodeHandlers(params))
    await act(async () => {
      await result.current.handleCreateInstance("n1")
    })
    expect(params.setNodes).toHaveBeenCalledOnce()
    expect(params.setSelectedNode).toHaveBeenCalledOnce()
    const newNode = params.setSelectedNode.mock.calls[0][0] as Node
    expect(newNode.data.config).toEqual({ instanceOf: "n1" })
    expect(newNode.data.label).toContain("instance")
    expect(newNode.data._functionName).toBe("authoritative_instance")
    const toasts = useToastStore.getState().toasts
    expect(toasts[toasts.length - 1]).toMatchObject({ type: "info" })
  })

  // An instance is a second node like any other, so the singleton rule that
  // handleDuplicateNode and the paste path enforce has to hold here too — the
  // toolbar's Instance button reaches every node type, not just submodels.
  it.each(["apiInput", "output", "liveSwitch"])(
    "handleCreateInstance refuses singleton node type %s",
    (nodeType) => {
      const params = makeParams()
      const singleton = makeNode("only1")
      singleton.data = { ...singleton.data, nodeType }
      params.graphRef.current = { nodes: [singleton], edges: [] }
      const { result } = renderHook(() => useNodeHandlers(params))
      act(() => {
        result.current.handleCreateInstance("only1")
      })
      expect(params.setNodes).not.toHaveBeenCalled()
      expect(useToastStore.getState().toasts.at(-1)?.text).toMatch(/only one node of this type/)
    },
  )

  // Instancing an instance must produce a SIBLING, not a chain: resolveInstanceOriginal
  // does no chain-walking, so a chained instanceOf would resolve the "original" to
  // another pointer with no content of its own.
  it("handleCreateInstance points a new instance at the original, not at the instance", async () => {
    const params = makeParams()
    const original = makeNode("polars_1")
    const existing = makeNode("polars_2")
    existing.data = { ...existing.data, config: { instanceOf: "polars_1" } }
    params.graphRef.current = { nodes: [original, existing], edges: [] }
    const { result } = renderHook(() => useNodeHandlers(params))
    await act(async () => {
      await result.current.handleCreateInstance("polars_2")
    })
    const newNode = params.setSelectedNode.mock.calls[0][0] as Node
    expect(newNode.data.config).toEqual({ instanceOf: "polars_1" })
  })

  it.each([
    "dangling",
    "self-pointing",
    "type-mismatched",
    "chained",
    "malformed",
  ])("handleCreateInstance refuses %s ordinary-instance identity", (identity) => {
    const params = makeParams()
    const selected = makeNode("polars_2", "polars", {
      data: {
        label: "Selected instance",
        nodeType: "polars",
        config: {
          instanceOf: identity === "self-pointing"
            ? "polars_2"
            : identity === "malformed" ? 42 : "polars_1",
        },
      },
    })
    const owner = makeNode(
      "polars_1",
      identity === "type-mismatched" ? "dataInput" : "polars",
      identity === "chained"
        ? { data: { label: "Chained owner", nodeType: "polars", config: { instanceOf: "polars_0" } } }
        : {},
    )
    params.graphRef.current = {
      nodes: ["dangling", "self-pointing", "malformed"].includes(identity)
        ? [selected]
        : [owner, selected],
      edges: [],
    }
    const { result } = renderHook(() => useNodeHandlers(params))

    act(() => {
      result.current.handleCreateInstance("polars_2")
    })

    expect(params.setNodes).not.toHaveBeenCalled()
    expect(params.setSelectedNode).not.toHaveBeenCalled()
    expect(useToastStore.getState().toasts.at(-1)).toMatchObject({
      type: "error",
      text: expect.stringMatching(/cannot create.*original/i),
    })
  })

  it("creates a SUBMODEL occurrence without copying its shared definition", async () => {
    const params = makeParams()
    const source = makeNode("scoring", "submodel", {
      position: { x: 100, y: 200 },
      data: {
        label: "scoring",
        nodeType: "submodel",
        config: {
          definitionId: "definition_scoring",
          alias: "scoring",
          file: "modules/scoring.py",
          childNodeIds: ["internal_input", "internal_output"],
          graph: { nodes: [{ id: "internal_input" }], edges: [] },
        },
      },
    })
    const existing = makeNode("scoring_2", "submodel", {
      data: {
        label: "scoring_2",
        nodeType: "submodel",
        config: {
          definitionId: "definition_scoring",
          alias: "scoring_2",
        },
      },
    })
    params.graphRef.current = { nodes: [source, existing], edges: [] }
    const { result } = renderHook(() => useNodeHandlers(params))

    await act(async () => {
      await result.current.handleCreateInstance(source.id)
    })

    expect(params.setNodes).toHaveBeenCalledOnce()
    expect(params.setNodesAndEdges).not.toHaveBeenCalled()
    expect(params.setSelectedNode).toHaveBeenCalledOnce()
    const created = params.setSelectedNode.mock.calls[0][0] as Node
    expect([source.id, existing.id]).not.toContain(created.id)
    expect(created.id).toBe("scoring_3")
    expect(created.type).toBe("submodel")
    expect(created.data.nodeType).toBe("submodel")
    expect(created.data.label).toBe("scoring_3")
    expect(created.data.config).toEqual({
      definitionId: "definition_scoring",
      alias: "scoring_3",
      instanceOf: source.id,
    })
    expect(created.position).toEqual({ x: 160, y: 280 })
    expect(created.data.config).not.toHaveProperty("graph")
    expect(created.data.config).not.toHaveProperty("file")
    expect(created.data.config).not.toHaveProperty("childNodeIds")
  })

  it("refuses to instance a submodel definition containing a singleton", async () => {
    const params = makeParams()
    const source = makeNode("inputs", "submodel", {
      data: {
        label: "inputs",
        nodeType: "submodel",
        config: { definitionId: "definition_inputs", alias: "inputs" },
      },
    })
    params.graphRef.current = { nodes: [source], edges: [] }
    params.submodels = {
      definition_inputs: {
        definitionId: "definition_inputs",
        file: "modules/inputs.py",
        graph: {
          nodes: [makeNode("quote_input", "apiInput", {
            data: { label: "Quote Input", nodeType: "apiInput", config: {} },
          })],
          edges: [],
        },
        inputPorts: [],
        outputPorts: [],
      },
    }
    const { result } = renderHook(() => useNodeHandlers(params))

    await act(async () => {
      await result.current.handleCreateInstance(source.id)
    })

    expect(params.resolveNodeIdentities).not.toHaveBeenCalled()
    expect(params.setNodes).not.toHaveBeenCalled()
    expect(params.setSelectedNode).not.toHaveBeenCalled()
    expect(useToastStore.getState().toasts.at(-1)).toMatchObject({
      type: "info",
      text: expect.stringMatching(/contains.*Quote Input.*only one/i),
    })
  })

  it("continues copy numbering past nine instead of nesting suffixes", async () => {
    const params = makeParams()
    const owner = makeNode("scoring", "submodel", {
      data: {
        label: "scoring",
        nodeType: "submodel",
        config: { definitionId: "definition_scoring", alias: "scoring" },
      },
    })
    const copies = Array.from({ length: 9 }, (_, index) =>
      makeNode(`scoring_${index + 2}`, "submodel", {
        data: {
          label: `scoring_${index + 2}`,
          nodeType: "submodel",
          config: {
            definitionId: "definition_scoring",
            alias: `scoring_${index + 2}`,
            instanceOf: "scoring",
          },
        },
      }))
    params.graphRef.current = { nodes: [owner, ...copies], edges: [] }
    const { result } = renderHook(() => useNodeHandlers(params))

    await act(async () => {
      await result.current.handleCreateInstance("scoring_10")
    })

    const created = params.setNodes.mock.calls[0][0](params.graphRef.current.nodes)
      .find((node: Node) => node.selected && !params.graphRef.current.nodes.includes(node))
    expect((created?.data.config as { alias?: string }).alias).toBe("scoring_11")
  })

  it("allocates occurrence ids and aliases across the combined identity namespace", async () => {
    const params = makeParams()
    const source = makeNode("scoring", "submodel", {
      data: {
        label: "scoring",
        nodeType: "submodel",
        config: { definitionId: "definition_scoring", alias: "scoring" },
      },
    })
    const aliasOccupier = makeNode("other_submodel", "submodel", {
      data: {
        label: "other_submodel",
        nodeType: "submodel",
        config: { definitionId: "definition_other", alias: "other_submodel" },
      },
    })
    const nodeIdOccupier = makeNode("scoring_2")
    params.graphRef.current = {
      nodes: [source, aliasOccupier, nodeIdOccupier],
      edges: [],
    }
    const { result } = renderHook(() => useNodeHandlers(params))

    await act(async () => {
      await result.current.handleCreateInstance(source.id)
    })

    const created = params.setSelectedNode.mock.calls[0][0] as Node
    expect(created.id).toBe("scoring_3")
    expect(created.data.config).toEqual({
      definitionId: "definition_scoring",
      alias: "scoring_3",
      instanceOf: source.id,
    })
  })

  it("mints the node id equal to its alias and never a submodel_<n> id", async () => {
    const params = makeParams()
    const source = makeNode("scoring", "submodel", {
      data: {
        label: "scoring",
        nodeType: "submodel",
        config: { definitionId: "definition_scoring", alias: "scoring" },
      },
    })
    params.graphRef.current = { nodes: [source], edges: [] }
    const { result } = renderHook(() => useNodeHandlers(params))

    await act(async () => {
      await result.current.handleCreateInstance(source.id)
    })

    const created = params.setSelectedNode.mock.calls[0][0] as Node
    expect(created.id).toBe("scoring_2")
    expect(created.id).not.toMatch(/^submodel_\d+$/)
    expect(created.data.label).toBe("scoring_2")
    expect(created.data.config).toEqual({
      definitionId: "definition_scoring",
      alias: "scoring_2",
      instanceOf: "scoring",
    })
  })

  it("rejects a partial reusable-submodel identity", () => {
    const params = makeParams()
    const source = makeNode("scoring", "submodel", {
      data: {
        label: "scoring",
        nodeType: "submodel",
        config: { alias: "scoring" },
      },
    })
    params.graphRef.current = { nodes: [source], edges: [] }
    const { result } = renderHook(() => useNodeHandlers(params))

    act(() => {
      result.current.handleCreateInstance(source.id)
    })

    expect(params.setNodes).not.toHaveBeenCalled()
    expect(useToastStore.getState().toasts.at(-1)?.text).toMatch(/identity/)
  })

  it("rejects an occurrence whose editable definition owner is missing", () => {
    const params = makeParams()
    const source = makeNode("scoring_2", "submodel", {
      data: {
        label: "scoring_2",
        nodeType: "submodel",
        config: {
          definitionId: "definition_scoring",
          alias: "scoring_2",
          instanceOf: "missing_owner",
        },
      },
    })
    params.graphRef.current = { nodes: [source], edges: [] }
    const { result } = renderHook(() => useNodeHandlers(params))

    act(() => {
      result.current.handleCreateInstance(source.id)
    })

    expect(params.setNodes).not.toHaveBeenCalled()
    expect(useToastStore.getState().toasts.at(-1)?.text).toMatch(
      /editable definition owner is invalid/,
    )
  })

  it("normalises a suffixed source alias before choosing the next occurrence alias", async () => {
    const params = makeParams()
    const base = makeNode("scoring", "submodel", {
      data: {
        label: "scoring",
        nodeType: "submodel",
        config: {
          definitionId: "definition_scoring",
          alias: "scoring",
        },
      },
    })
    const source = makeNode("scoring_2", "submodel", {
      data: {
        label: "scoring_2",
        nodeType: "submodel",
        config: {
          definitionId: "definition_scoring",
          alias: "scoring_2",
          instanceOf: base.id,
        },
      },
    })
    params.graphRef.current = { nodes: [base, source], edges: [] }
    const { result } = renderHook(() => useNodeHandlers(params))

    await act(async () => {
      await result.current.handleCreateInstance(source.id)
    })

    const created = params.setSelectedNode.mock.calls[0][0] as Node
    expect(created.id).toBe("scoring_3")
    expect(created.data.config).toEqual({
      definitionId: "definition_scoring",
      alias: "scoring_3",
      instanceOf: base.id,
    })
  })


  it("handleAutoLayout applies layout and toasts", async () => {
    vi.useFakeTimers()
    const params = makeParams()
    const n1 = makeNode("n1")
    params.graphRef.current = { nodes: [n1], edges: [] }
    const { result } = renderHook(() => useNodeHandlers(params))
    await act(async () => {
      await result.current.handleAutoLayout()
    })
    expect(params.setNodes).toHaveBeenCalledOnce()
    const toasts = useToastStore.getState().toasts
    expect(toasts[toasts.length - 1]).toMatchObject({ type: "info", text: "Auto-layout applied" })
    act(() => { vi.advanceTimersByTime(100) })
    expect(params.fitView).toHaveBeenCalledWith({ padding: 0.15 })
    vi.useRealTimers()
  })

  it("exposes pending auto-layout state while ELK is loading", async () => {
    let resolveLayout!: (nodes: Node[]) => void
    vi.mocked(getLayoutedElements).mockReturnValueOnce(new Promise((resolve) => {
      resolveLayout = resolve
    }))
    const params = makeParams()
    const n1 = makeNode("n1")
    params.graphRef.current = { nodes: [n1], edges: [] }
    const { result } = renderHook(() => useNodeHandlers(params))

    expect(result.current.isAutoLayouting).toBe(false)

    await act(async () => {
      void result.current.handleAutoLayout()
    })

    expect(result.current.isAutoLayouting).toBe(true)
    expect(params.setNodes).not.toHaveBeenCalled()

    await act(async () => {
      resolveLayout([n1])
    })

    expect(result.current.isAutoLayouting).toBe(false)
    expect(params.setNodes).toHaveBeenCalledOnce()
  })

  it("does not queue overlapping auto-layout runs from repeated clicks", async () => {
    let resolveLayout!: (nodes: Node[]) => void
    vi.mocked(getLayoutedElements).mockReturnValueOnce(new Promise((resolve) => {
      resolveLayout = resolve
    }))
    const params = makeParams()
    const n1 = makeNode("n1")
    params.graphRef.current = { nodes: [n1], edges: [] }
    const { result } = renderHook(() => useNodeHandlers(params))

    await act(async () => {
      void result.current.handleAutoLayout()
    })
    await act(async () => {
      void result.current.handleAutoLayout()
      void result.current.handleAutoLayout()
    })

    expect(getLayoutedElements).toHaveBeenCalledOnce()
    expect(params.setNodes).not.toHaveBeenCalled()

    await act(async () => {
      resolveLayout([n1])
    })

    expect(params.setNodes).toHaveBeenCalledOnce()
  })

  it("resets pending auto-layout state after a layout failure", async () => {
    const layoutError = new Error("ELK failed")
    vi.mocked(getLayoutedElements)
      .mockRejectedValueOnce(layoutError)
      .mockImplementationOnce(async (nodes: Node[]) => nodes)
    const params = makeParams()
    const n1 = makeNode("n1")
    params.graphRef.current = { nodes: [n1], edges: [] }
    const { result } = renderHook(() => useNodeHandlers(params))
    let thrown: unknown

    await act(async () => {
      try {
        await result.current.handleAutoLayout()
      } catch (error) {
        thrown = error
      }
    })

    expect(thrown).toBe(layoutError)
    expect(result.current.isAutoLayouting).toBe(false)
    expect(params.setNodes).not.toHaveBeenCalled()

    await act(async () => {
      await result.current.handleAutoLayout()
    })

    expect(getLayoutedElements).toHaveBeenCalledTimes(2)
    expect(params.setNodes).toHaveBeenCalledOnce()
  })

  it("handleAutoLayout does nothing with empty graph", async () => {
    const params = makeParams()
    params.graphRef.current = { nodes: [], edges: [] }
    const { result } = renderHook(() => useNodeHandlers(params))
    await act(async () => {
      await result.current.handleAutoLayout()
    })
    expect(params.setNodes).not.toHaveBeenCalled()
  })
})
