/**
 * A sample priced on the pipeline open in the editor (specs/workbench): the top-level
 * workbench nodes given the form's tables and sample for that request alone, and the
 * Workbench Output's tables previewed all at once, from one run.
 */
import { afterEach, describe, expect, it, vi } from "vitest"

vi.mock("../../api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../api/client")>()),
  previewNode: vi.fn(),
}))

import type { Node } from "@xyflow/react"
import { ApiError, previewNode } from "../../api/client"
import type { GraphPayload, PreviewNodeResponse, WorkbenchTable, WorkbenchTablesResponse } from "../../api/types"
import { priceSample } from "../priceSample"

const preview = vi.mocked(previewNode)

const policy: WorkbenchTable = { name: "policy_details", rows: "one", columns: [{ name: "exposure", type: "float" }] }
const pricing: WorkbenchTable = { name: "pricing_output", rows: "one", columns: [{ name: "premium", type: "float" }] }
const layers: WorkbenchTable = { name: "layer_premiums", rows: "many", columns: [{ name: "layer", type: "int" }] }
const typed = { policy_details: { exposure: 100000 } }
const workbench = (responseTables: WorkbenchTable[]): WorkbenchTablesResponse => ({
  tables: [policy],
  sample: typed,
  response_tables: responseTables,
})

const node = (id: string, nodeType: string, config: Record<string, unknown>): Node => ({
  id,
  type: nodeType,
  position: { x: 0, y: 0 },
  data: { label: id, nodeType, config },
})

/** The document as the editor holds it: copies from before the workbench last changed. */
function graph(): GraphPayload {
  return {
    nodes: [
      node("quote", "workbenchInput", { tables: [policy], sample: { policy_details: { exposure: 1 } } }),
      node("priced", "polars", { code: "df" }),
      node("response", "workbenchOutput", { tables: [pricing] }),
    ],
    edges: [],
    submodels: { rating: { graph: { nodes: [node("inner", "workbenchInput", { tables: [], sample: {} })], edges: [] } } },
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
  it("previews every one of the Workbench Output's tables in one request on the tables and sample given, leaving the document alone", async () => {
    preview.mockResolvedValue(ok({ pricing_output: [{ premium: 2000 }], layer_premiums: [{ layer: 1 }, { layer: 2 }] }))
    const document = graph()
    const before = structuredClone(document)

    const priced = await priceSample(document, workbench([pricing, layers]), "live")

    expect(priced).toEqual({ tables: { pricing_output: [{ premium: 2000 }], layer_premiums: [{ layer: 1 }, { layer: 2 }] } })
    expect(preview.mock.calls.map(([args]) => args.portLabels)).toEqual([["pricing_output", "layer_premiums"]])
    const [{ graph: sent, nodeId, source }] = preview.mock.calls[0]
    expect(nodeId).toBe("response")
    expect(source).toBe("live")
    const config = (id: string) => sent.nodes.find((n) => n.id === id)?.data.config
    expect(config("quote")).toEqual({ tables: [policy], sample: typed })
    expect(config("response")).toEqual({ tables: [pricing, layers] })
    expect(config("priced")).toEqual({ code: "df" })
    // A Workbench Input inside a submodel keeps its copy.
    expect(sent.submodels).toEqual(before.submodels)
    expect(document).toEqual(before)
  })

  it("refuses a pipeline without a Workbench Output", async () => {
    const document = graph()
    document.nodes = document.nodes.filter((n) => n.id !== "response")

    await expect(priceSample(document, workbench([pricing]), "live")).rejects.toThrow("The pipeline has no Workbench Output")
    expect(preview).not.toHaveBeenCalled()
  })

  it("refuses a pipeline whose sheets reach no top-level Workbench Input, saying what to replace or move", async () => {
    // A Quote Input beside the Workbench Output, which the singleton rules allow.
    const quoted = graph()
    quoted.nodes = quoted.nodes.map((n) => (n.id === "quote" ? node("quote", "apiInput", { tables: [] }) : n))
    await expect(priceSample(quoted, workbench([pricing]), "live")).rejects.toThrow(
      "The pipeline reads its quotes from a Quote Input, which the sheets never fill: replace it with a Workbench Input.",
    )
    // The Workbench Input inside a submodel alone.
    const nested = graph()
    nested.nodes = nested.nodes.filter((n) => n.id !== "quote")
    await expect(priceSample(nested, workbench([pricing]), "live")).rejects.toThrow(
      "The pipeline has no Workbench Input at its top level, so the sheets reach nothing: add one, or move the one inside a submodel out of it.",
    )
    expect(preview).not.toHaveBeenCalled()
  })

  it("rejects when a table asked for is missing from the answer", async () => {
    preview.mockResolvedValue(ok({ pricing_output: [{ premium: 2000 }] }))

    await expect(priceSample(graph(), workbench([pricing, layers]), "live")).rejects.toThrow(
      "The Workbench Output's 'layer_premiums' table was not previewed.",
    )
  })

  it("rejects with the preview's error", async () => {
    preview.mockResolvedValue({
      node_id: "response",
      status: "error",
      error: "Connect a frame to the Workbench Output's 'pricing_output' table.",
    })

    await expect(priceSample(graph(), workbench([pricing]), "live")).rejects.toThrow(
      "Connect a frame to the Workbench Output's 'pricing_output' table.",
    )
  })

  it("rejects with the server's reason when a table's preview request fails", async () => {
    const reason = "The workbench's sample does not fit this Workbench Input's tables: exposure is not a number."
    preview.mockRejectedValue(new ApiError("HTTP 422", 422, reason, { detail: { message: reason } }, { message: reason }))

    await expect(priceSample(graph(), workbench([pricing]), "live")).rejects.toThrow(reason)
  })

  it("refuses, when the complete response is asked for, a table Preview cut short", async () => {
    preview.mockResolvedValue(ok({ layer_premiums: [{ layer: 1 }] }, { layer_premiums: 12000 }))

    // Preview itself shows the rows it has; a test quote keeps the whole response.
    await expect(priceSample(graph(), workbench([layers]), "live")).resolves.toEqual({ tables: { layer_premiums: [{ layer: 1 }] } })
    await expect(priceSample(graph(), workbench([layers]), "live", { complete: true })).rejects.toThrow(
      "The 'layer_premiums' table has 12000 rows, more than Preview returns",
    )
  })

  it("resolves to no tables for a Workbench Output without any", async () => {
    await expect(priceSample(graph(), workbench([]), "live")).resolves.toEqual({ tables: {} })
    expect(preview).not.toHaveBeenCalled()
  })
})
