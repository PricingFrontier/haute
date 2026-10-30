/** Missing owner references on submodel occurrences in the parent graph. */
import type { Node } from "@xyflow/react"
import { isSubmodelInstanceConfig } from "../types/node"

export interface ConfigRefWarning {
  nodeId: string
  nodeLabel: string
  field: string
  referencedId: string
}

export function validateConfigRefs(nodes: Node[]): ConfigRefWarning[] {
  const nodeIds = new Set(nodes.filter((node) => node.data.nodeType === "submodel").map((node) => node.id))
  const warnings: ConfigRefWarning[] = []
  for (const node of nodes) {
    if (node.data.nodeType !== "submodel") continue
    const config = node.data.config
    if (!isSubmodelInstanceConfig(config)) throw new Error(`Submodel "${node.id}" has invalid identity configuration.`)
    const owner = config.instanceOf
    if (owner !== undefined && !nodeIds.has(owner)) {
      warnings.push({ nodeId: node.id, nodeLabel: String(node.data.label), field: "instanceOf", referencedId: owner })
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
