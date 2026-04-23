import type { Node, Edge } from "@xyflow/react"

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
