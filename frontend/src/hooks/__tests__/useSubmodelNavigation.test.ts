import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { renderHook, cleanup, act } from "@testing-library/react"
import type { Node, Edge } from "@xyflow/react"
import useSubmodelNavigation from "../useSubmodelNavigation"
import useToastStore from "../../stores/useToastStore"
import useGraphStore from "../../stores/useGraphStore"
import { makeNode } from "../../test-utils/factories"

vi.mock("../../api/client", () => ({
  createSubmodel: vi.fn(),
  loadSubmodel: vi.fn(),
  dissolveSubmodel: vi.fn(),
}))

vi.mock("../../utils/layout", () => ({
  getLayoutedElements: vi.fn(async (nodes: Node[]) => nodes),
}))

import { createSubmodel, loadSubmodel, dissolveSubmodel } from "../../api/client"
const mockCreate = vi.mocked(createSubmodel)
const mockLoad = vi.mocked(loadSubmodel)
const mockDissolve = vi.mocked(dissolveSubmodel)

function makeParams(overrides: Partial<Parameters<typeof useSubmodelNavigation>[0]> = {}) {
  return {
    graphRef: { current: { nodes: [makeNode("n1"), makeNode("n2")] as Node[], edges: [] as Edge[] } },
    parentGraphRef: { current: null as { nodes: Node[]; edges: Edge[]; submodels: Record<string, unknown> } | null },
    submodelsRef: { current: {} as Record<string, unknown> },
    setNodesRaw: vi.fn(),
    setEdgesRaw: vi.fn(),
    setSelectedNode: vi.fn(),
    setPreviewData: vi.fn(),
    preambleRef: { current: "" },
    descriptionRef: { current: "" },
    sourceFileRef: { current: "test.py" },
    pipelineNameRef: { current: "test" },
    fitView: vi.fn(),
    ...overrides,
  }
}

describe("useSubmodelNavigation", () => {
  beforeEach(() => {
    useToastStore.setState({ toasts: [], _toastCounter: 0 })
    useGraphStore.setState({ lastSavedSnapshot: null })
    mockCreate.mockReset()
    mockLoad.mockReset()
    mockDissolve.mockReset()
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

  it("handleCreateSubmodel calls API and updates nodes", async () => {
    vi.useFakeTimers()
    mockCreate.mockResolvedValue({
      status: "ok",
      submodel_file: "pricing.py",
      parent_file: "test.py",
      graph: {
        nodes: [makeNode("submodel__pricing")],
        edges: [],
        submodels: { pricing: {} },
      },
    })
    const params = makeParams()
    const { result } = renderHook(() => useSubmodelNavigation(params))
    await act(async () => {
      await result.current.handleCreateSubmodel("pricing", ["n1", "n2"])
    })
    expect(mockCreate).toHaveBeenCalledOnce()
    expect(params.setNodesRaw).toHaveBeenCalled()
    expect(params.setEdgesRaw).toHaveBeenCalled()
    const toasts = useToastStore.getState().toasts
    expect(toasts.some((t) => t.type === "success")).toBe(true)
    vi.useRealTimers()
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
      submodel_name: "pricing",
      graph: {
        nodes: [makeNode("child1")],
        edges: [],
      },
    })
    const params = makeParams()
    const { result } = renderHook(() => useSubmodelNavigation(params))
    await act(async () => {
      await result.current.handleDrillIntoSubmodel("submodel__pricing")
    })
    expect(result.current.viewStack).toHaveLength(2)
    expect(result.current.viewStack[1]).toMatchObject({ type: "submodel", name: "pricing" })
    expect(params.setNodesRaw).toHaveBeenCalled()
    expect(params.setSelectedNode).toHaveBeenCalledWith(null)
    vi.useRealTimers()
  })

  it("handleDrillIntoSubmodel updates the current source file to the submodel module", async () => {
    mockLoad.mockResolvedValue({
      status: "ok",
      submodel_name: "pricing",
      graph: { nodes: [makeNode("child1")], edges: [] },
    })
    const params = makeParams({
      sourceFileRef: { current: "parent.py" },
    })
    const { result } = renderHook(() => useSubmodelNavigation(params))

    await act(async () => {
      await result.current.handleDrillIntoSubmodel("submodel__pricing")
    })

    expect(params.sourceFileRef.current).toBe("modules/pricing.py")
  })

  it("handleDrillIntoSubmodel shows error toast on failure", async () => {
    mockLoad.mockRejectedValue(new Error("Load failed"))
    const params = makeParams()
    const { result } = renderHook(() => useSubmodelNavigation(params))
    await act(async () => {
      await result.current.handleDrillIntoSubmodel("submodel__test")
    })
    const toasts = useToastStore.getState().toasts
    expect(toasts.some((t) => t.type === "error" && t.text.includes("Drill-down failed"))).toBe(true)
  })

  it("handleBreadcrumbNavigate restores saved nodes at target depth", async () => {
    vi.useFakeTimers()
    mockLoad.mockResolvedValue({
      status: "ok",
      submodel_name: "pricing",
      graph: { nodes: [makeNode("child1")], edges: [] },
    })
    const params = makeParams()
    const savedNodes = [makeNode("n1"), makeNode("n2")]
    params.graphRef.current = { nodes: savedNodes, edges: [] }
    const { result } = renderHook(() => useSubmodelNavigation(params))
    // First drill in
    await act(async () => {
      await result.current.handleDrillIntoSubmodel("submodel__pricing")
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

  it("handleBreadcrumbNavigate restores the parent source file when returning to main", async () => {
    vi.useFakeTimers()
    mockLoad.mockResolvedValue({
      status: "ok",
      submodel_name: "pricing",
      graph: { nodes: [makeNode("child1")], edges: [] },
    })
    const params = makeParams({
      sourceFileRef: { current: "pipelines/main.py" },
    })
    const { result } = renderHook(() => useSubmodelNavigation(params))

    await act(async () => {
      await result.current.handleDrillIntoSubmodel("submodel__pricing")
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
      submodel_name: "pricing",
      graph: { nodes: [makeNode("child1")], edges: [] },
    })
    const params = makeParams({
      sourceFileRef: { current: "" },
    })
    const { result } = renderHook(() => useSubmodelNavigation(params))

    await act(async () => {
      await result.current.handleDrillIntoSubmodel("submodel__pricing")
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
      graph: {
        nodes: [makeNode("n1"), makeNode("n2")],
        edges: [],
      },
    })
    const params = makeParams()
    const { result } = renderHook(() => useSubmodelNavigation(params))
    await act(async () => {
      await result.current.handleDissolveSubmodel("pricing")
    })
    expect(mockDissolve).toHaveBeenCalledOnce()
    expect(params.setNodesRaw).toHaveBeenCalled()
    const toasts = useToastStore.getState().toasts
    expect(toasts.some((t) => t.type === "success" && t.text.includes("dissolved"))).toBe(true)
    vi.useRealTimers()
  })

  it("handleDissolveSubmodel shows error toast on failure", async () => {
    mockDissolve.mockRejectedValue(new Error("Dissolve failed"))
    const params = makeParams()
    const { result } = renderHook(() => useSubmodelNavigation(params))
    await act(async () => {
      await result.current.handleDissolveSubmodel("test")
    })
    const toasts = useToastStore.getState().toasts
    expect(toasts.some((t) => t.type === "error" && t.text.includes("Dissolve failed"))).toBe(true)
  })
})
