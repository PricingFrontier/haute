/**
 * Tests for buildGraph.ts — the request payload of the graph store's document.
 *
 * Tests cover:
 * 1. documentGraphPayload: the pipeline's own graph, also with a submodel open,
 *    canonical: editor identities and volatile metadata stripped, data kept
 * 2. graphForRequestIdentity: the fields that identify a request
 * 3. global constants carried from the store
 */
import { afterEach, describe, it, expect } from "vitest"
import { documentGraphPayload, graphForRequestIdentity } from "../buildGraph"
import { makeIdentifiedNode, makeNode, makeSimpleEdge } from "../../test-utils/factories"
import type { GraphPayload } from "../../api/types"
import type { PipelineEdge, SubmodelDefinition } from "../../types/node"
import type { Node } from "@xyflow/react"
import useGraphStore from "../../stores/useGraphStore"

const loadDocument = (nodes: Node[], edges: PipelineEdge[] = [], submodels: Record<string, unknown> = {}) =>
  useGraphStore.getState().loadGraphSnapshot({ nodes, edges, preamble: "", submodels })

describe("the request payload", () => {
  afterEach(() => {
    useGraphStore.getState().resetForTests()
  })

  it("keeps each node's id, type and data, as copies", () => {
    const node = makeNode("n1", "polars", { data: { label: "Rate", nodeType: "polars", config: { sql: "SELECT * FROM t", output_columns: ["a", "b"] }, description: "Important step" } })
    loadDocument([node], [makeSimpleEdge("e1", "n1", "n2") as unknown as PipelineEdge])

    const result = documentGraphPayload()

    expect(result.nodes.map((n) => [n.id, n.type])).toEqual([["n1", "polars"]])
    expect(result.nodes[0].data.config).toEqual({ sql: "SELECT * FROM t", output_columns: ["a", "b"] })
    expect(result.nodes[0].data.description).toBe("Important step")
    expect(result.nodes[0].data).not.toBe(useGraphStore.getState().nodes[0].data)
    expect(result.edges.map((edge) => edge.id)).toEqual(["e1"])
  })

  it("keeps every submodel definition, as a copy", () => {
    const submodels = { sub1: { graph: { nodes: [], edges: [] } } }
    loadDocument([], [], submodels)

    const result = documentGraphPayload()

    expect(result.submodels).toEqual(submodels)
    expect(result.submodels).not.toBe(useGraphStore.getState().submodels)
  })

  it("carries the graph store's preamble, so no request can leave it out", () => {
    useGraphStore.getState().setPreambleRaw("import polars as pl")

    expect(documentGraphPayload().preamble).toBe("import polars as pl")
  })

  it("is a valid empty graph for an empty pipeline", () => {
    const result = documentGraphPayload()

    expect(result.nodes).toEqual([])
    expect(result.edges).toEqual([])
  })

  it("strips editor identities from root and nested graphs", () => {
    const node = makeNode("n1", "polars", { data: {
      label: "Node n1",
      nodeType: "polars",
      description: "",
      config: { _semanticOption: true },
      _functionName: "root_identity",
      _defaultInputName: "root_identity",
    } })
    const edges = [{ ...makeSimpleEdge("e1", "n1", "n2"), data: { _inputName: "root_identity" } }] as unknown as PipelineEdge[]
    loadDocument([node], edges, {
      pricing: {
        definitionId: "pricing",
        file: "modules/pricing.py",
        graph: {
          nodes: [{
            id: "child",
            type: "polars",
            position: { x: 0, y: 0 },
            data: { label: "Child", nodeType: "polars", _functionName: "child_identity" },
          }],
          edges: [],
        },
        inputPorts: [],
        outputPorts: [],
      },
    })

    const result = documentGraphPayload()

    expect(result.nodes[0].data).toEqual({
      label: "Node n1",
      description: "",
      nodeType: "polars",
      config: { _semanticOption: true },
    })
    expect(result.edges[0]).not.toHaveProperty("data")
    const definition = result.submodels?.pricing as Record<string, unknown>
    const child = (definition.graph as { nodes: Array<{ data: Record<string, unknown> }> }).nodes[0]
    expect(child.data).not.toHaveProperty("_functionName")
    expect(useGraphStore.getState().nodes[0].data).toHaveProperty("_functionName", "root_identity")
  })
})

describe("graphForRequestIdentity", () => {
  const volatileNodeData = {
    _columns: [{ name: "preview", dtype: "Int64" }],
    _availableColumns: [{ name: "available", dtype: "Int64" }],
    _schemaWarnings: [{ column: "value", status: "stale" }],
    _columnsSource: "preview",
    _columnsStructuralVersion: 42,
    _status: "running",
    _traceActive: true,
    _traceDimmed: true,
    _hoverDimmed: true,
    _traceValue: { value: 1 },
    _traceMotionDisabled: true,
    _diffStatus: "changed",
  }

  it("removes volatile node metadata from top-level and nested submodel graphs", () => {
    const semanticData = {
      label: "Top",
      description: "",
      nodeType: "polars",
      config: { code: "pl.col('value')" },
    }
    const topNode = {
      id: "top",
      type: "polars",
      data: { ...semanticData, ...volatileNodeData },
    }
    const nestedNode = {
      id: "nested",
      type: "polars",
      data: {
        ...semanticData,
        label: "Nested",
        ...volatileNodeData,
      },
      position: { x: 12, y: 34 },
    }
    loadDocument([topNode as unknown as Node], [], {
      child: {
        label: "Child",
        graph: {
          nodes: [nestedNode],
          edges: [],
        },
      },
    })
    useGraphStore.getState().setPreambleRaw("import polars as pl")
    const graph = documentGraphPayload()

    const identity = graphForRequestIdentity(graph)
    const identityNodes = identity.nodes as Array<{ data: Record<string, unknown> }>
    const child = (identity.submodels as Record<string, Record<string, unknown>>).child
    const childGraph = child.graph as Record<string, unknown>
    const childNodes = childGraph.nodes as Array<{ data: Record<string, unknown> }>

    expect(identityNodes[0].data).toEqual(semanticData)
    expect(childNodes[0].data).toEqual({ ...semanticData, label: "Nested" })
    expect(childGraph.edges).toEqual([])
    expect(identity.preamble).toBe("import polars as pl")
    expect(graph.nodes[0].data).not.toHaveProperty("_columns")
    expect(topNode.data._columns).toEqual(volatileNodeData._columns)
    expect(nestedNode.data._columns).toEqual(volatileNodeData._columns)
  })

  it("leaves out where a node sits on the canvas", () => {
    loadDocument([makeNode("n1", "polars", { position: { x: 10, y: 20 } })])
    const before = graphForRequestIdentity(documentGraphPayload())
    useGraphStore.getState().setNodes((nodes) => nodes.map((node) => ({ ...node, position: { x: 400, y: 300 } })))

    expect(graphForRequestIdentity(documentGraphPayload())).toEqual(before)
    expect((before.nodes as Array<Record<string, unknown>>)[0]).not.toHaveProperty("position")
  })

  it("preserves opaque legacy nodes and submodel definitions", () => {
    const opaqueNodes = [
      null,
      "legacy-node",
      [],
      { id: "missing-data" },
      { id: "array-data", data: [] },
    ]
    const opaqueSubmodels = {
      nullDefinition: null,
      stringDefinition: "legacy-submodel",
      arrayDefinition: [],
      missingGraph: { label: "Legacy" },
      arrayGraph: { graph: [] },
    }
    const graph = {
      nodes: opaqueNodes,
      edges: [],
      submodels: opaqueSubmodels,
      preamble: "",
    } as unknown as GraphPayload

    const identity = graphForRequestIdentity(graph)

    expect(identity.nodes).toEqual(opaqueNodes)
    expect(identity.submodels).toEqual(opaqueSubmodels)
  })

  it("passes through non-record submodel collections and non-array node collections", () => {
    for (const submodels of [null, "legacy-submodels", []]) {
      const graph = {
        nodes: null,
        edges: [],
        submodels,
      } as unknown as GraphPayload

      const identity = graphForRequestIdentity(graph)

      expect(identity.nodes).toBeNull()
      expect(identity.submodels).toBe(submodels)
    }
  })
})

describe("documentGraphPayload", () => {
  afterEach(() => {
    useGraphStore.getState().resetForTests()
  })

  function loadDocument() {
    const definition: SubmodelDefinition = {
      definitionId: "definition_pricing",
      file: "modules/pricing.py",
      graph: { nodes: [makeIdentifiedNode("score")], edges: [] },
      inputPorts: [{ name: "policy", targets: [{ nodeId: "score", handleId: null }] }],
      outputPorts: [{ name: "premium", source: { nodeId: "score", handleId: null } }],
    }
    useGraphStore.getState().loadGraphSnapshot({
      nodes: [
        makeIdentifiedNode("api"),
        makeNode("pricing", "submodel", {
          data: { label: "pricing", nodeType: "submodel", config: { definition_id: "definition_pricing", alias: "pricing" } },
        }),
      ],
      edges: [{ id: "feed", source: "api", target: "pricing", targetHandle: "in__policy" }],
      preamble: "import numpy",
      submodels: { definition_pricing: definition },
    })
  }

  it("sends the pipeline's own graph, with every definition and the preamble", () => {
    loadDocument()

    const graph = documentGraphPayload()

    expect(graph.nodes.map((node) => node.id)).toEqual(["api", "pricing"])
    expect(graph.edges.map((edge) => edge.id)).toEqual(["feed"])
    expect(Object.keys(graph.submodels ?? {})).toEqual(["definition_pricing"])
    expect(graph.preamble).toBe("import numpy")
  })

  it("sends the pipeline's own graph while a submodel is open, with an edit made inside it", () => {
    // Catches: a request from inside a submodel sending the open view (whose
    // nodes stand in the document twice) instead of the whole pipeline, or
    // the definition as it was before the edit just made.
    loadDocument()
    useGraphStore.getState().enterSubmodelView({ instanceId: "pricing", definitionId: "definition_pricing" })
    useGraphStore.getState().setNodes((nodes) => nodes.map((node) => (
      node.id === "score" ? { ...node, data: { ...node.data, config: { how: "left" } } } : node
    )))

    const graph = documentGraphPayload()

    expect(graph.nodes.map((node) => node.id)).toEqual(["api", "pricing"])
    const definition = graph.submodels?.definition_pricing as SubmodelDefinition
    expect(definition.graph.nodes.map((node) => node.data.config)).toEqual([{ how: "left" }])
  })
})

describe("global constants in built graphs", () => {
  afterEach(() => {
    useGraphStore.getState().resetForTests()
  })

  it("carries the store's valid constants without caller arguments", () => {
    useGraphStore.getState().setGlobalConstantsRaw([
      { name: "rate", type: "float", split: true, value: "", bySource: { live: "1.5", nb_batch: "2.5" } },
      { name: "broken", type: "integer", split: false, value: "x", bySource: {} },
    ])
    const graph = documentGraphPayload()
    expect(graph.global_constants).toEqual([
      { name: "rate", type: "float", by_source: { live: 1.5, nb_batch: 2.5 } },
    ])
    expect(graph.global_constants_error).toBeNull()
  })

  it("carry a load error and no constants while the file failed to load", () => {
    useGraphStore.getState().loadGraphSnapshot({
      nodes: [],
      edges: [],
      preamble: "",
      submodels: {},
      globalConstants: [{ name: "rate", type: "float", split: false, value: "1", bySource: {} }],
      globalConstantsError: "bad JSON",
    })

    const graph = documentGraphPayload()

    expect(graph.global_constants).toEqual([])
    expect(graph.global_constants_error).toBe("bad JSON")
  })
})
