import { readdirSync, readFileSync } from "node:fs"
import { dirname, relative, resolve } from "node:path"
import { fileURLToPath } from "node:url"
import type { Node } from "@xyflow/react"
import { describe, expect, it } from "vitest"

import {
  documentRequestGraph,
  savedCodeForSteps,
  savedFormsFor,
  withoutSavedForms,
  withSavedAddresses,
  withSavedForms,
  withSavedParts,
} from "../documentRequest"

const steps = [
  { id: "s", kind: "source", input: "quotes" },
  { id: "w", kind: "with_column", name: "relativity", expr: { type: "operand", operand: { kind: "literal", type: "number", value: 1 } } },
]
const savedCode = 'df = quotes\ndf = df.with_columns(pl.lit(1.0).alias("relativity"))'

function transform(id: string, config: Record<string, unknown>, editor?: Record<string, unknown>): Node {
  return {
    id,
    position: { x: 0, y: 0 },
    data: { nodeType: "polars", label: id, config, ...(editor === undefined ? {} : { editor }) },
  }
}

function definition(nodes: Node[]) {
  return { definitionId: "sub", file: "modules/sub.py", graph: { nodes, edges: [] }, inputPorts: [], outputPorts: [] }
}

function config(node: Node): Record<string, unknown> {
  return node.data.config as Record<string, unknown>
}

function editor(node: Node): Record<string, unknown> | undefined {
  return node.data.editor as Record<string, unknown> | undefined
}

const live = { steps_error: null, steps_discarded: null }
// As the document gives its nodes: settings held as a browser holds them (`1.0` is `1`), and
// the saved form of each part as the server wrote it.
const baseline = {
  nodes: [
    transform("t", { steps, code: savedCode, rate: 1 }, { ...live, saved_code: savedCode, saved_settings: '{"rate":1.0}' }),
    transform("plain", { code: "df = quotes" }, { ...live, saved_settings: "{}" }),
  ],
  submodels: {
    sub: definition([transform("t", { steps, code: "df = inner" }, { ...live, saved_code: "df = inner", saved_settings: "{}" })]),
  },
}

describe("document request", () => {
  it("sends each part as the baseline holds it with its saved form, whatever the live node holds", () => {
    // Undo and Redo restore a stale rendering; the browser's own lost the decimal.
    const document = {
      nodes: [transform("t", { steps: structuredClone(steps), code: 'df = df.with_columns(pl.lit(1).alias("relativity"))', rate: 1 }, live)],
      submodels: { sub: definition([transform("t", { steps: structuredClone(steps), code: "stale" }, live)]) },
    }

    const sent = withSavedForms(document, baseline)

    expect(config(sent.nodes[0]).code).toBe(savedCode)
    expect(editor(sent.nodes[0])).toEqual({ ...live, saved_code: savedCode, saved_settings: '{"rate":1.0}' })
    const inner = (sent.submodels.sub as ReturnType<typeof definition>).graph.nodes[0]
    expect(config(inner).code).toBe("df = inner")
    expect(editor(inner)?.saved_code).toBe("df = inner")
  })

  it("sends an edited part, a node the baseline does not hold, and every node of a document never saved without one", () => {
    const changed = [...steps, { id: "l", kind: "limit", n: 5 }]
    const document = {
      nodes: [
        transform("t", { steps: changed, code: savedCode, rate: 1 }),
        transform("plain", { code: "df = edited", rate: 2 }),
        transform("new", { steps, code: "" }),
      ],
      submodels: { sub: definition([transform("t", { steps: changed, code: "df = inner" })]) },
    }

    expect(savedFormsFor(baseline, document, null, document.nodes[0])).toEqual({ saved_settings: '{"rate":1.0}' })
    // Edited settings go with the saved ones they began from, which keep the spelling of every
    // number the edit left (CGEN-09).
    expect(savedFormsFor(baseline, document, null, document.nodes[1])).toEqual({ settings_edited_from: "{}" })
    expect(savedFormsFor(baseline, document, null, document.nodes[2])).toEqual({})
    expect(savedFormsFor(baseline, document, "sub", document.submodels.sub.graph.nodes[0])).toEqual({ saved_settings: "{}" })
    expect(document.nodes.map((node) => savedFormsFor(null, document, null, node))).toEqual([{}, {}, {}])
  })

  it("compares a part by its content, not its key order", () => {
    const reordered = steps.map((step) => Object.fromEntries(Object.entries(step).reverse()))

    expect(savedCodeForSteps(baseline, null, "t", reordered)).toBe(savedCode)
    expect(savedCodeForSteps(baseline, "sub", "t", steps)).toBe("df = inner")
    expect(savedCodeForSteps(baseline, null, "plain", steps)).toBeNull()
  })

  it("reads every part of a node the baseline holds without its saved forms", () => {
    // A baseline the canvas was marked saved with (a discarded edit before the document is asked again).
    const bare = { nodes: [transform("t", { steps, code: savedCode, rate: 1 }, live)], submodels: {} }

    expect(savedFormsFor(bare, bare, null, bare.nodes[0])).toEqual({})
    expect(savedCodeForSteps(bare, null, "t", steps)).toBeNull()
  })

  it("builds a request graph that carries only the baseline's saved forms", () => {
    // A copy carried by the input itself never reaches the request.
    const stale = transform("t", { steps, code: savedCode, rate: 2 }, { ...live, saved_settings: '{"rate":1.0}' })

    const sent = documentRequestGraph({ nodes: [stale], edges: [] }, baseline)

    expect(editor(sent.nodes[0])).toEqual({ ...live, saved_code: savedCode, settings_edited_from: '{"rate":1.0}' })
    expect(editor(documentRequestGraph({ nodes: [stale], edges: [] }, null).nodes[0])).toEqual(live)
  })

  it("puts the saved form of each part the server wrote in the baseline it becomes, and keeps the rest", () => {
    const sent = withSavedForms(baseline, baseline)
    const saved = withSavedParts(sent, [
      { submodel: "sub", node_id: "t", code: "df = written", steps: null, settings: null },
      { submodel: null, node_id: "plain", code: null, steps: null, settings: '{"rate":2}' },
    ])

    const inner = (saved.submodels.sub as ReturnType<typeof definition>).graph.nodes[0]
    expect(config(inner).code).toBe("df = written")
    expect(editor(inner)).toEqual({ ...live, saved_code: "df = written", saved_settings: "{}" })
    expect(editor(saved.nodes[1])?.saved_settings).toBe('{"rate":2}')
    expect(editor(saved.nodes[0])).toEqual(editor(baseline.nodes[0]))
  })

  it("reads the steps of a node whose inputs changed: its saved code was rendered against them", () => {
    const edge = { id: "e", source: "quotes", target: "t", sourceHandle: null, targetHandle: null }
    const held = { ...baseline, edges: [edge] }
    const node = transform("t", { steps, code: savedCode, rate: 1 })
    const rate = { saved_settings: '{"rate":1.0}' }

    expect(savedFormsFor(held, { nodes: [node], edges: [edge] }, null, node)).toEqual({ saved_code: savedCode, ...rate })
    expect(savedFormsFor(held, { nodes: [node], edges: [] }, null, node)).toEqual(rate)
    const moved = { ...edge, source: "rates" }
    expect(savedFormsFor(held, { nodes: [node], edges: [moved] }, null, node)).toEqual(rate)
    // A Data Input's steps name no input: its edges are not its steps'.
    const hook = { ...node, data: { ...node.data, nodeType: "dataInput" } }
    const heldHook = { nodes: [{ ...held.nodes[0], data: { ...held.nodes[0].data, nodeType: "dataInput" } }], edges: [edge] }
    expect(savedFormsFor(heldHook, { nodes: [hook], edges: [] }, null, hook).saved_code).toBe(savedCode)
  })

  it("sends a node saved with unfinished steps with the list itself, and keeps one saved form for its steps", () => {
    const record = '[{"id":"s","kind":"source","input":"quotes"}]'
    const unfinished = {
      nodes: [transform("t", { steps, code: savedCode }, { ...live, saved_steps: record, saved_settings: "{}" })],
      submodels: {},
    }
    const document = { nodes: [transform("t", { steps: structuredClone(steps), code: "df = stale" }, live)], submodels: {} }

    const sent = withSavedForms(document, unfinished)
    expect(editor(sent.nodes[0])).toEqual({ ...live, saved_steps: record, saved_settings: "{}" })
    // Its code is the live cache's: the list is its program.
    expect(config(sent.nodes[0]).code).toBe("df = stale")

    const written = withSavedParts(withSavedForms(baseline, baseline), [
      { submodel: null, node_id: "t", code: null, steps: record, settings: null },
    ])
    expect(editor(written.nodes[0])).toEqual({ ...live, saved_steps: record, saved_settings: '{"rate":1.0}' })
    const rendered = withSavedParts(written, [{ submodel: null, node_id: "t", code: "df = done", steps: null, settings: null }])
    expect(editor(rendered.nodes[0])).toEqual({ ...live, saved_code: "df = done", saved_settings: '{"rate":1.0}' })
  })

  it("takes every saved form off a document", () => {
    const bare = withoutSavedForms(baseline)

    expect(bare.nodes.map(editor)).toEqual([live, live])
    expect(editor((bare.submodels.sub as ReturnType<typeof definition>).graph.nodes[0])).toEqual(live)
  })
})

describe("the one way a request carries the document", () => {
  const SRC = resolve(dirname(fileURLToPath(import.meta.url)), "../..")
  /** The modules that may build a request's graph: the canonical payload and this module. */
  const BUILDERS = new Set(["utils/graphSnapshot.ts", "utils/documentRequest.ts"])

  function sources(directory: string): string[] {
    return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
      const path = resolve(directory, entry.name)
      if (entry.isDirectory()) return ["__tests__", "generated", "test-utils"].includes(entry.name) ? [] : sources(path)
      return /\.tsx?$/.test(entry.name) && !/\.(?:test|spec|d)\.tsx?$/.test(entry.name) ? [path] : []
    })
  }

  it("is documentRequestGraph: no other module builds the canonical payload", () => {
    const callers = sources(SRC)
      .filter((path) => /\btoCanonicalGraphPayload\(/.test(readFileSync(path, "utf-8")))
      .map((path) => relative(SRC, path).split("\\").join("/"))
      .filter((path) => !BUILDERS.has(path))

    expect(callers).toEqual([])
  })
})

describe("a node a graph change moved", () => {
  // The rating Transform `t` moved into the definition `grouped` (Create Submodel), keeping its
  // id; then out again under a runtime id (Dissolve).
  const moved = (node: Node, submodel: string | null, nodeId: string): Node => ({
    ...node, data: { ...node.data, _savedAddress: { submodel, nodeId } },
  })

  it("finds its saved baseline through the place it was moved from", () => {
    const node = moved(transform("t", { steps, code: "", rate: 1 }, live), null, "t")
    const document = { nodes: [], submodels: { grouped: { ...definition([node]), definitionId: "grouped" } } }

    // Its inputs are as saved (none in either place), so its steps keep their saved form too.
    expect(savedFormsFor(baseline, document, "grouped", node)).toEqual({ saved_code: savedCode, saved_settings: '{"rate":1.0}' })
    const edited = moved(transform("t", { steps, code: "", rate: 2 }, live), null, "t")
    expect(savedFormsFor(baseline, document, "grouped", edited)).toEqual({ saved_code: savedCode, settings_edited_from: '{"rate":1.0}' })
    expect(savedFormsFor(baseline, document, "grouped", transform("t", { steps, code: "", rate: 1 }, live))).toEqual({})
  })

  it("is found by its identity after a save, after an Undo past one, and only as a node of its type", () => {
    // A save makes the moved document the baseline: its nodes keep their identity.
    const grouped = moved(transform("t", { steps, code: savedCode, rate: 1 }, { ...live, saved_code: savedCode, saved_settings: '{"rate":1.0}' }), null, "t")
    const saved = { nodes: [], submodels: { grouped: { ...definition([grouped]), definitionId: "grouped" } } }
    const movedLive = moved(transform("t", { steps, code: "", rate: 1 }, live), null, "t")
    expect(savedFormsFor(saved, saved, "grouped", movedLive).saved_settings).toBe('{"rate":1.0}')
    // Undo past that save restores the node where it was loaded, with no recorded place.
    const undone = transform("t", { steps, code: "", rate: 1 }, live)
    expect(savedFormsFor(saved, { nodes: [undone], submodels: {} }, null, undone).saved_settings).toBe('{"rate":1.0}')
    // Dissolved after the save: the identity goes with it.
    const dissolved = moved(transform("submodel_runtime/grouped/t", { steps, code: "", rate: 1 }, live), null, "t")
    expect(savedFormsFor(saved, { nodes: [dissolved], submodels: {} }, null, dissolved).saved_settings).toBe('{"rate":1.0}')
    // Only a node of its type.
    const occurrence: Node = {
      id: "t", position: { x: 0, y: 0 },
      data: { nodeType: "submodel", label: "t", config: { definition_id: "grouped" }, editor: { ...live, saved_settings: "{}" } },
    }
    const held = { nodes: [occurrence], submodels: {} }
    expect(savedFormsFor(held, held, null, transform("t", { rate: 1 }, live))).toEqual({})
  })

  it("keeps an earlier move's identity through a later graph change that leaves the node in place", () => {
    const earlier = moved(transform("t", { steps, code: "", rate: 1 }, live), null, "t")
    const before = { nodes: [], submodels: { grouped: { ...definition([earlier]), definitionId: "grouped" } } }
    // The answer is built on the server: its nodes carry no identity of the editor's.
    const answer = { nodes: [], submodels: { grouped: { ...definition([transform("t", { steps, code: "", rate: 1 }, live)]), definitionId: "grouped" } } }

    const placed = withSavedAddresses(answer, [], before)

    const inner = (placed.submodels.grouped as ReturnType<typeof definition>).graph.nodes[0]
    expect(inner.data._savedAddress).toEqual({ submodel: null, nodeId: "t" })
  })

  it("gives a copy its own identity, so an edited copy saved is never another node's", () => {
    // Codex r6: a definition two occurrences use. Dissolving one copies its node; the copy is
    // edited and saved; then the other occurrence, the last, is dissolved, which moves the node.
    const child = moved(transform("rated_node", { rate: 1 }, { ...live, saved_settings: '{"rate":1.0}' }), null, "rated")
    const shared = { ...definition([child]), definitionId: "rated" }
    const loaded = { nodes: [], submodels: { rated: shared } }
    const copyPlace = "submodel_runtime/rated_2/rated_node"
    const copied = withSavedAddresses(
      { nodes: [transform(copyPlace, { rate: 1 }, live)], submodels: { rated: shared } },
      [{ from_submodel: "rated", from_node_id: "rated_node", to_submodel: null, to_node_id: copyPlace }],
      loaded,
    )
    const copy = copied.nodes[0]
    expect(copy.data._savedAddress).toBeUndefined()
    expect(copy.data._savedOrigins).toEqual([{ submodel: null, nodeId: "rated" }])
    // Before a save the copy finds the node it was copied from.
    expect(savedFormsFor(loaded, copied, null, copy).saved_settings).toBe('{"rate":1.0}')

    // Edited and saved: the baseline holds the copy, with its own identity, beside the original.
    const edited: Node = { ...copy, data: { ...copy.data, config: { rate: 2 }, editor: { ...live, saved_settings: '{"rate":2}' } } }
    const saved = { nodes: [edited], submodels: { rated: shared } }
    const ownerPlace = "submodel_runtime/rated/rated_node"
    const dissolved = withSavedAddresses(
      { nodes: [edited, transform(ownerPlace, { rate: 1 }, live)], submodels: {} },
      [{ from_submodel: "rated", from_node_id: "rated_node", to_submodel: null, to_node_id: ownerPlace }],
      saved,
    )
    const owner = dissolved.nodes[1]
    expect(owner.data._savedAddress).toEqual({ submodel: null, nodeId: "rated" })
    expect(savedFormsFor(saved, dissolved, null, owner)).toEqual({ saved_settings: '{"rate":1.0}' })
  })

  it("records where the baseline holds each node an answer moved, through a second move", () => {
    const before = { nodes: [transform("t", { steps, code: "", rate: 1 }, live)], submodels: {} }
    const grouped = {
      nodes: [],
      submodels: { grouped: { ...definition([transform("t", { steps, code: "", rate: 1 }, live)]), definitionId: "grouped" } },
    }

    const placed = withSavedAddresses(
      grouped,
      [{ from_submodel: null, from_node_id: "t", to_submodel: "grouped", to_node_id: "t" }],
      before,
    )

    const inner = (placed.submodels.grouped as ReturnType<typeof definition>).graph.nodes[0]
    expect(inner.data._savedAddress).toEqual({ submodel: null, nodeId: "t" })
    const dissolved = withSavedAddresses(
      { nodes: [transform("submodel_runtime/grouped/t", { steps, code: "", rate: 1 }, live)], submodels: {} },
      [{ from_submodel: "grouped", from_node_id: "t", to_submodel: null, to_node_id: "submodel_runtime/grouped/t" }],
      placed,
    )
    expect(dissolved.nodes[0].data._savedAddress).toEqual({ submodel: null, nodeId: "t" })
    // An address, never sent: the request carries the saved forms it finds.
    const sent = documentRequestGraph({ nodes: dissolved.nodes, edges: [] }, baseline)
    expect(JSON.stringify(sent)).not.toContain("_savedAddress")
    expect(editor(sent.nodes[0])?.saved_settings).toBe('{"rate":1.0}')
  })
})
