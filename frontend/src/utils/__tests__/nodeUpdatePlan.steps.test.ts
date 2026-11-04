import type { Edge, Node } from "@xyflow/react"
import { describe, expect, it } from "vitest"

import { NODE_TYPES } from "../nodeTypes"
import { prepareNodeUpdate, type NodeUpdatePlanFailure, type PreparedNodeUpdate } from "../nodeUpdatePlan"

const RESERVED = new Set(["class", "for"])

function sanitize(label: string): string {
  return label.trim().replace(/[\s-]+/g, "_")
}

function makeNode(id: string, label: string, config: Record<string, unknown> = {}): Node {
  const sanitized = sanitize(label)
  return {
    id,
    type: NODE_TYPES.POLARS,
    position: { x: 0, y: 0 },
    data: {
      label,
      nodeType: NODE_TYPES.POLARS,
      config,
      _functionName: sanitized,
      _defaultInputName: sanitized,
      _sourceHandleInputNames: {},
    },
  }
}

function makeEdge(id: string, source: string, target: string, inputName: string): Edge {
  return { id, source, target, sourceHandle: null, targetHandle: null, data: { _inputName: inputName } }
}

function renamed(node: Node, newLabel: string): Record<string, unknown> {
  const sanitized = sanitize(newLabel)
  return { ...node.data, label: newLabel, _functionName: sanitized, _defaultInputName: sanitized, _sourceHandleInputNames: {} }
}

const STEPS = [
  { id: "s", kind: "source", input: "quotes" },
  { id: "j", kind: "join", input: "rates", how: "left", left_on: ["k"], right_on: ["k"], suffix: "_r" },
  { id: "c", kind: "concat", inputs: ["rates"], how: "vertical" },
]

describe("prepareNodeUpdate on stepped transforms", () => {
  it("rewrites source, join and concat references and never adds input_mapping", () => {
    const quotes = makeNode("quotes", "quotes")
    const rates = makeNode("rates", "rates")
    const consumer = makeNode("consumer", "Consumer", {
      steps: STEPS,
      code: "df = quotes",
    })
    const graph = {
      nodes: [quotes, rates, consumer],
      edges: [makeEdge("e1", "quotes", "consumer", "quotes"), makeEdge("e2", "rates", "consumer", "rates")],
    }

    const result = prepareNodeUpdate({
      nodeId: "rates",
      data: renamed(rates, "Rate Book"),
      refreshSourceIdentity: true,
      readOnly: false,
      graph,
      submodels: {},
      reservedApiInputFrameLabels: RESERVED,
    })

    expect(result.ok).toBe(true)
    const prepared = result as PreparedNodeUpdate
    const target = prepared.nodes.find((n) => n.id === "consumer")
    const config = target?.data.config as Record<string, unknown>
    expect(config.input_mapping).toBeUndefined()
    expect(config.steps).toEqual([
      { id: "s", kind: "source", input: "quotes" },
      { id: "j", kind: "join", input: "Rate_Book", how: "left", left_on: ["k"], right_on: ["k"], suffix: "_r" },
      { id: "c", kind: "concat", inputs: ["Rate_Book"], how: "vertical" },
    ])
  })

  it("refuses a rename that would make two references collide", () => {
    const quotes = makeNode("quotes", "quotes")
    const rates = makeNode("rates", "rates")
    const consumer = makeNode("consumer", "Consumer", { steps: STEPS })
    const graph = {
      nodes: [quotes, rates, consumer],
      edges: [makeEdge("e1", "quotes", "consumer", "quotes"), makeEdge("e2", "rates", "consumer", "rates")],
    }

    const result = prepareNodeUpdate({
      nodeId: "rates",
      data: renamed(rates, "quotes"),
      refreshSourceIdentity: true,
      readOnly: false,
      graph,
      submodels: {},
      reservedApiInputFrameLabels: RESERVED,
    })

    expect(result.ok).toBe(false)
    expect((result as NodeUpdatePlanFailure).error).toMatch(/already has an input named "quotes"/)
  })

  it("keeps a code step reading a renamed input bound through input_mapping, its code run as code", () => {
    const quotes = makeNode("quotes", "quotes")
    const rates = makeNode("rates", "rates")
    const steps = [
      { id: "s", kind: "source", input: "quotes" },
      { id: "f", kind: "free_code", code: "df = df.join(rates, on='k')" },
    ]
    const update = (code: string) => prepareNodeUpdate({
      nodeId: "rates",
      data: renamed(rates, "Rate Book"),
      refreshSourceIdentity: true,
      readOnly: false,
      graph: {
        nodes: [quotes, rates, makeNode("consumer", "Consumer", { steps, code })],
        edges: [makeEdge("e1", "quotes", "consumer", "quotes"), makeEdge("e2", "rates", "consumer", "rates")],
      },
      submodels: {},
      reservedApiInputFrameLabels: RESERVED,
    })

    const result = update("df = quotes\ndf = df.join(rates, on='k')")
    expect(result.ok).toBe(true)
    const config = (result as PreparedNodeUpdate).nodes.find((n) => n.id === "consumer")?.data.config
    expect(config).toEqual({ code: "df = quotes\ndf = df.join(rates, on='k')", input_mapping: { rates: "Rate_Book" } })

    // Unfinished steps have no code to run that way, so the rename is refused.
    const unfinished = update("")
    expect(unfinished.ok).toBe(false)
    expect((unfinished as NodeUpdatePlanFailure).error).toMatch(/code step that names an input this rename changes/)
  })

  it("rewrites the steps as usual when no code step names the renamed input", () => {
    const quotes = makeNode("quotes", "quotes")
    const rates = makeNode("rates", "rates")
    const steps = [
      { id: "s", kind: "source", input: "quotes" },
      { id: "j", kind: "join", input: "rates", how: "left", left_on: ["k"], right_on: ["k"], suffix: "_r" },
      { id: "f", kind: "free_code", code: "df = df.with_columns(total=pl.col('a').rates)" },
    ]
    const result = prepareNodeUpdate({
      nodeId: "rates",
      data: renamed(rates, "Rate Book"),
      refreshSourceIdentity: true,
      readOnly: false,
      graph: {
        nodes: [quotes, rates, makeNode("consumer", "Consumer", { steps, code: "df = quotes" })],
        edges: [makeEdge("e1", "quotes", "consumer", "quotes"), makeEdge("e2", "rates", "consumer", "rates")],
      },
      submodels: {},
      reservedApiInputFrameLabels: RESERVED,
    })

    expect(result.ok).toBe(true)
    const config = (result as PreparedNodeUpdate).nodes.find((n) => n.id === "consumer")?.data.config as Record<string, unknown>
    expect(config.input_mapping).toBeUndefined()
    expect((config.steps as Record<string, unknown>[])[1].input).toBe("Rate_Book")
  })
})
