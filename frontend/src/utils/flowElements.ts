import type { Edge, Node, XYPosition } from "@xyflow/react"
import { NODE_TYPE_META, type NodeTypeValue } from "./nodeTypes"

type AppNodeParams = {
  id: string
  type: NodeTypeValue
  position: XYPosition
  config?: Record<string, unknown>
  selected?: boolean
  description?: string
  label?: string
}

type AppEdgeParams = {
  source: string
  target: string
  sourceHandle?: string | null
  targetHandle?: string | null
}

export function appNode({
  id,
  type,
  position,
  config = {},
  selected,
  description = "",
  label = nodeLabel(type, id),
}: AppNodeParams): Node {
  const meta = NODE_TYPE_META[type]
  if (!meta) {
    throw new Error(`Unknown node type "${type}"`)
  }
  return {
    id,
    type,
    position,
    selected,
    ...(meta.origin ? { origin: [...meta.origin] as [number, number] } : {}),
    data: {
      label,
      description,
      nodeType: type,
      config: {
        ...structuredClone(meta.defaultConfig),
        ...config,
      },
    },
  }
}

export function appEdge({
  source,
  target,
  sourceHandle = null,
  targetHandle = null,
}: AppEdgeParams): Edge {
  return {
    id: edgeId({ source, target, sourceHandle, targetHandle }),
    source,
    target,
    sourceHandle,
    targetHandle,
  }
}

export function nodeLabel(type: NodeTypeValue, id: string): string {
  const meta = NODE_TYPE_META[type]
  if (!meta) {
    throw new Error(`Unknown node type "${type}"`)
  }
  const match = id.match(/(\d+)$/)
  return `${meta.name} ${match ? match[1] : id}`
}

function edgeIdField(value: string): string {
  if (value === "") throw new Error("An edge id cannot be minted from an empty endpoint or handle")
  return value.replaceAll("~", "~0").replaceAll("-", "~1")
}

/**
 * The edge's id: an injective encoding of its four-field identity, minted
 * exactly as the server mints it (`haute._graph_utils._edge_id`), so a
 * connection keeps its id through save and reload and two distinct
 * connections never share one. Each present field is escaped (`~` as `~0`,
 * `-` as `~1`) and joined with `-` after `e`; absent trailing handles are left
 * off, and an absent source handle before a target handle is empty.
 */
export function edgeId({
  source,
  target,
  sourceHandle = null,
  targetHandle = null,
}: AppEdgeParams): string {
  const fields = [edgeIdField(source), edgeIdField(target)]
  if (targetHandle !== null) {
    fields.push(sourceHandle === null ? "" : edgeIdField(sourceHandle), edgeIdField(targetHandle))
  } else if (sourceHandle !== null) {
    fields.push(edgeIdField(sourceHandle))
  }
  return ["e", ...fields].join("-")
}

export function deselectNodes<T extends { selected?: boolean }>(nodes: T[]): T[] {
  return nodes.map((node) => ({ ...node, selected: false }))
}

export function selectOnlyNode<T extends { id: string; selected?: boolean }>(
  nodes: T[],
  selectedNodeId: string,
): T[] {
  return nodes.map((node) => ({ ...node, selected: node.id === selectedNodeId }))
}
