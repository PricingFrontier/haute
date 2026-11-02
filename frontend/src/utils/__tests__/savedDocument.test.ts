import { describe, expect, it } from "vitest"

import { captureGraphSnapshot } from "../../stores/useGraphStore"
import { makeNode } from "../../test-utils/factories"
import { makePipelineEditorDocument } from "../../testSupport/pipelineDocumentFixture"
import { adaptPipelineEditorDocument } from "../../types/pipelineDocument"
import { constantDrafts } from "../globalConstants"
import { normalizeEdges } from "../graphHelpers"
import { documentHoldsSave, type SubmittedSave } from "../savedDocument"

const loaded = makePipelineEditorDocument({
  source_file: "rating/main.py",
  source_revision: "revision-before",
  nodes: [
    makeNode("quotes", "dataSource", { position: { x: 10, y: 20 } }),
    makeNode("price", "polars", { position: { x: 300, y: 20 } }),
  ],
  edges: [{ id: "e-quotes-price", source: "quotes", target: "price" }],
  preserved_blocks: ["# kept"],
})

/** What a save of the canvas loaded from `document`, unchanged, submits. */
function savedAsLoaded(document = loaded): SubmittedSave {
  const adapted = adaptPipelineEditorDocument(document)
  return {
    sourceFile: document.source_file,
    pipelineName: document.pipeline_name ?? "",
    description: document.pipeline_description ?? "",
    snapshot: captureGraphSnapshot({
      nodes: adapted.nodes,
      edges: normalizeEdges(adapted.edges),
      preamble: document.preamble ?? "",
      submodels: adapted.submodels,
      globalConstants: constantDrafts(document.global_constants),
    }),
    constantsSent: true,
    preservedBlocks: document.preserved_blocks,
    sources: document.sources,
    activeSource: document.active_source ?? "",
  }
}

describe("documentHoldsSave", () => {
  it("recognises the saved content in the document read back, whatever its revision", () => {
    expect(documentHoldsSave({ ...loaded, source_revision: "revision-after" }, savedAsLoaded())).toBe(true)
  })

  it.each([
    ["another file", { source_file: "rating/other.py" }],
    ["a node moved", { nodes: [makeNode("quotes", "dataSource", { position: { x: 11, y: 20 } }), makeNode("price", "polars", { position: { x: 300, y: 20 } })] }],
    ["a preserved block", { preserved_blocks: ["# kept", "# added"] }],
    ["the sources", { sources: ["live", "batch"] }],
    ["the active source", { active_source: "batch", sources: ["live", "batch"] }],
    ["the description another writer saved", { pipeline_description: "Edited elsewhere" }],
    ["the pipeline name", { pipeline_name: "renamed" }],
  ])("refuses a document that differs by %s", (_name, change) => {
    const read = makePipelineEditorDocument({
      source_file: "rating/main.py",
      source_revision: "revision-after",
      nodes: [
        makeNode("quotes", "dataSource", { position: { x: 10, y: 20 } }),
        makeNode("price", "polars", { position: { x: 300, y: 20 } }),
      ],
      edges: [{ id: "e-quotes-price", source: "quotes", target: "price" }],
      preserved_blocks: ["# kept"],
      ...change,
    })
    expect(documentHoldsSave(read, savedAsLoaded())).toBe(false)
  })

  it("knows a node by its name, as a read of the file does after a rename or a creation", () => {
    const submitted = savedAsLoaded()
    // The canvas still holds the provisional id of a node it created and named "price".
    const provisional = (id: string) => (id === "price" ? "polars_3" : id)
    const canvas = {
      ...submitted,
      snapshot: {
        ...submitted.snapshot,
        nodes: submitted.snapshot.nodes.map((node) => ({ ...node, id: provisional(node.id) })),
        edges: submitted.snapshot.edges.map((edge) => ({ ...edge, id: "e-provisional", target: provisional(edge.target) })),
      },
    }
    expect(documentHoldsSave({ ...loaded, source_revision: "revision-after" }, canvas)).toBe(true)
    // A node named differently is different content.
    const renamed = {
      ...canvas,
      snapshot: {
        ...canvas.snapshot,
        nodes: canvas.snapshot.nodes.map((node) => node.id === "polars_3" ? { ...node, data: { ...node.data, label: "premium" } } : node),
      },
    }
    expect(documentHoldsSave({ ...loaded, source_revision: "revision-after" }, renamed)).toBe(false)
  })

  it("leaves constants out when the save sent none", () => {
    const withConstants = makePipelineEditorDocument({
      source_file: "rating/main.py",
      global_constants: [{ name: "rate", type: "float", value: 1.5 }],
    })
    const submitted = { ...savedAsLoaded(), constantsSent: false }
    expect(documentHoldsSave({ ...loaded, global_constants: withConstants.global_constants }, submitted)).toBe(true)
    expect(documentHoldsSave(
      { ...loaded, global_constants: withConstants.global_constants },
      savedAsLoaded(),
    )).toBe(false)
  })
})
