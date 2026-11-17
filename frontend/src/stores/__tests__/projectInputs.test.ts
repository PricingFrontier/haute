/**
 * The browser's readers of what a result was computed from (CCH-06, specs/caching "Stale
 * results"): one key for a derived request and one notifier for a file a run reads, so no
 * kept answer is left on a list of its own that misses an input.
 */
import { readdirSync, readFileSync, statSync } from "node:fs"
import path from "node:path"
import { fileURLToPath } from "node:url"
import { beforeEach, describe, expect, it } from "vitest"
import useDataEpochStore from "../useDataEpochStore"
import useNodeDataStore from "../useNodeDataStore"
import useGraphStore, { computeStructuralFingerprint, derivedRequestKey, type DerivedRequestDocument } from "../useGraphStore"
import { noteProjectInputsChanged } from "../projectInputs"
import type { GlobalConstantDraft } from "../../utils/globalConstants"

const node = (id: string, code: string) => ({ id, data: { nodeType: "polars", config: { code } } })
const edge = (id: string, source: string, target: string) => ({ id, source, target })
const rate: GlobalConstantDraft = { name: "rate", type: "float", split: false, value: "1", bySource: {} }

const document = (overrides: Partial<DerivedRequestDocument> = {}): DerivedRequestDocument => ({
  nodes: [node("a", "df = x"), node("b", "df = y"), node("load", "df = df"), node("opt", "")],
  edges: [edge("e1", "a", "load"), edge("e2", "b", "load"), edge("e3", "load", "opt")],
  preamble: "X = 1",
  globalConstants: [rate],
  submodels: {},
  ...overrides,
})

describe("derivedRequestKey", () => {
  const base = derivedRequestKey(document(), "opt")

  it("moves with an upstream node, a constant, the preamble and a node's input order", () => {
    const moved = [
      document({ nodes: [node("a", "df = x * 2"), ...document().nodes.slice(1)] }),
      document({ globalConstants: [{ ...rate, value: "2" }] }),
      document({ preamble: "X = 2" }),
      document({ edges: [edge("e2", "b", "load"), edge("e1", "a", "load"), edge("e3", "load", "opt")] }),
    ]
    for (const changed of moved) expect(derivedRequestKey(changed, "opt")).not.toBe(base)
  })

  it("leaves the node's own settings, and the order of edges into different nodes, to the caller", () => {
    const own = document({ nodes: [...document().nodes.slice(0, 3), node("opt", "df = anything")] })
    expect(derivedRequestKey(own, "opt")).toBe(base)
    const reordered = document({ edges: [edge("e3", "load", "opt"), edge("e1", "a", "load"), edge("e2", "b", "load")] })
    expect(derivedRequestKey(reordered, "opt")).toBe(base)
  })

  it("is the structural fingerprint when no node is left out", () => {
    const whole = document()
    expect(derivedRequestKey(whole)).toBe(computeStructuralFingerprint(
      whole.nodes, whole.edges, whole.preamble, whole.globalConstants, whole.submodels,
    ))
  })
})

describe("noteProjectInputsChanged", () => {
  beforeEach(() => {
    useGraphStore.getState().resetForTests()
    useDataEpochStore.setState({ epoch: 0 })
  })

  it("moves every browser clock a kept answer is checked against", () => {
    const structural = useGraphStore.getState().structuralVersion
    const nodeData = useNodeDataStore.getState().epoch
    noteProjectInputsChanged()
    expect(useDataEpochStore.getState().epoch).toBe(1)
    expect(useGraphStore.getState().structuralVersion).toBe(structural + 1)
    expect(useNodeDataStore.getState().epoch).toBe(nodeData + 1)
  })

  it("leaves the graph, its history and its dirty state as they are", () => {
    const before = useGraphStore.getState()
    noteProjectInputsChanged()
    const after = useGraphStore.getState()
    expect(after.nodes).toBe(before.nodes)
    expect(after.undoStack).toBe(before.undoStack)
    expect(after.dirty).toBe(before.dirty)
  })
})

describe("the graph's structural version", () => {
  beforeEach(() => useGraphStore.getState().resetForTests())

  it("advances when a node's inputs are connected the other way round", () => {
    const load = { id: "load", position: { x: 0, y: 0 }, data: { label: "load", nodeType: "externalFile", config: {} } }
    const a = { ...load, id: "a" }
    const b = { ...load, id: "b" }
    useGraphStore.getState().setNodesRaw([a, b, load] as never)
    useGraphStore.getState().setEdges([edge("e1", "a", "load"), edge("e2", "b", "load")] as never)
    const version = useGraphStore.getState().structuralVersion
    useGraphStore.getState().setEdges([edge("e2", "b", "load"), edge("e1", "a", "load")] as never)
    expect(useGraphStore.getState().structuralVersion).toBe(version + 1)
  })
})

// ---------------------------------------------------------------------------
// One key, one notifier
// ---------------------------------------------------------------------------

const src = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..")

function sources(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const full = path.join(dir, name)
    if (statSync(full).isDirectory()) return name === "__tests__" ? [] : sources(full)
    return /\.(ts|tsx)$/.test(name) && !/\.test\.(ts|tsx)$/.test(name) ? [full] : []
  })
}

const relative = (file: string) => path.relative(src, file).split(path.sep).join("/")
const files = sources(src).map((file) => ({ file: relative(file), text: readFileSync(file, "utf8") }))

describe("the browser's readers of a run's inputs", () => {
  it("move the data epoch only through the one notifier", () => {
    const movers = files.filter(({ text }) => /useDataEpochStore\.getState\(\)\.moved\(/.test(text))
    expect(movers.map(({ file }) => file)).toEqual(["stores/projectInputs.ts"])
  })

  it("order the edges of an answer's identity only through inputOrderedEdges", () => {
    // A key that sorts a node's incoming edges signs two different computations alike.
    const sortedEdges = /\.map\(\(\s*(?:edge|e)\b[^)]*\)\s*=>(?:(?!const |return |\n\s*\n)[\s\S]){0,400}?\.target(?:(?!const |return |\n\s*\n)[\s\S]){0,300}?\.sort\(/
    const notAnswers: Record<string, string> = {
      "hooks/useNodeHandlers.ts": "the canvas layout's topology, which computes nothing",
      "stores/useGraphStore.ts": "the panel context key, which keeps no computed answer",
      "utils/structuralFingerprint.ts": "a submodel boundary edit's identity, which keeps no computed answer",
    }
    const sorters = files.filter(({ text }) => sortedEdges.test(text)).map(({ file }) => file)
    expect(sorters.filter((file) => !(file in notAnswers))).toEqual([])
    const users = files.filter(({ text }) => /\binputOrderedEdges\(/.test(text)).map(({ file }) => file).sort()
    expect(users).toEqual([
      "panels/dataPointIdentity.ts",
      "stores/useGraphStore.ts",
      "utils/trainedJobHandles.ts",
    ])
  })

  it("key a request by node data only through the graph store", () => {
    // A panel that hashes node data itself builds a request key of its own, which leaves out
    // whatever its author forgot (the constants, the preamble, a node's input order).
    const hashers = files.filter(({ text }) => /\bshallowNodeDataHash\b/.test(text))
    expect(hashers.map(({ file }) => file).sort()).toEqual(["stores/useGraphStore.ts", "utils/shallowNodeHash.ts"])
  })
})
