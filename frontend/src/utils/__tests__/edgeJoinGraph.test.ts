import type { Edge, Node } from "@xyflow/react"
import { describe, expect, it, vi } from "vitest"
import {
  finalizeResolvedEdgeJoinInsertion,
  insertEdgeJoinNode,
  insertEdgeJoinNodeFromSources,
  swapEdgeJoinInputs,
  validateEdgeJoinInsertionCandidate,
  type EdgeJoinInsertSuccess,
} from "../edgeJoinGraph"
import { NODE_TYPES } from "../nodeTypes"

function node(id: string): Node {
  return {
    id,
    type: NODE_TYPES.POLARS,
    position: { x: 0, y: 0 },
    data: {
      label: id,
      nodeType: NODE_TYPES.POLARS,
      config: {},
      _defaultInputName: id,
      _sourceHandleInputNames: {},
    },
  }
}

function edge(id: string, source: string, target: string, extra: Partial<Edge> = {}): Edge {
  return { id, source, target, ...extra }
}

function finalizeInsertion(
  insertion: EdgeJoinInsertSuccess,
  serverDefaultInputName: string,
) {
  return finalizeResolvedEdgeJoinInsertion(insertion, {
    nodes: insertion.nodes.map((candidate) => candidate.id === insertion.newNodeId
      ? {
          ...candidate,
          data: {
            ...candidate.data,
            _defaultInputName: serverDefaultInputName,
            _sourceHandleInputNames: {},
          },
        }
      : candidate),
    edges: insertion.edges,
  })
}

describe("validateEdgeJoinInsertionCandidate", () => {
  const nodes = [node("base"), node("downstream"), node("lookup")]
  const edges = [edge("base-to-downstream", "base", "downstream")]

  it("accepts a compatible source and target edge without mutating either input array", () => {
    const result = validateEdgeJoinInsertionCandidate({
      nodes,
      edges,
      targetEdgeId: "base-to-downstream",
      connection: { source: "lookup", sourceHandle: "lookup_frame" },
    })

    expect(result).toEqual({ ok: true })
    expect(nodes).toHaveLength(3)
    expect(edges).toEqual([edge("base-to-downstream", "base", "downstream")])
  })

  it.each([
    ["missing edge", "missing", "lookup", "target-edge-not-found"],
    ["missing source", "base-to-downstream", "missing", "source-node-not-found"],
    ["missing endpoint", "stale-edge", "lookup", "target-edge-node-not-found"],
    ["self join", "base-to-downstream", "base", "self-join"],
    ["cycle", "base-to-downstream", "downstream", "cycle"],
  ] as const)("rejects a %s candidate with the insertion failure reason", (
    _label,
    targetEdgeId,
    source,
    reason,
  ) => {
    const candidateEdges = targetEdgeId === "stale-edge"
      ? [...edges, edge("stale-edge", "ghost", "downstream")]
      : edges

    expect(validateEdgeJoinInsertionCandidate({
      nodes,
      edges: candidateEdges,
      targetEdgeId,
      connection: { source },
    })).toEqual({ ok: false, reason })
  })
})

describe("insertEdgeJoinNode", () => {
  it("splits the target edge and creates a compact edgeJoin node", () => {
    const nodes = [node("a"), node("b"), node("c")]
    const edges = [
      edge("e-a-b", "a", "b", { sourceHandle: "policies", targetHandle: "input" }),
    ]

    const result = insertEdgeJoinNode({
      nodes,
      edges,
      targetEdgeId: "e-a-b",
      connection: { source: "c", sourceHandle: "drivers" },
      position: { x: 100, y: 50 },
      idFactory: () => "edgeJoin_1",
    })

    expect(result.ok).toBe(true)
    if (!result.ok) return
    const finalized = finalizeInsertion(result, "server_assigned_join")

    expect(result.newNodeId).toBe("edgeJoin_1")
    const joinNode = result.nodes.find((n) => n.id === "edgeJoin_1")
    expect(joinNode?.type).toBe(NODE_TYPES.EDGE_JOIN)
    expect(joinNode?.position).toEqual({ x: 100, y: 50 })
    expect(joinNode?.origin).toEqual([0.5, 0.5])
    expect(joinNode?.data).toMatchObject({
      label: "Edge Join 1",
      nodeType: NODE_TYPES.EDGE_JOIN,
      config: {
        baseInput: "a",
        joinInput: "c",
        how: "left",
        suffix: "_right",
      },
    })

    expect(result.edges).toEqual([
      expect.objectContaining({
        source: "a",
        target: "edgeJoin_1",
        sourceHandle: "policies",
        targetHandle: "base",
      }),
      expect.objectContaining({
        source: "edgeJoin_1",
        target: "b",
        targetHandle: "input",
      }),
      expect.objectContaining({
        source: "c",
        target: "edgeJoin_1",
        sourceHandle: "drivers",
        targetHandle: "join",
      }),
    ])
    expect(nodes).toHaveLength(3)
    expect(edges).toHaveLength(1)
    expect(result.nodes.find((n) => n.id === "b")?.data.config).toEqual({})
    expect(finalized.nodes.find((n) => n.id === "b")?.data.config).toMatchObject({
      inputMapping: { a: "server_assigned_join" },
    })
  })

  it("supports repeated joins on already split segments", () => {
    const first = insertEdgeJoinNode({
      nodes: [node("a"), node("b"), node("c"), node("d")],
      edges: [edge("e-a-b", "a", "b")],
      targetEdgeId: "e-a-b",
      connection: { source: "c" },
      position: { x: 50, y: 0 },
      idFactory: () => "edgeJoin_1",
    })
    expect(first.ok).toBe(true)
    if (!first.ok) return
    const finalizedFirst = finalizeInsertion(first, "Edge_Join_1")

    const segment = finalizedFirst.edges.find((e) => e.source === "edgeJoin_1" && e.target === "b")
    expect(segment).toBeDefined()

    const second = insertEdgeJoinNode({
      nodes: finalizedFirst.nodes,
      edges: finalizedFirst.edges,
      targetEdgeId: segment!.id,
      connection: { source: "d" },
      position: { x: 100, y: 0 },
      idFactory: () => "edgeJoin_2",
    })

    expect(second.ok).toBe(true)
    if (!second.ok) return
    const finalizedSecond = finalizeInsertion(second, "server_second_join")
    expect(finalizedSecond.nodes.some((n) => n.id === "edgeJoin_2")).toBe(true)
    expect(finalizedSecond.nodes.map((n) => n.data.label)).toEqual(
      expect.arrayContaining(["Edge Join 1", "Edge Join 2"]),
    )
    expect(finalizedSecond.edges).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ source: "edgeJoin_1", target: "edgeJoin_2", targetHandle: "base" }),
        expect.objectContaining({ source: "edgeJoin_2", target: "b" }),
        expect.objectContaining({ source: "d", target: "edgeJoin_2", targetHandle: "join" }),
      ]),
    )
    expect(finalizedSecond.nodes.find((n) => n.id === "b")?.data.config).toMatchObject({
      inputMapping: { a: "server_second_join" },
    })
  })

  it("preserves an apiInput frame handle as the downstream logical input", () => {
    const request: Node = {
      ...node("request"),
      type: NODE_TYPES.API_INPUT,
      data: {
        label: "Request",
        nodeType: NODE_TYPES.API_INPUT,
        config: {},
        _defaultInputName: null,
        _sourceHandleInputNames: { raw_rows: "raw_rows" },
      },
    }
    const result = insertEdgeJoinNode({
      nodes: [request, node("enriched"), node("lookup")],
      edges: [edge("e-request-enriched", "request", "enriched", {
        sourceHandle: "raw_rows",
      })],
      targetEdgeId: "e-request-enriched",
      connection: { source: "lookup" },
      position: { x: 0, y: 0 },
      idFactory: () => "edgeJoin_1",
    })

    expect(result.ok).toBe(true)
    if (!result.ok) return
    const finalized = finalizeInsertion(result, "Edge_Join_1")
    expect(finalized.nodes.find((n) => n.id === "enriched")?.data.config).toMatchObject({
      inputMapping: { raw_rows: "Edge_Join_1" },
    })
  })

  it("preserves a collapsed submodel alias and output port as the logical input", () => {
    const child = node("child_output")
    const occurrence: Node = {
      ...node("pricing_instance"),
      type: NODE_TYPES.SUBMODEL,
      data: {
        label: "Pricing instance",
        nodeType: NODE_TYPES.SUBMODEL,
        config: {
          definitionId: "definition_pricing",
          alias: "pricing_secondary",
        },
        _defaultInputName: null,
        _sourceHandleInputNames: {
          out__written_premium: "pricing_secondary__written_premium",
        },
      },
    }
    const result = insertEdgeJoinNode({
      nodes: [occurrence, node("enriched"), node("lookup")],
      edges: [edge("e-pricing-enriched", occurrence.id, "enriched", {
        sourceHandle: "out__written_premium",
      })],
      submodels: {
        definition_pricing: {
          definitionId: "definition_pricing",
          file: "modules/pricing.py",
          graph: { nodes: [child], edges: [] },
          inputPorts: [],
          outputPorts: [
            {
              portId: "written_premium",
              label: "Written premium",
              source: { nodeId: child.id, handleId: null },
            },
          ],
        },
      },
      targetEdgeId: "e-pricing-enriched",
      connection: { source: "lookup" },
      position: { x: 0, y: 0 },
      idFactory: () => "edgeJoin_1",
    })

    expect(result.ok).toBe(true)
    if (!result.ok) return
    const finalized = finalizeInsertion(result, "Edge_Join_1")
    expect(finalized.nodes.find((n) => n.id === "enriched")?.data.config).toMatchObject({
      inputMapping: { pricing_secondary__written_premium: "Edge_Join_1" },
    })
  })

  it("rewrites an existing instance mapping value without inventing keys", () => {
    const target = {
      ...node("instance"),
      data: {
        ...node("instance").data,
        config: {
          instanceOf: "original",
          inputMapping: { original_input: "a", other_input: "other" },
        },
      },
    }
    const result = insertEdgeJoinNode({
      nodes: [node("a"), target, node("lookup")],
      edges: [edge("e-a-instance", "a", "instance")],
      targetEdgeId: "e-a-instance",
      connection: { source: "lookup" },
      position: { x: 0, y: 0 },
      idFactory: () => "edgeJoin_1",
    })

    expect(result.ok).toBe(true)
    if (!result.ok) return
    const finalized = finalizeInsertion(result, "Edge_Join_1")
    expect(finalized.nodes.find((n) => n.id === "instance")?.data.config).toMatchObject({
      inputMapping: {
        original_input: "Edge_Join_1",
        other_input: "other",
      },
    })
  })

  it("does not add inputMapping to a non-Polars downstream node", () => {
    const target: Node = {
      ...node("output"),
      type: NODE_TYPES.OUTPUT,
      data: {
        label: "Output",
        nodeType: NODE_TYPES.OUTPUT,
        config: {},
      },
    }
    const result = insertEdgeJoinNode({
      nodes: [node("a"), target, node("lookup")],
      edges: [edge("e-a-output", "a", "output")],
      targetEdgeId: "e-a-output",
      connection: { source: "lookup" },
      position: { x: 0, y: 0 },
      idFactory: () => "edgeJoin_1",
    })

    expect(result.ok).toBe(true)
    if (!result.ok) return
    const finalized = finalizeInsertion(result, "Edge_Join_1")
    expect(finalized.nodes.find((n) => n.id === "output")?.data.config).toEqual({})
  })

  it("fails before allocating an id when an apiInput frame identity is unresolved", () => {
    const request: Node = {
      ...node("request"),
      type: NODE_TYPES.API_INPUT,
      data: {
        label: "Request",
        nodeType: NODE_TYPES.API_INPUT,
        config: {},
      },
    }
    const idFactory = vi.fn(() => "edgeJoin_1")

    expect(() => insertEdgeJoinNode({
      nodes: [request, node("enriched"), node("lookup")],
      edges: [edge("e-request-enriched", "request", "enriched")],
      targetEdgeId: "e-request-enriched",
      connection: { source: "lookup" },
      position: { x: 0, y: 0 },
      idFactory,
    })).toThrow(/unresolved source frame/)
    expect(idFactory).not.toHaveBeenCalled()
  })

  it("fails loudly instead of overwriting a colliding logical mapping key", () => {
    const target = {
      ...node("target"),
      data: {
        ...node("target").data,
        config: { inputMapping: { a: "other" } },
      },
    }

    expect(() => insertEdgeJoinNode({
      nodes: [node("a"), node("other"), target, node("lookup")],
      edges: [
        edge("e-a-target", "a", "target"),
        edge("e-other-target", "other", "target"),
      ],
      targetEdgeId: "e-a-target",
      connection: { source: "lookup" },
      position: { x: 0, y: 0 },
      idFactory: () => "edgeJoin_1",
    })).toThrow(/already uses it/)
  })

  it("updates a downstream edgeJoin baseInput when splitting its base edge", () => {
    const join1: Node = {
      ...node("edgeJoin_1"),
      type: NODE_TYPES.EDGE_JOIN,
      data: {
        label: "Edge Join 1",
        nodeType: NODE_TYPES.EDGE_JOIN,
        config: { baseInput: "a", joinInput: "c", how: "left", on: ["id"] },
      },
    }
    const result = insertEdgeJoinNode({
      nodes: [node("a"), node("c"), node("d"), join1],
      edges: [
        edge("e-a-join1", "a", "edgeJoin_1", { targetHandle: "base" }),
        edge("e-c-join1", "c", "edgeJoin_1", { targetHandle: "join" }),
      ],
      targetEdgeId: "e-a-join1",
      connection: { source: "d" },
      position: { x: 0, y: 0 },
      idFactory: () => "edgeJoin_2",
    })

    expect(result.ok).toBe(true)
    if (!result.ok) return

    const updatedJoin1 = result.nodes.find((n) => n.id === "edgeJoin_1")
    expect(updatedJoin1?.data.config).toMatchObject({
      baseInput: "edgeJoin_2",
      joinInput: "c",
    })
    expect(result.edges).toEqual(expect.arrayContaining([
      expect.objectContaining({
        source: "edgeJoin_2",
        target: "edgeJoin_1",
        targetHandle: "base",
      }),
    ]))
  })

  it("updates a downstream edgeJoin joinInput when splitting its join edge", () => {
    const join1: Node = {
      ...node("edgeJoin_1"),
      type: NODE_TYPES.EDGE_JOIN,
      data: {
        label: "Edge Join 1",
        nodeType: NODE_TYPES.EDGE_JOIN,
        config: { baseInput: "a", joinInput: "c", how: "left", on: ["id"] },
      },
    }
    const result = insertEdgeJoinNode({
      nodes: [node("a"), node("c"), node("d"), join1],
      edges: [
        edge("e-a-join1", "a", "edgeJoin_1", { targetHandle: "base" }),
        edge("e-c-join1", "c", "edgeJoin_1", { targetHandle: "join" }),
      ],
      targetEdgeId: "e-c-join1",
      connection: { source: "d" },
      position: { x: 0, y: 0 },
      idFactory: () => "edgeJoin_2",
    })

    expect(result.ok).toBe(true)
    if (!result.ok) return

    const updatedJoin1 = result.nodes.find((n) => n.id === "edgeJoin_1")
    expect(updatedJoin1?.data.config).toMatchObject({
      baseInput: "a",
      joinInput: "edgeJoin_2",
    })
    expect(result.edges).toEqual(expect.arrayContaining([
      expect.objectContaining({
        source: "edgeJoin_2",
        target: "edgeJoin_1",
        targetHandle: "join",
      }),
    ]))
  })

  it("rewrites downstream fan-in contract parent ids when splitting an edge", () => {
    const target = {
      ...node("b"),
      data: {
        ...node("b").data,
        config: {
          contract: {
            inputs: ["policy_id", "territory"],
            outputs: ["premium"],
            inputs_by_parent: {
              a: ["policy_id"],
              existing_parent: ["territory"],
            },
          },
        },
      },
    }
    const result = insertEdgeJoinNode({
      nodes: [node("a"), target, node("lookup"), node("existing_parent")],
      edges: [
        edge("e-a-b", "a", "b"),
        edge("e-existing-b", "existing_parent", "b"),
      ],
      targetEdgeId: "e-a-b",
      connection: { source: "lookup" },
      position: { x: 100, y: 50 },
      idFactory: () => "edgeJoin_1",
    })

    expect(result.ok).toBe(true)
    if (!result.ok) return

    const updatedTarget = result.nodes.find((n) => n.id === "b")
    expect(updatedTarget?.data.config).toMatchObject({
      contract: {
        inputs: ["policy_id", "territory"],
        outputs: ["premium"],
        inputs_by_parent: {
          edgeJoin_1: ["policy_id"],
          existing_parent: ["territory"],
        },
      },
    })
    expect(
      Object.keys(
        ((updatedTarget?.data.config as Record<string, unknown>).contract as Record<string, unknown>)
          .inputs_by_parent as Record<string, unknown>,
      ),
    ).not.toContain("a")
  })

  it("rejects cycle creation", () => {
    const idFactory = vi.fn(() => "edgeJoin_1")
    const result = insertEdgeJoinNode({
      nodes: [node("a"), node("b")],
      edges: [edge("e-a-b", "a", "b")],
      targetEdgeId: "e-a-b",
      connection: { source: "b" },
      position: { x: 0, y: 0 },
      idFactory,
    })

    expect(result).toEqual({ ok: false, reason: "cycle" })
    expect(idFactory).not.toHaveBeenCalled()
  })

  it("rejects using the target edge source as the join input", () => {
    const result = insertEdgeJoinNode({
      nodes: [node("a"), node("b")],
      edges: [edge("e-a-b", "a", "b")],
      targetEdgeId: "e-a-b",
      connection: { source: "a" },
      position: { x: 0, y: 0 },
      idFactory: () => "edgeJoin_1",
    })

    expect(result).toEqual({ ok: false, reason: "self-join" })
  })

  it("rejects missing graph references", () => {
    const result = insertEdgeJoinNode({
      nodes: [node("a"), node("b")],
      edges: [edge("e-a-b", "a", "b")],
      targetEdgeId: "missing",
      connection: { source: "a" },
      position: { x: 0, y: 0 },
      idFactory: () => "edgeJoin_1",
    })

    expect(result).toEqual({ ok: false, reason: "target-edge-not-found" })
  })
})

describe("insertEdgeJoinNodeFromSources", () => {
  it("creates an unconnected edgeJoin from two source outputs", () => {
    const nodes = [node("base"), node("lookup")]
    const edges = [edge("existing", "unrelated", "base")]

    const result = insertEdgeJoinNodeFromSources({
      nodes,
      edges,
      base: { source: "base", sourceHandle: "base_out" },
      join: { source: "lookup", sourceHandle: "lookup_out" },
      position: { x: 120, y: 80 },
      idFactory: () => "edgeJoin_1",
    })

    expect(result.ok).toBe(true)
    if (!result.ok) return

    expect(result.newNodeId).toBe("edgeJoin_1")
    expect(result.nodes.find((n) => n.id === "edgeJoin_1")).toMatchObject({
      id: "edgeJoin_1",
      type: NODE_TYPES.EDGE_JOIN,
      position: { x: 120, y: 80 },
      origin: [0.5, 0.5],
      data: {
        label: "Edge Join 1",
        nodeType: NODE_TYPES.EDGE_JOIN,
        config: {
          baseInput: "base",
          joinInput: "lookup",
          how: "left",
          suffix: "_right",
        },
      },
      selected: true,
    })
    expect(result.edges).toEqual([
      edge("existing", "unrelated", "base"),
      expect.objectContaining({
        source: "base",
        target: "edgeJoin_1",
        sourceHandle: "base_out",
        targetHandle: "base",
      }),
      expect.objectContaining({
        source: "lookup",
        target: "edgeJoin_1",
        sourceHandle: "lookup_out",
        targetHandle: "join",
      }),
    ])
  })

  it("rejects joining a source output to itself", () => {
    const result = insertEdgeJoinNodeFromSources({
      nodes: [node("base")],
      edges: [],
      base: { source: "base" },
      join: { source: "base" },
      position: { x: 0, y: 0 },
      idFactory: () => "edgeJoin_1",
    })

    expect(result).toEqual({ ok: false, reason: "self-join" })
  })

  it("rejects missing source nodes", () => {
    const result = insertEdgeJoinNodeFromSources({
      nodes: [node("base")],
      edges: [],
      base: { source: "base" },
      join: { source: "missing" },
      position: { x: 0, y: 0 },
      idFactory: () => "edgeJoin_1",
    })

    expect(result).toEqual({ ok: false, reason: "source-node-not-found" })
  })
})

describe("swapEdgeJoinInputs", () => {
  it("swaps the role target handles and input config from incoming edge roles", () => {
    const joinNode: Node = {
      ...node("edgeJoin_1"),
      type: NODE_TYPES.EDGE_JOIN,
      data: {
        label: "Edge Join 1",
        nodeType: NODE_TYPES.EDGE_JOIN,
        config: {
          baseInput: "quotes",
          joinInput: "lookup",
          how: "inner",
          on: ["policy_id"],
          suffix: "_lookup",
        },
      },
    }
    const nodes = [node("quotes"), node("lookup"), joinNode, node("sink")]
    const edges = [
      edge("base-edge", "quotes", "edgeJoin_1", {
        sourceHandle: "quotes_out",
        targetHandle: "base",
      }),
      edge("join-edge", "lookup", "edgeJoin_1", {
        sourceHandle: "lookup_out",
        targetHandle: "join",
      }),
      edge("output-edge", "edgeJoin_1", "sink"),
    ]

    const result = swapEdgeJoinInputs({
      nodes,
      edges,
      edgeJoinNodeId: "edgeJoin_1",
    })

    expect(result.ok).toBe(true)
    if (!result.ok) return

    expect(result.nodes.find((n) => n.id === "edgeJoin_1")?.data.config).toEqual({
      baseInput: "lookup",
      joinInput: "quotes",
      how: "inner",
      on: ["policy_id"],
      suffix: "_lookup",
    })
    expect(result.edges).toEqual([
      expect.objectContaining({
        id: "base-edge",
        source: "quotes",
        target: "edgeJoin_1",
        sourceHandle: "quotes_out",
        targetHandle: "join",
      }),
      expect.objectContaining({
        id: "join-edge",
        source: "lookup",
        target: "edgeJoin_1",
        sourceHandle: "lookup_out",
        targetHandle: "base",
      }),
      edge("output-edge", "edgeJoin_1", "sink"),
    ])
    expect(joinNode.data.config).toMatchObject({
      baseInput: "quotes",
      joinInput: "lookup",
    })
    expect(edges[0].targetHandle).toBe("base")
    expect(edges[1].targetHandle).toBe("join")
  })

  it("rejects incomplete role-handled inputs instead of falling back to config", () => {
    const joinNode: Node = {
      ...node("edgeJoin_1"),
      type: NODE_TYPES.EDGE_JOIN,
      data: {
        label: "Edge Join 1",
        nodeType: NODE_TYPES.EDGE_JOIN,
        config: {
          baseInput: "quotes",
          joinInput: "lookup",
          how: "left",
        },
      },
    }

    const result = swapEdgeJoinInputs({
      nodes: [node("quotes"), node("lookup"), joinNode],
      edges: [
        edge("base-edge", "quotes", "edgeJoin_1", { targetHandle: "base" }),
        edge("unhandled-edge", "lookup", "edgeJoin_1"),
      ],
      edgeJoinNodeId: "edgeJoin_1",
    })

    expect(result).toEqual({ ok: false, reason: "join-input-not-found" })
  })
})
