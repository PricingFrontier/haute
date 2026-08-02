import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { renderHook, cleanup, act } from "@testing-library/react"
import type { Node, Edge } from "@xyflow/react"
import useSubmodelNavigation from "../useSubmodelNavigation"
import useToastStore from "../../stores/useToastStore"
import useGraphStore from "../../stores/useGraphStore"
import { makeNode } from "../../test-utils/factories"
import type { SubmodelDefinition } from "../../types/node"

vi.mock("../../api/client", () => ({
  createSubmodel: vi.fn(),
  loadSubmodel: vi.fn(),
  dissolveSubmodel: vi.fn(),
}))

vi.mock("../../utils/layout", () => ({
  getLayoutedElements: vi.fn(async (nodes: Node[]) => nodes),
}))

import { createSubmodel, loadSubmodel, dissolveSubmodel } from "../../api/client"
import { getLayoutedElements } from "../../utils/layout"
const mockCreate = vi.mocked(createSubmodel)
const mockLoad = vi.mocked(loadSubmodel)
const mockDissolve = vi.mocked(dissolveSubmodel)
const mockLayout = vi.mocked(getLayoutedElements)

const INSTANCE_ID = "instance_primary"
const DEFINITION_ID = "definition_pricing"

function makeDefinition(
  nodes: Node[] = [makeNode("child1")],
  edges: Edge[] = [],
): SubmodelDefinition {
  return {
    definitionId: DEFINITION_ID,
    file: "modules/pricing.py",
    graph: { nodes, edges },
    inputPorts: [],
    outputPorts: [],
  }
}

function makeOccurrence(instanceId = INSTANCE_ID, instanceOf?: string) {
  return makeNode(instanceId, "submodel", {
    data: {
      label: "pricing",
      nodeType: "submodel",
      config: {
        definitionId: DEFINITION_ID,
        alias: instanceId === INSTANCE_ID ? "pricing" : "pricing_copy",
        ...(instanceOf ? { instanceOf } : {}),
      },
    },
  })
}

function makeParams(overrides: Partial<Parameters<typeof useSubmodelNavigation>[0]> = {}) {
  return {
    graphRef: { current: { nodes: [makeNode("n1"), makeNode("n2"), makeOccurrence()] as Node[], edges: [] as Edge[] } },
    parentGraphRef: { current: null as { nodes: Node[]; edges: Edge[]; submodels: Record<string, unknown> } | null },
    setActiveSubmodelIdentity: vi.fn(),
    submodelsRef: { current: { [DEFINITION_ID]: makeDefinition() } as Record<string, unknown> },
    setNodesRaw: vi.fn(),
    setEdgesRaw: vi.fn(),
    setSubmodelsRaw: vi.fn(),
    setPreamble: vi.fn(),
    setSelectedNode: vi.fn(),
    setPreviewData: vi.fn(),
    preambleRef: { current: "" },
    descriptionRef: { current: "" },
    sourceFileRef: { current: "test.py" },
    sourceRevisionRef: { current: "parent-rev-1" },
    preservedBlocksRef: { current: ["import numpy as np"] },
    pipelineNameRef: { current: "test" },
    fitView: vi.fn(),
    ...overrides,
  }
}

function seedCanonicalGraph(params: ReturnType<typeof makeParams>) {
  useGraphStore.getState().loadGraphSnapshot({
    nodes: params.graphRef.current.nodes,
    edges: params.graphRef.current.edges,
    preamble: params.preambleRef.current,
    submodels: params.submodelsRef.current,
  })
}

function makeCreateResponse(
  graph: Awaited<ReturnType<typeof createSubmodel>>["graph"],
): Awaited<ReturnType<typeof createSubmodel>> {
  return {
    status: "ok",
    submodel_file: "modules/pricing.py",
    parent_file: "test.py",
    source_revision: "parent-rev-1",
    graph,
  }
}

describe("useSubmodelNavigation", () => {
  beforeEach(() => {
    useGraphStore.getState().loadGraphSnapshot({
      nodes: [],
      edges: [],
      preamble: "",
      submodels: {},
    })
    useGraphStore.setState({ lastSavedSnapshot: null })
    mockCreate.mockReset()
    mockLoad.mockReset()
    mockDissolve.mockReset()
    mockLayout.mockReset()
    mockLayout.mockImplementation(async (nodes: Node[]) => nodes)
  })

  afterEach(() => {
    cleanup()
    vi.restoreAllMocks()
  })

  it("initialises with pipeline-level view stack", () => {
    const { result } = renderHook(() => useSubmodelNavigation(makeParams()))
    expect(result.current.viewStack).toHaveLength(1)
    expect(result.current.viewStack[0]).toMatchObject({ type: "pipeline", name: "main" })
  })

  it("handleCreateSubmodel refuses while a drilled view is active", async () => {
    const params = makeParams({
      parentGraphRef: {
        current: {
          nodes: [] as Node[],
          edges: [] as Edge[],
          submodels: {} as Record<string, unknown>,
        },
      },
    })
    const { result } = renderHook(() => useSubmodelNavigation(params))

    await act(async () => {
      await result.current.handleCreateSubmodel("pricing", ["n1", "n2"])
    })

    expect(mockCreate).not.toHaveBeenCalled()
    expect(useToastStore.getState().toasts).toEqual([
      expect.objectContaining({
        type: "error",
        text: expect.stringContaining("main pipeline"),
      }),
    ])
  })

  it("does not apply a create response after a position-only canonical-store edit", async () => {
    let resolve!: (value: Awaited<ReturnType<typeof createSubmodel>>) => void
    mockCreate.mockReturnValue(new Promise((done) => { resolve = done }))
    const params = makeParams()
    seedCanonicalGraph(params)
    const { result } = renderHook(() => useSubmodelNavigation(params))
    const pending = result.current.handleCreateSubmodel("pricing", ["n1", "n2"])
    act(() => useGraphStore.getState().setNodesRaw((nodes) => nodes.map((node) =>
      node.id === "n1" ? { ...node, position: { x: 99, y: 0 } } : node)))
    await act(async () => {
      resolve(makeCreateResponse({ nodes: [], edges: [], submodels: {} }))
      await pending
    })
    expect(useGraphStore.getState().nodes.find((node) => node.id === "n1")?.position.x).toBe(99)
    expect(params.graphRef.current.nodes.map((node) => node.id)).toContain("n1")
    expect(useToastStore.getState().toasts.at(-1)?.text).toContain("workspace changed")
  })

  it("does not apply a dissolve response after a submodel-only canonical-store edit", async () => {
    let resolve!: (value: Awaited<ReturnType<typeof dissolveSubmodel>>) => void
    mockDissolve.mockReturnValue(new Promise((done) => { resolve = done }))
    const params = makeParams()
    seedCanonicalGraph(params)
    const { result } = renderHook(() => useSubmodelNavigation(params))
    const pending = result.current.handleDissolveSubmodel(INSTANCE_ID)
    const addedDefinition = {
      ...makeDefinition(),
      definitionId: "definition_other",
      file: "modules/other.py",
    }
    act(() => useGraphStore.getState().setSubmodelsRaw({
      ...useGraphStore.getState().submodels,
      definition_other: addedDefinition,
    }))
    await act(async () => {
      resolve({
        status: "ok",
        source_revision: "parent-rev-1",
        instance_id: INSTANCE_ID,
        definition_id: DEFINITION_ID,
        graph: { nodes: [], edges: [], submodels: {} },
      })
      await pending
    })
    expect(useGraphStore.getState().submodels).toHaveProperty("definition_other")
    expect(useGraphStore.getState().nodes.map((node) => node.id)).toContain(INSTANCE_ID)
    expect(params.graphRef.current.nodes.map((node) => node.id)).toContain(INSTANCE_ID)
    expect(useToastStore.getState().toasts.at(-1)?.text).toContain("workspace changed")
  })

  it("does not apply a transform after the source revision changes", async () => {
    let resolve!: (value: Awaited<ReturnType<typeof createSubmodel>>) => void
    mockCreate.mockReturnValue(new Promise((done) => { resolve = done }))
    const params = makeParams()
    seedCanonicalGraph(params)
    const { result } = renderHook(() => useSubmodelNavigation(params))
    const pending = result.current.handleCreateSubmodel("pricing", ["n1", "n2"])
    params.sourceRevisionRef.current = "parent-rev-2"
    await act(async () => {
      resolve(makeCreateResponse({ nodes: [], edges: [], submodels: {} }))
      await pending
    })
    expect(useGraphStore.getState().nodes.map((node) => node.id)).toContain("n1")
    expect(params.graphRef.current.nodes.map((node) => node.id)).toContain("n1")
    expect(useToastStore.getState().toasts.at(-1)?.text).toContain("workspace changed")
  })

  it("lets only the newest overlapping transform commit", async () => {
    let resolveFirst!: (value: Awaited<ReturnType<typeof createSubmodel>>) => void
    let resolveSecond!: (value: Awaited<ReturnType<typeof createSubmodel>>) => void
    mockCreate
      .mockReturnValueOnce(new Promise((done) => { resolveFirst = done }))
      .mockReturnValueOnce(new Promise((done) => { resolveSecond = done }))
    const params = makeParams()
    seedCanonicalGraph(params)
    const { result } = renderHook(() => useSubmodelNavigation(params))
    const first = result.current.handleCreateSubmodel("first", ["n1", "n2"])
    const second = result.current.handleCreateSubmodel("second", ["n1", "n2"])
    await act(async () => {
      resolveFirst(makeCreateResponse({
        nodes: [makeNode("first_result")],
        edges: [],
        submodels: {},
      }))
      await first
    })
    expect(useGraphStore.getState().nodes.map((node) => node.id)).toContain("n1")

    await act(async () => {
      resolveSecond(makeCreateResponse({
        nodes: [makeNode("second_result")],
        edges: [],
        submodels: {},
      }))
      await second
    })
    expect(useGraphStore.getState().nodes.map((node) => node.id)).toEqual(["second_result"])
    expect(params.graphRef.current.nodes.map((node) => node.id)).toEqual(["second_result"])
  })

  it("handleDissolveSubmodel refuses while a drilled view is active", async () => {
    const params = makeParams({
      parentGraphRef: {
        current: {
          nodes: [] as Node[],
          edges: [] as Edge[],
          submodels: {} as Record<string, unknown>,
        },
      },
    })
    const { result } = renderHook(() => useSubmodelNavigation(params))

    await act(async () => {
      await result.current.handleDissolveSubmodel(INSTANCE_ID)
    })

    expect(mockDissolve).not.toHaveBeenCalled()
    expect(useToastStore.getState().toasts).toEqual([
      expect.objectContaining({
        type: "error",
        text: expect.stringContaining("main pipeline"),
      }),
    ])
  })

  it("handleCreateSubmodel calls API and updates nodes", async () => {
    vi.useFakeTimers()
    mockCreate.mockResolvedValue({
      status: "ok",
      submodel_file: "pricing.py",
      parent_file: "test.py",
      source_revision: "parent-rev-2",
      graph: {
        nodes: [makeOccurrence()],
        edges: [],
        submodels: { [DEFINITION_ID]: makeDefinition() },
      },
    })
    const params = makeParams()
    seedCanonicalGraph(params)
    const { result } = renderHook(() => useSubmodelNavigation(params))
    await act(async () => {
      await result.current.handleCreateSubmodel("pricing", ["n1", "n2"])
    })
    expect(mockCreate).toHaveBeenCalledOnce()
    expect(params.setNodesRaw).not.toHaveBeenCalled()
    expect(params.setEdgesRaw).not.toHaveBeenCalled()
    expect(useGraphStore.getState().nodes.map((node) => node.id)).toEqual([
      INSTANCE_ID,
    ])
    const toasts = useToastStore.getState().toasts
    expect(toasts.some((t) => t.type === "success")).toBe(true)
    vi.useRealTimers()
  })

  it("creates from one current store snapshot when mirrored refs are stale", async () => {
    const currentNodes = [
      makeNode("competitor_premium", "dataInput"),
      makeNode("nb_batch", "dataInput"),
    ]
    const currentEdges: Edge[] = []
    const currentSubmodels = {}
    useGraphStore.setState({
      nodes: currentNodes,
      edges: currentEdges,
      submodels: currentSubmodels,
    })
    mockCreate.mockResolvedValue({
      status: "ok",
      submodel_file: "modules/two_inputs.py",
      parent_file: "test.py",
      source_revision: "parent-rev-2",
      graph: { nodes: [], edges: [], submodels: {} },
    })
    const params = makeParams({
      graphRef: {
        current: {
          nodes: [makeNode("polars_1", "polars")],
          edges: [],
        },
      },
      submodelsRef: { current: { stale_definition: makeDefinition() } },
    })
    const { result } = renderHook(() => useSubmodelNavigation(params))

    await act(async () => {
      await result.current.handleCreateSubmodel("two_inputs", currentNodes.map((node) => node.id))
    })

    expect(mockCreate).toHaveBeenCalledWith(expect.objectContaining({
      graph: { nodes: currentNodes, edges: currentEdges, submodels: currentSubmodels },
    }))
  })

  it("creates against the current source revision and preserves source blocks", async () => {
    mockCreate.mockResolvedValue({
      status: "ok",
      submodel_file: "modules/pricing.py",
      parent_file: "main.py",
      source_revision: "parent-rev-2",
      graph: { nodes: [], edges: [], submodels: {} },
    } as Awaited<ReturnType<typeof createSubmodel>>)
    const params = makeParams()
    seedCanonicalGraph(params)
    const { result } = renderHook(() => useSubmodelNavigation(params))

    await act(async () => {
      await result.current.handleCreateSubmodel("pricing", ["n1"])
    })

    expect(mockCreate).toHaveBeenCalledWith(expect.objectContaining({
      base_revision: "parent-rev-1",
      preserved_blocks: ["import numpy as np"],
    }))
    expect(params.sourceRevisionRef.current).toBe("parent-rev-1")
  })

  it("handleCreateSubmodel shows error toast on failure", async () => {
    mockCreate.mockRejectedValue(new Error("Create failed"))
    const params = makeParams()
    const { result } = renderHook(() => useSubmodelNavigation(params))
    await act(async () => {
      await result.current.handleCreateSubmodel("test", ["n1"])
    })
    const toasts = useToastStore.getState().toasts
    expect(toasts.some((t) => t.type === "error" && t.text.includes("Create submodel failed"))).toBe(true)
  })

  it("handleDrillIntoSubmodel loads submodel and pushes view stack", async () => {
    vi.useFakeTimers()
    mockLoad.mockResolvedValue({
      status: "ok",
      definition_id: DEFINITION_ID,
      submodel_name: "pricing",
      submodel_file: "modules/pricing.py",
      graph: {
        nodes: [makeNode("child1")],
        edges: [],
      },
    })
    const params = makeParams()
    const { result } = renderHook(() => useSubmodelNavigation(params))
    await act(async () => {
      await result.current.handleDrillIntoSubmodel(INSTANCE_ID)
    })
    expect(result.current.viewStack).toHaveLength(2)
    expect(result.current.viewStack[1]).toMatchObject({ type: "submodel", name: "pricing" })
    expect(params.setActiveSubmodelIdentity).toHaveBeenCalledWith({ instanceId: INSTANCE_ID, definitionId: DEFINITION_ID })
    expect(params.setNodesRaw).toHaveBeenCalled()
    expect(params.setSelectedNode).toHaveBeenCalledWith(null)
    vi.useRealTimers()
  })

  it("marks a created instance drill-down as read-only", async () => {
    const copyId = "instance_copy"
    const owner = makeOccurrence()
    const copy = makeOccurrence(copyId, owner.id)
    const params = makeParams({
      graphRef: {
        current: {
          nodes: [owner, copy],
          edges: [],
        },
      },
    })
    const { result } = renderHook(() => useSubmodelNavigation(params))

    await act(async () => {
      await result.current.handleDrillIntoSubmodel(copyId)
    })

    expect(result.current.viewStack[1]).toMatchObject({
      type: "submodel",
      instanceId: copyId,
      definitionId: DEFINITION_ID,
      readOnly: true,
    })
    expect(useToastStore.getState().toasts.at(-1)).toMatchObject({
      type: "info",
      text: expect.stringContaining("read-only"),
    })
  })

  it("handleDrillIntoSubmodel updates the current source file to the submodel module", async () => {
    mockLoad.mockResolvedValue({
      status: "ok",
      definition_id: DEFINITION_ID,
      submodel_name: "pricing",
      submodel_file: "modules/pricing.py",
      graph: { nodes: [makeNode("child1")], edges: [] },
    })
    const params = makeParams({
      sourceFileRef: { current: "parent.py" },
    })
    const { result } = renderHook(() => useSubmodelNavigation(params))

    await act(async () => {
      await result.current.handleDrillIntoSubmodel(INSTANCE_ID)
    })

    expect(params.sourceFileRef.current).toBe("modules/pricing.py")
  })

  it("uses the embedded definition path without loading from disk", async () => {
    mockLoad.mockResolvedValue({
      status: "ok",
      definition_id: DEFINITION_ID,
      submodel_name: "pricing",
      submodel_file: "generated/submodels/pricing_v2.py",
      graph: { nodes: [makeNode("child1")], edges: [] },
    } as Awaited<ReturnType<typeof loadSubmodel>>)
    const params = makeParams({
      sourceFileRef: { current: "pipelines/parent.py" },
      submodelsRef: {
        current: {
          [DEFINITION_ID]: {
            ...makeDefinition(),
            file: "generated/submodels/pricing_v2.py",
          },
        },
      },
    })
    const { result } = renderHook(() => useSubmodelNavigation(params))

    await act(async () => {
      await result.current.handleDrillIntoSubmodel(INSTANCE_ID)
    })

    expect(mockLoad).not.toHaveBeenCalled()
    expect(params.sourceFileRef.current).toBe("generated/submodels/pricing_v2.py")
  })

  it("handleDrillIntoSubmodel shows error toast for a missing embedded definition", async () => {
    const params = makeParams({ submodelsRef: { current: {} } })
    const { result } = renderHook(() => useSubmodelNavigation(params))
    await act(async () => {
      await result.current.handleDrillIntoSubmodel(INSTANCE_ID)
    })
    const toasts = useToastStore.getState().toasts
    expect(toasts.some((t) => t.type === "error" && t.text.includes("Drill-down failed"))).toBe(true)
  })

  it("handleBreadcrumbNavigate restores saved nodes at target depth", async () => {
    vi.useFakeTimers()
    mockLoad.mockResolvedValue({
      status: "ok",
      definition_id: DEFINITION_ID,
      submodel_name: "pricing",
      submodel_file: "modules/pricing.py",
      graph: { nodes: [makeNode("child1")], edges: [] },
    })
    const params = makeParams()
    const savedNodes = [makeNode("n1"), makeNode("n2"), makeOccurrence()]
    params.graphRef.current = { nodes: savedNodes, edges: [] }
    const { result } = renderHook(() => useSubmodelNavigation(params))
    // First drill in
    await act(async () => {
      await result.current.handleDrillIntoSubmodel(INSTANCE_ID)
    })
    expect(result.current.viewStack).toHaveLength(2)
    // Now navigate back
    act(() => {
      result.current.handleBreadcrumbNavigate(0)
    })
    expect(result.current.viewStack).toHaveLength(1)
    expect(params.setNodesRaw).toHaveBeenCalled()
    expect(params.setSelectedNode).toHaveBeenCalledWith(null)
    vi.useRealTimers()
  })

  it("handleBreadcrumbNavigate restores the reconciled parent graph and metadata", async () => {
    vi.useFakeTimers()
    mockLoad.mockResolvedValue({
      status: "ok",
      definition_id: DEFINITION_ID,
      submodel_name: "pricing",
      submodel_file: "modules/pricing.py",
      graph: { nodes: [makeNode("child1")], edges: [] },
    })
    const params = makeParams()
    const { result } = renderHook(() => useSubmodelNavigation(params))

    await act(async () => {
      await result.current.handleDrillIntoSubmodel(INSTANCE_ID)
    })

    const updatedNodes = [makeNode("root-updated"), makeOccurrence()]
    const updatedEdges: Edge[] = [
      { id: "updated-edge", source: "upstream", target: INSTANCE_ID },
    ]
    const updatedSubmodels = {
      [DEFINITION_ID]: makeDefinition([makeNode("child1")]),
    }
    params.parentGraphRef.current = {
      nodes: updatedNodes,
      edges: updatedEdges,
      submodels: updatedSubmodels,
    }

    act(() => {
      result.current.handleBreadcrumbNavigate(0)
    })

    expect(params.setNodesRaw).toHaveBeenLastCalledWith(updatedNodes)
    expect(params.setEdgesRaw).toHaveBeenLastCalledWith(
      expect.arrayContaining([expect.objectContaining({ id: "updated-edge" })]),
    )
    expect(params.setSubmodelsRaw).toHaveBeenLastCalledWith(updatedSubmodels)
    expect(params.submodelsRef.current).toEqual(updatedSubmodels)
    vi.useRealTimers()
  })

  it("handleBreadcrumbNavigate restores the parent source file when returning to main", async () => {
    vi.useFakeTimers()
    mockLoad.mockResolvedValue({
      status: "ok",
      definition_id: DEFINITION_ID,
      submodel_name: "pricing",
      submodel_file: "modules/pricing.py",
      graph: { nodes: [makeNode("child1")], edges: [] },
    })
    const params = makeParams({
      sourceFileRef: { current: "pipelines/main.py" },
    })
    const { result } = renderHook(() => useSubmodelNavigation(params))

    await act(async () => {
      await result.current.handleDrillIntoSubmodel(INSTANCE_ID)
    })
    expect(params.sourceFileRef.current).toBe("modules/pricing.py")

    act(() => {
      result.current.handleBreadcrumbNavigate(0)
    })

    expect(params.sourceFileRef.current).toBe("pipelines/main.py")
    vi.useRealTimers()
  })

  it("handleBreadcrumbNavigate restores an empty parent source file", async () => {
    vi.useFakeTimers()
    mockLoad.mockResolvedValue({
      status: "ok",
      definition_id: DEFINITION_ID,
      submodel_name: "pricing",
      submodel_file: "modules/pricing.py",
      graph: { nodes: [makeNode("child1")], edges: [] },
    })
    const params = makeParams({
      sourceFileRef: { current: "" },
    })
    const { result } = renderHook(() => useSubmodelNavigation(params))

    await act(async () => {
      await result.current.handleDrillIntoSubmodel(INSTANCE_ID)
    })
    expect(params.sourceFileRef.current).toBe("modules/pricing.py")

    act(() => {
      result.current.handleBreadcrumbNavigate(0)
    })

    expect(params.sourceFileRef.current).toBe("")
    vi.useRealTimers()
  })

  it("handleBreadcrumbNavigate does nothing when depth >= viewStack.length - 1", () => {
    const params = makeParams()
    const { result } = renderHook(() => useSubmodelNavigation(params))
    const initialStack = result.current.viewStack
    act(() => {
      result.current.handleBreadcrumbNavigate(0)
    })
    // viewStack unchanged (depth 0 === viewStack.length - 1 === 0)
    expect(result.current.viewStack).toBe(initialStack)
  })

  it("handleDissolveSubmodel calls API and updates nodes", async () => {
    vi.useFakeTimers()
    mockDissolve.mockResolvedValue({
      status: "ok",
      source_revision: "parent-rev-2",
      instance_id: INSTANCE_ID,
      definition_id: DEFINITION_ID,
      graph: {
        nodes: [makeNode("n1"), makeNode("n2")],
        edges: [],
      },
    })
    const params = makeParams()
    seedCanonicalGraph(params)
    const { result } = renderHook(() => useSubmodelNavigation(params))
    await act(async () => {
      await result.current.handleDissolveSubmodel(INSTANCE_ID)
    })
    expect(mockDissolve).toHaveBeenCalledOnce()
    expect(params.setNodesRaw).not.toHaveBeenCalled()
    expect(params.setEdgesRaw).not.toHaveBeenCalled()
    expect(params.submodelsRef.current).toEqual({})
    expect(params.setSubmodelsRaw).not.toHaveBeenCalled()
    expect(useGraphStore.getState().nodes.map((node) => node.id)).toEqual(["n1", "n2"])
    const toasts = useToastStore.getState().toasts
    expect(toasts.some((t) => t.type === "success" && t.text.includes("dissolved"))).toBe(true)
    vi.useRealTimers()
  })

  it("dissolves with concurrency metadata and preserves transform metadata", async () => {
    mockDissolve.mockResolvedValue({
      status: "ok",
      source_revision: "parent-rev-2",
      instance_id: INSTANCE_ID,
      definition_id: DEFINITION_ID,
      graph: {
        nodes: [],
        edges: [],
        submodels: {},
        preamble: "MERGED = 1",
        preserved_blocks: ["MERGED_KEEP = 2"],
      },
    } as Awaited<ReturnType<typeof dissolveSubmodel>>)
    const params = makeParams({
      preambleRef: { current: "PARENT = 1" },
      preservedBlocksRef: { current: ["PARENT_KEEP = 2"] },
    })
    seedCanonicalGraph(params)
    const { result } = renderHook(() => useSubmodelNavigation(params))

    await act(async () => {
      await result.current.handleDissolveSubmodel(INSTANCE_ID)
    })

    expect(mockDissolve).toHaveBeenCalledWith(expect.objectContaining({
      base_revision: "parent-rev-1",
      preserved_blocks: ["PARENT_KEEP = 2"],
    }))
    expect(params.sourceRevisionRef.current).toBe("parent-rev-1")
    expect(params.setPreamble).not.toHaveBeenCalled()
    expect(params.preambleRef.current).toBe("MERGED = 1")
    expect(useGraphStore.getState().preamble).toBe("MERGED = 1")
    expect(params.preservedBlocksRef.current).toEqual(["MERGED_KEEP = 2"])
    expect(useToastStore.getState().toasts.some((toast) =>
      toast.type === "success" && toast.text.includes("save to apply"),
    )).toBe(true)
  })

  it("handleDissolveSubmodel shows error toast on failure", async () => {
    mockDissolve.mockRejectedValue(new Error("Dissolve failed"))
    const params = makeParams()
    seedCanonicalGraph(params)
    const { result } = renderHook(() => useSubmodelNavigation(params))
    await act(async () => {
      await result.current.handleDissolveSubmodel(INSTANCE_ID)
    })
    const toasts = useToastStore.getState().toasts
    expect(toasts.some((t) => t.type === "error" && t.text.includes("Dissolve failed"))).toBe(true)
  })

  it("drills into a reusable occurrence by definition id and records both identities", async () => {
    mockLoad.mockResolvedValue({
      status: "ok",
      definition_id: "definition_scoring",
      submodel_name: "scoring",
      submodel_file: "modules/scoring.py",
      graph: { nodes: [makeNode("child")], edges: [] },
    })
    const primary = makeNode("instance_primary", "submodel", {
      data: {
        label: "Primary scoring",
        nodeType: "submodel",
        config: { definitionId: "definition_scoring", alias: "scoring_primary" },
      },
    })
    const secondary = makeNode("instance_secondary", "submodel", {
      data: {
        label: "Secondary scoring",
        nodeType: "submodel",
        config: { definitionId: "definition_scoring", alias: "scoring_secondary" },
      },
    })
    const definition = {
      definitionId: "definition_scoring",
      file: "modules/scoring.py",
      graph: { nodes: [makeNode("child")], edges: [] },
      inputPorts: [],
      outputPorts: [],
    }
    const params = makeParams({
      graphRef: { current: { nodes: [primary, secondary], edges: [] } },
      submodelsRef: { current: { definition_scoring: definition } },
    })
    const { result } = renderHook(() => useSubmodelNavigation(params))

    await act(async () => {
      await result.current.handleDrillIntoSubmodel("instance_primary")
    })

    expect(mockLoad).not.toHaveBeenCalled()
    expect(result.current.viewStack[1]).toMatchObject({
      type: "submodel",
      name: "Primary scoring",
      instanceId: "instance_primary",
      definitionId: "definition_scoring",
    })
    expect(useToastStore.getState().toasts).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          type: "info",
          text: expect.stringContaining("2 instances"),
        }),
      ]),
    )
  })

  it("leaves navigation state untouched when drilled-view layout fails", async () => {
    mockLoad.mockResolvedValue({
      status: "ok",
      definition_id: DEFINITION_ID,
      submodel_name: "pricing",
      submodel_file: "modules/pricing.py",
      graph: { nodes: [makeNode("child")], edges: [] },
    })
    mockLayout.mockRejectedValueOnce(new Error("Layout failed"))
    const params = makeParams({
      sourceFileRef: { current: "pipelines/main.py" },
      setCurrentSourceFile: vi.fn(),
    })
    const { result } = renderHook(() => useSubmodelNavigation(params))

    await act(async () => {
      await result.current.handleDrillIntoSubmodel(INSTANCE_ID)
    })

    expect(params.parentGraphRef.current).toBeNull()
    expect(params.sourceFileRef.current).toBe("pipelines/main.py")
    expect(params.setCurrentSourceFile).not.toHaveBeenCalled()
    expect(params.setNodesRaw).not.toHaveBeenCalled()
    expect(result.current.viewStack).toHaveLength(1)
    expect(useToastStore.getState().toasts.at(-1)).toMatchObject({
      type: "error",
      text: expect.stringContaining("Drill-down failed"),
    })
  })

  it("rejects a non-canonical occurrence instead of deriving a dissolve name", async () => {
    const occurrence = makeNode("instance_broken", "submodel", {
      data: {
        label: "Pricing",
        nodeType: "submodel",
        config: { alias: "pricing" },
      },
    })
    const params = makeParams({
      graphRef: { current: { nodes: [occurrence], edges: [] } },
      submodelsRef: { current: {} },
    })
    seedCanonicalGraph(params)
    const { result } = renderHook(() => useSubmodelNavigation(params))

    await act(async () => {
      await result.current.handleDissolveSubmodel("instance_broken")
    })

    expect(mockDissolve).not.toHaveBeenCalled()
    expect(useToastStore.getState().toasts.at(-1)).toMatchObject({
      type: "error",
      text: expect.stringContaining("malformed identity"),
    })
  })

  it("dissolves a reusable occurrence by immutable instance id", async () => {
    mockDissolve.mockResolvedValue({
      status: "ok",
      source_revision: "parent-rev-2",
      instance_id: "instance_primary",
      definition_id: "definition_scoring",
      graph: { nodes: [], edges: [], submodels: {} },
    })
    const occurrence = makeNode("instance_primary", "submodel", {
      data: {
        label: "Primary scoring",
        nodeType: "submodel",
        config: { definitionId: "definition_scoring", alias: "scoring_primary" },
      },
    })
    const params = makeParams({
      graphRef: { current: { nodes: [occurrence], edges: [] } },
      submodelsRef: {
        current: {
          definition_scoring: {
            definitionId: "definition_scoring",
            file: "modules/scoring.py",
            graph: { nodes: [], edges: [] },
            inputPorts: [],
            outputPorts: [],
          },
        },
      },
    })
    seedCanonicalGraph(params)
    const { result } = renderHook(() => useSubmodelNavigation(params))

    await act(async () => {
      await result.current.handleDissolveSubmodel("instance_primary")
    })

    expect(mockDissolve).toHaveBeenCalledWith(
      expect.objectContaining({
        instance_id: "instance_primary",
      }),
    )
  })

  it("creates one dirty undoable edit without advancing the persisted revision", async () => {
    const originalNodes = [makeNode("n1"), makeNode("n2")]
    useGraphStore.getState().loadGraphSnapshot({
      nodes: originalNodes,
      edges: [],
      preamble: "PARENT = 1",
      submodels: {},
    })
    const definition = makeDefinition([makeNode("child1")])
    mockCreate.mockResolvedValue({
      status: "ok",
      submodel_file: "modules/pricing.py",
      parent_file: "test.py",
      source_revision: "parent-rev-1",
      graph: {
        nodes: [makeOccurrence()],
        edges: [],
        submodels: { [DEFINITION_ID]: definition },
        preamble: "PARENT = 1\nCHILD_HELPER = 2",
        preserved_blocks: ["KEEP = 1"],
      },
    })
    const params = makeParams({
      preambleRef: { current: "PARENT = 1" },
      preservedBlocksRef: { current: ["KEEP = 0"] },
    })
    const { result } = renderHook(() => useSubmodelNavigation(params))

    await act(async () => {
      await result.current.handleCreateSubmodel("pricing", ["n1", "n2"])
    })

    const changed = useGraphStore.getState()
    expect(params.setNodesRaw).not.toHaveBeenCalled()
    expect(params.setEdgesRaw).not.toHaveBeenCalled()
    expect(params.setSubmodelsRaw).not.toHaveBeenCalled()
    expect(changed.nodes.map((node) => node.id)).toEqual([INSTANCE_ID])
    expect(changed.submodels).toEqual({ [DEFINITION_ID]: definition })
    expect(changed.preamble).toBe("PARENT = 1\nCHILD_HELPER = 2")
    expect(changed.undoStack).toHaveLength(1)
    expect(changed.dirty).toBe(true)
    expect(params.sourceRevisionRef.current).toBe("parent-rev-1")

    act(() => useGraphStore.getState().undo())
    expect(useGraphStore.getState().nodes).toEqual(originalNodes)
    expect(useGraphStore.getState().dirty).toBe(false)
  })

  it("drills into an unsaved embedded definition without loading from disk", async () => {
    const unsavedChild = makeNode("unsaved_child")
    const params = makeParams({
      submodelsRef: {
        current: {
          [DEFINITION_ID]: makeDefinition([unsavedChild]),
        },
      },
      sourceFileRef: { current: "parent.py" },
    })
    const { result } = renderHook(() => useSubmodelNavigation(params))

    await act(async () => {
      await result.current.handleDrillIntoSubmodel(INSTANCE_ID)
    })

    expect(mockLoad).not.toHaveBeenCalled()
    expect(result.current.viewStack).toHaveLength(2)
    expect(result.current.viewStack[1]).toMatchObject({
      definitionId: DEFINITION_ID,
      file: "modules/pricing.py",
    })
    expect(params.sourceFileRef.current).toBe("modules/pricing.py")
    expect(params.setNodesRaw).toHaveBeenCalledWith(
      expect.arrayContaining([expect.objectContaining({ id: "unsaved_child" })]),
    )
  })

  it("dissolves in one dirty undoable edit without advancing the persisted revision", async () => {
    const originalNodes = [makeOccurrence()]
    const definition = makeDefinition()
    useGraphStore.getState().loadGraphSnapshot({
      nodes: originalNodes,
      edges: [],
      preamble: "PARENT = 1",
      submodels: { [DEFINITION_ID]: definition },
    })
    mockDissolve.mockResolvedValue({
      status: "ok",
      source_revision: "parent-rev-1",
      instance_id: INSTANCE_ID,
      definition_id: DEFINITION_ID,
      graph: {
        nodes: [makeNode("child1")],
        edges: [],
        submodels: {},
        preamble: "PARENT = 1\nCHILD_HELPER = 2",
        preserved_blocks: ["KEEP = 2"],
      },
    })
    const params = makeParams({
      graphRef: { current: { nodes: originalNodes, edges: [] } },
      submodelsRef: { current: { [DEFINITION_ID]: definition } },
      preambleRef: { current: "PARENT = 1" },
    })
    const { result } = renderHook(() => useSubmodelNavigation(params))

    await act(async () => {
      await result.current.handleDissolveSubmodel(INSTANCE_ID)
    })

    const changed = useGraphStore.getState()
    expect(params.setNodesRaw).not.toHaveBeenCalled()
    expect(params.setEdgesRaw).not.toHaveBeenCalled()
    expect(params.setSubmodelsRaw).not.toHaveBeenCalled()
    expect(params.setPreamble).not.toHaveBeenCalled()
    expect(changed.nodes.map((node) => node.id)).toEqual(["child1"])
    expect(changed.submodels).toEqual({})
    expect(changed.preamble).toBe("PARENT = 1\nCHILD_HELPER = 2")
    expect(changed.undoStack).toHaveLength(1)
    expect(changed.dirty).toBe(true)
    expect(params.sourceRevisionRef.current).toBe("parent-rev-1")

    act(() => useGraphStore.getState().undo())
    expect(useGraphStore.getState().nodes).toEqual(originalNodes)
    expect(useGraphStore.getState().submodels).toEqual({ [DEFINITION_ID]: definition })
    expect(useGraphStore.getState().preamble).toBe("PARENT = 1")
    expect(useGraphStore.getState().dirty).toBe(false)
  })

})
