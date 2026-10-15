/**
 * A sample priced on the pipeline open in the editor (specs/workbench): the Workbench
 * Output's tables as its preview shows them, every table from one run of the pipeline, in
 * one preview asking for them all. The document's top-level workbench nodes run
 * with the tables and sample the form gives, in this request alone, as a fetch gives them
 * copies; a Workbench Input inside a submodel keeps its copy, and the document itself is
 * left alone. A pipeline without a top-level Workbench Input, which alone is given the
 * sheets' values, is refused, saying what to replace or move. Asked for the complete
 * response, as a test quote keeps it, a table Preview cut short at its row or cell limit is
 * refused, naming the table and its rows.
 */
import { previewNode } from "../api/client"
import { apiErrorMessage } from "../api/errors"
import type { GraphPayload, WorkbenchTablesResponse } from "../api/types"
import { NODE_TYPES } from "../utils/nodeTypes"
import { WORKBENCH_COPY_PATCHES, workbenchTablePorts, type PricedSample } from "../utils/workbenchTables"

/** The preview route's most rows: a quote's many-row tables fit. */
const ROW_LIMIT = 10_000

export async function priceSample(
  graph: GraphPayload,
  workbench: WorkbenchTablesResponse,
  source: string,
  { complete = false }: { complete?: boolean } = {},
): Promise<PricedSample> {
  const copies = { tables: workbench.tables, sample: workbench.sample, responseTables: workbench.response_tables }
  const nodes = graph.nodes.map((node) => {
    const config = (node.data.config ?? {}) as Record<string, unknown>
    const patch = WORKBENCH_COPY_PATCHES[String(node.data.nodeType)]?.(config, copies) ?? null
    return patch === null ? node : { ...node, data: { ...node.data, config: { ...config, ...patch } } }
  })
  const output = nodes.find((node) => node.data.nodeType === NODE_TYPES.WORKBENCH_OUTPUT)
  if (output === undefined) {
    throw new Error(
      "The pipeline has no Workbench Output: add one from the palette and connect a frame to each of its tables.",
    )
  }
  // Only a top-level Workbench Input is given the sheets' values: without one every value
  // typed would price alike, so nothing is priced.
  if (!nodes.some((node) => node.data.nodeType === NODE_TYPES.WORKBENCH_INPUT)) {
    throw new Error(
      nodes.some((node) => node.data.nodeType === NODE_TYPES.API_INPUT)
        ? "The pipeline reads its quotes from a Quote Input, which the sheets never fill: replace it with a Workbench Input."
        : "The pipeline has no Workbench Input at its top level, so the sheets reach nothing: add one, or move the one inside a submodel out of it.",
    )
  }
  const priced = { ...graph, nodes }
  const labels = workbenchTablePorts(output.data.config as Record<string, unknown> | undefined)
  if (labels.length === 0) return { tables: {} }
  const result = await previewNode({ graph: priced, nodeId: output.id, rowLimit: ROW_LIMIT, source, portLabels: labels })
    // The server's reason, such as a sample that does not fit, rather than its status.
    .catch((error: unknown) => {
      throw new Error(apiErrorMessage(error))
    })
  if (result.status !== "ok") {
    throw new Error(result.error || "The Workbench Output's tables could not be previewed.")
  }
  const tables = labels.map((label) => {
    const table = result.port_previews?.[label]
    if (table === undefined) throw new Error(`The Workbench Output's '${label}' table was not previewed.`)
    if (complete && table.preview_truncated) {
      throw new Error(
        `The '${label}' table has ${table.row_count} rows, more than Preview returns: a test quote keeps the whole response.`,
      )
    }
    return [label, table.preview] as const
  })
  return { tables: Object.fromEntries(tables) }
}
