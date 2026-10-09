/**
 * A sample priced on the pipeline open in the editor (specs/extensions): the workbench nodes
 * given the tables and sample the extension has, for that request alone, and the Workbench
 * Output previewed a table at a time.
 */
import { afterEach, describe, expect, it, vi } from "vitest"

vi.mock("../../api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../api/client")>()),
  previewNode: vi.fn(),
}))

import type { Node } from "@xyflow/react"
import { ApiError, previewNode } from "../../api/client"
import type { GraphPayload, PreviewNodeResponse } from "../../api/types"
import type { WorkbenchTables } from "../loadExtensionModule"
import { priceSample } from "../priceSample"

const preview = vi.mocked(previewNode)

const column = (table: string, name: string, type: string) => ({
  name,
  path: `$[:].${table}.${name}`,
  type,
  status: "Confirmed",
  selected: true,
  levels: null,
})
const policy = { path: "$[:]", label: "policy_details", emit: true, row_id_column: null, columns: [column("policy_details", "exposure", "float")] }
const pricing = { path: "$[:]", label: "pricing_output", emit: true, row_id_column: null, columns: [column("pricing_output", "premium", "float")] }
const layers = {
  path: "$[:].layer_premiums[:]",
  label: "layer_premiums",
  emit: true,
  row_id_column: "layer",
  columns: [{ ...column("layer_premiums", "layer", "int"), path: "$[:].layer_premiums[:].layer" }],
}
const typed = { policy_details: { exposure: 100000 } }
const workbench = (responseTables: Record<string, unknown>[]): WorkbenchTables => ({
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
      node("response", "workbenchOutput", { tables: [pricing], mapping: { pricing_output: { premium: "loaded" } } }),
    ],
    edges: [],
    submodels: { rating: { graph: { nodes: [node("inner", "workbenchInput", { tables: [], sample: {} })], edges: [] } } },
  }
}

const ok = (rows: Record<string, unknown>[]): PreviewNodeResponse => ({ node_id: "response", status: "ok", preview: rows })

afterEach(() => {
  preview.mockReset()
})

describe("priceSample", () => {
  it("previews each of the Workbench Output's tables on the tables and sample given, leaving the document alone", async () => {
    preview.mockImplementation(async ({ portLabel }) =>
      ok(portLabel === "pricing_output" ? [{ premium: 2000 }] : [{ layer: 1 }, { layer: 2 }]),
    )
    const document = graph()
    const before = structuredClone(document)

    const priced = await priceSample(document, workbench([pricing, layers]), "live")

    expect(priced).toEqual({ tables: { pricing_output: [{ premium: 2000 }], layer_premiums: [{ layer: 1 }, { layer: 2 }] } })
    expect(preview.mock.calls.map(([args]) => args.portLabel)).toEqual(["pricing_output", "layer_premiums"])
    const [{ graph: sent, nodeId, source }] = preview.mock.calls[0]
    expect(nodeId).toBe("response")
    expect(source).toBe("live")
    const config = (id: string) => sent.nodes.find((n) => n.id === id)?.data.config
    expect(config("quote")).toEqual({ tables: [policy], sample: typed })
    // The new tables, the mapping kept for the table and column they still have.
    expect(config("response")).toEqual({ tables: [pricing, layers], mapping: { pricing_output: { premium: "loaded" } } })
    expect(config("priced")).toEqual({ code: "df" })
    expect(sent.submodels).toEqual(before.submodels)
    expect(document).toEqual(before)
  })

  it("refuses a pipeline without a Workbench Output", async () => {
    const document = graph()
    document.nodes = document.nodes.filter((n) => n.id !== "response")

    await expect(priceSample(document, workbench([pricing]), "live")).rejects.toThrow("The pipeline has no Workbench Output")
    expect(preview).not.toHaveBeenCalled()
  })

  it("rejects with a table's preview error", async () => {
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

  it("resolves to no tables for a Workbench Output without any", async () => {
    await expect(priceSample(graph(), workbench([]), "live")).resolves.toEqual({ tables: {} })
    expect(preview).not.toHaveBeenCalled()
  })
})
