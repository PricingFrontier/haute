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

/** The bubble a trigger is described by, resolved through its `aria-describedby`. */
function bubbleOf(trigger: HTMLElement): HTMLElement {
  const id = trigger.getAttribute("aria-describedby")
  if (!id) throw new Error("trigger has no aria-describedby")
  const bubble = document.getElementById(id)
  if (!bubble) throw new Error(`no element with id ${id}`)
  return bubble
}

/** Render a single-button tooltip and return its button, hover wrapper and bubble. */
function renderButton(label: string, side?: "top" | "bottom") {
  render(
    <Tooltip label={label} side={side}>
      <button>anchor</button>
    </Tooltip>,
  )
  const trigger = screen.getByRole("button", { name: "anchor" })
  return { trigger, anchor: trigger.parentElement!, tip: bubbleOf(trigger) }
}

/**
 * Render a tooltip whose hover wrapper sits at `box` in the viewport and whose
 * bubble measures TIP_WIDTH x TIP_HEIGHT, then hover it.
 */
function renderPlaced(box: AnchorBox, side?: "top" | "bottom") {
  const { anchor, tip } = renderButton("Placed tip", side)
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

  it("describes the element child itself, not the hover wrapper", () => {
    render(
      <Tooltip label="Described">
        <button>Go</button>
      </Tooltip>,
    )
    const button = screen.getByRole("button", { name: "Go" })
    const tip = screen.getByRole("tooltip", { name: "Described" })
    expect(tip.id).not.toBe("")
    expect(button).toHaveAttribute("aria-describedby", tip.id)
    expect(button).toHaveAccessibleDescription("Described")
    expect(button.parentElement).not.toHaveAttribute("aria-describedby")
  })

  it("hands the tooltip id to a function child so it can describe the real control", () => {
    render(
      <Tooltip label="Radio help">
        {(describedBy) => (
          <label>
            x
            <input type="radio" aria-describedby={describedBy} />
          </label>
        )}
      </Tooltip>,
    )
    const radio = screen.getByRole("radio", { name: "x" })
    expect(radio).toHaveAccessibleDescription("Radio help")
    expect(bubbleOf(radio)).toBe(screen.getByRole("tooltip", { name: "Radio help" }))
    expect(radio.closest("label")).not.toHaveAttribute("aria-describedby")
    expect(radio.closest("label")!.parentElement).not.toHaveAttribute("aria-describedby")
  })

  it("keeps an element child's existing description and appends the tooltip", () => {
    render(
      <>
        <span id="other">Other</span>
        <Tooltip label="Described">
          <button aria-describedby="other">Go</button>
        </Tooltip>
      </>,
    )
    const button = screen.getByRole("button", { name: "Go" })
    const tip = screen.getByRole("tooltip", { name: "Described" })
    expect(button).toHaveAttribute("aria-describedby", `other ${tip.id}`)
    expect(button).toHaveAccessibleDescription("Other Described")
  })

  it("does not describe an element child whose aria-label already says the label", () => {
    render(
      <Tooltip label="Same">
        <button aria-label="Same" />
      </Tooltip>,
    )
    const button = screen.getByRole("button", { name: "Same" })
    expect(button).not.toHaveAttribute("aria-describedby")
    expect(button.parentElement).not.toHaveAttribute("aria-describedby")
    expect(button).toHaveAccessibleDescription("")

    const tip = screen.getByRole("tooltip", { name: "Same" })
    expect(tip).toHaveClass("hidden")
    fireEvent.mouseEnter(button.parentElement!)
    expect(tip).not.toHaveClass("hidden")
  })

  it("puts aria-describedby on the wrapper only when the child is not an element", () => {
    render(<Tooltip label="Text help">plain text</Tooltip>)
    const textTip = screen.getByRole("tooltip", { name: "Text help" })
    expect(screen.getByText("plain text")).toHaveAttribute("aria-describedby", textTip.id)

    cleanup()
    render(
      <Tooltip label="Fragment help">
        <>
          <span>first</span>
          <span>second</span>
        </>
      </Tooltip>,
    )
    const fragmentTip = screen.getByRole("tooltip", { name: "Fragment help" })
    const wrapper = screen.getByText("first").parentElement!
    expect(wrapper).toHaveAttribute("aria-describedby", fragmentTip.id)
    expect(screen.getByText("first")).not.toHaveAttribute("aria-describedby")
  })

  it("portals the bubble into document.body, outside the anchor", () => {
    const { anchor, tip } = renderButton("Portalled")
    expect(tip.parentElement).toBe(document.body)
    expect(anchor.contains(tip)).toBe(false)
  })

  it("stays hidden until hovered and hides again when the pointer leaves", () => {
    const { anchor, tip } = renderButton("Hover me")
    expect(tip).toHaveClass("hidden")

    fireEvent.mouseEnter(anchor)
    expect(tip).not.toHaveClass("hidden")

    fireEvent.mouseLeave(anchor)
    expect(tip).toHaveClass("hidden")
  })

  it("opens when a focusable child receives focus and closes on blur", () => {
    const { trigger, tip } = renderButton("Focus me")
    expect(tip).toHaveClass("hidden")

    fireEvent.focus(trigger)
    expect(tip).not.toHaveClass("hidden")

    fireEvent.blur(trigger)
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
