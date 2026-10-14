/**
 * The shared colour control (specs/frontend-shared): Automatic, the swatches, Haute's chart
 * hues unless the caller gives its own, and a colour of its own picked behind a colour
 * wheel's rainbow square, outlined while the colour is one no swatch holds.
 */
import { afterEach, describe, expect, it, vi } from "vitest"
import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { COLOUR_WHEEL_GRADIENT, NEUTRAL_SWATCHES, PIVOT_CHART_COLORS } from "../../../theme/colors"
import ColourSwatches from "../ColourSwatches"

const swatchNames = () =>
  screen
    .getAllByRole("button")
    .map((button) => button.getAttribute("aria-label"))
    .filter((label) => label?.startsWith("Colour #"))

/** A colour as the page holds it in a style: each #RRGGBB as rgb(). */
const asStyled = (css: string) =>
  css.replace(/#([0-9A-F]{2})([0-9A-F]{2})([0-9A-F]{2})/g, (_, r: string, g: string, b: string) => `rgb(${parseInt(r, 16)}, ${parseInt(g, 16)}, ${parseInt(b, 16)})`)

describe("ColourSwatches", () => {
  afterEach(cleanup)

  it("offers Haute's chart hues unless given swatches of its own, in their order", () => {
    const { rerender } = render(<ColourSwatches name="Font" value={null} onCommit={vi.fn()} />)
    expect(swatchNames()).toEqual(PIVOT_CHART_COLORS.defaultSeries.map((hex) => `Colour ${hex} for Font`))

    const swatches = [...NEUTRAL_SWATCHES, ...PIVOT_CHART_COLORS.defaultSeries]
    rerender(<ColourSwatches name="Font" value={null} onCommit={vi.fn()} swatches={swatches} />)
    expect(swatchNames().slice(0, 4)).toEqual(["Colour #FFFFFF for Font", "Colour #D1D5DB for Font", "Colour #4B5563 for Font", "Colour #000000 for Font"])
    expect(swatchNames()).toHaveLength(12)
  })

  it("picks a colour of its own behind a rainbow square, outlined while no swatch holds the colour", () => {
    const onCommit = vi.fn()
    const { rerender } = render(<ColourSwatches name="Font" value="#D97706" onCommit={onCommit} />)
    const square = () => screen.getByLabelText("Custom colour for Font").parentElement as HTMLElement

    expect(square().style.background).toBe(asStyled(COLOUR_WHEEL_GRADIENT))
    expect(square().style.borderColor).toBe("var(--border)")
    rerender(<ColourSwatches name="Font" value="#123456" onCommit={onCommit} />)
    expect(square().style.borderColor).toBe("var(--text-primary)")

    // Committed once, in capitals, as the picker is left.
    const picker = screen.getByLabelText("Custom colour for Font")
    fireEvent.change(picker, { target: { value: "#abcdef" } })
    expect(onCommit).not.toHaveBeenCalled()
    fireEvent.blur(picker)
    expect(onCommit).toHaveBeenCalledExactlyOnceWith("#ABCDEF")
  })
})
