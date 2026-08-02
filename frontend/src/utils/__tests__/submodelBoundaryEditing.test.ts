import { describe, expect, it } from "vitest"
import type { Connection, Edge, Node } from "@xyflow/react"
import type { PipelineEdge, SubmodelNodeData, SubmodelPortData } from "../../types/node"
import { makeNode } from "../../test-utils/factories"
import { buildSubmodelViewGraph } from "../submodelViewGraph"
import {
  applySubmodelBoundaryConnection,
  reconcileSubmodelBoundaryState,
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

  it("keeps both boundary cards fixed when an Input row is assigned", () => {
    const state = editState({
      parentEdges: [{
        id: "input-frame",
        source: "quote_input",
        sourceHandle: "quote",
        target: "submodel__pricing",
        targetHandle: null,
      }],
    })
    const inputId = boundary(state.viewNodes, "input").id
    const outputId = boundary(state.viewNodes, "output").id
    const inputPosition = { x: -420, y: 160 }
    const outputPosition = { x: 880, y: 260 }
    state.viewNodes = state.viewNodes.map((node) => {
      if (node.id === inputId) return { ...node, position: inputPosition }
      if (node.id === outputId) return { ...node, position: outputPosition }
      return node
    })
    const input = boundary(state.viewNodes, "input")
    const row = (input.data as unknown as SubmodelPortData).ports[0]

    const result = applySubmodelBoundaryConnection(state, {
      source: input.id,
      sourceHandle: row.id,
      target: "child_a",
      targetHandle: null,
    } as Connection)

    expect(result).not.toBeNull()
    expect(boundary(result!.viewNodes, "input").position).toEqual(
      inputPosition,
    )
    expect(boundary(result!.viewNodes, "output").position).toEqual(
      outputPosition,
    )
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

  it("removing every mapping of one frame in a single batch returns the frame to available", () => {
    const state = editState({
      inputPorts: ["child_a", "child_b"],
      parentEdges: [
        {
          id: "map-a",
          source: "quote_input",
          sourceHandle: "quote",
          target: "submodel__pricing",
          targetHandle: "in__child_a",
        },
        {
          id: "map-b",
          source: "quote_input",
          sourceHandle: "quote",
          target: "submodel__pricing",
          targetHandle: "in__child_b",
        },
      ],
    })
    const input = boundary(state.viewNodes, "input")
    const mappings = state.viewEdges.filter((edge) => edge.source === input.id)
    expect(mappings).toHaveLength(2)

    const result = removeSubmodelBoundaryEdges(state, mappings.map((edge) => edge.id))

    expect(result).not.toBeNull()
    expect(result!.parentEdges).toEqual([
      expect.objectContaining({
        source: "quote_input",
        sourceHandle: "quote",
        targetHandle: null,
        targetPort: null,
      }),
    ])
    expect(configOf(result!.parentNodes).inputPorts).toEqual([])
    const inputAfter = boundary(result!.viewNodes, "input")
    expect((inputAfter.data as unknown as SubmodelPortData).ports).toHaveLength(1)
  })

  it("reconcile returns a directly-deleted mapping's backing edge to available", () => {
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
    state.viewEdges = state.viewEdges.filter((edge) => edge.source !== input.id)

    const result = reconcileSubmodelBoundaryState(state)

    expect(result).not.toBeNull()
    expect(result!.parentEdges).toEqual([
      expect.objectContaining({
        id: "input-frame",
        targetHandle: null,
        targetPort: null,
      }),
    ])
    expect(configOf(result!.parentNodes).inputPorts).toEqual([])
    const inputAfter = boundary(result!.viewNodes, "input")
    expect((inputAfter.data as unknown as SubmodelPortData).ports).toHaveLength(1)
  })

  it("reconcile leaves an available draft when a mapped child is deleted directly", () => {
    const state = editState({
      inputPorts: ["child_a"],
      parentEdges: [{
        id: "input-frame",
        source: "quote_input",
        sourceHandle: "quote",
        target: "submodel__pricing",
        targetHandle: "in__child_a",
      }],
    })
    state.viewNodes = state.viewNodes.filter((node) => node.id !== "child_a")
    state.viewEdges = state.viewEdges.filter(
      (edge) => edge.source !== "child_a" && edge.target !== "child_a",
    )

    const result = reconcileSubmodelBoundaryState(state)

    expect(result).not.toBeNull()
    expect(result!.parentEdges).toEqual([
      expect.objectContaining({
        id: "input-frame",
        targetHandle: null,
        targetPort: null,
      }),
    ])
    expect(metadataOf(result!.submodels).childNodeIds).toEqual(["child_b"])
    expect(configOf(result!.parentNodes).inputPorts).toEqual([])
  })

  it("reconcile drops a directly-deleted mapping when its frame still has another mapping", () => {
    const state = editState({
      inputPorts: ["child_a", "child_b"],
      parentEdges: [
        {
          id: "map-a",
          source: "quote_input",
          sourceHandle: "quote",
          target: "submodel__pricing",
          targetHandle: "in__child_a",
        },
        {
          id: "map-b",
          source: "quote_input",
          sourceHandle: "quote",
          target: "submodel__pricing",
          targetHandle: "in__child_b",
        },
      ],
    })
    const input = boundary(state.viewNodes, "input")
    state.viewEdges = state.viewEdges.filter(
      (edge) => !(edge.source === input.id && edge.target === "child_b"),
    )

    const result = reconcileSubmodelBoundaryState(state)

    expect(result).not.toBeNull()
    expect(result!.parentEdges).toEqual([
      expect.objectContaining({ id: "map-a", targetHandle: "in__child_a" }),
    ])
    expect(configOf(result!.parentNodes).inputPorts).toEqual(["child_a"])
  })

  it("reconcile converts a stale mapped inbound edge into an available draft", () => {
    const state = editState({
      parentEdges: [{
        id: "stale",
        source: "quote_input",
        sourceHandle: "quote",
        target: "submodel__pricing",
        targetHandle: "in__ghost",
        targetPort: "join",
      }],
    })

    const result = reconcileSubmodelBoundaryState(state)

    expect(result).not.toBeNull()
    expect(result!.parentEdges).toEqual([
      expect.objectContaining({
        id: "stale",
        targetHandle: null,
        targetPort: null,
      }),
    ])
    const inputAfter = boundary(result!.viewNodes, "input")
    expect((inputAfter.data as unknown as SubmodelPortData).ports).toHaveLength(1)
  })

  it("reconcile passes wrong-prefixed boundary handles through untouched", () => {
    const malformed: PipelineEdge = {
      id: "bad",
      source: "quote_input",
      target: "submodel__pricing",
      targetHandle: "into__child_a",
    }
    const state = editState({ parentEdges: [malformed] })

    const result = reconcileSubmodelBoundaryState(state)

    expect(result).not.toBeNull()
    expect(result!.parentEdges).toEqual([malformed])
  })

  it("reconcile preserves the relative order of retained parent edges", () => {
    const mapped: PipelineEdge = {
      id: "map-a",
      source: "quote_input",
      sourceHandle: "quote",
      target: "submodel__pricing",
      targetHandle: "in__child_a",
    }
    const external: PipelineEdge = {
      id: "external",
      source: "quote_input",
      target: "consumer_a",
    }
    const consumer: PipelineEdge = {
      id: "consume-b",
      source: "submodel__pricing",
      sourceHandle: "out__child_b",
      target: "consumer_b",
    }
    const state = editState({
      inputPorts: ["child_a"],
      outputPorts: ["child_b"],
      parentEdges: [mapped, external, consumer],
    })

    const result = reconcileSubmodelBoundaryState(state)

    expect(result).not.toBeNull()
    expect(result!.parentEdges.map((edge) => edge.id)).toEqual([
      "map-a",
      "external",
      "consume-b",
    ])
  })

  it("reconcile refuses a view that lacks the composite boundary cards", () => {
    const state = editState()
    state.viewNodes = state.viewNodes.filter((node) => node.type !== "submodelPort")

    expect(reconcileSubmodelBoundaryState(state)).toBeNull()
  })
})
