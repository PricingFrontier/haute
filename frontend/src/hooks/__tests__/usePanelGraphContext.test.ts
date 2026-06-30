import type { Edge, Node } from "@xyflow/react"
import { act, renderHook } from "@testing-library/react"
import { beforeEach, describe, expect, it } from "vitest"
import usePanelGraphContext from "../usePanelGraphContext"
import useGraphStore from "../../stores/useGraphStore"

function makeNode(id: string, label: string, position = { x: 0, y: 0 }): Node {
  return {
    id,
    type: "polars",
    position,
    data: {
      label,
      description: "",
      nodeType: "polars",
      config: {},
    },
  }
}

function makeEdge(id: string, source: string, target: string, extra: Partial<Edge> = {}): Edge {
  return { id, source, target, ...extra }
}

function setGraph(nodes: Node[], edges: Edge[]): void {
  const store = useGraphStore.getState()
  act(() => {
    store.setNodesRaw(nodes)
    store.setEdgesRaw(edges)
  })
}

describe("usePanelGraphContext", () => {
  beforeEach(() => {
    useGraphStore.setState({
      nodes: [],
      edges: [],
      preamble: "",
      structuralVersion: 0,
      structuralFingerprint: "nodes:||edges:||preamble:\"\"",
      panelContextVersion: 0,
      panelContextFingerprint: "nodes:||edges:",
    })
  })

  it("keeps panel snapshot identities stable for position-only updates when structuralVersion is unchanged", () => {
    const initialNode = makeNode("n1", "Node 1")
    const initialNodes = [initialNode]
    const initialEdges = [makeEdge("e1", "n1", "n2")]
    setGraph(initialNodes, initialEdges)

    const { result, rerender } = renderHook(() => usePanelGraphContext())

    const initialSnapshot = result.current
    const initialAllNodes = result.current.allNodes
    const initialPanelEdges = result.current.edges
    const initialNodeById = result.current.nodeById
    const initialPanelVersion = useGraphStore.getState().panelContextVersion

    act(() => {
      useGraphStore.getState().setNodesRaw([
        { ...initialNode, position: { x: 100, y: 200 } },
      ])
    })
    rerender()

    expect(useGraphStore.getState().panelContextVersion).toBe(initialPanelVersion)
    expect(result.current).toBe(initialSnapshot)
    expect(result.current.allNodes).toBe(initialAllNodes)
    expect(result.current.edges).toBe(initialPanelEdges)
    expect(result.current.nodeById).toBe(initialNodeById)
  })

  it("rebuilds the panel snapshot for preview-only columns without changing structuralVersion", () => {
    setGraph([
      {
        ...makeNode("n1", "Node 1"),
        data: {
          label: "Node 1",
          description: "",
          nodeType: "polars",
          config: {},
          _columns: [{ name: "old", dtype: "f64" }],
        },
      },
    ], [makeEdge("e1", "n1", "n2")])
    const { result, rerender } = renderHook(() => usePanelGraphContext())

    const initialSnapshot = result.current
    const initialStructuralVersion = useGraphStore.getState().structuralVersion
    const initialPanelVersion = useGraphStore.getState().panelContextVersion

    act(() => {
      useGraphStore.getState().setNodesRaw([
        {
          ...makeNode("n1", "Node 1"),
          data: {
            label: "Node 1",
            description: "",
            nodeType: "polars",
            config: {},
            _columns: [{ name: "new", dtype: "f64" }],
            _availableColumns: [{ name: "new", dtype: "f64" }],
            _schemaWarnings: [],
          },
        },
      ])
    })
    rerender()

    expect(useGraphStore.getState().structuralVersion).toBe(initialStructuralVersion)
    expect(useGraphStore.getState().panelContextVersion).toBeGreaterThan(initialPanelVersion)
    expect(result.current).not.toBe(initialSnapshot)
    expect(result.current.getNode("n1")?.data._columns).toEqual([{ name: "new", dtype: "f64" }])
  })

  it("rebuilds the panel snapshot on structuralVersion changes", () => {
    setGraph([makeNode("n1", "Before")], [makeEdge("e1", "n1", "n2")])
    const { result } = renderHook(() => usePanelGraphContext())

    const initialSnapshot = result.current

    act(() => {
      useGraphStore.getState().setNodesRaw([makeNode("n1", "After"), makeNode("n2", "Added")])
      useGraphStore.getState().setEdgesRaw([makeEdge("e2", "n2", "n1")])
    })

    expect(result.current).not.toBe(initialSnapshot)
    expect(result.current.allNodes.map((node) => node.data.label)).toEqual(["After", "Added"])
    expect(result.current.edges).toEqual([{ id: "e2", source: "n2", target: "n1" }])
  })

  it("preserves edge handle metadata for role-aware editors", () => {
    setGraph(
      [makeNode("source", "Source"), makeNode("edge_join_1", "Edge Join")],
      [
        makeEdge("e-source-join", "source", "edge_join_1", {
          sourceHandle: "result",
          targetHandle: "base",
        }),
      ],
    )

    const { result } = renderHook(() => usePanelGraphContext())

    expect(result.current.edges).toEqual([
      {
        id: "e-source-join",
        source: "source",
        target: "edge_join_1",
        sourceHandle: "result",
        targetHandle: "base",
      },
    ])
  })

  it("provides map-based active node lookup and returns null for missing ids", () => {
    setGraph([makeNode("n1", "Node 1"), makeNode("n2", "Node 2")], [])
    const { result } = renderHook(() => usePanelGraphContext())

    expect(result.current.getNode("n2")).toBe(result.current.nodeById.get("n2"))
    expect(result.current.getNode("missing")).toBeNull()
    expect(result.current.getNode(null)).toBeNull()
  })
})
