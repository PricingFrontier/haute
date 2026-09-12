import { describe, it, expect, afterEach } from "vitest"
import { render, screen, cleanup, fireEvent } from "@testing-library/react"
import Tooltip from "../Tooltip"

const TIP_WIDTH = 200
const TIP_HEIGHT = 40

interface AnchorBox {
  left: number
  top: number
  width: number
  height: number
}

/** The wrapper span is the element carrying `aria-describedby`. */
function anchorOf(child: HTMLElement): HTMLElement {
  const anchor = child.closest<HTMLElement>("[aria-describedby]")
  if (!anchor) throw new Error("tooltip anchor not found")
  return anchor
}

/**
 * Render a tooltip whose anchor sits at `box` in the viewport and whose bubble
 * measures TIP_WIDTH x TIP_HEIGHT, then hover it.
 */
function renderPlaced(box: AnchorBox, side?: "top" | "bottom") {
  render(
    <Tooltip label="Placed tip" side={side}>
      <button>anchor</button>
    </Tooltip>,
  )
  const anchor = anchorOf(screen.getByText("anchor"))
  const tip = screen.getByRole("tooltip")
  anchor.getBoundingClientRect = () =>
    ({
      left: box.left,
      top: box.top,
      width: box.width,
      height: box.height,
      right: box.left + box.width,
      bottom: box.top + box.height,
      x: box.left,
      y: box.top,
      toJSON: () => ({}),
    }) as DOMRect
  Object.defineProperty(tip, "offsetWidth", { configurable: true, value: TIP_WIDTH })
  Object.defineProperty(tip, "offsetHeight", { configurable: true, value: TIP_HEIGHT })
  fireEvent.mouseEnter(anchor)
  return { anchor, tip }
}

describe("Tooltip", () => {
  afterEach(cleanup)

  it("renders the child and the label (role=tooltip)", () => {
    render(
      <Tooltip label="Commit hash explanation">
        <button>hash</button>
      </Tooltip>,
    )
    expect(screen.getByText("hash")).toBeInTheDocument()
    expect(screen.getByRole("tooltip")).toHaveTextContent("Commit hash explanation")
  })

  it("supports the bottom side without throwing", () => {
    render(
      <Tooltip label="below" side="bottom">
        <span>anchor</span>
      </Tooltip>,
    )
    expect(screen.getByRole("tooltip")).toHaveTextContent("below")
  })

  it("links the anchor to the bubble through aria-describedby", () => {
    render(
      <Tooltip label="Described">
        <button>anchor</button>
      </Tooltip>,
    )
    const tip = screen.getByRole("tooltip", { name: "Described" })
    expect(tip.id).not.toBe("")
    expect(anchorOf(screen.getByText("anchor"))).toHaveAttribute("aria-describedby", tip.id)
  })

  it("portals the bubble into document.body, outside the anchor", () => {
    render(
      <Tooltip label="Portalled">
        <button>anchor</button>
      </Tooltip>,
    )
    const tip = screen.getByRole("tooltip")
    expect(tip.parentElement).toBe(document.body)
    expect(anchorOf(screen.getByText("anchor")).contains(tip)).toBe(false)
  })

  it("stays hidden until hovered and hides again when the pointer leaves", () => {
    render(
      <Tooltip label="Hover me">
        <button>anchor</button>
      </Tooltip>,
    )
    const anchor = anchorOf(screen.getByText("anchor"))
    const tip = screen.getByRole("tooltip")
    expect(tip).toHaveClass("hidden")

    fireEvent.mouseEnter(anchor)
    expect(tip).not.toHaveClass("hidden")

    fireEvent.mouseLeave(anchor)
    expect(tip).toHaveClass("hidden")
  })

  it("opens when a focusable child receives focus and closes on blur", () => {
    render(
      <Tooltip label="Focus me">
        <button>anchor</button>
      </Tooltip>,
    )
    const tip = screen.getByRole("tooltip")
    expect(tip).toHaveClass("hidden")

    fireEvent.focus(screen.getByRole("button", { name: "anchor" }))
    expect(tip).not.toHaveClass("hidden")

    fireEvent.blur(screen.getByRole("button", { name: "anchor" }))
    expect(tip).toHaveClass("hidden")
  })

  it("renders with fixed positioning above modals once opened", () => {
    const { tip } = renderPlaced({ left: 400, top: 300, width: 20, height: 20 })
    expect(tip).toHaveClass("fixed")
    expect(tip).toHaveClass("z-[1000]")
    expect(tip.style.visibility).toBe("")
  })

  it("centres the bubble above the anchor when it fits", () => {
    const { tip } = renderPlaced({ left: 400, top: 300, width: 20, height: 20 })
    expect(tip.style.left).toBe(`${410 - TIP_WIDTH / 2}px`)
    expect(tip.style.top).toBe(`${300 - 4 - TIP_HEIGHT}px`)
  })

  it("clamps the bubble inside the left viewport edge", () => {
    const { tip } = renderPlaced({ left: 2, top: 300, width: 10, height: 20 })
    expect(tip.style.left).toBe("8px")
    expect(tip.style.top).toBe(`${300 - 4 - TIP_HEIGHT}px`)
  })

  it("clamps the bubble inside the right viewport edge", () => {
    const { tip } = renderPlaced({
      left: window.innerWidth - 12,
      top: 300,
      width: 10,
      height: 20,
    })
    expect(tip.style.left).toBe(`${window.innerWidth - 8 - TIP_WIDTH}px`)
  })

  it("flips below the anchor when the top would clip", () => {
    const { tip } = renderPlaced({ left: 400, top: 10, width: 20, height: 20 })
    expect(tip.style.top).toBe(`${30 + 4}px`)
  })

  it("flips side=bottom above the anchor when the bottom would clip", () => {
    const top = window.innerHeight - 30
    const { tip } = renderPlaced({ left: 400, top, width: 20, height: 20 }, "bottom")
    expect(tip.style.top).toBe(`${top - 4 - TIP_HEIGHT}px`)
  })

  it("keeps side=bottom below the anchor when it fits", () => {
    const { tip } = renderPlaced({ left: 400, top: 300, width: 20, height: 20 }, "bottom")
    expect(tip.style.top).toBe(`${320 + 4}px`)
  })

  it("closes when anything scrolls while open", () => {
    const { tip } = renderPlaced({ left: 400, top: 300, width: 20, height: 20 })
    expect(tip).not.toHaveClass("hidden")

    fireEvent.scroll(window)
    expect(tip).toHaveClass("hidden")
  })

  it("closes when a scroll container inside the page scrolls", () => {
    const { anchor, tip } = renderPlaced({ left: 400, top: 300, width: 20, height: 20 })
    expect(tip).not.toHaveClass("hidden")

    // `scroll` does not bubble, so only a capture-phase window listener sees it.
    fireEvent.scroll(anchor.parentElement!)
    expect(tip).toHaveClass("hidden")
  })

  it("closes when the window resizes while open", () => {
    const { tip } = renderPlaced({ left: 400, top: 300, width: 20, height: 20 })
    expect(tip).not.toHaveClass("hidden")

    fireEvent(window, new Event("resize"))
    expect(tip).toHaveClass("hidden")
  })
})
