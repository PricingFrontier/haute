import { describe, expect, it } from "vitest"
import type { Connection, Node } from "@xyflow/react"
import type { PipelineEdge, SubmodelDefinition, SubmodelPortData } from "../../types/node"
import { makeNode } from "../../test-utils/factories"
import { edgeInputName } from "../apiInputPorts"
import { buildSubmodelViewGraph } from "../submodelViewGraph"
import { applySubmodelBoundaryConnection, removeSubmodelBoundaryEdges, type SubmodelBoundaryEditState } from "../submodelBoundaryEditing"
function state(bound = false): SubmodelBoundaryEditState {
  const children = ["prepare", "score"].map((id) => makeNode(id, "polars", {
    data: {
      _functionName: `${id}_function`,
      _defaultInputName: `${id}_input`,
      _sourceHandleInputNames: {},
    },
  }))
  const definition: SubmodelDefinition = {
    definitionId: "definition_pricing",
    file: "modules/pricing.py",
    graph: { nodes: children, edges: [] },
    inputPorts: [{
      portId: "policy",
      label: "Policy",
      targets: [{ nodeId: "prepare", handleId: null }],
    }],
    outputPorts: [{
      portId: "premium",
      label: "Premium",
      source: { nodeId: "score", handleId: "result" },
    }],
    _inputPortInputNames: { policy: "policy_input" },
  }
  const parentNodes = [
    makeNode("instance_primary", "submodel", { data: { label: "Pricing", nodeType: "submodel", config: { definitionId: definition.definitionId, alias: "pricing" } } }),
    makeNode("instance_secondary", "submodel", { data: { label: "Pricing 2", nodeType: "submodel", config: { definitionId: definition.definitionId, alias: "pricing_2" } } }),
    makeNode("consumer"),
  ]
  const parentEdges: PipelineEdge[] = bound
    ? [{ id: "consumer", source: "instance_primary", sourceHandle: "out__premium", target: "consumer" }]
    : []
  const view = buildSubmodelViewGraph({
    submodelName: "pricing",
    instanceId: "instance_primary",
    definition,
    childNodes: children,
    childEdges: [],
    parentNodes,
    parentEdges,
  })
  const viewNodes = view.nodes.map((node) => {
    if (node.type !== "submodelPort") return node
    const input = (node.data as SubmodelPortData).portDirection === "input"
    return {
      ...node,
      data: {
        ...node.data,
        _functionName: input ? "boundary_input_function" : "boundary_output_function",
        _defaultInputName: null,
        _sourceHandleInputNames: input ? { policy: "policy_input" } : {},
        _configReference: input ? "boundary_input_config" : "boundary_output_config",
      },
    }
  })
  const viewEdges = view.edges.map((edge) => ({
    ...edge,
    data: {
      ...edge.data,
      _inputName: edge.source === boundary(viewNodes, "input").id
        ? "policy_input"
        : "score_input",
    },
  })) as PipelineEdge[]
  return {
    submodelName: "pricing",
    instanceId: "instance_primary",
    definitionId: definition.definitionId,
    viewNodes,
    viewEdges,
    parentNodes,
    parentEdges,
    submodels: { [definition.definitionId]: definition },
  }
}
const boundary = (nodes: Node[], direction: "input" | "output") => nodes.find((node) => node.type === "submodelPort" && (node.data as SubmodelPortData).portDirection === direction)!
describe("submodelBoundaryEditing", () => {
  it("adds a target without changing occurrences or dropping authoritative identities", () => {
    const current = state()
    const input = boundary(current.viewNodes, "input")
    const result = applySubmodelBoundaryConnection(current, {
      source: input.id,
      sourceHandle: "policy",
      target: "score",
      targetHandle: "joined",
    } as Connection)!

    expect(result.submodels.definition_pricing).toMatchObject({
      inputPorts: [{
        portId: "policy",
        targets: [
          { nodeId: "prepare", handleId: null },
          { nodeId: "score", handleId: "joined" },
        ],
      }],
      _inputPortInputNames: { policy: "policy_input" },
    })
    expect(result.parentNodes.map((node) => node.data.config)).toEqual(
      current.parentNodes.map((node) => node.data.config),
    )
    const nextInput = boundary(result.viewNodes, "input")
    expect(nextInput.data).toMatchObject({
      _functionName: "boundary_input_function",
      _defaultInputName: null,
      _sourceHandleInputNames: { policy: "policy_input" },
      _configReference: "boundary_input_config",
    })
    const added = result.viewEdges.find((edge) => edge.target === "score" && edge.source === nextInput.id)!
    expect(added.data?._inputName).toBe("policy_input")
    expect(edgeInputName(
      added,
      nextInput as unknown as Parameters<typeof edgeInputName>[1],
    )).toBe("policy_input")
    expect(result.viewEdges.every((edge) => typeof edge.data?._inputName === "string")).toBe(true)
  })

  it("filters authoritative input identities when the final target removes a public port", () => {
    const current = state()
    const input = boundary(current.viewNodes, "input")
    const declaration = current.viewEdges.find((edge) => edge.source === input.id)!

    const result = removeSubmodelBoundaryEdges(current, [declaration.id])!

    const definition = result.submodels.definition_pricing as SubmodelDefinition
    expect(definition.inputPorts).toEqual([])
    expect(definition._inputPortInputNames).toEqual({})
    expect(boundary(result.viewNodes, "input").data._sourceHandleInputNames).toEqual({})
  })
  it("blocks deletion of a used public output", () => { const current = state(true); const output = boundary(current.viewNodes, "output"); const declaration = current.viewEdges.find((edge) => edge.target === output.id)!; expect(() => removeSubmodelBoundaryEdges(current, [declaration.id])).toThrow(/Pricing.*Premium/s) })
  it("removes an unbound public output from the shared definition", () => { const current = state(); const output = boundary(current.viewNodes, "output"); const declaration = current.viewEdges.find((edge) => edge.target === output.id)!; expect(removeSubmodelBoundaryEdges(current, [declaration.id])?.submodels.definition_pricing).toMatchObject({ outputPorts: [] }) })
})
