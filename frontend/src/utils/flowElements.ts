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
    id: edgeId(source, target, targetHandle, sourceHandle),
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

export function edgeId(
  source: string,
  target: string,
  targetHandle?: string | null,
  sourceHandle?: string | null,
): string {
  return [
    "e",
    source,
    target,
    targetHandle || "default",
    sourceHandle || "default",
  ].join("_")
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
