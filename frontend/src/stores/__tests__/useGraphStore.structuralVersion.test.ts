import { describe, it, expect, beforeEach } from "vitest"
import { act } from "@testing-library/react"
import useGraphStore, { computeStructuralFingerprint, computePanelContextFingerprint } from "../useGraphStore"
import { serializeSnapshot } from "../../utils/graphSnapshot"
import { makeNode, makeEdge } from "../../test-utils/factories"
import type { Node } from "@xyflow/react"

function resetStore() {
  useGraphStore.setState({
    nodes: [],
    edges: [],
    preamble: "",
    submodels: {},
    lastSavedSnapshot: null,
    undoStack: [],
    redoStack: [],
    structuralVersion: 0,
    structuralFingerprint: computeStructuralFingerprint([], [], ""),
    panelContextVersion: 0,
    panelContextFingerprint: computePanelContextFingerprint([], []),
    persistedFingerprint: serializeSnapshot({ nodes: [], edges: [], preamble: "", submodels: {} }),
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

  it("keeps preview freshness through generated step-code refresh, but invalidates on authored edits", () => {
    const steps = [{ id: "s", kind: "source", input: "quotes" }]
    const initial = makeNode("t", "polars", { data: { config: { steps, code: "old code", _steps_error: "old error" } } })
    const store = useGraphStore.getState()
    store.setNodesRaw([initial])
    store.markSaved()
    const version = useGraphStore.getState().structuralVersion
    const rendered = { ...initial, data: { ...initial.data, config: { steps, code: "df = quotes" } } }

    store.setNodesAndEdgesAndSubmodels([rendered], [], {})

    expect(useGraphStore.getState().structuralVersion).toBe(version)
    expect(useGraphStore.getState().dirty).toBe(false)
    expect(useGraphStore.getState().undoStack).toHaveLength(0)

    const edited = { ...rendered, data: { ...rendered.data, config: { steps: [...steps, { id: "l", kind: "limit", n: 3 }], code: "df = quotes" } } }
    store.setNodesAndEdgesAndSubmodels([edited], [], {})
    expect(useGraphStore.getState().structuralVersion).toBe(version + 1)
    expect(useGraphStore.getState().dirty).toBe(true)
    expect(useGraphStore.getState().undoStack).toHaveLength(1)
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

  it("keeps underscore-prefixed node metadata out of persisted dirty fingerprints", () => {
    const store = useGraphStore.getState()
    const savedNode = makeNode("a", "polars", {
      data: {
        label: "Node a",
        nodeType: "polars",
        config: { alpha: 1 },
      },
    })

    act(() => {
      store.setNodesRaw([savedNode])
      store.markSaved()
    })

    const { persistedFingerprint, savedPersistedFingerprint, structuralVersion } = useGraphStore.getState()

    act(() => {
      store.setNodesRaw([
        {
          ...savedNode,
          data: {
            ...savedNode.data,
            _columns: [{ name: "new", dtype: "f64" }],
            _availableColumns: [{ name: "new", dtype: "f64" }],
            _schemaWarnings: [],
            _status: "ok",
          },
        },
      ])
    })

    expect(useGraphStore.getState().persistedFingerprint).toBe(persistedFingerprint)
    expect(useGraphStore.getState().savedPersistedFingerprint).toBe(savedPersistedFingerprint)
    expect(useGraphStore.getState().dirty).toBe(false)
    expect(useGraphStore.getState().isDirty()).toBe(false)
    expect(useGraphStore.getState().structuralVersion).toBe(structuralVersion)

    act(() => {
      store.setNodesRaw([
        {
          ...savedNode,
          data: {
            ...savedNode.data,
            config: { alpha: 2 },
            _columns: [{ name: "new", dtype: "f64" }],
          },
        },
      ])
    })

    expect(useGraphStore.getState().dirty).toBe(true)
    expect(useGraphStore.getState().isDirty()).toBe(true)
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
      { nodeType: "dataInput" },
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

  it("does not bump structuralVersion when Explore display-card config changes", () => {
    const store = useGraphStore.getState()
    const baseNode = makeNode("explore_1", "explore", {
      data: {
        label: "Explore",
        nodeType: "explore",
        config: {
          code: "df = df.select(pl.all())",
          overview: { dataset_snapshot: false },
          pivots: [{ id: "pivot_1" }],
          charts: [{ id: "chart_1", enabled: false }],
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
              pivots: [{ id: "pivot_1" }, { id: "pivot_2", future_setting: "rows" }],
              charts: [
                { id: "chart_1", enabled: true },
                { id: "chart_2", enabled: true },
              ],
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

  describe("submodels", () => {
    const definition = (children: Node[], inputPorts: unknown[] = []) => ({
      definition: {
        definitionId: "definition",
        file: "modules/definition.py",
        inputPorts,
        outputPorts: [],
        graph: { nodes: children, edges: [] },
      },
    })

    beforeEach(() => {
      useGraphStore.getState().resetForTests()
      useGraphStore.getState().loadGraphSnapshot({
        nodes: [makeNode("occurrence", "submodel")],
        edges: [],
        preamble: "",
        submodels: definition([makeNode("child")]),
      })
    })

    it("advances on a definition's child graph or ports, not on preview results written into it", () => {
      const store = () => useGraphStore.getState()
      const start = store().structuralVersion

      act(() => store().setSubmodelsRaw(definition([makeNode("child"), makeNode("child2")])))
      expect(store().structuralVersion).toBe(start + 1)

      act(() => store().setSubmodelsRaw(definition([makeNode("child"), makeNode("child2")], [{ name: "quotes" }])))
      expect(store().structuralVersion).toBe(start + 2)

      const withColumns = makeNode("child2", "polars", { data: { _columns: [{ name: "premium", dtype: "f64" }] } })
      act(() => store().setSubmodelsRaw(definition([makeNode("child"), withColumns], [{ name: "quotes" }])))
      expect(store().structuralVersion).toBe(start + 2)
    })

    it("advances when Undo restores only a definition", () => {
      const store = () => useGraphStore.getState()
      act(() => store().setNodesAndEdgesAndSubmodels(store().nodes, [], definition([makeNode("child"), makeNode("child2")])))
      const edited = store().structuralVersion

      act(() => store().undo())

      expect(store().structuralVersion).toBe(edited + 1)
    })

    it("advances both versions when a boundary card's ports change", () => {
      const store = () => useGraphStore.getState()
      const card = (ports: unknown[]) => makeNode("input", "submodelPort", {
        data: { label: "INPUT", portDirection: "input", ports },
      })
      act(() => store().setNodes([card([{ id: "quotes", label: "quotes" }])]))
      const structural = store().structuralVersion
      const panel = store().panelContextVersion

      act(() => store().setNodes([card([{ id: "quotes", label: "quotes" }, { id: "claims", label: "claims" }])]))

      expect(store().structuralVersion).toBe(structural + 1)
      expect(store().panelContextVersion).toBe(panel + 1)
    })
  })
})
