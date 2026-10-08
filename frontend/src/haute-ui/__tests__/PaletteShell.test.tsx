import { afterEach, describe, expect, it, vi } from "vitest"
import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { PaletteHeader, PaletteRevealStrip } from ".."

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
})
