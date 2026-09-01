import type { Edge, Node } from "@xyflow/react"
import { describe, expect, it, vi } from "vitest"

import type { EditorIdentityBatchResponse } from "../../api/types"
import type { SubmodelDefinition } from "../../types/node"
import {
  applyEditorIdentityResponse,
  attachEditorEdgeIdentities,
  buildEditorIdentityRequest,
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
  it("builds a strict batch from authored labels and structural source handles", () => {
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
        portId: "written-premium",
        label: "Written premium",
        source: { nodeId: "result", handleId: null },
      }],
    }

    expect(buildEditorIdentityRequest(
      [api, occurrence],
      { "pricing-definition": definition },
      RESERVED,
    )).toEqual({
      nodes: [
        {
          node_id: "api",
          label: "class",
          node_type: "apiInput",
          submodel_alias: null,
          source_handles: ["quotes"],
        },
        {
          node_id: "pricing",
          label: "Tarif café",
          node_type: "submodel",
          submodel_alias: "pricing_secondary",
          source_handles: ["out__written-premium"],
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
})
