/**
 * Whole-node drop-target resolution for connection drags.
 *
 * Pure / DOM-thin helpers consumed by the body-drop arm of the
 * onConnectEnd gesture arbiter in `hooks/useEdgeHandlers.ts`:
 *
 * - `topmostNodeAtPoint` / `pointerExactlyOnConnector` — DOM-thin
 *   `document.elementsFromPoint` walks (screen coordinates).
 * - `inDeadGap` / `resolveBodyDrop` — pure geometry over React Flow's
 *   internal node records (flow coordinates).
 *
 * The resolver picks the geometrically nearest complementary connector,
 * occupied or not (Nick's ruling 5) — occupancy conflicts surface via
 * `commitConnection`'s existing rejections/toasts, never by silent
 * re-targeting here.
 */
import {
  DEAD_BAND_BY_BUCKET,
  DEAD_BAND_MAX_WIDTH_FRACTION,
  type ZoomBucket,
} from "./zoomBuckets"

export type XYPoint = { x: number; y: number }

export type ConnectorKind = "source" | "target"

/** Subset of xyflow's HandleBounds entries the resolver needs. */
export type ConnectorBounds = {
  id?: string | null
  /** Position relative to the node, in flow px (unscaled). */
  x: number
  y: number
  width: number
  height: number
}

/** Subset of xyflow's InternalNode the geometry helpers need. */
export type InternalNodeGeometry = {
  internals: {
    positionAbsolute: XYPoint
    handleBounds?: {
      source?: ConnectorBounds[] | null
      target?: ConnectorBounds[] | null
    } | null
  }
  measured?: { width?: number | null; height?: number | null }
}

export type ResolvedBodyDrop = { handleId: string | null }

/**
 * Topmost canvas node under a screen point, by DOM paint order —
 * matches what the user sees when nodes overlap. Returns the node id
 * from `.react-flow__node[data-id]`, or null when the point is over
 * pane, edge, or chrome only.
 */
export function topmostNodeAtPoint(point: XYPoint): string | null {
  if (typeof document.elementsFromPoint !== "function") return null
  for (const element of document.elementsFromPoint(point.x, point.y)) {
    const nodeElement = element.closest?.(".react-flow__node[data-id]")
    const nodeId = nodeElement?.getAttribute("data-id")
    if (nodeId) return nodeId
  }
  return null
}

/**
 * True when the pointer is exactly over the given connector's DOM
 * element (its `::after` hit circle hit-tests to the element itself,
 * including the edge-join output connector's outward-offset circle).
 *
 * Used to strip all snap assistance from the output-onto-output join
 * gesture (ruling 2/6): xyflow may report a snapped `toHandle` from up
 * to `connectionRadius` away, but the gesture only fires on an exact
 * hit. Near-misses fall through to the body-drop arm.
 */
export function pointerExactlyOnConnector(
  point: XYPoint,
  nodeId: string,
  handleId: string | null,
  kind: ConnectorKind,
): boolean {
  if (typeof document.elementsFromPoint !== "function") return false
  for (const element of document.elementsFromPoint(point.x, point.y)) {
    const handleElement = element.closest?.(".react-flow__handle")
    if (!handleElement) continue
    if (!handleElement.classList.contains(kind)) continue
    if (handleElement.getAttribute("data-nodeid") !== nodeId) continue
    if ((handleElement.getAttribute("data-handleid") ?? null) === (handleId ?? null)) {
      return true
    }
  }
  return false
}

/** Dead-band width G for a node: bucket constant clamped to 25% of width. */
export function deadBandWidth(node: InternalNodeGeometry, bucket: ZoomBucket): number {
  const nodeWidth = node.measured?.width ?? 0
  return Math.min(DEAD_BAND_BY_BUCKET[bucket], nodeWidth * DEAD_BAND_MAX_WIDTH_FRACTION)
}

/**
 * True when a body drop lands in the node's dead band (ruling 3): the
 * full-height strip of width G at the node's NON-complementary end —
 * the output (right) end for a forward drag (`fromKind === "source"`),
 * mirrored to the input (left) end for a backward drag.
 *
 * The band is unbounded outward (no far-edge cutoff): the edge-join
 * output connector's offset hit circle extends past the node rect, and
 * DOM hit-testing attributes those points to the node — they must stay
 * dead rather than fall through to a body connect.
 */
export function inDeadGap(
  dropPosFlow: XYPoint,
  node: InternalNodeGeometry,
  fromKind: ConnectorKind,
  bucket: ZoomBucket,
): boolean {
  const left = node.internals.positionAbsolute.x
  const right = left + (node.measured?.width ?? 0)
  const band = deadBandWidth(node, bucket)
  return fromKind === "source"
    ? dropPosFlow.x >= right - band
    : dropPosFlow.x <= left + band
}

function connectorCentre(node: InternalNodeGeometry, connector: ConnectorBounds): XYPoint {
  return {
    x: node.internals.positionAbsolute.x + connector.x + connector.width / 2,
    y: node.internals.positionAbsolute.y + connector.y + connector.height / 2,
  }
}

/**
 * Candidate filter: hidden connectors never win a body drop.
 *
 * - Zero-area connectors (the `handle-hidden` class collapses
 *   SubmodelNode's per-port `in__<port>` resolver connectors to 0×0)
 *   are always excluded.
 * - Belt-and-braces for a CSS regression that re-inflates them to the
 *   stylesheet's 2×2 floor: ≤4px² candidates are excluded too, but only
 *   when the node also offers a larger connector. Edge-join nodes'
 *   connectors are ALL 2×2 by design (their hover/size suppression keeps
 *   them out of the visible-dot rules), so an absolute ≤4px² cut would
 *   wrongly disable edge-join body drops — the relative form keeps them.
 */
function visibleCandidates(candidates: ConnectorBounds[]): ConnectorBounds[] {
  const nonZero = candidates.filter((c) => c.width > 0 && c.height > 0)
  const large = nonZero.filter((c) => c.width * c.height > 4)
  return large.length > 0 ? large : nonZero
}

/**
 * Resolve a node-body drop to a connector of the wanted kind.
 *
 * Returns null when the node has no (visible) connector of that kind —
 * the node still wins the drop (silent no-op), it never falls through
 * to a hidden edge underneath.
 *
 * With 2+ candidates: geometrically nearest in flow coordinates,
 * occupied or not (ruling 5). Ties break topmost (smallest y), then
 * first-rendered. The returned id is verbatim from the rendered
 * connector (`"__default_target"` included — `commitConnection`
 * normalises sentinels); an empty-string id is normalised to null here
 * so `""` can never reach the store.
 */
export function resolveBodyDrop(
  node: InternalNodeGeometry,
  wantedKind: ConnectorKind,
  dropPosFlow: XYPoint,
): ResolvedBodyDrop | null {
  const candidates = visibleCandidates(node.internals.handleBounds?.[wantedKind] ?? [])
  if (candidates.length === 0) return null

  let best = candidates[0]
  if (candidates.length > 1) {
    let bestDistance = Infinity
    for (const candidate of candidates) {
      const centre = connectorCentre(node, candidate)
      const distance = Math.hypot(centre.x - dropPosFlow.x, centre.y - dropPosFlow.y)
      if (distance < bestDistance || (distance === bestDistance && candidate.y < best.y)) {
        best = candidate
        bestDistance = distance
      }
    }
  }
  const handleId = best.id ?? null
  return { handleId: handleId === "" ? null : handleId }
}
