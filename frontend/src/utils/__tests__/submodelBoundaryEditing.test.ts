import { describe, expect, it } from "vitest"
import type { Connection, Node } from "@xyflow/react"
import type { PipelineEdge, SubmodelDefinition, SubmodelPortData } from "../../types/node"
import { makeNode } from "../../test-utils/factories"
import { edgeInputName } from "../apiInputPorts"
import { buildSubmodelViewGraph } from "../submodelViewGraph"
import {
  applySubmodelBoundaryConnection,
  connectSubmodelInputFromParentConnection,
  removeSubmodelBoundaryEdges,
  removeSubmodelElements,
  removeSubmodelInputPort,
  type SubmodelBoundaryEditState,
} from "../submodelBoundaryEditing"
import { SUBMODEL_INPUT_HANDLE } from "../flowHandles"
import { chooseInstanceInput, retargetSubmodelInput } from "../canonicalSubmodelBoundaryEditing"
const INTERNAL: PipelineEdge = { id: "e_prepare_score", source: "prepare", target: "score", data: { _inputName: "prepare_input" } }
function state(bound = false, internal = false): SubmodelBoundaryEditState {
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
    graph: { nodes: children, edges: internal ? [INTERNAL] : [] },
    inputPorts: [{
      name: "policy",
      targets: [{ nodeId: "prepare", handleId: null }],
    }],
    outputPorts: [{
      name: "premium",
      source: { nodeId: "score", handleId: "result" },
    }],
  }
  const parentNodes = [
    makeNode("pricing", "submodel", { data: { label: "pricing", nodeType: "submodel", config: { definition_id: definition.definitionId, alias: "pricing" } } }),
    makeNode("pricing_2", "submodel", { data: { label: "pricing_2", nodeType: "submodel", config: { definition_id: definition.definitionId, alias: "pricing_2" } } }),
    makeNode("consumer"),
  ]
  const parentEdges: PipelineEdge[] = bound
    ? [{ id: "consumer", source: "pricing", sourceHandle: "out__premium", target: "consumer" }]
    : []
  const view = buildSubmodelViewGraph({
    submodelName: "pricing",
    instanceId: "pricing",
    definition,
    childNodes: children,
    childEdges: internal ? [INTERNAL] : [],
    parentNodes,
    parentEdges,
  })
  const viewNodes = view.nodes
  const viewEdges = view.edges as PipelineEdge[]
  return {
    submodelName: "pricing",
    instanceId: "pricing",
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
  it("chooses an instance input by name, sole unbound input, or explicit pick, never position", () => {
    const definition = state().submodels.definition_pricing as SubmodelDefinition
    const ports = ["policy", "rates", "claims"].map((name) => ({ name, targets: [] }))
    const target = makeNode("copy", "submodel", { data: { label: "copy", config: { definition_id: definition.definitionId, alias: "copy", instance_of: "pricing" } } })
    const incoming = (name: string): PipelineEdge => ({ id: name, source: name, target: "copy", targetHandle: `in__${name}` })
    expect(chooseInstanceInput(target, ports, [], "rates")).toEqual({ name: "rates" })
    expect(() => chooseInstanceInput(target, ports, [incoming("rates")], "rates")).toThrow(/rates.*already bound/)
    expect(chooseInstanceInput(target, ports, [incoming("rates"), incoming("claims")], "other")).toEqual({ name: "policy" })
    expect(chooseInstanceInput(target, ports, [incoming("policy"), incoming("rates")], "other")).toEqual({ name: "claims" })
    expect(chooseInstanceInput(target, ports, [incoming("rates")], "other")).toEqual({ choices: ["policy", "claims"] })
    expect(chooseInstanceInput(target, ports, [incoming("rates")], "other", "claims")).toEqual({ name: "claims" })
    expect(() => chooseInstanceInput(target, ports, ports.map((p) => incoming(p.name)), "other")).toThrow("Every input of copy is connected")
    expect(() => chooseInstanceInput(target, ports, [incoming("rates")], "other", "rates")).toThrow(/rates/)
  })

  it("retargets an occurrence connection and unbinds the displaced edge without removing its declaration", () => {
    const current = state()
    const definition = current.submodels.definition_pricing as SubmodelDefinition
    definition.inputPorts.push({ name: "rates", targets: [] })
    const root = { nodes: current.parentNodes, submodels: current.submodels, edges: [
      { id: "policy", source: "a", target: "pricing_2", targetHandle: "in__policy" },
      { id: "rates", source: "b", target: "pricing_2", targetHandle: "in__rates" },
    ] }
    const moved = retargetSubmodelInput(root, "pricing_2", "policy", "rates")
    // The moved connection is named by its new handle.
    expect(moved.edges).toEqual([{ ...root.edges[1], id: "e-b-pricing_2--in__policy", targetHandle: "in__policy" }])
    expect(moved.submodels).toBe(root.submodels)
    expect(root.edges).toHaveLength(2)
    expect(retargetSubmodelInput(moved, "pricing_2", "policy", null).edges).toEqual([])
    expect(retargetSubmodelInput(root, "pricing_2", "rates", "policy").edges)
      .toEqual([{ ...root.edges[0], id: "e-a-pricing_2--in__rates", targetHandle: "in__rates" }])
    expect(() => retargetSubmodelInput(root, "pricing_2", "missing", "rates")).toThrow(/missing/)
  })
  it("creates the first named public input from a parent connection", () => {
    const current = state()
    const definition = current.submodels.definition_pricing as SubmodelDefinition
    const source = makeNode("upstream", "polars", {
      data: { _defaultInputName: "incoming_frame" },
    })
    const root = {
      nodes: [source, ...current.parentNodes],
      edges: [] as PipelineEdge[],
      submodels: {
        ...current.submodels,
        definition_pricing: {
          ...definition,
          inputPorts: [],
        },
      } as Record<string, unknown>,
    }

    const created = connectSubmodelInputFromParentConnection(root, {
      source: source.id,
      sourceHandle: null,
      target: "pricing",
      targetHandle: SUBMODEL_INPUT_HANDLE,
    })

    expect(created).not.toBeNull()
    expect(created?.name).toBe("incoming_frame")
    expect(created?.submodels.definition_pricing).toMatchObject({
      inputPorts: [{
        name: "incoming_frame",
        targets: [],
      }],
    })
    expect(created?.edges).toEqual([expect.objectContaining({
      source: source.id,
      sourceHandle: null,
      target: "pricing",
      targetHandle: "in__incoming_frame",
      data: { _inputName: "incoming_frame" },
    })])

    const createdDefinition = created!.submodels.definition_pricing as SubmodelDefinition
    const drilled = buildSubmodelViewGraph({
      submodelName: "pricing",
      instanceId: "pricing",
      definition: createdDefinition,
      childNodes: createdDefinition.graph.nodes,
      childEdges: createdDefinition.graph.edges,
      parentNodes: created!.nodes,
      parentEdges: created!.edges,
    })
    expect((boundary(drilled.nodes, "input").data as SubmodelPortData).ports)
      .toEqual([{ id: "incoming_frame", label: "incoming_frame" }])
    expect(drilled.edges.some((edge) => edge.source === boundary(drilled.nodes, "input").id))
      .toBe(false)
  })

  it("mints public ids from sanitised frame labels", () => {
    const current = state()
    const definition = current.submodels.definition_pricing as SubmodelDefinition
    const source = makeNode("upstream", "polars", {
      data: { _defaultInputName: "incoming_frame" },
    })

    const created = connectSubmodelInputFromParentConnection({
      nodes: [source, ...current.parentNodes],
      edges: [],
      submodels: {
        ...current.submodels,
        definition_pricing: definition,
      },
    }, {
      source: source.id,
      sourceHandle: null,
      target: "pricing",
      targetHandle: SUBMODEL_INPUT_HANDLE,
    })

    expect(created?.name).toBe("incoming_frame")
  })

  it("skips an input id already occupied by an output port", () => {
    const current = state()
    const definition = current.submodels.definition_pricing as SubmodelDefinition
    const source = makeNode("upstream", "polars", {
      data: { _defaultInputName: "incoming_frame" },
    })

    const created = connectSubmodelInputFromParentConnection({
      nodes: [source, ...current.parentNodes],
      edges: [],
      submodels: {
        ...current.submodels,
        definition_pricing: {
          ...definition,
          inputPorts: [],
          outputPorts: [{ ...definition.outputPorts[0], name: "incoming_frame" }],
        },
      },
    }, {
      source: source.id,
      sourceHandle: null,
      target: "pricing",
      targetHandle: SUBMODEL_INPUT_HANDLE,
    })

    expect(created?.name).toBe("incoming_frame_2")
  })

  it("binds an existing named port through the same socket on a copy", () => {
    const current = state()
    const source = makeNode("upstream", "polars", {
      data: { _defaultInputName: "policy" },
    })
    const parentNodes = current.parentNodes.map((node) => node.id === "pricing_2" ? {
      ...node,
      data: {
        ...node.data,
        config: {
          ...(node.data.config as Record<string, unknown>),
          instance_of: "pricing",
        },
      },
    } : node)
    const result = connectSubmodelInputFromParentConnection({
      nodes: [source, ...parentNodes],
      edges: [],
      submodels: current.submodels,
    }, {
      source: source.id,
      sourceHandle: null,
      target: "pricing_2",
      targetHandle: SUBMODEL_INPUT_HANDLE,
    })

    expect(result?.name).toBe("policy")
    expect(result?.submodels).toBe(current.submodels)
    expect(result?.edges).toEqual([expect.objectContaining({
      source: source.id,
      target: "pricing_2",
      targetHandle: "in__policy",
      data: { _inputName: "policy" },
    })])
  })

  it("binds differently named frames to the sole unbound copy input and rejects duplicates", () => {
    const current = state()
    const unseenSource = makeNode("unseen", "polars", {
      data: { _defaultInputName: "unseen_frame" },
    })
    const matchingSource = makeNode("matching", "polars", {
      data: { _defaultInputName: "policy" },
    })
    const parentNodes = current.parentNodes.map((node) => node.id === "pricing_2" ? {
      ...node,
      data: {
        ...node.data,
        config: {
          ...(node.data.config as Record<string, unknown>),
          instance_of: "pricing",
        },
      },
    } : node)
    const root = {
      nodes: [unseenSource, matchingSource, ...parentNodes],
      edges: [{
        id: "existing-policy-binding",
        source: matchingSource.id,
        target: "pricing",
        targetHandle: "in__policy",
        data: { _inputName: "policy" },
      }] as PipelineEdge[],
      submodels: current.submodels,
    }
    expect(connectSubmodelInputFromParentConnection(root, {
      source: unseenSource.id,
      sourceHandle: null,
      target: "pricing_2",
      targetHandle: SUBMODEL_INPUT_HANDLE,
    })?.edges.at(-1)).toMatchObject({ target: "pricing_2", targetHandle: "in__policy" })
    expect(() => connectSubmodelInputFromParentConnection(root, {
      source: matchingSource.id,
      sourceHandle: null,
      target: "pricing",
      targetHandle: SUBMODEL_INPUT_HANDLE,
    })).toThrow(/policy.*already bound/i)
  })

  it("fails closed when the reserved generic handle targets anything else", () => {
    const current = state()
    const source = makeNode("upstream", "polars", {
      data: { _defaultInputName: "incoming_frame" },
    })
    expect(() => connectSubmodelInputFromParentConnection({
      nodes: [source, ...current.parentNodes],
      edges: [],
      submodels: current.submodels,
    }, {
      source: source.id,
      sourceHandle: null,
      target: "missing-submodel",
      targetHandle: SUBMODEL_INPUT_HANDLE,
    })).toThrow(/generic submodel input handle.*submodel/i)
  })

  it("adds a target without changing occurrences, naming the new frame by its port", () => {
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
        name: "policy",
        targets: [
          { nodeId: "prepare", handleId: null },
          { nodeId: "score", handleId: "joined" },
        ],
      }],
    })
    expect(result.parentNodes.map((node) => node.data.config)).toEqual(
      current.parentNodes.map((node) => node.data.config),
    )
    const nextInput = boundary(result.viewNodes, "input")
    expect(nextInput.data).toMatchObject({
      _functionName: "INPUT",
      _defaultInputName: null,
      _sourceHandleInputNames: { policy: "policy" },
    })
    expect(nextInput.data).not.toHaveProperty("_configReference")
    const added = result.viewEdges.find((edge) => edge.target === "score" && edge.source === nextInput.id)!
    expect(added.data?._inputName).toBe("policy")
    expect(edgeInputName(
      added,
      nextInput as unknown as Parameters<typeof edgeInputName>[1],
    )).toBe("policy")
    expect(result.viewEdges.every((edge) => typeof edge.data?._inputName === "string")).toBe(true)
  })

  it("filters authoritative input identities when the final target removes a public port", () => {
    const current = state()
    const input = boundary(current.viewNodes, "input")
    const declaration = current.viewEdges.find((edge) => edge.source === input.id)!

    const result = removeSubmodelBoundaryEdges(current, [declaration.id])!

    const definition = result.submodels.definition_pricing as SubmodelDefinition
    expect(definition.inputPorts).toEqual([])
    expect(boundary(result.viewNodes, "input").data._sourceHandleInputNames).toEqual({})
  })

  it("explicitly retires a public input and every occurrence binding", () => {
    const current = state()
    const parentNodes = current.parentNodes.map((node) => node.id === "pricing_2" ? {
      ...node,
      data: {
        ...node.data,
        config: {
          ...(node.data.config as Record<string, unknown>),
          instance_of: "pricing",
        },
      },
    } : node)
    const primaryBinding: PipelineEdge = {
      id: "primary-policy-binding",
      source: "consumer",
      target: "pricing",
      targetHandle: "in__policy",
      data: { _inputName: "policy_input" },
    }
    const secondaryBinding: PipelineEdge = {
      id: "secondary-policy-binding",
      source: "consumer",
      target: "pricing_2",
      targetHandle: "in__policy",
      data: { _inputName: "policy_input" },
    }
    const unrelatedOutput: PipelineEdge = {
      id: "unrelated-output",
      source: "pricing",
      sourceHandle: "out__premium",
      target: "consumer",
      targetHandle: "in__policy",
      data: { _inputName: "premium_input" },
    }

    const result = removeSubmodelInputPort({
      ...current,
      parentNodes,
      parentEdges: [primaryBinding, secondaryBinding, unrelatedOutput],
    }, "policy")!

    const definition = result.submodels.definition_pricing as SubmodelDefinition
    expect(definition.inputPorts).toEqual([])
    expect(definition.graph).toEqual(
      (current.submodels.definition_pricing as SubmodelDefinition).graph,
    )
    expect(definition.outputPorts).toEqual(
      (current.submodels.definition_pricing as SubmodelDefinition).outputPorts,
    )
    expect(result.parentEdges).toEqual([unrelatedOutput])
    expect((boundary(result.viewNodes, "input").data as SubmodelPortData).ports).toEqual([])
    expect(result.viewEdges.some((edge) => {
      const info = (edge.data as { submodelBoundary?: { direction?: string } } | undefined)
        ?.submodelBoundary
      return info?.direction === "input"
    })).toBe(false)
  })

  it("explicitly retires an unrouted public input with no synthetic edge", () => {
    const current = state()
    const input = boundary(current.viewNodes, "input")
    const definition = current.submodels.definition_pricing as SubmodelDefinition
    const unroutedDefinition: SubmodelDefinition = {
      ...definition,
      inputPorts: definition.inputPorts.map((port) => ({ ...port, targets: [] })),
    }

    const result = removeSubmodelInputPort({
      ...current,
      viewEdges: current.viewEdges.filter((edge) => edge.source !== input.id),
      submodels: { ...current.submodels, definition_pricing: unroutedDefinition },
    }, "policy")!

    expect((result.submodels.definition_pricing as SubmodelDefinition).inputPorts).toEqual([])
    expect((boundary(result.viewNodes, "input").data as SubmodelPortData).ports).toEqual([])
  })

  it("keeps ordinary boundary deletion guarded when a public input is bound", () => {
    const current = state()
    const input = boundary(current.viewNodes, "input")
    const route = current.viewEdges.find((edge) => edge.source === input.id)!
    const binding: PipelineEdge = {
      id: "policy-binding",
      source: "consumer",
      target: "pricing",
      targetHandle: "in__policy",
      data: { _inputName: "policy_input" },
    }
    const boundState: SubmodelBoundaryEditState = {
      ...current,
      viewNodes: current.viewNodes.map((node) => node.id === input.id ? {
        ...node,
        data: {
          ...node.data,
          ports: (node.data as unknown as SubmodelPortData).ports.map((port) => ({
            ...port,
            parentEdges: [binding],
          })),
        },
      } : node),
      parentEdges: [binding],
    }

    expect(() => removeSubmodelBoundaryEdges(boundState, [route.id]))
      .toThrow(/Pricing.*policy/i)
  })

  it("blocks deletion of a used public output", () => { const current = state(true); const output = boundary(current.viewNodes, "output"); const declaration = current.viewEdges.find((edge) => edge.target === output.id)!; expect(() => removeSubmodelBoundaryEdges(current, [declaration.id])).toThrow(/Pricing.*premium/i) })
  it("removes an unbound public output from the shared definition", () => { const current = state(); const output = boundary(current.viewNodes, "output"); const declaration = current.viewEdges.find((edge) => edge.target === output.id)!; expect(removeSubmodelBoundaryEdges(current, [declaration.id])?.submodels.definition_pricing).toMatchObject({ outputPorts: [] }) })

  it("removes a child with its routes, the outputs it sources and its edges, in one command", () => {
    const current = state(false, true)
    const result = removeSubmodelElements(current, { nodeIds: new Set(["score"]) })
    const definition = result.submodels.definition_pricing as SubmodelDefinition
    expect(definition.graph.nodes.map((node) => node.id)).toEqual(["prepare"])
    expect(definition.graph.edges).toEqual([])
    expect(definition.outputPorts).toEqual([])
    expect(definition.inputPorts).toEqual([{ name: "policy", targets: [{ nodeId: "prepare", handleId: null }] }])
    expect(result.viewEdges.some((edge) => edge.source === "score" || edge.target === "score")).toBe(false)
    expect(result.parentEdges).toBe(current.parentEdges)
  })

  it("removes an internal edge and a boundary edge of one batch together", () => {
    const current = state(false, true)
    const declaration = current.viewEdges.find((edge) => edge.target === boundary(current.viewNodes, "output").id)!
    const result = removeSubmodelElements(current, { edgeIds: new Set([INTERNAL.id, declaration.id]) })
    const definition = result.submodels.definition_pricing as SubmodelDefinition
    expect(definition.graph.edges).toEqual([])
    expect(definition.outputPorts).toEqual([])
    expect(definition.graph.nodes.map((node) => node.id)).toEqual(["prepare", "score"])
  })

  it("retires an input whose last route goes and keeps one never routed", () => {
    const current = state()
    const definition = current.submodels.definition_pricing as SubmodelDefinition
    current.submodels = {
      definition_pricing: { ...definition, inputPorts: [...definition.inputPorts, { name: "claims", targets: [] }] },
    }
    const route = current.viewEdges.find((edge) => edge.source === boundary(current.viewNodes, "input").id)!
    const result = removeSubmodelBoundaryEdges(current, [route.id])!
    expect((result.submodels.definition_pricing as SubmodelDefinition).inputPorts)
      .toEqual([{ name: "claims", targets: [] }])
  })

  it("changes nothing for a route or an output the definition already has", () => {
    const current = state()
    expect(applySubmodelBoundaryConnection(current, {
      source: boundary(current.viewNodes, "input").id, sourceHandle: "policy", target: "prepare", targetHandle: null,
    })).toBeNull()
    expect(applySubmodelBoundaryConnection(current, {
      source: "score", sourceHandle: "result", target: boundary(current.viewNodes, "output").id, targetHandle: null,
    })).toBeNull()
  })

  it("refuses a route from an undeclared input or to anything but a child, saying why", () => {
    const current = state()
    const input = boundary(current.viewNodes, "input").id
    expect(() => applySubmodelBoundaryConnection(current, {
      source: input, sourceHandle: "claims", target: "prepare", targetHandle: null,
    })).toThrow('The submodel has no input "claims"')
    expect(() => applySubmodelBoundaryConnection(current, {
      source: input, sourceHandle: "policy", target: boundary(current.viewNodes, "output").id, targetHandle: null,
    })).toThrow("An input can only be routed to a node inside the submodel")
  })

  it("keeps both cards where the user put them", () => {
    const current = state()
    const moved = { x: -480, y: 17 }
    current.viewNodes = current.viewNodes.map((node) => (node.type === "submodelPort" ? { ...node, position: moved } : node))
    const result = applySubmodelBoundaryConnection(current, {
      source: boundary(current.viewNodes, "input").id, sourceHandle: "policy", target: "score", targetHandle: null,
    })!
    expect(boundary(result.viewNodes, "input").position).toEqual(moved)
    expect(boundary(result.viewNodes, "output").position).toEqual(moved)
  })

  it("mints output port names from child executable name and appends _2 on collision", () => {
    const current = state()
    const prepareNode = current.viewNodes.find((n) => n.id === "prepare")!
    prepareNode.data = {
      ...prepareNode.data,
      _sourceHandleInputNames: { extra: "prepare_input" },
    }
    const output = boundary(current.viewNodes, "output")
    const firstResult = applySubmodelBoundaryConnection(current, {
      source: "prepare",
      sourceHandle: null,
      target: output.id,
      targetHandle: null,
    } as Connection)!

    const firstDefinition = firstResult.submodels.definition_pricing as SubmodelDefinition
    expect(firstDefinition.outputPorts.map((p) => p.name)).toEqual(["premium", "prepare_input"])

    const secondResult = applySubmodelBoundaryConnection(firstResult, {
      source: "prepare",
      sourceHandle: "extra",
      target: boundary(firstResult.viewNodes, "output").id,
      targetHandle: null,
    } as Connection)!

    const secondDefinition = secondResult.submodels.definition_pricing as SubmodelDefinition
    expect(secondDefinition.outputPorts.map((p) => p.name)).toEqual([
      "premium",
      "prepare_input",
      "prepare_input_2",
    ])
  })
})
