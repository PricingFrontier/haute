import { describe, it, expect, beforeEach } from "vitest"
import { act } from "@testing-library/react"
import useGraphStore, { computeStructuralFingerprint } from "../useGraphStore"
import { makeNode, makeEdge } from "../../test-utils/factories"
import type { Node } from "@xyflow/react"

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
    panelContextVersion: 0,
    panelContextFingerprint: "nodes:||edges:",
    persistedFingerprint: "nodes:[]|edges:[]|preamble:\"\"",
    savedPersistedFingerprint: null,
    dirty: false,
  })
}

function makeTrackedConfig(payload: Record<string, unknown>) {
  let stringifyCalls = 0
  const config = {
    toJSON() {
      stringifyCalls += 1
      return payload
    },
  }

  return {
    config,
    stringifyCalls: () => stringifyCalls,
  }
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

  it("does not bump panelContextVersion for position or selection-only node changes", () => {
    const store = useGraphStore.getState()
    const data = {
      label: "Node a",
      nodeType: "polars",
      config: { alpha: 1 },
      _columns: [{ name: "stable", dtype: "f64" }],
    }
    const node = makeNode("a", "polars", {
      position: { x: 10, y: 20 },
      selected: false,
      data,
    })

    act(() => {
      store.setNodesRaw([node])
    })
    const startVersion = useGraphStore.getState().panelContextVersion

    act(() => {
      store.setNodesRaw([
        {
          ...node,
          position: { x: 100, y: 200 },
          selected: true,
        },
      ])
    })

    expect(useGraphStore.getState().panelContextVersion).toBe(startVersion)
  })

  it("bumps panelContextVersion, but not structuralVersion, for preview-only node data changes", () => {
    const store = useGraphStore.getState()
    act(() => {
      store.setNodesRaw([
        makeNode("a", "polars", {
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

    const { structuralVersion, panelContextVersion } = useGraphStore.getState()

    act(() => {
      store.setNodesRaw([
        makeNode("a", "polars", {
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

    expect(useGraphStore.getState().structuralVersion).toBe(structuralVersion)
    expect(useGraphStore.getState().panelContextVersion).toBeGreaterThan(panelContextVersion)
  })

  it("does not recompute the structural fingerprint for visual-only raw node changes", () => {
    const store = useGraphStore.getState()

    act(() => {
      store.setNodesRaw([
        makeNode("a", "polars", {
          data: { label: "Node a", nodeType: "polars", config: { alpha: 1 } },
        }),
      ])
    })

    const { structuralFingerprint, structuralVersion } = useGraphStore.getState()
    const hazardousData = {
      label: "Node a",
      nodeType: "polars",
      config: {
        alpha: 1,
        toJSON() {
          throw new Error("visual-only update recomputed the structural fingerprint")
        },
      },
    }
    const currentNode = {
      ...makeNode("a", "polars", { data: hazardousData }),
      selected: false,
      position: { x: 0, y: 0 },
    } as Node

    act(() => {
      useGraphStore.setState({
        nodes: [currentNode],
        structuralFingerprint,
        structuralVersion,
      })
    })

    expect(() => {
      act(() => {
        useGraphStore.getState().setNodesRaw([
          {
            ...currentNode,
            selected: true,
          },
        ])
      })
    }).not.toThrow()

    expect(useGraphStore.getState().structuralFingerprint).toBe(structuralFingerprint)
    expect(useGraphStore.getState().structuralVersion).toBe(structuralVersion)
  })

  it("reuses cached node config hashes when only visual node fields change", () => {
    const tracked = makeTrackedConfig({ alpha: 1 })
    const data = {
      label: "Node a",
      nodeType: "polars",
      config: tracked.config,
    }
    const node = makeNode("a", "polars", {
      position: { x: 0, y: 0 },
      selected: false,
      data,
    })

    const initialFingerprint = computeStructuralFingerprint([node], [], "")
    expect(tracked.stringifyCalls()).toBe(1)

    const visualOnlyNode = {
      ...node,
      data: { ...data },
      position: { x: 320, y: -80 },
      selected: true,
      dragging: true,
    }

    expect(computeStructuralFingerprint([visualOnlyNode], [], "")).toBe(initialFingerprint)
    expect(tracked.stringifyCalls()).toBe(1)
  })

  it("does not rehash node config when only edges or preamble change", () => {
    const tracked = makeTrackedConfig({ alpha: 1 })
    const node = makeNode("a", "polars", {
      data: {
        label: "Node a",
        nodeType: "polars",
        config: tracked.config,
      },
    })
    const edge = makeEdge("a", "b", { sourceHandle: "out", targetHandle: "in" })

    const baseFingerprint = computeStructuralFingerprint([node], [], "")
    expect(tracked.stringifyCalls()).toBe(1)

    const edgeFingerprint = computeStructuralFingerprint([node], [edge], "")
    expect(edgeFingerprint).not.toBe(baseFingerprint)
    expect(tracked.stringifyCalls()).toBe(1)

    const preambleFingerprint = computeStructuralFingerprint([node], [edge], "import polars as pl")
    expect(preambleFingerprint).not.toBe(edgeFingerprint)
    expect(tracked.stringifyCalls()).toBe(1)
  })

  it("invalidates cached node hashes when structural node inputs change", () => {
    const tracked = makeTrackedConfig({ alpha: 1 })
    const baseNode = makeNode("a", "polars", {
      data: {
        label: "Node a",
        description: "before",
        nodeType: "polars",
        config: tracked.config,
        code: "df",
        func_name: "node_a",
      },
    })
    const baseFingerprint = computeStructuralFingerprint([baseNode], [], "")

    const changedConfig = makeTrackedConfig({ alpha: 2 })
    const configFingerprint = computeStructuralFingerprint(
      [
        {
          ...baseNode,
          data: { ...baseNode.data, config: changedConfig.config },
        },
      ],
      [],
      "",
    )
    expect(configFingerprint).not.toBe(baseFingerprint)
    expect(changedConfig.stringifyCalls()).toBe(1)

    for (const dataPatch of [
      { label: "Node a renamed" },
      { description: "after" },
      { nodeType: "dataSource" },
      { code: "df.select('x')" },
      { func_name: "renamed_node" },
    ]) {
      expect(
        computeStructuralFingerprint(
          [
            {
              ...baseNode,
              data: { ...baseNode.data, ...dataPatch },
            },
          ],
          [],
          "",
        ),
      ).not.toBe(baseFingerprint)
    }
  })

  it("does not bump structuralVersion when Explore overview card config changes", () => {
    const store = useGraphStore.getState()
    const baseNode = makeNode("explore_1", "explore", {
      data: {
        label: "Explore",
        nodeType: "explore",
        config: {
          code: "df = df.select(pl.all())",
          overview: { dataset_snapshot: false },
        },
      },
    })

    act(() => {
      store.setNodesRaw([baseNode])
      store.markSaved()
    })

    const { structuralVersion, panelContextVersion } = useGraphStore.getState()

    act(() => {
      store.setNodesRaw([
        makeNode("explore_1", "explore", {
          data: {
            label: "Explore",
            nodeType: "explore",
            config: {
              code: "df = df.select(pl.all())",
              overview: { dataset_snapshot: true, schema: true, future_card: true },
            },
          },
        }),
      ])
    })

    expect(useGraphStore.getState().structuralVersion).toBe(structuralVersion)
    expect(useGraphStore.getState().panelContextVersion).toBeGreaterThan(panelContextVersion)
    expect(useGraphStore.getState().dirty).toBe(true)
  })

  it("still bumps structuralVersion when Explore data-prep code changes", () => {
    const store = useGraphStore.getState()
    act(() => {
      store.setNodesRaw([
        makeNode("explore_1", "explore", {
          data: {
            label: "Explore",
            nodeType: "explore",
            config: {
              code: "df = df.select(pl.all())",
              overview: { dataset_snapshot: true },
            },
          },
        }),
      ])
    })

    const { structuralVersion } = useGraphStore.getState()

    act(() => {
      store.setNodesRaw([
        makeNode("explore_1", "explore", {
          data: {
            label: "Explore",
            nodeType: "explore",
            config: {
              code: "df = df.filter(pl.col('premium') > 0)",
              overview: { dataset_snapshot: true },
            },
          },
        }),
      ])
    })

    expect(useGraphStore.getState().structuralVersion).toBeGreaterThan(structuralVersion)
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
