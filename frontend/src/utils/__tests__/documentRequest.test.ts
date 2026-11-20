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
  withSavedForms,
  withSavedParts,
} from "../documentRequest"
import { toCanonicalGraphPayload } from "../graphSnapshot"

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
    expect(savedFormsFor(baseline, document, null, document.nodes[1])).toEqual({})
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

    expect(editor(sent.nodes[0])).toEqual({ ...live, saved_code: savedCode })
    expect(editor(toCanonicalGraphPayload({ nodes: [stale], edges: [] }).nodes[0])).toEqual(live)
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
