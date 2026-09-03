/**
 * Tests for buildGraph.ts — graph serialization utility.
 *
 * Tests cover:
 * 1. buildGraph: basic serialization with correct shape
 * 2. buildGraph: node type fallback (type || data.nodeType)
 * 3. buildGraph: position is always zeroed out
 * 4. buildGraph: optional submodels and preamble
 * 5. resolveGraphFromRefs: uses parentGraphRef when available
 * 6. resolveGraphFromRefs: falls back to graphRef when parentGraphRef is null
 * 7. buildGraph: empty inputs
 */
import { describe, it, expect } from "vitest"
import type { Node, Edge } from "@xyflow/react"
import { buildGraph, graphForRequestIdentity, resolveGraphFromRefs } from "../buildGraph"
import { makeSimpleNode, makeSimpleEdge } from "../../test-utils/factories"

describe("buildGraph", () => {
  it("serializes nodes with id, type, data, and zeroed position", () => {
    // Catches: if position is taken from the input node instead of
    // being zeroed, the backend would receive UI coordinates that
    // could interfere with pipeline serialization.
    const nodes = [makeSimpleNode("n1", "polars", { type: "polars" })]
    const edges = [makeSimpleEdge("e1", "n1", "n2")]

    const result = buildGraph(nodes, edges)

    expect(result.nodes).toHaveLength(1)
    expect(result.nodes[0].id).toBe("n1")
    expect(result.nodes[0].type).toBe("polars")
    expect(result.nodes[0].position).toEqual({ x: 0, y: 0 })
    expect(result.nodes[0].data).toEqual(nodes[0].data)
    expect(result.nodes[0].data).not.toBe(nodes[0].data)
    expect(result.edges).toEqual(edges)
    expect(result.edges).not.toBe(edges)
  })

  it("falls back to data.nodeType when node.type is undefined", () => {
    // Catches: if the fallback `n.type || n.data.nodeType` is changed
    // to just `n.type`, nodes without an explicit type field would be
    // serialized with type=undefined, causing the backend to reject them.
    const nodes = [makeSimpleNode("n1", "dataInput")]
    // makeSimpleNode doesn't set .type by default, only data.nodeType

    const result = buildGraph(nodes, [])

    expect(result.nodes[0].type).toBe("dataInput")
  })

  it("uses explicit node.type over data.nodeType", () => {
    // Catches: if the precedence is reversed, the explicit type set
    // by the frontend would be overridden by the default nodeType.
    const nodes = [makeSimpleNode("n1", "polars", { type: "submodel" })]

    const result = buildGraph(nodes, [])

    expect(result.nodes[0].type).toBe("submodel")
  })

  it("preserves canonical submodels and preamble when provided", () => {
    // Catches: if these optional fields are accidentally dropped,
    // saving a pipeline with submodels or preamble would lose that data.
    const submodels = { sub1: { graph: { nodes: [], edges: [] } } }
    const preamble = "import polars as pl"

    const result = buildGraph([], [], submodels, preamble)

    expect(result.submodels).toEqual(submodels)
    expect(result.submodels).not.toBe(submodels)
    expect(result.preamble).toBe(preamble)
  })

  it("sets submodels and preamble to undefined when not provided", () => {
    // Catches: if defaults were accidentally set to empty objects/strings,
    // the backend might interpret them differently than "not provided".
    const result = buildGraph([], [])

    expect(result.submodels).toBeUndefined()
    expect(result.preamble).toBeUndefined()
  })

  it("handles empty node and edge arrays", () => {
    // Catches: edge case where buildGraph is called with no nodes
    // (e.g. new empty pipeline). Should return valid structure.
    const result = buildGraph([], [])

    expect(result.nodes).toEqual([])
    expect(result.edges).toEqual([])
  })

  it("preserves all data fields on each node", () => {
    // Catches: if buildGraph cherry-picked data fields instead of
    // passing the whole object, custom config would be lost on save.
    const nodes = [makeSimpleNode("n1", "polars", {
      config: { sql: "SELECT * FROM t", output_columns: ["a", "b"] },
      description: "Important step",
    })]

    const result = buildGraph(nodes, [])

    expect(result.nodes[0].data.config).toEqual({
      sql: "SELECT * FROM t",
      output_columns: ["a", "b"],
    })
    expect(result.nodes[0].data.description).toBe("Important step")
  })

  it("strips editor identities from root and nested graphs", () => {
    const nodes = [makeSimpleNode("n1", "polars", {
      config: { _semanticOption: true },
      _functionName: "root_identity",
      _defaultInputName: "root_identity",
    })]
    const edges = [{
      ...makeSimpleEdge("e1", "n1", "n2"),
      data: { _inputName: "root_identity" },
    }]
    const submodels = {
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
        _inputPortInputNames: {},
      },
    }

    const result = buildGraph(nodes, edges, submodels)

    expect(result.nodes[0].data).toEqual({
      label: "Node n1",
      description: "",
      nodeType: "polars",
      config: { _semanticOption: true },
    })
    expect(result.edges[0]).not.toHaveProperty("data")
    const definition = result.submodels?.pricing as Record<string, unknown>
    expect(definition).not.toHaveProperty("_inputPortInputNames")
    const child = (definition.graph as { nodes: Array<{ data: Record<string, unknown> }> }).nodes[0]
    expect(child.data).not.toHaveProperty("_functionName")
    expect(nodes[0].data).toHaveProperty("_functionName", "root_identity")
  })
})

describe("graphForRequestIdentity", () => {
  const volatileNodeData = {
    _columns: [{ name: "preview", dtype: "Int64" }],
    _availableColumns: [{ name: "available", dtype: "Int64" }],
    _schemaWarnings: [{ column: "value", status: "stale" }],
    _columnsSource: "preview",
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
    const graph = buildGraph(
      [topNode],
      [],
      {
        child: {
          label: "Child",
          graph: {
            nodes: [nestedNode],
            edges: [],
          },
        },
      },
      "import polars as pl",
    )

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
    } as unknown as ReturnType<typeof buildGraph>

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
      } as unknown as ReturnType<typeof buildGraph>

      const identity = graphForRequestIdentity(graph)

      expect(identity.nodes).toBeNull()
      expect(identity.submodels).toBe(submodels)
    }
  })
})

describe("resolveGraphFromRefs", () => {
  it("returns parentGraphRef contents when parentGraphRef is not null", () => {
    // Catches: when inside a submodel, the preview API needs the PARENT
    // graph (with submodel definitions). If resolveGraphFromRefs ignores
    // parentGraphRef, previewing inside submodels would send the wrong
    // graph and produce incorrect results.
    const parentNodes = [{ id: "p1" }] as Node[]
    const parentEdges = [{ id: "pe1", source: "p1", target: "p2" }] as Edge[]
    const parentSubmodels = { sub1: { nodes: [], edges: [] } }

    const graphRef = { current: { nodes: [] as Node[], edges: [] as Edge[] } }
    const parentGraphRef = {
      current: {
        nodes: parentNodes,
        edges: parentEdges,
        submodels: parentSubmodels,
      },
    }
    const submodelsRef = { current: {} }
    const preambleRef = { current: "import numpy" }

    const result = resolveGraphFromRefs(graphRef, parentGraphRef, submodelsRef, preambleRef)

    expect(result.nodes).toEqual(parentNodes)
    expect(result.nodes).not.toBe(parentNodes)
    expect(result.edges).toEqual(parentEdges)
    expect(result.edges).not.toBe(parentEdges)
    expect(result.submodels).toEqual(parentSubmodels)
    expect(result.submodels).not.toBe(parentSubmodels)
    expect(result.preamble).toBe("import numpy")
  })

  it("falls back to graphRef when parentGraphRef.current is null", () => {
    // Catches: when at the top level (not inside a submodel),
    // parentGraphRef is null, and the regular graphRef should be used.
    const nodes = [{ id: "n1" }] as Node[]
    const edges = [{ id: "e1", source: "n1", target: "n2" }] as Edge[]
    const submodels = { s: {} }

    const graphRef = { current: { nodes, edges } }
    const parentGraphRef = { current: null }
    const submodelsRef = { current: submodels }
    const preambleRef = { current: "# preamble" }

    const result = resolveGraphFromRefs(graphRef, parentGraphRef, submodelsRef, preambleRef)

    expect(result.nodes).toEqual(nodes)
    expect(result.nodes).not.toBe(nodes)
    expect(result.edges).toEqual(edges)
    expect(result.edges).not.toBe(edges)
    expect(result.submodels).toEqual(submodels)
    expect(result.submodels).not.toBe(submodels)
    expect(result.preamble).toBe("# preamble")
  })

  it("always uses preambleRef regardless of which graph ref is active", () => {
    // Catches: if preamble were accidentally sourced from parentGraphRef
    // (which doesn't have a preamble field), editing preamble while
    // inside a submodel would use stale data.
    const graphRef = { current: { nodes: [] as Node[], edges: [] as Edge[] } }
    const parentGraphRef = {
      current: {
        nodes: [] as Node[],
        edges: [] as Edge[],
        submodels: {},
      },
    }
    const submodelsRef = { current: {} }
    const preambleRef = { current: "import pandas as pd" }

    const result = resolveGraphFromRefs(graphRef, parentGraphRef, submodelsRef, preambleRef)

    expect(result.preamble).toBe("import pandas as pd")
  })
})
