/**
 * A sample priced on the pipeline open in the editor (specs/workbench): the Workbench
 * Output's tables as its preview shows them, every table from one run of the pipeline, in
 * one preview asking for them all. The document goes as the editor holds it, with the
 * form the view holds, whose tables and sample every Workbench Input and Workbench Output
 * of that request takes, at the top level or inside a submodel, in place of the form
 * folder's. A pipeline without a Workbench Input, which alone is given the sheets' values,
 * is refused, saying what to replace or add.
 */
import { previewNode } from "../api/client"
import { apiErrorMessage } from "../api/errors"
import type { FormSpec, GraphPayload, WorkbenchTablesResponse } from "../api/types"
import { submodelGraphFromMetadata } from "../utils/apiInputPorts"
import { NODE_TYPES } from "../utils/nodeTypes"
import { workbenchTablePorts, type PricedSample } from "../utils/workbenchTables"

/** The preview route's most rows: a quote's many-row tables fit. */
const ROW_LIMIT = 10_000

export async function priceSample(
  graph: GraphPayload,
  form: FormSpec,
  workbench: WorkbenchTablesResponse,
  source: string,
): Promise<PricedSample> {
  const output = graph.nodes.find((node) => node.data.nodeType === NODE_TYPES.WORKBENCH_OUTPUT)
  if (output === undefined) {
    throw new Error(
      "The pipeline has no Workbench Output: add one from the palette and connect a frame to each of its tables.",
    )
  }
  // Only a Workbench Input is given the sheets' values: without one every value typed
  // would price alike, so nothing is priced.
  const nodes = [
    ...graph.nodes,
    ...Object.values(graph.submodels ?? {}).flatMap((definition) => submodelGraphFromMetadata(definition)?.nodes ?? []),
  ]
  if (!nodes.some((node) => node.data.nodeType === NODE_TYPES.WORKBENCH_INPUT)) {
    throw new Error(
      nodes.some((node) => node.data.nodeType === NODE_TYPES.API_INPUT)
        ? "The pipeline reads its quotes from a Quote Input, which the sheets never fill: replace it with a Workbench Input."
        : "The pipeline has no Workbench Input, so the sheets reach nothing: add one.",
    )
  }
  const labels = workbenchTablePorts(workbench.response_tables)
  if (labels.length === 0) return { tables: {} }
  const result = await previewNode({
    graph,
    nodeId: output.id,
    rowLimit: ROW_LIMIT,
    source,
    portLabels: labels,
    workbenchForm: form,
  })
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
    return [label, table.preview] as const
  })
  return { tables: Object.fromEntries(tables) }
}
