/**
 * Whether the uppermost element under a client point is the empty canvas
 * pane. Anything drawn over the pane — a node, handle, edge, canvas panel or
 * an overlay such as the submodel breadcrumb bar — covers it, so a release
 * there is not on empty canvas.
 */
export function isEmptyCanvasAtPoint(point: { x: number; y: number }): boolean {
  const uppermost = document.elementsFromPoint(point.x, point.y)[0]
  return uppermost?.classList.contains("react-flow__pane") ?? false
}
