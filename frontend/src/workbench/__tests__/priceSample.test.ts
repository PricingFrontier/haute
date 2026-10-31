/**
 * A sample priced on the pipeline open in the editor (specs/workbench): the document sent
 * as it is with the form the view holds, whose tables and sample every workbench node of
 * the request takes, and the Workbench Output's tables previewed all at once, from one run.
 */
import { afterEach, describe, expect, it, vi } from "vitest"

vi.mock("../../api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../api/client")>()),
  previewNode: vi.fn(),
}))

import type { Node } from "@xyflow/react"
import { ApiError, previewNode } from "../../api/client"
import type { FormSpec, GraphPayload, PreviewNodeResponse, WorkbenchTable, WorkbenchTablesResponse } from "../../api/types"
import type { SubmodelDefinition } from "../../types/node"
import { priceSample } from "../priceSample"

const preview = vi.mocked(previewNode)

const policy: WorkbenchTable = { name: "policy_details", rows: "one", columns: [{ name: "exposure", type: "float" }] }
const pricing: WorkbenchTable = { name: "pricing_output", rows: "one", columns: [{ name: "premium", type: "float" }] }
const layers: WorkbenchTable = { name: "layer_premiums", rows: "many", columns: [{ name: "layer", type: "int" }] }
const bare: WorkbenchTable = { name: "notes", rows: "one", columns: [] }
const typed = { policy_details: { exposure: 100000 } }
const workbench = (responseTables: WorkbenchTable[]): WorkbenchTablesResponse => ({
  tables: [policy],
  sample: typed,
  response_tables: responseTables,
  store_columns: {},
})
/** The form the view holds; its content is the server's business. */
const form = { name: "Commercial property" } as unknown as FormSpec

const node = (id: string, nodeType: string, config: Record<string, unknown> = {}): Node => ({
  id,
  type: nodeType,
  position: { x: 0, y: 0 },
  data: { label: id, nodeType, config },
})

function definition(definitionId: string, nodes: Node[]): SubmodelDefinition {
  return { definitionId, file: `modules/${definitionId}.py`, graph: { nodes, edges: [] }, inputPorts: [], outputPorts: [] }
}

/** The document as the editor holds it: its workbench nodes hold nothing of the form. */
function graph(): GraphPayload {
  return {
    nodes: [
      node("quote", "workbenchInput"),
      node("priced", "polars", { code: "df" }),
      node("response", "workbenchOutput"),
    ],
    edges: [],
    submodels: { rating: definition("rating", [node("inner", "polars")]) },
  }
}

/** A preview's answer: each table asked for with its rows, as many as it has and not cut short unless said. */
const ok = (tables: Record<string, Record<string, unknown>[]>, cut: Record<string, number> = {}): PreviewNodeResponse => ({
  node_id: "response",
  status: "ok",
  port_previews: Object.fromEntries(
    Object.entries(tables).map(([label, rows]) => [
      label,
      { row_count: cut[label] ?? rows.length, columns: [], preview: rows, preview_truncated: label in cut },
    ]),
  ),
})

afterEach(() => {
  preview.mockReset()
})

describe("priceSample", () => {
  it("previews every one of the Workbench Output's response tables in one request, sending the document unchanged with the view's form", async () => {
    preview.mockResolvedValue(ok({ pricing_output: [{ premium: 2000 }], layer_premiums: [{ layer: 1 }, { layer: 2 }] }))
    const document = graph()
    const before = structuredClone(document)

    const priced = await priceSample(document, form, workbench([pricing, bare, layers]), "live")

    expect(priced).toEqual({ tables: { pricing_output: [{ premium: 2000 }], layer_premiums: [{ layer: 1 }, { layer: 2 }] } })
    expect(preview).toHaveBeenCalledOnce()
    const [{ graph: sent, nodeId, source, portLabels, workbenchForm }] = preview.mock.calls[0]
    expect(nodeId).toBe("response")
    expect(source).toBe("live")
    // A table without a column is no port, so nothing to preview.
    expect(portLabels).toEqual(["pricing_output", "layer_premiums"])
    expect(workbenchForm).toBe(form)
    expect(sent).toEqual(before)
    expect(document).toEqual(before)
  })

  it("refuses a pipeline without a Workbench Output", async () => {
    const document = graph()
    document.nodes = document.nodes.filter((n) => n.id !== "response")

    await expect(priceSample(document, form, workbench([pricing]), "live")).rejects.toThrow("The pipeline has no Workbench Output")
    expect(preview).not.toHaveBeenCalled()
  })

  it("prices a pipeline whose Workbench Input is inside a submodel, which the form reaches as well", async () => {
    preview.mockResolvedValue(ok({ pricing_output: [{ premium: 2000 }] }))
    const nested = graph()
    nested.nodes = nested.nodes.filter((n) => n.id !== "quote")
    nested.submodels = { rating: definition("rating", [node("inner", "workbenchInput")]) }

    await expect(priceSample(nested, form, workbench([pricing]), "live")).resolves.toEqual({
      tables: { pricing_output: [{ premium: 2000 }] },
    })
  })

  it("refuses a pipeline whose sheets reach no Workbench Input anywhere, saying what to replace or add", async () => {
    // A Quote Input beside the Workbench Output, which the singleton rules allow, here
    // inside a submodel.
    const quoted = graph()
    quoted.nodes = quoted.nodes.filter((n) => n.id !== "quote")
    quoted.submodels = { rating: definition("rating", [node("inner", "apiInput")]) }
    await expect(priceSample(quoted, form, workbench([pricing]), "live")).rejects.toThrow(
      "The pipeline reads its quotes from a Quote Input, which the sheets never fill: replace it with a Workbench Input.",
    )
    const bareGraph = graph()
    bareGraph.nodes = bareGraph.nodes.filter((n) => n.id !== "quote")
    await expect(priceSample(bareGraph, form, workbench([pricing]), "live")).rejects.toThrow(
      "The pipeline has no Workbench Input, so the sheets reach nothing: add one.",
    )
    expect(preview).not.toHaveBeenCalled()
  })

  it("rejects when a table asked for is missing from the answer", async () => {
    preview.mockResolvedValue(ok({ pricing_output: [{ premium: 2000 }] }))

    await expect(priceSample(graph(), form, workbench([pricing, layers]), "live")).rejects.toThrow(
      "The Workbench Output's 'layer_premiums' table was not previewed.",
    )
  })

  it("rejects with the preview's error", async () => {
    preview.mockResolvedValue({
      node_id: "response",
      status: "error",
      error: "Connect a frame to the Workbench Output's 'pricing_output' table.",
    })

    await expect(priceSample(graph(), form, workbench([pricing]), "live")).rejects.toThrow(
      "Connect a frame to the Workbench Output's 'pricing_output' table.",
    )
  })

  it("rejects with the server's reason when a table's preview request fails", async () => {
    const reason = "The workbench's sample does not fit this Workbench Input's tables: exposure is not a number."
    preview.mockRejectedValue(new ApiError("HTTP 422", 422, reason, { detail: { message: reason } }, { message: reason }))

    await expect(priceSample(graph(), form, workbench([pricing]), "live")).rejects.toThrow(reason)
  })

  it("shows the rows it has of a table Preview cut short", async () => {
    preview.mockResolvedValue(ok({ layer_premiums: [{ layer: 1 }] }, { layer_premiums: 12000 }))

    await expect(priceSample(graph(), form, workbench([layers]), "live")).resolves.toEqual({ tables: { layer_premiums: [{ layer: 1 }] } })
  })

  it("resolves to no tables for a Workbench Output without any", async () => {
    await expect(priceSample(graph(), form, workbench([]), "live")).resolves.toEqual({ tables: {} })
    expect(preview).not.toHaveBeenCalled()
  })
})
