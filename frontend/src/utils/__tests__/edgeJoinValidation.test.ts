import type { Edge, Node } from "@xyflow/react"
import { describe, expect, it } from "vitest"
import {
  analyzeEdgeJoinNode,
  findFirstInvalidEdgeJoin,
  formatEdgeJoinValidationIssue,
} from "../edgeJoinValidation"

function node(id: string, extra: Partial<Node> = {}): Node {
  return {
    id,
    type: "pipelineNode",
    position: { x: 0, y: 0 },
    data: {
      label: id,
      nodeType: "polars",
      config: {},
      _columns: [
        { name: "policy_id", dtype: "String" },
        { name: "state", dtype: "String" },
      ],
    },
    ...extra,
  }
}

function edge(
  id: string,
  source: string,
  target = "join",
  targetHandle: string | null = null,
  sourceHandle: string | null = null,
): Edge {
  return { id, source, target, targetHandle, sourceHandle }
}

function edgeJoin(config: Record<string, unknown>): Node {
  return node("join", {
    data: {
      label: "Edge Join",
      nodeType: "edgeJoin",
      config,
    },
  })
}

const connectedEdges = [
  edge("e-base", "base", "join", "base"),
  edge("e-lookup", "lookup", "join", "join"),
]

const FENCE = { structuralVersion: 7, activeSource: "live" }

/** An input whose columns are stamped as captured under *stamp*. */
function stampedInput(id: string, columns: string[], stamp: Partial<typeof FENCE> = FENCE): Node {
  return node(id, {
    data: {
      label: id,
      nodeType: "polars",
      config: {},
      _columns: columns.map((name) => ({ name, dtype: "String" })),
      _columnsStructuralVersion: stamp.structuralVersion,
      _columnsSource: stamp.activeSource,
    },
  })
}

describe("edgeJoinValidation", () => {
  it("accepts two distinct frames from one api-input node when their roles are handle-defined", () => {
    const apiInput = node("api", {
      data: {
        label: "Quote API",
        nodeType: "apiInput",
        config: {
          tables: [
            {
              label: "quotes",
              emit: true,
              columns: [{ name: "policy_id", type: "String", selected: true }],
            },
            {
              label: "drivers",
              emit: true,
              columns: [{ name: "driver_policy_id", type: "String", selected: true }],
            },
          ],
        },
        _columns: [],
      },
    })
    const join = edgeJoin({
      how: "left",
      leftOn: ["policy_id"],
      rightOn: ["driver_policy_id"],
    })
    const analysis = analyzeEdgeJoinNode({
      nodeId: "join",
      config: join.data.config as Record<string, unknown>,
      nodes: [apiInput, join],
      edges: [
        edge("quotes", "api", "join", "base", "quotes"),
        edge("drivers", "api", "join", "join", "drivers"),
      ],
    })

    expect(analysis.diagnostics).toEqual([])
    expect(analysis.baseRoleInput).toBe("api")
    expect(analysis.joinRoleInput).toBe("api")
    expect(analysis.baseColumns.map((column) => column.name)).toEqual(["policy_id"])
    expect(analysis.joinColumns.map((column) => column.name)).toEqual(["driver_policy_id"])
  })

  it("accepts a connected non-cross edgeJoin with same-name keys", () => {
    const nodes = [
      node("base"),
      node("lookup"),
      edgeJoin({ how: "left", on: ["policy_id"] }),
    ]

    const analysis = analyzeEdgeJoinNode({
      nodeId: "join",
      config: nodes[2].data.config as Record<string, unknown>,
      nodes,
      edges: connectedEdges,
    })

    expect(analysis.diagnostics).toEqual([])
  })

  it("flags non-cross edgeJoin configs without join keys", () => {
    const nodes = [
      node("base"),
      node("lookup"),
      edgeJoin({ how: "left" }),
    ]

    const issue = findFirstInvalidEdgeJoin(nodes, connectedEdges)

    expect(issue?.analysis.diagnostics).toContain("Non-cross joins need join keys.")
    expect(issue ? formatEdgeJoinValidationIssue(issue) : "").toBe(
      "Edge Join: Non-cross joins need join keys.",
    )
  })

  it("flags cross edgeJoin configs that still carry join keys", () => {
    const nodes = [
      node("base"),
      node("lookup"),
      edgeJoin({ how: "cross", on: ["policy_id"] }),
    ]

    const issue = findFirstInvalidEdgeJoin(nodes, connectedEdges)

    expect(issue?.analysis.diagnostics).toContain("Cross joins must not configure join keys.")
  })

  it("flags mismatched paired key counts", () => {
    const nodes = [
      node("base"),
      node("lookup"),
      edgeJoin({
        how: "left",
        leftOn: ["policy_id", "state"],
        rightOn: ["policy_id"],
      }),
    ]

    const issue = findFirstInvalidEdgeJoin(nodes, connectedEdges)

    expect(issue?.analysis.diagnostics).toContain(
      "leftOn and rightOn must contain the same number of keys.",
    )
  })

  it("rejects legacy role config instead of treating it as role authority", () => {
    const nodes = [
      node("base"),
      node("lookup"),
      edgeJoin({ baseInput: "lookup", joinInput: "base", how: "left", on: ["policy_id"] }),
    ]

    const issue = findFirstInvalidEdgeJoin(nodes, connectedEdges)

    expect(issue?.analysis.diagnostics).toContain(
      "Edge Join input roles are stored on incoming edge handles; remove legacy baseInput/joinInput config.",
    )
  })

  // A column stash is written by whichever preview last ran at or below a
  // node, so it can describe an older graph or another source. Only a stash
  // the caller vouches for may be quoted back as "the current upstream
  // columns" — a save gate that blocks on a stale one blocks a valid save.
  describe("column diagnostics only speak for a vouched-for stash", () => {
    const missingKeyConfig = { how: "left", on: ["policy_id"] }
    const MISSING = "Same-name key policy_id is not in the current upstream columns."

    it("reports a missing key when both stashes match the fence", () => {
      const nodes = [
        stampedInput("base", ["state"]),
        stampedInput("lookup", ["state"]),
        edgeJoin(missingKeyConfig),
      ]

      const issue = findFirstInvalidEdgeJoin(nodes, connectedEdges, FENCE)

      expect(issue?.analysis.diagnostics).toContain(MISSING)
    })

    it("stays silent when a stash was captured under an older structural version", () => {
      const nodes = [
        stampedInput("base", ["state"], { ...FENCE, structuralVersion: FENCE.structuralVersion - 1 }),
        stampedInput("lookup", ["state"]),
        edgeJoin(missingKeyConfig),
      ]

      expect(findFirstInvalidEdgeJoin(nodes, connectedEdges, FENCE)).toBeNull()
    })

    it("stays silent when a stash was captured under another source", () => {
      const nodes = [
        stampedInput("base", ["state"], { ...FENCE, activeSource: "staging" }),
        stampedInput("lookup", ["state"]),
        edgeJoin(missingKeyConfig),
      ]

      expect(findFirstInvalidEdgeJoin(nodes, connectedEdges, FENCE)).toBeNull()
    })

    it("stays silent when the caller vouches for nothing", () => {
      const nodes = [
        stampedInput("base", ["state"]),
        stampedInput("lookup", ["state"]),
        edgeJoin(missingKeyConfig),
      ]

      expect(findFirstInvalidEdgeJoin(nodes, connectedEdges)).toBeNull()
    })

    it("still offers a stale stash's columns as key options", () => {
      const stale = { ...FENCE, structuralVersion: FENCE.structuralVersion - 1 }
      const nodes = [
        stampedInput("base", ["policy_id", "state"], stale),
        stampedInput("lookup", ["policy_id"], stale),
        edgeJoin(missingKeyConfig),
      ]

      const analysis = analyzeEdgeJoinNode({
        nodeId: "join",
        config: missingKeyConfig,
        nodes,
        edges: connectedEdges,
        columnsFence: FENCE,
      })

      expect(analysis.diagnostics).toEqual([])
      expect(analysis.baseColumns.map((column) => column.name)).toEqual(["policy_id", "state"])
      expect(analysis.commonColumns.map((column) => column.name)).toEqual(["policy_id"])
    })

    it("treats an api-input's authored columns as current without any fence", () => {
      // They come from the node's own config, not from a preview capture, so
      // no stash can be stale and the diagnostic is always well-founded.
      // Keyed through leftOn so only the base side's currency decides.
      const apiInput = node("api", {
        data: {
          label: "Quote API",
          nodeType: "apiInput",
          config: {
            tables: [
              { label: "quotes", emit: true, columns: [{ name: "state", type: "String", selected: true }] },
            ],
          },
          _columns: [],
        },
      })
      const join = edgeJoin({ how: "left", leftOn: ["policy_id"], rightOn: ["state"] })
      const nodes = [apiInput, stampedInput("lookup", ["state"]), join]
      const edges = [
        edge("quotes", "api", "join", "base", "quotes"),
        edge("e-lookup", "lookup", "join", "join"),
      ]

      const issue = findFirstInvalidEdgeJoin(nodes, edges)

      expect(issue?.analysis.diagnostics).toContain(
        "Base key policy_id is not in the current upstream columns.",
      )
    })
  })
})
