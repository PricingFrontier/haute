import { describe, it, expect, vi, afterEach } from "vitest"
import { render, screen, cleanup, fireEvent } from "@testing-library/react"
import ConnectionDropMenu from "../ConnectionDropMenu"
import { NODE_TYPES, type NodeTypeValue } from "../../utils/nodeTypes"

function renderMenu(existingSingletonTypes: ReadonlySet<NodeTypeValue> = new Set()) {
  const onSelect = vi.fn()
  const onClose = vi.fn()
  render(
    <ConnectionDropMenu
      x={120}
      y={80}
      existingSingletonTypes={existingSingletonTypes}
      onSelect={onSelect}
      onClose={onClose}
    />,
  )
  return { onSelect, onClose }
}

describe("ConnectionDropMenu", () => {
  afterEach(cleanup)

  it("lists Edge Join first, then the data-taking palette types in palette order", () => {
    renderMenu()

    expect(screen.getAllByRole("menuitem").map((item) => item.textContent)).toEqual([
      "Edge Join",
      "Source Switch",
      "Data Output",
      "Polars",
      "Expander",
      "Banding",
      "Rating Step",
      "Explore",
      "Model Training",
      "Model Scoring",
      "Optimisation",
      "Apply Optimisation",
    ])
  })

  it("reports the chosen type", () => {
    const { onSelect } = renderMenu()

    fireEvent.click(screen.getByRole("menuitem", { name: "Banding" }))

    expect(onSelect).toHaveBeenCalledWith(NODE_TYPES.BANDING)
  })

  it("disables an occupied singleton", () => {
    const { onSelect } = renderMenu(new Set([NODE_TYPES.LIVE_SWITCH]))

    const item = screen.getByRole("menuitem", { name: "Source Switch" })
    expect(item).toBeDisabled()
    fireEvent.click(item)
    expect(onSelect).not.toHaveBeenCalled()
  })

  it("focuses the first item and moves focus with the arrow keys, skipping disabled items", () => {
    renderMenu(new Set([NODE_TYPES.LIVE_SWITCH]))
    const menu = screen.getByRole("menu", { name: "Add node" })

    expect(screen.getByRole("menuitem", { name: "Edge Join" })).toHaveFocus()
    fireEvent.keyDown(menu, { key: "ArrowDown" })
    expect(screen.getByRole("menuitem", { name: "Data Output" })).toHaveFocus()
    fireEvent.keyDown(menu, { key: "ArrowUp" })
    fireEvent.keyDown(menu, { key: "ArrowUp" })
    expect(screen.getByRole("menuitem", { name: "Apply Optimisation" })).toHaveFocus()
  })

  it("moves up and left to keep the whole menu inside the viewport", () => {
    vi.spyOn(HTMLElement.prototype, "offsetWidth", "get").mockReturnValue(190)
    vi.spyOn(HTMLElement.prototype, "offsetHeight", "get").mockReturnValue(400)
    vi.spyOn(window, "innerWidth", "get").mockReturnValue(250)
    vi.spyOn(window, "innerHeight", "get").mockReturnValue(450)
    renderMenu()

    expect(screen.getByRole("menu")).toHaveStyle({ left: "52px", top: "42px" })
    vi.restoreAllMocks()
  })

  it("closes on Escape and on a mousedown outside the menu, but not inside it", () => {
    const { onClose } = renderMenu()

    fireEvent.mouseDown(screen.getByRole("menuitem", { name: "Polars" }))
    expect(onClose).not.toHaveBeenCalled()
    fireEvent.keyDown(screen.getByRole("menu"), { key: "Escape" })
    expect(onClose).toHaveBeenCalledTimes(1)
    fireEvent.mouseDown(document.body)
    expect(onClose).toHaveBeenCalledTimes(2)
  })

  it("closes on Escape after focus has left the menu, and claims the key from the canvas shortcuts", () => {
    const { onClose } = renderMenu()
    const outside = document.createElement("button")
    document.body.appendChild(outside)
    outside.focus()
    const windowEscape = vi.fn((event: KeyboardEvent) => event.defaultPrevented)
    window.addEventListener("keydown", windowEscape)

    fireEvent.keyDown(outside, { key: "Escape" })

    expect(onClose).toHaveBeenCalledOnce()
    expect(windowEscape).toHaveReturnedWith(true)
    window.removeEventListener("keydown", windowEscape)
    outside.remove()
  })
})
