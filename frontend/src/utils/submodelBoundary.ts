/**
 * Submodel boundary derivation (node-explosion design §3.5).
 *
 * A submodel's boundary — its input/output PORT nodes and the dashed links tying
 * them to the internal nodes — is NOT stored in the submodel's own graph. It is
 * DERIVED from the PARENT graph's edges to and from the submodel node.
 *
 * The boundary reflects the wrapper's OWN frames, NOT the external nodes it wires
 * to, and is invariant to external rewiring beyond the frame set itself. It is
 * literally one wrapper-boundary component per frame, in or out:
 *   - OUTPUT: one component per internal EMITTING node (distinct
 *     `out__<childId>`), regardless of how many external nodes consume it — the
 *     output frame is 1-1 with the node that produces it. Adding or removing an
 *     external consumer never changes the inside.
 *   - INPUT: one component per cross-boundary input LINK (frame). Inputs are 1-1
 *     with external links because different links carry different frames, so they
 *     are NOT collapsed by external source, NOR by the internal node-input
 *     connector they feed: `S→C1` and `S→C2` are two frames; `S1→C` and `S2→C`
 *     are two frames feeding the same node.
 *
 * Boundary components are labelled by the wrapped node they bind to (the frame) —
 * a provisional label until per-frame names land with the wrapper output model.
 *
 * Both the live drill-in (useSubmodelNavigation) and the read-only peek
 * (SubmodelPeekBody) build the SAME boundary from this one helper, so the peek is
 * a faithful window into the canvas you'd land on if you drilled in.
 */
import type { Node, Edge } from "@xyflow/react"
import { NODE_TYPES } from "./nodeTypes"
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
  /** Parent-canvas nodes — retained for the call signature; not read here. */
  parentNodes: Node[]
  /** Parent-canvas edges — the boundary is derived entirely from these. */
  parentEdges: Edge[]
  /** Ids of the submodel's internal nodes; links to non-members are dropped. */
  childIds: Set<string>
}): SubmodelBoundary {
  const { smNodeId, parentEdges, childIds } = params
  // params.parentNodes is intentionally unused: a boundary component reflects the
  // wrapper's own frame (labelled by the wrapped node it binds to), not the
  // external neighbour it happens to connect to.
  const portNodes: Node[] = []
  const boundaryEdges: Edge[] = []

  // INPUT — one component per cross-boundary input LINK (frame). Each external
  // link feeding a CURRENT child becomes its own input port: NOT collapsed by
  // source, so two links to the same child are two frames. The membership filter
  // drops a link to a stale/missing child — or an `__unconnected__` handle — so
  // it produces no orphan, edgeless port pill. Deduped by port id to guard
  // against a duplicated identical crossing producing a colliding React key.
  const seenInputPorts = new Set<string>()
  for (const e of parentEdges) {
    if (e.target !== smNodeId) continue
    const handle = e.targetHandle
    const childId = handle ? handle.replace("in__", "") : "__unconnected__"
    if (!childIds.has(childId)) continue
    const portId = `port_in__${childId}__${e.source}`
    if (seenInputPorts.has(portId)) continue
    seenInputPorts.add(portId)
    portNodes.push(
      validateReactFlowNode({
        id: portId,
        type: NODE_TYPES.SUBMODEL_PORT,
        position: { x: 0, y: 0 },
        data: { label: childId, portDirection: "input", portName: childId },
      }),
    )
    boundaryEdges.push({
      id: `e_${portId}_${childId}`,
      source: portId,
      target: childId,
      type: "default",
      animated: false,
      style: { ...BOUNDARY_EDGE_STYLE },
    } as Edge)
  }

  // OUTPUT — one component per internal EMITTING node (1-1 with the output
  // frame), deduped so multiple external consumers never multiply the port. The
  // membership filter drops a stale `out__<gone>` handle.
  const emittingChildIds = new Set<string>()
  for (const e of parentEdges) {
    if (e.source !== smNodeId || !e.sourceHandle) continue
    const childId = (e.sourceHandle as string).replace("out__", "")
    if (!childIds.has(childId)) continue
    emittingChildIds.add(childId)
  }
  for (const childId of emittingChildIds) {
    const portId = `port_out__${childId}`
    portNodes.push(
      validateReactFlowNode({
        id: portId,
        type: NODE_TYPES.SUBMODEL_PORT,
        position: { x: 0, y: 0 },
        data: { label: childId, portDirection: "output", portName: childId },
      }),
    )
    boundaryEdges.push({
      id: `e_${childId}_${portId}`,
      source: childId,
      target: portId,
      type: "default",
      animated: false,
      style: { ...BOUNDARY_EDGE_STYLE },
    } as Edge)
  }

  return { portNodes, boundaryEdges }
}
