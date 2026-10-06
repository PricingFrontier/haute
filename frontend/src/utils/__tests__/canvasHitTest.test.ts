import { describe, it, expect, afterEach, vi } from "vitest"
import { isEmptyCanvasAtPoint } from "../canvasHitTest"

function element(className: string, parent?: Element): Element {
  const el = document.createElement("div")
  el.className = className
  parent?.appendChild(el)
  return el
}

function stack(...elements: Element[]) {
  // jsdom has no layout, so the hit list is supplied uppermost first.
  const elementsFromPoint = vi.fn(() => elements)
  Object.defineProperty(document, "elementsFromPoint", { value: elementsFromPoint, configurable: true })
  return elementsFromPoint
}

describe("isEmptyCanvasAtPoint", () => {
  afterEach(() => {
    delete (document as { elementsFromPoint?: unknown }).elementsFromPoint
  })

  it("is true when the pane is the uppermost element under the point", () => {
    const elementsFromPoint = stack(element("react-flow__pane selection"), element("react-flow__renderer"))

    expect(isEmptyCanvasAtPoint({ x: 12, y: 34 })).toBe(true)
    expect(elementsFromPoint).toHaveBeenCalledWith(12, 34)
  })

  it.each([
    ["an overlay without React Flow classes (the breadcrumb bar)", "absolute top-2 z-10 flex"],
    ["a node", "react-flow__node"],
    ["a canvas panel", "react-flow__panel"],
  ])("is false when %s covers the pane", (_label, className) => {
    stack(element(className), element("react-flow__pane"))

    expect(isEmptyCanvasAtPoint({ x: 0, y: 0 })).toBe(false)
  })

  it("is false for an element nested inside the pane's subtree that is not the pane", () => {
    const pane = element("react-flow__pane")
    stack(element("react-flow__edge-path", pane), pane)

    expect(isEmptyCanvasAtPoint({ x: 0, y: 0 })).toBe(false)
  })

  it("is false when nothing is under the point", () => {
    stack()

    expect(isEmptyCanvasAtPoint({ x: 0, y: 0 })).toBe(false)
  })
})
