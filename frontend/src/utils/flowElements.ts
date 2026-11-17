import type { Edge, Node, XYPosition } from "@xyflow/react"
import { edgeId } from "./edgeIds"
import { NODE_TYPE_META, NODE_TYPES, SINGLETON_NODE_NAMES, type NodeTypeValue } from "./nodeTypes"

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

/** A node type's name as its decorator spells it, snake_case: `workbench_output`. */
export function typeName(type: NodeTypeValue): string {
  return type.replace(/[A-Z]/g, (letter) => `_${letter.toLowerCase()}`)
}

/** A label as the function name it becomes, compared ignoring case. */
const functionKey = (label: string): string => label.trim().replace(/\W+/g, "_").toLowerCase()

/** New-node names that differ from the decorator's: a Transform is named as the palette calls it. */
const NEW_NODE_NAMES: Readonly<Partial<Record<NodeTypeValue, string>>> = {
  [NODE_TYPES.POLARS]: "transform",
}

/**
 * A new node's label (READ-13): its type's name, numbered from 2 only when a node already
 * holds the name (`transform`, then `transform_2`), ignoring case as function names must differ.
 */
export function freshNodeLabel(type: NodeTypeValue, taken: Iterable<string>): string {
  // A request input or response node has its type's one name (READ-46).
  const fixed = SINGLETON_NODE_NAMES[type]
  if (fixed !== undefined) return fixed
  const used = new Set([...taken].map(functionKey))
  const base = NEW_NODE_NAMES[type] ?? typeName(type)
  if (!used.has(base)) return base
  for (let number = 2; ; number += 1) {
    if (!used.has(`${base}_${number}`)) return `${base}_${number}`
  }
}

/** The labels the nodes of `nodes` hold, which a new node's label must not take. */
export const nodeLabels = (nodes: readonly Node[]): string[] =>
  nodes.map((node) => String((node.data as { label?: unknown } | undefined)?.label ?? node.id))

export function nodeLabel(type: NodeTypeValue, id: string): string {
  const meta = NODE_TYPE_META[type]
  if (!meta) {
    throw new Error(`Unknown node type "${type}"`)
  }
  const match = id.match(/(\d+)$/)
  return `${meta.name} ${match ? match[1] : id}`
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
