import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { renderHook, cleanup, act } from "@testing-library/react"
import type { Node, Edge } from "@xyflow/react"
import useEdgeHandlers from "../useEdgeHandlers"
import useToastStore from "../../stores/useToastStore"
import { NODE_TYPES } from "../../utils/nodeTypes"
import { DEFAULT_TARGET_HANDLE } from "../../utils/flowHandles"
import type { InternalNodeGeometry } from "../../utils/dropResolver"

function makeParams() {
  return {
    selectedNode: null as Node | null,
    graphRef: { current: { nodes: [] as Node[], edges: [] as Edge[] } },
    nodeIdCounter: { current: 0 },
    lastSelectedNodeRef: { current: null as Node | null },
    setNodes: vi.fn((updater: (nds: Node[]) => Node[]) => updater([])),
    setEdges: vi.fn((updater: (eds: Edge[]) => Edge[]) => updater([])),
    setNodesRaw: vi.fn(),
    setEdgesRaw: vi.fn(),
    pushSnapshot: vi.fn(),
    setSelectedNode: vi.fn(),
    setPreviewData: vi.fn(),
    setContextMenu: vi.fn(),
    fetchPreview: vi.fn(),
    cancelPreview: vi.fn(),
    shouldSkipAutomaticPreview: vi.fn(() => false),
    clearTrace: vi.fn(),
    screenToFlowPosition: vi.fn((pos: { x: number; y: number }) => pos),
    graphRefreshingRef: { current: 0 },
    findEdgeIdAtPoint: vi.fn(() => null as string | null),
    findNodeIdAtPoint: vi.fn(() => null as string | null),
    getInternalNode: vi.fn(() => undefined as InternalNodeGeometry | undefined),
    getZoom: vi.fn(() => 1),
  }
}

type HandleType = "source" | "target"

function connectionEndState({
  from,
  to,
  fromHandleId = null,
  toHandleId = null,
  fromHandleType = "source",
  toHandleType = "target",
  isValid = true,
}: {
  from: string
  to?: string | null
  fromHandleId?: string | null
  toHandleId?: string | null
  fromHandleType?: HandleType
  toHandleType?: HandleType
  isValid?: boolean | null
}) {
  return {
    isValid,
    fromNode: { id: from },
    fromHandle: { id: fromHandleId, type: fromHandleType },
    toNode: to ? { id: to } : null,
    toHandle: to ? { id: toHandleId, type: toHandleType } : null,
  } as never
}

const mouseUpEvent = { clientX: 200, clientY: 150 } as MouseEvent

describe("useEdgeHandlers", () => {
  afterEach(() => {
    cleanup()
    vi.useRealTimers()
    vi.restoreAllMocks()
  })

  it("onConnect waits for onConnectEnd so handle directions can be interpreted", () => {
    const params = makeParams()
    const { result } = renderHook(() => useEdgeHandlers(params))
    act(() => {
      result.current.onConnect({
        source: "a",
        target: "b",
        sourceHandle: null,
        targetHandle: null,
      })
    })
    expect(params.setEdges).not.toHaveBeenCalled()
    expect(params.setEdgesRaw).not.toHaveBeenCalled()
  })

  it("onConnectEnd creates a new edge for source-to-target handles", () => {
    const params = makeParams()
    const { result } = renderHook(() => useEdgeHandlers(params))
    act(() => {
      result.current.onConnectEnd(
        mouseUpEvent,
        connectionEndState({
          from: "a",
          to: "b",
          fromHandleId: "out",
          toHandleId: "in",
        }),
      )
    })
    expect(params.setEdges).toHaveBeenCalledOnce()
    const updater = params.setEdges.mock.calls[0][0] as (eds: Edge[]) => Edge[]
    expect(updater([])).toEqual([
      {
        id: "e_a_b_in_out",
        source: "a",
        target: "b",
        sourceHandle: "out",
        targetHandle: "in",
      },
    ])
  })

  it("onConnectEnd creates a new edge for target-to-source handles", () => {
    const params = makeParams()
    const { result } = renderHook(() => useEdgeHandlers(params))
    act(() => {
      result.current.onConnectEnd(
        mouseUpEvent,
        connectionEndState({
          from: "inputNode",
          to: "outputNode",
          fromHandleId: "in",
          toHandleId: "out",
          fromHandleType: "target",
          toHandleType: "source",
        }),
      )
    })
    expect(params.setEdges).toHaveBeenCalledOnce()
    const updater = params.setEdges.mock.calls[0][0] as (eds: Edge[]) => Edge[]
    const newEdges = updater([])
    expect(newEdges[0]).toMatchObject({
      source: "outputNode",
      target: "inputNode",
      sourceHandle: "out",
      targetHandle: "in",
    })
  })

  it("onConnectEnd prevents self-loop", () => {
    const params = makeParams()
    const { result } = renderHook(() => useEdgeHandlers(params))
    act(() => {
      result.current.onConnectEnd(mouseUpEvent, connectionEndState({ from: "a", to: "a" }))
    })
    expect(params.setEdges).not.toHaveBeenCalled()
  })

  it("onConnectEnd prevents duplicate edges", () => {
    const params = makeParams()
    params.graphRef.current.edges = [
      { id: "e1", source: "a", target: "b", sourceHandle: null, targetHandle: null } as Edge,
    ]
    const { result } = renderHook(() => useEdgeHandlers(params))
    act(() => {
      result.current.onConnectEnd(mouseUpEvent, connectionEndState({ from: "a", to: "b" }))
    })
    expect(params.setEdges).not.toHaveBeenCalled()
  })

  it("onConnectEnd preserves targetHandle for submodel nodes", () => {
    const params = makeParams()
    params.graphRef.current.nodes = [
      { id: "sm1", data: { label: "SM", nodeType: NODE_TYPES.SUBMODEL } } as unknown as Node,
    ]
    const { result } = renderHook(() => useEdgeHandlers(params))
    act(() => {
      result.current.onConnectEnd(
        mouseUpEvent,
        connectionEndState({ from: "a", to: "sm1", toHandleId: "in__child1" }),
      )
    })
    expect(params.setEdges).toHaveBeenCalledOnce()
    // targetHandle is preserved for submodel navigation
    const updater = params.setEdges.mock.calls[0][0] as (eds: Edge[]) => Edge[]
    const newEdges = updater([])
    expect(newEdges[0]).toHaveProperty("targetHandle", "in__child1")
  })

  it("onConnectEnd blocks when target node has reached maxInputs", () => {
    const params = makeParams()
    const expanderNode = {
      id: "exp1",
      data: { label: "Expander", nodeType: NODE_TYPES.SCENARIO_EXPANDER },
    } as unknown as Node
    params.graphRef.current.nodes = [expanderNode]
    params.graphRef.current.edges = [
      { id: "e1", source: "a", target: "exp1" } as Edge,
    ]
    const { result } = renderHook(() => useEdgeHandlers(params))
    act(() => {
      result.current.onConnectEnd(mouseUpEvent, connectionEndState({ from: "b", to: "exp1" }))
    })
    expect(params.setEdges).not.toHaveBeenCalled()
  })

  it("onConnectEnd blocks a second explore input", () => {
    const params = makeParams()
    const exploreNode = {
      id: "explore1",
      data: { label: "Explore", nodeType: NODE_TYPES.EXPLORE },
    } as unknown as Node
    params.graphRef.current.nodes = [exploreNode]
    params.graphRef.current.edges = [
      { id: "e1", source: "a", target: "explore1" } as Edge,
    ]
    const { result } = renderHook(() => useEdgeHandlers(params))
    act(() => {
      result.current.onConnectEnd(mouseUpEvent, connectionEndState({ from: "b", to: "explore1" }))
    })
    expect(params.setEdges).not.toHaveBeenCalled()
  })

  it("onConnectEnd allows connection when target has not reached maxInputs", () => {
    const params = makeParams()
    const expanderNode = {
      id: "exp1",
      data: { label: "Expander", nodeType: NODE_TYPES.SCENARIO_EXPANDER },
    } as unknown as Node
    params.graphRef.current.nodes = [expanderNode]
    params.graphRef.current.edges = []
    const { result } = renderHook(() => useEdgeHandlers(params))
    act(() => {
      result.current.onConnectEnd(mouseUpEvent, connectionEndState({ from: "a", to: "exp1" }))
    })
    expect(params.setEdges).toHaveBeenCalledOnce()
  })

  it("onConnectEnd allows multiple inputs for nodes without maxInputs", () => {
    const params = makeParams()
    const transformNode = {
      id: "t1",
      data: { label: "Transform", nodeType: NODE_TYPES.POLARS },
    } as unknown as Node
    params.graphRef.current.nodes = [transformNode]
    params.graphRef.current.edges = [
      { id: "e1", source: "a", target: "t1" } as Edge,
    ]
    const { result } = renderHook(() => useEdgeHandlers(params))
    act(() => {
      result.current.onConnectEnd(mouseUpEvent, connectionEndState({ from: "b", to: "t1" }))
    })
    expect(params.setEdges).toHaveBeenCalledOnce()
  })

  it.each([
    { label: "when it has no outgoing edge", existingEdges: [] as Edge[] },
    {
      label: "when it already has an outgoing edge",
      existingEdges: [
        { id: "e-join-existing", source: "join1", target: "existing", sourceHandle: null, targetHandle: "in" } as Edge,
      ],
    },
  ])("onConnectEnd allows dragging from an edgeJoin output $label", ({ existingEdges }) => {
    const params = makeParams()
    params.graphRef.current.nodes = [
      { id: "join1", data: { label: "Edge Join 1", nodeType: NODE_TYPES.EDGE_JOIN, config: {} } } as unknown as Node,
      { id: "existing", data: { label: "Existing", nodeType: NODE_TYPES.POLARS, config: {} } } as unknown as Node,
      { id: "target", data: { label: "Target", nodeType: NODE_TYPES.POLARS, config: {} } } as unknown as Node,
    ]
    params.graphRef.current.edges = existingEdges
    const { result } = renderHook(() => useEdgeHandlers(params))

    act(() => {
      result.current.onConnectEnd(
        mouseUpEvent,
        connectionEndState({ from: "join1", to: "target", toHandleId: "in" }),
      )
    })

    expect(params.setEdges).toHaveBeenCalledOnce()
    const updater = params.setEdges.mock.calls[0][0] as (eds: Edge[]) => Edge[]
    expect(updater(existingEdges)).toEqual([
      ...existingEdges,
      expect.objectContaining({
        source: "join1",
        target: "target",
        sourceHandle: null,
        targetHandle: "in",
      }),
    ])
  })

  it("onConnectEnd normalises the default input handle id before storing a normal edge", () => {
    const params = makeParams()
    params.graphRef.current.nodes = [
      { id: "join1", position: { x: 0, y: 0 }, data: { label: "Edge Join 1", nodeType: NODE_TYPES.EDGE_JOIN, config: {} } } as unknown as Node,
      {
        id: "target",
        position: { x: 300, y: 0 },
        measured: { width: 240, height: 70 },
        data: { label: "Target", nodeType: NODE_TYPES.POLARS, config: {} },
      } as unknown as Node,
    ]
    const { result } = renderHook(() => useEdgeHandlers(params))

    act(() => {
      result.current.onConnectEnd(
        mouseUpEvent,
        connectionEndState({
          from: "join1",
          to: "target",
          fromHandleType: "source",
          toHandleType: "target",
          toHandleId: DEFAULT_TARGET_HANDLE,
          isValid: true,
        }),
      )
    })

    expect(params.setNodesRaw).not.toHaveBeenCalled()
    expect(params.setEdgesRaw).not.toHaveBeenCalled()
    expect(params.setEdges).toHaveBeenCalledOnce()
    const updater = params.setEdges.mock.calls[0][0] as (eds: Edge[]) => Edge[]
    expect(updater([])).toEqual([
      expect.objectContaining({
        source: "join1",
        target: "target",
        sourceHandle: null,
        targetHandle: null,
      }),
    ])
  })

  it("onConnectEnd does not infer an input connection from a source-to-source ending", () => {
    const params = makeParams()
    params.graphRef.current.nodes = [
      { id: "join1", position: { x: 0, y: 0 }, data: { label: "Edge Join 1", nodeType: NODE_TYPES.EDGE_JOIN, config: {} } } as unknown as Node,
      {
        id: "target",
        position: { x: 300, y: 0 },
        measured: { width: 240, height: 70 },
        data: { label: "Target", nodeType: NODE_TYPES.POLARS, config: {} },
      } as unknown as Node,
    ]
    const { result } = renderHook(() => useEdgeHandlers(params))

    act(() => {
      result.current.onConnectEnd(
        { clientX: 320, clientY: 35 } as MouseEvent,
        connectionEndState({
          from: "join1",
          to: "target",
          fromHandleType: "source",
          toHandleType: "source",
          isValid: false,
        }),
      )
    })

    expect(params.setEdges).not.toHaveBeenCalled()
    expect(params.pushSnapshot).not.toHaveBeenCalled()
    expect(params.setNodesRaw).not.toHaveBeenCalled()
    expect(params.setEdgesRaw).not.toHaveBeenCalled()
  })

  it("onConnectEnd ignores invalid source-to-source drops on a Polars output side", () => {
    const params = makeParams()
    params.graphRef.current.nodes = [
      { id: "base", position: { x: 300, y: 0 }, measured: { width: 240, height: 70 }, data: { label: "Base", nodeType: NODE_TYPES.POLARS } } as unknown as Node,
      { id: "join1", position: { x: 0, y: 160 }, data: { label: "Edge Join 1", nodeType: NODE_TYPES.EDGE_JOIN } } as unknown as Node,
    ]
    const { result } = renderHook(() => useEdgeHandlers(params))

    act(() => {
      result.current.onConnectEnd(
        { clientX: 520, clientY: 35 } as MouseEvent,
        {
          isValid: false,
          fromNode: { id: "join1" },
          fromHandle: { id: null, type: "source" },
          toNode: { id: "base" },
          toHandle: { id: null, type: "source" },
        } as never,
      )
    })

    expect(params.setEdges).not.toHaveBeenCalled()
    expect(params.pushSnapshot).not.toHaveBeenCalled()
    expect(params.setNodesRaw).not.toHaveBeenCalled()
    expect(params.setEdgesRaw).not.toHaveBeenCalled()
  })

  it("onConnectEnd updates edgeJoin baseInput when connecting to the base handle", () => {
    const params = makeParams()
    params.graphRef.current.nodes = [
      { id: "join1", data: { label: "Edge Join 1", nodeType: NODE_TYPES.EDGE_JOIN, config: { baseInput: "old", joinInput: "lookup" } } } as unknown as Node,
      { id: "quotes", data: { label: "Quotes", nodeType: NODE_TYPES.POLARS, config: {} } } as unknown as Node,
    ]
    params.graphRef.current.edges = []
    const { result } = renderHook(() => useEdgeHandlers(params))

    act(() => {
      result.current.onConnectEnd(
        mouseUpEvent,
        connectionEndState({ from: "quotes", to: "join1", toHandleId: "base" }),
      )
    })

    expect(params.pushSnapshot).toHaveBeenCalledOnce()
    expect(params.setNodesRaw).toHaveBeenCalledOnce()
    expect(params.setEdgesRaw).toHaveBeenCalledOnce()
    const nextNodes = params.setNodesRaw.mock.calls[0][0] as Node[]
    expect(nextNodes.find((n) => n.id === "join1")?.data.config).toMatchObject({
      baseInput: "quotes",
      joinInput: "lookup",
    })
    const nextEdges = params.setEdgesRaw.mock.calls[0][0] as Edge[]
    expect(nextEdges).toEqual([
      expect.objectContaining({ source: "quotes", target: "join1", targetHandle: "base" }),
    ])
  })

  it("onConnectEnd normalises the bottom edgeJoin drop target to the join role", () => {
    const params = makeParams()
    params.graphRef.current.nodes = [
      { id: "join1", data: { label: "Edge Join 1", nodeType: NODE_TYPES.EDGE_JOIN, config: { baseInput: "base", joinInput: "removed" } } } as unknown as Node,
      { id: "base", data: { label: "Base", nodeType: NODE_TYPES.POLARS, config: {} } } as unknown as Node,
      { id: "lookup", data: { label: "Lookup", nodeType: NODE_TYPES.POLARS, config: {} } } as unknown as Node,
    ]
    params.graphRef.current.edges = [
      { id: "e-base-join", source: "base", target: "join1", targetHandle: "base" } as Edge,
    ]
    const { result } = renderHook(() => useEdgeHandlers(params))

    act(() => {
      result.current.onConnectEnd(
        mouseUpEvent,
        connectionEndState({ from: "lookup", to: "join1", toHandleId: "join-bottom" }),
      )
    })

    expect(params.pushSnapshot).toHaveBeenCalledOnce()
    expect(params.setNodesRaw).toHaveBeenCalledOnce()
    expect(params.setEdgesRaw).toHaveBeenCalledOnce()
    const nextNodes = params.setNodesRaw.mock.calls[0][0] as Node[]
    expect(nextNodes.find((n) => n.id === "join1")?.data.config).toMatchObject({
      baseInput: "base",
      joinInput: "lookup",
    })
    const nextEdges = params.setEdgesRaw.mock.calls[0][0] as Edge[]
    expect(nextEdges).toEqual(expect.arrayContaining([
      expect.objectContaining({ source: "base", target: "join1", targetHandle: "base" }),
      expect.objectContaining({ source: "lookup", target: "join1", targetHandle: "join" }),
    ]))
    expect(nextEdges).not.toEqual(expect.arrayContaining([
      expect.objectContaining({ targetHandle: "join-bottom" }),
    ]))
  })

  it("onConnectEnd rejects edgeJoin connections without a role target handle", () => {
    const params = makeParams()
    params.graphRef.current.nodes = [
      { id: "join1", data: { label: "Edge Join 1", nodeType: NODE_TYPES.EDGE_JOIN, config: {} } } as unknown as Node,
    ]
    const { result } = renderHook(() => useEdgeHandlers(params))

    act(() => {
      result.current.onConnectEnd(mouseUpEvent, connectionEndState({ from: "quotes", to: "join1" }))
    })

    expect(params.setEdges).not.toHaveBeenCalled()
    expect(params.setEdgesRaw).not.toHaveBeenCalled()
    expect(params.pushSnapshot).not.toHaveBeenCalled()
  })

  it("onConnectEnd rejects duplicate edgeJoin role connections", () => {
    const params = makeParams()
    params.graphRef.current.nodes = [
      { id: "join1", data: { label: "Edge Join 1", nodeType: NODE_TYPES.EDGE_JOIN, config: { baseInput: "a", joinInput: "b" } } } as unknown as Node,
    ]
    params.graphRef.current.edges = [
      { id: "e-a-join", source: "a", target: "join1", targetHandle: "base" } as Edge,
    ]
    const { result } = renderHook(() => useEdgeHandlers(params))

    act(() => {
      result.current.onConnectEnd(
        mouseUpEvent,
        connectionEndState({ from: "c", to: "join1", toHandleId: "base" }),
      )
    })

    expect(params.setEdges).not.toHaveBeenCalled()
    expect(params.setEdgesRaw).not.toHaveBeenCalled()
  })

  it("onConnectEnd rejects a third edgeJoin input", () => {
    const params = makeParams()
    params.graphRef.current.nodes = [
      { id: "join1", data: { label: "Edge Join 1", nodeType: NODE_TYPES.EDGE_JOIN, config: { baseInput: "a", joinInput: "b" } } } as unknown as Node,
    ]
    params.graphRef.current.edges = [
      { id: "e-a-join", source: "a", target: "join1", targetHandle: "base" } as Edge,
      { id: "e-b-join", source: "b", target: "join1", targetHandle: "join" } as Edge,
    ]
    const { result } = renderHook(() => useEdgeHandlers(params))

    act(() => {
      result.current.onConnectEnd(
        mouseUpEvent,
        connectionEndState({ from: "c", to: "join1", toHandleId: "join" }),
      )
    })

    expect(params.setEdges).not.toHaveBeenCalled()
    expect(params.setEdgesRaw).not.toHaveBeenCalled()
  })

  it("onConnectEnd inserts an edgeJoin node when a connection is dropped on an edge", () => {
    const params = makeParams()
    params.graphRef.current.nodes = [
      { id: "a", position: { x: 0, y: 0 }, data: { label: "Base", nodeType: NODE_TYPES.POLARS } } as unknown as Node,
      { id: "b", position: { x: 300, y: 0 }, data: { label: "Downstream", nodeType: NODE_TYPES.POLARS } } as unknown as Node,
      { id: "c", position: { x: 0, y: 160 }, data: { label: "Lookup", nodeType: NODE_TYPES.POLARS } } as unknown as Node,
    ]
    params.graphRef.current.edges = [
      { id: "e_ab", source: "a", target: "b", sourceHandle: "base_out", targetHandle: null } as Edge,
    ]
    params.findEdgeIdAtPoint.mockReturnValue("e_ab")
    const { result } = renderHook(() => useEdgeHandlers(params))

    act(() => {
      result.current.onConnectEnd(
        { clientX: 200, clientY: 150 } as MouseEvent,
        {
          isValid: null,
          fromNode: { id: "c" },
          fromHandle: { id: "lookup_out", type: "source" },
          toNode: null,
        } as never,
      )
    })

    expect(params.pushSnapshot).toHaveBeenCalledOnce()
    expect(params.setNodesRaw).toHaveBeenCalledOnce()
    expect(params.setEdgesRaw).toHaveBeenCalledOnce()
    expect(params.nodeIdCounter.current).toBe(1)

    const nextNodes = params.setNodesRaw.mock.calls[0][0] as Node[]
    const edgeJoin = nextNodes.find((node) => node.id === "edgeJoin_1")
    expect(edgeJoin).toMatchObject({
      id: "edgeJoin_1",
      type: NODE_TYPES.EDGE_JOIN,
      position: { x: 200, y: 150 },
      origin: [0.5, 0.5],
      data: {
        label: "Edge Join 1",
        nodeType: NODE_TYPES.EDGE_JOIN,
        config: {
          baseInput: "a",
          joinInput: "c",
          how: "left",
          suffix: "_right",
        },
      },
      selected: true,
    })
    expect(params.setSelectedNode).toHaveBeenCalledWith(edgeJoin)
    expect(params.lastSelectedNodeRef.current).toBe(edgeJoin)

    const nextEdges = params.setEdgesRaw.mock.calls[0][0] as Edge[]
    expect(nextEdges).toEqual(expect.arrayContaining([
      expect.objectContaining({
        source: "a",
        target: "edgeJoin_1",
        sourceHandle: "base_out",
        targetHandle: "base",
      }),
      expect.objectContaining({
        source: "edgeJoin_1",
        target: "b",
        targetHandle: null,
      }),
      expect.objectContaining({
        source: "c",
        target: "edgeJoin_1",
        sourceHandle: "lookup_out",
        targetHandle: "join",
      }),
    ]))
    expect(nextEdges).toHaveLength(3)
  })

  it("onConnectEnd ignores input-handle drops on an existing edge", () => {
    const params = makeParams()
    params.graphRef.current.nodes = [
      { id: "a", position: { x: 0, y: 0 }, data: { label: "Base", nodeType: NODE_TYPES.POLARS } } as unknown as Node,
      { id: "b", position: { x: 300, y: 0 }, data: { label: "Downstream", nodeType: NODE_TYPES.POLARS } } as unknown as Node,
      { id: "c", position: { x: 0, y: 160 }, data: { label: "Input", nodeType: NODE_TYPES.POLARS } } as unknown as Node,
    ]
    params.graphRef.current.edges = [
      { id: "e_ab", source: "a", target: "b" } as Edge,
    ]
    params.findEdgeIdAtPoint.mockReturnValue("e_ab")
    const { result } = renderHook(() => useEdgeHandlers(params))

    act(() => {
      result.current.onConnectEnd(
        { clientX: 200, clientY: 150 } as MouseEvent,
        {
          isValid: null,
          fromNode: { id: "c" },
          fromHandle: { id: "input", type: "target" },
          toNode: null,
        } as never,
      )
    })

    expect(params.findEdgeIdAtPoint).not.toHaveBeenCalled()
    expect(params.pushSnapshot).not.toHaveBeenCalled()
    expect(params.setNodesRaw).not.toHaveBeenCalled()
    expect(params.setEdgesRaw).not.toHaveBeenCalled()
  })

  it("onConnectEnd ignores invalid node-to-node endings when nothing is under the pointer", () => {
    const params = makeParams()
    const { result } = renderHook(() => useEdgeHandlers(params))

    act(() => {
      result.current.onConnectEnd(
        { clientX: 200, clientY: 150 } as MouseEvent,
        {
          isValid: false,
          fromNode: { id: "a" },
          fromHandle: { id: "out", type: "source" },
          toNode: { id: "b" },
          toHandle: { id: "in", type: "target" },
        } as never,
      )
    })

    expect(params.setEdges).not.toHaveBeenCalled()
    expect(params.pushSnapshot).not.toHaveBeenCalled()
    expect(params.setNodesRaw).not.toHaveBeenCalled()
    expect(params.setEdgesRaw).not.toHaveBeenCalled()
  })

  it("onConnectEnd lets an invalid snapped ending fall through to the exposed-edge splice", () => {
    // Behaviour change vs the shipped arbiter (which returned as soon as a
    // node had snapped): a snap rejected by isValidConnection no longer
    // swallows the gesture — with no node body under the pointer and a
    // visibly exposed edge there, the drop is the join-splice gesture.
    const params = makeParams()
    params.graphRef.current.nodes = [
      { id: "a", position: { x: 0, y: 0 }, data: { label: "Base", nodeType: NODE_TYPES.POLARS } } as unknown as Node,
      { id: "b", position: { x: 300, y: 0 }, data: { label: "Downstream", nodeType: NODE_TYPES.POLARS } } as unknown as Node,
      { id: "c", position: { x: 0, y: 160 }, data: { label: "Lookup", nodeType: NODE_TYPES.POLARS } } as unknown as Node,
    ]
    params.graphRef.current.edges = [
      { id: "e_ab", source: "a", target: "b", sourceHandle: null, targetHandle: null } as Edge,
    ]
    params.findEdgeIdAtPoint.mockReturnValue("e_ab")
    const { result } = renderHook(() => useEdgeHandlers(params))

    act(() => {
      result.current.onConnectEnd(
        { clientX: 200, clientY: 150 } as MouseEvent,
        {
          isValid: false,
          fromNode: { id: "c" },
          fromHandle: { id: "lookup_out", type: "source" },
          toNode: { id: "b" },
          toHandle: { id: "in", type: "target" },
        } as never,
      )
    })

    expect(params.setEdges).not.toHaveBeenCalled()
    expect(params.pushSnapshot).toHaveBeenCalledOnce()
    expect(params.setNodesRaw).toHaveBeenCalledOnce()
    const nextNodes = params.setNodesRaw.mock.calls[0][0] as Node[]
    expect(nextNodes.some((node) => node.id === "edgeJoin_1")).toBe(true)
  })

  it("onConnectEnd ignores target-to-target endings", () => {
    const params = makeParams()
    params.findEdgeIdAtPoint.mockReturnValue("e_ab")
    const { result } = renderHook(() => useEdgeHandlers(params))

    act(() => {
      result.current.onConnectEnd(
        { clientX: 200, clientY: 150 } as MouseEvent,
        connectionEndState({
          from: "a",
          to: "b",
          fromHandleType: "target",
          toHandleType: "target",
          isValid: true,
        }),
      )
    })

    expect(params.pushSnapshot).not.toHaveBeenCalled()
    expect(params.setEdges).not.toHaveBeenCalled()
    expect(params.setNodesRaw).not.toHaveBeenCalled()
    expect(params.setEdgesRaw).not.toHaveBeenCalled()
  })

  it("onConnectEnd leaves a cancelled connection alone when no edge is under the pointer", () => {
    const params = makeParams()
    params.graphRef.current.nodes = [
      { id: "a", position: { x: 0, y: 0 }, data: { label: "A", nodeType: NODE_TYPES.POLARS } } as unknown as Node,
      { id: "b", position: { x: 300, y: 0 }, data: { label: "B", nodeType: NODE_TYPES.POLARS } } as unknown as Node,
    ]
    params.graphRef.current.edges = [
      { id: "e_ab", source: "a", target: "b" } as Edge,
    ]
    params.findEdgeIdAtPoint.mockReturnValue(null)
    const { result } = renderHook(() => useEdgeHandlers(params))

    act(() => {
      result.current.onConnectEnd(
        { clientX: 200, clientY: 150 } as MouseEvent,
        {
          isValid: null,
          fromNode: { id: "a" },
          fromHandle: { id: null },
          toNode: null,
        } as never,
      )
    })

    expect(params.pushSnapshot).not.toHaveBeenCalled()
    expect(params.setNodesRaw).not.toHaveBeenCalled()
    expect(params.setEdgesRaw).not.toHaveBeenCalled()
  })

  describe("whole-node body drops (edge-targeting arm)", () => {
    const realAddToast = useToastStore.getState().addToast
    let toastSpy: ReturnType<typeof vi.fn<typeof realAddToast>>

    function installToastSpy() {
      toastSpy = vi.fn<typeof realAddToast>()
      useToastStore.setState({ addToast: toastSpy })
    }

    afterEach(() => {
      useToastStore.setState({ addToast: realAddToast })
    })

    /** A 240×70 consumer at flow x 300..540, y 0..70 with default connectors. */
    function consumerGeometry(): InternalNodeGeometry {
      return {
        internals: {
          positionAbsolute: { x: 300, y: 0 },
          handleBounds: {
            source: [{ id: null, x: 236, y: 31, width: 8, height: 8 }],
            target: [{ id: DEFAULT_TARGET_HANDLE, x: -4, y: 31, width: 8, height: 8 }],
          },
        },
        measured: { width: 240, height: 70 },
      }
    }

    function bodyDropParams(geometry: InternalNodeGeometry = consumerGeometry()) {
      const params = makeParams()
      params.graphRef.current.nodes = [
        { id: "a", position: { x: 0, y: 0 }, data: { label: "A", nodeType: NODE_TYPES.POLARS } } as unknown as Node,
        { id: "t1", position: { x: 300, y: 0 }, measured: { width: 240, height: 70 }, data: { label: "Target", nodeType: NODE_TYPES.POLARS, config: {} } } as unknown as Node,
      ]
      params.findNodeIdAtPoint.mockReturnValue("t1")
      params.getInternalNode.mockReturnValue(geometry)
      return params
    }

    /** Forward unsnapped ending from a's output connector. */
    const forwardEnding = {
      isValid: null,
      fromNode: { id: "a" },
      fromHandle: { id: "out", type: "source" },
      toNode: null,
    } as never

    it("connects a forward drag dropped on a node body to its input connector", () => {
      const params = bodyDropParams()
      const { result } = renderHook(() => useEdgeHandlers(params))

      act(() => {
        result.current.onConnectEnd({ clientX: 350, clientY: 35 } as MouseEvent, forwardEnding)
      })

      expect(params.setEdges).toHaveBeenCalledOnce()
      const updater = params.setEdges.mock.calls[0][0] as (eds: Edge[]) => Edge[]
      expect(updater([])).toEqual([
        expect.objectContaining({
          source: "a",
          target: "t1",
          sourceHandle: "out",
          targetHandle: null,
        }),
      ])
      // Node won: no edgeJoin splice was attempted.
      expect(params.setNodesRaw).not.toHaveBeenCalled()
    })

    it("body drop never persists the __default_target sentinel", () => {
      const params = bodyDropParams()
      const { result } = renderHook(() => useEdgeHandlers(params))

      act(() => {
        result.current.onConnectEnd({ clientX: 350, clientY: 35 } as MouseEvent, forwardEnding)
      })

      const updater = params.setEdges.mock.calls[0][0] as (eds: Edge[]) => Edge[]
      const stored = updater([])
      expect(stored[0].targetHandle).toBeNull()
      expect(stored.every((edge) => edge.targetHandle !== DEFAULT_TARGET_HANDLE)).toBe(true)
    })

    it("connects a backward drag dropped on a producer's body from its nearest output connector", () => {
      const params = bodyDropParams({
        internals: {
          positionAbsolute: { x: 300, y: 0 },
          handleBounds: {
            source: [
              { id: "quotes", x: 236, y: 19, width: 8, height: 8 },
              { id: "policies", x: 236, y: 43, width: 8, height: 8 },
            ],
            target: [],
          },
        },
        measured: { width: 240, height: 70 },
      })
      const { result } = renderHook(() => useEdgeHandlers(params))

      act(() => {
        result.current.onConnectEnd(
          { clientX: 400, clientY: 60 } as MouseEvent,
          {
            isValid: null,
            fromNode: { id: "a" },
            fromHandle: { id: DEFAULT_TARGET_HANDLE, type: "target" },
            toNode: null,
          } as never,
        )
      })

      expect(params.setEdges).toHaveBeenCalledOnce()
      const updater = params.setEdges.mock.calls[0][0] as (eds: Edge[]) => Edge[]
      expect(updater([])).toEqual([
        expect.objectContaining({
          source: "t1",
          target: "a",
          sourceHandle: "policies",
          targetHandle: null,
        }),
      ])
    })

    it("treats a forward drop in the output-end dead band as a silent no-op", () => {
      const params = bodyDropParams()
      installToastSpy()
      const { result } = renderHook(() => useEdgeHandlers(params))

      act(() => {
        // Band starts at 540 - 28 = 512 at full zoom.
        result.current.onConnectEnd({ clientX: 520, clientY: 35 } as MouseEvent, forwardEnding)
      })

      expect(params.setEdges).not.toHaveBeenCalled()
      expect(params.setNodesRaw).not.toHaveBeenCalled()
      expect(toastSpy).not.toHaveBeenCalled()
      // The node still claimed the drop: no hidden-edge splice probe.
      expect(params.findEdgeIdAtPoint).not.toHaveBeenCalled()
    })

    it("treats a backward drop in the input-end dead band as a silent no-op", () => {
      const params = bodyDropParams()
      installToastSpy()
      const { result } = renderHook(() => useEdgeHandlers(params))

      act(() => {
        // Band ends at 300 + 28 = 328 at full zoom.
        result.current.onConnectEnd(
          { clientX: 310, clientY: 35 } as MouseEvent,
          {
            isValid: null,
            fromNode: { id: "a" },
            fromHandle: { id: DEFAULT_TARGET_HANDLE, type: "target" },
            toNode: null,
          } as never,
        )
      })

      expect(params.setEdges).not.toHaveBeenCalled()
      expect(toastSpy).not.toHaveBeenCalled()
    })

    it("widens the dead band at compact zoom", () => {
      // x=506 is inside the compact band (540-36=504) but outside the full band (512).
      const compactParams = bodyDropParams()
      compactParams.getZoom.mockReturnValue(0.2)
      const compact = renderHook(() => useEdgeHandlers(compactParams))
      act(() => {
        compact.result.current.onConnectEnd({ clientX: 506, clientY: 35 } as MouseEvent, forwardEnding)
      })
      expect(compactParams.setEdges).not.toHaveBeenCalled()

      const fullParams = bodyDropParams()
      const full = renderHook(() => useEdgeHandlers(fullParams))
      act(() => {
        full.result.current.onConnectEnd({ clientX: 506, clientY: 35 } as MouseEvent, forwardEnding)
      })
      expect(fullParams.setEdges).toHaveBeenCalledOnce()
    })

    it("node wins over a hidden edge: a body drop never splices the edge underneath", () => {
      const params = bodyDropParams()
      params.graphRef.current.edges = [
        { id: "e_hidden", source: "x", target: "y", sourceHandle: null, targetHandle: null } as Edge,
      ]
      params.findEdgeIdAtPoint.mockReturnValue("e_hidden")
      const { result } = renderHook(() => useEdgeHandlers(params))

      act(() => {
        result.current.onConnectEnd({ clientX: 350, clientY: 35 } as MouseEvent, forwardEnding)
      })

      expect(params.setEdges).toHaveBeenCalledOnce()
      expect(params.setNodesRaw).not.toHaveBeenCalled()
      expect(params.findEdgeIdAtPoint).not.toHaveBeenCalled()
    })

    it("output-onto-output drop creates no join node and falls through to the body arm", () => {
      // Rollback: the output-onto-output edge-join gesture was removed, so a
      // source→source ending never creates a join. xyflow reports a snapped
      // source connector; with the pointer over the target's body the drop
      // falls through to the body connect.
      const params = bodyDropParams()
      const { result } = renderHook(() => useEdgeHandlers(params))

      act(() => {
        result.current.onConnectEnd(
          { clientX: 350, clientY: 35 } as MouseEvent,
          {
            isValid: true,
            fromNode: { id: "a" },
            fromHandle: { id: "out", type: "source" },
            toNode: { id: "t1" },
            toHandle: { id: null, type: "source" },
          } as never,
        )
      })

      expect(params.setNodesRaw).not.toHaveBeenCalled()
      expect(params.setEdges).toHaveBeenCalledOnce()
      const updater = params.setEdges.mock.calls[0][0] as (eds: Edge[]) => Edge[]
      expect(updater([])[0]).toMatchObject({ source: "a", target: "t1", targetHandle: null })
    })

    it("output-onto-output drop into the output-end dead band is a silent no-op", () => {
      // Rollback: with the join gesture gone, an output→output drop landing in
      // the target's output-end dead band is the intended silent no-op.
      const params = bodyDropParams()
      installToastSpy()
      const { result } = renderHook(() => useEdgeHandlers(params))

      act(() => {
        result.current.onConnectEnd(
          { clientX: 520, clientY: 35 } as MouseEvent,
          {
            isValid: true,
            fromNode: { id: "a" },
            fromHandle: { id: "out", type: "source" },
            toNode: { id: "t1" },
            toHandle: { id: null, type: "source" },
          } as never,
        )
      })

      expect(params.setNodesRaw).not.toHaveBeenCalled()
      expect(params.setEdges).not.toHaveBeenCalled()
      expect(toastSpy).not.toHaveBeenCalled()
    })

    /** The 40×34 edge-join marker at flow 0..40 × 0..34 with 2×2 connectors. */
    function edgeJoinGeometry(): InternalNodeGeometry {
      return {
        internals: {
          positionAbsolute: { x: 0, y: 0 },
          handleBounds: {
            source: [{ id: null, x: 35, y: 16, width: 2, height: 2 }],
            target: [
              { id: "base", x: 3, y: 16, width: 2, height: 2 },
              { id: "join", x: 19, y: 5, width: 2, height: 2 },
            ],
          },
        },
        measured: { width: 40, height: 34 },
      }
    }

    function edgeJoinParams() {
      const params = makeParams()
      params.graphRef.current.nodes = [
        { id: "j1", position: { x: 0, y: 0 }, data: { label: "Edge Join 1", nodeType: NODE_TYPES.EDGE_JOIN, config: { baseInput: "a" } } } as unknown as Node,
        { id: "a", position: { x: -300, y: 0 }, data: { label: "A", nodeType: NODE_TYPES.POLARS } } as unknown as Node,
        { id: "c", position: { x: -300, y: 160 }, data: { label: "C", nodeType: NODE_TYPES.POLARS } } as unknown as Node,
      ]
      params.graphRef.current.edges = [
        { id: "e_aj", source: "a", target: "j1", sourceHandle: null, targetHandle: "base" } as Edge,
      ]
      params.findNodeIdAtPoint.mockReturnValue("j1")
      params.getInternalNode.mockReturnValue(edgeJoinGeometry())
      return params
    }

    it("edgeJoin body drop resolves the nearest role regardless of occupancy and toasts when occupied", () => {
      const params = edgeJoinParams()
      installToastSpy()
      const { result } = renderHook(() => useEdgeHandlers(params))

      act(() => {
        // Nearest the occupied base connector (centre 4,17) — ruling 5:
        // nearest wins even though join is free; the occupied role toasts.
        result.current.onConnectEnd(
          { clientX: 5, clientY: 20 } as MouseEvent,
          {
            isValid: null,
            fromNode: { id: "c" },
            fromHandle: { id: null, type: "source" },
            toNode: null,
          } as never,
        )
      })

      expect(toastSpy).toHaveBeenCalledWith("error", "Edge join already has a base input")
      expect(params.setEdges).not.toHaveBeenCalled()
      expect(params.setEdgesRaw).not.toHaveBeenCalled()
    })

    it("edgeJoin body drop connects the free join role when it is nearest", () => {
      const params = edgeJoinParams()
      const { result } = renderHook(() => useEdgeHandlers(params))

      act(() => {
        // Nearest the join connector (centre 20,6); dead band is clamped
        // to 10px (25% of the 40px root), so x=20 is in the connect zone.
        result.current.onConnectEnd(
          { clientX: 20, clientY: 4 } as MouseEvent,
          {
            isValid: null,
            fromNode: { id: "c" },
            fromHandle: { id: null, type: "source" },
            toNode: null,
          } as never,
        )
      })

      expect(params.pushSnapshot).toHaveBeenCalledOnce()
      const nextEdges = params.setEdgesRaw.mock.calls[0][0] as Edge[]
      expect(nextEdges).toEqual(expect.arrayContaining([
        expect.objectContaining({ source: "c", target: "j1", targetHandle: "join" }),
      ]))
      const nextNodes = params.setNodesRaw.mock.calls[0][0] as Node[]
      expect(nextNodes.find((n) => n.id === "j1")?.data.config).toMatchObject({
        baseInput: "a",
        joinInput: "c",
      })
    })

    it("edgeJoin body drop with two existing inputs toasts the exactly-two rule", () => {
      const params = edgeJoinParams()
      params.graphRef.current.edges = [
        { id: "e_1", source: "a", target: "j1", sourceHandle: null, targetHandle: "join" } as Edge,
        { id: "e_2", source: "x", target: "j1", sourceHandle: null, targetHandle: "join" } as Edge,
      ]
      installToastSpy()
      const { result } = renderHook(() => useEdgeHandlers(params))

      act(() => {
        // Nearest the free base role, but the join node already has two inputs.
        result.current.onConnectEnd(
          { clientX: 5, clientY: 20 } as MouseEvent,
          {
            isValid: null,
            fromNode: { id: "c" },
            fromHandle: { id: null, type: "source" },
            toNode: null,
          } as never,
        )
      })

      expect(toastSpy).toHaveBeenCalledWith("error", "Edge join nodes accept exactly two inputs")
      expect(params.setEdgesRaw).not.toHaveBeenCalled()
    })

    it("a drag from an edgeJoin output dropped on a node body makes a plain edge", () => {
      const params = bodyDropParams()
      params.graphRef.current.nodes.push(
        { id: "j1", position: { x: 0, y: 160 }, data: { label: "Edge Join 1", nodeType: NODE_TYPES.EDGE_JOIN, config: {} } } as unknown as Node,
      )
      const { result } = renderHook(() => useEdgeHandlers(params))

      act(() => {
        result.current.onConnectEnd(
          { clientX: 350, clientY: 35 } as MouseEvent,
          {
            isValid: null,
            fromNode: { id: "j1" },
            fromHandle: { id: null, type: "source" },
            toNode: null,
          } as never,
        )
      })

      expect(params.setEdges).toHaveBeenCalledOnce()
      const updater = params.setEdges.mock.calls[0][0] as (eds: Edge[]) => Edge[]
      expect(updater([])[0]).toMatchObject({
        source: "j1",
        target: "t1",
        sourceHandle: null,
        targetHandle: null,
      })
    })

    it("a forward drop on a source-only node body is a no-op even with an edge underneath", () => {
      const params = bodyDropParams({
        internals: {
          positionAbsolute: { x: 300, y: 0 },
          handleBounds: {
            source: [{ id: null, x: 236, y: 31, width: 8, height: 8 }],
            target: [],
          },
        },
        measured: { width: 240, height: 70 },
      })
      params.findEdgeIdAtPoint.mockReturnValue("e_hidden")
      const { result } = renderHook(() => useEdgeHandlers(params))

      act(() => {
        result.current.onConnectEnd({ clientX: 350, clientY: 35 } as MouseEvent, forwardEnding)
      })

      expect(params.setEdges).not.toHaveBeenCalled()
      expect(params.setNodesRaw).not.toHaveBeenCalled()
      expect(params.findEdgeIdAtPoint).not.toHaveBeenCalled()
    })

    it("never resolves to an empty-string handle", () => {
      const params = bodyDropParams({
        internals: {
          positionAbsolute: { x: 300, y: 0 },
          handleBounds: {
            // Pathological rendered id — must be normalised, never stored.
            source: [{ id: "", x: 236, y: 31, width: 8, height: 8 }],
            target: [],
          },
        },
        measured: { width: 240, height: 70 },
      })
      const { result } = renderHook(() => useEdgeHandlers(params))

      act(() => {
        result.current.onConnectEnd(
          { clientX: 400, clientY: 35 } as MouseEvent,
          {
            isValid: null,
            fromNode: { id: "a" },
            fromHandle: { id: DEFAULT_TARGET_HANDLE, type: "target" },
            toNode: null,
          } as never,
        )
      })

      expect(params.setEdges).toHaveBeenCalledOnce()
      const updater = params.setEdges.mock.calls[0][0] as (eds: Edge[]) => Edge[]
      const stored = updater([])
      expect(stored[0].sourceHandle).toBeNull()
      expect(stored.every((e) => e.sourceHandle !== "" && e.targetHandle !== "")).toBe(true)
    })

    it("ignores a body drop when the node cannot be measured, without falling through to the splice", () => {
      const params = makeParams()
      params.findNodeIdAtPoint.mockReturnValue("t1")
      params.getInternalNode.mockReturnValue(undefined)
      params.findEdgeIdAtPoint.mockReturnValue("e_hidden")
      const { result } = renderHook(() => useEdgeHandlers(params))

      act(() => {
        result.current.onConnectEnd({ clientX: 350, clientY: 35 } as MouseEvent, forwardEnding)
      })

      expect(params.setEdges).not.toHaveBeenCalled()
      expect(params.setNodesRaw).not.toHaveBeenCalled()
      expect(params.findEdgeIdAtPoint).not.toHaveBeenCalled()
    })

    it("ignores a body drop when the drag has no usable from-handle type", () => {
      const params = bodyDropParams()
      const { result } = renderHook(() => useEdgeHandlers(params))

      act(() => {
        result.current.onConnectEnd(
          { clientX: 350, clientY: 35 } as MouseEvent,
          {
            isValid: null,
            fromNode: { id: "a" },
            fromHandle: { id: null },
            toNode: null,
          } as never,
        )
      })

      expect(params.getInternalNode).not.toHaveBeenCalled()
      expect(params.setEdges).not.toHaveBeenCalled()
    })
  })

  describe("onConnectEnd arbiter branch coverage", () => {
    it("ignores a connection whose fromNode is missing (no source node id)", () => {
      // L228 guard: a drag with no originating node can never produce an
      // edge — the arbiter must bail before touching the graph.
      const params = makeParams()
      params.findEdgeIdAtPoint.mockReturnValue("e_ab")
      const { result } = renderHook(() => useEdgeHandlers(params))

      act(() => {
        result.current.onConnectEnd(
          mouseUpEvent,
          {
            isValid: null,
            fromNode: null,
            fromHandle: { id: "out", type: "source" },
            toNode: null,
          } as never,
        )
      })

      expect(params.setEdges).not.toHaveBeenCalled()
      expect(params.setNodesRaw).not.toHaveBeenCalled()
      expect(params.setEdgesRaw).not.toHaveBeenCalled()
      // Bailed before the splice probe.
      expect(params.findEdgeIdAtPoint).not.toHaveBeenCalled()
    })

    it("Arm 2 backward snap tolerates a null toHandle id on the source side", () => {
      // L304: the backward arm's `sourceHandle: toHandle.id ?? null`
      // fallback — a snapped target->source connect where the snapped
      // source connector reports no id.
      const params = makeParams()
      const { result } = renderHook(() => useEdgeHandlers(params))

      act(() => {
        result.current.onConnectEnd(
          mouseUpEvent,
          connectionEndState({
            from: "inputNode",
            to: "producer",
            fromHandleId: "in",
            toHandleId: null,
            fromHandleType: "target",
            toHandleType: "source",
            isValid: true,
          }),
        )
      })

      expect(params.setEdges).toHaveBeenCalledOnce()
      const updater = params.setEdges.mock.calls[0][0] as (eds: Edge[]) => Edge[]
      expect(updater([])[0]).toMatchObject({
        source: "producer",
        target: "inputNode",
        sourceHandle: null,
        targetHandle: "in",
      })
    })

    it("Arm 4 splice tolerates a null fromHandle id on the spliced source", () => {
      // L362: `connectionState.fromHandle?.id ?? null` — a forward drag
      // with a null rendered output id dropped on an exposed edge.
      const params = makeParams()
      params.graphRef.current.nodes = [
        { id: "a", position: { x: 0, y: 0 }, data: { label: "Base", nodeType: NODE_TYPES.POLARS } } as unknown as Node,
        { id: "b", position: { x: 300, y: 0 }, data: { label: "Downstream", nodeType: NODE_TYPES.POLARS } } as unknown as Node,
        { id: "c", position: { x: 0, y: 160 }, data: { label: "Lookup", nodeType: NODE_TYPES.POLARS } } as unknown as Node,
      ]
      params.graphRef.current.edges = [
        { id: "e_ab", source: "a", target: "b", sourceHandle: null, targetHandle: null } as Edge,
      ]
      params.findEdgeIdAtPoint.mockReturnValue("e_ab")
      const { result } = renderHook(() => useEdgeHandlers(params))

      act(() => {
        result.current.onConnectEnd(
          { clientX: 200, clientY: 150 } as MouseEvent,
          {
            isValid: null,
            fromNode: { id: "c" },
            fromHandle: { id: null, type: "source" },
            toNode: null,
          } as never,
        )
      })

      expect(params.pushSnapshot).toHaveBeenCalledOnce()
      const nextEdges = params.setEdgesRaw.mock.calls[0][0] as Edge[]
      expect(nextEdges).toEqual(expect.arrayContaining([
        expect.objectContaining({ source: "c", target: "edgeJoin_1", sourceHandle: null, targetHandle: "join" }),
      ]))
    })

    it("throws when a touch-ending connection carries no pointer coordinates", () => {
      // L68-69: connectionEndPoint on a TouchEvent with empty touches AND
      // empty changedTouches has nowhere to read the drop position from.
      const params = makeParams()
      const { result } = renderHook(() => useEdgeHandlers(params))

      expect(() => {
        act(() => {
          result.current.onConnectEnd(
            { touches: [], changedTouches: [] } as unknown as TouchEvent,
            {
              isValid: null,
              fromNode: { id: "a" },
              fromHandle: { id: "out", type: "source" },
              toNode: null,
            } as never,
          )
        })
      }).toThrow(/pointer coordinates/)
    })

    it("reads the drop position from changedTouches on a touch-ending splice", () => {
      // L67/L71: connectionEndPoint's touch branch — a TouchEvent with no
      // active touches reads the drop point from changedTouches[0]. Drive it
      // through the exposed-edge splice (Arm 4) so the read position lands on
      // the inserted edgeJoin node.
      const params = makeParams()
      params.graphRef.current.nodes = [
        { id: "a", position: { x: 0, y: 0 }, data: { label: "Base", nodeType: NODE_TYPES.POLARS } } as unknown as Node,
        { id: "b", position: { x: 300, y: 0 }, data: { label: "Downstream", nodeType: NODE_TYPES.POLARS } } as unknown as Node,
        { id: "c", position: { x: 0, y: 160 }, data: { label: "Lookup", nodeType: NODE_TYPES.POLARS } } as unknown as Node,
      ]
      params.graphRef.current.edges = [
        { id: "e_ab", source: "a", target: "b", sourceHandle: "base_out", targetHandle: null } as Edge,
      ]
      params.findEdgeIdAtPoint.mockReturnValue("e_ab")
      const { result } = renderHook(() => useEdgeHandlers(params))

      act(() => {
        result.current.onConnectEnd(
          {
            touches: [],
            changedTouches: [{ clientX: 240, clientY: 180 }],
          } as unknown as TouchEvent,
          {
            isValid: null,
            fromNode: { id: "c" },
            fromHandle: { id: "lookup_out", type: "source" },
            toNode: null,
          } as never,
        )
      })

      expect(params.findEdgeIdAtPoint).toHaveBeenCalledWith({ x: 240, y: 180 })
      const nextNodes = params.setNodesRaw.mock.calls[0][0] as Node[]
      expect(nextNodes.find((node) => node.id === "edgeJoin_1")?.position).toEqual({
        x: 240,
        y: 180,
      })
    })

    it("uses the default no-op findEdgeIdAtPoint when the param is omitted", () => {
      // L136 default: omitting findEdgeIdAtPoint must not throw — the
      // built-in `() => null` makes a forward drag onto empty canvas a
      // silent no-op.
      const params = makeParams()
      const { findEdgeIdAtPoint: _omit, ...rest } = params
      const { result } = renderHook(() => useEdgeHandlers(rest))

      act(() => {
        result.current.onConnectEnd(
          { clientX: 200, clientY: 150 } as MouseEvent,
          {
            isValid: null,
            fromNode: { id: "a" },
            fromHandle: { id: "out", type: "source" },
            toNode: null,
          } as never,
        )
      })

      expect(params.setEdges).not.toHaveBeenCalled()
      expect(params.setNodesRaw).not.toHaveBeenCalled()
      expect(params.setEdgesRaw).not.toHaveBeenCalled()
    })

    it("commits an edgeJoin role connection when the target node has no config object", () => {
      // L181: `nodeData(node).config ?? {}` — an edge-join node whose data
      // omits `config` entirely must still accept a base-role connection.
      const params = makeParams()
      params.graphRef.current.nodes = [
        { id: "join1", data: { label: "Edge Join 1", nodeType: NODE_TYPES.EDGE_JOIN } } as unknown as Node,
        { id: "quotes", data: { label: "Quotes", nodeType: NODE_TYPES.POLARS } } as unknown as Node,
      ]
      params.graphRef.current.edges = []
      const { result } = renderHook(() => useEdgeHandlers(params))

      act(() => {
        result.current.onConnectEnd(
          mouseUpEvent,
          connectionEndState({ from: "quotes", to: "join1", toHandleId: "base" }),
        )
      })

      expect(params.pushSnapshot).toHaveBeenCalledOnce()
      expect(params.setNodesRaw).toHaveBeenCalledOnce()
      const nextNodes = params.setNodesRaw.mock.calls[0][0] as Node[]
      expect(nextNodes.find((n) => n.id === "join1")?.data.config).toMatchObject({
        baseInput: "quotes",
      })
    })
  })

  it("onDragOver prevents default and sets the move drop effect", () => {
    // L442-443.
    const params = makeParams()
    const { result } = renderHook(() => useEdgeHandlers(params))
    const dataTransfer = { dropEffect: "" }
    const event = {
      preventDefault: vi.fn(),
      dataTransfer,
    } as unknown as React.DragEvent

    act(() => {
      result.current.onDragOver(event)
    })

    expect(event.preventDefault).toHaveBeenCalledOnce()
    expect(dataTransfer.dropEffect).toBe("move")
  })

  it("onDrop reports a non-Error JSON failure via String(err)", () => {
    // L466 false branch: when JSON.parse throws something that is not an
    // Error instance, the message is derived via String(err).
    const params = makeParams()
    const realAddToast = useToastStore.getState().addToast
    const toastSpy = vi.fn<typeof realAddToast>()
    useToastStore.setState({ addToast: toastSpy })
    vi.spyOn(JSON, "parse").mockImplementation(() => {
      throw "boom-not-an-error"
    })
    const { result } = renderHook(() => useEdgeHandlers(params))
    const event = {
      preventDefault: vi.fn(),
      clientX: 10,
      clientY: 20,
      dataTransfer: {
        getData: vi.fn((key: string) => {
          if (key === "application/reactflow-type") return NODE_TYPES.POLARS
          if (key === "application/reactflow-config") return "{}"
          return ""
        }),
      },
    } as unknown as React.DragEvent

    act(() => {
      result.current.onDrop(event)
    })

    expect(toastSpy).toHaveBeenCalledWith(
      "error",
      "Drop rejected: invalid node config JSON (boom-not-an-error)",
    )
    expect(params.setNodes).not.toHaveBeenCalled()
    useToastStore.setState({ addToast: realAddToast })
  })

  it("onSelectionChange with single node does NOT open panel (drag-safe)", () => {
    const params = makeParams()
    const node = { id: "n1", position: { x: 0, y: 0 }, data: { label: "A" } } as Node
    const { result } = renderHook(() => useEdgeHandlers(params))
    act(() => {
      result.current.onSelectionChange({ nodes: [node], edges: [] })
    })
    // Panel opening moved to onNodeClick — selection alone should not trigger it
    expect(params.fetchPreview).not.toHaveBeenCalled()
    expect(params.setSelectedNode).not.toHaveBeenCalled()
  })

  it("onSelectionChange with no nodes sets selectedNode to null", () => {
    const params = makeParams()
    const { result } = renderHook(() => useEdgeHandlers(params))
    act(() => {
      result.current.onSelectionChange({ nodes: [], edges: [] })
    })
    expect(params.setSelectedNode).toHaveBeenCalledWith(null)
    expect(params.clearTrace).toHaveBeenCalled()
  })

  it("onSelectionChange skips deselection when graphRefreshingRef is true", () => {
    const params = makeParams()
    params.graphRefreshingRef.current = 1
    const { result } = renderHook(() => useEdgeHandlers(params))
    act(() => {
      result.current.onSelectionChange({ nodes: [], edges: [] })
    })
    expect(params.setSelectedNode).not.toHaveBeenCalled()
    expect(params.clearTrace).not.toHaveBeenCalled()
  })

  it("onNodeClick opens panel and fetches preview", () => {
    const params = makeParams()
    const node = { id: "n1", position: { x: 0, y: 0 }, data: { label: "A" } } as Node
    const event = {} as React.MouseEvent
    const { result } = renderHook(() => useEdgeHandlers(params))
    act(() => {
      result.current.onNodeClick(event, node)
    })
    expect(params.setSelectedNode).toHaveBeenCalledWith(node)
    expect(params.clearTrace).toHaveBeenCalled()
    expect(params.cancelPreview).toHaveBeenCalledOnce()
    expect(params.lastSelectedNodeRef.current).toBe(node)
    expect(params.fetchPreview).toHaveBeenCalledWith(node, {})
  })

  it("onNodeClick fetches optimiser preview with an idle delay", () => {
    const params = makeParams()
    const node = {
      id: "optimiser1",
      position: { x: 0, y: 0 },
      data: { label: "Optimiser", nodeType: NODE_TYPES.OPTIMISER },
    } as Node
    const event = {} as React.MouseEvent

    const { result } = renderHook(() => useEdgeHandlers(params))
    act(() => {
      result.current.onNodeClick(event, node)
    })

    expect(params.setSelectedNode).toHaveBeenCalledWith(node)
    expect(params.clearTrace).toHaveBeenCalled()
    expect(params.cancelPreview).toHaveBeenCalledOnce()
    expect(params.lastSelectedNodeRef.current).toBe(node)
    expect(params.fetchPreview).toHaveBeenCalledWith(node, {
      debounceMs: 800,
    })
  })

  it("onNodeClick fetches preview for modelling nodes so training field data loads", () => {
    const params = makeParams()
    const node = {
      id: "modelling1",
      position: { x: 0, y: 0 },
      data: { label: "Conversion", nodeType: NODE_TYPES.MODELLING },
    } as Node
    const event = {} as React.MouseEvent

    const { result } = renderHook(() => useEdgeHandlers(params))
    act(() => {
      result.current.onNodeClick(event, node)
    })

    expect(params.setSelectedNode).toHaveBeenCalledWith(node)
    expect(params.clearTrace).toHaveBeenCalled()
    expect(params.cancelPreview).toHaveBeenCalledOnce()
    expect(params.lastSelectedNodeRef.current).toBe(node)
    expect(params.fetchPreview).toHaveBeenCalledWith(node, {})
  })

  it("onNodeClick fetches preview for explore nodes so prepared rows load", () => {
    const params = makeParams()
    const node = {
      id: "explore1",
      position: { x: 0, y: 0 },
      data: { label: "Explore", nodeType: NODE_TYPES.EXPLORE },
    } as Node
    const event = {} as React.MouseEvent

    const { result } = renderHook(() => useEdgeHandlers(params))
    act(() => {
      result.current.onNodeClick(event, node)
    })

    expect(params.setSelectedNode).toHaveBeenCalledWith(node)
    expect(params.clearTrace).toHaveBeenCalled()
    expect(params.cancelPreview).toHaveBeenCalledOnce()
    expect(params.lastSelectedNodeRef.current).toBe(node)
    expect(params.fetchPreview).toHaveBeenCalledWith(node, {})
  })

  it.each([
    NODE_TYPES.DATA_SINK,
    NODE_TYPES.OUTPUT,
    NODE_TYPES.SUBMODEL,
    NODE_TYPES.SUBMODEL_PORT,
  ])("onNodeClick skips automatic preview for non-previewable node type %s", (nodeType) => {
    const params = makeParams()
    const node = {
      id: "sink1",
      position: { x: 0, y: 0 },
      data: { label: "Sink", nodeType },
    } as Node
    const event = {} as React.MouseEvent

    const { result } = renderHook(() => useEdgeHandlers(params))
    act(() => {
      result.current.onNodeClick(event, node)
    })

    expect(params.setSelectedNode).toHaveBeenCalledWith(node)
    expect(params.clearTrace).toHaveBeenCalled()
    expect(params.cancelPreview).toHaveBeenCalledOnce()
    expect(params.lastSelectedNodeRef.current).toBe(node)
    expect(params.setPreviewData).toHaveBeenCalledWith(null)
    expect(params.fetchPreview).not.toHaveBeenCalled()
  })

  it("onNodeClick skips automatic preview for submodel port nodes typed by React Flow", () => {
    const params = makeParams()
    const node = {
      id: "port_in__source",
      type: NODE_TYPES.SUBMODEL_PORT,
      position: { x: 0, y: 0 },
      data: { label: "Source Port", portDirection: "input", portName: "Source Port" },
    } as unknown as Node
    const event = {} as React.MouseEvent

    const { result } = renderHook(() => useEdgeHandlers(params))
    act(() => {
      result.current.onNodeClick(event, node)
    })

    expect(params.setSelectedNode).toHaveBeenCalledWith(node)
    expect(params.clearTrace).toHaveBeenCalled()
    expect(params.cancelPreview).toHaveBeenCalledOnce()
    expect(params.lastSelectedNodeRef.current).toBe(node)
    expect(params.setPreviewData).toHaveBeenCalledWith(null)
    expect(params.fetchPreview).not.toHaveBeenCalled()
  })

  it("onNodeClick skips automatic preview when a result panel will render", () => {
    const params = makeParams()
    params.shouldSkipAutomaticPreview.mockReturnValue(true)
    const node = {
      id: "optimiser1",
      position: { x: 0, y: 0 },
      data: { label: "Optimiser", nodeType: NODE_TYPES.OPTIMISER },
    } as Node
    const event = {} as React.MouseEvent

    const { result } = renderHook(() => useEdgeHandlers(params))
    act(() => {
      result.current.onNodeClick(event, node)
    })

    expect(params.setSelectedNode).toHaveBeenCalledWith(node)
    expect(params.clearTrace).toHaveBeenCalled()
    expect(params.cancelPreview).toHaveBeenCalledOnce()
    expect(params.fetchPreview).not.toHaveBeenCalled()
  })

  it("onNodeClick skips fetchPreview when re-clicking the same node", () => {
    const params = makeParams()
    const node = { id: "n1", position: { x: 0, y: 0 }, data: { label: "A" } } as Node
    params.selectedNode = node
    const event = {} as React.MouseEvent
    const { result } = renderHook(() => useEdgeHandlers(params))
    act(() => {
      result.current.onNodeClick(event, node)
    })
    expect(params.setSelectedNode).toHaveBeenCalledWith(node)
    expect(params.fetchPreview).not.toHaveBeenCalled()
    expect(params.clearTrace).not.toHaveBeenCalled()
    expect(params.cancelPreview).not.toHaveBeenCalled()
  })

  it("onNodeClick skips duplicate fetch before React re-renders selection props", () => {
    const params = makeParams()
    const node = { id: "n1", position: { x: 0, y: 0 }, data: { label: "A" } } as Node
    const event = {} as React.MouseEvent

    const { result } = renderHook(() => useEdgeHandlers(params))
    act(() => {
      result.current.onNodeClick(event, node)
      result.current.onNodeClick(event, node)
    })

    expect(params.fetchPreview).toHaveBeenCalledOnce()
  })

  it("onNodeClick fetches rapid distinct selections immediately", () => {
    const params = makeParams()
    const first = { id: "n1", position: { x: 0, y: 0 }, data: { label: "A" } } as Node
    const second = { id: "n2", position: { x: 0, y: 0 }, data: { label: "B" } } as Node
    const event = {} as React.MouseEvent

    const { result } = renderHook(() => useEdgeHandlers(params))
    act(() => {
      result.current.onNodeClick(event, first)
      result.current.onNodeClick(event, second)
    })

    expect(params.fetchPreview).toHaveBeenNthCalledWith(1, first, {})
    expect(params.fetchPreview).toHaveBeenNthCalledWith(2, second, {})
    expect(params.lastSelectedNodeRef.current).toBe(second)
  })

  it("handleDeleteEdge removes edge by id", () => {
    const params = makeParams()
    const { result } = renderHook(() => useEdgeHandlers(params))
    act(() => {
      result.current.handleDeleteEdge("e1")
    })
    expect(params.setEdges).toHaveBeenCalledOnce()
    const updater = params.setEdges.mock.calls[0][0] as (eds: Edge[]) => Edge[]
    const remaining = updater([{ id: "e1" } as Edge, { id: "e2" } as Edge])
    expect(remaining).toHaveLength(1)
    expect(remaining[0].id).toBe("e2")
  })

  it("onNodeContextMenu sets context menu with correct data", () => {
    const params = makeParams()
    const node = { id: "n1", data: { label: "Test Node", nodeType: "polars" } } as unknown as Node
    const { result } = renderHook(() => useEdgeHandlers(params))
    const event = { preventDefault: vi.fn(), clientX: 100, clientY: 200 } as unknown as React.MouseEvent
    act(() => {
      result.current.onNodeContextMenu(event, node)
    })
    expect(event.preventDefault).toHaveBeenCalled()
    expect(params.setContextMenu).toHaveBeenCalledWith({
      x: 100,
      y: 200,
      nodeId: "n1",
      nodeLabel: "Test Node",
      isSubmodel: false,
      isSingleton: false,
      isExplodable: false,
    })
  })

  it("onNodeContextMenu marks submodel nodes as explodable (node-explosion peek)", () => {
    const params = makeParams()
    const node = { id: "submodel__pricing", data: { label: "Pricing", nodeType: "submodel" } } as unknown as Node
    const { result } = renderHook(() => useEdgeHandlers(params))
    const event = { preventDefault: vi.fn(), clientX: 10, clientY: 20 } as unknown as React.MouseEvent
    act(() => {
      result.current.onNodeContextMenu(event, node)
    })
    expect(params.setContextMenu).toHaveBeenCalledWith(
      expect.objectContaining({ isSubmodel: true, isExplodable: true }),
    )
  })

  it("onNodeContextMenu marks apiInput nodes as singleton", () => {
    const params = makeParams()
    const node = { id: "n1", data: { label: "Quote Input", nodeType: "apiInput" } } as unknown as Node
    const { result } = renderHook(() => useEdgeHandlers(params))
    const event = { preventDefault: vi.fn(), clientX: 50, clientY: 60 } as unknown as React.MouseEvent
    act(() => {
      result.current.onNodeContextMenu(event, node)
    })
    expect(params.setContextMenu).toHaveBeenCalledWith(
      expect.objectContaining({ isSingleton: true }),
    )
  })

  it("onNodeContextMenu marks output nodes as singleton", () => {
    const params = makeParams()
    const node = { id: "out1", data: { label: "Quote Response", nodeType: "output" } } as unknown as Node
    const { result } = renderHook(() => useEdgeHandlers(params))
    const event = { preventDefault: vi.fn(), clientX: 50, clientY: 60 } as unknown as React.MouseEvent
    act(() => {
      result.current.onNodeContextMenu(event, node)
    })
    expect(params.setContextMenu).toHaveBeenCalledWith(
      expect.objectContaining({ isSingleton: true }),
    )
  })

  it("onDragOver enables dropping by preventing default and signalling a move", () => {
    const params = makeParams()
    const { result } = renderHook(() => useEdgeHandlers(params))
    const event = {
      preventDefault: vi.fn(),
      dataTransfer: { dropEffect: "none" },
    } as unknown as React.DragEvent

    act(() => {
      result.current.onDragOver(event)
    })

    expect(event.preventDefault).toHaveBeenCalledOnce()
    expect(event.dataTransfer.dropEffect).toBe("move")
  })

  it("onDrop creates a new node from shared node metadata and drag config", () => {
    const params = makeParams()
    const { result } = renderHook(() => useEdgeHandlers(params))
    const event = {
      preventDefault: vi.fn(),
      clientX: 300,
      clientY: 400,
      dataTransfer: {
        getData: vi.fn((key: string) => {
          if (key === "application/reactflow-type") return NODE_TYPES.DATA_SINK
          if (key === "application/reactflow-config") return "{}"
          return ""
        }),
      },
    } as unknown as React.DragEvent
    act(() => {
      result.current.onDrop(event)
    })
    expect(params.setNodes).toHaveBeenCalledOnce()
    expect(params.setSelectedNode).toHaveBeenCalledOnce()
    expect(params.nodeIdCounter.current).toBe(1)

    const updater = params.setNodes.mock.calls[0][0] as (nds: Node[]) => Node[]
    const nextNodes = updater([{ id: "existing", selected: true } as Node])
    expect(nextNodes).toEqual([
      expect.objectContaining({ id: "existing", selected: false }),
      expect.objectContaining({
        id: "dataSink_1",
        type: NODE_TYPES.DATA_SINK,
        selected: true,
        position: { x: 300, y: 400 },
        data: {
          label: "Data Sink 1",
          description: "",
          nodeType: NODE_TYPES.DATA_SINK,
          config: {
            path: "",
            format: "parquet",
          },
        },
      }),
    ])
    expect(params.setSelectedNode).toHaveBeenCalledWith(
      expect.objectContaining({ id: "dataSink_1", selected: true }),
    )
  })

  it("onDrop with no type does nothing", () => {
    const params = makeParams()
    const { result } = renderHook(() => useEdgeHandlers(params))
    const event = {
      preventDefault: vi.fn(),
      clientX: 0,
      clientY: 0,
      dataTransfer: {
        getData: vi.fn(() => ""),
      },
    } as unknown as React.DragEvent
    act(() => {
      result.current.onDrop(event)
    })
    expect(params.setNodes).not.toHaveBeenCalled()
  })
})

describe("useEdgeHandlers edge-join failures and multi-port handles", () => {
  beforeEach(() => {
    useToastStore.setState({ toasts: [], _toastCounter: 0 })
  })

  afterEach(() => {
    cleanup()
    vi.restoreAllMocks()
  })

  it("onConnectEnd ignores connection endings that never had a source node", () => {
    const params = makeParams()
    const { result } = renderHook(() => useEdgeHandlers(params))

    act(() => {
      result.current.onConnectEnd(mouseUpEvent, {
        isValid: null,
        fromNode: null,
        fromHandle: null,
        toNode: null,
        toHandle: null,
      } as never)
    })

    expect(params.setEdges).not.toHaveBeenCalled()
    expect(params.setEdgesRaw).not.toHaveBeenCalled()
    expect(params.setNodesRaw).not.toHaveBeenCalled()
    expect(params.pushSnapshot).not.toHaveBeenCalled()
    expect(params.findEdgeIdAtPoint).not.toHaveBeenCalled()
    expect(useToastStore.getState().toasts).toEqual([])
  })

  it("onConnectEnd fails loudly when a touch ending carries no pointer coordinates", () => {
    const params = makeParams()
    params.graphRef.current.nodes = [
      { id: "base", position: { x: 300, y: 0 }, data: { label: "Base", nodeType: NODE_TYPES.POLARS } } as unknown as Node,
      { id: "lookup", position: { x: 0, y: 160 }, data: { label: "Lookup", nodeType: NODE_TYPES.POLARS } } as unknown as Node,
    ]
    const { result } = renderHook(() => useEdgeHandlers(params))
    const touchEndWithoutPoints = { touches: [], changedTouches: [] } as unknown as TouchEvent

    expect(() => {
      act(() => {
        result.current.onConnectEnd(
          touchEndWithoutPoints,
          {
            isValid: true,
            fromNode: { id: "lookup" },
            fromHandle: { id: "lookup_out", type: "source" },
            toNode: { id: "base" },
            toHandle: { id: "base_out", type: "source" },
          } as never,
        )
      })
    }).toThrow("Connection end touch event did not include pointer coordinates")

    expect(params.pushSnapshot).not.toHaveBeenCalled()
    expect(params.setNodesRaw).not.toHaveBeenCalled()
    expect(params.setEdgesRaw).not.toHaveBeenCalled()
  })

  it("onConnectEnd rejects a third edgeJoin input when legacy edges occupy no role handles", () => {
    const params = makeParams()
    params.graphRef.current.nodes = [
      { id: "join1", data: { label: "Edge Join 1", nodeType: NODE_TYPES.EDGE_JOIN, config: {} } } as unknown as Node,
    ]
    // Legacy/imported graphs can hold edges whose targetHandle never went
    // through the role-handle migration; neither occupies "base" or "join".
    params.graphRef.current.edges = [
      { id: "e-a-join", source: "a", target: "join1", sourceHandle: null, targetHandle: null } as Edge,
      { id: "e-b-join", source: "b", target: "join1", sourceHandle: null, targetHandle: null } as Edge,
    ]
    const { result } = renderHook(() => useEdgeHandlers(params))

    act(() => {
      result.current.onConnectEnd(
        mouseUpEvent,
        connectionEndState({ from: "c", to: "join1", toHandleId: "join" }),
      )
    })

    expect(params.setEdges).not.toHaveBeenCalled()
    expect(params.setEdgesRaw).not.toHaveBeenCalled()
    expect(params.setNodesRaw).not.toHaveBeenCalled()
    expect(params.pushSnapshot).not.toHaveBeenCalled()
    expect(useToastStore.getState().toasts).toEqual([
      expect.objectContaining({ type: "error", text: "Edge join nodes accept exactly two inputs" }),
    ])
  })

  it("onConnectEnd seeds role config on an edgeJoin node that has no config object", () => {
    const params = makeParams()
    params.graphRef.current.nodes = [
      { id: "join1", data: { label: "Edge Join 1", nodeType: NODE_TYPES.EDGE_JOIN } } as unknown as Node,
      { id: "quotes", data: { label: "Quotes", nodeType: NODE_TYPES.POLARS, config: {} } } as unknown as Node,
    ]
    const { result } = renderHook(() => useEdgeHandlers(params))

    act(() => {
      result.current.onConnectEnd(
        mouseUpEvent,
        connectionEndState({ from: "quotes", to: "join1", toHandleId: "base" }),
      )
    })

    expect(params.pushSnapshot).toHaveBeenCalledOnce()
    expect(params.setNodesRaw).toHaveBeenCalledOnce()
    expect(params.setEdgesRaw).toHaveBeenCalledOnce()
    const nextNodes = params.setNodesRaw.mock.calls[0][0] as Node[]
    expect(nextNodes.find((n) => n.id === "join1")?.data.config).toEqual({ baseInput: "quotes" })
    const nextEdges = params.setEdgesRaw.mock.calls[0][0] as Edge[]
    expect(nextEdges).toEqual([
      expect.objectContaining({ source: "quotes", target: "join1", targetHandle: "base" }),
    ])
  })

  it("onConnectEnd rejects dropping a node's own output onto its outgoing edge as a self-join", () => {
    const params = makeParams()
    params.graphRef.current.nodes = [
      { id: "a", position: { x: 0, y: 0 }, data: { label: "Base", nodeType: NODE_TYPES.POLARS } } as unknown as Node,
      { id: "b", position: { x: 300, y: 0 }, data: { label: "Downstream", nodeType: NODE_TYPES.POLARS } } as unknown as Node,
    ]
    params.graphRef.current.edges = [
      { id: "e_ab", source: "a", target: "b", sourceHandle: null, targetHandle: null } as Edge,
    ]
    params.findEdgeIdAtPoint.mockReturnValue("e_ab")
    const { result } = renderHook(() => useEdgeHandlers(params))

    act(() => {
      result.current.onConnectEnd(
        { clientX: 200, clientY: 150 } as MouseEvent,
        {
          isValid: null,
          fromNode: { id: "a" },
          fromHandle: { id: "a_out", type: "source" },
          toNode: null,
        } as never,
      )
    })

    expect(useToastStore.getState().toasts).toEqual([
      expect.objectContaining({
        type: "error",
        text: "Edge join rejected: choose a different dataframe to join",
      }),
    ])
    expect(params.pushSnapshot).not.toHaveBeenCalled()
    expect(params.setNodesRaw).not.toHaveBeenCalled()
    expect(params.setEdgesRaw).not.toHaveBeenCalled()
    expect(params.setSelectedNode).not.toHaveBeenCalled()
    expect(params.clearTrace).not.toHaveBeenCalled()
    expect(params.cancelPreview).not.toHaveBeenCalled()
    // Self-join is detected before an id is minted, so no id is burnt.
    expect(params.nodeIdCounter.current).toBe(0)
  })

  it("onConnectEnd rejects an edge drop that would create a cycle", () => {
    const params = makeParams()
    params.graphRef.current.nodes = [
      { id: "a", position: { x: 0, y: 0 }, data: { label: "Base", nodeType: NODE_TYPES.POLARS } } as unknown as Node,
      { id: "b", position: { x: 300, y: 0 }, data: { label: "Downstream", nodeType: NODE_TYPES.POLARS } } as unknown as Node,
    ]
    params.graphRef.current.edges = [
      { id: "e_ab", source: "a", target: "b", sourceHandle: null, targetHandle: null } as Edge,
    ]
    params.findEdgeIdAtPoint.mockReturnValue("e_ab")
    const { result } = renderHook(() => useEdgeHandlers(params))

    // Dragging the downstream node's output onto the edge feeding it would
    // route b -> join -> b.
    act(() => {
      result.current.onConnectEnd(
        { clientX: 200, clientY: 150 } as MouseEvent,
        {
          isValid: null,
          fromNode: { id: "b" },
          fromHandle: { id: "b_out", type: "source" },
          toNode: null,
        } as never,
      )
    })

    expect(useToastStore.getState().toasts).toEqual([
      expect.objectContaining({
        type: "error",
        text: "Edge join rejected: that connection would create a cycle",
      }),
    ])
    expect(params.pushSnapshot).not.toHaveBeenCalled()
    expect(params.setNodesRaw).not.toHaveBeenCalled()
    expect(params.setEdgesRaw).not.toHaveBeenCalled()
    expect(params.setSelectedNode).not.toHaveBeenCalled()
  })

  // (nick-dev merge reconcile) Three tests here used to assert the
  // output-to-output edge-join gesture — "Arm 1", insertEdgeJoinNodeFromSources:
  // a same-node self-join reject, a stale-source reject, and a
  // two-default-outputs-make-a-join success. That gesture was deliberately
  // rolled back on this branch (commit 6b099de) per Nick — an output→output
  // drop has no edge to join to, so the arbiter now treats it as a silent
  // no-op (see the "Rolled back / Arm 1" comment in useEdgeHandlers.ts). The
  // merge re-admitted nick-dev's Arm-1 tests against the Arm-1-less arbiter;
  // they are removed here rather than re-asserting behaviour Nick reverted.
  // The no-op is covered by the "output-onto-output …" cases above; the
  // self-join / source-node-not-found reasons stay covered by the pure helper
  // unit tests in utils/__tests__/edgeJoinGraph.test.ts.

  it("onConnectEnd normalises a reverse drag between default handles into a null-handle edge", () => {
    const params = makeParams()
    const { result } = renderHook(() => useEdgeHandlers(params))

    act(() => {
      result.current.onConnectEnd(
        mouseUpEvent,
        connectionEndState({
          from: "sinkNode",
          to: "sourceNode",
          fromHandleId: DEFAULT_TARGET_HANDLE,
          toHandleId: null,
          fromHandleType: "target",
          toHandleType: "source",
        }),
      )
    })

    expect(params.setEdges).toHaveBeenCalledOnce()
    const updater = params.setEdges.mock.calls[0][0] as (eds: Edge[]) => Edge[]
    expect(updater([])).toEqual([
      expect.objectContaining({
        source: "sourceNode",
        target: "sinkNode",
        sourceHandle: null,
        targetHandle: null,
      }),
    ])
  })

  it("onConnectEnd inserts an edgeJoin from a default source handle dropped on an edge", () => {
    const params = makeParams()
    params.graphRef.current.nodes = [
      { id: "a", position: { x: 0, y: 0 }, data: { label: "Base", nodeType: NODE_TYPES.POLARS } } as unknown as Node,
      { id: "b", position: { x: 300, y: 0 }, data: { label: "Downstream", nodeType: NODE_TYPES.POLARS } } as unknown as Node,
      { id: "c", position: { x: 0, y: 160 }, data: { label: "Lookup", nodeType: NODE_TYPES.POLARS } } as unknown as Node,
    ]
    params.graphRef.current.edges = [
      { id: "e_ab", source: "a", target: "b", sourceHandle: null, targetHandle: null } as Edge,
    ]
    params.findEdgeIdAtPoint.mockReturnValue("e_ab")
    const { result } = renderHook(() => useEdgeHandlers(params))

    act(() => {
      result.current.onConnectEnd(
        { clientX: 200, clientY: 150 } as MouseEvent,
        {
          isValid: null,
          fromNode: { id: "c" },
          fromHandle: { id: null, type: "source" },
          toNode: null,
        } as never,
      )
    })

    expect(params.pushSnapshot).toHaveBeenCalledOnce()
    const nextEdges = params.setEdgesRaw.mock.calls[0][0] as Edge[]
    expect(nextEdges).toHaveLength(3)
    expect(nextEdges).toEqual(expect.arrayContaining([
      expect.objectContaining({
        source: "c",
        target: "edgeJoin_1",
        sourceHandle: null,
        targetHandle: "join",
      }),
    ]))
  })

  it("onConnectEnd consults edge hit-testing and stays inert when no edge is under the pointer", () => {
    const params = makeParams()
    params.graphRef.current.nodes = [
      { id: "a", position: { x: 0, y: 0 }, data: { label: "A", nodeType: NODE_TYPES.POLARS } } as unknown as Node,
      { id: "b", position: { x: 300, y: 0 }, data: { label: "B", nodeType: NODE_TYPES.POLARS } } as unknown as Node,
    ]
    params.graphRef.current.edges = [
      { id: "e_ab", source: "a", target: "b", sourceHandle: null, targetHandle: null } as Edge,
    ]
    params.findEdgeIdAtPoint.mockReturnValue(null)
    const { result } = renderHook(() => useEdgeHandlers(params))

    act(() => {
      result.current.onConnectEnd(
        { clientX: 200, clientY: 150 } as MouseEvent,
        {
          isValid: null,
          fromNode: { id: "a" },
          fromHandle: { id: "a_out", type: "source" },
          toNode: null,
        } as never,
      )
    })

    expect(params.findEdgeIdAtPoint).toHaveBeenCalledWith({ x: 200, y: 150 })
    expect(params.pushSnapshot).not.toHaveBeenCalled()
    expect(params.setNodesRaw).not.toHaveBeenCalled()
    expect(params.setEdgesRaw).not.toHaveBeenCalled()
    expect(useToastStore.getState().toasts).toEqual([])
  })

  it("onConnectEnd treats missing edge hit-testing as no edge under the pointer", () => {
    const { findEdgeIdAtPoint: _omitted, ...params } = makeParams()
    const { result } = renderHook(() => useEdgeHandlers(params))

    act(() => {
      result.current.onConnectEnd(
        { clientX: 200, clientY: 150 } as MouseEvent,
        {
          isValid: null,
          fromNode: { id: "a" },
          fromHandle: { id: "a_out", type: "source" },
          toNode: null,
        } as never,
      )
    })

    expect(params.pushSnapshot).not.toHaveBeenCalled()
    expect(params.setNodesRaw).not.toHaveBeenCalled()
    expect(params.setEdgesRaw).not.toHaveBeenCalled()
    expect(params.screenToFlowPosition).not.toHaveBeenCalled()
    expect(useToastStore.getState().toasts).toEqual([])
  })
})
