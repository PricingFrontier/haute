import type { Node, Edge, InternalNode } from "@xyflow/react"
import type { ElkNode } from "elkjs/lib/elk-api"

const DEFAULT_NODE_WIDTH = 240
const DEFAULT_NODE_HEIGHT = 70
const LAYOUT_COLLISION_GAP = 60

type ElkEngine = {
  layout: (graph: ElkNode) => Promise<ElkNode>
}

type NodeLookup = (id: string) => InternalNode | undefined
type SizedElkNode = ElkNode & { width: number; height: number }

const PORT_SIDES = { left: "WEST", right: "EAST", top: "NORTH", bottom: "SOUTH" }

let elkPromise: Promise<ElkEngine> | null = null

async function getElk(): Promise<ElkEngine> {
  if (!elkPromise) {
    elkPromise = import("elkjs/lib/elk.bundled.js").then(
      ({ default: ELK }) => new ELK() as unknown as ElkEngine,
    )
  }
  return elkPromise
}

function buildLayoutGraph(
  nodes: Node[], edges: Edge[], getInternalNode?: NodeLookup,
): Omit<ElkNode, "children"> & { children: SizedElkNode[] } {
  // Snapshot geometry before awaiting ELK; never add internal fields to saved nodes.
  const internalNodes = new Map(nodes.map(node => [node.id, getInternalNode?.(node.id)]))
  const children = new Map<string, SizedElkNode>(nodes.map(node => [node.id, {
    id: node.id,
    ...nodeDimensions(internalNodes.get(node.id) ?? node),
  }]))
  const usedIds = new Set(["root", ...nodes.map(node => node.id), ...edges.map(edge => edge.id)])
  const portIds = new Map<string, string>()
  let portCounter = 0

  function endpoint(nodeId: string, type: "source" | "target", handleId?: string | null): string {
    const child = children.get(nodeId)
    if (!child) throw new Error(`Layout edge references missing node "${nodeId}"`)
    const bounds = internalNodes.get(nodeId)?.internals.handleBounds
    // Initial submodel layout runs before its nodes mount and have handle bounds.
    if (!bounds) return nodeId
    const handles = bounds[type] ?? []
    const handle = handleId == null ? handles[0] : handles.find(handle => handle.id === handleId)
    if (!handle) {
      throw new Error(`Layout could not find ${type} handle "${handleId ?? "default"}" on node "${nodeId}"`)
    }
    const x = handle.x + handle.width / 2
    const y = handle.y + handle.height / 2
    if (!Number.isFinite(x) || !Number.isFinite(y)) {
      throw new Error(`Layout received invalid handle geometry for node "${nodeId}"`)
    }
    // Hidden submodel input aliases occupy the same socket. Model one shared port
    // so ELK optimizes the connections the user sees rather than separate anchors.
    const key = JSON.stringify([nodeId, type, handle.position, x, y])
    const existing = portIds.get(key)
    if (existing) return existing
    let id: string
    do {
      id = `__layout_port_${portCounter++}`
    } while (usedIds.has(id))
    usedIds.add(id)
    portIds.set(key, id)
    child.layoutOptions = { "elk.portConstraints": "FIXED_POS" }
    child.ports ??= []
    child.ports.push({
      id, x, y, width: 0, height: 0,
      layoutOptions: { "elk.port.side": PORT_SIDES[handle.position] },
    })
    return id
  }

  const layoutEdges = edges.map(edge => ({
    id: edge.id,
    sources: [endpoint(edge.source, "source", edge.sourceHandle)],
    targets: [endpoint(edge.target, "target", edge.targetHandle)],
  }))
  return {
    id: "root",
    layoutOptions: {
      "elk.algorithm": "layered",
      "elk.direction": "RIGHT",
      "elk.spacing.nodeNode": "60",
      "elk.layered.spacing.nodeNodeBetweenLayers": "120",
      "elk.layered.crossingMinimization.strategy": "LAYER_SWEEP",
      "elk.layered.thoroughness": "30",
      "elk.layered.nodePlacement.strategy": "NETWORK_SIMPLEX",
      "elk.layered.nodePlacement.favorStraightEdges": "true",
    },
    children: [...children.values()],
    edges: layoutEdges,
  }
}

export async function getLayoutedElements(
  nodes: Node[], edges: Edge[], getInternalNode?: NodeLookup,
): Promise<Node[]> {
  if (nodes.length === 0 && edges.length === 0) return []
  const elkGraph = buildLayoutGraph(nodes, edges, getInternalNode)
  const elk = await getElk()
  const layout = await elk.layout(elkGraph)
  const positions = new Map(layout.children?.map(child => [child.id, child]))
  const dimensions = new Map(elkGraph.children.map(child => [child.id, child]))

  return nodes.map(node => {
    const child = positions.get(node.id)
    if (child?.x == null || child.y == null || !Number.isFinite(child.x) || !Number.isFinite(child.y)) {
      throw new Error(`Layout did not return a finite position for node "${node.id}"`)
    }
    const { width, height } = dimensions.get(node.id)!
    const [originX, originY] = node.origin ?? [0, 0]
    return {
      ...node,
      position: { x: child.x + originX * width, y: child.y + originY * height },
    }
  })
}

function hasFinitePosition(node: Node): boolean {
  return Number.isFinite(node.position?.x) && Number.isFinite(node.position?.y)
}

/**
 * Identify imported nodes that do not yet carry a real canvas position.
 *
 * Every finite coordinate pair is authoritative, including the origin. Only
 * absent or non-finite coordinates need generated layout.
 */
export function nodeIdsNeedingLayout(incomingNodes: Node[]): Set<string> {
  return new Set(
    incomingNodes
      .filter(node => !hasFinitePosition(node))
      .map(node => node.id),
  )
}

type NodeBounds = {
  left: number
  top: number
  right: number
  bottom: number
}

function positiveDimension(value: unknown, fallback: number): number {
  return typeof value === "number" && Number.isFinite(value) && value > 0
    ? value
    : fallback
}

function nodeDimensions(node: Node): { width: number; height: number } {
  const measured = node.measured as { width?: number; height?: number } | undefined
  return {
    width: positiveDimension(node.width ?? measured?.width, DEFAULT_NODE_WIDTH),
    height: positiveDimension(node.height ?? measured?.height, DEFAULT_NODE_HEIGHT),
  }
}

function nodeBounds(node: Node, position = node.position): NodeBounds {
  const { width, height } = nodeDimensions(node)
  const origin = node.origin ?? [0, 0]
  const left = position.x - origin[0] * width
  const top = position.y - origin[1] * height
  return {
    left,
    top,
    right: left + width,
    bottom: top + height,
  }
}

function overlaps(a: NodeBounds, b: NodeBounds): boolean {
  return (
    a.left < b.right + LAYOUT_COLLISION_GAP
    && a.right + LAYOUT_COLLISION_GAP > b.left
    && a.top < b.bottom + LAYOUT_COLLISION_GAP
    && a.bottom + LAYOUT_COLLISION_GAP > b.top
  )
}

/**
 * Copy layout coordinates only to the requested missing nodes.
 *
 * Preserved nodes reserve their actual boxes. Missing nodes are considered in
 * incoming order and shifted down by one node-height-plus-gap step until their
 * candidate box is clear, producing deterministic non-overlapping placement.
 */
export function mergeLayoutedNodePositions(
  incomingNodes: Node[],
  layoutedNodes: Node[],
  missingNodeIds: ReadonlySet<string>,
): Node[] {
  const layoutedById = new Map(layoutedNodes.map(node => [node.id, node]))
  const reservedBounds = incomingNodes
    .filter(node => !missingNodeIds.has(node.id))
    .map(node => nodeBounds(node))

  return incomingNodes.map(node => {
    if (!missingNodeIds.has(node.id)) return node

    const layouted = layoutedById.get(node.id)
    if (!layouted || !hasFinitePosition(layouted)) {
      throw new Error(`Layout did not return a finite position for node "${node.id}"`)
    }

    const { height } = nodeDimensions(node)
    const step = height + LAYOUT_COLLISION_GAP
    const position = { ...layouted.position }
    let candidateBounds = nodeBounds(node, position)
    while (reservedBounds.some(bounds => overlaps(candidateBounds, bounds))) {
      position.y += step
      candidateBounds = nodeBounds(node, position)
    }
    reservedBounds.push(candidateBounds)
    return { ...node, position }
  })
}
