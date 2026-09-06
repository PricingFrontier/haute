import { describe, it, expect, vi, afterEach } from "vitest"
import { renderHook, cleanup, act } from "@testing-library/react"
import type { Node, Edge, Connection } from "@xyflow/react"
import useEdgeHandlers from "../useEdgeHandlers"
import useToastStore from "../../stores/useToastStore"
import { NODE_TYPES, type NodeTypeValue } from "../../utils/nodeTypes"
import { DEFAULT_TARGET_HANDLE, SUBMODEL_INPUT_HANDLE } from "../../utils/flowHandles"
import { validatePipelineConnection } from "../../utils/connectionValidation"
import type { SimpleNode } from "../../panels/editors/_shared"

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
    existingSingletonTypes: new Set<NodeTypeValue>(),
    resolveGraphIdentities: vi.fn(async (
      nodes: readonly Node[],
      edges: readonly Edge[],
    ): Promise<{ nodes: Node[]; edges: Edge[] }> => ({
      nodes: nodes.map((node): Node => ({
        ...node,
        data: {
          ...node.data,
          _defaultInputName: node.data._defaultInputName ?? `server_${node.id}`,
          _sourceHandleInputNames: node.data._sourceHandleInputNames ?? {},
        },
      })),
      edges: [...edges],
    })),
    findEdgeIdAtPoint: vi.fn(() => null as string | null),
    validateConnection: undefined as
      | undefined
      | ((connection: Connection) => ReturnType<typeof validatePipelineConnection>),
    commitBoundaryConnection: undefined as
      | undefined
      | ((connection: Connection) => boolean),
    deleteBoundaryEdge: undefined as undefined | ((edgeId: string) => boolean),
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

function identifiedNode(
  id: string,
  nodeType: string = NODE_TYPES.POLARS,
  data: Record<string, unknown> = {},
): Node {
  return {
    id,
    position: { x: 0, y: 0 },
    data: {
      label: id,
      nodeType,
      _defaultInputName: `server_${id}`,
      _sourceHandleInputNames: {},
      ...data,
    },
  } as unknown as Node
}

function makeEdgeJoinInsertionParams() {
  const params = makeParams()
  params.graphRef.current.nodes = [
    { id: "a", position: { x: 0, y: 0 }, data: { label: "Base", nodeType: NODE_TYPES.POLARS, _defaultInputName: "base", _sourceHandleInputNames: {} } } as unknown as Node,
    { id: "b", position: { x: 300, y: 0 }, data: { label: "Downstream", nodeType: NODE_TYPES.POLARS, _defaultInputName: "downstream", _sourceHandleInputNames: {} } } as unknown as Node,
    { id: "c", position: { x: 0, y: 160 }, data: { label: "Lookup", nodeType: NODE_TYPES.POLARS, _defaultInputName: "lookup", _sourceHandleInputNames: {} } } as unknown as Node,
  ]
  params.graphRef.current.edges = [
    { id: "e_ab", source: "a", target: "b", sourceHandle: "base_out", targetHandle: null } as Edge,
  ]
  params.findEdgeIdAtPoint.mockReturnValue("e_ab")
  return params
}

function droppedNodeEvent(type: NodeTypeValue = NODE_TYPES.POLARS): React.DragEvent {
  return {
    preventDefault: vi.fn(),
    clientX: 300,
    clientY: 400,
    dataTransfer: {
      getData: vi.fn((key: string) => {
        if (key === "application/reactflow-type") return type
        if (key === "application/reactflow-config") return "{}"
        return ""
      }),
    },
  } as unknown as React.DragEvent
}

async function flushIdentityCommit(): Promise<void> {
  await act(async () => {
    await Promise.resolve()
    await Promise.resolve()
  })
}

describe("useEdgeHandlers", () => {
  afterEach(() => {
    cleanup()
    useToastStore.setState({ toasts: [], _toastCounter: 0 })
    vi.useRealTimers()
    vi.restoreAllMocks()
  })

  it("tracks only source connect starts, including the default source handle", () => {
    const params = makeParams()
    const { result } = renderHook(() => useEdgeHandlers(params))
    act(() => {
      result.current.onConnectStart({} as never, {
        nodeId: "a",
        handleId: null,
        handleType: "target",
      } as never)
      result.current.onConnectionPointerMove({ clientX: 1, clientY: 2 })
    })
    expect(params.findEdgeIdAtPoint).not.toHaveBeenCalled()

    act(() => {
      result.current.onConnectStart({} as never, {
        nodeId: "a",
        handleId: null,
        handleType: "source",
      } as never)
      result.current.onConnectionPointerMove({ clientX: 3, clientY: 4 })
    })
    expect(params.findEdgeIdAtPoint).toHaveBeenCalledWith({ x: 3, y: 4 })
  })

  it("reports a non-Error identity rejection from a dropped node", async () => {
    const params = makeParams()
    params.resolveGraphIdentities = vi.fn(async () => {
      throw "identity service unavailable"
    })
    const { result } = renderHook(() => useEdgeHandlers(params))

    act(() => result.current.onDrop(droppedNodeEvent()))
    await flushIdentityCommit()

    expect(useToastStore.getState().toasts.at(-1)?.text).toContain(
      "Create node failed: identity service unavailable",
    )
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
    params.graphRef.current.nodes = [
      {
        id: "a",
        position: { x: 0, y: 0 },
        data: {
          label: "Source",
          nodeType: NODE_TYPES.POLARS,
          _defaultInputName: "server_source",
          _sourceHandleInputNames: {},
        },
      } as unknown as Node,
      { id: "b", position: { x: 200, y: 0 }, data: { label: "Target", nodeType: NODE_TYPES.POLARS } } as unknown as Node,
    ]
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
        data: { _inputName: "server_source" },
      },
    ])
  })

  it("onConnectEnd attaches the authoritative source-handle identity", () => {
    const params = makeParams()
    params.graphRef.current.nodes = [
      {
        id: "api",
        position: { x: 0, y: 0 },
        data: {
          label: "API",
          nodeType: NODE_TYPES.API_INPUT,
          _defaultInputName: null,
          _sourceHandleInputNames: { quotes: "server_api_quotes" },
        },
      } as unknown as Node,
      { id: "target", position: { x: 200, y: 0 }, data: { label: "Target", nodeType: NODE_TYPES.POLARS } } as unknown as Node,
    ]
    const { result } = renderHook(() => useEdgeHandlers(params))

    act(() => {
      result.current.onConnectEnd(
        mouseUpEvent,
        connectionEndState({
          from: "api",
          to: "target",
          fromHandleId: "quotes",
          toHandleId: "in",
        }),
      )
    })

    const updater = params.setEdges.mock.calls[0][0] as (eds: Edge[]) => Edge[]
    expect(updater([])[0]).toMatchObject({
      source: "api",
      sourceHandle: "quotes",
      data: { _inputName: "server_api_quotes" },
    })
  })

  it("onConnectEnd refuses a new edge when source identity is unavailable", () => {
    const params = makeParams()
    params.graphRef.current.nodes = [
      { id: "a", position: { x: 0, y: 0 }, data: { label: "Source", nodeType: NODE_TYPES.POLARS } } as unknown as Node,
      { id: "b", position: { x: 200, y: 0 }, data: { label: "Target", nodeType: NODE_TYPES.POLARS } } as unknown as Node,
    ]
    const { result } = renderHook(() => useEdgeHandlers(params))

    act(() => {
      result.current.onConnectEnd(mouseUpEvent, connectionEndState({ from: "a", to: "b" }))
    })

    expect(params.setEdges).not.toHaveBeenCalled()
    expect(params.setEdgesRaw).not.toHaveBeenCalled()
    expect(params.pushSnapshot).not.toHaveBeenCalled()
    expect(useToastStore.getState().toasts).toEqual([
      expect.objectContaining({
        type: "error",
        text: expect.stringMatching(/connection rejected.*authoritative default identity/i),
      }),
    ])
  })

  it("lets the drilled-boundary adapter atomically own a boundary connection", () => {
    const params = makeParams()
    params.commitBoundaryConnection = vi.fn(() => true)
    const { result } = renderHook(() => useEdgeHandlers(params))

    act(() => {
      result.current.onConnectEnd(
        mouseUpEvent,
        connectionEndState({
          from: "child_a",
          to: "submodel-output",
          toHandleId: DEFAULT_TARGET_HANDLE,
        }),
      )
    })

    expect(params.commitBoundaryConnection).toHaveBeenCalledWith({
      source: "child_a",
      sourceHandle: null,
      target: "submodel-output",
      targetHandle: null,
    })
    expect(params.setEdges).not.toHaveBeenCalled()
  })

  it("onConnectEnd creates a new edge for target-to-source handles", () => {
    const params = makeParams()
    params.graphRef.current.nodes = [identifiedNode("outputNode"), identifiedNode("inputNode")]
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
      identifiedNode("a"),
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

  it("leaves a new collapsed submodel input unassigned", () => {
    const params = makeParams()
    params.graphRef.current.nodes = [
      identifiedNode("api"),
      { id: "sm1", data: { label: "SM", nodeType: NODE_TYPES.SUBMODEL } } as unknown as Node,
    ]
    const { result } = renderHook(() => useEdgeHandlers(params))

    act(() => {
      result.current.onConnectEnd(
        mouseUpEvent,
        connectionEndState({ from: "api", to: "sm1", toHandleId: DEFAULT_TARGET_HANDLE }),
      )
    })

    expect(params.setEdges).toHaveBeenCalledOnce()
    const updater = params.setEdges.mock.calls[0][0] as (eds: Edge[]) => Edge[]
    expect(updater([])).toEqual([
      expect.objectContaining({
        source: "api",
        target: "sm1",
        targetHandle: null,
      }),
    ])
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
    params.graphRef.current.nodes = [identifiedNode("a"), expanderNode]
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
    params.graphRef.current.nodes = [identifiedNode("b"), transformNode]
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
      identifiedNode("join1", NODE_TYPES.EDGE_JOIN, { label: "Edge Join 1", config: {} }),
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
      identifiedNode("join1", NODE_TYPES.EDGE_JOIN, { label: "Edge Join 1", config: {} }),
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

  it("onConnectEnd keeps source-to-source edgeJoin creation when dropped on a Polars output side", async () => {
    const params = makeParams()
    params.graphRef.current.nodes = [
      { id: "base", position: { x: 300, y: 0 }, measured: { width: 240, height: 70 }, data: { label: "Base", nodeType: NODE_TYPES.POLARS, _defaultInputName: "base", _sourceHandleInputNames: {} } } as unknown as Node,
      { id: "lookup", position: { x: 0, y: 160 }, data: { label: "Lookup", nodeType: NODE_TYPES.POLARS, _defaultInputName: "lookup", _sourceHandleInputNames: {} } } as unknown as Node,
    ]
    const { result } = renderHook(() => useEdgeHandlers(params))

    act(() => {
      result.current.onConnectEnd(
        { clientX: 520, clientY: 35 } as MouseEvent,
        {
          isValid: true,
          fromNode: { id: "lookup" },
          fromHandle: { id: "lookup_out", type: "source" },
          toNode: { id: "base" },
          toHandle: { id: "base_out", type: "source" },
        } as never,
      )
    })
    await flushIdentityCommit()

    expect(params.setEdges).not.toHaveBeenCalled()
    expect(params.pushSnapshot).toHaveBeenCalledOnce()
    expect(params.setNodesRaw).toHaveBeenCalledOnce()
    expect(params.setEdgesRaw).toHaveBeenCalledOnce()
    const nextNodes = params.setNodesRaw.mock.calls[0][0] as Node[]
    expect(nextNodes.find((node) => node.id === "edgeJoin_1")).toMatchObject({
      data: {
        config: {},
      },
    })
  })

  it("onConnectEnd reports validation failures for target-to-source handles", () => {
    const params = makeParams()
    params.validateConnection = vi.fn(() => ({
      ok: false,
      reason: { kind: "duplicate-input-name" as const, inputName: "quotes" },
    }))
    const { result } = renderHook(() => useEdgeHandlers(params))

    act(() => {
      result.current.onConnectEnd(
        mouseUpEvent,
        connectionEndState({
          from: "target",
          to: "source",
          fromHandleId: "in",
          toHandleId: "out",
          fromHandleType: "target",
          toHandleType: "source",
          isValid: false,
        }),
      )
    })

    expect(params.validateConnection).toHaveBeenCalledWith({
      source: "source",
      sourceHandle: "out",
      target: "target",
      targetHandle: "in",
    })
    expect(useToastStore.getState().toasts).toEqual([
      expect.objectContaining({ type: "error", text: expect.stringMatching(/quotes.*already connected/i) }),
    ])
    expect(params.setEdges).not.toHaveBeenCalled()
  })

  it("onConnectEnd reports the domain reason for an invalid targeted connection", () => {
    const params = makeParams()
    params.validateConnection = vi.fn(() => ({
      ok: false,
      reason: {
        kind: "invalid-connection" as const,
        message: "New public inputs can only be added through the definition owner",
      },
    }))
    const { result } = renderHook(() => useEdgeHandlers(params))

    act(() => {
      result.current.onConnectEnd(
        mouseUpEvent,
        connectionEndState({
          from: "source",
          to: "target",
          fromHandleId: "out",
          toHandleId: SUBMODEL_INPUT_HANDLE,
          isValid: false,
        }),
      )
    })

    expect(params.validateConnection).toHaveBeenCalledWith({
      source: "source",
      sourceHandle: "out",
      target: "target",
      targetHandle: SUBMODEL_INPUT_HANDLE,
    })
    expect(useToastStore.getState().toasts).toEqual([
      expect.objectContaining({
        type: "error",
        text: "Connection rejected: New public inputs can only be added through the definition owner",
      }),
    ])
    expect(params.setEdges).not.toHaveBeenCalled()
  })

  it("onConnectEnd honours React Flow rejection after reverse-direction validation passes", () => {
    const params = makeParams()
    params.validateConnection = vi.fn(() => ({ ok: true as const }))
    const { result } = renderHook(() => useEdgeHandlers(params))

    act(() => {
      result.current.onConnectEnd(
        mouseUpEvent,
        connectionEndState({
          from: "target",
          to: "source",
          fromHandleType: "target",
          toHandleType: "source",
          isValid: false,
        }),
      )
    })

    expect(params.validateConnection).toHaveBeenCalledOnce()
    expect(params.setEdges).not.toHaveBeenCalled()
    expect(useToastStore.getState().toasts).toEqual([])
  })

  it("creates an edgeJoin when an apiInput frame is dropped on an output that already consumes that frame", async () => {
    const params = makeParams()
    const apiInput = {
      id: "api",
      position: { x: 0, y: 160 },
      data: {
        label: "API",
        nodeType: NODE_TYPES.API_INPUT,
        config: {
          tables: [
            {
              path: "$[:].quotes[:]",
              label: "quotes",
              emit: true,
              columns: [{ name: "id", selected: true }],
            },
          ],
        },
      },
    } as unknown as Node
    const transform = {
      id: "transform",
      position: { x: 300, y: 0 },
      measured: { width: 240, height: 70 },
      data: { label: "Transform", nodeType: NODE_TYPES.POLARS, config: {} },
    } as unknown as Node
    const existing = {
      id: "e_api_transform",
      source: "api",
      target: "transform",
      sourceHandle: "quotes",
      targetHandle: null,
    } as Edge
    params.graphRef.current = { nodes: [apiInput, transform], edges: [existing] }
    const { result } = renderHook(() => useEdgeHandlers(params))

    act(() => {
      result.current.onConnectEnd(
        mouseUpEvent,
        connectionEndState({
          from: "api",
          to: "transform",
          fromHandleId: "quotes",
          toHandleId: "transform-output",
          fromHandleType: "source",
          toHandleType: "source",
          // React Flow can report false here because ordinary input-name
          // uniqueness sees the frame already entering this node. The
          // output-to-output gesture is an edgeJoin request, not another
          // input edge, so domain handling must still proceed.
          isValid: false,
        }),
      )
    })
    await flushIdentityCommit()

    expect(params.pushSnapshot).toHaveBeenCalledOnce()
    const nextNodes = params.setNodesRaw.mock.calls[0][0] as Node[]
    expect(nextNodes).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ id: "edgeJoin_1", data: expect.objectContaining({ nodeType: NODE_TYPES.EDGE_JOIN }) }),
      ]),
    )
    const nextEdges = params.setEdgesRaw.mock.calls[0][0] as Edge[]
    expect(nextEdges).toEqual(
      expect.arrayContaining([
        existing,
        expect.objectContaining({ source: "api", sourceHandle: "quotes", target: "edgeJoin_1" }),
        expect.objectContaining({ source: "transform", target: "edgeJoin_1" }),
      ]),
    )
  })

  it("onConnectEnd does not reinterpret source-only nodes as input-side targets", async () => {
    const params = makeParams()
    params.graphRef.current.nodes = [
      { id: "base", position: { x: 300, y: 0 }, measured: { width: 240, height: 70 }, data: { label: "Base", nodeType: NODE_TYPES.DATA_INPUT } } as unknown as Node,
      { id: "lookup", position: { x: 0, y: 160 }, data: { label: "Lookup", nodeType: NODE_TYPES.POLARS } } as unknown as Node,
    ]
    const { result } = renderHook(() => useEdgeHandlers(params))

    act(() => {
      result.current.onConnectEnd(
        { clientX: 320, clientY: 35 } as MouseEvent,
        {
          isValid: true,
          fromNode: { id: "lookup" },
          fromHandle: { id: "lookup_out", type: "source" },
          toNode: { id: "base" },
          toHandle: { id: "base_out", type: "source" },
        } as never,
      )
    })
    await flushIdentityCommit()

    expect(params.setEdges).not.toHaveBeenCalled()
    expect(params.pushSnapshot).toHaveBeenCalledOnce()
    expect(params.setNodesRaw).toHaveBeenCalledOnce()
    expect(params.setEdgesRaw).toHaveBeenCalledOnce()
    const nextNodes = params.setNodesRaw.mock.calls[0][0] as Node[]
    expect(nextNodes.find((node) => node.id === "edgeJoin_1")).toMatchObject({
      data: {
        config: {},
      },
    })
  })

  it("onConnectEnd records the base role only on the target handle", () => {
    const params = makeParams()
    params.graphRef.current.nodes = [
      { id: "join1", data: { label: "Edge Join 1", nodeType: NODE_TYPES.EDGE_JOIN, config: {} } } as unknown as Node,
      {
        id: "quotes",
        data: {
          label: "Quotes",
          nodeType: NODE_TYPES.POLARS,
          config: {},
          _defaultInputName: "server_quotes",
          _sourceHandleInputNames: {},
        },
      } as unknown as Node,
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
    expect(params.setNodesRaw).not.toHaveBeenCalled()
    expect(params.setEdgesRaw).toHaveBeenCalledOnce()
    expect(params.graphRef.current.nodes.find((n) => n.id === "join1")?.data.config).toEqual({})
    const nextEdges = params.setEdgesRaw.mock.calls[0][0] as Edge[]
    expect(nextEdges).toEqual([
      expect.objectContaining({
        source: "quotes",
        target: "join1",
        targetHandle: "base",
        data: { _inputName: "server_quotes" },
      }),
    ])
  })

  it("onConnectEnd leaves an edgeJoin role update atomic when source identity is unavailable", () => {
    const params = makeParams()
    params.graphRef.current.nodes = [
      { id: "join1", data: { label: "Edge Join 1", nodeType: NODE_TYPES.EDGE_JOIN, config: {} } } as unknown as Node,
      { id: "quotes", data: { label: "Quotes", nodeType: NODE_TYPES.POLARS, config: {} } } as unknown as Node,
    ]
    const { result } = renderHook(() => useEdgeHandlers(params))

    act(() => {
      result.current.onConnectEnd(
        mouseUpEvent,
        connectionEndState({ from: "quotes", to: "join1", toHandleId: "base" }),
      )
    })

    expect(params.pushSnapshot).not.toHaveBeenCalled()
    expect(params.setNodesRaw).not.toHaveBeenCalled()
    expect(params.setEdgesRaw).not.toHaveBeenCalled()
    expect(useToastStore.getState().toasts.at(-1)).toMatchObject({
      type: "error",
      text: expect.stringMatching(/connection rejected.*authoritative default identity/i),
    })
  })

  it("onConnectEnd normalises the bottom edgeJoin drop target to the join role", () => {
    const params = makeParams()
    params.graphRef.current.nodes = [
      { id: "join1", data: { label: "Edge Join 1", nodeType: NODE_TYPES.EDGE_JOIN, config: {} } } as unknown as Node,
      { id: "base", data: { label: "Base", nodeType: NODE_TYPES.POLARS, config: {} } } as unknown as Node,
      identifiedNode("lookup", NODE_TYPES.POLARS, { label: "Lookup", config: {} }),
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
    expect(params.setNodesRaw).not.toHaveBeenCalled()
    expect(params.setEdgesRaw).toHaveBeenCalledOnce()
    expect(params.graphRef.current.nodes.find((n) => n.id === "join1")?.data.config).toEqual({})
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
      { id: "join1", data: { label: "Edge Join 1", nodeType: NODE_TYPES.EDGE_JOIN, config: {} } } as unknown as Node,
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
      { id: "join1", data: { label: "Edge Join 1", nodeType: NODE_TYPES.EDGE_JOIN, config: {} } } as unknown as Node,
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

  it("onConnectEnd inserts an edgeJoin node when a connection is dropped on an edge", async () => {
    const params = makeEdgeJoinInsertionParams()
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
    await flushIdentityCommit()

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
        _defaultInputName: "server_edgeJoin_1",
        config: {
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

  it("does not commit an edgeJoin when the server omits its executable identity", async () => {
    const params = makeEdgeJoinInsertionParams()
    params.resolveGraphIdentities = vi.fn(async (nodes, edges) => ({
      nodes: [...nodes],
      edges: [...edges],
    }))
    const originalGraph = params.graphRef.current
    const { result } = renderHook(() => useEdgeHandlers(params))

    act(() => {
      result.current.onConnectEnd(
        mouseUpEvent,
        connectionEndState({ from: "c", fromHandleId: "lookup_out" }),
      )
    })
    await flushIdentityCommit()

    expect(params.graphRef.current).toBe(originalGraph)
    expect(params.pushSnapshot).not.toHaveBeenCalled()
    expect(params.setNodesRaw).not.toHaveBeenCalled()
    expect(params.setEdgesRaw).not.toHaveBeenCalled()
    expect(params.setSelectedNode).not.toHaveBeenCalled()
    expect(useToastStore.getState().toasts.at(-1)?.text).toMatch(/did not provide a default input name/)
  })

  it("does not commit a resolved edgeJoin after the graph changes", async () => {
    const params = makeEdgeJoinInsertionParams()
    let candidate: { nodes: Node[]; edges: Edge[] } | null = null
    let resolveIdentities!: (value: { nodes: Node[]; edges: Edge[] }) => void
    params.resolveGraphIdentities = vi.fn((nodes, edges) => {
      candidate = { nodes: [...nodes], edges: [...edges] }
      return new Promise((resolve) => { resolveIdentities = resolve })
    })
    const { result } = renderHook(() => useEdgeHandlers(params))

    act(() => {
      result.current.onConnectEnd(
        mouseUpEvent,
        connectionEndState({ from: "c", fromHandleId: "lookup_out" }),
      )
    })
    await vi.waitFor(() => expect(params.resolveGraphIdentities).toHaveBeenCalledOnce())
    params.graphRef.current = { nodes: [], edges: [] }
    await act(async () => {
      if (!candidate) throw new Error("Expected an Edge Join identity candidate")
      resolveIdentities({
        nodes: candidate.nodes.map((node) => node.id === "edgeJoin_1"
          ? { ...node, data: { ...node.data, _defaultInputName: "server_join" } }
          : node),
        edges: candidate.edges,
      })
      await Promise.resolve()
    })

    expect(params.pushSnapshot).not.toHaveBeenCalled()
    expect(params.setNodesRaw).not.toHaveBeenCalled()
    expect(params.setEdgesRaw).not.toHaveBeenCalled()
    expect(params.setSelectedNode).not.toHaveBeenCalled()
    expect(useToastStore.getState().toasts.at(-1)?.text).toMatch(/graph changed/)
  })

  it("onConnectEnd ignores input-handle drops on an existing edge", () => {
    const params = makeParams()
    params.graphRef.current.nodes = [
      { id: "a", position: { x: 0, y: 0 }, data: { label: "Base", nodeType: NODE_TYPES.POLARS, _defaultInputName: "base" } } as unknown as Node,
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

  it("onConnectEnd inserts an unconnected edgeJoin node when a source output is dropped on another source output", async () => {
    const params = makeParams()
    params.graphRef.current.nodes = [
      { id: "base", position: { x: 0, y: 0 }, data: { label: "Base", nodeType: NODE_TYPES.POLARS } } as unknown as Node,
      { id: "lookup", position: { x: 0, y: 160 }, data: { label: "Lookup", nodeType: NODE_TYPES.POLARS } } as unknown as Node,
    ]
    params.graphRef.current.edges = []
    const { result } = renderHook(() => useEdgeHandlers(params))

    act(() => {
      result.current.onConnectEnd(
        { clientX: 220, clientY: 120 } as MouseEvent,
        {
          isValid: true,
          fromNode: { id: "lookup" },
          fromHandle: { id: "lookup_out", type: "source" },
          toNode: { id: "base" },
          toHandle: { id: "base_out", type: "source" },
        } as never,
      )
    })
    await flushIdentityCommit()

    expect(params.findEdgeIdAtPoint).not.toHaveBeenCalled()
    expect(params.pushSnapshot).toHaveBeenCalledOnce()
    expect(params.setNodesRaw).toHaveBeenCalledOnce()
    expect(params.setEdgesRaw).toHaveBeenCalledOnce()
    expect(params.nodeIdCounter.current).toBe(1)

    const nextNodes = params.setNodesRaw.mock.calls[0][0] as Node[]
    const edgeJoin = nextNodes.find((node) => node.id === "edgeJoin_1")
    expect(edgeJoin).toMatchObject({
      id: "edgeJoin_1",
      type: NODE_TYPES.EDGE_JOIN,
      position: { x: 220, y: 120 },
      data: {
        label: "Edge Join 1",
        nodeType: NODE_TYPES.EDGE_JOIN,
        config: {
          how: "left",
          suffix: "_right",
        },
      },
      selected: true,
    })
    expect(params.setSelectedNode).toHaveBeenCalledWith(edgeJoin)
    expect(params.lastSelectedNodeRef.current).toBe(edgeJoin)

    const nextEdges = params.setEdgesRaw.mock.calls[0][0] as Edge[]
    expect(nextEdges).toEqual([
      expect.objectContaining({
        source: "base",
        target: "edgeJoin_1",
        sourceHandle: "base_out",
        targetHandle: "base",
      }),
      expect.objectContaining({
        source: "lookup",
        target: "edgeJoin_1",
        sourceHandle: "lookup_out",
        targetHandle: "join",
      }),
    ])
  })

  it("onConnectEnd uses changedTouches to position source-to-source edgeJoin creation", async () => {
    const params = makeParams()
    params.graphRef.current.nodes = [
      { id: "base", position: { x: 0, y: 0 }, data: { label: "Base", nodeType: NODE_TYPES.POLARS } } as unknown as Node,
      { id: "lookup", position: { x: 0, y: 160 }, data: { label: "Lookup", nodeType: NODE_TYPES.POLARS } } as unknown as Node,
    ]
    const { result } = renderHook(() => useEdgeHandlers(params))

    act(() => {
      result.current.onConnectEnd(
        {
          touches: [],
          changedTouches: [{ clientX: 240, clientY: 180 }],
        } as unknown as TouchEvent,
        {
          isValid: true,
          fromNode: { id: "lookup" },
          fromHandle: { id: "lookup_out", type: "source" },
          toNode: { id: "base" },
          toHandle: { id: "base_out", type: "source" },
        } as never,
      )
    })
    await flushIdentityCommit()

    const nextNodes = params.setNodesRaw.mock.calls[0][0] as Node[]
    expect(nextNodes.find((node) => node.id === "edgeJoin_1")?.position).toEqual({
      x: 240,
      y: 180,
    })
  })

  it("onConnectEnd ignores invalid node-to-node endings unless both handles are source outputs", () => {
    const params = makeParams()
    params.findEdgeIdAtPoint.mockReturnValue("e_ab")
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

    expect(params.pushSnapshot).not.toHaveBeenCalled()
    expect(params.setNodesRaw).not.toHaveBeenCalled()
    expect(params.setEdgesRaw).not.toHaveBeenCalled()
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
    NODE_TYPES.DATA_OUTPUT,
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

  it("onNodeClick skips automatic preview for an API input without tables[]", () => {
    const params = makeParams()
    const node = {
      id: "quotes",
      position: { x: 0, y: 0 },
      data: {
        label: "Quotes",
        nodeType: NODE_TYPES.API_INPUT,
        config: { path: "data/quotes.json" },
      },
    } as Node
    const event = {} as React.MouseEvent
    const { result } = renderHook(() => useEdgeHandlers(params))

    act(() => {
      result.current.onNodeClick(event, node)
    })

    expect(params.setSelectedNode).toHaveBeenCalledWith(node)
    expect(params.clearTrace).toHaveBeenCalled()
    expect(params.cancelPreview).toHaveBeenCalledOnce()
    expect(params.setPreviewData).toHaveBeenCalledWith(null)
    expect(params.fetchPreview).not.toHaveBeenCalled()
  })

  it("onNodeClick previews an API input once tables[] exists", () => {
    const params = makeParams()
    const node = {
      id: "quotes",
      position: { x: 0, y: 0 },
      data: {
        label: "Quotes",
        nodeType: NODE_TYPES.API_INPUT,
        config: { path: "data/quotes.json", tables: [] },
      },
    } as Node
    const event = {} as React.MouseEvent
    const { result } = renderHook(() => useEdgeHandlers(params))

    act(() => {
      result.current.onNodeClick(event, node)
    })

    expect(params.fetchPreview).toHaveBeenCalledWith(node, {})
    expect(params.setPreviewData).not.toHaveBeenCalled()
  })

  it("onNodeClick previews a flat-file API input without tables[]", () => {
    const params = makeParams()
    const node = {
      id: "quotes",
      position: { x: 0, y: 0 },
      data: {
        label: "Quotes",
        nodeType: NODE_TYPES.API_INPUT,
        config: { path: "data/quotes.parquet" },
      },
    } as Node
    const event = {} as React.MouseEvent
    const { result } = renderHook(() => useEdgeHandlers(params))

    act(() => {
      result.current.onNodeClick(event, node)
    })

    expect(params.fetchPreview).toHaveBeenCalledWith(node, {})
    expect(params.setPreviewData).not.toHaveBeenCalled()
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

  it("lets the drilled-boundary adapter atomically own boundary deletion", () => {
    const params = makeParams()
    params.deleteBoundaryEdge = vi.fn(() => true)
    const { result } = renderHook(() => useEdgeHandlers(params))

    act(() => {
      result.current.handleDeleteEdge("boundary-export")
    })

    expect(params.deleteBoundaryEdge).toHaveBeenCalledWith("boundary-export")
    expect(params.setEdges).not.toHaveBeenCalled()
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
      isSubmodelCopy: false,
      isSingleton: false,
    })
  })

  it("onNodeContextMenu flags a submodel instance copy as deletable", () => {
    const params = makeParams()
    const owner = {
      id: "scoring",
      data: {
        label: "scoring",
        nodeType: "submodel",
        config: { definitionId: "definition_scoring", alias: "scoring" },
      },
    } as unknown as Node
    const copy = {
      id: "scoring_2",
      data: {
        label: "scoring_2",
        nodeType: "submodel",
        config: {
          definitionId: "definition_scoring",
          alias: "scoring_2",
          instanceOf: "scoring",
        },
      },
    } as unknown as Node
    const { result } = renderHook(() => useEdgeHandlers(params))
    const event = { preventDefault: vi.fn(), clientX: 0, clientY: 0 } as unknown as React.MouseEvent

    act(() => { result.current.onNodeContextMenu(event, owner) })
    expect(params.setContextMenu).toHaveBeenLastCalledWith(
      expect.objectContaining({ isSubmodel: true, isSubmodelCopy: false }),
    )

    act(() => { result.current.onNodeContextMenu(event, copy) })
    expect(params.setContextMenu).toHaveBeenLastCalledWith(
      expect.objectContaining({ isSubmodel: true, isSubmodelCopy: true }),
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

  it("onNodeContextMenu marks a submodel containing a singleton as singleton", () => {
    const params = makeParams()
    const node = {
      id: "inputs",
      data: {
        label: "Inputs",
        nodeType: NODE_TYPES.SUBMODEL,
        config: { definitionId: "definition_inputs", alias: "inputs" },
      },
    } as unknown as Node
    const submodels = {
      definition_inputs: {
        definitionId: "definition_inputs",
        file: "modules/inputs.py",
        graph: {
          nodes: [identifiedNode("quote_input", NODE_TYPES.API_INPUT)],
          edges: [],
        },
        inputPorts: [],
        outputPorts: [],
      },
    }
    const { result } = renderHook(() => useEdgeHandlers({ ...params, submodels }))
    const event = { preventDefault: vi.fn(), clientX: 50, clientY: 60 } as unknown as React.MouseEvent

    act(() => {
      result.current.onNodeContextMenu(event, node)
    })

    expect(params.setContextMenu).toHaveBeenCalledWith(
      expect.objectContaining({ isSubmodel: true, isSingleton: true }),
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

  it("onDrop creates a new node from shared node metadata and drag config", async () => {
    const params = makeParams()
    const { result } = renderHook(() => useEdgeHandlers(params))
    const event = {
      preventDefault: vi.fn(),
      clientX: 300,
      clientY: 400,
      dataTransfer: {
        getData: vi.fn((key: string) => {
          if (key === "application/reactflow-type") return NODE_TYPES.DATA_OUTPUT
          if (key === "application/reactflow-config") return "{}"
          return ""
        }),
      },
    } as unknown as React.DragEvent
    act(() => {
      result.current.onDrop(event)
    })
    await flushIdentityCommit()
    expect(params.setNodes).toHaveBeenCalledOnce()
    expect(params.setSelectedNode).toHaveBeenCalledOnce()
    expect(params.nodeIdCounter.current).toBe(1)

    const updater = params.setNodes.mock.calls[0][0] as (nds: Node[]) => Node[]
    const nextNodes = updater([{ id: "existing", selected: true } as Node])
    expect(nextNodes).toEqual([
      expect.objectContaining({ id: "existing", selected: false }),
      expect.objectContaining({
        id: "dataOutput_1",
        type: NODE_TYPES.DATA_OUTPUT,
        selected: true,
        position: { x: 300, y: 400 },
        data: expect.objectContaining({
          label: "Data Output 1",
          description: "",
          nodeType: NODE_TYPES.DATA_OUTPUT,
          _defaultInputName: "server_dataOutput_1",
          _sourceHandleInputNames: {},
          config: {
            outputType: "file",
            format: "parquet",
            mode: "sink",
            path: "",
            arguments: {},
          },
        }),
      }),
    ])
    expect(params.setSelectedNode).toHaveBeenCalledWith(
      expect.objectContaining({ id: "dataOutput_1", selected: true }),
    )
  })

  it("onDrop rejects a singleton already present elsewhere in the document", () => {
    const params = makeParams()
    params.existingSingletonTypes = new Set([NODE_TYPES.API_INPUT])
    const { result } = renderHook(() => useEdgeHandlers(params))

    act(() => {
      result.current.onDrop(droppedNodeEvent(NODE_TYPES.API_INPUT))
    })

    expect(params.resolveGraphIdentities).not.toHaveBeenCalled()
    expect(params.setNodes).not.toHaveBeenCalled()
    expect(useToastStore.getState().toasts.at(-1)).toMatchObject({
      type: "info",
      text: "Only one Quote Input node is allowed per pipeline",
    })
  })

  it("onDrop rechecks the current graph before accepting a stale singleton drag", () => {
    const params = makeParams()
    params.graphRef.current.nodes = [identifiedNode("quote_input", NODE_TYPES.API_INPUT)]
    const { result } = renderHook(() => useEdgeHandlers(params))

    act(() => {
      result.current.onDrop(droppedNodeEvent(NODE_TYPES.API_INPUT))
    })

    expect(params.resolveGraphIdentities).not.toHaveBeenCalled()
    expect(params.setNodes).not.toHaveBeenCalled()
    expect(useToastStore.getState().toasts.at(-1)?.text).toBe(
      "Only one Quote Input node is allowed per pipeline",
    )
  })

  it("keeps a dropped-node creation atomic when identity resolution rejects", async () => {
    const params = makeParams()
    params.resolveGraphIdentities = vi.fn(async () => {
      throw new Error("identity service unavailable")
    })
    const { result } = renderHook(() => useEdgeHandlers(params))

    act(() => result.current.onDrop(droppedNodeEvent()))
    await flushIdentityCommit()

    expect(params.setNodes).not.toHaveBeenCalled()
    expect(params.setSelectedNode).not.toHaveBeenCalled()
    expect(useToastStore.getState().toasts.at(-1)?.text).toMatch(
      /Create node failed: identity service unavailable/,
    )
  })

  it("rejects malformed dropped-node identity output atomically", async () => {
    const params = makeParams()
    params.resolveGraphIdentities = vi.fn(async () => ({ nodes: [], edges: [] }))
    const { result } = renderHook(() => useEdgeHandlers(params))

    act(() => result.current.onDrop(droppedNodeEvent()))
    await flushIdentityCommit()

    expect(params.setNodes).not.toHaveBeenCalled()
    expect(params.setSelectedNode).not.toHaveBeenCalled()
    expect(useToastStore.getState().toasts.at(-1)?.text).toMatch(/invalid node/)
  })

  it("does not commit a dropped node after the graph changes during resolution", async () => {
    const params = makeParams()
    let candidate: Node | null = null
    let resolveIdentities!: (value: { nodes: Node[]; edges: Edge[] }) => void
    params.resolveGraphIdentities = vi.fn((nodes) => {
      candidate = nodes[0] ?? null
      return new Promise((resolve) => { resolveIdentities = resolve })
    })
    const { result } = renderHook(() => useEdgeHandlers(params))

    act(() => result.current.onDrop(droppedNodeEvent()))
    await vi.waitFor(() => expect(params.resolveGraphIdentities).toHaveBeenCalledOnce())
    params.graphRef.current = { nodes: [], edges: [] }
    await act(async () => {
      if (!candidate) throw new Error("Expected a dropped-node identity candidate")
      resolveIdentities({ nodes: [candidate], edges: [] })
      await Promise.resolve()
    })

    expect(params.setNodes).not.toHaveBeenCalled()
    expect(params.setSelectedNode).not.toHaveBeenCalled()
    expect(useToastStore.getState().toasts.at(-1)?.text).toMatch(/graph changed/)
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
  afterEach(() => {
    cleanup()
    useToastStore.setState({ toasts: [], _toastCounter: 0 })
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

  it("toasts the colliding derived input name when a normal drag is rejected", () => {
    const params = makeParams()
    params.graphRef.current.nodes = [
      {
        id: "api",
        data: {
          label: "API",
          nodeType: NODE_TYPES.API_INPUT,
          _defaultInputName: null,
          _sourceHandleInputNames: { quotes: "quotes" },
          config: {
            tables: [
              {
                path: "$[:].quotes[:]",
                label: "quotes",
                emit: true,
                columns: [{ name: "id", selected: true }],
              },
            ],
          },
        },
      } as unknown as Node,
      {
        id: "ordinary",
        data: { label: "quotes", nodeType: NODE_TYPES.POLARS, config: {}, _defaultInputName: "quotes", _sourceHandleInputNames: {} },
      } as unknown as Node,
      {
        id: "target",
        data: { label: "Target", nodeType: NODE_TYPES.POLARS, config: {}, _defaultInputName: "Target", _sourceHandleInputNames: {} },
      } as unknown as Node,
    ]
    params.graphRef.current.edges = [
      {
        id: "e_existing",
        source: "ordinary",
        target: "target",
        sourceHandle: null,
        targetHandle: null,
      } as Edge,
    ]
    params.validateConnection = (candidate) =>
      validatePipelineConnection(
        candidate,
        params.graphRef.current.nodes as unknown as SimpleNode[],
        params.graphRef.current.edges,
      )
    const { result } = renderHook(() => useEdgeHandlers(params))

    act(() => {
      result.current.onConnectEnd(
        mouseUpEvent,
        connectionEndState({
          from: "api",
          to: "target",
          fromHandleId: "quotes",
          fromHandleType: "source",
          toHandleType: "target",
          isValid: false,
        }),
      )
    })

    expect(useToastStore.getState().toasts).toEqual([
      expect.objectContaining({
        type: "error",
        text: expect.stringMatching(/input name.*quotes.*already connected/i),
      }),
    ])
    expect(params.setEdges).not.toHaveBeenCalled()
    expect(params.pushSnapshot).not.toHaveBeenCalled()
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

  it("onConnectEnd rejects a third edgeJoin input when malformed persisted edges occupy no role", () => {
    const params = makeParams()
    params.graphRef.current.nodes = [
      { id: "join1", data: { label: "Edge Join 1", nodeType: NODE_TYPES.EDGE_JOIN, config: {} } } as unknown as Node,
    ]
    // A hand-edited graph can contain target handles outside the canonical
    // base/join pair. It must not bypass the two-input invariant.
    params.graphRef.current.edges = [
      { id: "e-a-join", source: "a", target: "join1", sourceHandle: null, targetHandle: "other-a" } as Edge,
      { id: "e-b-join", source: "b", target: "join1", sourceHandle: null, targetHandle: "other-b" } as Edge,
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

  it("onConnectEnd leaves edgeJoin config untouched when adding a role", () => {
    const params = makeParams()
    params.graphRef.current.nodes = [
      { id: "join1", data: { label: "Edge Join 1", nodeType: NODE_TYPES.EDGE_JOIN } } as unknown as Node,
      identifiedNode("quotes", NODE_TYPES.POLARS, { label: "Quotes", config: {} }),
    ]
    const { result } = renderHook(() => useEdgeHandlers(params))

    act(() => {
      result.current.onConnectEnd(
        mouseUpEvent,
        connectionEndState({ from: "quotes", to: "join1", toHandleId: "base" }),
      )
    })

    expect(params.pushSnapshot).toHaveBeenCalledOnce()
    expect(params.setNodesRaw).not.toHaveBeenCalled()
    expect(params.setEdgesRaw).toHaveBeenCalledOnce()
    expect(params.graphRef.current.nodes.find((n) => n.id === "join1")?.data.config).toBeUndefined()
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
          fromHandle: { id: null, type: "source" },
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

  it("continues to reject direct same-node source-to-source gestures", () => {
    const params = makeParams()
    params.graphRef.current.nodes = [
      { id: "split", position: { x: 0, y: 0 }, data: { label: "Split", nodeType: NODE_TYPES.POLARS } } as unknown as Node,
    ]
    const { result } = renderHook(() => useEdgeHandlers(params))

    act(() => {
      result.current.onConnectEnd(
        mouseUpEvent,
        {
          isValid: true,
          fromNode: { id: "split" },
          fromHandle: { id: "out_a", type: "source" },
          toNode: { id: "split" },
          toHandle: { id: "out_b", type: "source" },
        } as never,
      )
    })

    expect(useToastStore.getState().toasts).toEqual([
      expect.objectContaining({ type: "error", text: "Edge join rejected: choose a different dataframe to join" }),
    ])
  })

  it("onConnectEnd rejects a source-to-source join from a node that is no longer in the graph", () => {
    const params = makeParams()
    // Simulates a stale drag finishing after a websocket refresh removed
    // the dragged node from graphRef.
    params.graphRef.current.nodes = [
      { id: "base", position: { x: 300, y: 0 }, data: { label: "Base", nodeType: NODE_TYPES.POLARS } } as unknown as Node,
    ]
    const { result } = renderHook(() => useEdgeHandlers(params))

    act(() => {
      result.current.onConnectEnd(
        mouseUpEvent,
        {
          isValid: true,
          fromNode: { id: "ghost" },
          fromHandle: { id: "ghost_out", type: "source" },
          toNode: { id: "base" },
          toHandle: { id: "base_out", type: "source" },
        } as never,
      )
    })

    expect(useToastStore.getState().toasts).toEqual([
      expect.objectContaining({
        type: "error",
        text: "Edge join rejected: source node is no longer available",
      }),
    ])
    expect(params.pushSnapshot).not.toHaveBeenCalled()
    expect(params.setNodesRaw).not.toHaveBeenCalled()
    expect(params.setEdgesRaw).not.toHaveBeenCalled()
  })

  it("onConnectEnd stores null source handles when joining two default outputs", async () => {
    const params = makeParams()
    params.graphRef.current.nodes = [
      { id: "base", position: { x: 300, y: 0 }, data: { label: "Base", nodeType: NODE_TYPES.POLARS } } as unknown as Node,
      { id: "lookup", position: { x: 0, y: 160 }, data: { label: "Lookup", nodeType: NODE_TYPES.POLARS } } as unknown as Node,
    ]
    const { result } = renderHook(() => useEdgeHandlers(params))

    act(() => {
      result.current.onConnectEnd(
        mouseUpEvent,
        {
          isValid: true,
          fromNode: { id: "lookup" },
          fromHandle: { id: null, type: "source" },
          toNode: { id: "base" },
          toHandle: { id: null, type: "source" },
        } as never,
      )
    })
    await flushIdentityCommit()

    expect(params.pushSnapshot).toHaveBeenCalledOnce()
    const nextNodes = params.setNodesRaw.mock.calls[0][0] as Node[]
    expect(nextNodes.find((node) => node.id === "edgeJoin_1")).toMatchObject({
      data: { config: { how: "left", suffix: "_right" } },
    })
    const nextEdges = params.setEdgesRaw.mock.calls[0][0] as Edge[]
    expect(nextEdges).toEqual([
      expect.objectContaining({
        source: "base",
        target: "edgeJoin_1",
        sourceHandle: null,
        targetHandle: "base",
      }),
      expect.objectContaining({
        source: "lookup",
        target: "edgeJoin_1",
        sourceHandle: null,
        targetHandle: "join",
      }),
    ])
  })

  it("onConnectEnd normalises a reverse drag between default handles into a null-handle edge", () => {
    const params = makeParams()
    params.graphRef.current.nodes = [identifiedNode("sourceNode"), identifiedNode("sinkNode")]
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

  it("onConnectEnd inserts an edgeJoin from a default source handle dropped on an edge", async () => {
    const params = makeParams()
    params.graphRef.current.nodes = [
      { id: "a", position: { x: 0, y: 0 }, data: { label: "Base", nodeType: NODE_TYPES.POLARS, _defaultInputName: "Base", _sourceHandleInputNames: {} } } as unknown as Node,
      { id: "b", position: { x: 300, y: 0 }, data: { label: "Downstream", nodeType: NODE_TYPES.POLARS, _defaultInputName: "Downstream", _sourceHandleInputNames: {} } } as unknown as Node,
      { id: "c", position: { x: 0, y: 160 }, data: { label: "Lookup", nodeType: NODE_TYPES.POLARS, _defaultInputName: "Lookup", _sourceHandleInputNames: {} } } as unknown as Node,
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
    await flushIdentityCommit()

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

describe("useEdgeHandlers edge-join insertion candidates", () => {
  afterEach(() => {
    cleanup()
    useToastStore.setState({ toasts: [], _toastCounter: 0 })
    vi.restoreAllMocks()
  })

  function candidateParams() {
    const params = makeParams()
    params.graphRef.current.nodes = [
      { id: "base", position: { x: 0, y: 0 }, data: { label: "Base", nodeType: NODE_TYPES.POLARS, _defaultInputName: "Base", _sourceHandleInputNames: {} } } as unknown as Node,
      { id: "middle", position: { x: 200, y: 0 }, data: { label: "Middle", nodeType: NODE_TYPES.POLARS, _defaultInputName: "Middle", _sourceHandleInputNames: {} } } as unknown as Node,
      { id: "downstream", position: { x: 400, y: 0 }, data: { label: "Downstream", nodeType: NODE_TYPES.POLARS, _defaultInputName: "Downstream", _sourceHandleInputNames: {} } } as unknown as Node,
      { id: "lookup", position: { x: 0, y: 180 }, data: { label: "Lookup", nodeType: NODE_TYPES.POLARS, _defaultInputName: "Lookup", _sourceHandleInputNames: {} } } as unknown as Node,
    ]
    params.graphRef.current.edges = [
      { id: "edge-base-middle", source: "base", target: "middle" } as Edge,
      { id: "edge-middle-downstream", source: "middle", target: "downstream" } as Edge,
    ]
    return params
  }

  function startConnection(
    result: { current: ReturnType<typeof useEdgeHandlers> },
    nodeId = "lookup",
    handleType: HandleType = "source",
  ) {
    act(() => {
      result.current.onConnectStart({} as never, {
        nodeId,
        handleId: "lookup-output",
        handleType,
      } as never)
    })
  }

  it("marks a compatible edge before release without mutating graph or history", () => {
    const params = candidateParams()
    params.findEdgeIdAtPoint.mockReturnValue("edge-base-middle")
    const { result } = renderHook(() => useEdgeHandlers(params))

    startConnection(result)
    act(() => {
      result.current.onConnectionPointerMove({ clientX: 120, clientY: 80 })
    })

    expect(result.current.edgeJoinCandidateEdgeId).toBe("edge-base-middle")
    const resultAfterEntry = result.current
    act(() => {
      result.current.onConnectionPointerMove({ clientX: 120, clientY: 80 })
    })
    expect(result.current).toBe(resultAfterEntry)
    expect(params.findEdgeIdAtPoint).toHaveBeenCalledWith({ x: 120, y: 80 })
    expect(params.setNodesRaw).not.toHaveBeenCalled()
    expect(params.setEdgesRaw).not.toHaveBeenCalled()
    expect(params.setSelectedNode).not.toHaveBeenCalled()
    expect(params.pushSnapshot).not.toHaveBeenCalled()
    expect(useToastStore.getState().toasts).toEqual([])
  })

  it("moves feedback between compatible edges and clears it off-edge", () => {
    const params = candidateParams()
    params.findEdgeIdAtPoint
      .mockReturnValueOnce("edge-base-middle")
      .mockReturnValueOnce("edge-middle-downstream")
      .mockReturnValueOnce(null)
    const { result } = renderHook(() => useEdgeHandlers(params))

    startConnection(result)
    act(() => {
      result.current.onConnectionPointerMove({ clientX: 100, clientY: 50 })
    })
    expect(result.current.edgeJoinCandidateEdgeId).toBe("edge-base-middle")

    act(() => {
      result.current.onConnectionPointerMove({ clientX: 300, clientY: 50 })
    })
    expect(result.current.edgeJoinCandidateEdgeId).toBe("edge-middle-downstream")

    act(() => {
      result.current.onConnectionPointerMove({ clientX: 300, clientY: 180 })
    })
    expect(result.current.edgeJoinCandidateEdgeId).toBeNull()
    expect(params.pushSnapshot).not.toHaveBeenCalled()
  })

  it.each([
    ["stale edge", "lookup", "missing-edge"],
    ["incomplete edge", "lookup", "edge-stale"],
    ["cycle", "middle", "edge-base-middle"],
  ] as const)("does not expose feedback for a %s candidate", (_label, sourceId, edgeId) => {
    const params = candidateParams()
    params.graphRef.current.edges.push({
      id: "edge-stale",
      source: "missing-node",
      target: "middle",
    } as Edge)
    params.findEdgeIdAtPoint.mockReturnValue(edgeId)
    const { result } = renderHook(() => useEdgeHandlers(params))

    startConnection(result, sourceId)
    act(() => {
      result.current.onConnectionPointerMove({ clientX: 120, clientY: 80 })
    })

    expect(result.current.edgeJoinCandidateEdgeId).toBeNull()
    expect(params.pushSnapshot).not.toHaveBeenCalled()
    expect(useToastStore.getState().toasts).toEqual([])
  })

  it("ignores non-source gestures and does not hit-test them", () => {
    const params = candidateParams()
    params.findEdgeIdAtPoint.mockReturnValue("edge-base-middle")
    const { result } = renderHook(() => useEdgeHandlers(params))

    startConnection(result, "lookup", "target")
    act(() => {
      result.current.onConnectionPointerMove({ clientX: 120, clientY: 80 })
    })

    expect(result.current.edgeJoinCandidateEdgeId).toBeNull()
    expect(params.findEdgeIdAtPoint).not.toHaveBeenCalled()
  })

  it("clears feedback on canvas leave but keeps the source gesture eligible on re-entry", () => {
    const params = candidateParams()
    params.findEdgeIdAtPoint.mockReturnValue("edge-base-middle")
    const { result } = renderHook(() => useEdgeHandlers(params))

    startConnection(result)
    act(() => {
      result.current.onConnectionPointerMove({ clientX: 120, clientY: 80 })
    })
    expect(result.current.edgeJoinCandidateEdgeId).toBe("edge-base-middle")

    act(() => {
      result.current.clearEdgeJoinCandidate()
    })
    expect(result.current.edgeJoinCandidateEdgeId).toBeNull()

    act(() => {
      result.current.onConnectionPointerMove({ clientX: 120, clientY: 80 })
    })
    expect(result.current.edgeJoinCandidateEdgeId).toBe("edge-base-middle")
  })

  it("clears candidate and active gesture state on cancellation", () => {
    const params = candidateParams()
    params.findEdgeIdAtPoint.mockReturnValue("edge-base-middle")
    const { result } = renderHook(() => useEdgeHandlers(params))

    startConnection(result)
    act(() => {
      result.current.onConnectionPointerMove({ clientX: 120, clientY: 80 })
    })
    expect(result.current.edgeJoinCandidateEdgeId).toBe("edge-base-middle")

    act(() => {
      result.current.onConnectEnd(mouseUpEvent, {
        isValid: null,
        fromNode: null,
        fromHandle: null,
        toNode: null,
        toHandle: null,
      } as never)
    })
    expect(result.current.edgeJoinCandidateEdgeId).toBeNull()

    params.findEdgeIdAtPoint.mockClear()
    act(() => {
      result.current.onConnectionPointerMove({ clientX: 120, clientY: 80 })
    })
    expect(params.findEdgeIdAtPoint).not.toHaveBeenCalled()
    expect(result.current.edgeJoinCandidateEdgeId).toBeNull()
  })

  it("clears candidate feedback when an ordinary node connection ends", () => {
    const params = candidateParams()
    params.findEdgeIdAtPoint
      .mockReturnValueOnce("edge-base-middle")
      .mockReturnValueOnce(null)
    const { result } = renderHook(() => useEdgeHandlers(params))

    startConnection(result)
    act(() => {
      result.current.onConnectionPointerMove({ clientX: 120, clientY: 80 })
    })
    expect(result.current.edgeJoinCandidateEdgeId).toBe("edge-base-middle")

    act(() => {
      result.current.onConnectionPointerMove({ clientX: 400, clientY: 80 })
    })
    expect(result.current.edgeJoinCandidateEdgeId).toBeNull()

    act(() => {
      result.current.onConnectEnd(
        mouseUpEvent,
        connectionEndState({ from: "lookup", to: "downstream" }),
      )
    })

    expect(result.current.edgeJoinCandidateEdgeId).toBeNull()
    expect(params.setEdges).toHaveBeenCalledOnce()
  })

  it("honours the hit-tested edge when handle proximity snapping reports its source node", async () => {
    const params = candidateParams()
    params.findEdgeIdAtPoint.mockReturnValue("edge-base-middle")
    const { result } = renderHook(() => useEdgeHandlers(params))

    startConnection(result)
    act(() => {
      result.current.onConnectionPointerMove({ clientX: 120, clientY: 80 })
    })
    expect(result.current.edgeJoinCandidateEdgeId).toBe("edge-base-middle")

    act(() => {
      result.current.onConnectEnd(
        { clientX: 120, clientY: 80 } as MouseEvent,
        {
          isValid: true,
          fromNode: { id: "lookup" },
          fromHandle: { id: "lookup-output", type: "source" },
          toNode: { id: "base" },
          toHandle: { id: "base-output", type: "source" },
        } as never,
      )
    })
    await flushIdentityCommit()

    expect(params.pushSnapshot).toHaveBeenCalledOnce()
    const nextEdges = params.setEdgesRaw.mock.calls[0][0] as Edge[]
    expect(nextEdges).toEqual(expect.arrayContaining([
      expect.objectContaining({
        source: "edgeJoin_1",
        target: "middle",
      }),
      expect.objectContaining({
        source: "lookup",
        target: "edgeJoin_1",
        sourceHandle: "lookup-output",
        targetHandle: "join",
      }),
    ]))
    expect(nextEdges).not.toContainEqual(expect.objectContaining({
      source: "base",
      target: "middle",
    }))
  })

  it("revalidates the announced edge at release when the graph changed mid-gesture", () => {
    const params = candidateParams()
    params.findEdgeIdAtPoint.mockReturnValue("edge-base-middle")
    const { result } = renderHook(() => useEdgeHandlers(params))

    startConnection(result)
    act(() => {
      result.current.onConnectionPointerMove({ clientX: 120, clientY: 80 })
    })
    expect(result.current.edgeJoinCandidateEdgeId).toBe("edge-base-middle")

    params.graphRef.current.edges = params.graphRef.current.edges.filter(
      (edge) => edge.id !== "edge-base-middle",
    )
    act(() => {
      result.current.onConnectEnd(
        { clientX: 120, clientY: 80 } as MouseEvent,
        connectionEndState({ from: "lookup" }),
      )
    })

    expect(result.current.edgeJoinCandidateEdgeId).toBeNull()
    expect(params.nodeIdCounter.current).toBe(0)
    expect(params.pushSnapshot).not.toHaveBeenCalled()
    expect(params.setNodesRaw).not.toHaveBeenCalled()
    expect(params.setEdgesRaw).not.toHaveBeenCalled()
    expect(useToastStore.getState().toasts).toEqual([
      expect.objectContaining({
        type: "error",
        text: "Edge join rejected: drop the connection on an existing edge",
      }),
    ])
  })
})
