import { beforeEach, describe, expect, it } from "vitest"
import useGraphStore, {
  computePanelContextFingerprint,
  computeStructuralFingerprint,
  type GraphSnapshot,
} from "../useGraphStore"
import { serializeSnapshot } from "../../utils/graphSnapshot"
import { makeEdge, makeNode } from "../../test-utils/factories"

function snapshot(
  name: string,
  preamble: string,
  submodels: Record<string, unknown>,
): GraphSnapshot {
  const node = makeNode(name)
  return {
    nodes: [node],
    edges: [makeEdge(name, `${name}-out`, { id: `${name}-edge` })],
    preamble,
    submodels,
  }
}

describe("useGraphStore loadGraphSnapshot", () => {
  beforeEach(() => {
    useGraphStore.setState({
      nodes: [],
      edges: [],
      preamble: "",
      submodels: {},
      lastSavedSnapshot: null,
      undoStack: [],
      redoStack: [],
      structuralVersion: 0,
      panelContextVersion: 0,
      dirty: false,
    })
  })

  it("atomically installs a clean saved baseline and clears both histories", () => {
    const first = snapshot("first", "import first", {
      first: { graph: { nodes: [makeNode("first-child")], edges: [] } },
    })
    useGraphStore.getState().loadGraphSnapshot(first)
    useGraphStore.getState().setNodes([...first.nodes, makeNode("edited")])
    useGraphStore.getState().undo()
    expect(useGraphStore.getState().redoStack).toHaveLength(1)

    const before = useGraphStore.getState()
    const loaded = snapshot("second", "import second", {
      second: { graph: { nodes: [makeNode("second-child")], edges: [] } },
    })
    useGraphStore.getState().loadGraphSnapshot(loaded)

    const state = useGraphStore.getState()
    expect({
      nodes: state.nodes,
      edges: state.edges,
      preamble: state.preamble,
      submodels: state.submodels,
    }).toEqual(loaded)
    expect(state.lastSavedSnapshot).toEqual(loaded)
    expect(state.lastSavedSnapshot).not.toBe(loaded)
    expect(state.nodes).not.toBe(loaded.nodes)
    expect(state.submodels).not.toBe(loaded.submodels)
    expect(state.undoStack).toEqual([])
    expect(state.redoStack).toEqual([])
    expect(state.dirty).toBe(false)
    expect(state.persistedFingerprint).toBe(serializeSnapshot(loaded))
    expect(state.savedPersistedFingerprint).toBe(state.persistedFingerprint)
    expect(state.structuralFingerprint).toBe(
      computeStructuralFingerprint(loaded.nodes, loaded.edges, loaded.preamble),
    )
    expect(state.panelContextFingerprint).toBe(
      computePanelContextFingerprint(loaded.nodes, loaded.edges),
    )
    expect(state.structuralVersion).toBe(before.structuralVersion + 1)
    expect(state.panelContextVersion).toBe(before.panelContextVersion + 1)

    useGraphStore.getState().undo()
    useGraphStore.getState().redo()
    expect(useGraphStore.getState().nodes.map((node) => node.id)).toEqual(["second"])

    loaded.nodes[0].data.label = "mutated outside the store"
    loaded.submodels.second = { graph: { nodes: [], edges: [] } }
    expect(useGraphStore.getState().nodes[0].data.label).not.toBe("mutated outside the store")
    expect(useGraphStore.getState().submodels).toEqual({
      second: { graph: { nodes: [makeNode("second-child")], edges: [] } },
    })
  })

  it("advances cache identities when only submodels change", () => {
    const base = snapshot("same", "import same", {
      nested: { graph: { nodes: [makeNode("old-child")], edges: [] } },
    })
    useGraphStore.getState().loadGraphSnapshot(base)
    const before = useGraphStore.getState()

    useGraphStore.getState().loadGraphSnapshot({
      ...base,
      submodels: {
        nested: { graph: { nodes: [makeNode("new-child")], edges: [] } },
      },
    })

    const state = useGraphStore.getState()
    expect(state.structuralFingerprint).toBe(before.structuralFingerprint)
    expect(state.panelContextFingerprint).toBe(before.panelContextFingerprint)
    expect(state.structuralVersion).toBe(before.structuralVersion + 1)
    expect(state.panelContextVersion).toBe(before.panelContextVersion + 1)
    expect(state.dirty).toBe(false)
  })

  it("preserves loaded runtime metadata only in the live graph", () => {
    const loaded = snapshot("cached", "", {})
    loaded.nodes[0].data._columns = [{ name: "premium", dtype: "i64" }]
    loaded.nodes[0].data._status = "ok"

    useGraphStore.getState().loadGraphSnapshot(loaded)

    const state = useGraphStore.getState()
    expect(state.nodes[0].data._columns).toEqual([{ name: "premium", dtype: "i64" }])
    expect(state.nodes[0].data._status).toBe("ok")
    expect(state.lastSavedSnapshot?.nodes[0].data._columns).toBeUndefined()
    expect(state.lastSavedSnapshot?.nodes[0].data._status).toBeUndefined()
    expect(state.dirty).toBe(false)
  })

  it("uses the loaded document as the undoable clean baseline for later edits", () => {
    const loaded = snapshot("loaded", "import original", {
      nested: { graph: { nodes: [makeNode("child")], edges: [] } },
    })
    useGraphStore.getState().loadGraphSnapshot(loaded)

    useGraphStore.getState().setPreamble("import edited")
    expect(useGraphStore.getState().dirty).toBe(true)
    expect(useGraphStore.getState().canUndo()).toBe(true)

    useGraphStore.getState().undo()
    const state = useGraphStore.getState()
    expect(state.preamble).toBe("import original")
    expect(state.submodels).toEqual(loaded.submodels)
    expect(state.dirty).toBe(false)
  })
})

describe("useGraphStore global constants", () => {
  const rate = { name: "rate", type: "float" as const, split: false, value: "1.5", bySource: {} }

  beforeEach(() => {
    useGraphStore.getState().resetForTests()
  })

  it("loading sets the constants and their load error as the clean baseline", () => {
    useGraphStore.getState().loadGraphSnapshot({
      ...snapshot("a", "", {}),
      globalConstants: [rate],
      globalConstantsError: "bad JSON",
    })

    const state = useGraphStore.getState()
    expect(state.globalConstants).toEqual([rate])
    expect(state.globalConstantsError).toBe("bad JSON")
    expect(state.dirty).toBe(false)
    expect(state.isDirty()).toBe(false)
  })

  it("an edit marks the pipeline unsaved, moves the structural version and pushes no undo entry", () => {
    useGraphStore.getState().loadGraphSnapshot({ ...snapshot("a", "", {}), globalConstants: [rate] })
    const before = useGraphStore.getState().structuralVersion

    useGraphStore.getState().setGlobalConstantsRaw([{ ...rate, value: "2" }])

    const state = useGraphStore.getState()
    expect(state.dirty).toBe(true)
    expect(state.isDirty()).toBe(true)
    expect(state.structuralVersion).toBe(before + 1)
    expect(state.undoStack).toEqual([])

    useGraphStore.getState().setGlobalConstantsRaw([rate])
    expect(useGraphStore.getState().dirty).toBe(false)
  })

  it("undo and redo of a graph edit leave the constants alone", () => {
    useGraphStore.getState().loadGraphSnapshot({ ...snapshot("a", "", {}), globalConstants: [rate] })
    useGraphStore.getState().setPreamble("import math")
    useGraphStore.getState().setGlobalConstantsRaw([{ ...rate, value: "2" }])

    useGraphStore.getState().undo()
    expect(useGraphStore.getState().preamble).toBe("")
    expect(useGraphStore.getState().globalConstants).toEqual([{ ...rate, value: "2" }])
    expect(useGraphStore.getState().dirty).toBe(true)

    useGraphStore.getState().redo()
    expect(useGraphStore.getState().globalConstants).toEqual([{ ...rate, value: "2" }])
  })

  it("marking a saved snapshot saves the constants it carries", () => {
    useGraphStore.getState().loadGraphSnapshot({ ...snapshot("a", "", {}), globalConstants: [rate] })
    const edited = [{ ...rate, value: "2" }]
    useGraphStore.getState().setGlobalConstantsRaw(edited)
    const state = useGraphStore.getState()

    state.markSaved({
      nodes: state.nodes,
      edges: state.edges,
      preamble: state.preamble,
      submodels: state.submodels,
      globalConstants: edited,
    })

    expect(useGraphStore.getState().dirty).toBe(false)
  })
})
