import { beforeEach, describe, expect, it } from "vitest"
import type { Node } from "@xyflow/react"
import useGraphStore from "../useGraphStore"
import useToastStore from "../useToastStore"
import { makeIdentifiedNode, makeNode } from "../../test-utils/factories"
import type { PipelineEdge, SubmodelDefinition } from "../../types/node"

const DEFINITION_ID = "definition_pricing"
const OPEN = { instanceId: "pricing", definitionId: DEFINITION_ID }
const COPY = { instanceId: "pricing_2", definitionId: DEFINITION_ID }

function occurrence(id: string, instanceOf?: string): Node {
  return makeNode(id, "submodel", {
    data: {
      label: id,
      nodeType: "submodel",
      config: { definition_id: DEFINITION_ID, alias: id, ...(instanceOf ? { instance_of: instanceOf } : {}) },
    },
  })
}

function definition(children: Node[]): SubmodelDefinition {
  return {
    definitionId: DEFINITION_ID,
    file: "modules/pricing.py",
    graph: {
      nodes: children,
      edges: [{ id: "e_prepare_score", source: "prepare", target: "score", data: { _inputName: "prepare" } }],
    },
    inputPorts: [{ name: "policy", targets: [{ nodeId: "prepare", handleId: null }] }],
    outputPorts: [{ name: "premium", source: { nodeId: "score", handleId: null } }],
  }
}

function load(extraChildren: Node[] = []) {
  const rootEdges: PipelineEdge[] = [
    { id: "feed", source: "api", target: "pricing", targetHandle: "in__policy" },
    { id: "consume", source: "pricing", sourceHandle: "out__premium", target: "consumer" },
  ]
  store().loadGraphSnapshot({
    nodes: [makeIdentifiedNode("api"), occurrence("pricing"), occurrence("pricing_2", "pricing"), makeIdentifiedNode("consumer")],
    edges: rootEdges,
    preamble: "",
    submodels: {
      [DEFINITION_ID]: definition([
        makeIdentifiedNode("prepare", "polars", { position: { x: 100, y: 100 } }),
        makeIdentifiedNode("score", "polars", { position: { x: 400, y: 100 } }),
        ...extraChildren,
      ]),
    },
  })
}

const store = () => useGraphStore.getState()
const openDefinition = () => store().submodels[DEFINITION_ID] as SubmodelDefinition
const child = (id: string) => openDefinition().graph.nodes.find((node) => node.id === id)!
const onCanvas = (id: string) => store().nodes.find((node) => node.id === id)!
const configure = (id: string, config: Record<string, unknown>) =>
  store().setNodes((nodes) => nodes.map((node) => (node.id === id ? { ...node, data: { ...node.data, config } } : node)))
const lastToast = () => useToastStore.getState().toasts.at(-1)?.text

describe("useGraphStore with a submodel open", () => {
  beforeEach(() => {
    store().resetForTests()
    useToastStore.setState({ toasts: [] })
    load()
  })

  it("shows an occurrence drawn from the document, changing neither history nor the saved state", () => {
    const before = store().persistedFingerprint
    const serial = store().navigationSerial

    store().enterSubmodelView(OPEN)

    expect(store().focus).toMatchObject({ ...OPEN, readOnly: false })
    expect(store().nodes.map((node) => node.id)).toEqual(expect.arrayContaining(["prepare", "score"]))
    expect(store().nodes.filter((node) => node.type === "submodelPort")).toHaveLength(2)
    expect(store().persistedFingerprint).toBe(before)
    expect(store().dirty).toBe(false)
    expect(store().canUndo()).toBe(false)
    expect(store().navigationSerial).toBe(serial + 1)
    expect(() => store().enterSubmodelView(OPEN)).toThrow("already open")
  })

  it("makes an edit inside the submodel one step of the document", () => {
    store().enterSubmodelView(OPEN)

    configure("score", { how: "left" })

    expect(child("score").data.config).toEqual({ how: "left" })
    expect(store().focus?.rootNodes.map((node) => node.id)).toEqual(["api", "pricing", "pricing_2", "consumer"])
    expect(store().undoStack).toHaveLength(1)
    expect(store().dirty).toBe(true)

    store().undo()
    expect(child("score").data.config).toEqual({})
    expect(onCanvas("score").data.config).toEqual({})
    expect(store().dirty).toBe(false)

    store().redo()
    expect(child("score").data.config).toEqual({ how: "left" })
    expect(store().focus).toMatchObject(OPEN)
  })

  it("undoes a step made in a submodel from the main pipeline by showing that submodel, and redoes it there", () => {
    store().enterSubmodelView(OPEN)
    configure("score", { how: "left" })
    store().exitSubmodelView()
    const serial = store().navigationSerial

    store().undo()

    expect(store().focus).toMatchObject(OPEN)
    expect(onCanvas("score").data.config).toEqual({})
    expect(store().navigationSerial).toBe(serial + 1)

    store().exitSubmodelView()
    store().redo()

    expect(store().focus).toMatchObject(OPEN)
    expect(child("score").data.config).toEqual({ how: "left" })
  })

  it("undoes a step made in the main pipeline from inside a submodel by showing the main pipeline", () => {
    store().setNodes((nodes) => nodes.map((node) => (node.id === "api" ? { ...node, data: { ...node.data, label: "Policies" } } : node)))
    store().enterSubmodelView(OPEN)

    store().undo()

    expect(store().focus).toBeNull()
    expect(onCanvas("api").data.label).toBe("Node api")
  })

  it("shows the main pipeline, with a notice, when the submodel a step was made in is gone", () => {
    const entry = { nodes: store().nodes.filter((node) => node.id !== "pricing"), edges: [], preamble: "", submodels: store().submodels, globalConstants: [], focus: OPEN }
    useGraphStore.setState({ undoStack: [entry] })
    store().enterSubmodelView(OPEN)

    store().undo()

    expect(store().focus).toBeNull()
    expect(store().nodes.map((node) => node.id)).not.toContain("pricing")
    expect(lastToast()).toBe("The submodel that step was made in is gone, so the main pipeline is shown.")
  })

  it("refuses an authored change to a read-only copy, whole, while its canvas still takes a selection", () => {
    store().enterSubmodelView(COPY)
    const before = store().persistedFingerprint

    configure("score", { how: "left" })

    expect(lastToast()).toContain("read-only")
    expect(onCanvas("score").data.config).toEqual({})
    expect(store().persistedFingerprint).toBe(before)
    expect(store().canUndo()).toBe(false)

    store().setNodesRaw((nodes) => nodes.map((node) => (node.id === "score" ? { ...node, selected: true } : node)))
    expect(onCanvas("score").selected).toBe(true)
    expect(store().persistedFingerprint).toBe(before)
  })

  it("refuses a canvas write that adds or removes a boundary edge, leaving the gesture no step", () => {
    store().enterSubmodelView(OPEN)
    const boundary = store().edges.find((edge) => edge.target === "prepare")!

    store().pushSnapshot()
    store().setEdgesRaw((edges) => edges.filter((edge) => edge.id !== boundary.id))

    expect(lastToast()).toContain("boundary")
    expect(store().edges.map((edge) => edge.id)).toContain(boundary.id)
    expect(store().canUndo()).toBe(false)

    // A later write of its own is its own step, not the refused gesture's.
    configure("score", { how: "left" })
    expect(store().undoStack).toHaveLength(1)
    store().undo()
    expect(child("score").data.config).toEqual({})
  })

  it("makes a drag one step that moves the child in the definition when it ends", () => {
    store().enterSubmodelView(OPEN)
    const move = (x: number, dragging: boolean) =>
      store().setNodesRaw((nodes) => nodes.map((node) => (node.id === "prepare" ? { ...node, position: { x, y: 100 }, dragging } : node)))

    store().pushSnapshot()
    move(120, true)
    move(160, true)
    expect(child("prepare").position).toEqual({ x: 100, y: 100 })
    move(160, false)

    expect(child("prepare").position).toEqual({ x: 160, y: 100 })
    expect(store().undoStack).toHaveLength(1)
    store().undo()
    expect(child("prepare").position).toEqual({ x: 100, y: 100 })
    expect(onCanvas("prepare").position).toEqual({ x: 100, y: 100 })
  })

  it("keeps the frames of a drag across 200 children to the canvas until it ends", () => {
    const many = Array.from({ length: 200 }, (_, index) =>
      makeIdentifiedNode(`step_${index}`, "polars", { position: { x: index * 10, y: 400 } }))
    load(many)
    store().enterSubmodelView(OPEN)
    const settled = {
      structuralVersion: store().structuralVersion,
      persistedFingerprint: store().persistedFingerprint,
      submodels: store().submodels,
    }
    let notifications = 0
    const unsubscribe = useGraphStore.subscribe(() => { notifications += 1 })

    const untouched = store().nodes.filter((node) => node.id !== "step_7")
    store().pushSnapshot()
    for (let frame = 1; frame <= 50; frame += 1) {
      store().setNodesRaw((nodes) => nodes.map((node) => (
        node.id === "step_7" ? { ...node, position: { x: 70 + frame, y: 400 }, dragging: true } : node
      )))
      // Every other node keeps its object, so only the dragged one re-renders.
      expect(store().nodes.filter((node) => node.id !== "step_7").every((node, index) => node === untouched[index])).toBe(true)
      expect(store().structuralVersion).toBe(settled.structuralVersion)
      expect(store().persistedFingerprint).toBe(settled.persistedFingerprint)
      expect(store().submodels).toBe(settled.submodels)
    }
    store().setNodesRaw((nodes) => nodes.map((node) => (node.id === "step_7" ? { ...node, dragging: false } : node)))
    unsubscribe()

    expect(notifications).toBe(51)
    expect(child("step_7").position).toEqual({ x: 120, y: 400 })
    expect(store().undoStack).toHaveLength(1)
  })

  it("keeps a preview's columns on the occurrence's view, out of the definition", () => {
    store().enterSubmodelView(OPEN)
    const before = store().persistedFingerprint
    const columns = [{ name: "premium", dtype: "Float64" }]

    store().setNodesRaw((nodes) => nodes.map((node) => (node.id === "score" ? { ...node, data: { ...node.data, _columns: columns } } : node)))

    expect(child("score").data).not.toHaveProperty("_columns")
    expect(store().persistedFingerprint).toBe(before)
    expect(store().canUndo()).toBe(false)

    store().exitSubmodelView()
    store().enterSubmodelView(OPEN)
    expect(onCanvas("score").data._columns).toEqual(columns)
    store().exitSubmodelView()
    store().enterSubmodelView(COPY)
    expect(onCanvas("score").data).not.toHaveProperty("_columns")
  })

  it("is clean after an edit inside the submodel is saved, and dirty again after the next", () => {
    store().enterSubmodelView(OPEN)
    configure("score", { how: "left" })

    store().markSaved()
    expect(store().dirty).toBe(false)
    expect(store().isDirty()).toBe(false)

    configure("score", { how: "inner" })
    expect(store().dirty).toBe(true)
    store().undo()
    expect(store().dirty).toBe(false)
  })

  it("writes the pipeline's own nodes behind the open submodel without a step", () => {
    store().enterSubmodelView(OPEN)

    store().setRootNodesRaw((nodes) => nodes.map((node) => (node.id === "api" ? { ...node, data: { ...node.data, _columns: [] } } : node)))

    expect(store().focus?.rootNodes.find((node) => node.id === "api")?.data._columns).toEqual([])
    expect(store().canUndo()).toBe(false)
  })

  it("closes the open submodel on a document load, keeping only version-control history", () => {
    store().pushVcEntry({ label: "Switch branch", undo: async () => {}, redo: async () => {} })
    store().enterSubmodelView(OPEN)
    configure("score", { how: "left" })
    const serial = store().navigationSerial

    load()

    expect(store().focus).toBeNull()
    expect(store().navigationSerial).toBe(serial + 1)
    expect(store().undoStack).toEqual([expect.objectContaining({ kind: "vc", label: "Switch branch" })])
    expect(() => store().enterSubmodelView(OPEN)).not.toThrow()
  })

  it("keeps a boundary edit's definition in its authored form, its annotations on the view", () => {
    store().enterSubmodelView(OPEN)
    const columns = [{ name: "premium", dtype: "Float64" }]
    // The rebuild a boundary action runs hands back canvas objects as the definition's children.
    const viewNodes = store().nodes.map((node) => (node.id === "score" ? { ...node, selected: true, data: { ...node.data, _columns: columns } } : node))
    const children = viewNodes.filter((node) => node.type !== "submodelPort")
    const focus = store().focus!

    store().commitSubmodelBoundaryEdit({
      viewNodes,
      viewEdges: store().edges,
      parentNodes: focus.rootNodes,
      parentEdges: focus.rootEdges,
      submodels: { [DEFINITION_ID]: { ...openDefinition(), graph: { ...openDefinition().graph, nodes: children }, inputPorts: [] } },
    })

    expect(child("score")).not.toHaveProperty("selected")
    expect(child("score").data).not.toHaveProperty("_columns")
    expect(onCanvas("score").data._columns).toEqual(columns)
    expect(store().submodelAnnotations[OPEN.instanceId]).toEqual({ score: { _columns: columns } })
    expect(store().undoStack).toHaveLength(1)
  })

  it("refuses a boundary edit that would change a read-only copy's definition", () => {
    store().enterSubmodelView(COPY)
    const focus = store().focus!
    const before = store().persistedFingerprint

    store().commitSubmodelBoundaryEdit({
      viewNodes: store().nodes,
      viewEdges: store().edges,
      parentNodes: focus.rootNodes,
      parentEdges: focus.rootEdges,
      submodels: { [DEFINITION_ID]: { ...openDefinition(), inputPorts: [] } },
    })

    expect(lastToast()).toContain("read-only")
    expect(openDefinition().inputPorts).toHaveLength(1)
    expect(store().persistedFingerprint).toBe(before)
    expect(store().canUndo()).toBe(false)
  })

  it("puts back the canvas when the write that ends a drag is refused, and takes the next write as usual", () => {
    store().enterSubmodelView(COPY)
    const move = (x: number, dragging: boolean) =>
      store().setNodesRaw((nodes) => nodes.map((node) => (node.id === "prepare" ? { ...node, position: { x, y: 100 }, dragging } : node)))

    store().pushSnapshot()
    move(160, true)
    move(160, false)

    expect(lastToast()).toContain("read-only")
    expect(onCanvas("prepare").position).toEqual({ x: 100, y: 100 })
    expect(onCanvas("prepare").dragging).toBeFalsy()
    store().setNodesRaw((nodes) => nodes.map((node) => (node.id === "prepare" ? { ...node, selected: true } : node)))
    expect(onCanvas("prepare").selected).toBe(true)
    expect(store().canUndo()).toBe(false)
  })

  it("ends a gesture that changed nothing, so a later write records no step of it", () => {
    store().enterSubmodelView(OPEN)
    const card = store().nodes.find((node) => node.type === "submodelPort")!

    // A card moved: the canvas's alone.
    store().pushSnapshot()
    store().setNodesRaw((nodes) => nodes.map((node) => (node.id === card.id ? { ...node, position: { x: -500, y: 0 } } : node)))
    expect(store().canUndo()).toBe(false)

    // A raw write after it is no part of that gesture.
    store().setNodesRaw((nodes) => nodes.map((node) => (node.id === "score" ? { ...node, data: { ...node.data, config: { how: "left" } } } : node)))
    expect(child("score").data.config).toEqual({ how: "left" })
    expect(store().canUndo()).toBe(false)
  })

  it("advances the semantic generation with every change but a move, and with every Undo, Redo, view change and load", () => {
    const generation = () => store().semanticGeneration
    const advances = (action: () => void) => {
      const before = generation()
      action()
      return generation() > before
    }
    const moveRoot = (x: number) => store().setNodes((nodes) => nodes.map((node) => (node.id === "api" ? { ...node, position: { x, y: 0 } } : node)))

    expect(advances(() => moveRoot(40))).toBe(false)
    expect(advances(() => store().setNodesRaw((nodes) => nodes.map((node) => ({ ...node, selected: true }))))).toBe(false)
    expect(advances(() => store().setNodes((nodes) => nodes.map((node) => (node.id === "api" ? { ...node, data: { ...node.data, label: "Policies" } } : node))))).toBe(true)
    expect(advances(() => store().enterSubmodelView(OPEN))).toBe(true)
    expect(advances(() => store().setNodesRaw((nodes) => nodes.map((node) => (node.id === "score" ? { ...node, position: { x: 450, y: 120 } } : node))))).toBe(false)
    expect(advances(() => store().setNodesRaw((nodes) => nodes.map((node) => (node.id === "score" ? { ...node, data: { ...node.data, _columns: [] } } : node))))).toBe(false)
    expect(advances(() => configure("score", { how: "left" }))).toBe(true)
    expect(advances(() => store().undo())).toBe(true)
    expect(advances(() => store().redo())).toBe(true)
    expect(advances(() => store().exitSubmodelView())).toBe(true)
    expect(advances(() => load())).toBe(true)
  })

  it("refuses to leave a view that is not open", () => {
    expect(() => store().exitSubmodelView()).toThrow("No submodel is open")
  })
})
