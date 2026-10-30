import type { Edge, Node } from "@xyflow/react"
import { describe, expect, it } from "vitest"

import { NODE_TYPES } from "../nodeTypes"
import {
  prepareNodeUpdate,
  reconcileGraphInputBindings,
  type NodeUpdatePlanFailure,
  type PreparedNodeUpdate,
} from "../nodeUpdatePlan"
import useGraphStore from "../../stores/useGraphStore"

const RESERVED = new Set(["class", "for"])

describe("occurrence output identities", () => {
  it.each([0, 1])("renames response mappings with a disabled duplicate in position %s", (disabledIndex) => {
    const source = makeNode("source", "old", NODE_TYPES.POLARS)
    const outputMapping = [
      { source_port: "old", source_column: "x", output_path: "$.x", enabled: disabledIndex !== 0 },
      { source_port: "new", source_column: "y", output_path: "$.x", enabled: disabledIndex !== 1 },
    ]
    const response = makeNode("response", "response", NODE_TYPES.OUTPUT, { outputMapping })
    const input = {
      nodeId: source.id, data: renamedData(source, "new"), refreshSourceIdentity: true, readOnly: false,
      graph: { nodes: [source, response], edges: [makeEdge("e", source.id, response.id, null, "old")] },
      submodels: {}, reservedApiInputFrameLabels: RESERVED,
    }
    const before = structuredClone(input)
    const result = prepareNodeUpdate(input)
    expect(result.ok).toBe(true)
    if (!result.ok) throw new Error(result.error)
    expect(result.nodes[1].data.config).toEqual({
      outputMapping: outputMapping.map((entry) => ({ ...entry, source_port: "new" })),
    })
    expect(input).toEqual(before)
  })

  it.each(["rename", "add output", "remove output"])("reconciles %s and all consumers in one undoable edit", (edit) => {
    const from = edit === "remove output" ? "copy_premium" : "copy"
    const to = edit === "rename" ? "renamed" : edit === "add output" ? "copy_premium" : "copy"
    const source = makeNode("copy", "copy", NODE_TYPES.SUBMODEL, { definitionId: "pricing", alias: "copy", instanceOf: "owner" })
    source.data._sourceHandleInputNames = { out__premium: from }
    const response = makeNode("response", "response", NODE_TYPES.OUTPUT, {
      outputMapping: [{ source_port: from, source_column: "premium", output_path: "$.premium" }],
    })
    const coded = makeNode("coded", "coded", NODE_TYPES.POLARS, { code: `df = ${from}` })
    const stepped = makeNode("stepped", "stepped", NODE_TYPES.POLARS, { steps: [{ kind: "source", input: from }] })
    const previous = { nodes: [source, response, coded, stepped], edges: [response, coded, stepped].map((n) => makeEdge(n.id, "copy", n.id, "out__premium", from)), submodels: {} }
    const data = { ...source.data, label: edit === "rename" ? "renamed" : "copy", _sourceHandleInputNames: { out__premium: to } }
    const next: typeof previous = { ...previous, nodes: previous.nodes.map((n) => n.id === "copy" ? { ...n, data } : n), edges: previous.edges.map((e) => ({ ...e, data: { _inputName: to } })) }
    const result = edit === "rename" ? prepareNodeUpdate({
      nodeId: "copy", data, refreshSourceIdentity: true, readOnly: false,
      graph: previous, submodels: {}, reservedApiInputFrameLabels: RESERVED,
    }) : reconcileGraphInputBindings(previous, next)
    expect(result.ok).toBe(true)
    if (!result.ok) throw new Error(result.error)
    expect(result.nodes.find((n) => n.id === "response")?.data.config).toEqual({
      outputMapping: [{ source_port: to, source_column: "premium", output_path: "$.premium" }],
    })
    expect(result.nodes.find((n) => n.id === "coded")?.data.config).toMatchObject({ inputMapping: { [from]: to } })
    expect(result.nodes.find((n) => n.id === "stepped")?.data.config).toEqual({ steps: [{ kind: "source", input: to }] })
    useGraphStore.setState({ ...previous, undoStack: [], redoStack: [] })
    useGraphStore.getState().setNodesAndEdgesAndSubmodels(result.nodes, result.edges, result.submodels)
    expect(useGraphStore.getState().undoStack).toHaveLength(1)
    useGraphStore.getState().undo()
    expect(useGraphStore.getState().nodes).toEqual(previous.nodes)
    expect(useGraphStore.getState().edges).toEqual(previous.edges)
    useGraphStore.getState().redo()
    expect(useGraphStore.getState().nodes).toEqual(result.nodes)
    expect(useGraphStore.getState().edges).toEqual(result.edges)

    const collision = structuredClone(next)
    collision.nodes.find((node) => node.id === "response")!.data.config = {
      outputMapping: [
        { source_port: from, source_column: "premium", output_path: "$.premium" },
        { source_port: to, source_column: "other", output_path: "$.premium" },
      ],
    }
    const beforeCollision = structuredClone(collision)
    expect(reconcileGraphInputBindings(previous, collision)).toMatchObject({ ok: false, error: expect.stringMatching(/mapping.*collid/i) })
    expect(collision).toEqual(beforeCollision)

    const competing = makeNode("competing", to, NODE_TYPES.POLARS)
    const duplicateInput = { ...next, nodes: [...next.nodes, competing], edges: [...next.edges, makeEdge("compete", competing.id, "coded", null, to)] }
    expect(reconcileGraphInputBindings(previous, duplicateInput)).toMatchObject({ ok: false, error: expect.stringContaining(to) })
  })

  it.each([NODE_TYPES.POLARS, NODE_TYPES.API_INPUT])("reconciles a %s rename in response mappings and refuses a collision", (type) => {
    const source = makeNode("source", "old", type, type === NODE_TYPES.API_INPUT ? { tables: [table("old", true)] } : {})
    source.data._sourceHandleInputNames = { old: "old" }
    const response = makeNode("response", "response", NODE_TYPES.OUTPUT, {
      outputMapping: [{ source_port: "old", source_column: "x", output_path: "$.x" }],
    })
    const nextData = type === NODE_TYPES.API_INPUT
      ? { ...source.data, config: { tables: [{ ...table("new", true), path: "$[:].old" }] }, _sourceHandleInputNames: { new: "new" } }
      : renamedData(source, "new")
    const input = { nodeId: source.id, data: nextData, refreshSourceIdentity: true, readOnly: false,
      graph: { nodes: [source, response], edges: [makeEdge("e", source.id, response.id, type === NODE_TYPES.API_INPUT ? "old" : null, "old")] },
      submodels: {}, reservedApiInputFrameLabels: RESERVED }
    const result = prepareNodeUpdate(input)
    expect(result.ok).toBe(true)
    if (!result.ok) throw new Error(result.error)
    expect(result.nodes[1].data.config).toEqual({ outputMapping: [{ source_port: "new", source_column: "x", output_path: "$.x" }] })
    response.data.config = { outputMapping: [
      { source_port: "old", source_column: "x", output_path: "$.x" },
      { source_port: "new", source_column: "y", output_path: "$.x" },
    ] }
    const before = structuredClone(input)
    expect(prepareNodeUpdate(input)).toMatchObject({ ok: false, error: expect.stringMatching(/mapping.*collid/i) })
    expect(input).toEqual(before)
  })
})

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

  // A Load File's free code names its inputs as a Transform's does.
  it("11. coded Load File consumer gains the binding on rename", () => {
    const src = makeNode("src", "src", NODE_TYPES.POLARS)
    const consumer = makeNode("consumer", "Loader", NODE_TYPES.EXTERNAL_FILE, {
      code: "df = df.join(src, on='k')",
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
    expect(prepared.nodes.find((n) => n.id === "consumer")?.data.config).toEqual({
      code: "df = df.join(src, on='k')",
      inputMapping: { src: "Renamed_Src" },
    })
  })
})

describe("prepareNodeUpdate on a Workbench Output", () => {
  const outputTable = (name: string) => ({ name, rows: "one", columns: [{ name: "x", type: "int" }] })
  const fill = (id: string, source: string, table: string): Edge => ({
    id,
    source,
    target: "response",
    sourceHandle: null,
    targetHandle: table,
  })

  it("keeps a connection while its table remains, wherever it moved, and removes it with its table", () => {
    const response = makeNode("response", "response", NODE_TYPES.WORKBENCH_OUTPUT, {
      tables: [outputTable("pricing_output"), outputTable("layers")],
    })
    const priced = makeNode("priced", "priced", NODE_TYPES.POLARS)
    const layered = makeNode("layered", "layered", NODE_TYPES.POLARS)
    const edges = [fill("e_priced", "priced", "pricing_output"), fill("e_layered", "layered", "layers")]

    const result = prepareNodeUpdate({
      nodeId: "response",
      data: { ...response.data, config: { tables: [outputTable("summary"), outputTable("pricing_output")] } },
      refreshSourceIdentity: false,
      readOnly: false,
      graph: { nodes: [response, priced, layered], edges },
      submodels: {},
      reservedApiInputFrameLabels: RESERVED,
    })

    expect(result.ok).toBe(true)
    const prepared = result as PreparedNodeUpdate
    expect(prepared.edges.map((edge) => edge.id)).toEqual(["e_priced"])
    expect(prepared.removed.map(({ edge }) => edge.id)).toEqual(["e_layered"])
  })
})
