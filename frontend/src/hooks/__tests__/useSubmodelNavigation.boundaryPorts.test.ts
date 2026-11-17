/**
 * Drilling into a submodel: its declared boundaries become Input and Output cards, an
 * undeclared parent handle is refused, an untouched visit leaves the document and its history
 * clean, and breadcrumb navigation closes the open submodel only on returning to the top level.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { renderHook, cleanup, act } from "@testing-library/react"
import type { Node, Edge } from "@xyflow/react"
import type { SubmodelDefinition } from "../../types/node"
import useSubmodelNavigation from "../useSubmodelNavigation"
import useSubmodelBoundaryEditing from "../useSubmodelBoundaryEditing"
import useToastStore from "../../stores/useToastStore"
import useGraphStore from "../../stores/useGraphStore"
import { makeEdge, makeIdentifiedNode, makeNode } from "../../test-utils/factories"
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
      makeIdentifiedNode("child1", "edgeJoin", { data: { label: "Claims frame" } }),
      makeIdentifiedNode("child2", "polars", { data: { label: "Quote frame" } }),
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
      config: { definition_id: DEFINITION_ID, alias: "pricing" },
    },
  })
}

function makeParams(overrides: Partial<Parameters<typeof useSubmodelNavigation>[0]> = {}) {
  return {
    setSelectedNode: vi.fn(),
    setPreviewData: vi.fn(),
    cancelPreview: vi.fn(),
    clearTrace: vi.fn(),
    lastSelectedKeyRef: { current: null as string | null },
    setPreamble: vi.fn(),
    descriptionRef: { current: "" },
    sourceFileRef: { current: "test.py" },
    sourceRevisionRef: { current: "revision-test" },
    preservedBlocksRef: { current: [] as string[] },
    pipelineNameRef: { current: "test" },
    fitView: vi.fn(),
    reservedApiInputFrameLabels: new Set<string>(),
    ...overrides,
  }
}

type CanvasDocument = { nodes: Node[]; edges: Edge[]; submodels: Record<string, unknown> }

/** Load the document the hooks read into the graph store: the occurrence and its definition unless overridden. */
function seed(pipeline: Partial<CanvasDocument> = {}) {
  useGraphStore.getState().loadGraphSnapshot({
    nodes: [occurrence()],
    edges: [],
    submodels: { [DEFINITION_ID]: definition },
    ...pipeline,
    preamble: "",
  })
}

describe("useSubmodelNavigation - canonical port building and drill-in navigation", () => {
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
    const params = makeParams()
    seed({
      nodes: [makeNode("src1"), makeNode("tgt1"), occurrence()],
      // Loaded edges are normalized, as both document load paths do.
      edges: normalizeEdges([
        { ...makeEdge("src1", INSTANCE_ID), id: "input-claims", sourceHandle: "proposer_claims", targetHandle: "in__claims" } as Edge,
        { ...makeEdge("src1", INSTANCE_ID), id: "input-quote", sourceHandle: "quote_info", targetHandle: "in__quote" } as Edge,
        { ...makeEdge(INSTANCE_ID, "tgt1"), id: "output-claims", sourceHandle: "out__claims_result" } as Edge,
      ]),
      submodels: { [DEFINITION_ID]: positioned },
    })
    const saved = useGraphStore.getState().persistedFingerprint
    // An earlier root edit, undone: the root's redo stack must survive the visit.
    act(() => useGraphStore.getState().setPreamble("import math"))
    act(() => useGraphStore.getState().undo())

    const { result } = renderHook(() => {
      const navigation = useSubmodelNavigation(params)
      useSubmodelBoundaryEditing({
        setNodesAndEdgesAndSubmodels: useGraphStore.getState().setNodesAndEdgesAndSubmodels,
        reservedApiInputFrameLabels: new Set<string>(),
      })
      return navigation
    })

    await act(async () => {
      await result.current.handleDrillIntoSubmodel(INSTANCE_ID)
    })
    expect(result.current.viewStack).toHaveLength(2)
    expect(useGraphStore.getState().dirty).toBe(false)
    expect(useGraphStore.getState().focus).not.toBeNull()

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
    seed({
      nodes: [makeNode("src1"), makeNode("tgt1"), occurrence()],
      edges: [
        { ...makeEdge("src1", INSTANCE_ID), id: "input-claims", sourceHandle: "proposer_claims", targetHandle: "in__claims" } as Edge,
        { ...makeEdge("src1", INSTANCE_ID), id: "input-quote", sourceHandle: "quote_info", targetHandle: "in__quote" } as Edge,
        { ...makeEdge(INSTANCE_ID, "tgt1"), id: "output-claims", sourceHandle: "out__claims_result" } as Edge,
      ],
    })
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
    const params = makeParams()
    seed({
      nodes: [makeNode("src1"), occurrence()],
      edges: [{ ...makeEdge("src1", INSTANCE_ID), targetHandle: "in__missing" } as Edge],
    })
    const { result } = renderHook(() => useSubmodelNavigation(params))

    await act(async () => {
      await result.current.handleDrillIntoSubmodel(INSTANCE_ID)
    })

    expect(result.current.viewStack).toHaveLength(1)
    expect(useGraphStore.getState().focus).toBeNull()
    expect(useToastStore.getState().toasts).toEqual(expect.arrayContaining([
      expect.objectContaining({ type: "error", text: expect.stringContaining("undeclared") }),
    ]))
  })

  it("handleBreadcrumbNavigate closes the open submodel only when returning to depth 0", async () => {
    vi.useFakeTimers()
    const params = makeParams()
    seed()
    const { result } = renderHook(() => useSubmodelNavigation(params))
    await act(async () => {
      await result.current.handleDrillIntoSubmodel(INSTANCE_ID)
    })
    expect(useGraphStore.getState().focus).toMatchObject({ instanceId: INSTANCE_ID, definitionId: DEFINITION_ID })

    act(() => {
      result.current.handleBreadcrumbNavigate(0)
    })
    expect(useGraphStore.getState().focus).toBeNull()
    expect(result.current.viewStack).toHaveLength(1)
    vi.useRealTimers()
  })

  it("handleDrillIntoSubmodel no-ops when the embedded graph cannot satisfy its ports", async () => {
    const params = makeParams()
    seed({
      submodels: {
        [DEFINITION_ID]: {
          ...definition,
          graph: { nodes: [], edges: [] },
        },
      },
    })
    const { result } = renderHook(() => useSubmodelNavigation(params))
    await act(async () => {
      await result.current.handleDrillIntoSubmodel(INSTANCE_ID)
    })
    expect(mockLoad).not.toHaveBeenCalled()
    expect(result.current.viewStack).toHaveLength(1)
    expect(useGraphStore.getState().focus).toBeNull()
  })
})
