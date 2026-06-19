/**
 * Submodel boundary derivation (node-explosion design §3.5).
 *
 * A submodel's boundary — its input/output PORT nodes and the dashed links tying
 * them to the internal nodes — is NOT stored in the submodel's own graph. It is
 * DERIVED from the PARENT graph's edges to and from the submodel node:
 *   - an edge whose `target` is the submodel node (`targetHandle = in__<childId>`)
 *     becomes an INPUT port — one per distinct parent source — linked to <childId>;
 *   - an edge whose `source` is the submodel node (`sourceHandle = out__<childId>`)
 *     becomes an OUTPUT port — one per distinct parent target — linked from <childId>.
 *
 * Both the live drill-in (useSubmodelNavigation) and the read-only peek
 * (SubmodelPeekBody) build the SAME boundary from this one helper, so the peek is
 * a faithful window into the canvas you'd land on if you drilled in.
 */
import type { Node, Edge } from "@xyflow/react"
import { NODE_TYPES } from "./nodeTypes"
import { nodeData } from "../types/node"
import { validateReactFlowNode } from "../types/guards"

export interface SubmodelBoundary {
  /** SUBMODEL_PORT nodes (inputs + outputs) to add to the submodel's canvas. */
  portNodes: Node[]
  /** Dashed port↔child edges. `style.strokeDasharray` marks them as boundary. */
  boundaryEdges: Edge[]
}

/** Dashed + faded — the visual marker that an edge crosses the boundary. */
const BOUNDARY_EDGE_STYLE = { strokeDasharray: "6 3", opacity: 0.5 } as const

export function buildSubmodelBoundary(params: {
  /** The submodel node's id on the parent canvas (`submodel__<name>`). */
  smNodeId: string
  /** Parent-canvas nodes — used only to resolve port labels. */
  parentNodes: Node[]
  /** Parent-canvas edges — the boundary is derived entirely from these. */
  parentEdges: Edge[]
  /** Ids of the submodel's internal nodes; links to non-members are dropped. */
  childIds: Set<string>
}): SubmodelBoundary {
  const { smNodeId, parentNodes, parentEdges, childIds } = params
  const parentNodeMap = new Map(parentNodes.map((n) => [n.id, n]))
  const portNodes: Node[] = []
  const boundaryEdges: Edge[] = []

  // Input ports — one per distinct parent SOURCE feeding a CURRENT child. The
  // membership filter runs BEFORE grouping (mirroring the output path below), so
  // a source whose only links reference a stale/missing child — or an
  // `__unconnected__` handle — produces no port at all, never an orphan,
  // edgeless port pill.
  const inputPortEdges = parentEdges.filter((e) => e.target === smNodeId)
  const inputsBySource = new Map<string, string[]>()
  for (const e of inputPortEdges) {
    const handle = e.targetHandle
    const childId = handle ? handle.replace("in__", "") : "__unconnected__"
    if (!childIds.has(childId)) continue
    const targets = inputsBySource.get(e.source) || []
    targets.push(childId)
    inputsBySource.set(e.source, targets)
  }
  for (const [srcId, targetChildIds] of inputsBySource) {
    const srcNode = parentNodeMap.get(srcId)
    const label = srcNode ? String(nodeData(srcNode).label || srcId) : srcId
    const portId = `port_in__${srcId}`
    portNodes.push(
      validateReactFlowNode({
        id: portId,
        type: NODE_TYPES.SUBMODEL_PORT,
        position: { x: 0, y: 0 },
        data: { label, portDirection: "input", portName: label },
      }),
    )
    for (const childId of [...new Set(targetChildIds)]) {
      boundaryEdges.push({
        id: `e_${portId}_${childId}`,
        source: portId,
        target: childId,
        type: "default",
        animated: false,
        style: { ...BOUNDARY_EDGE_STYLE },
      } as Edge)
    }
  }

  // Output ports — one per distinct parent TARGET drawing from the submodel.
  const outputPortEdges = parentEdges.filter((e) => e.source === smNodeId && e.sourceHandle)
  const outputsByTarget = new Map<string, string[]>()
  for (const e of outputPortEdges) {
    const childId = (e.sourceHandle as string).replace("out__", "")
    if (!childIds.has(childId)) continue
    const sources = outputsByTarget.get(e.target) || []
    sources.push(childId)
    outputsByTarget.set(e.target, sources)
  }
  for (const [tgtId, sourceChildIds] of outputsByTarget) {
    const tgtNode = parentNodeMap.get(tgtId)
    const label = tgtNode ? String(nodeData(tgtNode).label || tgtId) : tgtId
    const portId = `port_out__${tgtId}`
    portNodes.push(
      validateReactFlowNode({
        id: portId,
        type: NODE_TYPES.SUBMODEL_PORT,
        position: { x: 0, y: 0 },
        data: { label, portDirection: "output", portName: label },
      }),
    )
    for (const childId of [...new Set(sourceChildIds)]) {
      boundaryEdges.push({
        id: `e_${childId}_${portId}`,
        source: childId,
        target: portId,
        type: "default",
        animated: false,
        style: { ...BOUNDARY_EDGE_STYLE },
      } as Edge)
    }
  }

  return { portNodes, boundaryEdges }
}
