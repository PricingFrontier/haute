import { describe, expect, it } from "vitest"
import type { Connection, Edge, Node } from "@xyflow/react"
import type { PipelineEdge, SubmodelNodeData, SubmodelPortData } from "../../types/node"
import { makeNode } from "../../test-utils/factories"
import { buildSubmodelViewGraph } from "../submodelViewGraph"
import {
  applySubmodelBoundaryConnection,
  removeSubmodelBoundaryEdges,
  type SubmodelBoundaryEditState,
} from "../submodelBoundaryEditing"

function placeholder(
  inputPorts: string[] = [],
  outputPorts: string[] = [],
): Node<SubmodelNodeData> {
  return makeNode("submodel__pricing", "submodel", {
    data: {
      label: "pricing",
      config: {
        childNodeIds: ["child_a", "child_b"],
        inputPorts,
        outputPorts,
        outputPortLabels: Object.fromEntries(
          outputPorts.map((id) => [id, id === "child_a" ? "Frame A" : "Frame B"]),
        ),
      },
    },
  }) as Node<SubmodelNodeData>
}

function childNodes(): Node[] {
  return [
    makeNode("child_a", "polars", { data: { label: "Frame A" } }),
    makeNode("child_b", "polars", { data: { label: "Frame B" } }),
  ]
}

function boundary(
  nodes: Node[],
  direction: "input" | "output",
): Node {
  const node = nodes.find(
    (candidate) =>
      candidate.type === "submodelPort"
      && (candidate.data as unknown as SubmodelPortData).portDirection === direction,
  )
  if (!node) throw new Error(`Missing ${direction} boundary`)
  return node
}

function editState({
  inputPorts = [],
  outputPorts = [],
  parentEdges = [],
}: {
  inputPorts?: string[]
  outputPorts?: string[]
  parentEdges?: PipelineEdge[]
} = {}): SubmodelBoundaryEditState {
  const children = childNodes()
  const parentNodes = [
    makeNode("quote_input", "apiInput", { data: { label: "QUOTE IN" } }),
    makeNode("consumer_a"),
    makeNode("consumer_b"),
    placeholder(inputPorts, outputPorts),
  ]
  const submodels = {
    pricing: {
      file: "modules/pricing.py",
      childNodeIds: ["child_a", "child_b"],
      inputPorts,
      outputPorts,
      graph: {
        nodes: children,
        edges: [],
        pipeline_name: "pricing",
      },
    },
  }
  const view = buildSubmodelViewGraph({
    submodelName: "pricing",
    childNodes: children,
    childEdges: [],
    parentNodes,
    parentEdges,
  })
  return {
    submodelName: "pricing",
    viewNodes: view.nodes,
    viewEdges: view.edges as PipelineEdge[],
    parentNodes,
    parentEdges,
    submodels,
  }
}

function configOf(nodes: Node[]): NonNullable<SubmodelNodeData["config"]> {
  const node = nodes.find((candidate) => candidate.id === "submodel__pricing")
  if (!node) throw new Error("Missing placeholder")
  return (node.data as SubmodelNodeData).config ?? {}
}

function metadataOf(submodels: Record<string, unknown>): Record<string, unknown> {
  return submodels.pricing as Record<string, unknown>
}

describe("submodelBoundaryEditing", () => {
  it("assigns an available Input row only after the user connects it to a child", () => {
    const unassigned: PipelineEdge = {
      id: "input-frame",
      source: "quote_input",
      sourceHandle: "quote",
      target: "submodel__pricing",
      targetHandle: null,
    }
    const state = editState({ parentEdges: [unassigned] })
    const input = boundary(state.viewNodes, "input")
    const row = (input.data as unknown as SubmodelPortData).ports[0]

    const result = applySubmodelBoundaryConnection(state, {
      source: input.id,
      sourceHandle: row.id,
      target: "child_a",
      targetHandle: "join",
    } as Connection)

    expect(result).not.toBeNull()
    expect(result!.parentEdges).toEqual([
      expect.objectContaining({
        id: "input-frame",
        targetHandle: "in__child_a",
        targetPort: "join",
      }),
    ])
    expect(result!.viewEdges).toContainEqual(expect.objectContaining({
      source: input.id,
      sourceHandle: row.id,
      target: "child_a",
      targetHandle: "join",
    }))
    expect(configOf(result!.parentNodes).inputPorts).toEqual(["child_a"])
    expect(metadataOf(result!.submodels).inputPorts).toEqual(["child_a"])
  })

  it("deleting the last Input mapping returns the parent frame to available", () => {
    const state = editState({
      inputPorts: ["child_a"],
      parentEdges: [{
        id: "input-frame",
        source: "quote_input",
        sourceHandle: "quote",
        target: "submodel__pricing",
        targetHandle: "in__child_a",
        targetPort: "join",
      }],
    })
    const input = boundary(state.viewNodes, "input")
    const mapping = state.viewEdges.find((edge) => edge.source === input.id)
    if (!mapping) throw new Error("Missing projected input mapping")

    const result = removeSubmodelBoundaryEdges(state, [mapping.id])

    expect(result).not.toBeNull()
    expect(result!.parentEdges).toEqual([
      expect.objectContaining({
        id: "input-frame",
        targetHandle: null,
        targetPort: null,
      }),
    ])
    expect(configOf(result!.parentNodes).inputPorts).toEqual([])
    expect(metadataOf(result!.submodels).inputPorts).toEqual([])
    const inputAfter = boundary(result!.viewNodes, "input")
    expect((inputAfter.data as unknown as SubmodelPortData).ports).toHaveLength(1)
    expect(result!.viewEdges.some((edge) => edge.source === inputAfter.id)).toBe(false)
  })

  it("declares an unused export when a child connects to the shared Output", () => {
    const state = editState()
    const output = boundary(state.viewNodes, "output")

    const result = applySubmodelBoundaryConnection(state, {
      source: "child_b",
      sourceHandle: null,
      target: output.id,
      targetHandle: null,
    } as Connection)

    expect(result).not.toBeNull()
    expect(configOf(result!.parentNodes).outputPorts).toEqual(["child_b"])
    expect(configOf(result!.parentNodes).outputPortLabels).toEqual({
      child_b: "Frame B",
    })
    expect(metadataOf(result!.submodels).outputPorts).toEqual(["child_b"])
    expect(result!.parentEdges).toEqual([])
    expect(result!.viewEdges).toContainEqual(expect.objectContaining({
      source: "child_b",
      target: output.id,
      targetHandle: null,
      data: {
        submodelBoundary: {
          direction: "output",
          parentConsumerEdges: [],
        },
      },
    }))
  })

  it("undeclares an export and removes every collapsed consumer atomically", () => {
    const consumers: PipelineEdge[] = [
      {
        id: "consume-a",
        source: "submodel__pricing",
        sourceHandle: "out__child_b",
        target: "consumer_a",
      },
      {
        id: "consume-b",
        source: "submodel__pricing",
        sourceHandle: "out__child_b",
        target: "consumer_b",
      },
    ]
    const state = editState({
      outputPorts: ["child_b"],
      parentEdges: consumers,
    })
    const output = boundary(state.viewNodes, "output")
    const declaration = state.viewEdges.find(
      (edge) => edge.source === "child_b" && edge.target === output.id,
    )
    if (!declaration) throw new Error("Missing projected export")

    const result = removeSubmodelBoundaryEdges(state, [declaration.id])

    expect(result).not.toBeNull()
    expect(result!.parentEdges).toEqual([])
    expect(configOf(result!.parentNodes).outputPorts).toEqual([])
    expect(configOf(result!.parentNodes).outputPortLabels).toEqual({})
    expect(metadataOf(result!.submodels).outputPorts).toEqual([])
    expect(result!.viewEdges.some((edge) => edge.id === declaration.id)).toBe(false)
  })

  it("rejects a duplicate connection to the same exported child", () => {
    const state = editState({ outputPorts: ["child_b"] })
    const output = boundary(state.viewNodes, "output")

    expect(applySubmodelBoundaryConnection(state, {
      source: "child_b",
      sourceHandle: null,
      target: output.id,
      targetHandle: null,
    } as Connection)).toBeNull()
  })

  it("does not handle ordinary internal edges", () => {
    const state = editState()
    expect(applySubmodelBoundaryConnection(state, {
      source: "child_a",
      sourceHandle: null,
      target: "child_b",
      targetHandle: null,
    } as Connection)).toBeNull()
    expect(removeSubmodelBoundaryEdges(state, ["missing"])).toBeNull()
  })

  it("preserves unrelated internal edges while reconciling a boundary edit", () => {
    const state = editState()
    const internal: Edge = {
      id: "internal",
      source: "child_a",
      target: "child_b",
    }
    state.viewEdges = [...state.viewEdges, internal]
    const output = boundary(state.viewNodes, "output")

    const result = applySubmodelBoundaryConnection(state, {
      source: "child_a",
      sourceHandle: null,
      target: output.id,
      targetHandle: null,
    } as Connection)

    const graph = metadataOf(result!.submodels).graph as {
      edges: Edge[]
    }
    expect(graph.edges).toEqual([internal])
  })
})
