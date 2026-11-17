import { act, renderHook } from "@testing-library/react"
import { applyNodeChanges, type Edge, type Node, type NodeChange } from "@xyflow/react"
import { beforeEach, describe, expect, it, vi } from "vitest"
import useSubmodelBoundaryEditing from "../useSubmodelBoundaryEditing"
import useGraphStore, { canvasGraph } from "../../stores/useGraphStore"
import useToastStore from "../../stores/useToastStore"
import { makeEdge, makeNode } from "../../test-utils/factories"
import type { PipelineEdge, SubmodelDefinition, SubmodelPortData } from "../../types/node"
import { buildSubmodelViewGraph } from "../../utils/submodelViewGraph"
import { SUBMODEL_INPUT_HANDLE } from "../../utils/flowHandles"
import { attachEditorEdgeIdentities } from "../../utils/editorIdentities"

const SUBMODEL_NAME = "pricing"
const PLACEHOLDER_ID = "pricing"
const DEFINITION_ID = "definition_pricing"
const OCCURRENCE = { instanceId: PLACEHOLDER_ID, definitionId: DEFINITION_ID }

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
  const identify = (node: Node): Node => ({
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
      config: { definition_id: DEFINITION_ID, alias: "pricing" },
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
  })) as PipelineEdge[]
  const identifiedView = { nodes: viewNodes, edges: viewEdges }
  const setNodesAndEdgesAndSubmodels = vi.fn()
  return { childNodes, childEdges, parentNodes, parentEdges, submodels: submodels as Record<string, unknown>, view: identifiedView, setNodesAndEdgesAndSubmodels }
}
type Fixture = ReturnType<typeof makeFixture>

const store = () => useGraphStore.getState()
/** The open submodel's focus, read afresh: every commit, undo and redo replaces it. */
const focus = () => store().focus!
const storedDefinition = () => store().submodels[DEFINITION_ID] as SubmodelDefinition

/** The fixture's document as the main pipeline, with no submodel open. */
function loadParent(fixture: Fixture) {
  store().loadGraphSnapshot({
    nodes: fixture.parentNodes,
    edges: fixture.parentEdges,
    preamble: "",
    submodels: fixture.submodels,
  })
}

/**
 * Show the fixture's hand-identified view of the occurrence over the loaded
 * document. Set directly rather than through `enterSubmodelView`, which would
 * project a fresh view without the fixture's identities.
 */
function focusView(
  fixture: Fixture,
  root: { nodes: Node[]; edges: PipelineEdge[] } = { nodes: fixture.parentNodes, edges: fixture.parentEdges },
) {
  useGraphStore.setState({
    nodes: fixture.view.nodes,
    edges: fixture.view.edges,
    focus: { ...OCCURRENCE, readOnly: false, rootNodes: root.nodes, rootEdges: root.edges },
  })
}

function openInStore(fixture: Fixture) {
  loadParent(fixture)
  focusView(fixture)
}

function hookParams(fixture: Fixture) {
  return {
    setNodesAndEdgesAndSubmodels: fixture.setNodesAndEdgesAndSubmodels,
    reservedApiInputFrameLabels: new Set<string>(),
    resolveGraphIdentities: vi.fn(async ({ nodes, edges }: GraphIdentityRequest) => ({
      nodes: [...nodes],
      edges: [...edges] as PipelineEdge[],
    })),
  }
}
describe("useSubmodelBoundaryEditing", () => {
  beforeEach(() => {
    store().resetForTests()
    useToastStore.setState({ toasts: [] })
  })

  it.each([
    { removing: false, logicalName: "child_a" },
    { removing: true, logicalName: "child_a" },
    { removing: true, logicalName: "pricing" },
  ])("preserves code and selectors across output count changes ($removing, $logicalName)", async ({ removing, logicalName }) => {
    const fixture = makeFixture({
      outputPorts: removing ? ["child_a", "child_b"] : ["child_a"],
      bindOutputConsumers: false,
    })
    const beforeName = "child_a"
    const afterName = "child_a"
    const occurrence = fixture.parentNodes.find((node) => node.id === PLACEHOLDER_ID)!
    occurrence.data._sourceHandleInputNames = {
      out__child_a: beforeName,
      ...(removing ? { out__child_b: "child_b" } : {}),
    }
    const code = `df = ${logicalName}.with_columns(flag=pl.lit(1))`
    fixture.parentNodes.find((node) => node.id === "consumer_a")!.data.config = {
      code,
      ...(logicalName !== beforeName ? { input_mapping: { [logicalName]: beforeName } } : {}),
    }
    fixture.parentNodes.find((node) => node.id === "consumer_b")!.data.config = {
      data_input: beforeName, banding_source: beforeName, analysis_input: beforeName, ratebook_input: beforeName,
    }
    for (const target of ["consumer_a", "consumer_b"]) fixture.parentEdges.push({
      id: `consume-${target}`, source: PLACEHOLDER_ID, target, sourceHandle: "out__child_a", data: { _inputName: beforeName },
    })
    const copyBefore = removing ? "pricing_copy_child_a" : "pricing_copy"
    const copyAfter = removing ? "pricing_copy" : "pricing_copy_child_a"
    const copyCode = `df = ${copyBefore}`
    fixture.parentNodes.push(
      {
        ...occurrence,
        id: "pricing_copy",
        data: {
          ...occurrence.data,
          label: "pricing_copy",
          config: { definition_id: DEFINITION_ID, alias: "pricing_copy", instance_of: PLACEHOLDER_ID },
          _sourceHandleInputNames: {
            out__child_a: copyBefore,
            ...(removing ? { out__child_b: "pricing_copy_child_b" } : {}),
          },
        },
      },
      makeNode("copy_consumer", "polars", {
        data: { nodeType: "polars", label: "copy_consumer", config: { code: copyCode } },
      }),
      makeNode("response", "output", {
        data: { nodeType: "output", label: "response", config: {
          output_mapping: [{ source_port: copyBefore, source_column: "x", output_path: "$.x" }],
        } },
      }),
    )
    fixture.parentEdges.push({
      id: "copy-consume", source: "pricing_copy", target: "copy_consumer",
      sourceHandle: "out__child_a", data: { _inputName: copyBefore },
    }, {
      id: "copy-response", source: "pricing_copy", target: "response",
      sourceHandle: "out__child_a", data: { _inputName: copyBefore },
    }, {
      id: "copy-to-owner", source: "pricing_copy", target: PLACEHOLDER_ID,
      sourceHandle: "out__child_a", targetHandle: "in__incoming", data: { _inputName: copyBefore },
    })
    const projected = buildSubmodelViewGraph({
      submodelName: SUBMODEL_NAME, instanceId: PLACEHOLDER_ID,
      definition: fixture.submodels[DEFINITION_ID] as SubmodelDefinition,
      childNodes: fixture.childNodes, childEdges: fixture.childEdges,
      parentNodes: fixture.parentNodes, parentEdges: fixture.parentEdges,
    })
    fixture.view.nodes = fixture.view.nodes.map((node) => ({
      ...node, data: { ...node.data, ...projected.nodes.find((candidate) => candidate.id === node.id)!.data },
    }))
    const resolveGraphIdentities = vi.fn(async ({ nodes, edges, submodels }: GraphIdentityRequest) => {
      const definition = submodels[DEFINITION_ID] as SubmodelDefinition
      const resolved = nodes.map((node) => node.data.nodeType === "submodel" ? {
        ...node,
        data: {
          ...node.data,
          _sourceHandleInputNames: Object.fromEntries(definition.outputPorts.map((port) => [
            `out__${port.name}`, node.id === PLACEHOLDER_ID ? port.name : definition.outputPorts.length > 1 ? `${node.id}_${port.name}` : node.id,
          ])),
        },
      } : node)
      return { nodes: resolved, edges: attachEditorEdgeIdentities(edges, resolved) }
    })
    const params = { ...hookParams(fixture), resolveGraphIdentities }
    openInStore(fixture)
    const { result } = renderHook(() => useSubmodelBoundaryEditing(params))
    const output = fixture.view.nodes.find((node) => node.type === "submodelPort" && node.data.portDirection === "output")!
    await act(async () => {
      if (removing) {
        const removedEdge = fixture.view.edges.find((edge) => edge.source === "child_b" && edge.target === output.id)!
        result.current.deleteBoundaryEdge(removedEdge.id)
      } else {
        result.current.commitBoundaryConnection({ source: "child_b", sourceHandle: null, target: output.id, targetHandle: null })
      }
    })
    expect(store().undoStack).toHaveLength(1)
    const consumerConfig = (id: string) => focus().rootNodes.find((node) => node.id === id)!.data.config
    const copyToOwnerInput = () => focus().rootEdges.find((edge) => edge.id === "copy-to-owner")?.data?._inputName
    const copySelectors = () => focus().rootNodes.find((node) => node.id === "pricing_copy")!.data._sourceHandleInputNames
    const outputCount = () => storedDefinition().outputPorts.length
    const outputsBefore = removing ? 2 : 1
    const outputsAfter = removing ? 1 : 2
    expect(consumerConfig("consumer_a")).toEqual({
      code, ...(logicalName !== afterName ? { input_mapping: { [logicalName]: afterName } } : {}),
    })
    expect(consumerConfig("consumer_b")).toEqual({
      data_input: afterName, banding_source: afterName, analysis_input: afterName, ratebook_input: afterName,
    })
    expect(consumerConfig("copy_consumer")).toEqual({
      code: copyCode, input_mapping: { [copyBefore]: copyAfter },
    })
    expect(focus().rootEdges.every((edge) => (
      edge.data?._inputName === (edge.source === PLACEHOLDER_ID ? afterName : copyAfter)
    ))).toBe(true)
    expect(copyToOwnerInput()).toBe(copyAfter)
    expect(outputCount()).toBe(outputsAfter)

    act(() => store().undo())
    expect(store().focus).toMatchObject(OCCURRENCE)
    expect(outputCount()).toBe(outputsBefore)
    expect(copyToOwnerInput()).toBe(copyBefore)
    expect(consumerConfig("copy_consumer")).toEqual({ code: copyCode })
    expect(consumerConfig("response")).toEqual({ output_mapping: [{ source_port: copyBefore, source_column: "x", output_path: "$.x" }] })
    expect(copySelectors()).toMatchObject({ out__child_a: copyBefore })

    act(() => store().redo())
    expect(store().focus).toMatchObject(OCCURRENCE)
    expect(outputCount()).toBe(outputsAfter)
    expect(copyToOwnerInput()).toBe(copyAfter)
    expect(consumerConfig("copy_consumer")).toEqual({ code: copyCode, input_mapping: { [copyBefore]: copyAfter } })
    expect(consumerConfig("response")).toEqual({ output_mapping: [{ source_port: copyAfter, source_column: "x", output_path: "$.x" }] })
    expect(copySelectors()).toMatchObject({ out__child_a: copyAfter })
    expect(store().undoStack).toHaveLength(1)
    expect(fixture.setNodesAndEdgesAndSubmodels).not.toHaveBeenCalled()
  })

  it("atomically commits a parent-created public input and canonical edge", () => {
    const fixture = makeFixture({ includeInputPort: false })
    loadParent(fixture)
    const { result } = renderHook(() => useSubmodelBoundaryEditing(hookParams(fixture)))
    act(() => {
      expect(result.current.commitBoundaryConnection({
        source: "external",
        sourceHandle: null,
        target: PLACEHOLDER_ID,
        targetHandle: SUBMODEL_INPUT_HANDLE,
      })).toBe(true)
    })
    expect(fixture.setNodesAndEdgesAndSubmodels).toHaveBeenCalledOnce()
    const [, committedEdges, committedSubmodels] = fixture.setNodesAndEdgesAndSubmodels.mock.calls[0]
    expect(committedSubmodels[DEFINITION_ID]).toMatchObject({
      inputPorts: [{
        name: "external_input",
        targets: [],
      }],
    })
    expect(committedEdges).toEqual([expect.objectContaining({
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

    openInStore(fixture)
    const { result } = renderHook(() => useSubmodelBoundaryEditing(hookParams(fixture)))
    act(() => {
      expect(result.current.commitBoundaryConnection({
        source: input!.id,
        sourceHandle: inputData.ports[0].id,
        target: "child_b",
        targetHandle: null,
      })).toBe(true)
    })

    expect(store().undoStack).toHaveLength(1)
    expect(fixture.setNodesAndEdgesAndSubmodels).not.toHaveBeenCalled()
    expect(focus().rootEdges).toEqual([
      expect.objectContaining({
        id: "incoming",
        targetHandle: "in__incoming",
      }),
    ])
    expect(storedDefinition().inputPorts[0].targets.map((target) => target.nodeId)).toEqual([
      "child_a",
      "child_b",
    ])
  })

  it("explicitly removes a bound public input in one atomic commit", () => {
    const fixture = makeFixture({ bindInput: true })
    openInStore(fixture)
    const { result } = renderHook(() => useSubmodelBoundaryEditing(hookParams(fixture)))

    act(() => {
      expect(result.current.deleteBoundaryInputPort("incoming")).toBe(true)
    })

    expect(store().undoStack).toHaveLength(1)
    expect(fixture.setNodesAndEdgesAndSubmodels).not.toHaveBeenCalled()
    expect(focus().rootEdges).toEqual([])
    expect(storedDefinition()).toMatchObject({
      inputPorts: [],
    })
    const input = store().nodes.find(
      (node) => (node.data as unknown as SubmodelPortData).portDirection === "input",
    )!
    expect((input.data as unknown as SubmodelPortData).ports).toEqual([])
    expect(store().edges.some((edge) => edge.source === input.id)).toBe(false)
  })

  it("restores parent input bindings on undo and redo of an input's removal, keeping the submodel open", () => {
    const fixture = makeFixture({ bindInput: true })
    openInStore(fixture)
    const { result } = renderHook(() => useSubmodelBoundaryEditing(hookParams(fixture)))

    act(() => {
      expect(result.current.deleteBoundaryInputPort("incoming")).toBe(true)
    })
    expect(focus().rootEdges).toEqual([])

    act(() => store().undo())
    expect(store().focus).toMatchObject(OCCURRENCE)
    expect(focus().rootEdges).toEqual(fixture.parentEdges)

    act(() => store().redo())
    expect(store().focus).toMatchObject(OCCURRENCE)
    expect(focus().rootEdges).toEqual([])
  })

  it("restores a parent binding to its own position, not the end of the list", () => {
    // Parent edge order is persisted: the dirty fingerprint compares it and
    // codegen emits `connect` calls in it. Appending a restored binding would
    // report unsaved changes after undoing back to the saved graph.
    const fixture = makeFixture({ bindInput: true, outputPorts: ["child_b"] })
    const before = fixture.parentEdges.map((edge) => edge.id)
    expect(before[0]).toBe("incoming")
    openInStore(fixture)
    const { result } = renderHook(() => useSubmodelBoundaryEditing(hookParams(fixture)))

    act(() => {
      expect(result.current.deleteBoundaryInputPort("incoming")).toBe(true)
    })
    expect(focus().rootEdges.map((edge) => edge.id))
      .toEqual(before.filter((id) => id !== "incoming"))

    act(() => store().undo())

    expect(store().focus).toMatchObject(OCCURRENCE)
    expect(focus().rootEdges.map((edge) => edge.id)).toEqual(before)
  })

  it("resolves changed parent occurrence handles before committing a new public output", async () => {
    const fixture = makeFixture()
    const output = fixture.view.nodes.find(
      (node) => (node.data as unknown as SubmodelPortData).portDirection === "output",
    )!
    let finishResolution!: (value: { nodes: Node[]; edges: PipelineEdge[] }) => void
    const identityResolution = new Promise<{ nodes: Node[]; edges: PipelineEdge[] }>((resolve) => {
      finishResolution = resolve
    })
    const resolveGraphIdentities = vi.fn((_request: GraphIdentityRequest) => identityResolution)
    openInStore(fixture)
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
    expect(store().undoStack).toHaveLength(0)
    expect(storedDefinition().outputPorts).toEqual([])
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
      finishResolution({ nodes: resolvedParentNodes, edges: [...candidate.edges] as PipelineEdge[] })
      await identityResolution
    })

    await vi.waitFor(() => expect(store().undoStack).toHaveLength(1))
    expect(fixture.setNodesAndEdgesAndSubmodels).not.toHaveBeenCalled()
    const committedParent = focus().rootNodes.find(
      (node) => node.id === PLACEHOLDER_ID,
    )!
    expect(committedParent.data._sourceHandleInputNames).toEqual({
      out__child_a_input: "Public_child_a_input",
    })
    expect(storedDefinition()).toMatchObject({
      outputPorts: [{ name: "child_a_input" }],
    })
  })

  it("does not publish a parent identity response after the graph changes", async () => {
    const fixture = makeFixture()
    const output = fixture.view.nodes.find(
      (node) => (node.data as unknown as SubmodelPortData).portDirection === "output",
    )!
    let finishResolution!: (value: { nodes: Node[]; edges: PipelineEdge[] }) => void
    const identityResolution = new Promise<{ nodes: Node[]; edges: PipelineEdge[] }>((resolve) => {
      finishResolution = resolve
    })
    const resolveGraphIdentities = vi.fn((_request: GraphIdentityRequest) => identityResolution)
    openInStore(fixture)
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
    // A semantic edit made meanwhile — a node leaving the view — voids the
    // pending gesture; a re-render or a move does not.
    act(() => store().setNodesRaw((nodes) => nodes.filter((node) => node.id !== "child_b")))
    const externallyChanged = store().nodes
    expect(externallyChanged.map((node) => node.id)).not.toContain("child_b")
    const canvasBefore = canvasGraph()
    const submodelsBefore = store().submodels
    const candidate = resolveGraphIdentities.mock.calls[0]![0]
    await act(async () => {
      finishResolution({ nodes: [...candidate.nodes], edges: [...candidate.edges] as PipelineEdge[] })
      await identityResolution
    })

    await vi.waitFor(() => expect(useToastStore.getState().toasts.at(-1)?.text)
      .toContain("workspace changed"))
    expect(store().undoStack).toHaveLength(0)
    expect(fixture.setNodesAndEdgesAndSubmodels).not.toHaveBeenCalled()
    expect(store().nodes).toBe(externallyChanged)
    expect(store().submodels).toBe(submodelsBefore)
    expect(focus().rootNodes).toBe(fixture.parentNodes)
    expect(focus().rootEdges).toBe(fixture.parentEdges)
    expect(canvasGraph()).toBe(canvasBefore)
  })

  it("keeps a node moved while identities resolve, and still commits the edit", async () => {
    const fixture = makeFixture()
    const output = fixture.view.nodes.find((node) => node.type === "submodelPort" && node.data.portDirection === "output")!
    let finishResolution!: (value: { nodes: Node[]; edges: PipelineEdge[] }) => void
    const identityResolution = new Promise<{ nodes: Node[]; edges: PipelineEdge[] }>((resolve) => { finishResolution = resolve })
    const resolveGraphIdentities = vi.fn((_request: GraphIdentityRequest) => identityResolution)
    openInStore(fixture)
    const { result } = renderHook(() => useSubmodelBoundaryEditing({ ...hookParams(fixture), resolveGraphIdentities }))

    act(() => {
      result.current.commitBoundaryConnection({ source: "child_a", sourceHandle: null, target: output.id, targetHandle: null })
    })
    await vi.waitFor(() => expect(resolveGraphIdentities).toHaveBeenCalledOnce())
    const moveTo = (x: number, dragging: boolean) => store().setNodesRaw((nodes) =>
      nodes.map((node) => (node.id === "child_b" ? { ...node, position: { x, y: 640 }, dragging } : node)))
    act(() => {
      store().pushSnapshot()
      moveTo(500, true)
      moveTo(520, false)
    })
    expect(storedDefinition().graph.nodes.find((node) => node.id === "child_b")?.position).toEqual({ x: 520, y: 640 })

    const candidate = resolveGraphIdentities.mock.calls[0]![0]
    const resolvedParentNodes = candidate.nodes.map((node) => node.id === PLACEHOLDER_ID
      ? { ...node, data: { ...node.data, _sourceHandleInputNames: { out__child_a_input: "Public_child_a_input" } } }
      : node)
    await act(async () => {
      finishResolution({ nodes: resolvedParentNodes, edges: [...candidate.edges] as PipelineEdge[] })
      await identityResolution
    })

    await vi.waitFor(() => expect(storedDefinition().outputPorts.map((port) => port.name)).toEqual(["child_a_input"]))
    expect(storedDefinition().graph.nodes.find((node) => node.id === "child_b")?.position).toEqual({ x: 520, y: 640 })
    expect(store().nodes.find((node) => node.id === "child_b")?.position).toEqual({ x: 520, y: 640 })
    expect(store().undoStack).toHaveLength(2)
  })

  it("lands a drag still under way when identities resolve, as its own step after the edit", async () => {
    const fixture = makeFixture()
    const output = fixture.view.nodes.find((node) => node.type === "submodelPort" && node.data.portDirection === "output")!
    let finishResolution!: (value: { nodes: Node[]; edges: PipelineEdge[] }) => void
    const identityResolution = new Promise<{ nodes: Node[]; edges: PipelineEdge[] }>((resolve) => { finishResolution = resolve })
    const resolveGraphIdentities = vi.fn((_request: GraphIdentityRequest) => identityResolution)
    openInStore(fixture)
    const { result } = renderHook(() => useSubmodelBoundaryEditing({ ...hookParams(fixture), resolveGraphIdentities }))
    const startedAt = storedDefinition().graph.nodes.find((node) => node.id === "child_b")?.position

    act(() => {
      result.current.commitBoundaryConnection({ source: "child_a", sourceHandle: null, target: output.id, targetHandle: null })
    })
    await vi.waitFor(() => expect(resolveGraphIdentities).toHaveBeenCalledOnce())
    const moveTo = (x: number, dragging: boolean) => store().setNodesRaw((nodes) =>
      nodes.map((node) => (node.id === "child_b" ? { ...node, position: { x, y: 640 }, dragging } : node)))
    act(() => {
      store().pushSnapshot()
      moveTo(500, true)
    })

    const candidate = resolveGraphIdentities.mock.calls[0]![0]
    const resolvedParentNodes = candidate.nodes.map((node) => node.id === PLACEHOLDER_ID
      ? { ...node, data: { ...node.data, _sourceHandleInputNames: { out__child_a_input: "Public_child_a_input" } } }
      : node)
    await act(async () => {
      finishResolution({ nodes: resolvedParentNodes, edges: [...candidate.edges] as PipelineEdge[] })
      await identityResolution
    })
    await vi.waitFor(() => expect(storedDefinition().outputPorts.map((port) => port.name)).toEqual(["child_a_input"]))
    // The drag still under way stays where it is on the canvas, and not yet in the definition.
    expect(store().nodes.find((node) => node.id === "child_b")?.position).toEqual({ x: 500, y: 640 })
    expect(storedDefinition().graph.nodes.find((node) => node.id === "child_b")?.position).toEqual(startedAt)
    // Released where the last frame left it, after the edit landed.
    act(() => moveTo(500, false))

    expect(storedDefinition().graph.nodes.find((node) => node.id === "child_b")?.position).toEqual({ x: 500, y: 640 })
    expect(store().undoStack).toHaveLength(2)
    act(() => store().undo())
    expect(storedDefinition().graph.nodes.find((node) => node.id === "child_b")?.position).toEqual(startedAt)
    expect(storedDefinition().outputPorts.map((port) => port.name)).toEqual(["child_a_input"])
  })

  it.each([
    ["an Undo and a Redo back to the same document", () => {
      store().undo()
      store().redo()
    }],
    ["leaving the submodel and opening it again", (fixture: Fixture) => {
      store().exitSubmodelView()
      focusView(fixture)
    }],
  ] as const)("discards an edit waiting on identities after %s", async (_label, meanwhile) => {
    const fixture = makeFixture()
    const output = fixture.view.nodes.find((node) => node.type === "submodelPort" && node.data.portDirection === "output")!
    let finishResolution!: (value: { nodes: Node[]; edges: PipelineEdge[] }) => void
    const identityResolution = new Promise<{ nodes: Node[]; edges: PipelineEdge[] }>((resolve) => { finishResolution = resolve })
    const resolveGraphIdentities = vi.fn((_request: GraphIdentityRequest) => identityResolution)
    openInStore(fixture)
    // An earlier step for Undo and Redo to cross.
    act(() => store().setNodes((nodes) => nodes.map((node) => (
      node.id === "child_b" ? { ...node, data: { ...node.data, config: { code: "df = child_a" } } } : node))))
    const { result } = renderHook(() => useSubmodelBoundaryEditing({ ...hookParams(fixture), resolveGraphIdentities }))

    act(() => {
      result.current.commitBoundaryConnection({ source: "child_a", sourceHandle: null, target: output.id, targetHandle: null })
    })
    await vi.waitFor(() => expect(resolveGraphIdentities).toHaveBeenCalledOnce())
    const documentBefore = store().persistedFingerprint
    act(() => meanwhile(fixture))
    expect(store().persistedFingerprint).toBe(documentBefore)

    const candidate = resolveGraphIdentities.mock.calls[0]![0]
    await act(async () => {
      finishResolution({ nodes: [...candidate.nodes], edges: [...candidate.edges] as PipelineEdge[] })
      await identityResolution
    })

    await vi.waitFor(() => expect(useToastStore.getState().toasts.at(-1)?.text).toContain("workspace changed"))
    expect(storedDefinition().outputPorts).toEqual([])
    expect(store().persistedFingerprint).toBe(documentBefore)
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
        edges: [...request.edges] as PipelineEdge[],
      }
    })
    openInStore(fixture)
    const { result } = renderHook(() => useSubmodelBoundaryEditing({ ...hookParams(fixture), resolveGraphIdentities }))

    act(() => {
      result.current.commitBoundaryConnection({
        source: "child_a", sourceHandle: null, target: output.id, targetHandle: null,
      })
    })
    await vi.waitFor(() => expect(resolveGraphIdentities).toHaveBeenCalledTimes(1))
    await vi.waitFor(() => expect(store().undoStack).toHaveLength(1))

    const firstExport = store().edges.find((edge) => edge.source === "child_a")!
    act(() => expect(result.current.deleteBoundaryEdge(firstExport.id)).toBe(true))
    await vi.waitFor(() => expect(resolveGraphIdentities).toHaveBeenCalledTimes(2))
    await vi.waitFor(() => expect(store().undoStack).toHaveLength(2))
    expect(focus().rootNodes.find((node) => node.id === PLACEHOLDER_ID)
      ?.data._sourceHandleInputNames).toEqual({})

    act(() => {
      result.current.commitBoundaryConnection({
        source: "child_b", sourceHandle: null, target: output.id, targetHandle: null,
      })
    })
    await vi.waitFor(() => expect(resolveGraphIdentities).toHaveBeenCalledTimes(3))
    await vi.waitFor(() => expect(store().undoStack).toHaveLength(3))
    expect(focus().rootNodes.find((node) => node.id === PLACEHOLDER_ID)
      ?.data._sourceHandleInputNames).toEqual({ out__child_b_input: "identity_child_b_input" })
    expect(fixture.setNodesAndEdgesAndSubmodels).not.toHaveBeenCalled()
  })

  it("layers overlapping output gestures onto the pending boundary candidate", async () => {
    const fixture = makeFixture()
    const output = fixture.view.nodes.find(
      (node) => (node.data as unknown as SubmodelPortData).portDirection === "output",
    )!
    const resolutions: Array<{
      resolve: (value: { nodes: Node[]; edges: PipelineEdge[] }) => void
      reject: (reason?: unknown) => void
    }> = []
    const resolveGraphIdentities = vi.fn((_: GraphIdentityRequest) => new Promise<{ nodes: Node[]; edges: PipelineEdge[] }>(
      (resolve, reject) => resolutions.push({ resolve, reject }),
    ))
    openInStore(fixture)
    const { result } = renderHook(() => useSubmodelBoundaryEditing({
      ...hookParams(fixture), resolveGraphIdentities,
    }))

    act(() => {
      result.current.commitBoundaryConnection({ source: "child_a", sourceHandle: null, target: output.id, targetHandle: null })
      result.current.commitBoundaryConnection({ source: "child_b", sourceHandle: null, target: output.id, targetHandle: null })
    })
    await vi.waitFor(() => expect(resolveGraphIdentities).toHaveBeenCalledTimes(2))
    expect(store().undoStack).toHaveLength(0)
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
    await act(async () => resolutions[1]!.resolve({ nodes: resolvedLatestNodes, edges: [...latest.edges] as PipelineEdge[] }))
    await vi.waitFor(() => expect(store().undoStack).toHaveLength(1))
    await act(async () => resolutions[0]!.reject(new Error("superseded request failed")))

    expect(store().undoStack).toHaveLength(1)
    expect(fixture.setNodesAndEdgesAndSubmodels).not.toHaveBeenCalled()
    expect(focus().rootNodes.find((node) => node.id === PLACEHOLDER_ID)
      ?.data._sourceHandleInputNames).toEqual({
        out__child_a_input: "identity_child_a", out__child_b_input: "identity_child_b",
      })
    expect(storedDefinition().outputPorts.map((port) => port.name)).toEqual(["child_a_input", "child_b_input"])
    expect(useToastStore.getState().toasts).toEqual([])
  })

  it("blocks deletion of a public output used by the active instance", () => {
    const fixture = makeFixture({ outputPorts: ["child_a"] })
    const exportEdge = fixture.view.edges.find(
      (edge) => edge.source === "child_a" && edge.target.includes("boundary"),
    )
    openInStore(fixture)
    const { result } = renderHook(() => useSubmodelBoundaryEditing(hookParams(fixture)))

    act(() => {
      expect(result.current.deleteBoundaryEdge(exportEdge!.id)).toBe(true)
    })

    expect(store().undoStack).toHaveLength(0)
    expect(fixture.setNodesAndEdgesAndSubmodels).not.toHaveBeenCalled()
    expect(focus().rootEdges).toEqual(fixture.parentEdges)
    expect(store().edges.map((edge) => edge.id)).toContain(exportEdge!.id)
    expect(storedDefinition().outputPorts).toHaveLength(1)
  })

  it("blocks a mixed boundary and internal edge deletion atomically", () => {
    const fixture = makeFixture({
      outputPorts: ["child_a"],
      includeInternalEdge: true,
    })
    const exportEdge = fixture.view.edges.find(
      (edge) => edge.source === "child_a" && edge.id !== "internal",
    )
    openInStore(fixture)
    const { result } = renderHook(() => useSubmodelBoundaryEditing(hookParams(fixture)))

    act(() => {
      expect(result.current.onBoundaryEdgesChange([
        { type: "remove", id: exportEdge!.id },
        { type: "remove", id: "internal" },
      ])).toBe(true)
    })

    expect(store().undoStack).toHaveLength(0)
    expect(fixture.setNodesAndEdgesAndSubmodels).not.toHaveBeenCalled()
    expect(store().edges.map((edge) => edge.id)).toEqual(expect.arrayContaining(["internal", exportEdge!.id]))
    expect(storedDefinition().graph.edges.map((edge) => edge.id)).toContain("internal")
    expect(focus().rootEdges).toEqual(fixture.parentEdges)
  })

  it("keeps a deletion batch's other changes on the canvas, never in the definition", () => {
    const fixture = makeFixture({ includeInternalEdge: true })
    const route = fixture.view.edges.find((edge) => edge.target === "child_a" && edge.id !== "internal")!
    openInStore(fixture)
    const { result } = renderHook(() => useSubmodelBoundaryEditing(hookParams(fixture)))

    act(() => {
      expect(result.current.onBoundaryEdgesChange([
        { type: "remove", id: route.id },
        { type: "select", id: "internal", selected: true },
      ])).toBe(true)
    })

    expect(store().undoStack).toHaveLength(1)
    expect(storedDefinition().inputPorts).toEqual([])
    expect(store().edges.find((edge) => edge.id === "internal")?.selected).toBe(true)
    expect(storedDefinition().graph.edges.find((edge) => edge.id === "internal")).not.toHaveProperty("selected")
  })

  it("leaves a bound public output in the document when the canvas re-renders without it", () => {
    // Undo and Redo restore the whole document, so nothing re-reconciles the
    // parent from whatever the canvas shows on a re-render.
    const fixture = makeFixture({ outputPorts: ["child_a"] })
    openInStore(fixture)
    const { rerender } = renderHook(() => useSubmodelBoundaryEditing(hookParams(fixture)))

    const restored = makeFixture()
    useGraphStore.setState({ nodes: restored.view.nodes, edges: restored.view.edges })
    rerender()

    expect(store().undoStack).toHaveLength(0)
    expect(focus().rootEdges).toEqual(fixture.parentEdges)
    expect(storedDefinition().outputPorts.map((port) => port.name)).toEqual(["child_a"])
    expect(fixture.setNodesAndEdgesAndSubmodels).not.toHaveBeenCalled()
  })

  it("leaves the definition and parent untouched when undo returns to the main pipeline", () => {
    const fixture = makeFixture({ outputPorts: ["child_a"] })
    loadParent(fixture)
    // A step made in the main pipeline before the submodel was opened.
    store().setNodes((nodes) => nodes.map((node) => (
      node.id === "external" ? { ...node, data: { ...node.data, label: "Policies" } } : node
    )))
    focusView(fixture, { nodes: store().nodes, edges: store().edges })
    const { result } = renderHook(() => useSubmodelBoundaryEditing(hookParams(fixture)))

    act(() => store().undo())

    expect(store().focus).toBeNull()
    expect(store().nodes.find((node) => node.id === "external")?.data.label).toBe("Node external")
    expect(storedDefinition().graph.nodes.map((node) => node.id)).toEqual(["child_a", "child_b"])
    expect(storedDefinition().outputPorts.map((port) => port.name)).toEqual(["child_a"])
    expect(store().edges.map((edge) => edge.id)).toEqual([
      "consumer-a-child_a",
      "consumer-b-child_a",
    ])
    // The main pipeline is no drilled view: the boundary actions do not own it.
    expect(result.current.commitSharedNodeDeletion(new Set(["child_a"]))).toBe("not-applicable")
    expect(result.current.deleteBoundaryInputPort("incoming")).toBe(false)
    expect(store().undoStack).toHaveLength(0)
    expect(fixture.setNodesAndEdgesAndSubmodels).not.toHaveBeenCalled()
  })

  it("does not own ordinary edges or any gesture outside a drilled submodel", () => {
    const fixture = makeFixture()
    loadParent(fixture)
    const { result } = renderHook(() => useSubmodelBoundaryEditing(hookParams(fixture)))

    expect(result.current.commitBoundaryConnection({
      source: "external",
      sourceHandle: null,
      target: "consumer_a",
      targetHandle: null,
    })).toBe(false)
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
    expect(store().undoStack).toHaveLength(0)
    expect(useToastStore.getState().toasts).toEqual([])
  })

  it("leaves an ordinary edge between two children to the canvas while a submodel is open", () => {
    const fixture = makeFixture()
    openInStore(fixture)
    const { result } = renderHook(() => useSubmodelBoundaryEditing(hookParams(fixture)))

    expect(result.current.commitBoundaryConnection({
      source: "child_a",
      sourceHandle: null,
      target: "child_b",
      targetHandle: null,
    })).toBe(false)
    expect(store().undoStack).toHaveLength(0)
    expect(store().nodes).toBe(fixture.view.nodes)
    expect(store().edges).toBe(fixture.view.edges)
    expect(fixture.setNodesAndEdgesAndSubmodels).not.toHaveBeenCalled()
    expect(useToastStore.getState().toasts).toEqual([])
  })

  it("refuses an input routed straight to the Output card, saying why", () => {
    const fixture = makeFixture()
    openInStore(fixture)
    const { result } = renderHook(() => useSubmodelBoundaryEditing(hookParams(fixture)))
    const card = (direction: "input" | "output") => fixture.view.nodes.find((node) => (
      node.type === "submodelPort" && (node.data as SubmodelPortData).portDirection === direction))!
    expect(result.current.commitBoundaryConnection({
      source: card("input").id, sourceHandle: "incoming", target: card("output").id, targetHandle: null,
    })).toBe(true)
    expect(store().undoStack).toHaveLength(0)
    expect(useToastStore.getState().toasts.map((toast) => toast.text)).toEqual([
      "Submodel edit refused: An input can only be routed to a node inside the submodel",
    ])
  })

  it("blocks deletion of a bound child without committing", () => {
    const fixture = makeFixture({ outputPorts: ["child_a"] })
    openInStore(fixture)
    const submodelsBefore = store().submodels
    const { result } = renderHook(() => useSubmodelBoundaryEditing(hookParams(fixture)))
    expect(result.current.commitSharedNodeDeletion(new Set(["child_a"]))).toBe("blocked")
    expect(store().undoStack).toHaveLength(0)
    expect(store().submodels).toBe(submodelsBefore)
    expect(store().nodes.map((node) => node.id)).toContain("child_a")
    expect(fixture.setNodesAndEdgesAndSubmodels).not.toHaveBeenCalled()
  })

  it("lands a refused deletion batch's other changes on the canvas", () => {
    const fixture = makeFixture({ outputPorts: ["child_a"] })
    openInStore(fixture)
    const { result } = renderHook(() => useSubmodelBoundaryEditing(hookParams(fixture)))
    const applyToCanvas = vi.fn()
    expect(result.current.commitNodesChange([
      { type: "remove", id: "child_a" },
      { type: "select", id: "child_b", selected: true },
    ], applyToCanvas)).toBe(true)
    expect(applyToCanvas).toHaveBeenCalledWith([{ type: "select", id: "child_b", selected: true }])
    expect(store().undoStack).toHaveLength(0)
    expect(store().nodes.map((node) => node.id)).toContain("child_a")
  })

  it("leaves a batch with no submodel open to the canvas", () => {
    const fixture = makeFixture()
    loadParent(fixture)
    const { result } = renderHook(() => useSubmodelBoundaryEditing(hookParams(fixture)))
    const applyToCanvas = vi.fn()
    expect(result.current.commitNodesChange([{ type: "remove", id: "external" }], applyToCanvas)).toBe(false)
    expect(applyToCanvas).not.toHaveBeenCalled()
  })

  it("commits deletion of an unbound child exactly once", () => {
    const fixture = makeFixture()
    openInStore(fixture)
    const { result } = renderHook(() => useSubmodelBoundaryEditing(hookParams(fixture)))
    expect(result.current.commitSharedNodeDeletion(new Set(["child_a"]))).toBe("committed")
    expect(store().undoStack).toHaveLength(1)
    expect(storedDefinition().graph.nodes.map((node) => node.id)).toEqual(["child_b"])
    expect(fixture.setNodesAndEdgesAndSubmodels).not.toHaveBeenCalled()
  })

  it("applies a mixed React Flow deletion batch as one command, its moves in the document", () => {
    const fixture = makeFixture()
    openInStore(fixture)
    const { result } = renderHook(() => useSubmodelBoundaryEditing(hookParams(fixture)))
    const applyToCanvas = vi.fn()
    expect(result.current.commitNodesChange([
      { type: "remove", id: "child_a" },
      {
        type: "position",
        id: "child_b",
        position: { x: 42, y: 24 },
        dragging: false,
      },
    ], applyToCanvas)).toBe(true)
    // Committed at once, the batch is the step's: nothing is left for the canvas to apply.
    expect(applyToCanvas).not.toHaveBeenCalled()

    expect(store().undoStack).toHaveLength(1)
    expect(fixture.setNodesAndEdgesAndSubmodels).not.toHaveBeenCalled()
    const committedNodes = store().nodes
    expect(committedNodes.some((node) => node.id === "child_a")).toBe(false)
    expect(committedNodes.find((node) => node.id === "child_b")?.position).toEqual({
      x: 42,
      y: 24,
    })
    // The batch's move is part of the step: the definition holds it, so a save writes it.
    expect(storedDefinition().graph.nodes.find((node) => node.id === "child_b")?.position)
      .toEqual({ x: 42, y: 24 })
    expect(storedDefinition().graph.nodes.map((node) => node.id)).toEqual(["child_b"])
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
        resolve: (value: { nodes: Node[]; edges: PipelineEdge[] }) => void
        reject: (reason?: unknown) => void
      }> = []
      const resolveGraphIdentities = vi.fn((_: GraphIdentityRequest) =>
        new Promise<{ nodes: Node[]; edges: PipelineEdge[] }>((resolve, reject) => {
          resolutions.push({ resolve, reject })
        }))
      return { resolutions, resolveGraphIdentities }
    }

    it("reports a pending commit and settles true once identities land", async () => {
      const fixture = makeUnresolvedFixture()
      const { resolutions, resolveGraphIdentities } = deferredResolver()
      openInStore(fixture)
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
      expect(store().undoStack).toHaveLength(0)
      expect(store().nodes.map((node) => node.id)).toContain("child_a")

      const request = resolveGraphIdentities.mock.calls[0]![0]
      await act(async () => resolutions[0]!.resolve({
        nodes: [...request.nodes], edges: [...request.edges] as PipelineEdge[],
      }))

      expect(settled).toEqual([true])
      expect(store().undoStack).toHaveLength(1)
      expect(store().nodes.map((node) => node.id)).not.toContain("child_a")
      expect(fixture.setNodesAndEdgesAndSubmodels).not.toHaveBeenCalled()
    })

    it("settles false without committing when the identity request rejects", async () => {
      const fixture = makeUnresolvedFixture()
      const { resolutions, resolveGraphIdentities } = deferredResolver()
      openInStore(fixture)
      const submodelsBefore = store().submodels
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
      expect(store().undoStack).toHaveLength(0)
      expect(store().submodels).toBe(submodelsBefore)
      expect(store().nodes.map((node) => node.id)).toContain("child_a")
      expect(fixture.setNodesAndEdgesAndSubmodels).not.toHaveBeenCalled()
      expect(useToastStore.getState().toasts.map((toast) => toast.type)).toEqual(["error"])
    })

    it("settles true synchronously when no identity request is needed", () => {
      const fixture = makeFixture()
      openInStore(fixture)
      const { result } = renderHook(() => useSubmodelBoundaryEditing(hookParams(fixture)))
      const settled: boolean[] = []

      expect(result.current.commitSharedNodeDeletion(
        new Set(["child_a"]), new Set(), undefined, (committed) => settled.push(committed),
      )).toBe("committed")
      expect(settled).toEqual([true])
      expect(store().undoStack).toHaveLength(1)
      expect(fixture.setNodesAndEdgesAndSubmodels).not.toHaveBeenCalled()
    })

    it("survives selection and position changes made while identities resolve", async () => {
      const fixture = makeUnresolvedFixture()
      const { resolutions, resolveGraphIdentities } = deferredResolver()
      openInStore(fixture)
      const { result } = renderHook(() => useSubmodelBoundaryEditing({
        ...hookParams(fixture), resolveGraphIdentities,
      }))
      const settled: boolean[] = []

      act(() => {
        expect(result.current.commitSharedNodeDeletion(
          new Set(["child_a"]), new Set(), undefined, (committed) => settled.push(committed),
        )).toBe("pending")
      })

      act(() => store().setNodesRaw((nodes) => nodes.map((node) => node.id === "child_b"
        ? { ...node, selected: true, position: { x: 111, y: 222 } }
        : node)))
      const request = resolveGraphIdentities.mock.calls[0]![0]
      await act(async () => resolutions[0]!.resolve({
        nodes: [...request.nodes], edges: [...request.edges] as PipelineEdge[],
      }))

      expect(settled).toEqual([true])
      expect(useToastStore.getState().toasts).toEqual([])
      expect(store().undoStack).toHaveLength(1)
      const moved = store().nodes.find((node) => node.id === "child_b")!
      expect(moved.position).toEqual({ x: 111, y: 222 })
      expect(moved.selected).toBe(true)
      expect(store().nodes.map((node) => node.id)).not.toContain("child_a")
    })

    /** The canvas's own change handler, as App passes it: the changes applied to the canvas, which the store lifts. */
    const canvasHandler = () => vi.fn((changes: NodeChange[]) => {
      store().setNodesRaw((nodes) => applyNodeChanges(changes, nodes))
    })
    const childB = () => store().nodes.find((node) => node.id === "child_b")!
    const definitionPosition = () => storedDefinition().graph.nodes.find((node) => node.id === "child_b")!.position
    const startDrag = () => act(() => {
      store().pushSnapshot()
      store().setNodesRaw((nodes) => nodes.map((node) => (
        node.id === "child_b" ? { ...node, position: { x: 10, y: 20 }, dragging: true } : node)))
    })
    const releasingBatch: NodeChange[] = [
      { type: "remove", id: "child_a" },
      { type: "position", id: "child_b", position: { x: 42, y: 24 }, dragging: false },
      { type: "select", id: "child_b", selected: true },
    ]

    it("lands the batch's other changes on the canvas at once while the deletion waits for identities", async () => {
      const fixture = makeUnresolvedFixture()
      const { resolutions, resolveGraphIdentities } = deferredResolver()
      openInStore(fixture)
      const { result } = renderHook(() => useSubmodelBoundaryEditing({
        ...hookParams(fixture), resolveGraphIdentities,
      }))
      const applyToCanvas = canvasHandler()

      act(() => {
        expect(result.current.commitNodesChange([
          { type: "remove", id: "child_a" },
          { type: "position", id: "child_b", position: { x: 42, y: 24 }, dragging: false },
        ], applyToCanvas)).toBe(true)
      })
      // The deletion waits; the move is on the canvas and in the definition now.
      expect(applyToCanvas).toHaveBeenCalledWith([
        { type: "position", id: "child_b", position: { x: 42, y: 24 }, dragging: false },
      ])
      expect(store().nodes.map((node) => node.id)).toContain("child_a")
      expect(childB().position).toEqual({ x: 42, y: 24 })
      expect(definitionPosition()).toEqual({ x: 42, y: 24 })
      // The Output card is moved while the names resolve.
      const card = store().nodes.find((node) => (
        node.type === "submodelPort" && (node.data as SubmodelPortData).portDirection === "output"))!
      useGraphStore.setState({
        nodes: store().nodes.map((node) => node.id === card.id ? { ...node, position: { x: 900, y: 9 } } : node),
      })
      const request = resolveGraphIdentities.mock.calls[0]![0]
      await act(async () => resolutions[0]!.resolve({
        nodes: [...request.nodes], edges: [...request.edges] as PipelineEdge[],
      }))

      expect(store().undoStack).toHaveLength(1)
      expect(store().nodes.map((node) => node.id)).not.toContain("child_a")
      expect(childB().position).toEqual({ x: 42, y: 24 })
      expect(definitionPosition()).toEqual({ x: 42, y: 24 })
      expect(store().nodes.find((node) => node.id === card.id)?.position).toEqual({ x: 900, y: 9 })
      // The move landed before the deletion, as the canvas lands any move, so it outlives the step's Undo.
      act(() => store().undo())
      expect(store().nodes.map((node) => node.id)).toContain("child_a")
      expect(definitionPosition()).toEqual({ x: 42, y: 24 })
      act(() => store().redo())
      expect(store().nodes.map((node) => node.id)).not.toContain("child_a")
      expect(definitionPosition()).toEqual({ x: 42, y: 24 })
    })

    it.each([
      // Nothing happens meanwhile: the batch's release and selection stand.
      ["nothing meanwhile", null, { x: 42, y: 24 }, false],
      // A new drag of the node is under way when the names arrive: it is shown where it is.
      ["a new drag meanwhile", { x: 500, y: 640 }, { x: 500, y: 640 }, true],
    ] as const)("ends a drag the waiting deletion's batch releases, given %s", async (_label, dragTo, shown, dragging) => {
      const fixture = makeUnresolvedFixture()
      const { resolutions, resolveGraphIdentities } = deferredResolver()
      openInStore(fixture)
      const { result } = renderHook(() => useSubmodelBoundaryEditing({
        ...hookParams(fixture), resolveGraphIdentities,
      }))
      startDrag()

      act(() => {
        expect(result.current.commitNodesChange(releasingBatch, canvasHandler())).toBe(true)
      })
      expect(childB().dragging).toBe(false)
      if (dragTo) {
        act(() => store().setNodesRaw((nodes) => nodes.map((node) => (
          node.id === "child_b" ? { ...node, position: dragTo, dragging: true } : node))))
      }
      const request = resolveGraphIdentities.mock.calls[0]![0]
      await act(async () => resolutions[0]!.resolve({
        nodes: [...request.nodes], edges: [...request.edges] as PipelineEdge[],
      }))

      expect(store().nodes.map((node) => node.id)).not.toContain("child_a")
      expect(childB().position).toEqual(shown)
      expect(childB().dragging).toBe(dragging)
      expect(childB().selected).toBe(true)
      // A drag under way reaches the definition when it ends.
      expect(definitionPosition()).toEqual({ x: 42, y: 24 })
    })

    it.each([
      [{ x: 10, y: 20 }],
      [{ x: 0, y: 0 }],
    ])("keeps the canvas and the definition together when the node is moved to %o while the deletion waits", async (release) => {
      const fixture = makeUnresolvedFixture()
      const { resolutions, resolveGraphIdentities } = deferredResolver()
      openInStore(fixture)
      const { result } = renderHook(() => useSubmodelBoundaryEditing({
        ...hookParams(fixture), resolveGraphIdentities,
      }))
      startDrag()

      act(() => {
        expect(result.current.commitNodesChange(releasingBatch, canvasHandler())).toBe(true)
      })
      // Moved again before the names arrive: the later move wins, on both layers.
      act(() => store().setNodesRaw((nodes) => nodes.map((node) => (
        node.id === "child_b" ? { ...node, position: release, dragging: false } : node))))
      expect(definitionPosition()).toEqual(release)
      const request = resolveGraphIdentities.mock.calls[0]![0]
      await act(async () => resolutions[0]!.resolve({
        nodes: [...request.nodes], edges: [...request.edges] as PipelineEdge[],
      }))

      expect(store().nodes.map((node) => node.id)).not.toContain("child_a")
      expect(childB().position).toEqual(release)
      expect(definitionPosition()).toEqual(release)
      act(() => store().undo())
      expect(childB().position).toEqual(definitionPosition())
      act(() => store().redo())
      expect(childB().position).toEqual(release)
      expect(definitionPosition()).toEqual(release)
    })

    it.each([
      ["the identity request fails", (resolution: { reject: (reason?: unknown) => void }) => {
        resolution.reject(new Error("identity service unavailable"))
      }],
      ["the document changes meanwhile", (resolution: { resolve: (value: { nodes: Node[]; edges: PipelineEdge[] }) => void }, request: GraphIdentityRequest) => {
        store().setNodes((nodes) => nodes.map((node) => (
          node.id === "child_b" ? { ...node, data: { ...node.data, config: { code: "df = child_a" } } } : node)))
        resolution.resolve({ nodes: [...request.nodes], edges: [...request.edges] as PipelineEdge[] })
      }],
    ] as const)("keeps the batch's release and selection when %s and the deletion does not land", async (_label, settle) => {
      const fixture = makeUnresolvedFixture()
      const { resolutions, resolveGraphIdentities } = deferredResolver()
      openInStore(fixture)
      const { result } = renderHook(() => useSubmodelBoundaryEditing({
        ...hookParams(fixture), resolveGraphIdentities,
      }))
      startDrag()

      act(() => {
        expect(result.current.commitNodesChange(releasingBatch, canvasHandler())).toBe(true)
      })
      const request = resolveGraphIdentities.mock.calls[0]![0]
      await act(async () => settle(resolutions[0]!, request))

      expect(store().nodes.map((node) => node.id)).toContain("child_a")
      expect(childB().position).toEqual({ x: 42, y: 24 })
      expect(childB().dragging).toBe(false)
      expect(childB().selected).toBe(true)
      expect(definitionPosition()).toEqual({ x: 42, y: 24 })
      expect(useToastStore.getState().toasts.map((toast) => toast.type)).toEqual(["error"])
    })

    it("voids the gesture when the document is edited while identities resolve", async () => {
      const fixture = makeUnresolvedFixture()
      const { resolutions, resolveGraphIdentities } = deferredResolver()
      openInStore(fixture)
      const { result } = renderHook(() => useSubmodelBoundaryEditing({
        ...hookParams(fixture), resolveGraphIdentities,
      }))
      const settled: boolean[] = []

      act(() => {
        expect(result.current.commitSharedNodeDeletion(
          new Set(["child_a"]), new Set(), undefined, (committed) => settled.push(committed),
        )).toBe("pending")
      })

      // A semantic edit made meanwhile voids the gesture.
      act(() => store().setNodes((nodes) => nodes.map((node) => (
        node.id === "child_b" ? { ...node, data: { ...node.data, config: { code: "df = child_a" } } } : node))))
      const externallyChanged = store().nodes
      const submodelsBefore = store().submodels
      const request = resolveGraphIdentities.mock.calls[0]![0]
      await act(async () => resolutions[0]!.resolve({
        nodes: [...request.nodes], edges: [...request.edges] as PipelineEdge[],
      }))

      expect(settled).toEqual([false])
      expect(store().undoStack).toHaveLength(1)
      expect(store().nodes).toBe(externallyChanged)
      expect(store().submodels).toBe(submodelsBefore)
      expect(fixture.setNodesAndEdgesAndSubmodels).not.toHaveBeenCalled()
      expect(useToastStore.getState().toasts.map((toast) => toast.text)).toEqual([
        "Submodel edit refused: the workspace changed while parent identities were resolving",
      ])
    })
  })
})
