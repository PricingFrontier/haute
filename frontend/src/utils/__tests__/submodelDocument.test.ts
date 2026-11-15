import { describe, expect, it } from "vitest"
import type { Edge, Node } from "@xyflow/react"
import type { PipelineEdge, SubmodelDefinition, SubmodelPortData } from "../../types/node"
import { makeIdentifiedNode, makeNode } from "../../test-utils/factories"
import { reconcileCanonicalSubmodelBoundaryState } from "../canonicalSubmodelBoundaryEditing"
import { canonicalJson } from "../canonicalJson"
import { authoredEdgeForm, authoredNodeForm, serializeSnapshot } from "../graphSnapshot"
import {
  childAnnotations,
  liftSubmodelCanvas,
  projectSubmodelFocus,
  type SubmodelCanvasLift,
} from "../submodelDocument"

const DEFINITION_ID = "definition_pricing"
const focus = { instanceId: "pricing", definitionId: DEFINITION_ID }

function occurrence(id: string, alias: string, instanceOf?: string): Node {
  return makeNode(id, "submodel", {
    data: {
      label: alias,
      nodeType: "submodel",
      config: { definition_id: DEFINITION_ID, alias, ...(instanceOf ? { instance_of: instanceOf } : {}) },
    },
  })
}

function document() {
  const definition: SubmodelDefinition = {
    definitionId: DEFINITION_ID,
    file: "modules/pricing.py",
    graph: {
      nodes: [
        makeIdentifiedNode("prepare", "polars", { position: { x: 100, y: 100 } }),
        makeIdentifiedNode("score", "edgeJoin", { position: { x: 400, y: 100 } }),
        makeIdentifiedNode("spare", "polars", { position: { x: 400, y: 300 } }),
      ],
      edges: [{ id: "e_prepare_score", source: "prepare", target: "score", targetHandle: "base", data: { _inputName: "prepare" } }],
    },
    inputPorts: [{ name: "policy", targets: [{ nodeId: "prepare", handleId: null }] }],
    outputPorts: [{ name: "premium", source: { nodeId: "score", handleId: null } }],
  }
  const rootNodes = [makeIdentifiedNode("api"), occurrence("pricing", "pricing"), occurrence("pricing_2", "pricing_2", "pricing"), makeIdentifiedNode("consumer")]
  const rootEdges: PipelineEdge[] = [
    { id: "feed", source: "api", target: "pricing", targetHandle: "in__policy" },
    { id: "consume", source: "pricing", sourceHandle: "out__premium", target: "consumer" },
  ]
  const submodels = { [DEFINITION_ID]: definition }
  const view = projectSubmodelFocus({ rootNodes, rootEdges, submodels, focus })
  return { definition, rootNodes, rootEdges, submodels, view }
}

const card = (nodes: readonly Node[], direction: "input" | "output") =>
  nodes.find((node) => node.type === "submodelPort" && (node.data as SubmodelPortData).portDirection === direction)!

function lift(next: { nodes: Node[]; edges: Edge[] }, readOnly = false): SubmodelCanvasLift {
  const { definition, view } = document()
  return liftSubmodelCanvas({ definition, readOnly, previous: view, next })
}

/** What the old rebuild made of the same canvas, in the document's authored form. */
function oracle(next: { nodes: Node[]; edges: Edge[] }) {
  const { rootNodes, rootEdges, submodels } = document()
  const result = reconcileCanonicalSubmodelBoundaryState({
    submodelName: "pricing",
    instanceId: focus.instanceId,
    definitionId: DEFINITION_ID,
    viewNodes: next.nodes,
    viewEdges: next.edges as PipelineEdge[],
    parentNodes: rootNodes,
    parentEdges: rootEdges,
    submodels,
  })!
  const definition = result.submodels[DEFINITION_ID] as SubmodelDefinition
  // The parent is untouched by any write the lift accepts.
  const persisted = (edges: PipelineEdge[]) => serializeSnapshot({ nodes: [], edges, preamble: "", submodels: {} })
  expect(persisted(result.parentEdges)).toBe(persisted(rootEdges))
  return authored(definition)
}

function authored(definition: SubmodelDefinition) {
  return canonicalJson({
    nodes: definition.graph.nodes.map(authoredNodeForm),
    edges: definition.graph.edges.map((edge) => authoredEdgeForm(edge as PipelineEdge)),
    inputPorts: definition.inputPorts,
    outputPorts: definition.outputPorts,
  })
}

function expectSameAsOracle(next: { nodes: Node[]; edges: Edge[] }) {
  const result = lift(next)
  expect(result.kind).toBe("definition")
  if (result.kind !== "definition") return
  expect(authored(result.definition)).toBe(oracle(next))
  for (const node of result.definition.graph.nodes) {
    expect(node).not.toHaveProperty("selected")
    expect(Object.keys(node.data).filter((key) => key === "_columns")).toEqual([])
  }
}

const mapNodes = (nodes: Node[], id: string, change: (node: Node) => Node) =>
  nodes.map((node) => (node.id === id ? change(node) : node))

describe("liftSubmodelCanvas", () => {
  it("changes the definition as the old rebuild did, for every write it accepts", () => {
    const { view } = document()
    const moved = { ...view, nodes: mapNodes(view.nodes, "prepare", (node) => ({ ...node, position: { x: 140, y: 90 } })) }
    expectSameAsOracle(moved)

    const configured = { ...view, nodes: mapNodes(view.nodes, "score", (node) => ({ ...node, data: { ...node.data, config: { how: "left" } } })) }
    expectSameAsOracle(configured)

    const added = { ...view, nodes: [...view.nodes, makeIdentifiedNode("extra", "polars", { position: { x: 700, y: 0 } })] }
    expectSameAsOracle(added)

    const deleted = { nodes: view.nodes.filter((node) => node.id !== "spare"), edges: view.edges }
    expectSameAsOracle(deleted)

    const connected = { ...view, edges: [...view.edges, { id: "e_prepare_spare", source: "prepare", target: "spare", data: { _inputName: "prepare" } }] }
    expectSameAsOracle(connected)

    const disconnected = { ...view, edges: view.edges.filter((edge) => edge.id !== "e_prepare_score") }
    expectSameAsOracle(disconnected)

    // An Edge Join's inputs swapped where one comes from the Input card: the port follows the handle.
    const input = card(view.nodes, "input")
    const swapped = { ...view, edges: view.edges.map((edge) => (edge.source === input.id ? { ...edge, targetHandle: "join" } : edge)) }
    expectSameAsOracle(swapped)

    // A frame renamed at the child feeding the Output card.
    const output = card(view.nodes, "output")
    const renamed = { ...view, edges: view.edges.map((edge) => (edge.target === output.id ? { ...edge, sourceHandle: "joined" } : edge)) }
    expectSameAsOracle(renamed)
  })

  it("refuses a write that adds or removes a boundary edge, a card's ports or a port's child", () => {
    const { view } = document()
    const input = card(view.nodes, "input")
    const boundaryEdge = view.edges.find((edge) => edge.source === input.id)!

    const removedBoundary = lift({ ...view, edges: view.edges.filter((edge) => edge !== boundaryEdge) })
    const addedBoundary = lift({ ...view, edges: [...view.edges, { ...boundaryEdge, id: "extra-boundary", target: "spare" }] })
    const retargeted = lift({ ...view, edges: view.edges.map((edge) => (edge === boundaryEdge ? { ...edge, target: "spare" } : edge)) })
    const portsChanged = lift({ ...view, nodes: mapNodes(view.nodes, input.id, (node) => ({ ...node, data: { ...node.data, ports: [] } })) })
    const childGone = lift({ ...view, nodes: view.nodes.filter((node) => node.id !== "prepare") })

    for (const result of [removedBoundary, addedBoundary, retargeted, portsChanged]) {
      expect(result).toMatchObject({ kind: "refused", reason: expect.stringContaining("boundary") })
    }
    expect(childGone).toMatchObject({ kind: "refused", reason: expect.stringContaining("policy") })
  })

  it("leaves the definition alone for a selection or a preview's columns, even on a read-only copy", () => {
    const { view } = document()
    const selected = { ...view, nodes: mapNodes(view.nodes, "score", (node) => ({ ...node, selected: true })) }
    const previewed = { ...view, nodes: mapNodes(view.nodes, "score", (node) => ({ ...node, data: { ...node.data, _columns: [{ name: "premium", dtype: "Float64" }] } })) }

    expect(lift(selected)).toEqual({ kind: "canvas" })
    expect(lift(previewed)).toEqual({ kind: "canvas" })
    expect(lift(previewed, true)).toEqual({ kind: "canvas" })
    expect(childAnnotations(previewed.nodes)).toEqual({ score: { _columns: [{ name: "premium", dtype: "Float64" }] } })
  })

  it("refuses, rather than throws on, a new edge to a card that names no port", () => {
    const { view } = document()
    const input = card(view.nodes, "input")
    const dropped = lift({ ...view, edges: [...view.edges, { id: "dropped", source: input.id, sourceHandle: "policy", target: "spare" }] })

    expect(dropped).toMatchObject({ kind: "refused", reason: expect.stringContaining("boundary") })
  })

  it("refuses an authored change to a read-only copy", () => {
    const { view } = document()
    const configured = { ...view, nodes: mapNodes(view.nodes, "score", (node) => ({ ...node, data: { ...node.data, config: { how: "left" } } })) }

    expect(lift(configured, true)).toMatchObject({ kind: "refused", reason: expect.stringContaining("read-only") })
  })
})

describe("projectSubmodelFocus", () => {
  it("draws an occurrence from the document, with its annotations and the cards where they were", () => {
    const { rootNodes, rootEdges, submodels, view } = document()
    const moved = view.nodes.map((node) =>
      node.type === "submodelPort" ? { ...node, position: { x: -900, y: 5 } } : node,
    )

    const projected = projectSubmodelFocus({
      rootNodes,
      rootEdges,
      submodels,
      focus,
      annotations: { score: { _columns: [{ name: "premium", dtype: "Float64" }] } },
      previous: { nodes: moved, edges: view.edges },
    })

    expect(projected.nodes.find((node) => node.id === "score")?.data._columns).toEqual([{ name: "premium", dtype: "Float64" }])
    expect(projected.nodes.filter((node) => node.type === "submodelPort").map((node) => node.position)).toEqual([
      { x: -900, y: 5 },
      { x: -900, y: 5 },
    ])
    expect(projected.edges.every((edge) => typeof edge.data?._inputName === "string")).toBe(true)
  })

  it("refuses an occurrence the document no longer holds", () => {
    const { rootEdges, submodels } = document()

    expect(() => projectSubmodelFocus({ rootNodes: [makeNode("api")], rootEdges, submodels, focus })).toThrow(
      /not in the document/,
    )
  })
})
