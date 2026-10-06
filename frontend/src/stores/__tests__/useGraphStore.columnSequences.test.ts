import { describe, expect, it, beforeEach } from "vitest"
import type { Node } from "@xyflow/react"
import useGraphStore, { resetGraphStoreForTests } from "../useGraphStore"

type ColumnConfig = {
  selected_columns?: string[]
  column_renames?: Record<string, string>
  categorical_levels?: Record<string, Array<string | null>>
}

type Model = { config: ColumnConfig; saved: ColumnConfig; undo: ColumnConfig[]; redo: ColumnConfig[] }
type Operation =
  | "select"
  | "edit"
  | "rename"
  | "category"
  | "categoryNull"
  | "emptySettings"
  | "clear"
  | "undo"
  | "redo"
  | "markSaved"
  | "reload"

const clone = <T,>(value: T): T => structuredClone(value)
const configOf = (state: Pick<ReturnType<typeof useGraphStore.getState>, "nodes"> = useGraphStore.getState()): ColumnConfig =>
  clone((state.nodes[0]?.data.config ?? {}) as ColumnConfig)
const nodeFor = (config: ColumnConfig): Node => ({
  id: "columns",
  type: "pipelineNode",
  position: { x: 0, y: 0 },
  data: { label: "Columns", nodeType: "polars", config },
}) as Node

function initialModel(): Model {
  const config: ColumnConfig = {
    selected_columns: ["id", "city"],
    column_renames: { city: "City" },
    categorical_levels: { city: ["London"] },
  }
  return { config: clone(config), saved: clone(config), undo: [], redo: [] }
}

function applyModel(model: Model, operation: Operation) {
  const push = () => {
    model.undo.push(clone(model.config))
    model.redo = []
  }
  const next = clone(model.config)
  if (operation === "select") {
    push()
    next.selected_columns = ["id", "city", "country"]
    model.config = next
  } else if (operation === "edit") {
    push()
    next.selected_columns = ["city", "id"]
    model.config = next
  } else if (operation === "rename") {
    push()
    next.column_renames = { ...(next.column_renames ?? {}), city: "Municipality" }
    model.config = next
  } else if (operation === "category") {
    push()
    next.categorical_levels = { ...(next.categorical_levels ?? {}), city: ["London", "Paris"] }
    model.config = next
  } else if (operation === "categoryNull") {
    push()
    next.categorical_levels = { ...(next.categorical_levels ?? {}), city: ["London", null] }
    model.config = next
  } else if (operation === "emptySettings") {
    push()
    model.config = { selected_columns: [], column_renames: {}, categorical_levels: {} }
  } else if (operation === "clear") {
    push()
    delete next.selected_columns
    delete next.column_renames
    delete next.categorical_levels
    model.config = next
  } else if (operation === "undo" && model.undo.length) {
    model.redo.push(clone(model.config))
    model.config = model.undo.pop()!
  } else if (operation === "redo" && model.redo.length) {
    model.undo.push(clone(model.config))
    model.config = model.redo.pop()!
  } else if (operation === "markSaved") {
    model.saved = clone(model.config)
  } else if (operation === "reload") {
    model.config = {
      selected_columns: ["sku"],
      column_renames: { sku: "SKU" },
      categorical_levels: { sku: ["A"], city: [null] },
    }
    model.saved = clone(model.config)
    model.undo = []
    model.redo = []
  }
}

function updateStore(operation: Operation) {
  const state = useGraphStore.getState()
  if (operation === "undo") return state.undo()
  if (operation === "redo") return state.redo()
  if (operation === "markSaved") return state.markSaved()
  if (operation === "reload") {
    return state.loadGraphSnapshot({
      nodes: [nodeFor({ selected_columns: ["sku"], column_renames: { sku: "SKU" }, categorical_levels: { sku: ["A"], city: [null] } })],
      edges: [],
      preamble: "",
      submodels: {},
    })
  }
  state.setNodes((nodes) => nodes.map((node) => {
    const config = clone(node.data.config as ColumnConfig)
    if (operation === "select") config.selected_columns = ["id", "city", "country"]
    if (operation === "edit") config.selected_columns = ["city", "id"]
    if (operation === "rename") config.column_renames = { ...config.column_renames, city: "Municipality" }
    if (operation === "category") config.categorical_levels = { ...config.categorical_levels, city: ["London", "Paris"] }
    if (operation === "categoryNull") config.categorical_levels = { ...config.categorical_levels, city: ["London", null] }
    if (operation === "emptySettings") {
      config.selected_columns = []
      config.column_renames = {}
      config.categorical_levels = {}
    }
    if (operation === "clear") {
      delete config.selected_columns
      delete config.column_renames
      delete config.categorical_levels
    }
    return { ...node, data: { ...node.data, config } }
  }))
}

function seeded(seed: number, count: number): Operation[] {
  const choices: Operation[] = ["select", "edit", "rename", "category", "categoryNull", "emptySettings", "clear", "undo", "redo", "markSaved", "reload"]
  let value = seed
  return Array.from({ length: count }, () => {
    value = (value * 1664525 + 1013904223) >>> 0
    return choices[value % choices.length]
  })
}

function runSequence(operations: Operation[], adapter: (operation: Operation) => ColumnConfig = defaultAdapter) {
  resetGraphStoreForTests()
  const model = initialModel()
  useGraphStore.getState().loadGraphSnapshot({ nodes: [nodeFor(model.config)], edges: [], preamble: "", submodels: {} })
  for (const operation of operations) {
    applyModel(model, operation)
    const observed = adapter(operation)
    expect(observed, `operation ${operation}`).toEqual(model.config)
    const state = useGraphStore.getState()
    expect(state.dirty).toBe(JSON.stringify(model.config) !== JSON.stringify(model.saved))
    expect(state.undoStack.map((entry) => "nodes" in entry ? configOf(entry) : undefined)).toEqual(model.undo)
    expect(state.redoStack.map((entry) => "nodes" in entry ? configOf(entry) : undefined)).toEqual(model.redo)
    expect(configOf({ ...state, nodes: state.lastSavedSnapshot?.nodes ?? [] })).toEqual(model.saved)
    expect(state.undoStack.length + state.redoStack.length).toBeLessThan(100)
  }
}
const defaultAdapter = (operation: Operation) => {
  updateStore(operation)
  return configOf()
}

describe("useGraphStore column configuration state sequences", () => {
  beforeEach(() => resetGraphStoreForTests())

  it.each(Array.from({ length: 12 }, (_, index) => index + 1))(
    "preserves column settings across generated edit/history/save/load sequences (seed %i)",
    (seed) => runSequence(seeded(seed, 40)),
  )

  it("covers empty history, redo discard, clean undo, repeated reload, and explicit empty settings", () => {
    runSequence([
      "undo", "redo", "markSaved", "rename", "undo", "redo", "undo", "category", "redo",
      "reload", "reload", "emptySettings", "markSaved", "edit", "undo", "clear",
    ])
  })

  it("does not alias history or saved snapshots after nested config mutation", () => {
    useGraphStore.getState().loadGraphSnapshot({ nodes: [nodeFor(initialModel().config)], edges: [], preamble: "", submodels: {} })
    updateStore("rename")
    useGraphStore.getState().markSaved()
    const saved = useGraphStore.getState().lastSavedSnapshot!
    ;((useGraphStore.getState().nodes[0].data.config as ColumnConfig).categorical_levels!.city).push("Aliased")
    expect((saved.nodes[0].data.config as ColumnConfig).categorical_levels!.city).toEqual(["London"])
    useGraphStore.getState().undo()
    expect(configOf()).toEqual(initialModel().config)
  })

  it("shared oracle detects a dropped rename mutation", () => {
    const badAdapter = (operation: Operation) => {
      updateStore(operation)
      const observed = configOf()
      if (operation === "rename") delete observed.column_renames
      return observed
    }
    expect(() => runSequence(["rename"], badAdapter)).toThrow(/rename/)
  })
})
