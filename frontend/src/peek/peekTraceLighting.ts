/**
 * Peek trace lighting (#3, Facet 1 "inside").
 *
 * When a wrapper's Peek is open, a data-path that crosses the wrapper boundary
 * should light its relevant INTERNAL segment inside the Peek — the only place
 * the children are actually drawn. This pure helper decides which peek nodes and
 * edges stay bright (everything else dims), from three hover sources:
 *
 *   1. Internal self-hover (hovering a node inside the Peek) → 1-hop, undirected.
 *      Takes priority: you're inspecting the internals directly.
 *   2. Hovering the wrapper's own card on the parent canvas → everything inside
 *      is relevant, so light the whole peek graph.
 *   3. Hovering an external node wired to the wrapper → light the internal cone
 *      tied to the boundary port(s) it crosses. An INPUT port lights its
 *      DOWNSTREAM cone (what the input feeds); an OUTPUT port lights its UPSTREAM
 *      cone (what feeds the output). Seeds are derived from the parent edges:
 *      the input port id encodes the external source (`port_in__<child>__<src>`),
 *      and an output consumer is matched via the parent edge to the wrapper.
 *
 * When no source applies the result is inactive and the Peek renders unchanged.
 */
import type { Node, Edge } from "@xyflow/react"

export interface PeekLighting {
  /** Whether any lighting is applied. When false, render the peek graph as-is. */
  active: boolean
  /** Peek node ids to keep bright; all others dim. */
  litNodeIds: Set<string>
  /** Peek edge ids to keep bright; all others dim. */
  litEdgeIds: Set<string>
}

const INACTIVE: PeekLighting = {
  active: false,
  litNodeIds: new Set<string>(),
  litEdgeIds: new Set<string>(),
}

interface AdjEntry {
  node: string
  edgeId: string
}

function pushAdj(map: Map<string, AdjEntry[]>, key: string, entry: AdjEntry) {
  const list = map.get(key)
  if (list) list.push(entry)
  else map.set(key, [entry])
}

export function computePeekTraceLighting(params: {
  /** The peek's nodes (internal + boundary port nodes). */
  peekNodes: Node[]
  /** The peek's edges (internal + boundary port edges). */
  peekEdges: Edge[]
  /** Parent-canvas edges — used to map an external hover to a boundary port. */
  parentEdges: Edge[]
  /** The wrapper node's id on the parent canvas (`submodel__<name>`). */
  wrapperNodeId: string
  /** Node hovered on the PARENT canvas (from the shared UI store), or null. */
  hoveredNodeId: string | null
  /** Node hovered INSIDE the peek itself, or null. */
  peekHoverId: string | null
}): PeekLighting {
  const { peekNodes, peekEdges, parentEdges, wrapperNodeId, hoveredNodeId, peekHoverId } = params
  const nodeIdSet = new Set(peekNodes.map((n) => n.id))

  // 1) Internal self-hover wins: 1-hop, undirected — mirrors canvas hover.
  if (peekHoverId && nodeIdSet.has(peekHoverId)) {
    const litNodeIds = new Set<string>([peekHoverId])
    const litEdgeIds = new Set<string>()
    for (const e of peekEdges) {
      if (e.source === peekHoverId) {
        litNodeIds.add(e.target)
        litEdgeIds.add(e.id)
      } else if (e.target === peekHoverId) {
        litNodeIds.add(e.source)
        litEdgeIds.add(e.id)
      }
    }
    return { active: true, litNodeIds, litEdgeIds }
  }

  if (!hoveredNodeId) return INACTIVE

  // 2) Hovering the wrapper card itself → everything inside is relevant.
  if (hoveredNodeId === wrapperNodeId) {
    return {
      active: true,
      litNodeIds: new Set(nodeIdSet),
      litEdgeIds: new Set(peekEdges.map((e) => e.id)),
    }
  }

  // 3) Hovering an external node connected to this wrapper → boundary cones.
  const inputSeeds: string[] = []
  const outputSeeds: string[] = []
  for (const e of parentEdges) {
    if (e.target === wrapperNodeId && e.source === hoveredNodeId) {
      const child = (e.targetHandle ?? "").replace("in__", "")
      const port = `port_in__${child}__${hoveredNodeId}`
      if (nodeIdSet.has(port)) inputSeeds.push(port)
    } else if (e.source === wrapperNodeId && e.target === hoveredNodeId) {
      const child = (e.sourceHandle ?? "").replace("out__", "")
      const port = `port_out__${child}`
      if (nodeIdSet.has(port)) outputSeeds.push(port)
    }
  }
  if (inputSeeds.length === 0 && outputSeeds.length === 0) return INACTIVE

  const forward = new Map<string, AdjEntry[]>()
  const backward = new Map<string, AdjEntry[]>()
  for (const e of peekEdges) {
    pushAdj(forward, e.source, { node: e.target, edgeId: e.id })
    pushAdj(backward, e.target, { node: e.source, edgeId: e.id })
  }

  const litNodeIds = new Set<string>()
  const litEdgeIds = new Set<string>()
  // Directed reachability from each seed; per-walk `visited` so the input and
  // output cones don't prematurely block each other while sharing the lit sets.
  const walk = (seeds: string[], adj: Map<string, AdjEntry[]>) => {
    const visited = new Set<string>()
    const stack = [...seeds]
    while (stack.length > 0) {
      const u = stack.pop()!
      if (visited.has(u)) continue
      visited.add(u)
      litNodeIds.add(u)
      for (const { node, edgeId } of adj.get(u) ?? []) {
        litEdgeIds.add(edgeId)
        if (!visited.has(node)) stack.push(node)
      }
    }
  }
  walk(inputSeeds, forward)
  walk(outputSeeds, backward)
  return { active: true, litNodeIds, litEdgeIds }
}
