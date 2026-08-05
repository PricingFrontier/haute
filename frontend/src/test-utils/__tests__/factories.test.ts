import { describe, it, expect } from "vitest"
import { makeNode, makeEdge, makeSimpleNode, makeSimpleEdge, makeGraph, makeConfig, makeTrainResult, makeTrainEstimate } from "../factories"

describe("makeNode", () => {
  it("creates a node with defaults", () => {
    const node = makeNode("n1")
    expect(node.id).toBe("n1")
    expect(node.position).toEqual({ x: 0, y: 0 })
    expect(node.type).toBe("polars")
    expect(node.data.label).toBe("Node n1")
    expect(node.data.nodeType).toBe("polars")
    expect(node.data.config).toEqual({})
  })

  it("accepts custom nodeType", () => {
    const node = makeNode("n2", "dataInput")
    expect(node.type).toBe("dataInput")
    expect(node.data.nodeType).toBe("dataInput")
  })

  it("accepts position override", () => {
    const node = makeNode("n3", "polars", { position: { x: 10, y: 20 } })
    expect(node.position).toEqual({ x: 10, y: 20 })
  })

  it("accepts data overrides that merge with defaults", () => {
    const node = makeNode("n4", "polars", { data: { label: "Custom" } })
    expect(node.data.label).toBe("Custom")
    expect(node.data.nodeType).toBe("polars")
  })

  it("each call returns a distinct object", () => {
    const a = makeNode("a")
    const b = makeNode("a")
    expect(a).not.toBe(b)
    expect(a).toEqual(b)
  })
})

describe("makeEdge", () => {
  it("creates an edge with auto-generated id", () => {
    const edge = makeEdge("n1", "n2")
    expect(edge.id).toBe("e_n1_n2")
    expect(edge.source).toBe("n1")
    expect(edge.target).toBe("n2")
  })

  it("accepts explicit id override", () => {
    const edge = makeEdge("a", "b", { id: "custom_id" })
    expect(edge.id).toBe("custom_id")
    expect(edge.source).toBe("a")
    expect(edge.target).toBe("b")
  })

  it("accepts additional edge properties", () => {
    const edge = makeEdge("a", "b", { animated: true })
    expect(edge.animated).toBe(true)
  })
})

describe("makeSimpleNode", () => {
  it("creates a SimpleNode with defaults", () => {
    const node = makeSimpleNode("s1")
    expect(node.id).toBe("s1")
    expect(node.data.label).toBe("Node s1")
    expect(node.data.description).toBe("")
    expect(node.data.nodeType).toBe("polars")
    expect(node.type).toBeUndefined()
  })

  it("accepts type override", () => {
    const node = makeSimpleNode("s2", "banding", { type: "custom" })
    expect(node.type).toBe("custom")
    expect(node.data.nodeType).toBe("banding")
  })

  it("accepts config override", () => {
    const node = makeSimpleNode("s3", "dataInput", { config: { path: "/data.csv" } })
    expect(node.data.config).toEqual({ path: "/data.csv" })
  })

  it("accepts description override", () => {
    const node = makeSimpleNode("s4", "polars", { description: "A node" })
    expect(node.data.description).toBe("A node")
  })
})

describe("makeSimpleEdge", () => {
  it("creates a SimpleEdge with given id, source, target", () => {
    const edge = makeSimpleEdge("e1", "a", "b")
    expect(edge.id).toBe("e1")
    expect(edge.source).toBe("a")
    expect(edge.target).toBe("b")
  })
})

describe("makeGraph", () => {
  it("creates a linear graph with default 3 nodes", () => {
    const { nodes, edges } = makeGraph()
    expect(nodes).toHaveLength(3)
    expect(edges).toHaveLength(2)
    expect(edges[0].source).toBe("n0")
    expect(edges[0].target).toBe("n1")
    expect(edges[1].source).toBe("n1")
    expect(edges[1].target).toBe("n2")
  })

  it("creates a graph with custom node count", () => {
    const { nodes, edges } = makeGraph(5)
    expect(nodes).toHaveLength(5)
    expect(edges).toHaveLength(4)
  })

  it("creates a graph without edges when linear is false", () => {
    const { nodes, edges } = makeGraph(3, false)
    expect(nodes).toHaveLength(3)
    expect(edges).toHaveLength(0)
  })
})

describe("makeConfig", () => {
  it("creates a config with sensible defaults", () => {
    const config = makeConfig()
    expect(config.target).toBe("loss_amount")
    expect(config.task).toBe("regression")
    expect(config.metrics).toEqual(["gini", "rmse"])
    expect(config.evaluation).toBeDefined()
    expect(config.params).toBeDefined()
  })

  it("accepts overrides", () => {
    const config = makeConfig({ target: "claim_count", task: "classification" })
    expect(config.target).toBe("claim_count")
    expect(config.task).toBe("classification")
  })
})

describe("makeTrainResult", () => {
  it("creates a result with sensible defaults", () => {
    const result = makeTrainResult()
    expect(result.status).toBe("completed")
    expect(result.development_rows).toBe(8000)
    expect(result.final_test_rows).toBe(2000)
    expect(result.feature_importance).toHaveLength(3)
    expect(result.final_test_metrics).toHaveProperty("gini")
  })

  it("accepts overrides", () => {
    const result = makeTrainResult({ status: "error", error: "OOM" })
    expect(result.status).toBe("error")
    expect(result.error).toBe("OOM")
  })
})

describe("makeTrainEstimate", () => {
  it("creates an estimate with sensible defaults", () => {
    const est = makeTrainEstimate()
    expect(est.total_rows).toBe(10000)
    expect(est.available_mb).toBe(16384)
    expect(est.was_downsampled).toBe(false)
  })

  it("accepts overrides", () => {
    const est = makeTrainEstimate({ was_downsampled: true, total_rows: 500000 })
    expect(est.was_downsampled).toBe(true)
    expect(est.total_rows).toBe(500000)
  })
})
