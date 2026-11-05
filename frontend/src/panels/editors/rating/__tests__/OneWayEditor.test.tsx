import { describe, it, expect, vi, afterEach } from "vitest"
import { render, screen, fireEvent, cleanup } from "@testing-library/react"
import { OneWayEditor } from "../OneWayEditor"
import type { RatingTable } from "../ratingTableUtils"

function makeTable(overrides: Partial<RatingTable> = {}): RatingTable {
  return {
    factors: ["age_band"],
    output_column: "age_factor",
    default_value: "1.0",
    entries: [
      { age_band: "young", value: 1.2 },
      { age_band: "mid", value: 1.0 },
      { age_band: "old", value: 0.8 },
    ],
    ...overrides,
  }
}

describe("OneWayEditor", () => {
  afterEach(cleanup)

  it("renders factor column header", () => {
    render(
      <OneWayEditor
        table={makeTable()}
        bandingLevels={{ age_band: ["young", "mid", "old"] }}
        onUpdateEntries={vi.fn()}
      />,
    )
    expect(screen.getByText("age_band")).toBeInTheDocument()
  })

  it("renders all banding levels as rows", () => {
    render(
      <OneWayEditor
        table={makeTable()}
        bandingLevels={{ age_band: ["young", "mid", "old"] }}
        onUpdateEntries={vi.fn()}
      />,
    )
    expect(screen.getByText("young")).toBeInTheDocument()
    expect(screen.getByText("mid")).toBeInTheDocument()
    expect(screen.getByText("old")).toBeInTheDocument()
  })

  it("renders empty message when no banding levels", () => {
    render(
      <OneWayEditor
        table={makeTable()}
        bandingLevels={{}}
        onUpdateEntries={vi.fn()}
      />,
    )
    expect(screen.getByText("No banding levels found")).toBeInTheDocument()
  })

  it("returns null when factor is missing", () => {
    const { container } = render(
      <OneWayEditor
        table={makeTable({ factors: [] })}
        bandingLevels={{}}
        onUpdateEntries={vi.fn()}
      />,
    )
    expect(container.innerHTML).toBe("")
  })

  it("calls onUpdateEntries when a cell value changes", () => {
    const onUpdate = vi.fn()
    render(
      <OneWayEditor
        table={makeTable()}
        bandingLevels={{ age_band: ["young", "mid", "old"] }}
        onUpdateEntries={onUpdate}
      />,
    )
    const input = screen.getByRole("textbox", { name: "Relativity for age_band young" })
    fireEvent.change(input, { target: { value: "1.5" } })
    fireEvent.blur(input)
    expect(onUpdate).toHaveBeenCalledOnce()
    expect(onUpdate).toHaveBeenCalledWith(expect.arrayContaining([
      expect.objectContaining({ age_band: "young", value: 1.5 }),
    ]))
  })

  it.each(["1,5", "abc", "1.2.3"])("refuses a typed relativity %j that is not a number, marking the cell", (typed) => {
    const onUpdateEntries = vi.fn()
    render(
      <OneWayEditor table={makeTable()} bandingLevels={{ age_band: ["young", "mid", "old"] }} onUpdateEntries={onUpdateEntries} />,
    )
    const cell = screen.getByLabelText("Relativity for age_band young")
    fireEvent.change(cell, { target: { value: typed } })
    fireEvent.blur(cell)

    expect(onUpdateEntries).not.toHaveBeenCalled()
    expect(cell).toHaveValue(typed)
    expect(cell).toHaveAttribute("aria-invalid", "true")
    expect(screen.getByRole("img", { name: "Enter a number, such as 1.25." })).toBeInTheDocument()
  })

  it("commits a typed number exactly and a cleared cell as the table default", () => {
    const onUpdateEntries = vi.fn()
    render(
      <OneWayEditor table={makeTable({ default_value: "1.0" })} bandingLevels={{ age_band: ["young", "mid", "old"] }} onUpdateEntries={onUpdateEntries} />,
    )
    const young = screen.getByLabelText("Relativity for age_band young")
    fireEvent.change(young, { target: { value: " 1.5 " } })
    fireEvent.blur(young)
    expect(onUpdateEntries).toHaveBeenLastCalledWith(expect.arrayContaining([{ age_band: "young", value: 1.5 }]))

    const old = screen.getByLabelText("Relativity for age_band old")
    fireEvent.change(old, { target: { value: "" } })
    fireEvent.blur(old)
    expect(onUpdateEntries).toHaveBeenLastCalledWith(expect.arrayContaining([{ age_band: "old", value: 1 }]))
  })

  it("styles editable values as neutral Excel-style cells", () => {
    render(
      <OneWayEditor
        table={makeTable()}
        bandingLevels={{ age_band: ["young", "mid", "old"] }}
        onUpdateEntries={vi.fn()}
      />,
    )

    const inputs = screen.getAllByRole("textbox")
    const gridRegion = screen.getByRole("region", { name: "age_band rating grid" })
    expect(gridRegion).toHaveAttribute("tabindex", "0")
    expect(gridRegion).toHaveClass("rating-editor-grid-region")
    expect(inputs).toHaveLength(3)
    expect(screen.getByRole("textbox", { name: "Relativity for age_band young" })).toBeInTheDocument()
    for (const input of inputs) {
      expect(input).toHaveClass("rating-editor-number-cell")
      expect(input).toHaveAttribute("type", "text")
      expect(input).toHaveAttribute("inputmode", "decimal")
      expect(input).not.toHaveAttribute("step")
      expect(input.getAttribute("class")).not.toContain("focus:ring")
      expect(input.getAttribute("class")).not.toContain("emerald")
      expect(input.getAttribute("style")).toContain("border: 0px")
      expect(input.getAttribute("style")).not.toContain("accent")
      expect(input.getAttribute("style")).not.toContain("box-shadow")
      expect(input).toHaveStyle({
        background: "transparent",
        color: "var(--text-primary)",
      })
    }
    const editableCell = screen.getByRole("textbox", { name: "Relativity for age_band young" }).closest("td")
    expect(editableCell).toHaveClass("rating-editor-value-cell")
    expect(editableCell?.getAttribute("style")).toContain("border-bottom: 1px solid var(--border)")
    expect(editableCell?.getAttribute("style")).toContain("border-right: 1px solid var(--border)")
    const rowLabel = screen.getByRole("rowheader", { name: "young" })
    expect(rowLabel.getAttribute("style")).toContain("border-right: 1px solid var(--border)")
    expect(rowLabel).toHaveStyle({
      background: "var(--bg-elevated)",
      color: "var(--text-secondary)",
    })
  })

  it("selects a dragged range of editable cells and copies selected values as TSV", () => {
    const setData = vi.fn()
    render(
      <OneWayEditor
        table={makeTable()}
        bandingLevels={{ age_band: ["young", "mid", "old"] }}
        onUpdateEntries={vi.fn()}
      />,
    )

    const young = screen.getByRole("textbox", { name: "Relativity for age_band young" }).closest("td")!
    const old = screen.getByRole("textbox", { name: "Relativity for age_band old" }).closest("td")!

    fireEvent.mouseDown(young)
    fireEvent.mouseEnter(old)
    fireEvent.mouseUp(old)

    expect(young).toHaveAttribute("data-selected", "true")
    expect(young).toHaveAttribute("aria-selected", "true")
    expect(screen.getByRole("textbox", { name: "Relativity for age_band mid" }).closest("td")).toHaveAttribute("data-selected", "true")
    expect(old).toHaveAttribute("data-selected", "true")

    fireEvent.copy(screen.getByRole("table"), {
      clipboardData: { setData },
    })

    expect(setData).toHaveBeenCalledWith("text/plain", "1.2\n1\n0.8")
  })

  it("does not override native input copy after a grid row range was selected", () => {
    const setData = vi.fn()
    render(
      <OneWayEditor
        table={makeTable()}
        bandingLevels={{ age_band: ["young", "mid", "old"] }}
        onUpdateEntries={vi.fn()}
      />,
    )

    const youngInput = screen.getByRole("textbox", { name: "Relativity for age_band young" }) as HTMLInputElement
    const old = screen.getByRole("textbox", { name: "Relativity for age_band old" }).closest("td")!

    fireEvent.mouseDown(youngInput)
    youngInput.setSelectionRange(0, 3)
    fireEvent.mouseEnter(old)
    fireEvent.mouseUp(old)
    fireEvent.copy(youngInput, {
      clipboardData: { setData },
    })

    expect(setData).not.toHaveBeenCalled()
  })

  it("clears selected editable cells with Escape", () => {
    render(
      <OneWayEditor
        table={makeTable()}
        bandingLevels={{ age_band: ["young", "mid", "old"] }}
        onUpdateEntries={vi.fn()}
      />,
    )

    const young = screen.getByRole("textbox", { name: "Relativity for age_band young" }).closest("td")!
    const old = screen.getByRole("textbox", { name: "Relativity for age_band old" }).closest("td")!

    fireEvent.mouseDown(young)
    expect(document.activeElement).toBe(screen.getByRole("region", { name: "age_band rating grid" }))
    fireEvent.mouseEnter(old)
    fireEvent.mouseUp(old)
    expect(young).toHaveAttribute("data-selected", "true")

    fireEvent.keyDown(document.activeElement!, { key: "Escape" })

    expect(young).not.toHaveAttribute("data-selected")
    expect(old).not.toHaveAttribute("data-selected")
  })

  it("does not override native copy when text inside an editable cell is selected", () => {
    const setData = vi.fn()
    render(
      <OneWayEditor
        table={makeTable()}
        bandingLevels={{ age_band: ["young", "mid", "old"] }}
        onUpdateEntries={vi.fn()}
      />,
    )

    const input = screen.getByRole("textbox", { name: "Relativity for age_band young" }) as HTMLInputElement
    input.setSelectionRange(0, 3)

    fireEvent.copy(input, {
      clipboardData: { setData },
    })

    expect(setData).not.toHaveBeenCalled()
  })
})
