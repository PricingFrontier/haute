import type { Edge, Node } from "@xyflow/react"
import { describe, expect, it } from "vitest"

import { NODE_TYPES } from "../nodeTypes"
import {
  prepareNodeUpdate,
  type NodeUpdatePlanFailure,
  type PreparedNodeUpdate,
} from "../nodeUpdatePlan"

const RESERVED = new Set(["class", "for"])

function sanitizeIdentifier(label: string): string {
  return label.trim().replace(/[\s-]+/g, "_")
}

function renamedData(node: Node, newLabel: string): Record<string, unknown> {
  const sanitized = sanitizeIdentifier(newLabel)
  return {
    ...node.data,
    label: newLabel,
    _functionName: sanitized,
    _defaultInputName: sanitized,
    _sourceHandleInputNames: {},
  }
}

function makeNode(
  id: string,
  label: string,
  nodeType: string,
  config: Record<string, unknown> = {},
): Node {
  const sanitized = sanitizeIdentifier(label)
  return {
    id,
    type: nodeType,
    position: { x: 0, y: 0 },
    data: {
      label,
      nodeType,
      config,
      _functionName: sanitized,
      _defaultInputName: sanitized,
      _sourceHandleInputNames: {},
    },
  }
}

function makeEdge(
  id: string,
  source: string,
  target: string,
  sourceHandle: string | null = null,
  inputName?: string,
): Edge {
  return {
    id,
    source,
    target,
    sourceHandle,
    targetHandle: null,
    ...(inputName ? { data: { _inputName: inputName } } : {}),
  }
}

const table = (
  label: string,
  emit: boolean,
  columns: Array<Record<string, unknown>> = [{ name: "c", selected: true }],
) => ({
  path: `$[:].${label}`,
  label,
  emit,
  columns,
})

describe("prepareNodeUpdate rename stable bindings", () => {
  it("1. coded consumer gains the binding on rename", () => {
    const src = makeNode("src", "src", NODE_TYPES.POLARS)
    const consumer = makeNode("consumer", "Consumer", NODE_TYPES.POLARS, {
      code: 'df = src.select("v")',
    })
    const edge = makeEdge("e1", "src", "consumer", null, "src")

    const result = prepareNodeUpdate({
      nodeId: "src",
      data: renamedData(src, "Renamed Src"),
      refreshSourceIdentity: true,
      readOnly: false,
      graph: { nodes: [src, consumer], edges: [edge] },
      submodels: {},
      reservedApiInputFrameLabels: RESERVED,
    })

    expect(result.ok).toBe(true)
    const prepared = result as PreparedNodeUpdate
    const targetNode = prepared.nodes.find((n) => n.id === "consumer")
    expect(targetNode?.data.config).toEqual({
      code: 'df = src.select("v")',
      inputMapping: { src: "Renamed_Src" },
    })
    const updatedEdge = prepared.edges.find((e) => e.id === "e1")
    expect(updatedEdge?.data?._inputName).toBe("Renamed_Src")
  })

  it("2. uncoded consumer does not gain a mapping when code is empty or absent", () => {
    const src = makeNode("src", "src", NODE_TYPES.POLARS)
    const uncodedEmpty = makeNode("consumer_empty", "Consumer Empty", NODE_TYPES.POLARS, {
      code: "",
    })
    const uncodedAbsent = makeNode("consumer_absent", "Consumer Absent", NODE_TYPES.POLARS, {})
    const edge1 = makeEdge("e1", "src", "consumer_empty", null, "src")
    const edge2 = makeEdge("e2", "src", "consumer_absent", null, "src")

    const result = prepareNodeUpdate({
      nodeId: "src",
      data: renamedData(src, "Renamed Src"),
      refreshSourceIdentity: true,
      readOnly: false,
      graph: { nodes: [src, uncodedEmpty, uncodedAbsent], edges: [edge1, edge2] },
      submodels: {},
      reservedApiInputFrameLabels: RESERVED,
    })

    expect(result.ok).toBe(true)
    const prepared = result as PreparedNodeUpdate
    const targetEmpty = prepared.nodes.find((n) => n.id === "consumer_empty")
    expect((targetEmpty?.data.config as Record<string, unknown>).inputMapping).toBeUndefined()
    const targetAbsent = prepared.nodes.find((n) => n.id === "consumer_absent")
    expect((targetAbsent?.data.config as Record<string, unknown>).inputMapping).toBeUndefined()
  })

  it("3. existing mapping value rewritten, logical kept", () => {
    const src = makeNode("src", "src", NODE_TYPES.POLARS)
    const consumer = makeNode("consumer", "Consumer", NODE_TYPES.POLARS, {
      code: 'df = raw.select("v")',
      inputMapping: { raw: "src" },
    })
    const edge = makeEdge("e1", "src", "consumer", null, "src")

    const result = prepareNodeUpdate({
      nodeId: "src",
      data: renamedData(src, "Renamed Src"),
      refreshSourceIdentity: true,
      readOnly: false,
      graph: { nodes: [src, consumer], edges: [edge] },
      submodels: {},
      reservedApiInputFrameLabels: RESERVED,
    })

    expect(result.ok).toBe(true)
    const prepared = result as PreparedNodeUpdate
    const targetNode = prepared.nodes.find((n) => n.id === "consumer")
    expect(targetNode?.data.config).toEqual({
      code: 'df = raw.select("v")',
      inputMapping: { raw: "Renamed_Src" },
    })
  })

  it("4. renaming back removes the identity entry", () => {
    const src = makeNode("src", "Renamed Src", NODE_TYPES.POLARS)
    const consumer = makeNode("consumer", "Consumer", NODE_TYPES.POLARS, {
      code: 'df = src.select("v")',
      inputMapping: { src: "Renamed_Src" },
    })
    const edge = makeEdge("e1", "src", "consumer", null, "Renamed_Src")

    const result = prepareNodeUpdate({
      nodeId: "src",
      data: renamedData(src, "src"),
      refreshSourceIdentity: true,
      readOnly: false,
      graph: { nodes: [src, consumer], edges: [edge] },
      submodels: {},
      reservedApiInputFrameLabels: RESERVED,
    })

    expect(result.ok).toBe(true)
    const prepared = result as PreparedNodeUpdate
    const targetNode = prepared.nodes.find((n) => n.id === "consumer")
    expect((targetNode?.data.config as Record<string, unknown>).inputMapping).toBeUndefined()
    expect(targetNode?.data.config).toEqual({
      code: 'df = src.select("v")',
    })
  })

  it("5. two coded consumers both gain binding while uncoded does not", () => {
    const src = makeNode("src", "src", NODE_TYPES.POLARS)
    const consumer1 = makeNode("c1", "Consumer 1", NODE_TYPES.POLARS, {
      code: 'df = src.select("v1")',
    })
    const consumer2 = makeNode("c2", "Consumer 2", NODE_TYPES.POLARS, {
      code: 'df = src.select("v2")',
    })
    const consumer3 = makeNode("c3", "Consumer 3", NODE_TYPES.POLARS, {})
    const edge1 = makeEdge("e1", "src", "c1", null, "src")
    const edge2 = makeEdge("e2", "src", "c2", null, "src")
    const edge3 = makeEdge("e3", "src", "c3", null, "src")

    const result = prepareNodeUpdate({
      nodeId: "src",
      data: renamedData(src, "Renamed Src"),
      refreshSourceIdentity: true,
      readOnly: false,
      graph: { nodes: [src, consumer1, consumer2, consumer3], edges: [edge1, edge2, edge3] },
      submodels: {},
      reservedApiInputFrameLabels: RESERVED,
    })

    expect(result.ok).toBe(true)
    const prepared = result as PreparedNodeUpdate
    expect((prepared.nodes.find((n) => n.id === "c1")?.data.config as Record<string, unknown>).inputMapping)
      .toEqual({ src: "Renamed_Src" })
    expect((prepared.nodes.find((n) => n.id === "c2")?.data.config as Record<string, unknown>).inputMapping)
      .toEqual({ src: "Renamed_Src" })
    expect((prepared.nodes.find((n) => n.id === "c3")?.data.config as Record<string, unknown>).inputMapping)
      .toBeUndefined()
  })

  it("6. collision rejected when rename collides with another input without mutating graph", () => {
    const src = makeNode("src", "src", NODE_TYPES.POLARS)
    const other = makeNode("other", "other", NODE_TYPES.POLARS)
    const consumer = makeNode("consumer", "Consumer", NODE_TYPES.POLARS, {
      code: 'df = src.join(other, on="id")',
    })
    const edgeSrc = makeEdge("e_src", "src", "consumer", null, "src")
    const edgeOther = makeEdge("e_other", "other", "consumer", null, "other")
    const nodes = [src, other, consumer]
    const edges = [edgeSrc, edgeOther]
    const nodesBefore = JSON.stringify(nodes)
    const edgesBefore = JSON.stringify(edges)

    const result = prepareNodeUpdate({
      nodeId: "src",
      data: renamedData(src, "other"),
      refreshSourceIdentity: true,
      readOnly: false,
      graph: { nodes, edges },
      submodels: {},
      reservedApiInputFrameLabels: RESERVED,
    })

    expect(result.ok).toBe(false)
    const failure = result as NodeUpdatePlanFailure
    expect(failure.error).toContain("Consumer")
    expect(failure.error).toContain("other")
    expect(JSON.stringify(nodes)).toBe(nodesBefore)
    expect(JSON.stringify(edges)).toBe(edgesBefore)
  })

  it("7. logical collision rejected when logical name is already bound", () => {
    const third = makeNode("third", "third", NODE_TYPES.POLARS)
    const src = makeNode("src", "src", NODE_TYPES.POLARS)
    const consumer = makeNode("consumer", "Consumer", NODE_TYPES.POLARS, {
      code: 'df = other.join(src, on="id")',
      inputMapping: { other: "third" },
    })
    const edgeThird = makeEdge("e_third", "third", "consumer", null, "third")
    const edgeSrc = makeEdge("e_src", "src", "consumer", null, "src")

    const result = prepareNodeUpdate({
      nodeId: "src",
      data: renamedData(src, "other"),
      refreshSourceIdentity: true,
      readOnly: false,
      graph: { nodes: [third, src, consumer], edges: [edgeThird, edgeSrc] },
      submodels: {},
      reservedApiInputFrameLabels: RESERVED,
    })

    expect(result.ok).toBe(false)
    const failure = result as NodeUpdatePlanFailure
    expect(failure.error).toContain("Consumer")
    expect(failure.error).toContain("other")
  })

  it("8. API-frame rename reaches the rule and binds logical name", () => {
    const api = makeNode("api_0", "Quotes", NODE_TYPES.API_INPUT, {
      tables: [table("policies", true)],
    })
    api.data._sourceHandleInputNames = { policies: "policies" }
    const consumer = makeNode("consumer", "Consumer", NODE_TYPES.POLARS, {
      code: 'df = policies.select("c")',
    })
    const edge = makeEdge("e1", "api_0", "consumer", "policies", "policies")

    const updatedApiData: Record<string, unknown> = {
      ...api.data,
      config: {
        tables: [{ ...table("policies", true), label: "quotes" }],
      },
      _sourceHandleInputNames: { quotes: "quotes" },
    }

    const result = prepareNodeUpdate({
      nodeId: "api_0",
      data: updatedApiData,
      refreshSourceIdentity: true,
      readOnly: false,
      graph: { nodes: [api, consumer], edges: [edge] },
      submodels: {},
      reservedApiInputFrameLabels: RESERVED,
    })

    expect(result.ok).toBe(true)
    const prepared = result as PreparedNodeUpdate
    const targetNode = prepared.nodes.find((n) => n.id === "consumer")
    expect(targetNode?.data.config).toEqual({
      code: 'df = policies.select("c")',
      inputMapping: { policies: "quotes" },
    })
  })

  it("9. instance keys still migrate exactly as before", () => {
    const src = makeNode("src", "Other Source", NODE_TYPES.POLARS)
    const original = makeNode("original", "Original", NODE_TYPES.POLARS)
    const instanceNode = makeNode("instance", "Instance", NODE_TYPES.POLARS, {
      instanceOf: "original",
      inputMapping: { original_input: "Other_Source" },
    })
    const edge = makeEdge("e1", "src", "instance", null, "Other_Source")

    const result = prepareNodeUpdate({
      nodeId: "src",
      data: renamedData(src, "Renamed Source"),
      refreshSourceIdentity: true,
      readOnly: false,
      graph: { nodes: [src, original, instanceNode], edges: [edge] },
      submodels: {},
      reservedApiInputFrameLabels: RESERVED,
    })

    expect(result.ok).toBe(true)
    const prepared = result as PreparedNodeUpdate
    const targetNode = prepared.nodes.find((n) => n.id === "instance")
    expect((targetNode?.data.config as Record<string, unknown>).inputMapping).toEqual({
      original_input: "Renamed_Source",
    })
  })

  it("10. sanitised label uses identity identifier in binding", () => {
    const src = makeNode("src", "src", NODE_TYPES.POLARS)
    const consumer = makeNode("consumer", "Consumer", NODE_TYPES.POLARS, {
      code: 'df = src.select("v")',
    })
    const edge = makeEdge("e1", "src", "consumer", null, "src")

    const result = prepareNodeUpdate({
      nodeId: "src",
      data: renamedData(src, "My Source 2"),
      refreshSourceIdentity: true,
      readOnly: false,
      graph: { nodes: [src, consumer], edges: [edge] },
      submodels: {},
      reservedApiInputFrameLabels: RESERVED,
    })

    expect(result.ok).toBe(true)
    const prepared = result as PreparedNodeUpdate
    const targetNode = prepared.nodes.find((n) => n.id === "consumer")
    expect(targetNode?.data.config).toEqual({
      code: 'df = src.select("v")',
      inputMapping: { src: "My_Source_2" },
    })
  })
})
