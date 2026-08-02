import { act, renderHook } from "@testing-library/react"
import type { Edge } from "@xyflow/react"
import { describe, expect, it, vi } from "vitest"
import useSubmodelBoundaryEditing from "../useSubmodelBoundaryEditing"
import { makeEdge, makeNode } from "../../test-utils/factories"
import type { PipelineEdge, SubmodelPortData } from "../../types/node"
import { buildSubmodelViewGraph } from "../../utils/submodelViewGraph"

const SUBMODEL_NAME = "pricing"
const PLACEHOLDER_ID = "submodel__pricing"

type FixtureOptions = {
  unassignedInput?: boolean
  outputPorts?: string[]
  includeInternalEdge?: boolean
}

function makeFixture({
  unassignedInput = false,
  outputPorts = [],
  includeInternalEdge = false,
}: FixtureOptions = {}) {
  const childNodes = [makeNode("child_a"), makeNode("child_b")]
  const childEdges = includeInternalEdge
    ? [makeEdge("child_a", "child_b", { id: "internal" })]
    : []
  const placeholder = makeNode(PLACEHOLDER_ID, "submodel", {
    data: {
      label: SUBMODEL_NAME,
      config: {
        childNodeIds: childNodes.map((node) => node.id),
        inputPorts: [],
        outputPorts,
      },
    },
  })
  const parentNodes = [
    makeNode("external"),
    makeNode("consumer_a"),
    makeNode("consumer_b"),
    placeholder,
  ]
  const parentEdges: PipelineEdge[] = []
  if (unassignedInput) {
    parentEdges.push({
      ...makeEdge("external", PLACEHOLDER_ID, { id: "incoming" }),
      targetHandle: null,
    })
  }
  for (const childId of outputPorts) {
    parentEdges.push(
      {
        ...makeEdge(PLACEHOLDER_ID, "consumer_a", { id: `consumer-a-${childId}` }),
        sourceHandle: `out__${childId}`,
      },
      {
        ...makeEdge(PLACEHOLDER_ID, "consumer_b", { id: `consumer-b-${childId}` }),
        sourceHandle: `out__${childId}`,
      },
    )
  }
  const submodels = {
    [SUBMODEL_NAME]: {
      file: "modules/pricing.py",
      childNodeIds: childNodes.map((node) => node.id),
      inputPorts: [],
      outputPorts,
      graph: { nodes: childNodes, edges: childEdges },
    },
  }
  const view = buildSubmodelViewGraph({
    submodelName: SUBMODEL_NAME,
    childNodes,
    childEdges,
    parentNodes,
    parentEdges,
  })
  const graphRef = { current: { nodes: view.nodes, edges: view.edges as Edge[] } }
  const parentGraphRef = {
    current: {
      nodes: parentNodes,
      edges: parentEdges,
      submodels: submodels as Record<string, unknown>,
    },
  }
  const submodelsRef = { current: submodels as Record<string, unknown> }
  const setNodesAndEdgesAndSubmodels = vi.fn()

  return {
    childNodes,
    childEdges,
    parentNodes,
    parentEdges,
    submodels: submodels as Record<string, unknown>,
    view,
    graphRef,
    parentGraphRef,
    submodelsRef,
    setNodesAndEdgesAndSubmodels,
  }
}

function hookParams(fixture: ReturnType<typeof makeFixture>) {
  return {
    activeSubmodelName: SUBMODEL_NAME,
    nodes: fixture.view.nodes,
    edges: fixture.view.edges as PipelineEdge[],
    submodels: fixture.submodels,
    graphRef: fixture.graphRef,
    parentGraphRef: fixture.parentGraphRef,
    submodelsRef: fixture.submodelsRef,
    setNodesAndEdgesAndSubmodels: fixture.setNodesAndEdgesAndSubmodels,
  }
}

describe("useSubmodelBoundaryEditing", () => {
  it("keeps a new external frame unassigned until the user maps its Input row", () => {
    const fixture = makeFixture({ unassignedInput: true })
    const input = fixture.view.nodes.find(
      (node) => (node.data as unknown as SubmodelPortData).portDirection === "input",
    )
    const inputData = input?.data as unknown as SubmodelPortData
    expect(inputData.ports).toHaveLength(1)
    expect(fixture.view.edges).toHaveLength(0)

    const { result } = renderHook(() => useSubmodelBoundaryEditing(hookParams(fixture)))
    act(() => {
      expect(result.current.commitBoundaryConnection({
        source: input!.id,
        sourceHandle: inputData.ports[0].id,
        target: "child_a",
        targetHandle: null,
      })).toBe(true)
    })

    expect(fixture.setNodesAndEdgesAndSubmodels).toHaveBeenCalledOnce()
    expect(fixture.parentGraphRef.current?.edges).toEqual([
      expect.objectContaining({
        id: "incoming",
        targetHandle: "in__child_a",
      }),
    ])
  })

  it("deleting one declared export removes every collapsed parent consumer", () => {
    const fixture = makeFixture({ outputPorts: ["child_a"] })
    const exportEdge = fixture.view.edges.find(
      (edge) => edge.source === "child_a" && edge.target.includes("boundary"),
    )
    const { result } = renderHook(() => useSubmodelBoundaryEditing(hookParams(fixture)))

    act(() => {
      expect(result.current.deleteBoundaryEdge(exportEdge!.id)).toBe(true)
    })

    expect(fixture.setNodesAndEdgesAndSubmodels).toHaveBeenCalledOnce()
    expect(fixture.parentGraphRef.current?.edges).toEqual([])
    const placeholder = fixture.parentGraphRef.current?.nodes.find(
      (node) => node.id === PLACEHOLDER_ID,
    )
    const config = placeholder?.data.config as { outputPorts?: string[] }
    expect(config.outputPorts).toEqual([])
  })

  it("commits a mixed boundary and internal edge deletion as one atomic update", () => {
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

    expect(fixture.setNodesAndEdgesAndSubmodels).toHaveBeenCalledOnce()
    const committedEdges = fixture.setNodesAndEdgesAndSubmodels.mock.calls[0][1] as Edge[]
    expect(committedEdges).toEqual([])
    expect(fixture.parentGraphRef.current?.edges).toEqual([])
  })

  it("reconciles parent refs when an undo-like rerender restores older visible state", () => {
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

    expect(fixture.parentGraphRef.current?.edges).toEqual([])
    const metadata = fixture.submodelsRef.current[SUBMODEL_NAME] as {
      outputPorts: string[]
    }
    expect(metadata.outputPorts).toEqual([])
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

    const metadata = fixture.submodelsRef.current[SUBMODEL_NAME] as {
      childNodeIds: string[]
      outputPorts: string[]
    }
    expect(metadata.childNodeIds).toEqual(["child_a", "child_b"])
    expect(metadata.outputPorts).toEqual(["child_a"])
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
})
