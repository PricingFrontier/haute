import { describe, it, expect, beforeEach } from "vitest"
import { act } from "@testing-library/react"
import useGraphStore from "../useGraphStore"
import { makeNode, makeEdge } from "../../test-utils/factories"

function resetStore() {
  useGraphStore.setState({
    nodes: [],
    edges: [],
    preamble: "",
    lastSavedSnapshot: null,
    undoStack: [],
    redoStack: [],
    structuralVersion: 0,
    structuralFingerprint: "nodes:||edges:||preamble:\"\"",
  })
}

describe("useGraphStore structuralVersion", () => {
  beforeEach(() => {
    resetStore()
  })

  it("does not bump for position, selection, or preview-only node data changes", () => {
    const store = useGraphStore.getState()
    act(() => {
      store.setNodesRaw([
        makeNode("a", "polars", {
          position: { x: 10, y: 20 },
          selected: false,
          data: {
            label: "Node a",
            nodeType: "polars",
            config: { alpha: 1 },
            _columns: [{ name: "old", dtype: "f64" }],
            _availableColumns: [{ name: "old", dtype: "f64" }],
            _schemaWarnings: [{ column: "old", status: "missing" }],
          },
        }),
      ])
    })

    const startVersion = useGraphStore.getState().structuralVersion

    act(() => {
      store.setNodesRaw([
        makeNode("a", "polars", {
          position: { x: 999, y: -42 },
          selected: true,
          data: {
            label: "Node a",
            nodeType: "polars",
            config: { alpha: 1 },
            _columns: [{ name: "new", dtype: "f64" }],
            _availableColumns: [{ name: "new", dtype: "f64" }],
            _schemaWarnings: [],
          },
        }),
      ])
    })

    expect(useGraphStore.getState().structuralVersion).toBe(startVersion)
  })

  it("bumps structuralVersion when nodes are added, removed, rewired, or reconfigured", () => {
    const store = useGraphStore.getState()

    act(() => {
      store.setNodesRaw([
        makeNode("a", "polars", { data: { label: "Node a", nodeType: "polars", config: { alpha: 1 } } }),
      ])
    })
    const afterAdd = useGraphStore.getState().structuralVersion
    expect(afterAdd).toBeGreaterThan(0)

    act(() => {
      store.setNodesRaw([
        makeNode("a", "polars", { data: { label: "Node a", nodeType: "polars", config: { alpha: 1 } } }),
        makeNode("b", "polars", { data: { label: "Node b", nodeType: "polars", config: { beta: 2 } } }),
      ])
    })
    expect(useGraphStore.getState().structuralVersion).toBeGreaterThan(afterAdd)

    const afterAddTwo = useGraphStore.getState().structuralVersion
    act(() => {
      store.setNodesRaw([
        makeNode("a", "polars", { data: { label: "Node a", nodeType: "polars", config: { alpha: 1 } } }),
      ])
    })
    expect(useGraphStore.getState().structuralVersion).toBeGreaterThan(afterAddTwo)

    const afterRemove = useGraphStore.getState().structuralVersion
    act(() => {
      store.setEdgesRaw([
        makeEdge("a", "b", { sourceHandle: "out", targetHandle: "in" }),
      ])
    })
    const afterWire = useGraphStore.getState().structuralVersion
    expect(afterWire).toBeGreaterThan(afterRemove)

    act(() => {
      store.setEdgesRaw([
        makeEdge("a", "b", { sourceHandle: "out-2", targetHandle: "in" }),
      ])
    })
    expect(useGraphStore.getState().structuralVersion).toBeGreaterThan(afterWire)

    const afterRewire = useGraphStore.getState().structuralVersion
    act(() => {
      store.setNodesRaw([
        makeNode("a", "polars", { data: { label: "Node a", nodeType: "polars", config: { alpha: 2 } } }),
      ])
    })
    expect(useGraphStore.getState().structuralVersion).toBeGreaterThan(afterRewire)
  })

  it("bumps structuralVersion when executable preamble changes", () => {
    const store = useGraphStore.getState()
    const startVersion = store.structuralVersion

    act(() => {
      store.setPreambleRaw("import polars as pl")
    })

    expect(useGraphStore.getState().structuralVersion).toBeGreaterThan(startVersion)

    const afterChange = useGraphStore.getState().structuralVersion
    act(() => {
      useGraphStore.getState().setPreambleRaw("import polars as pl")
    })

    expect(useGraphStore.getState().structuralVersion).toBe(afterChange)
  })
})
