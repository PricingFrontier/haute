/**
 * A sample priced on the pipeline open in the editor (specs/workbench): the Workbench
 * Output's tables as its preview shows them, every table from one run of the pipeline, in
 * one preview asking for each by its port and answering it under its table's name, a table
 * the preview cut short refused rather than priced from its first rows. The document goes
 * as the editor holds it, with the
 * form the view holds, whose tables and sample every Workbench Input and Workbench Output
 * of that request takes, at the top level or inside a submodel, in place of the form
 * folder's, the Workbench Output previewed by its address wherever it is. A pipeline without a Workbench Input, which alone is given the sheets' values,
 * is refused, saying what to replace or add.
 */
import { previewNode } from "../api/client"
import { apiErrorMessage } from "../api/errors"
import type { FormSpec, PreparedGraph, WorkbenchTablesResponse } from "../api/types"
import { submodelGraphFromMetadata } from "../utils/apiInputPorts"
import { NODE_TYPES } from "../utils/nodeTypes"
import { childAddress, nodeFrame, rootAddress, type NodeAddress } from "../utils/nodeAddress"
import { frameTargetPayload } from "../utils/requestGateway"
import { workbenchTablePorts, type PricedSample } from "../utils/workbenchTables"

/** The preview route's most rows: a quote's many-row tables fit. */
const ROW_LIMIT = 10_000

/**
 * Where the pipeline's Workbench Output is: a node of the pipeline, or a child of the
 * submodel an occurrence on the pipeline runs, by its address in that occurrence; null for
 * none. The singleton rules give a pipeline one.
 */
function workbenchOutput(graph: PreparedGraph): NodeAddress | null {
  const isOutput = (node: { data: { nodeType?: unknown } }): boolean => node.data.nodeType === NODE_TYPES.WORKBENCH_OUTPUT
  const top = graph.nodes.find(isOutput)
  if (top !== undefined) return rootAddress(top.id)
  for (const occurrence of graph.nodes) {
    const definition = (occurrence.data.config as { definition_id?: unknown } | undefined)?.definition_id
    if (occurrence.data.nodeType !== NODE_TYPES.SUBMODEL || typeof definition !== "string") continue
    const child = submodelGraphFromMetadata(graph.submodels?.[definition])?.nodes.find(isOutput)
    if (child !== undefined) return childAddress(occurrence.id, child.id)
  }
  return null
}

export async function priceSample(
  graph: PreparedGraph,
  form: FormSpec,
  workbench: WorkbenchTablesResponse,
  source: string,
): Promise<PricedSample> {
  const output = workbenchOutput(graph)
  if (output === null) {
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
  const sides = workbench.response_tables.filter((table) => table.columns.length > 0)
  const labels = workbenchTablePorts(sides)
  if (labels.length === 0) return { tables: {} }
  const result = await previewNode({
    graph,
    // Every table of the Workbench Output, from one run.
    target: frameTargetPayload(nodeFrame(output), labels),
    rowLimit: ROW_LIMIT,
    source,
    workbenchForm: form,
  })
    // The server's reason, such as a sample that does not fit, rather than its status.
    .catch((error: unknown) => {
      throw new Error(apiErrorMessage(error))
    })
  if (result.status !== "ok") {
    throw new Error(result.error || "The Workbench Output's tables could not be previewed.")
  }
  // Each side's rows by its port, kept under its table's name, as a response holds them.
  const tables = sides.map(({ name, port }) => {
    const table = result.port_previews?.[port]
    if (table === undefined) throw new Error(`The Workbench Output's '${port}' table was not previewed.`)
    if (table.preview_truncated) {
      throw new Error(`${port} answers more rows than Preview prices whole (${table.preview.length} shown): a quote's answer must fit.`)
    }
    return [name, table.preview] as const
  })
  return { tables: Object.fromEntries(tables) }
}
