import { describe, it, expect, vi, afterEach } from "vitest"
import { render, screen, fireEvent, cleanup } from "@testing-library/react"
import SelectionContextMenu from "../SelectionContextMenu"

function makeProps(overrides: Partial<Parameters<typeof SelectionContextMenu>[0]> = {}) {
  return {
    x: 100,
    y: 200,
    nodeIds: ["n1", "n2", "n3"],
    onClose: vi.fn(),
    onGroup: vi.fn(),
    onDelete: vi.fn(),
    ...overrides,
  }
}

describe("SelectionContextMenu", () => {
  afterEach(cleanup)

  it("renders only the selection actions: Group into wrapper + Delete", () => {
    render(<SelectionContextMenu {...makeProps()} />)
    expect(screen.getByTestId("selection-context-menu")).toBeInTheDocument()
    expect(screen.getByTestId("context-menu-group-submodel")).toBeInTheDocument()
    expect(screen.getByTestId("context-menu-delete-selected")).toBeInTheDocument()
    // No per-node items leak in.
    expect(screen.queryByText("Rename")).toBeNull()
    expect(screen.queryByText("Duplicate")).toBeNull()
  })

  it("shows the selected-node count in the header", () => {
    render(<SelectionContextMenu {...makeProps({ nodeIds: ["a", "b"] })} />)
    expect(screen.getByText("2 selected")).toBeInTheDocument()
  })

  it("clicking Group into wrapper calls onGroup with the node ids and closes", () => {
    const props = makeProps()
    render(<SelectionContextMenu {...props} />)
    fireEvent.click(screen.getByTestId("context-menu-group-submodel"))
    expect(props.onGroup).toHaveBeenCalledWith(["n1", "n2", "n3"])
    expect(props.onClose).toHaveBeenCalled()
  })

  it("clicking Delete calls onDelete with the node ids and closes", () => {
    const props = makeProps()
    render(<SelectionContextMenu {...props} />)
    fireEvent.click(screen.getByTestId("context-menu-delete-selected"))
    expect(props.onDelete).toHaveBeenCalledWith(["n1", "n2", "n3"])
    expect(props.onClose).toHaveBeenCalled()
  })

  it("Escape key calls onClose", () => {
    const props = makeProps()
    render(<SelectionContextMenu {...props} />)
    fireEvent.keyDown(document, { key: "Escape" })
    expect(props.onClose).toHaveBeenCalled()
  })

  it("clicking outside the menu closes it", () => {
    const props = makeProps()
    render(
      <div>
        <button data-testid="outside">outside</button>
        <SelectionContextMenu {...props} />
      </div>,
    )
    fireEvent.mouseDown(screen.getByTestId("outside"))
    expect(props.onClose).toHaveBeenCalled()
  })

  it("ArrowDown moves focus to the next item", () => {
    render(<SelectionContextMenu {...makeProps()} />)
    // First item auto-focused.
    expect(screen.getByTestId("context-menu-group-submodel")).toHaveFocus()
    fireEvent.keyDown(document, { key: "ArrowDown" })
    expect(screen.getByTestId("context-menu-delete-selected")).toHaveFocus()
  })

  it("is positioned at the provided x, y coordinates", () => {
    render(<SelectionContextMenu {...makeProps({ x: 150, y: 300 })} />)
    const menu = screen.getByTestId("selection-context-menu")
    expect(menu).toHaveStyle({ left: "150px", top: "300px" })
  })

  it("greys out and inerts Group into wrapper when a disabled reason is given", () => {
    const props = makeProps({ groupDisabledReason: "A wrapper can't contain another wrapper" })
    render(<SelectionContextMenu {...props} />)
    const group = screen.getByTestId("context-menu-group-submodel")
    expect(group).toHaveAttribute("aria-disabled", "true")
    expect(group).toHaveAttribute("title", "A wrapper can't contain another wrapper")
    // Inert: no action, menu stays open.
    fireEvent.click(group)
    expect(props.onGroup).not.toHaveBeenCalled()
    expect(props.onClose).not.toHaveBeenCalled()
  })

  it("leaves Group into wrapper enabled when no disabled reason is given", () => {
    const props = makeProps({ groupDisabledReason: null })
    render(<SelectionContextMenu {...props} />)
    const group = screen.getByTestId("context-menu-group-submodel")
    expect(group).not.toHaveAttribute("aria-disabled")
    fireEvent.click(group)
    expect(props.onGroup).toHaveBeenCalledWith(["n1", "n2", "n3"])
  })
})
