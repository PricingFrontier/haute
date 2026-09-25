import type { Node } from "@xyflow/react"

import { dataInputIsDirect } from "./dataInputMode"
import { NODE_TYPES } from "./nodeTypes"

/** The input-cache request body that names one node's snapshot. */
export type SnapshotSource = {
  schema_version: 1
  node_type?: "apiInput"
  config: Record<string, unknown>
}

const STRUCTURED_PATH = /\.(json|jsonl|ndjson|xml)$/i

function plainConfig(node: Pick<Node, "data">): Record<string, unknown> | null {
  const config = (node.data as { config?: unknown }).config
  return config && typeof config === "object" && !Array.isArray(config)
    ? (config as Record<string, unknown>)
    : null
}

/**
 * The snapshot a node reads from, or null when it reads none: a Data Input
 * other than a direct Parquet scan, or a structured (JSON/JSONL/NDJSON/XML)
 * Quote Input whose tables are built together from one shred of the source.
 */
export function inputSnapshotSource(node: Pick<Node, "data">): SnapshotSource | null {
  const nodeType = (node.data as { nodeType?: unknown }).nodeType
  const config = plainConfig(node)
  if (!config) return null
  if (nodeType === NODE_TYPES.DATA_INPUT) {
    return dataInputIsDirect(config) ? null : { schema_version: 1, config }
  }
  if (
    nodeType === NODE_TYPES.API_INPUT &&
    Object.prototype.hasOwnProperty.call(config, "tables") &&
    typeof config.path === "string" &&
    STRUCTURED_PATH.test(config.path)
  ) {
    return { schema_version: 1, node_type: "apiInput", config }
  }
  return null
}
