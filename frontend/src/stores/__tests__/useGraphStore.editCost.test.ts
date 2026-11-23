/**
 * What one edit costs (FEG-01): a commit's time and the undo history's size follow what
 * changed, not the size of every node in the graph. A Rating Step's tables live in its
 * node's settings with no cap on their rows, so a graph holding one large table is the
 * case: an edit to any other node must not serialise, hash or clone the table again.
 *
 * The bounds are generous (an edit costs well under a millisecond here, and the history
 * grows by kilobytes); before the fix one edit took a quarter of a second and the history
 * grew to most of a gigabyte.
 */
import { describe, it, expect, beforeEach } from "vitest"
import type { Node } from "@xyflow/react"
import useGraphStore, { canvasGraph, resetGraphStoreForTests } from "../useGraphStore"
import type { PipelineEdge, SubmodelDefinition } from "../../types/node"
import { prepareNodeUpdate } from "../../utils/nodeUpdatePlan"
import { NODE_TYPES } from "../../utils/nodeTypes"

const CODE_NODES = 100
const TABLE_ROWS = 100_000
const EDITS = 100

function codeNode(index: number): Node {
  return {
    id: `code_${index}`,
    type: "pipelineNode",
    position: { x: index * 10, y: 0 },
    data: {
      label: `code_${index}`,
      nodeType: NODE_TYPES.POLARS,
      _defaultInputName: `code_${index}`,
      config: { code: `df = df.with_columns(pl.lit(${index}).alias("c${index}"))` },
    },
  }
}

function ratingNode(rows: number): Node {
  return {
    id: "rating",
    type: "pipelineNode",
    position: { x: 0, y: 200 },
    data: {
      label: "rating",
      nodeType: NODE_TYPES.RATING_STEP,
      config: {
        tables: [{
          factors: ["area", "age"],
          output_column: "factor",
          entries: Array.from({ length: rows }, (_, row) => ({
            area: `area_${row % 500}`,
            age: row % 200,
            value: 1 + (row % 97) / 100,
          })),
        }],
      },
    },
  }
}

/** One character typed into a code node's editor, committed as the editors commit. */
function typeInto(nodeId: string, character: string): void {
  useGraphStore.getState().setNodesAndEdgesAndSubmodels(
    (nodes) => nodes.map((node) => node.id !== nodeId ? node : {
      ...node,
      data: {
        ...node.data,
        config: { code: `${(node.data.config as { code: string }).code}${character}` },
      },
    }),
    (edges) => edges,
    useGraphStore.getState().submodels,
  )
}

/** One character typed into a code node's editor, through the editor's own update plan. */
function typeThroughEditor(nodeId: string, character: string): void {
  const node = canvasGraph().nodes.find((candidate) => candidate.id === nodeId)!
  const config = node.data.config as { code: string }
  const prepared = prepareNodeUpdate({
    nodeId,
    data: { ...node.data, config: { ...config, code: `${config.code}${character}` } },
    refreshSourceIdentity: false,
    readOnly: false,
    graph: canvasGraph(),
    submodels: useGraphStore.getState().submodels,
    reservedApiInputFrameLabels: new Set(),
  })
  if (!prepared.ok) throw new Error(prepared.error)
  // What the editor's commit controller hands the store (`App.tsx`'s `commitGraph`).
  useGraphStore.getState().setNodesAndEdgesAndSubmodels(prepared.nodes, prepared.edges, prepared.submodels)
}

const DEFINITION_ID = "definition_rates"

function occurrence(): Node {
  return {
    id: "rates",
    type: "submodel",
    position: { x: 0, y: 400 },
    data: { label: "rates", nodeType: NODE_TYPES.SUBMODEL, config: { definition_id: DEFINITION_ID, alias: "rates" } },
  }
}

/** A submodel whose children are a code node and the large rating table. */
function ratesDefinition(rows: number): SubmodelDefinition {
  return {
    definitionId: DEFINITION_ID,
    file: "modules/rates.py",
    graph: {
      nodes: [
        { ...codeNode(900), id: "child_code", data: { ...codeNode(900).data, label: "child_code", _defaultInputName: "child_code" } },
        { ...ratingNode(rows), data: { ...ratingNode(rows).data, _defaultInputName: "rating" } },
      ],
      edges: [{ id: "child_feed", source: "child_code", target: "rating", data: { _inputName: "child_code" } }],
    },
    inputPorts: [{ name: "policy", targets: [{ nodeId: "child_code", handleId: null }] }],
    outputPorts: [{ name: "rated", source: { nodeId: "rating", handleId: null } }],
  }
}

function loadWithSubmodel(rows: number): void {
  const edges: PipelineEdge[] = [
    { id: "feed", source: "code_0", target: "rates", targetHandle: "in__policy" },
  ]
  useGraphStore.getState().loadGraphSnapshot({
    nodes: [...Array.from({ length: CODE_NODES }, (_, index) => codeNode(index)), occurrence()],
    edges,
    preamble: "",
    submodels: { [DEFINITION_ID]: ratesDefinition(rows) },
    globalConstants: [],
  })
}

/** The rating table each history entry's definition holds. */
function definitionTables(): Set<unknown> {
  return new Set(useGraphStore.getState().undoStack.map((entry) => {
    if (!("submodels" in entry)) return null
    const definition = entry.submodels[DEFINITION_ID] as SubmodelDefinition
    return (definition.graph.nodes.find((node) => node.id === "rating")?.data.config as { tables: unknown[] }).tables[0]
  }))
}

const gc = (globalThis as { gc?: () => void }).gc

function heapUsed(): number {
  gc?.()
  return process.memoryUsage().heapUsed
}

describe("useGraphStore - the cost of one edit", () => {
  beforeEach(() => resetGraphStoreForTests())

  it("an edit to a code node beside a 100,000-row table neither re-reads nor copies the table", () => {
    const nodes = [...Array.from({ length: CODE_NODES }, (_, index) => codeNode(index)), ratingNode(TABLE_ROWS)]
    useGraphStore.getState().loadGraphSnapshot({ nodes, edges: [], preamble: "", submodels: {}, globalConstants: [] })
    // The first edit reads every node once, as loading does; the bound is for the edits after it.
    typeInto("code_0", "x")
    useGraphStore.getState().markSaved()
    const before = heapUsed()
    const started = performance.now()
    for (let edit = 0; edit < EDITS; edit += 1) typeInto(`code_${edit % CODE_NODES}`, "x")
    const perEdit = (performance.now() - started) / EDITS
    const grown = heapUsed() - before

    const state = useGraphStore.getState()
    expect(state.undoStack).toHaveLength(EDITS)
    expect(state.dirty).toBe(true)
    expect(perEdit).toBeLessThan(5)
    // Every entry holds the one table: the history shares what an edit did not touch.
    const tables = new Set(state.undoStack.map((entry) => "nodes" in entry && entry.nodes.find((node) => node.id === "rating")))
    expect(tables.size).toBe(1)
    if (gc) expect(grown).toBeLessThan(10 * 2 ** 20)

    // Undo walks back to the saved document, and Redo forward again, at the same cost.
    const undoStarted = performance.now()
    for (let step = 0; step < EDITS; step += 1) useGraphStore.getState().undo()
    expect((performance.now() - undoStarted) / EDITS).toBeLessThan(5)
    expect(useGraphStore.getState().dirty).toBe(false)
    expect(useGraphStore.getState().nodes.find((node) => node.id === "code_1")?.data.config).toEqual(codeNode(1).data.config)
    for (let step = 0; step < EDITS; step += 1) useGraphStore.getState().redo()
    expect(useGraphStore.getState().dirty).toBe(true)
    expect((useGraphStore.getState().nodes.find((node) => node.id === "code_1")?.data.config as { code: string }).code)
      .toBe(`${(codeNode(1).data.config as { code: string }).code}x`)
  })

  it("an edit through the editor beside a submodel holding the table neither re-reads nor copies it", () => {
    loadWithSubmodel(TABLE_ROWS)
    typeThroughEditor("code_1", "x")
    useGraphStore.getState().markSaved()
    const before = heapUsed()
    const started = performance.now()
    for (let edit = 0; edit < EDITS; edit += 1) typeThroughEditor(`code_${1 + (edit % (CODE_NODES - 1))}`, "x")
    const perEdit = (performance.now() - started) / EDITS
    const grown = heapUsed() - before

    expect(useGraphStore.getState().undoStack).toHaveLength(EDITS)
    expect(useGraphStore.getState().dirty).toBe(true)
    expect(perEdit).toBeLessThan(5)
    expect(definitionTables().size).toBe(1)
    if (gc) expect(grown).toBeLessThan(10 * 2 ** 20)
    for (let step = 0; step < EDITS; step += 1) useGraphStore.getState().undo()
    expect(useGraphStore.getState().dirty).toBe(false)
  })

  it("an edit inside the open submodel beside the table neither re-reads nor copies it", () => {
    loadWithSubmodel(TABLE_ROWS)
    useGraphStore.getState().enterSubmodelView({ instanceId: "rates", definitionId: DEFINITION_ID })
    typeInto("child_code", "x")
    useGraphStore.getState().markSaved()
    const before = heapUsed()
    const started = performance.now()
    for (let edit = 0; edit < EDITS; edit += 1) typeInto("child_code", "x")
    const perEdit = (performance.now() - started) / EDITS
    const grown = heapUsed() - before

    const state = useGraphStore.getState()
    expect(state.undoStack).toHaveLength(EDITS)
    expect(state.dirty).toBe(true)
    const child = (state.submodels[DEFINITION_ID] as SubmodelDefinition).graph.nodes.find((node) => node.id === "child_code")
    expect((child?.data.config as { code: string }).code).toMatch(/x{101}$/)
    expect(perEdit).toBeLessThan(5)
    expect(definitionTables().size).toBe(1)
    if (gc) expect(grown).toBeLessThan(10 * 2 ** 20)
    for (let step = 0; step < EDITS; step += 1) useGraphStore.getState().undo()
    expect(useGraphStore.getState().dirty).toBe(false)
  })

  it("a node is frozen as it is committed: a new node, its data and its settings, and the lists holding them", () => {
    useGraphStore.getState().loadGraphSnapshot({ nodes: [codeNode(0)], edges: [], preamble: "", submodels: {}, globalConstants: [] })
    useGraphStore.getState().markSaved()
    useGraphStore.getState().setNodes((nodes) => [...nodes, codeNode(1)])
    useGraphStore.getState().setEdges([{ id: "joined", source: "code_0", target: "code_1" }])
    const state = useGraphStore.getState()
    const added = state.nodes.find((node) => node.id === "code_1")!
    expect(Object.isFrozen(added)).toBe(true)
    expect(Object.isFrozen(added.data)).toBe(true)
    expect(Object.isFrozen(added.data.config)).toBe(true)
    expect(Object.isFrozen(added.position)).toBe(true)
    // Replacing a node's settings in place, which would leave the digests kept for the node
    // reading the old ones, throws; so do changes to the lists and to an edge.
    expect(() => { (added.data as Record<string, unknown>).config = { code: "df = df" } }).toThrow(TypeError)
    expect(() => { (added as { data: unknown }).data = {} }).toThrow(TypeError)
    expect(() => state.nodes.push(codeNode(2))).toThrow(TypeError)
    expect(() => { state.edges[0].target = "code_0" }).toThrow(TypeError)
    expect(useGraphStore.getState().dirty).toBe(true)
    expect(useGraphStore.getState().isDirty()).toBe(true)
  })

  it("a committed node is frozen: a write into it in place throws instead of rewriting history", () => {
    useGraphStore.getState().loadGraphSnapshot({ nodes: [codeNode(0), ratingNode(3)], edges: [], preamble: "", submodels: {}, globalConstants: [] })
    typeInto("code_0", "x")
    const live = useGraphStore.getState().nodes.find((node) => node.id === "rating")!
    const entries = (live.data.config as { tables: { entries: { value: number }[] }[] }).tables[0].entries
    expect(() => { entries[0].value = 99 }).toThrow(TypeError)
    expect(() => entries.push({ value: 1 })).toThrow(TypeError)
  })
})
