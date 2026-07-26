/**
 * Validates config fields that reference other node IDs.
 *
 * Scans all nodes' config for fields known to contain node references
 * (data_input, banding_source, instanceOf) and flags any that point to
 * non-existent node IDs. Returns human-readable warnings.
 *
 * A reference target is valid if it names either a top-level node OR a node
 * exported by a submodel. The latter is how `instanceOf` legitimately points
 * into a submodel's internal graph (e.g. a top-level instance of
 * `competitor_features`, which is defined inside the `model_stuff` submodel).
 * This mirrors the submodel-aware resolution in
 * `NodePanel.tsx#resolveInstanceOriginal` so we don't false-positive on a
 * valid submodel-exported target while still flagging genuinely-absent ones.
 */
import type { Node } from "@xyflow/react"

/** Config keys that store node ID references. */
const NODE_REF_FIELDS = ["data_input", "banding_source", "instanceOf"] as const

export interface ConfigRefWarning {
  nodeId: string
  nodeLabel: string
  field: string
  referencedId: string
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
}

/**
 * Collect the ids of nodes inside a single submodel's graph metadata.
 *
 * Reads the canonical nested `{ graph: { nodes: [...] } }` shape.
 * Defensive about malformed input — anything that isn't a recognisable node
 * with a string `id` is skipped rather than throwing, since validation must
 * never crash a save.
 */
function submodelNodeIds(metadata: unknown): string[] {
  if (!isRecord(metadata) || !isRecord(metadata.graph)) return []
  const graph = metadata.graph
  if (!Array.isArray(graph.nodes)) return []
  const ids: string[] = []
  for (const node of graph.nodes) {
    if (isRecord(node) && typeof node.id === "string" && node.id) ids.push(node.id)
  }
  return ids
}

export function validateConfigRefs(
  nodes: Node[],
  submodels?: Record<string, unknown> | null,
): ConfigRefWarning[] {
  const nodeIds = new Set(nodes.map((n) => n.id))
  for (const metadata of Object.values(submodels ?? {})) {
    for (const id of submodelNodeIds(metadata)) nodeIds.add(id)
  }
  const warnings: ConfigRefWarning[] = []

  for (const node of nodes) {
    const config = (node.data?.config ?? {}) as Record<string, unknown>
    const label = (node.data?.label as string) || node.id

    for (const field of NODE_REF_FIELDS) {
      const ref = config[field]
      if (typeof ref === "string" && ref && !nodeIds.has(ref)) {
        warnings.push({ nodeId: node.id, nodeLabel: label, field, referencedId: ref })
      }
    }
  }

  return warnings
}

export function formatConfigRefWarnings(warnings: ConfigRefWarning[]): string {
  if (warnings.length === 0) return ""
  if (warnings.length === 1) {
    const w = warnings[0]
    return `"${w.nodeLabel}" references missing node "${w.referencedId}" in ${w.field}`
  }
  return `${warnings.length} nodes have broken references to missing nodes`
}
