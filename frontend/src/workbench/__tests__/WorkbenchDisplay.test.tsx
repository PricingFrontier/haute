/**
 * Display in the workbench's toolbar (specs/workbench): the box of the sheets' colours it
 * opens, each a swatch, Automatic or a colour of its own shown on the sheet at each step of
 * its pick, the whole pick one undo step, and the box closed by Display, Escape or a press
 * outside it.
 */
import { afterEach, beforeEach, describe, expect, it } from "vitest"
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react"
import useWorkbenchFormStore from "../../stores/useWorkbenchFormStore"
import { BLANK_THEME } from "../../utils/workbenchForm"
import WorkbenchDisplay from "../WorkbenchDisplay"
import { currentForm, loadForm } from "./fixtures"

const display = () => screen.getByRole("button", { name: "Display" })
const box = () => screen.getByRole("dialog", { name: "Display" })
const undoSteps = () => useWorkbenchFormStore.getState().undoStack.length

describe("WorkbenchDisplay", () => {
  beforeEach(() => loadForm())
  afterEach(cleanup)

  it("opens a box of the sheets' colours under Display, each Automatic to start, and Display closes it", () => {
    render(<WorkbenchDisplay />)
    expect(display()).toHaveAttribute("aria-haspopup", "dialog")
    expect(display()).toHaveAttribute("aria-expanded", "false")
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument()

    fireEvent.click(display())
    expect(display()).toHaveAttribute("aria-expanded", "true")
    for (const name of ["Sheet background", "Component background", "Borders", "Font", "Input cells", "Output cells"]) {
      expect(within(box()).getByRole("button", { name: `Automatic colour for ${name}` })).toHaveAttribute("aria-pressed", "true")
    }
    fireEvent.click(display())
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument()
  })

  it("offers white, light grey, dark grey and black before Haute's hues for each colour", () => {
    render(<WorkbenchDisplay />)
    fireEvent.click(display())
    const font = within(box())
      .getAllByRole("button", { name: /^Colour #[0-9A-F]{6} for Font$/ })
      .map((swatch) => swatch.getAttribute("aria-label"))
    expect(font.slice(0, 4)).toEqual(["Colour #FFFFFF for Font", "Colour #D1D5DB for Font", "Colour #4B5563 for Font", "Colour #000000 for Font"])
    expect(font).toHaveLength(12)

    fireEvent.click(within(box()).getByRole("button", { name: "Colour #FFFFFF for Sheet background" }))
    expect(currentForm().theme).toEqual({ ...BLANK_THEME, sheet_color: "#FFFFFF" })
  })

  it("sets a colour from a swatch, and the editor's own again with Automatic, each one undo step", () => {
    render(<WorkbenchDisplay />)
    fireEvent.click(display())

    fireEvent.click(within(box()).getByRole("button", { name: "Colour #2563EB for Sheet background" }))
    expect(currentForm().theme).toEqual({ ...BLANK_THEME, sheet_color: "#2563EB" })
    expect(within(box()).getByRole("button", { name: "Colour #2563EB for Sheet background" })).toHaveAttribute("aria-pressed", "true")
    fireEvent.click(within(box()).getByRole("button", { name: "Colour #D97706 for Borders" }))
    fireEvent.click(within(box()).getByRole("button", { name: "Automatic colour for Sheet background" }))
    expect(currentForm().theme).toEqual({ ...BLANK_THEME, border_color: "#D97706" })
    expect(undoSteps()).toBe(3)
  })

  it("shows a colour of its own on the sheet at each step of its pick, the picker the same element throughout, and records the pick as one undo step", () => {
    render(<WorkbenchDisplay />)
    fireEvent.click(display())
    const custom = within(box()).getByLabelText("Custom colour for Font")

    fireEvent.change(custom, { target: { value: "#118822" } })
    expect(currentForm().theme.font_color).toBe("#118822")
    // Replaced, the picker would close under the pointer.
    expect(within(box()).getByLabelText("Custom colour for Font")).toBe(custom)
    fireEvent.change(custom, { target: { value: "#1a2b3c" } })
    expect(currentForm().theme.font_color).toBe("#1A2B3C")
    expect(undoSteps()).toBe(0)

    fireEvent.blur(custom)
    expect(undoSteps()).toBe(1)
    act(() => useWorkbenchFormStore.getState().undo())
    expect(currentForm().theme.font_color).toBeNull()
  })

  it("closes on Escape, keeping the key from the sheet, and on a press outside it, a component's too, a colour being picked recorded first", () => {
    render(
      <>
        <WorkbenchDisplay />
        <div data-testid="outside" />
        {/* A component on the sheet: its press stops there, cancelling the mouse events after it. */}
        <div
          data-testid="component"
          onPointerDown={(event) => {
            event.preventDefault()
            event.stopPropagation()
          }}
        />
      </>,
    )
    fireEvent.click(display())
    // Its default prevented: the sheet's own Escape, which deselects, does not run.
    expect(fireEvent.keyDown(within(box()).getByRole("button", { name: "Automatic colour for Font" }), { key: "Escape" })).toBe(false)
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument()

    fireEvent.click(display())
    const custom = within(box()).getByLabelText("Custom colour for Input cells")
    act(() => custom.focus())
    fireEvent.change(custom, { target: { value: "#118822" } })
    fireEvent.pointerDown(screen.getByTestId("outside"))
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument()
    expect(currentForm().theme.input_cell_color).toBe("#118822")
    expect(undoSteps()).toBe(1)

    fireEvent.click(display())
    fireEvent.pointerDown(screen.getByTestId("component"))
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument()
  })
})
