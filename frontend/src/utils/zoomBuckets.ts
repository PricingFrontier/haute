/**
 * Shared zoom-bucket model for the canvas.
 *
 * The canvas renders at three fidelity levels ("buckets") keyed off the
 * viewport zoom. PipelineNode has always bucketed its rendering this way;
 * the edge-targeting work (whole-node drop targets + zoom-compensated
 * connection radius / hot zones) needs the same thresholds, so the
 * selector and the behavioural constants live here as the single source
 * of truth.
 *
 * The constants are behavioural contract values (pinned by tests):
 * tune them here, not at call sites.
 */

export type ZoomBucket = "full" | "medium" | "compact"

/** Zoom above this renders the full node card. */
export const ZOOM_BUCKET_FULL_MIN = 0.55
/** Zoom above this (and at or below full) renders the medium card. */
export const ZOOM_BUCKET_MEDIUM_MIN = 0.3

export function zoomToBucket(zoom: number): ZoomBucket {
  if (zoom > ZOOM_BUCKET_FULL_MIN) return "full"
  if (zoom > ZOOM_BUCKET_MEDIUM_MIN) return "medium"
  return "compact"
}

/**
 * Zoom-level selector for React Flow's `useStore` — only re-renders
 * subscribers when the zoom crosses a bucket threshold, not on every
 * pixel of zoom change.
 */
export const zoomSelector = (s: { transform: [number, number, number] }): ZoomBucket =>
  zoomToBucket(s.transform[2])

/**
 * `connectionRadius` (flow px) per bucket — how far a drag end can sit
 * from a connector and still snap to it.
 *
 * Deliberately modest: under ConnectionMode.Loose output connectors are
 * snap candidates too, so a large radius would starve the whole-node
 * body-drop arm and turn near-misses into output-onto-output endings.
 * full keeps the xyflow default; medium matches the 28px hot circle;
 * compact matches one compact hot circle and equals the compact dead
 * band, so snap reach never crosses into the body's connect zone from
 * the wrong side.
 */
export const CONNECTION_RADIUS_BY_BUCKET: Record<ZoomBucket, number> = {
  full: 20,
  medium: 28,
  compact: 36,
}

/**
 * Dead-band width G (flow px) per bucket — the strip of node body at the
 * non-complementary end of a connection drag (output end for a forward
 * drag, input end for a backward drag) where a body drop is a silent
 * no-op. Separates the body's connect zone from the connector's own
 * gesture space and from the join zone on the exposed edge just outside
 * the output connector. Derived from the connector hot-circle diameter
 * at each bucket.
 */
export const DEAD_BAND_BY_BUCKET: Record<ZoomBucket, number> = {
  full: 28,
  medium: 32,
  compact: 36,
}

/**
 * The dead band is clamped to this fraction of the node's rendered width
 * so narrow nodes (the 40px edge-join marker root) keep a usable connect
 * zone.
 */
export const DEAD_BAND_MAX_WIDTH_FRACTION = 0.25
