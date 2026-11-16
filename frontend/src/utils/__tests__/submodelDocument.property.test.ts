/**
 * Property tests for an open submodel as a view of the one document: over seeded
 * random definitions and random sequences of the canvas's writes, the canvas stays
 * a projection of the document, each edit is one step, and Undo and Redo walk the
 * whole sequence back and forth.
 */
import { beforeEach, describe, expect, it } from "vitest"
import type { Node } from "@xyflow/react"
import useGraphStore from "../../stores/useGraphStore"
import useToastStore from "../../stores/useToastStore"
import { makeIdentifiedNode, makeNode } from "../../test-utils/factories"
import type { PipelineEdge, SubmodelDefinition } from "../../types/node"
import { canonicalJson } from "../canonicalJson"
import { authoredNodeForm } from "../graphSnapshot"
import { definitionEdgeForm, isSubmodelCard, projectSubmodelFocus } from "../submodelDocument"

const DEFINITION_ID = "definition_pricing"
const OPEN = { instanceId: "pricing", definitionId: DEFINITION_ID }
const SEEDS = Array.from({ length: 40 }, (_, index) => index + 1)
const STEPS_PER_CASE = 30

/** A small seeded generator, so a failing case reruns the same. */
function random(seed: number) {
  let state = seed >>> 0
  const next = () => {
    state = (state + 0x6d2b79f5) >>> 0
    let value = state
    value = Math.imul(value ^ (value >>> 15), value | 1)
    value ^= value + Math.imul(value ^ (value >>> 7), value | 61)
    return ((value ^ (value >>> 14)) >>> 0) / 4294967296
  }
  return {
    int: (below: number) => Math.floor(next() * below),
    pick: <T,>(items: readonly T[]): T => items[Math.floor(next() * items.length)]!,
  }
}

const store = () => useGraphStore.getState()
const definition = () => store().submodels[DEFINITION_ID] as SubmodelDefinition
const children = () => store().nodes.filter((node) => !isSubmodelCard(node))
const internalEdges = () => {
  const ids = new Set(children().map((node) => node.id))
  return store().edges.filter((edge) => ids.has(edge.source) && ids.has(edge.target))
}

function load(rng: ReturnType<typeof random>) {
  const count = 2 + rng.int(6)
  const nodes = Array.from({ length: count }, (_, index) =>
    makeIdentifiedNode(`c${index}`, "polars", { position: { x: 100 + index * 200, y: rng.int(400) } }))
  const edges: PipelineEdge[] = []
  for (let target = 1; target < count; target += 1) {
    const source = rng.int(target)
    edges.push({ id: `e_c${source}_c${target}`, source: `c${source}`, target: `c${target}`, data: { _inputName: `c${source}` } })
  }
  store().loadGraphSnapshot({
    nodes: [
      makeIdentifiedNode("api"),
      makeNode("pricing", "submodel", {
        data: { label: "pricing", nodeType: "submodel", config: { definition_id: DEFINITION_ID, alias: "pricing" } },
      }),
      makeIdentifiedNode("consumer"),
    ],
    edges: [
      { id: "feed", source: "api", target: "pricing", targetHandle: "in__policy" },
      { id: "consume", source: "pricing", sourceHandle: "out__premium", target: "consumer" },
    ],
    preamble: "",
    submodels: {
      [DEFINITION_ID]: {
        definitionId: DEFINITION_ID,
        file: "modules/pricing.py",
        graph: { nodes, edges },
        inputPorts: [{ name: "policy", targets: [{ nodeId: "c0", handleId: null }] }],
        outputPorts: [{ name: "premium", source: { nodeId: `c${count - 1}`, handleId: null } }],
      },
    },
  })
}

/** The ports' children are the boundary's: the canvas's writes leave them be. */
const portChildren = () => new Set([
  ...definition().inputPorts.flatMap((port) => port.targets.map((target) => target.nodeId)),
  ...definition().outputPorts.map((port) => port.source.nodeId),
])

type Write = { name: string; step: boolean; run: () => void } | null

/** One of the canvas's writes, chosen at random; null when none of its kind applies. */
function randomWrite(rng: ReturnType<typeof random>, serial: number): Write {
  const nodes = children()
  const target = rng.pick(nodes)
  switch (rng.int(8)) {
    case 0:
      return {
        name: `configure ${target.id}`,
        step: true,
        run: () => store().setNodes((all) => all.map((node) => (
          node.id === target.id ? { ...node, data: { ...node.data, config: { code: `df = df  # ${serial}` } } } : node))),
      }
    case 1:
      return {
        name: `add n${serial}`,
        step: true,
        run: () => store().setNodes((all) => [...all, makeIdentifiedNode(`n${serial}`, "polars", { position: { x: rng.int(900), y: rng.int(900) } })]),
      }
    case 2: {
      const deletable = nodes.filter((node) => !portChildren().has(node.id))
      if (deletable.length === 0) return null
      const gone = rng.pick(deletable).id
      return {
        name: `delete ${gone}`,
        step: true,
        run: () => store().setNodesAndEdges(
          (all) => all.filter((node) => node.id !== gone),
          (all) => all.filter((edge) => edge.source !== gone && edge.target !== gone),
        ),
      }
    }
    case 3: {
      const ids = nodes.map((node) => node.id)
      const source = rng.pick(ids)
      const sink = rng.pick(ids)
      if (source === sink || internalEdges().some((edge) => edge.source === source && edge.target === sink)) return null
      return {
        name: `connect ${source}->${sink}`,
        step: true,
        run: () => store().setEdges((all) => [...all, { id: `e${serial}`, source, target: sink, data: { _inputName: source } }]),
      }
    }
    case 4: {
      const edges = internalEdges()
      if (edges.length === 0) return null
      const gone = rng.pick(edges).id
      return { name: `disconnect ${gone}`, step: true, run: () => store().setEdges((all) => all.filter((edge) => edge.id !== gone)) }
    }
    case 5:
      return {
        name: `drag ${target.id}`,
        step: true,
        run: () => {
          const frame = (x: number, dragging: boolean) => store().setNodesRaw((all) => all.map((node) => (
            node.id === target.id ? { ...node, position: { x, y: target.position.y }, dragging } : node)))
          store().pushSnapshot()
          frame(target.position.x + 10, true)
          frame(target.position.x + 37, true)
          frame(target.position.x + 37, false)
        },
      }
    case 6:
      return {
        name: `select ${target.id}`,
        step: false,
        run: () => store().setNodesRaw((all) => all.map((node) => ({ ...node, selected: node.id === target.id }))),
      }
    default:
      return {
        name: `preview ${target.id}`,
        step: false,
        run: () => store().setNodesRaw((all) => all.map((node) => (
          node.id === target.id ? { ...node, data: { ...node.data, _columns: [{ name: `col${serial}`, dtype: "Int64" }] } } : node))),
      }
  }
}

const authoredSet = (nodes: readonly Node[]) =>
  canonicalJson(nodes.map(authoredNodeForm).sort((a, b) => (a.id < b.id ? -1 : 1)))
const authoredEdgeSet = (edges: readonly PipelineEdge[]) =>
  canonicalJson(edges.map(definitionEdgeForm).sort((a, b) => (a.id < b.id ? -1 : 1)))

/** The canvas is the document's projection, and the definition holds no canvas state. */
function expectCanvasIsTheDocument(label: string) {
  const { focus, submodels, submodelAnnotations, nodes, edges } = store()
  expect(authoredSet(children()), label).toBe(authoredSet(definition().graph.nodes))
  expect(authoredEdgeSet(internalEdges() as PipelineEdge[]), label).toBe(authoredEdgeSet(definition().graph.edges as PipelineEdge[]))
  for (const node of definition().graph.nodes) {
    expect(node, label).not.toHaveProperty("selected")
    expect(Object.keys(node.data).filter((key) => key === "_columns"), label).toEqual([])
  }
  const projected = projectSubmodelFocus({
    rootNodes: focus!.rootNodes,
    rootEdges: focus!.rootEdges,
    submodels,
    focus: OPEN,
    annotations: submodelAnnotations[OPEN.instanceId],
    previous: { nodes, edges },
  })
  expect(authoredSet(projected.nodes.filter((node) => !isSubmodelCard(node))), label).toBe(authoredSet(children()))
}

describe("an open submodel as a view of the one document (property)", () => {
  beforeEach(() => {
    store().resetForTests()
    useToastStore.setState({ toasts: [] })
  })

  it.each(SEEDS)("keeps the canvas the document's projection, one step per edit, Undo and Redo walking it all (seed %i)", (seed) => {
    const rng = random(seed)
    load(rng)
    store().enterSubmodelView(OPEN)
    const initial = store().persistedFingerprint
    expectCanvasIsTheDocument(`seed ${seed}: opened`)

    // Projecting the document and taking the projection back changes nothing.
    store().setNodes((nodes) => [...nodes])
    expect(store().persistedFingerprint).toBe(initial)
    expect(store().canUndo()).toBe(false)

    let steps = 0
    for (let serial = 0; serial < STEPS_PER_CASE; serial += 1) {
      const write = randomWrite(rng, serial)
      if (write === null) continue
      const before = store().persistedFingerprint
      const undoBefore = store().undoStack.length
      write.run()
      const label = `seed ${seed}: #${serial} ${write.name}`
      expectCanvasIsTheDocument(label)
      const changed = store().persistedFingerprint !== before
      expect(store().undoStack.length, label).toBe(undoBefore + (write.step && changed ? 1 : 0))
      if (!write.step) expect(changed, label).toBe(false)
      if (write.step && changed) steps += 1
    }
    expect(useToastStore.getState().toasts, `seed ${seed}: no write refused`).toEqual([])
    const final = store().persistedFingerprint

    for (let step = 0; step < steps; step += 1) store().undo()
    expect(store().persistedFingerprint, `seed ${seed}: undone`).toBe(initial)
    expect(store().focus).toMatchObject(OPEN)
    expectCanvasIsTheDocument(`seed ${seed}: undone`)
    for (let step = 0; step < steps; step += 1) store().redo()
    expect(store().persistedFingerprint, `seed ${seed}: redone`).toBe(final)
    expectCanvasIsTheDocument(`seed ${seed}: redone`)
  })
})
