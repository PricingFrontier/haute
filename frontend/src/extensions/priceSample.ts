/**
 * A sample quote priced on the pipeline open in the editor (specs/extensions): the Workbench
 * Output's tables as its preview shows them. The workbench nodes run with the tables and
 * sample the extension gives, in this request alone, as the pipeline view gives them a
 * fetch's.
 */
import { previewNode } from "../api/client"
import { apiErrorMessage } from "../api/errors"
import type { GraphPayload } from "../api/types"
import { WORKBENCH_COPY_PATCHES, workbenchOutputTableLabels } from "../utils/extensionQuoteTables"
import { NODE_TYPES } from "../utils/nodeTypes"
import type { PricedSample, WorkbenchTables } from "./loadExtensionModule"

/** The preview route's most rows: a quote's many-row tables fit. */
const ROW_LIMIT = 10_000

export async function priceSample(graph: GraphPayload, workbench: WorkbenchTables, source: string): Promise<PricedSample> {
  const copies = { tables: workbench.tables, sample: workbench.sample, responseTables: workbench.response_tables }
  // The top level's alone, as the editor keeps them: a submodel's workbench nodes keep theirs.
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
  const priced = { ...graph, nodes }
  const labels = workbenchOutputTableLabels(output.data.config as Record<string, unknown> | undefined)
  const tables = await Promise.all(
    labels.map(async (label) => {
      const result = await previewNode({ graph: priced, nodeId: output.id, rowLimit: ROW_LIMIT, source, portLabel: label })
        // The server's reason, such as a sample that does not fit, rather than its status.
        .catch((error: unknown) => {
          throw new Error(apiErrorMessage(error))
        })
      if (result.status !== "ok") {
        throw new Error(result.error || `The Workbench Output's '${label}' table could not be previewed.`)
      }
      return [label, result.preview ?? []] as const
    }),
  )
  return { tables: Object.fromEntries(tables) }
}
