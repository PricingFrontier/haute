import type { Node, Edge } from "@xyflow/react"

const DEFAULT_NODE_WIDTH = 240
const DEFAULT_NODE_HEIGHT = 70
const LAYOUT_COLLISION_GAP = 60

type ElkLayout = {
  children?: Array<{ id: string; x?: number; y?: number }>
}

type ElkEngine = {
  layout: (graph: unknown) => Promise<ElkLayout>
}

let elkPromise: Promise<ElkEngine> | null = null

async function getElk(): Promise<ElkEngine> {
  if (!elkPromise) {
    elkPromise = import("elkjs/lib/elk.bundled.js").then(
      ({ default: ELK }) => new ELK() as unknown as ElkEngine,
    )
  }
  return elkPromise
}

/**
 * Cluster nearby coordinate values and snap each cluster to its median.
 * E.g. y-values [100, 103, 108, 250, 253] with threshold 20
 * → two clusters: [100,103,108]→103, [250,253]→250
 */
function clusterSnap(values: number[], threshold: number): Map<number, number> {
  const sorted = [...new Set(values)].sort((a, b) => a - b)
  const snap = new Map<number, number>()

  let i = 0
  while (i < sorted.length) {
    let j = i
    while (j < sorted.length && sorted[j] - sorted[i] <= threshold) {
      j++
    }
    const cluster = sorted.slice(i, j)
    const median = cluster[Math.floor(cluster.length / 2)]
    for (const v of cluster) {
      snap.set(v, median)
    }
    i = j
  }
  return snap
}

/** Snap positions so nodes at nearly-the-same x or y align exactly. */
function alignPositions(posMap: Map<string, { x: number; y: number }>, threshold = 20): void {
  const positions = [...posMap.values()]
  const xSnap = clusterSnap(positions.map((p) => p.x), threshold)
  const ySnap = clusterSnap(positions.map((p) => p.y), threshold)

  for (const pos of posMap.values()) {
    pos.x = xSnap.get(pos.x) ?? pos.x
    pos.y = ySnap.get(pos.y) ?? pos.y
  }
}

export async function getLayoutedElements(nodes: Node[], edges: Edge[]): Promise<Node[]> {
  const elkGraph = {
    id: "root",
    layoutOptions: {
      "elk.algorithm": "layered",
      "elk.direction": "RIGHT",
      "elk.spacing.nodeNode": "60",
      "elk.layered.spacing.nodeNodeBetweenLayers": "120",
      "elk.layered.crossingMinimization.strategy": "LAYER_SWEEP",
    },
    children: nodes.map((n) => ({
      id: n.id,
      width: 240,
      height: 70,
    })),
    edges: edges.map((e) => ({
      id: e.id,
      sources: [e.source],
      targets: [e.target],
    })),
  }

  const elk = await getElk()
  const layout = await elk.layout(elkGraph)
  const posMap = new Map<string, { x: number; y: number }>()
  for (const child of layout.children || []) {
    posMap.set(child.id, { x: child.x ?? 0, y: child.y ?? 0 })
  }

  alignPositions(posMap)

  return nodes.map((n) => ({
    ...n,
    position: posMap.get(n.id) || n.position,
  }))
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
