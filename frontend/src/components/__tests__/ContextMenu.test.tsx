import { describe, it, expect, vi, afterEach } from "vitest"
import { render, screen, fireEvent, cleanup } from "@testing-library/react"
import ContextMenu from "../ContextMenu"

function makeProps(overrides: Partial<Parameters<typeof ContextMenu>[0]> = {}) {
  return {
    x: 100,
    y: 200,
    nodeId: "n1",
    nodeLabel: "Test Node",
    onClose: vi.fn(),
    onDelete: vi.fn(),
    onDuplicate: vi.fn(),
    onRename: vi.fn(),
    ...overrides,
  }
}

describe("ContextMenu", () => {
  afterEach(cleanup)

  it("renders menu with node label", () => {
    render(<ContextMenu {...makeProps()} />)
    expect(screen.getByRole("menu")).toBeInTheDocument()
    expect(screen.getByText("Test Node")).toBeInTheDocument()
  })

  it("renders Rename, Duplicate, and Delete items", () => {
    render(<ContextMenu {...makeProps()} />)
    expect(screen.getByText("Rename")).toBeInTheDocument()
    expect(screen.getByText("Duplicate")).toBeInTheDocument()
    expect(screen.getByText("Delete")).toBeInTheDocument()
  })

  it("shows Create Instance when onCreateInstance is provided and not submodel", () => {
    render(<ContextMenu {...makeProps({ onCreateInstance: vi.fn(), isSubmodel: false })} />)
    expect(screen.getByText("Create Instance")).toBeInTheDocument()
  })

  it("hides Create Instance for submodel nodes", () => {
    render(<ContextMenu {...makeProps({ onCreateInstance: vi.fn(), isSubmodel: true })} />)
    expect(screen.queryByText("Create Instance")).not.toBeInTheDocument()
  })

  it("shows Dissolve Submodel for submodel nodes", () => {
    render(<ContextMenu {...makeProps({ isSubmodel: true, nodeId: "submodel__pricing", onDissolveSubmodel: vi.fn() })} />)
    expect(screen.getByText("Dissolve Submodel")).toBeInTheDocument()
  })

  it("clicking Rename calls onRename with nodeId and closes", () => {
    const props = makeProps()
    render(<ContextMenu {...props} />)
    fireEvent.click(screen.getByText("Rename"))
    expect(props.onRename).toHaveBeenCalledWith("n1")
    expect(props.onClose).toHaveBeenCalled()
  })

  it("clicking Delete calls onDelete with nodeId and closes", () => {
    const props = makeProps()
    render(<ContextMenu {...props} />)
    fireEvent.click(screen.getByText("Delete"))
    expect(props.onDelete).toHaveBeenCalledWith("n1")
    expect(props.onClose).toHaveBeenCalled()
  })

  it("clicking Duplicate calls onDuplicate with nodeId and closes", () => {
    const props = makeProps()
    render(<ContextMenu {...props} />)
    fireEvent.click(screen.getByText("Duplicate"))
    expect(props.onDuplicate).toHaveBeenCalledWith("n1")
    expect(props.onClose).toHaveBeenCalled()
  })

  it("hides Duplicate for singleton nodes", () => {
    render(<ContextMenu {...makeProps({ isSingleton: true })} />)
    expect(screen.queryByText("Duplicate")).not.toBeInTheDocument()
  })

  it("shows Duplicate when isSingleton is false", () => {
    render(<ContextMenu {...makeProps({ isSingleton: false })} />)
    expect(screen.getByText("Duplicate")).toBeInTheDocument()
  })

  it("Escape key calls onClose", () => {
    const props = makeProps()
    render(<ContextMenu {...props} />)
    fireEvent.keyDown(document, { key: "Escape" })
    expect(props.onClose).toHaveBeenCalled()
  })

  it("positioned at the provided x, y coordinates", () => {
    render(<ContextMenu {...makeProps({ x: 150, y: 300 })} />)
    const menu = screen.getByRole("menu")
    expect(menu.style.left).toBe("150px")
    expect(menu.style.top).toBe("300px")
  })

  it("ArrowDown moves focus to next menu item", () => {
    render(<ContextMenu {...makeProps()} />)
    const items = screen.getAllByRole("menuitem")
    // First item should be focused initially
    expect(items[0]).toHaveFocus()
    // Press ArrowDown
    fireEvent.keyDown(document, { key: "ArrowDown" })
    expect(items[1]).toHaveFocus()
  })

  it("ArrowUp moves focus to previous menu item", () => {
    render(<ContextMenu {...makeProps()} />)
    const items = screen.getAllByRole("menuitem")
    // Move down first, then up
    fireEvent.keyDown(document, { key: "ArrowDown" })
    expect(items[1]).toHaveFocus()
    fireEvent.keyDown(document, { key: "ArrowUp" })
    expect(items[0]).toHaveFocus()
  })

  it("ArrowDown wraps around to first item from last", () => {
    render(<ContextMenu {...makeProps()} />)
    const items = screen.getAllByRole("menuitem")
    // Items: Rename, Duplicate, Delete (3 items)
    fireEvent.keyDown(document, { key: "ArrowDown" }) // -> Duplicate
    fireEvent.keyDown(document, { key: "ArrowDown" }) // -> Delete
    fireEvent.keyDown(document, { key: "ArrowDown" }) // -> Rename (wrap)
    expect(items[0]).toHaveFocus()
  })

  it("ArrowUp wraps around to last item from first", () => {
    render(<ContextMenu {...makeProps()} />)
    const items = screen.getAllByRole("menuitem")
    // From Rename (index 0), ArrowUp should wrap to Delete (last)
    fireEvent.keyDown(document, { key: "ArrowUp" })
    expect(items[items.length - 1]).toHaveFocus()
  })

  it("menu has correct aria-label", () => {
    render(<ContextMenu {...makeProps({ nodeLabel: "Premium Calc" })} />)
    const menu = screen.getByRole("menu")
    expect(menu).toHaveAttribute("aria-label", "Actions for Premium Calc")
  })

  it("outside click closes the menu", () => {
    const props = makeProps()
    render(<ContextMenu {...props} />)
    // Simulate outside click
    fireEvent.mouseDown(document.body)
    expect(props.onClose).toHaveBeenCalled()
  })

  it("clicking Dissolve Submodel calls onDissolveSubmodel with submodel name", () => {
    const onDissolveSubmodel = vi.fn()
    render(
      <ContextMenu
        {...makeProps({
          isSubmodel: true,
          nodeId: "submodel__pricing",
          onDissolveSubmodel,
        })}
      />,
    )
    fireEvent.click(screen.getByText("Dissolve Submodel"))
    expect(onDissolveSubmodel).toHaveBeenCalledWith("pricing")
  })

  it("menu items have correct tabIndex based on focus", () => {
    render(<ContextMenu {...makeProps()} />)
    const items = screen.getAllByRole("menuitem")
    // Initially first item has tabIndex 0, rest have -1
    expect(items[0]).toHaveAttribute("tabindex", "0")
    expect(items[1]).toHaveAttribute("tabindex", "-1")
    expect(items[2]).toHaveAttribute("tabindex", "-1")
  })
})
