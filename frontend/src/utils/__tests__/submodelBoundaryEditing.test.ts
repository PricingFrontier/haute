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
  removeSubmodelInputPort,
  type SubmodelBoundaryEditState,
} from "../submodelBoundaryEditing"
import { SUBMODEL_INPUT_HANDLE } from "../flowHandles"
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
          _inputPortInputNames: {},
        },
      } as Record<string, unknown>,
    }

    const created = connectSubmodelInputFromParentConnection(root, {
      source: source.id,
      sourceHandle: null,
      target: "instance_primary",
      targetHandle: SUBMODEL_INPUT_HANDLE,
    })

    expect(created).not.toBeNull()
    expect(created?.portId).toBe("incoming_frame")
    expect(created?.submodels.definition_pricing).toMatchObject({
      inputPorts: [{
        portId: "incoming_frame",
        label: "incoming_frame",
        targets: [],
      }],
      _inputPortInputNames: { incoming_frame: "incoming_frame" },
    })
    expect(created?.edges).toEqual([expect.objectContaining({
      source: source.id,
      sourceHandle: null,
      target: "instance_primary",
      targetHandle: "in__incoming_frame",
      data: { _inputName: "incoming_frame" },
    })])

    const createdDefinition = created!.submodels.definition_pricing as SubmodelDefinition
    const drilled = buildSubmodelViewGraph({
      submodelName: "pricing",
      instanceId: "instance_primary",
      definition: createdDefinition,
      childNodes: createdDefinition.graph.nodes,
      childEdges: createdDefinition.graph.edges,
      parentNodes: created!.nodes,
      parentEdges: created!.edges,
    })
    expect((boundary(drilled.nodes, "input").data as SubmodelPortData).ports)
      .toEqual([{ id: "incoming_frame", label: "incoming_frame", parentEdges: created!.edges }])
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
        definition_pricing: {
          ...definition,
          _inputPortInputNames: { policy: "policy" },
        },
      },
    }, {
      source: source.id,
      sourceHandle: null,
      target: "instance_primary",
      targetHandle: SUBMODEL_INPUT_HANDLE,
    })

    expect(created?.portId).toBe("incoming_frame")
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
          outputPorts: [{ ...definition.outputPorts[0], portId: "incoming_frame" }],
          _inputPortInputNames: {},
        },
      },
    }, {
      source: source.id,
      sourceHandle: null,
      target: "instance_primary",
      targetHandle: SUBMODEL_INPUT_HANDLE,
    })

    expect(created?.portId).toBe("incoming_frame_2")
  })

  it("binds an existing named port through the same socket on a copy", () => {
    const current = state()
    const source = makeNode("upstream", "polars", {
      data: { _defaultInputName: "policy_input" },
    })
    const parentNodes = current.parentNodes.map((node) => node.id === "instance_secondary" ? {
      ...node,
      data: {
        ...node.data,
        config: {
          ...(node.data.config as Record<string, unknown>),
          instanceOf: "instance_primary",
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
      target: "instance_secondary",
      targetHandle: SUBMODEL_INPUT_HANDLE,
    })

    expect(result?.portId).toBe("policy")
    expect(result?.submodels).toBe(current.submodels)
    expect(result?.edges).toEqual([expect.objectContaining({
      source: source.id,
      target: "instance_secondary",
      targetHandle: "in__policy",
      data: { _inputName: "policy_input" },
    })])
  })

  it("rejects new names on copies and duplicate occurrence bindings", () => {
    const current = state()
    const unseenSource = makeNode("unseen", "polars", {
      data: { _defaultInputName: "unseen_frame" },
    })
    const matchingSource = makeNode("matching", "polars", {
      data: { _defaultInputName: "policy_input" },
    })
    const parentNodes = current.parentNodes.map((node) => node.id === "instance_secondary" ? {
      ...node,
      data: {
        ...node.data,
        config: {
          ...(node.data.config as Record<string, unknown>),
          instanceOf: "instance_primary",
        },
      },
    } : node)
    const root = {
      nodes: [unseenSource, matchingSource, ...parentNodes],
      edges: [{
        id: "existing-policy-binding",
        source: matchingSource.id,
        target: "instance_primary",
        targetHandle: "in__policy",
        data: { _inputName: "policy_input" },
      }] as PipelineEdge[],
      submodels: current.submodels,
    }
    expect(() => connectSubmodelInputFromParentConnection(root, {
      source: unseenSource.id,
      sourceHandle: null,
      target: "instance_secondary",
      targetHandle: SUBMODEL_INPUT_HANDLE,
    })).toThrow(/owner/i)
    expect(() => connectSubmodelInputFromParentConnection(root, {
      source: matchingSource.id,
      sourceHandle: null,
      target: "instance_primary",
      targetHandle: SUBMODEL_INPUT_HANDLE,
    })).toThrow(/policy_input.*already bound/i)
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

  it("explicitly retires a public input and every occurrence binding", () => {
    const current = state()
    const parentNodes = current.parentNodes.map((node) => node.id === "instance_secondary" ? {
      ...node,
      data: {
        ...node.data,
        config: {
          ...(node.data.config as Record<string, unknown>),
          instanceOf: "instance_primary",
        },
      },
    } : node)
    const primaryBinding: PipelineEdge = {
      id: "primary-policy-binding",
      source: "consumer",
      target: "instance_primary",
      targetHandle: "in__policy",
      data: { _inputName: "policy_input" },
    }
    const secondaryBinding: PipelineEdge = {
      id: "secondary-policy-binding",
      source: "consumer",
      target: "instance_secondary",
      targetHandle: "in__policy",
      data: { _inputName: "policy_input" },
    }
    const unrelatedOutput: PipelineEdge = {
      id: "unrelated-output",
      source: "instance_primary",
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
    expect(definition._inputPortInputNames).toEqual({})
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
      target: "instance_primary",
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
      .toThrow(/Pricing.*Policy/s)
  })

  it("blocks deletion of a used public output", () => { const current = state(true); const output = boundary(current.viewNodes, "output"); const declaration = current.viewEdges.find((edge) => edge.target === output.id)!; expect(() => removeSubmodelBoundaryEdges(current, [declaration.id])).toThrow(/Pricing.*Premium/s) })
  it("removes an unbound public output from the shared definition", () => { const current = state(); const output = boundary(current.viewNodes, "output"); const declaration = current.viewEdges.find((edge) => edge.target === output.id)!; expect(removeSubmodelBoundaryEdges(current, [declaration.id])?.submodels.definition_pricing).toMatchObject({ outputPorts: [] }) })
})
