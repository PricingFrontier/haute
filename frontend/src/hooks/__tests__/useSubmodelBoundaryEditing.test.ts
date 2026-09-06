import { act, renderHook } from "@testing-library/react"
import type { Edge, Node } from "@xyflow/react"
import { beforeEach, describe, expect, it, vi } from "vitest"
import useSubmodelBoundaryEditing from "../useSubmodelBoundaryEditing"
import useToastStore from "../../stores/useToastStore"
import { makeEdge, makeNode } from "../../test-utils/factories"
import type { PipelineEdge, SubmodelDefinition, SubmodelPortData } from "../../types/node"
import { buildSubmodelViewGraph } from "../../utils/submodelViewGraph"
import { SUBMODEL_INPUT_HANDLE } from "../../utils/flowHandles"
import { cloneGraphSnapshot } from "../../utils/graphSnapshot"

const SUBMODEL_NAME = "pricing"
const PLACEHOLDER_ID = "pricing"
const DEFINITION_ID = "definition_pricing"

type FixtureOptions = {
  bindInput?: boolean
  includeInputPort?: boolean
  outputPorts?: string[]
  includeInternalEdge?: boolean
  bindOutputConsumers?: boolean
}
type GraphIdentityRequest = {
  nodes: readonly Node[]
  edges: readonly Edge[]
  submodels: Record<string, unknown>
  reservedApiInputFrameLabels: ReadonlySet<string>
}
function makeFixture({
  bindInput = false,
  includeInputPort = true,
  outputPorts = [],
  includeInternalEdge = false,
  bindOutputConsumers = true,
}: FixtureOptions = {}) {
  const identify = (node: Node) => ({
    ...node,
    data: {
      ...node.data,
      _functionName: `${node.id}_function`,
      _defaultInputName: `${node.id}_input`,
      _sourceHandleInputNames: {},
    },
  })
  const childNodes = [identify(makeNode("child_a")), identify(makeNode("child_b"))]
  const childEdges = includeInternalEdge
    ? [{ ...makeEdge("child_a", "child_b", { id: "internal" }), data: { _inputName: "child_a_input" } }]
    : []
  const placeholder = makeNode(PLACEHOLDER_ID, "submodel", {
    data: {
      label: SUBMODEL_NAME,
      nodeType: "submodel",
      config: { definitionId: DEFINITION_ID, alias: "pricing" },
      _functionName: "pricing_function",
      _defaultInputName: null,
      _sourceHandleInputNames: Object.fromEntries(
        outputPorts.map((name) => [`out__${name}`, `Public_${name}`]),
      ),
    },
  })
  const parentNodes = [
    identify(makeNode("external")),
    identify(makeNode("consumer_a")),
    identify(makeNode("consumer_b")),
    placeholder,
  ]
  const parentEdges: PipelineEdge[] = []
  if (bindInput && includeInputPort) parentEdges.push({
    ...makeEdge("external", PLACEHOLDER_ID, { id: "incoming" }),
    targetHandle: "in__incoming",
    data: { _inputName: "external_input" },
  })
  for (const childId of bindOutputConsumers ? outputPorts : []) parentEdges.push(
    {
      ...makeEdge(PLACEHOLDER_ID, "consumer_a", { id: `consumer-a-${childId}` }),
      sourceHandle: `out__${childId}`,
      data: { _inputName: `Public_${childId}` },
    },
    {
      ...makeEdge(PLACEHOLDER_ID, "consumer_b", { id: `consumer-b-${childId}` }),
      sourceHandle: `out__${childId}`,
      data: { _inputName: `Public_${childId}` },
    },
  )
  const definition: SubmodelDefinition = {
    definitionId: DEFINITION_ID,
    file: "modules/pricing.py",
    graph: { nodes: childNodes, edges: childEdges },
    inputPorts: includeInputPort ? [{
      name: "incoming",
      targets: [{ nodeId: "child_a", handleId: null }],
    }] : [],
    outputPorts: outputPorts.map((name) => ({
      name,
      source: { nodeId: name, handleId: null },
    })),
  }
  const submodels = { [DEFINITION_ID]: definition }
  const view = buildSubmodelViewGraph({ submodelName: SUBMODEL_NAME, instanceId: PLACEHOLDER_ID, definition, childNodes, childEdges, parentNodes, parentEdges })
  const inputBoundaryId = view.nodes.find((node) =>
    node.type === "submodelPort" && node.data.portDirection === "input")!.id
  const viewNodes = view.nodes.map((node) => {
    if (node.type !== "submodelPort") return node
    const input = node.id === inputBoundaryId
    return {
      ...node,
      data: {
        ...node.data,
        _functionName: input ? "input_boundary" : "output_boundary",
        _defaultInputName: null,
        _sourceHandleInputNames: input && includeInputPort
          ? { incoming: "Incoming_policy_data" }
          : {},
      },
    }
  })
  const viewEdges = view.edges.map((edge) => ({
    ...edge,
    data: {
      ...edge.data,
      _inputName: edge.source === inputBoundaryId ? "Incoming_policy_data" : `${edge.source}_input`,
    },
  }))
  const identifiedView = { nodes: viewNodes, edges: viewEdges }
  const graphRef = { current: { nodes: viewNodes, edges: viewEdges as Edge[] } }
  const parentGraphRef = { current: { nodes: parentNodes, edges: parentEdges, submodels: submodels as Record<string, unknown> } }
  const submodelsRef = { current: submodels as Record<string, unknown> }
  const setNodesAndEdgesAndSubmodels = vi.fn()
  return { childNodes, childEdges, parentNodes, parentEdges, submodels: submodels as Record<string, unknown>, view: identifiedView, graphRef, parentGraphRef, submodelsRef, setNodesAndEdgesAndSubmodels }
}
function hookParams(fixture: ReturnType<typeof makeFixture>) {
  return {
    activeSubmodelName: SUBMODEL_NAME,
    activeSubmodelInstanceId: PLACEHOLDER_ID,
    activeSubmodelDefinitionId: DEFINITION_ID,
    nodes: fixture.view.nodes,
    edges: fixture.view.edges as PipelineEdge[],
    submodels: fixture.submodels,
    graphRef: fixture.graphRef,
    parentGraphRef: fixture.parentGraphRef,
    submodelsRef: fixture.submodelsRef,
    setNodesAndEdgesAndSubmodels: fixture.setNodesAndEdgesAndSubmodels,
    reservedApiInputFrameLabels: new Set<string>(),
    resolveGraphIdentities: vi.fn(async ({ nodes, edges }) => ({
      nodes: [...nodes],
      edges: [...edges],
    })),
  }
}
describe("useSubmodelBoundaryEditing", () => {
  beforeEach(() => useToastStore.setState({ toasts: [] }))

  it("atomically commits a parent-created public input and canonical edge", () => {
    const fixture = makeFixture({ includeInputPort: false })
    fixture.graphRef.current = {
      nodes: fixture.parentNodes,
      edges: fixture.parentEdges,
    }
    const parentGraphRef = { current: null }
    const { result } = renderHook(() => useSubmodelBoundaryEditing({
      ...hookParams(fixture),
      activeSubmodelName: null,
      activeSubmodelInstanceId: null,
      activeSubmodelDefinitionId: null,
      nodes: fixture.parentNodes,
      edges: fixture.parentEdges,
      graphRef: fixture.graphRef,
      parentGraphRef,
    }))
    act(() => {
      expect(result.current.commitBoundaryConnection({
        source: "external",
        sourceHandle: null,
        target: PLACEHOLDER_ID,
        targetHandle: SUBMODEL_INPUT_HANDLE,
      })).toBe(true)
    })
    expect(fixture.setNodesAndEdgesAndSubmodels).toHaveBeenCalledOnce()
    expect(fixture.submodelsRef.current[DEFINITION_ID]).toMatchObject({
      inputPorts: [{
        name: "external_input",
        targets: [],
      }],
    })
    expect(fixture.graphRef.current.edges).toEqual([expect.objectContaining({
      source: "external",
      target: PLACEHOLDER_ID,
      targetHandle: "in__external_input",
      data: { _inputName: "external_input" },
    })])
  })

  it("adds a second internal target to a declared public input", () => {
    const fixture = makeFixture({ bindInput: true })
    const input = fixture.view.nodes.find(
      (node) => (node.data as unknown as SubmodelPortData).portDirection === "input",
    )
    const inputData = input?.data as unknown as SubmodelPortData
    expect(inputData.ports).toHaveLength(1)
    expect(fixture.view.edges).toHaveLength(1)

    const { result } = renderHook(() => useSubmodelBoundaryEditing(hookParams(fixture)))
    act(() => {
      expect(result.current.commitBoundaryConnection({
        source: input!.id,
        sourceHandle: inputData.ports[0].id,
        target: "child_b",
        targetHandle: null,
      })).toBe(true)
    })

    expect(fixture.setNodesAndEdgesAndSubmodels).toHaveBeenCalledOnce()
    expect(fixture.parentGraphRef.current?.edges).toEqual([
      expect.objectContaining({
        id: "incoming",
        targetHandle: "in__incoming",
      }),
    ])
    const definition = fixture.submodelsRef.current[DEFINITION_ID] as {
      inputPorts: Array<{ targets: Array<{ nodeId: string }> }>
    }
    expect(definition.inputPorts[0].targets.map((target) => target.nodeId)).toEqual([
      "child_a",
      "child_b",
    ])
  })

  it("explicitly removes a bound public input in one atomic commit", () => {
    const fixture = makeFixture({ bindInput: true })
    const { result } = renderHook(() => useSubmodelBoundaryEditing(hookParams(fixture)))

    act(() => {
      expect(result.current.deleteBoundaryInputPort("incoming")).toBe(true)
    })

    expect(fixture.setNodesAndEdgesAndSubmodels).toHaveBeenCalledOnce()
    expect(fixture.parentGraphRef.current?.edges).toEqual([])
    expect(fixture.submodelsRef.current[DEFINITION_ID]).toMatchObject({
      inputPorts: [],
    })
    const input = fixture.graphRef.current.nodes.find(
      (node) => (node.data as unknown as SubmodelPortData).portDirection === "input",
    )!
    expect((input.data as unknown as SubmodelPortData).ports).toEqual([])
    expect(fixture.graphRef.current.edges.some((edge) => edge.source === input.id)).toBe(false)
  })

  it("restores parent input bindings when undo and redo restore drilled snapshots", () => {
    const fixture = makeFixture({ bindInput: true })
    const originalSnapshot = cloneGraphSnapshot({
      nodes: fixture.view.nodes,
      edges: fixture.view.edges as PipelineEdge[],
      preamble: "",
      submodels: fixture.submodels,
    })
    const { result, rerender } = renderHook(
      (params: ReturnType<typeof hookParams>) => useSubmodelBoundaryEditing(params),
      { initialProps: hookParams(fixture) },
    )

    act(() => {
      expect(result.current.deleteBoundaryInputPort("incoming")).toBe(true)
    })
    const removedSnapshot = cloneGraphSnapshot({
      nodes: fixture.graphRef.current.nodes,
      edges: fixture.graphRef.current.edges as PipelineEdge[],
      preamble: "",
      submodels: fixture.submodelsRef.current,
    })
    expect(fixture.parentGraphRef.current?.edges).toEqual([])

    rerender({
      ...hookParams(fixture),
      nodes: removedSnapshot.nodes,
      edges: removedSnapshot.edges,
      submodels: removedSnapshot.submodels,
    })
    rerender({
      ...hookParams(fixture),
      nodes: originalSnapshot.nodes,
      edges: originalSnapshot.edges,
      submodels: originalSnapshot.submodels,
    })
    expect(fixture.parentGraphRef.current?.edges).toEqual(fixture.parentEdges)

    rerender({
      ...hookParams(fixture),
      nodes: removedSnapshot.nodes,
      edges: removedSnapshot.edges,
      submodels: removedSnapshot.submodels,
    })
    expect(fixture.parentGraphRef.current?.edges).toEqual([])
  })

  it("restores a parent binding to its own position, not the end of the list", () => {
    // Parent edge order is persisted: the dirty fingerprint compares it and
    // codegen emits `connect` calls in it. Appending a restored binding would
    // report unsaved changes after undoing back to the saved graph.
    const fixture = makeFixture({ bindInput: true, outputPorts: ["child_b"] })
    const before = fixture.parentEdges.map((edge) => edge.id)
    expect(before[0]).toBe("incoming")
    const originalSnapshot = cloneGraphSnapshot({
      nodes: fixture.view.nodes,
      edges: fixture.view.edges as PipelineEdge[],
      preamble: "",
      submodels: fixture.submodels,
    })
    const { result, rerender } = renderHook(
      (params: ReturnType<typeof hookParams>) => useSubmodelBoundaryEditing(params),
      { initialProps: hookParams(fixture) },
    )

    act(() => {
      expect(result.current.deleteBoundaryInputPort("incoming")).toBe(true)
    })
    const removedSnapshot = cloneGraphSnapshot({
      nodes: fixture.graphRef.current.nodes,
      edges: fixture.graphRef.current.edges as PipelineEdge[],
      preamble: "",
      submodels: fixture.submodelsRef.current,
    })
    expect(fixture.parentGraphRef.current?.edges.map((edge) => edge.id))
      .toEqual(before.filter((id) => id !== "incoming"))

    rerender({
      ...hookParams(fixture),
      nodes: removedSnapshot.nodes,
      edges: removedSnapshot.edges,
      submodels: removedSnapshot.submodels,
    })
    rerender({
      ...hookParams(fixture),
      nodes: originalSnapshot.nodes,
      edges: originalSnapshot.edges,
      submodels: originalSnapshot.submodels,
    })

    expect(fixture.parentGraphRef.current?.edges.map((edge) => edge.id)).toEqual(before)
  })

  it("resolves changed parent occurrence handles before committing a new public output", async () => {
    const fixture = makeFixture()
    const output = fixture.view.nodes.find(
      (node) => (node.data as unknown as SubmodelPortData).portDirection === "output",
    )!
    let finishResolution!: (value: { nodes: Node[]; edges: Edge[] }) => void
    const identityResolution = new Promise<{ nodes: Node[]; edges: Edge[] }>((resolve) => {
      finishResolution = resolve
    })
    const resolveGraphIdentities = vi.fn((_request: GraphIdentityRequest) => identityResolution)
    const { result } = renderHook(() => useSubmodelBoundaryEditing({
      ...hookParams(fixture),
      resolveGraphIdentities,
    }))

    act(() => {
      expect(result.current.commitBoundaryConnection({
        source: "child_a",
        sourceHandle: null,
        target: output.id,
        targetHandle: null,
      })).toBe(true)
    })
    expect(fixture.setNodesAndEdgesAndSubmodels).not.toHaveBeenCalled()
    await vi.waitFor(() => expect(resolveGraphIdentities).toHaveBeenCalledOnce())
    const candidate = resolveGraphIdentities.mock.calls[0]![0]
    const resolvedParentNodes = candidate.nodes.map((node) => node.id === PLACEHOLDER_ID
      ? {
          ...node,
          data: {
            ...node.data,
            _sourceHandleInputNames: { out__child_a_input: "Public_child_a_input" },
          },
        }
      : node)
    await act(async () => {
      finishResolution({ nodes: resolvedParentNodes, edges: [...candidate.edges] })
      await identityResolution
    })

    await vi.waitFor(() => expect(fixture.setNodesAndEdgesAndSubmodels).toHaveBeenCalledOnce())
    const committedParent = fixture.parentGraphRef.current!.nodes.find(
      (node) => node.id === PLACEHOLDER_ID,
    )!
    expect(committedParent.data._sourceHandleInputNames).toEqual({
      out__child_a_input: "Public_child_a_input",
    })
    expect(fixture.submodelsRef.current[DEFINITION_ID]).toMatchObject({
      outputPorts: [{ name: "child_a_input" }],
    })
  })

  it("does not publish a parent identity response after the graph changes", async () => {
    const fixture = makeFixture()
    const output = fixture.view.nodes.find(
      (node) => (node.data as unknown as SubmodelPortData).portDirection === "output",
    )!
    let finishResolution!: (value: { nodes: Node[]; edges: Edge[] }) => void
    const identityResolution = new Promise<{ nodes: Node[]; edges: Edge[] }>((resolve) => {
      finishResolution = resolve
    })
    const resolveGraphIdentities = vi.fn((_request: GraphIdentityRequest) => identityResolution)
    const { result } = renderHook(() => useSubmodelBoundaryEditing({
      ...hookParams(fixture),
      resolveGraphIdentities,
    }))

    act(() => {
      result.current.commitBoundaryConnection({
        source: "child_a",
        sourceHandle: null,
        target: output.id,
        targetHandle: null,
      })
    })
    await vi.waitFor(() => expect(resolveGraphIdentities).toHaveBeenCalledOnce())
    // A structural change — a node leaving the view — is what voids a pending
    // gesture; a re-rendered but structurally identical graph does not.
    const externallyChanged = {
      nodes: fixture.graphRef.current.nodes.filter((node) => node.id !== "child_b"),
      edges: [...fixture.graphRef.current.edges],
    }
    fixture.graphRef.current = externallyChanged
    const candidate = resolveGraphIdentities.mock.calls[0]![0]
    await act(async () => {
      finishResolution({ nodes: [...candidate.nodes], edges: [...candidate.edges] })
      await identityResolution
    })

    await vi.waitFor(() => expect(useToastStore.getState().toasts.at(-1)?.text)
      .toContain("workspace changed"))
    expect(fixture.setNodesAndEdgesAndSubmodels).not.toHaveBeenCalled()
    expect(fixture.graphRef.current).toBe(externallyChanged)
  })

  it("resolves stale parent output handles after an unbound output is removed and reused", async () => {
    const fixture = makeFixture({ bindOutputConsumers: false })
    const output = fixture.view.nodes.find(
      (node) => (node.data as unknown as SubmodelPortData).portDirection === "output",
    )!
    const resolveGraphIdentities = vi.fn(async (request: GraphIdentityRequest) => {
      const definition = request.submodels[DEFINITION_ID] as {
        outputPorts: Array<{ name: string }>
      }
      const mapping = Object.fromEntries(definition.outputPorts.map((port) => [
        `out__${port.name}`, `identity_${port.name.replaceAll(" ", "_")}`,
      ]))
      return {
        nodes: request.nodes.map((node) => node.id === PLACEHOLDER_ID ? {
          ...node,
          data: { ...node.data, _sourceHandleInputNames: mapping },
        } : node),
        edges: [...request.edges],
      }
    })
    const { result, rerender } = renderHook(
      (params: ReturnType<typeof hookParams>) => useSubmodelBoundaryEditing(params),
      { initialProps: { ...hookParams(fixture), resolveGraphIdentities } },
    )

    act(() => {
      result.current.commitBoundaryConnection({
        source: "child_a", sourceHandle: null, target: output.id, targetHandle: null,
      })
    })
    await vi.waitFor(() => expect(resolveGraphIdentities).toHaveBeenCalledTimes(1))
    await vi.waitFor(() => expect(fixture.setNodesAndEdgesAndSubmodels).toHaveBeenCalledTimes(1))
    rerender({
      ...hookParams(fixture),
      nodes: fixture.graphRef.current.nodes,
      edges: fixture.graphRef.current.edges as PipelineEdge[],
      submodels: fixture.submodelsRef.current,
      resolveGraphIdentities,
    })

    const firstExport = fixture.graphRef.current.edges.find((edge) => edge.source === "child_a")!
    act(() => expect(result.current.deleteBoundaryEdge(firstExport.id)).toBe(true))
    await vi.waitFor(() => expect(resolveGraphIdentities).toHaveBeenCalledTimes(2))
    await vi.waitFor(() => expect(fixture.setNodesAndEdgesAndSubmodels).toHaveBeenCalledTimes(2))
    expect(fixture.parentGraphRef.current!.nodes.find((node) => node.id === PLACEHOLDER_ID)
      ?.data._sourceHandleInputNames).toEqual({})
    rerender({
      ...hookParams(fixture),
      nodes: fixture.graphRef.current.nodes,
      edges: fixture.graphRef.current.edges as PipelineEdge[],
      submodels: fixture.submodelsRef.current,
      resolveGraphIdentities,
    })

    act(() => {
      result.current.commitBoundaryConnection({
        source: "child_b", sourceHandle: null, target: output.id, targetHandle: null,
      })
    })
    await vi.waitFor(() => expect(resolveGraphIdentities).toHaveBeenCalledTimes(3))
    await vi.waitFor(() => expect(fixture.setNodesAndEdgesAndSubmodels).toHaveBeenCalledTimes(3))
    expect(fixture.parentGraphRef.current!.nodes.find((node) => node.id === PLACEHOLDER_ID)
      ?.data._sourceHandleInputNames).toEqual({ out__child_b_input: "identity_child_b_input" })
  })

  it("layers overlapping output gestures onto the pending boundary candidate", async () => {
    const fixture = makeFixture()
    const output = fixture.view.nodes.find(
      (node) => (node.data as unknown as SubmodelPortData).portDirection === "output",
    )!
    const resolutions: Array<{
      resolve: (value: { nodes: Node[]; edges: Edge[] }) => void
      reject: (reason?: unknown) => void
    }> = []
    const resolveGraphIdentities = vi.fn((_: GraphIdentityRequest) => new Promise<{ nodes: Node[]; edges: Edge[] }>(
      (resolve, reject) => resolutions.push({ resolve, reject }),
    ))
    const { result } = renderHook(() => useSubmodelBoundaryEditing({
      ...hookParams(fixture), resolveGraphIdentities,
    }))

    act(() => {
      result.current.commitBoundaryConnection({ source: "child_a", sourceHandle: null, target: output.id, targetHandle: null })
      result.current.commitBoundaryConnection({ source: "child_b", sourceHandle: null, target: output.id, targetHandle: null })
    })
    await vi.waitFor(() => expect(resolveGraphIdentities).toHaveBeenCalledTimes(2))
    const latest = resolveGraphIdentities.mock.calls[1]![0]
    expect((latest.submodels[DEFINITION_ID] as { outputPorts: Array<{ name: string }> })
      .outputPorts.map((port) => port.name)).toEqual(["child_a_input", "child_b_input"])

    const resolvedLatestNodes = latest.nodes.map((node) => node.id === PLACEHOLDER_ID ? {
      ...node,
      data: {
        ...node.data,
        _sourceHandleInputNames: {
          out__child_a_input: "identity_child_a",
          out__child_b_input: "identity_child_b",
        },
      },
    } : node)
    await act(async () => resolutions[1]!.resolve({ nodes: resolvedLatestNodes, edges: [...latest.edges] }))
    await vi.waitFor(() => expect(fixture.setNodesAndEdgesAndSubmodels).toHaveBeenCalledOnce())
    await act(async () => resolutions[0]!.reject(new Error("superseded request failed")))

    expect(fixture.setNodesAndEdgesAndSubmodels).toHaveBeenCalledOnce()
    expect(fixture.parentGraphRef.current!.nodes.find((node) => node.id === PLACEHOLDER_ID)
      ?.data._sourceHandleInputNames).toEqual({
        out__child_a_input: "identity_child_a", out__child_b_input: "identity_child_b",
      })
    expect(useToastStore.getState().toasts).toEqual([])
  })

  it("blocks deletion of a public output used by the active instance", () => {
    const fixture = makeFixture({ outputPorts: ["child_a"] })
    const exportEdge = fixture.view.edges.find(
      (edge) => edge.source === "child_a" && edge.target.includes("boundary"),
    )
    const { result } = renderHook(() => useSubmodelBoundaryEditing(hookParams(fixture)))

    act(() => {
      expect(result.current.deleteBoundaryEdge(exportEdge!.id)).toBe(true)
    })

    expect(fixture.setNodesAndEdgesAndSubmodels).not.toHaveBeenCalled()
    expect(fixture.parentGraphRef.current?.edges).toEqual(fixture.parentEdges)
    const definition = fixture.submodelsRef.current[DEFINITION_ID] as { outputPorts: unknown[] }
    expect(definition.outputPorts).toHaveLength(1)
  })

  it("blocks a mixed boundary and internal edge deletion atomically", () => {
    const fixture = makeFixture({
      outputPorts: ["child_a"],
      includeInternalEdge: true,
    })
    const exportEdge = fixture.view.edges.find(
      (edge) => edge.source === "child_a" && edge.id !== "internal",
    )
    const { result } = renderHook(() => useSubmodelBoundaryEditing(hookParams(fixture)))

    act(() => {
      expect(result.current.onBoundaryEdgesChange([
        { type: "remove", id: exportEdge!.id },
        { type: "remove", id: "internal" },
      ])).toBe(true)
    })

    expect(fixture.setNodesAndEdgesAndSubmodels).not.toHaveBeenCalled()
    expect(fixture.graphRef.current.edges.map((edge) => edge.id)).toContain("internal")
    expect(fixture.parentGraphRef.current?.edges).toEqual(fixture.parentEdges)
  })

  it("blocks an undo-like rerender that would remove a bound public output", () => {
    const fixture = makeFixture({ outputPorts: ["child_a"] })
    const { rerender } = renderHook(
      (params: ReturnType<typeof hookParams>) => useSubmodelBoundaryEditing(params),
      { initialProps: hookParams(fixture) },
    )

    const restored = makeFixture()
    rerender({
      ...hookParams(fixture),
      nodes: restored.view.nodes,
      edges: restored.view.edges as PipelineEdge[],
      submodels: restored.submodels,
    })

    expect(fixture.parentGraphRef.current?.edges).toEqual(fixture.parentEdges)
    const metadata = fixture.submodelsRef.current[DEFINITION_ID] as {
      outputPorts: Array<{ name: string }>
    }
    expect(metadata.outputPorts.map((port) => port.name)).toEqual(["child_a"])
  })

  it("leaves parent refs untouched when history restores a non-drilled snapshot", () => {
    const fixture = makeFixture({ outputPorts: ["child_a"] })
    const { rerender } = renderHook(
      (params: ReturnType<typeof hookParams>) => useSubmodelBoundaryEditing(params),
      { initialProps: hookParams(fixture) },
    )

    // Undo past the drill-in boundary: the visible graph becomes the parent
    // canvas again while the drilled view is still active.
    rerender({
      ...hookParams(fixture),
      nodes: fixture.parentNodes,
      edges: fixture.parentEdges as PipelineEdge[],
    })

    const metadata = fixture.submodelsRef.current[DEFINITION_ID] as {
      graph: { nodes: Node[] }
      outputPorts: Array<{ name: string }>
    }
    expect(metadata.graph.nodes.map((node) => node.id)).toEqual(["child_a", "child_b"])
    expect(metadata.outputPorts.map((port) => port.name)).toEqual(["child_a"])
    expect((fixture.parentGraphRef.current?.edges ?? []).map((edge) => edge.id)).toEqual([
      "consumer-a-child_a",
      "consumer-b-child_a",
    ])
  })

  it("does not own ordinary edges or any gesture outside a drilled submodel", () => {
    const fixture = makeFixture()
    const { result } = renderHook(() => useSubmodelBoundaryEditing({
      ...hookParams(fixture),
      activeSubmodelName: null,
    }))

    expect(result.current.commitBoundaryConnection({
      source: "child_a",
      sourceHandle: null,
      target: "child_b",
      targetHandle: null,
    })).toBe(false)
    expect(result.current.deleteBoundaryEdge("missing")).toBe(false)
    expect(result.current.onBoundaryEdgesChange([
      { type: "remove", id: "missing" },
    ])).toBe(false)
    expect(fixture.setNodesAndEdgesAndSubmodels).not.toHaveBeenCalled()
  })

  it("blocks deletion of a bound child without committing", () => {
    const fixture = makeFixture({ outputPorts: ["child_a"] })
    const { result } = renderHook(() => useSubmodelBoundaryEditing(hookParams(fixture)))
    expect(result.current.commitSharedNodeDeletion(new Set(["child_a"]))).toBe("blocked")
    expect(fixture.setNodesAndEdgesAndSubmodels).not.toHaveBeenCalled()
  })

  it("commits deletion of an unbound child exactly once", () => {
    const fixture = makeFixture()
    const { result } = renderHook(() => useSubmodelBoundaryEditing(hookParams(fixture)))
    expect(result.current.commitSharedNodeDeletion(new Set(["child_a"]))).toBe("committed")
    expect(fixture.setNodesAndEdgesAndSubmodels).toHaveBeenCalledOnce()
  })

  it("applies a mixed React Flow deletion batch in the same reconciled commit", () => {
    const fixture = makeFixture()
    const { result } = renderHook(() => useSubmodelBoundaryEditing(hookParams(fixture)))
    expect(result.current.commitSharedNodeDeletion(
      new Set(["child_a"]),
      new Set(),
      [
        { type: "remove", id: "child_a" },
        {
          type: "position",
          id: "child_b",
          position: { x: 42, y: 24 },
          dragging: false,
        },
      ],
    )).toBe("committed")

    expect(fixture.setNodesAndEdgesAndSubmodels).toHaveBeenCalledOnce()
    const committedNodes = fixture.setNodesAndEdgesAndSubmodels.mock.calls[0][0] as Node[]
    expect(committedNodes.some((node) => node.id === "child_a")).toBe(false)
    expect(committedNodes.find((node) => node.id === "child_b")?.position).toEqual({
      x: 42,
      y: 24,
    })
  })
  describe("asynchronous shared-node deletion", () => {
    function makeUnresolvedFixture() {
      const fixture = makeFixture({ outputPorts: ["child_b"] })
      const placeholder = fixture.parentNodes.find((node) => node.id === PLACEHOLDER_ID)!
      placeholder.data._sourceHandleInputNames = {}
      return fixture
    }

    function deferredResolver() {
      const resolutions: Array<{
        resolve: (value: { nodes: Node[]; edges: Edge[] }) => void
        reject: (reason?: unknown) => void
      }> = []
      const resolveGraphIdentities = vi.fn((_: GraphIdentityRequest) =>
        new Promise<{ nodes: Node[]; edges: Edge[] }>((resolve, reject) => {
          resolutions.push({ resolve, reject })
        }))
      return { resolutions, resolveGraphIdentities }
    }

    it("reports a pending commit and settles true once identities land", async () => {
      const fixture = makeUnresolvedFixture()
      const { resolutions, resolveGraphIdentities } = deferredResolver()
      const { result } = renderHook(() => useSubmodelBoundaryEditing({
        ...hookParams(fixture), resolveGraphIdentities,
      }))
      const settled: boolean[] = []

      act(() => {
        expect(result.current.commitSharedNodeDeletion(
          new Set(["child_a"]), new Set(), undefined, (committed) => settled.push(committed),
        )).toBe("pending")
      })
      expect(settled).toEqual([])
      expect(fixture.setNodesAndEdgesAndSubmodels).not.toHaveBeenCalled()

      const request = resolveGraphIdentities.mock.calls[0]![0]
      await act(async () => resolutions[0]!.resolve({
        nodes: [...request.nodes], edges: [...request.edges],
      }))

      expect(settled).toEqual([true])
      expect(fixture.setNodesAndEdgesAndSubmodels).toHaveBeenCalledOnce()
    })

    it("settles false without committing when the identity request rejects", async () => {
      const fixture = makeUnresolvedFixture()
      const { resolutions, resolveGraphIdentities } = deferredResolver()
      const { result } = renderHook(() => useSubmodelBoundaryEditing({
        ...hookParams(fixture), resolveGraphIdentities,
      }))
      const settled: boolean[] = []

      act(() => {
        expect(result.current.commitSharedNodeDeletion(
          new Set(["child_a"]), new Set(), undefined, (committed) => settled.push(committed),
        )).toBe("pending")
      })
      await act(async () => resolutions[0]!.reject(new Error("identity service unavailable")))

      expect(settled).toEqual([false])
      expect(fixture.setNodesAndEdgesAndSubmodels).not.toHaveBeenCalled()
      expect(useToastStore.getState().toasts.map((toast) => toast.type)).toEqual(["error"])
    })

    it("settles true synchronously when no identity request is needed", () => {
      const fixture = makeFixture()
      const { result } = renderHook(() => useSubmodelBoundaryEditing(hookParams(fixture)))
      const settled: boolean[] = []

      expect(result.current.commitSharedNodeDeletion(
        new Set(["child_a"]), new Set(), undefined, (committed) => settled.push(committed),
      )).toBe("committed")
      expect(settled).toEqual([true])
      expect(fixture.setNodesAndEdgesAndSubmodels).toHaveBeenCalledOnce()
    })

    it("survives selection and position changes made while identities resolve", async () => {
      const fixture = makeUnresolvedFixture()
      const { resolutions, resolveGraphIdentities } = deferredResolver()
      const { result } = renderHook(() => useSubmodelBoundaryEditing({
        ...hookParams(fixture), resolveGraphIdentities,
      }))
      const settled: boolean[] = []

      act(() => {
        expect(result.current.commitSharedNodeDeletion(
          new Set(["child_a"]), new Set(), undefined, (committed) => settled.push(committed),
        )).toBe("pending")
      })

      fixture.graphRef.current = {
        nodes: fixture.graphRef.current.nodes.map((node) => node.id === "child_b"
          ? { ...node, selected: true, position: { x: 111, y: 222 } }
          : node),
        edges: fixture.graphRef.current.edges,
      }
      const request = resolveGraphIdentities.mock.calls[0]![0]
      await act(async () => resolutions[0]!.resolve({
        nodes: [...request.nodes], edges: [...request.edges],
      }))

      expect(settled).toEqual([true])
      expect(useToastStore.getState().toasts).toEqual([])
      const committedNodes = fixture.setNodesAndEdgesAndSubmodels.mock.calls[0][0] as Node[]
      const moved = committedNodes.find((node) => node.id === "child_b")!
      expect(moved.position).toEqual({ x: 111, y: 222 })
      expect(moved.selected).toBe(true)
    })

    it("voids the gesture when a node is removed while identities resolve", async () => {
      const fixture = makeUnresolvedFixture()
      const { resolutions, resolveGraphIdentities } = deferredResolver()
      const { result } = renderHook(() => useSubmodelBoundaryEditing({
        ...hookParams(fixture), resolveGraphIdentities,
      }))
      const settled: boolean[] = []

      act(() => {
        expect(result.current.commitSharedNodeDeletion(
          new Set(["child_a"]), new Set(), undefined, (committed) => settled.push(committed),
        )).toBe("pending")
      })

      fixture.graphRef.current = {
        nodes: fixture.graphRef.current.nodes.filter((node) => node.id !== "child_b"),
        edges: fixture.graphRef.current.edges,
      }
      const request = resolveGraphIdentities.mock.calls[0]![0]
      await act(async () => resolutions[0]!.resolve({
        nodes: [...request.nodes], edges: [...request.edges],
      }))

      expect(settled).toEqual([false])
      expect(fixture.setNodesAndEdgesAndSubmodels).not.toHaveBeenCalled()
      expect(useToastStore.getState().toasts.map((toast) => toast.text)).toEqual([
        "Shared submodel edit blocked: the workspace changed while parent identities were resolving",
      ])
    })
  })
})
