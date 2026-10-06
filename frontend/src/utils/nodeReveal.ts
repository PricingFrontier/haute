import type { Viewport } from "@xyflow/react"

/** Screen-pixel gap a revealed node keeps from the canvas edges. */
export const NODE_REVEAL_MARGIN_PX = 40

export type NodeRevealPlacement =
  | { kind: "nearest" }
  | { kind: "centre"; zoom: number }

type FlowRect = { x: number; y: number; width: number; height: number }
type CanvasSize = { width: number; height: number }

/** Offset along one axis that brings a clipped screen span inside `extent`, or 0 if it is fully visible. */
function nearestAxisShift(start: number, size: number, extent: number): number {
  if (start >= 0 && start + size <= extent) return 0
  if (size > extent - 2 * NODE_REVEAL_MARGIN_PX) return extent / 2 - (start + size / 2)
  if (start < 0) return NODE_REVEAL_MARGIN_PX - start
  return extent - NODE_REVEAL_MARGIN_PX - (start + size)
}

/**
 * The viewport that shows `node` (flow coordinates) inside a canvas of `canvas` screen pixels.
 * `nearest` keeps zoom and pans the least distance, returning null when the node is already fully
 * visible; `centre` always centres the node at the requested zoom.
 */
export function nodeRevealViewport(
  viewport: Viewport,
  node: FlowRect,
  canvas: CanvasSize,
  placement: NodeRevealPlacement,
): Viewport | null {
  if (placement.kind === "centre") {
    const { zoom } = placement
    return {
      x: canvas.width / 2 - (node.x + node.width / 2) * zoom,
      y: canvas.height / 2 - (node.y + node.height / 2) * zoom,
      zoom,
    }
  }
  const { zoom } = viewport
  const shiftX = nearestAxisShift(node.x * zoom + viewport.x, node.width * zoom, canvas.width)
  const shiftY = nearestAxisShift(node.y * zoom + viewport.y, node.height * zoom, canvas.height)
  if (shiftX === 0 && shiftY === 0) return null
  return { x: viewport.x + shiftX, y: viewport.y + shiftY, zoom }
}
