import type { Edge, Node } from "@xyflow/react"
import { describe, expect, it, vi } from "vitest"

import type {
  EditorIdentityBatchResponse,
  EditorIdentityRequestNode,
} from "../../api/types"
import type { SubmodelDefinition } from "../../types/node"
import {
  applyEditorIdentityResponse,
  attachEditorEdgeIdentities,
  buildEditorIdentityRequest,
  resolveCanonicalGraphIdentities,
  resolveEditorGraphIdentities,
} from "../editorIdentities"

const RESERVED = new Set(["class", "for"])

function node(
  id: string,
  label: string,
  nodeType: string,
  config: Record<string, unknown> = {},
): Node {
  return { id, type: nodeType, position: { x: 0, y: 0 }, data: { label, nodeType, config } }
}

describe("editor identity resolution", () => {
  it("builds a strict batch with occurrence name and public submodel port handles", () => {
    const api = node("api", "class", "apiInput", {
      tables: [
        {
          path: "$[:].quotes",
          label: "quotes",
          emit: true,
          columns: [{ name: "id", selected: true }],
        },
        {
          path: "$[:].class",
          label: "class",
          emit: true,
          columns: [{ name: "id", selected: true }],
        },
      ],
    })
    const occurrence = node("pricing", "Tarif café", "submodel", {
      definitionId: "pricing-definition",
      alias: "pricing_secondary",
    })
    const definition: SubmodelDefinition = {
      definitionId: "pricing-definition",
      file: "modules/pricing.py",
      graph: { nodes: [], edges: [] },
      inputPorts: [],
      outputPorts: [{
        name: "written-premium",
        source: { nodeId: "result", handleId: null },
      }],
    }

    expect(buildEditorIdentityRequest(
      [api, node("ordinary", "Polars", "polars"), occurrence],
      { "pricing-definition": definition },
      RESERVED,
    )).toEqual({
      nodes: [
        {
          node_id: "api",
          label: "class",
          node_type: "apiInput",
          source_handles: ["quotes"],
        },
        {
          node_id: "ordinary",
          label: "Polars",
          node_type: "polars",
          source_handles: [],
        },
        {
          node_id: "pricing",
          label: "Tarif café",
          node_type: "submodel",
          source_handles: ["out__written-premium"],
          alias: "pricing_secondary",
        },
      ],
    })
  })

  it("attaches server identities immutably and uses them for every edge", () => {
    const originalNodes = [
      node("ordinary", "class café", "polars"),
      node("api", "Quotes", "apiInput"),
    ]
    const response: EditorIdentityBatchResponse = {
      identities: [
        {
          node_id: "ordinary",
          function_name: "node_class_cafe",
          config_reference: "config/polars/node_class_cafe.json",
          default_input_name: "node_class_cafe",
          source_handle_input_names: {},
        },
        {
          node_id: "api",
          function_name: "quotes",
          config_reference: "config/quote_input/quotes.json",
          default_input_name: null,
          source_handle_input_names: { vehicles: "vehicles" },
        },
      ],
    }
    const resolvedNodes = applyEditorIdentityResponse(originalNodes, response)
    expect(resolvedNodes).not.toBe(originalNodes)
    expect(originalNodes[0].data).not.toHaveProperty("_functionName")
    expect(resolvedNodes[0].data).toMatchObject({
      _functionName: "node_class_cafe",
      _defaultInputName: "node_class_cafe",
      _configReference: "config/polars/node_class_cafe.json",
      _sourceHandleInputNames: {},
    })

    const edges: Edge[] = [
      { id: "ordinary-edge", source: "ordinary", target: "target" },
      { id: "api-edge", source: "api", target: "target", sourceHandle: "vehicles" },
    ]
    const resolvedEdges = attachEditorEdgeIdentities(edges, resolvedNodes)
    expect(resolvedEdges.map((edge) => edge.data?._inputName)).toEqual([
      "node_class_cafe",
      "vehicles",
    ])
    expect(edges.every((edge) => edge.data === undefined)).toBe(true)
  })

  it("rejects an executable edge whose source identity is absent", () => {
    const api = {
      ...node("api", "Quotes", "apiInput"),
      data: { label: "Quotes", nodeType: "apiInput", _sourceHandleInputNames: {} },
    }
    expect(() => attachEditorEdgeIdentities(
      [{ id: "edge", source: "api", target: "target", sourceHandle: "stale" }],
      [api],
    )).toThrow(/edge.*stale.*authoritative/i)
  })

  it("resolves a graph as one immutable operation", async () => {
    const nodes = [node("source", "class", "polars")]
    const edges = [{ id: "edge", source: "source", target: "target" }]
    const resolve = vi.fn(async (): Promise<EditorIdentityBatchResponse> => ({
      identities: [{
        node_id: "source",
        function_name: "node_class",
        config_reference: null,
        default_input_name: "node_class",
        source_handle_input_names: {},
      }],
    }))

    const result = await resolveEditorGraphIdentities({
      nodes,
      edges,
      submodels: {},
      reservedApiInputFrameLabels: RESERVED,
      resolve,
    })
    expect(resolve).toHaveBeenCalledOnce()
    expect(result.nodes[0].data._functionName).toBe("node_class")
    expect(result.edges[0].data?._inputName).toBe("node_class")
  })

  it("resolves root and canonical definition scopes without retaining the boundary node", async () => {
    const child = node("__submodel_input_ports__", "Child", "polars")
    const definition: SubmodelDefinition = {
      definitionId: "pricing", file: "modules/pricing.py",
      graph: {
        nodes: [child], edges: [{ id: "child-edge", source: child.id, target: "sink" }],
        pipeline_name: "Pricing child", pipeline_description: null,
        preamble: "from haute import submodel", source_file: "modules/pricing.py",
        preserved_blocks: ["# keep this"],
      },
      inputPorts: [{ name: "policy", targets: [{ nodeId: child.id, handleId: null }] }],
      outputPorts: [],
    }
    const root = node("instance", "Pricing", "submodel", { definitionId: "pricing", alias: "pricing" })
    const resolve = vi.fn(async (request): Promise<EditorIdentityBatchResponse> => ({
      identities: request.nodes.map((requestNode: EditorIdentityRequestNode) => ({
        node_id: requestNode.node_id,
        function_name: `fn_${requestNode.node_id}`,
        config_reference: null,
        default_input_name: `in_${requestNode.node_id}`,
        source_handle_input_names: requestNode.node_type === "submodelPort" ? { policy: "policy_input" } : {},
      })),
    }))

    const result = await resolveCanonicalGraphIdentities({
      nodes: [root], edges: [], submodels: { pricing: definition },
      reservedApiInputFrameLabels: RESERVED, resolve,
    })

    expect(resolve).toHaveBeenCalledTimes(2)
    expect(resolve.mock.calls.map(([request]) => request.nodes.map(
      (item: EditorIdentityRequestNode) => item.node_id,
    ))).toEqual([
      ["instance"], ["__submodel_input_ports__", "__submodel_input_ports___1"],
    ])
    expect(resolve.mock.calls[1]?.[0].nodes.at(-1)).toMatchObject({
      node_type: "submodelPort",
      source_handles: ["policy"],
    })
    expect(result.nodes[0].data._functionName).toBe("fn_instance")
    expect(result.submodels.pricing.graph.nodes[0].data._functionName).toBe("fn___submodel_input_ports__")
    expect(result.submodels.pricing.graph.edges[0].data?._inputName).toBe("in___submodel_input_ports__")
    expect(result.submodels.pricing.graph.nodes).toHaveLength(1)
    expect(result.submodels.pricing.graph).toMatchObject({
      pipeline_name: "Pricing child", pipeline_description: null,
      preamble: "from haute import submodel", source_file: "modules/pricing.py",
      preserved_blocks: ["# keep this"],
    })
    expect(result.submodels.pricing.graph).not.toHaveProperty("warning")
    expect(result.submodels.pricing.graph).not.toBe(definition.graph)
    expect(definition.graph.nodes[0].data).not.toHaveProperty("_functionName")
    expect(definition.graph).toEqual({
      nodes: [child], edges: [{ id: "child-edge", source: child.id, target: "sink" }],
      pipeline_name: "Pricing child", pipeline_description: null,
      preamble: "from haute import submodel", source_file: "modules/pricing.py",
      preserved_blocks: ["# keep this"],
    })
  })

  it("rejects a submodel node without alias", () => {
    const occurrence = node("pricing", "Tarif café", "submodel", {
      definitionId: "pricing-definition",
    })
    const definition: SubmodelDefinition = {
      definitionId: "pricing-definition",
      file: "modules/pricing.py",
      graph: { nodes: [], edges: [] },
      inputPorts: [],
      outputPorts: [],
    }
    expect(() =>
      buildEditorIdentityRequest(
        [occurrence],
        { "pricing-definition": definition },
        RESERVED,
      ),
    ).toThrow("Cannot resolve editor identity for submodel pricing: malformed occurrence")
  })
})
