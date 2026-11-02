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

  it("knows a node by the name the file gives it, as a read does after a creation or a rename", () => {
    // The file holds each node as its function name (`_sanitize_func_name` of the
    // label); a read gives that as both id and label.
    const transform = { label: "Transform_3", nodeType: "polars", _functionName: "Transform_3" }
    const read = makePipelineEditorDocument({
      source_file: "rating/main.py",
      source_revision: "revision-after",
      nodes: [
        makeNode("quotes", "dataSource", { position: { x: 10, y: 20 } }),
        makeNode("Transform_3", "polars", { position: { x: 300, y: 20 }, data: transform }),
      ],
      edges: [{ id: "e-quotes-Transform_3", source: "quotes", target: "Transform_3" }],
    })
    const submitted = savedAsLoaded(read)
    // The canvas holds the node it created under its provisional id and display
    // label, with the function name the server resolved for that label.
    const onCanvas = (label: string, functionName: string) => ({
      ...submitted,
      snapshot: {
        ...submitted.snapshot,
        nodes: submitted.snapshot.nodes.map((node) => node.id === "Transform_3"
          ? { ...node, id: "polars_3", data: { ...node.data, label, _functionName: functionName } }
          : node),
        edges: submitted.snapshot.edges.map((edge) => ({ ...edge, id: "e-provisional", target: "polars_3" })),
      },
    })

    expect(documentHoldsSave(read, onCanvas("Transform 3", "Transform_3"))).toBe(true)
    // A node the file names differently is different content.
    expect(documentHoldsSave(read, onCanvas("Premium", "Premium"))).toBe(false)
  })

  it("keeps the order of a node's inputs, which the file keeps, and of nothing else", () => {
    const inputs = (edges: { source: string; target: string }[]) => makePipelineEditorDocument({
      source_file: "rating/main.py",
      source_revision: "revision-after",
      nodes: ["a", "b", "x", "y"].map((id) => makeNode(id, "polars")),
      edges: edges.map((edge) => ({ id: `e-${edge.source}-${edge.target}`, ...edge })),
    })
    const sent = savedAsLoaded(inputs([{ source: "a", target: "x" }, { source: "b", target: "x" }, { source: "a", target: "y" }]))

    // Another node's edges listed first: the same file.
    expect(documentHoldsSave(
      inputs([{ source: "a", target: "y" }, { source: "a", target: "x" }, { source: "b", target: "x" }]),
      sent,
    )).toBe(true)
    // The same node's inputs reversed: its first input (an External File hook's frame) changed.
    expect(documentHoldsSave(
      inputs([{ source: "b", target: "x" }, { source: "a", target: "x" }, { source: "a", target: "y" }]),
      sent,
    )).toBe(false)
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
