import { act, renderHook } from "@testing-library/react"
import type { Edge, Node } from "@xyflow/react"
import { describe, expect, it, vi } from "vitest"
import useSubmodelBoundaryEditing from "../useSubmodelBoundaryEditing"
import { makeEdge, makeNode } from "../../test-utils/factories"
import type { PipelineEdge, SubmodelPortData } from "../../types/node"
import { buildSubmodelViewGraph } from "../../utils/submodelViewGraph"

const SUBMODEL_NAME = "Pricing"
const PLACEHOLDER_ID = "instance_primary"
const DEFINITION_ID = "definition_pricing"

type FixtureOptions = { bindInput?: boolean; outputPorts?: string[]; includeInternalEdge?: boolean }
function makeFixture({ bindInput = false, outputPorts = [], includeInternalEdge = false }: FixtureOptions = {}) {
  const childNodes = [makeNode("child_a"), makeNode("child_b")]
  const childEdges = includeInternalEdge ? [makeEdge("child_a", "child_b", { id: "internal" })] : []
  const placeholder = makeNode(PLACEHOLDER_ID, "submodel", { data: { label: SUBMODEL_NAME, nodeType: "submodel", config: { definitionId: DEFINITION_ID, alias: "pricing" } } })
  const parentNodes = [makeNode("external"), makeNode("consumer_a"), makeNode("consumer_b"), placeholder]
  const parentEdges: PipelineEdge[] = []
  if (bindInput) parentEdges.push({ ...makeEdge("external", PLACEHOLDER_ID, { id: "incoming" }), targetHandle: "in__incoming" })
  for (const childId of outputPorts) parentEdges.push({ ...makeEdge(PLACEHOLDER_ID, "consumer_a", { id: `consumer-a-${childId}` }), sourceHandle: `out__${childId}` }, { ...makeEdge(PLACEHOLDER_ID, "consumer_b", { id: `consumer-b-${childId}` }), sourceHandle: `out__${childId}` })
  const definition = { definitionId: DEFINITION_ID, file: "modules/pricing.py", graph: { nodes: childNodes, edges: childEdges }, inputPorts: [{ portId: "incoming", label: "Incoming", targets: [{ nodeId: "child_a", handleId: null }] }], outputPorts: outputPorts.map((portId) => ({ portId, label: portId, source: { nodeId: portId, handleId: null } })) }
  const submodels = { [DEFINITION_ID]: definition }
  const view = buildSubmodelViewGraph({ submodelName: SUBMODEL_NAME, instanceId: PLACEHOLDER_ID, definition, childNodes, childEdges, parentNodes, parentEdges })
  const graphRef = { current: { nodes: view.nodes, edges: view.edges as Edge[] } }
  const parentGraphRef = { current: { nodes: parentNodes, edges: parentEdges, submodels: submodels as Record<string, unknown> } }
  const submodelsRef = { current: submodels as Record<string, unknown> }
  const setNodesAndEdgesAndSubmodels = vi.fn()
  return { childNodes, childEdges, parentNodes, parentEdges, submodels: submodels as Record<string, unknown>, view, graphRef, parentGraphRef, submodelsRef, setNodesAndEdgesAndSubmodels }
}
function hookParams(fixture: ReturnType<typeof makeFixture>) {
  return { activeSubmodelName: SUBMODEL_NAME, activeSubmodelInstanceId: PLACEHOLDER_ID, activeSubmodelDefinitionId: DEFINITION_ID, nodes: fixture.view.nodes, edges: fixture.view.edges as PipelineEdge[], submodels: fixture.submodels, graphRef: fixture.graphRef, parentGraphRef: fixture.parentGraphRef, submodelsRef: fixture.submodelsRef, setNodesAndEdgesAndSubmodels: fixture.setNodesAndEdgesAndSubmodels }
}
describe("useSubmodelBoundaryEditing", () => {
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
      outputPorts: Array<{ portId: string }>
    }
    expect(metadata.outputPorts.map((port) => port.portId)).toEqual(["child_a"])
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
      outputPorts: Array<{ portId: string }>
    }
    expect(metadata.graph.nodes.map((node) => node.id)).toEqual(["child_a", "child_b"])
    expect(metadata.outputPorts.map((port) => port.portId)).toEqual(["child_a"])
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
})
