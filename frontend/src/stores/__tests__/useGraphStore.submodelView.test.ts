import { beforeEach, describe, expect, it } from "vitest"
import type { Node } from "@xyflow/react"
import useGraphStore, { type GraphSnapshot } from "../useGraphStore"
import { makeNode } from "../../test-utils/factories"

const root = (...ids: string[]): GraphSnapshot => ({
  nodes: ids.map((id) => makeNode(id)),
  edges: [],
  preamble: "",
  submodels: { definition: { graph: { nodes: [makeNode("child")], edges: [] } } },
})

const view = (...ids: string[]) => ({ nodes: ids.map((id) => makeNode(id)) as Node[], edges: [] })

const store = () => useGraphStore.getState()

/** The root as the boundary reconciler hands it back on exit. */
const exitWith = (snapshot: GraphSnapshot) =>
  store().exitSubmodelView({ nodes: snapshot.nodes, edges: snapshot.edges, submodels: snapshot.submodels })

describe("useGraphStore submodel views", () => {
  beforeEach(() => {
    store().resetForTests()
    store().loadGraphSnapshot(root("a", "b"))
  })

  it("is clean in an unchanged view when a save begun at the root lands while drilled", () => {
    store().setNodes(root("a", "b", "c").nodes)
    const saving = { ...root("a", "b", "c"), nodes: store().nodes }
    store().enterSubmodelView(view("child", "input", "output"))
    expect(store().dirty).toBe(true)

    store().markSaved(saving)
    expect(store().dirty).toBe(false)

    exitWith(saving)
    expect(store().dirty).toBe(false)
    expect(store().canUndo()).toBe(true)
  })

  it("stays dirty when the save that lands while drilled is not the root that was entered", () => {
    store().setNodes(root("a", "b", "c").nodes)
    store().enterSubmodelView(view("child", "input", "output"))

    store().markSaved(root("a", "b", "older"))
    expect(store().dirty).toBe(true)

    const older = root("a", "b", "older")
    exitWith(older)
    expect(store().dirty).toBe(false)
  })

  it("makes the root on exit the baseline only when leaving the view that was marked saved", () => {
    store().enterSubmodelView(view("child", "input", "output"))
    store().setNodes((nodes) => [...nodes, makeNode("child2")])
    store().markSaved()
    expect(store().dirty).toBe(false)
    exitWith(root("a", "b", "reconciled"))
    expect(store().dirty).toBe(false)

    store().enterSubmodelView(view("child", "input", "output"))
    store().markSaved()
    store().setNodes((nodes) => [...nodes, makeNode("after-mark")])
    exitWith(root("a", "b", "after-mark"))
    expect(store().dirty).toBe(true)
  })

  it("ends an open view on a document load, keeping only version-control history", () => {
    const vc = { label: "Switch branch", undo: async () => {}, redo: async () => {} }
    store().pushVcEntry(vc)
    store().setNodes(root("a", "b", "c").nodes)
    store().enterSubmodelView(view("child", "input", "output"))
    store().setNodes((nodes) => [...nodes, makeNode("child2")])

    store().loadGraphSnapshot(root("loaded"))

    expect(store().submodelView).toBeNull()
    expect(store().undoStack).toEqual([expect.objectContaining({ kind: "vc", label: "Switch branch" })])
    expect(store().nodes.map((node) => node.id)).toEqual(["loaded"])
    expect(() => store().enterSubmodelView(view("child"))).not.toThrow()
  })

  it("refuses to leave a view that is not open", () => {
    expect(() => exitWith(root("a"))).toThrow("No submodel is open")
  })
})
