import { afterEach, describe, expect, it, vi } from "vitest"
import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { Table2 } from "lucide-react"
import { PaletteHeader, PaletteItem, PaletteRevealStrip } from ".."

describe("haute-ui palette shell", () => {
  afterEach(cleanup)

  it("collapses the palette from its header's minimiser, and has none without onCollapse", () => {
    const onCollapse = vi.fn()
    const { rerender } = render(<PaletteHeader title="Nodes" onCollapse={onCollapse} />)

    expect(screen.getByRole("heading", { name: "Nodes" })).toBeInTheDocument()
    fireEvent.click(screen.getByTitle("Collapse palette"))
    expect(onCollapse).toHaveBeenCalledOnce()

    rerender(<PaletteHeader title="Nodes" />)
    expect(screen.queryByTitle("Collapse palette")).toBeNull()
  })

  it("shows the palette again from the collapsed strip", () => {
    const onReveal = vi.fn()
    render(<PaletteRevealStrip onReveal={onReveal} />)

    fireEvent.click(screen.getByRole("button", { name: "Show node palette" }))
    expect(onReveal).toHaveBeenCalledOnce()
  })

  it("shows an item's name and its icon on a tint of its colour, passing the host's props through", () => {
    const onClick = vi.fn()
    const { rerender } = render(<PaletteItem icon={Table2} label="Table input" color="#E69F00" title="A grid" onClick={onClick} />)

    const item = screen.getByTitle("A grid")
    expect(item).toHaveTextContent("Table input")
    expect((item.firstElementChild as HTMLElement).style.background).toBe("rgba(230, 159, 0, 0.094)")
    expect(item).not.toHaveAttribute("aria-disabled")
    fireEvent.click(item)
    expect(onClick).toHaveBeenCalledOnce()

    rerender(<PaletteItem icon={Table2} label="Table input" color="#E69F00" title="A grid" disabled />)
    expect(item).toHaveAttribute("aria-disabled", "true")
    expect(item).toHaveClass("palette-item-disabled")
  })
})
