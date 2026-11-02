import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { renderHook, cleanup, act } from "@testing-library/react"
import type { Node, Edge } from "@xyflow/react"
import type { SubmodelDefinition } from "../../types/node"
import useSubmodelNavigation from "../useSubmodelNavigation"
import useSubmodelBoundaryEditing from "../useSubmodelBoundaryEditing"
import useToastStore from "../../stores/useToastStore"
import useGraphStore from "../../stores/useGraphStore"
import { makeNode, makeEdge } from "../../test-utils/factories"
import { normalizeEdges } from "../../utils/graphHelpers"

vi.mock("../../api/client", () => ({
  createSubmodel: vi.fn(),
  loadSubmodel: vi.fn(),
  dissolveSubmodel: vi.fn(),
}))


import { loadSubmodel } from "../../api/client"
const mockLoad = vi.mocked(loadSubmodel)

const INSTANCE_ID = "pricing"
const DEFINITION_ID = "definition_pricing"

const definition: SubmodelDefinition = {
  definitionId: DEFINITION_ID,
  file: "modules/pricing.py",
  graph: {
    nodes: [
      makeNode("child1", "edgeJoin", { data: { label: "Claims frame" } }),
      makeNode("child2", "polars", { data: { label: "Quote frame" } }),
    ],
    edges: [],
  },
  inputPorts: [
    { name: "claims", targets: [{ nodeId: "child1", handleId: "join" }] },
    { name: "quote", targets: [{ nodeId: "child2", handleId: null }] },
  ],
  outputPorts: [
    { name: "claims_result", source: { nodeId: "child1", handleId: "result" } },
  ],
}

function occurrence() {
  return makeNode(INSTANCE_ID, "submodel", {
    data: {
      label: "pricing",
      nodeType: "submodel",
      config: { definitionId: DEFINITION_ID, alias: "pricing" },
    },
  })
}

function makeParams(overrides: Partial<Parameters<typeof useSubmodelNavigation>[0]> = {}) {
  return {
    graphRef: { current: { nodes: [occurrence()] as Node[], edges: [] as Edge[] } },
    parentGraphRef: { current: null as { nodes: Node[]; edges: Edge[]; submodels: Record<string, unknown> } | null },
    setActiveSubmodelIdentity: vi.fn(),
    submodelsRef: { current: { [DEFINITION_ID]: definition } as Record<string, unknown> },
    setNodesRaw: vi.fn(),
    setEdgesRaw: vi.fn(),
    setSelectedNode: vi.fn(),
    setPreviewData: vi.fn(),
    cancelPreview: vi.fn(),
    clearTrace: vi.fn(),
    setPreamble: vi.fn(),
    preambleRef: { current: "" },
    descriptionRef: { current: "" },
    sourceFileRef: { current: "test.py" },
    sourceRevisionRef: { current: "revision-test" },
    preservedBlocksRef: { current: [] as string[] },
    pipelineNameRef: { current: "test" },
    fitView: vi.fn(),
    reservedApiInputFrameLabels: new Set<string>(),
    resolveGraphIdentities: vi.fn(async ({ nodes, edges }) => ({ nodes: [...nodes], edges: [...edges] })),
    ...overrides,
  }
}

describe("useSubmodelNavigation - canonical port building & branch gaps", () => {
  beforeEach(() => {
    useGraphStore.getState().resetForTests()
    mockLoad.mockReset()
  })

  afterEach(() => {
    cleanup()
    vi.restoreAllMocks()
  })

  it("leaves the document clean and its history as it was after an untouched visit", async () => {
    // The real projection, boundary reconciler and store, with authored child positions.
    const positioned: SubmodelDefinition = {
      ...definition,
      graph: {
        nodes: definition.graph.nodes.map((node, index) => ({
          ...node,
          position: { x: 700 + index * 400, y: 300 + index * 120 },
        })),
        edges: [],
      },
    }
    // Nodes carry the identities the server's resolver gives them.
    const handles = { claims: "claims", quote: "quote", claims_result: "claims_result" }
    const params = makeParams({
      submodelsRef: { current: { [DEFINITION_ID]: positioned } as Record<string, unknown> },
      resolveGraphIdentities: vi.fn(async ({ nodes, edges }) => ({
        nodes: nodes.map((node: Node) => node.type === "submodelPort"
          ? { ...node, data: { ...node.data, _functionName: node.id, _defaultInputName: null, _sourceHandleInputNames: handles } }
          : { ...node, data: { ...node.data, _functionName: node.id, _defaultInputName: node.id, _sourceHandleInputNames: { result: `${node.id}_result` } } }),
        edges: [...edges],
      })),
    })
    params.graphRef.current = {
      nodes: [makeNode("src1"), makeNode("tgt1"), occurrence()],
      // Loaded edges are normalized, as both document load paths do.
      edges: normalizeEdges([
        { ...makeEdge("src1", INSTANCE_ID), id: "input-claims", sourceHandle: "proposer_claims", targetHandle: "in__claims" } as Edge,
        { ...makeEdge("src1", INSTANCE_ID), id: "input-quote", sourceHandle: "quote_info", targetHandle: "in__quote" } as Edge,
        { ...makeEdge(INSTANCE_ID, "tgt1"), id: "output-claims", sourceHandle: "out__claims_result" } as Edge,
      ]),
    }
    useGraphStore.getState().loadGraphSnapshot({
      nodes: params.graphRef.current.nodes,
      edges: params.graphRef.current.edges,
      preamble: "",
      submodels: params.submodelsRef.current,
    })
    const saved = useGraphStore.getState().persistedFingerprint
    // An earlier root edit, undone: the root's redo stack must survive the visit.
    act(() => useGraphStore.getState().setPreamble("import math"))
    act(() => useGraphStore.getState().undo())

    const { result } = renderHook(() => {
      const navigation = useSubmodelNavigation(params)
      const nodes = useGraphStore((state) => state.nodes)
      const edges = useGraphStore((state) => state.edges)
      const submodels = useGraphStore((state) => state.submodels)
      const view = navigation.viewStack.at(-1)
      useSubmodelBoundaryEditing({
        activeSubmodelName: view?.type === "submodel" ? view.name : null,
        activeSubmodelInstanceId: view?.type === "submodel" ? view.instanceId ?? null : null,
        activeSubmodelDefinitionId: view?.type === "submodel" ? view.definitionId ?? null : null,
        nodes,
        edges,
        submodels,
        graphRef: params.graphRef,
        parentGraphRef: params.parentGraphRef,
        submodelsRef: params.submodelsRef,
        setNodesAndEdgesAndSubmodels: useGraphStore.getState().setNodesAndEdgesAndSubmodels,
        reservedApiInputFrameLabels: new Set<string>(),
        resolveGraphIdentities: params.resolveGraphIdentities,
      })
      return navigation
    })

    await act(async () => {
      await result.current.handleDrillIntoSubmodel(INSTANCE_ID)
    })
    expect(result.current.viewStack).toHaveLength(2)
    expect(useGraphStore.getState().dirty).toBe(false)
    expect(params.parentGraphRef.current).not.toBeNull()

    act(() => result.current.handleBreadcrumbNavigate(0))

    const state = useGraphStore.getState()
    expect(state.dirty).toBe(false)
    expect(state.persistedFingerprint).toBe(saved)
    expect(state.undoStack).toHaveLength(0)
    expect(state.redoStack).toHaveLength(1)
    const children = (state.submodels[DEFINITION_ID] as SubmodelDefinition).graph.nodes
    expect(children.map((node) => node.position)).toEqual([{ x: 700, y: 300 }, { x: 1100, y: 420 }])
  })

  it("projects declared parent boundaries into Input and Output cards", async () => {
    vi.useFakeTimers()
    const params = makeParams()
    params.graphRef.current = {
      nodes: [makeNode("src1"), makeNode("tgt1"), occurrence()],
      edges: [
        { ...makeEdge("src1", INSTANCE_ID), id: "input-claims", sourceHandle: "proposer_claims", targetHandle: "in__claims" } as Edge,
        { ...makeEdge("src1", INSTANCE_ID), id: "input-quote", sourceHandle: "quote_info", targetHandle: "in__quote" } as Edge,
        { ...makeEdge(INSTANCE_ID, "tgt1"), id: "output-claims", sourceHandle: "out__claims_result" } as Edge,
      ],
    }
    const { result } = renderHook(() => useSubmodelNavigation(params))

    await act(async () => {
      await result.current.handleDrillIntoSubmodel(INSTANCE_ID)
    })

    expect(mockLoad).not.toHaveBeenCalled()
    const lastNodes: Node[] = useGraphStore.getState().nodes
    const boundaries = lastNodes.filter((node) => node.type === "submodelPort")
    expect(boundaries).toHaveLength(2)
    const input = boundaries.find((node) => node.data.portDirection === "input")
    const output = boundaries.find((node) => node.data.portDirection === "output")
    expect(input?.data).toMatchObject({
      label: "INPUT",
      ports: [{ id: "claims", label: "claims" }, { id: "quote", label: "quote" }],
    })
    expect(output?.data).toMatchObject({ label: "OUTPUT", ports: [] })

    const lastEdges: Edge[] = useGraphStore.getState().edges
    expect(lastEdges).toEqual(expect.arrayContaining([
      expect.objectContaining({ source: input?.id, sourceHandle: "claims", target: "child1", targetHandle: "join" }),
      expect.objectContaining({ source: input?.id, sourceHandle: "quote", target: "child2", targetHandle: null }),
      expect.objectContaining({ source: "child1", sourceHandle: "result", target: output?.id, targetHandle: null }),
    ]))
    vi.useRealTimers()
  })

  it("rejects a parent edge with an undeclared boundary handle", async () => {
    const params = makeParams({
      graphRef: {
        current: {
          nodes: [makeNode("src1"), occurrence()],
          edges: [{ ...makeEdge("src1", INSTANCE_ID), targetHandle: "in__missing" } as Edge],
        },
      },
    })
    const { result } = renderHook(() => useSubmodelNavigation(params))

    await act(async () => {
      await result.current.handleDrillIntoSubmodel(INSTANCE_ID)
    })

    expect(result.current.viewStack).toHaveLength(1)
    expect(useGraphStore.getState().submodelView).toBeNull()
    expect(useToastStore.getState().toasts).toEqual(expect.arrayContaining([
      expect.objectContaining({ type: "error", text: expect.stringContaining("undeclared") }),
    ]))
  })

  it("handleBreadcrumbNavigate clears parentGraphRef only when returning to depth 0", async () => {
    vi.useFakeTimers()
    const params = makeParams()
    const { result } = renderHook(() => useSubmodelNavigation(params))
    await act(async () => {
      await result.current.handleDrillIntoSubmodel(INSTANCE_ID)
    })
    expect(params.parentGraphRef.current).not.toBeNull()
    expect(params.setActiveSubmodelIdentity).toHaveBeenCalledWith({ instanceId: INSTANCE_ID, definitionId: DEFINITION_ID })

    act(() => {
      result.current.handleBreadcrumbNavigate(0)
    })
    expect(params.parentGraphRef.current).toBeNull()
    expect(params.setActiveSubmodelIdentity).toHaveBeenLastCalledWith(null)
    expect(result.current.viewStack).toHaveLength(1)
    vi.useRealTimers()
  })

  it("handleDrillIntoSubmodel no-ops when the embedded graph cannot satisfy its ports", async () => {
    const params = makeParams({
      submodelsRef: {
        current: {
          [DEFINITION_ID]: {
            ...definition,
            graph: { nodes: [], edges: [] },
          },
        },
      },
    })
    const { result } = renderHook(() => useSubmodelNavigation(params))
    await act(async () => {
      await result.current.handleDrillIntoSubmodel(INSTANCE_ID)
    })
    expect(mockLoad).not.toHaveBeenCalled()
    expect(result.current.viewStack).toHaveLength(1)
    expect(useGraphStore.getState().submodelView).toBeNull()
  })
})
