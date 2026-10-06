import { describe, expect, it } from "vitest"

import { NODE_REVEAL_MARGIN_PX, nodeRevealViewport } from "../nodeReveal"

const nearest = { kind: "nearest" } as const
const canvas = { width: 800, height: 600 }
const node = { x: 0, y: 0, width: 200, height: 80 }

describe("nodeRevealViewport - nearest placement", () => {
  it("leaves the view alone when the node is fully visible, even inside the margin", () => {
    // Screen box x 2..202, y 5..85: visible but closer to the edge than the margin.
    const viewport = { x: 2, y: 5, zoom: 1 }

    expect(nodeRevealViewport(viewport, node, canvas, nearest)).toBeNull()
  })

  it("pans left by the least distance when the inspector clips the node's right side", () => {
    // The canvas narrowed to 800px; the node spans 700..900 on screen.
    const viewport = { x: 700, y: 100, zoom: 1 }

    expect(nodeRevealViewport(viewport, node, canvas, nearest)).toEqual({
      x: 800 - NODE_REVEAL_MARGIN_PX - 200,
      y: 100,
      zoom: 1,
    })
  })

  it("pans up by the least distance when the preview pane clips the node's bottom", () => {
    const viewport = { x: 100, y: 560, zoom: 1 }

    expect(nodeRevealViewport(viewport, node, canvas, nearest)).toEqual({
      x: 100,
      y: 600 - NODE_REVEAL_MARGIN_PX - 80,
      zoom: 1,
    })
  })

  it("pans right and down when the node lies beyond the top-left corner", () => {
    const viewport = { x: -150, y: -40, zoom: 1 }

    expect(nodeRevealViewport(viewport, node, canvas, nearest)).toEqual({
      x: NODE_REVEAL_MARGIN_PX,
      y: NODE_REVEAL_MARGIN_PX,
      zoom: 1,
    })
  })

  it("measures the node in screen pixels at the current zoom and never changes zoom", () => {
    // Flow box 1600..1800 at zoom 0.5 → screen 820..920 once offset by x=20.
    const viewport = { x: 20, y: 0, zoom: 0.5 }
    const farNode = { x: 1600, y: 100, width: 200, height: 80 }

    const target = nodeRevealViewport(viewport, farNode, canvas, nearest)

    expect(target).toEqual({ x: 800 - NODE_REVEAL_MARGIN_PX - 1800 * 0.5, y: 0, zoom: 0.5 })
  })

  it("centres on an axis where the node cannot fit inside the margins", () => {
    const shortCanvas = { width: 800, height: 120 }
    const viewport = { x: 100, y: 200, zoom: 1 }

    expect(nodeRevealViewport(viewport, node, shortCanvas, nearest)).toEqual({
      x: 100,
      y: 60 - 40,
      zoom: 1,
    })
  })
})

describe("nodeRevealViewport - centred placement", () => {
  it("centres the node in the canvas at the requested zoom", () => {
    const viewport = { x: 0, y: 0, zoom: 1 }
    const placedNode = { x: 1000, y: 400, width: 200, height: 80 }

    expect(
      nodeRevealViewport(viewport, placedNode, canvas, { kind: "centre", zoom: 0.8 }),
    ).toEqual({ x: 400 - 1100 * 0.8, y: 300 - 440 * 0.8, zoom: 0.8 })
  })

  it("still returns the centred viewport when the node is already visible", () => {
    const viewport = { x: 300, y: 260, zoom: 1 }

    expect(
      nodeRevealViewport(viewport, node, canvas, { kind: "centre", zoom: 1 }),
    ).toEqual({ x: 300, y: 260, zoom: 1 })
  })
})
