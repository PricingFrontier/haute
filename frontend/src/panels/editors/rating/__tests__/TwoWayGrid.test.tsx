import { describe, it, expect, vi, afterEach } from "vitest"
import { render, screen, fireEvent, cleanup, waitFor } from "@testing-library/react"
import { TwoWayGrid } from "../TwoWayGrid"
import type { RatingTable } from "../ratingTableUtils"
import useToastStore from "../../../../stores/useToastStore"

function makeTable(overrides: Partial<RatingTable> = {}): RatingTable {
  return {
    factors: ["age_band", "region"],
    outputColumn: "factor",
    defaultValue: "1.0",
    entries: [
      { age_band: "young", region: "north", value: 1.1 },
      { age_band: "young", region: "south", value: 0.9 },
      { age_band: "old", region: "north", value: 1.3 },
      { age_band: "old", region: "south", value: 0.7 },
    ],
    ...overrides,
  }
}

const bandingLevels = {
  age_band: ["young", "old"],
  region: ["north", "south"],
}

describe("TwoWayGrid", () => {
  afterEach(() => {
    cleanup()
    vi.restoreAllMocks()
  })

  it("renders row and column factor headers", () => {
    render(
      <TwoWayGrid
        table={makeTable()}
        bandingLevels={bandingLevels}
        onUpdateEntries={vi.fn()}
      />,
    )
    expect(screen.getByText(/age_band/)).toBeInTheDocument()
    expect(screen.getByText(/region/)).toBeInTheDocument()
  })

  it("renders row labels from bandingLevels", () => {
    render(
      <TwoWayGrid
        table={makeTable()}
        bandingLevels={bandingLevels}
        onUpdateEntries={vi.fn()}
      />,
    )
    expect(screen.getByText("young")).toBeInTheDocument()
    expect(screen.getByText("old")).toBeInTheDocument()
  })

  it("renders column labels in table header", () => {
    render(
      <TwoWayGrid
        table={makeTable()}
        bandingLevels={bandingLevels}
        onUpdateEntries={vi.fn()}
      />,
    )
    expect(screen.getByText("north")).toBeInTheDocument()
    expect(screen.getByText("south")).toBeInTheDocument()
  })

  it("returns null when less than two factors", () => {
    const { container } = render(
      <TwoWayGrid
        table={makeTable({ factors: ["age_band"] })}
        bandingLevels={bandingLevels}
        onUpdateEntries={vi.fn()}
      />,
    )
    expect(container.innerHTML).toBe("")
  })

  it("shows empty message when no banding levels for factors", () => {
    render(
      <TwoWayGrid
        table={makeTable()}
        bandingLevels={{ age_band: [], region: [] }}
        onUpdateEntries={vi.fn()}
      />,
    )
    expect(screen.getByText(/No banding levels found/)).toBeInTheDocument()
  })

  it("calls onUpdateEntries when a cell value changes", () => {
    const onUpdate = vi.fn()
    render(
      <TwoWayGrid
        table={makeTable()}
        bandingLevels={bandingLevels}
        onUpdateEntries={onUpdate}
      />,
    )
    const input = screen.getByRole("textbox", { name: "Relativity for age_band young and region north" })
    fireEvent.change(input, { target: { value: "2.0" } })
    fireEvent.blur(input)
    expect(onUpdate).toHaveBeenCalledOnce()
    expect(onUpdate).toHaveBeenCalledWith(expect.arrayContaining([
      expect.objectContaining({ age_band: "young", region: "north", value: 2.0 }),
    ]))
  })

  it("styles editable values as neutral Excel-style cells", () => {
    render(
      <TwoWayGrid
        table={makeTable()}
        bandingLevels={bandingLevels}
        onUpdateEntries={vi.fn()}
      />,
    )

    const inputs = screen.getAllByRole("textbox")
    expect(inputs).toHaveLength(4)
    expect(screen.getByRole("textbox", { name: "Relativity for age_band young and region north" })).toBeInTheDocument()
    const gridRegion = screen.getByRole("region", { name: "age_band by region rating grid" })
    expect(gridRegion).toHaveAttribute("tabindex", "0")
    expect(gridRegion).toHaveClass("rating-editor-grid-region")
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
    const editableCell = screen.getByRole("textbox", { name: "Relativity for age_band young and region north" }).closest("td")
    expect(editableCell).toHaveClass("rating-editor-value-cell")
    expect(editableCell?.getAttribute("style")).toContain("border-bottom: 1px solid var(--border)")
    expect(editableCell?.getAttribute("style")).toContain("border-right: 1px solid var(--border)")
    const rowLabel = screen.getByRole("rowheader", { name: "young" })
    expect(rowLabel.getAttribute("style")).toContain("border-right: 1px solid var(--border)")
    expect(rowLabel).toHaveStyle({
      background: "var(--bg-elevated)",
      color: "var(--text-secondary)",
    })
    const colLabel = screen.getByRole("columnheader", { name: "north" })
    expect(colLabel.getAttribute("style")).toContain("border-right: 1px solid var(--border)")
    expect(colLabel).toHaveStyle({
      background: "var(--bg-elevated)",
      color: "var(--text-secondary)",
    })
  })

  it("pastes an Excel-style numeric matrix from the focused cell", () => {
    const onUpdate = vi.fn()
    render(
      <TwoWayGrid
        table={makeTable()}
        bandingLevels={bandingLevels}
        onUpdateEntries={onUpdate}
      />,
    )

    fireEvent.paste(screen.getAllByRole("textbox")[0], {
      clipboardData: { getData: () => "1.2\t1.3\n0.8\t0.9" },
    })

    expect(onUpdate).toHaveBeenCalledWith(expect.arrayContaining([
      expect.objectContaining({ age_band: "young", region: "north", value: 1.2 }),
      expect.objectContaining({ age_band: "young", region: "south", value: 1.3 }),
      expect.objectContaining({ age_band: "old", region: "north", value: 0.8 }),
      expect.objectContaining({ age_band: "old", region: "south", value: 0.9 }),
    ]))
  })

  it("pastes an Excel-style numeric matrix from the grid container", () => {
    const onUpdate = vi.fn()
    const { container } = render(
      <TwoWayGrid
        table={makeTable()}
        bandingLevels={bandingLevels}
        onUpdateEntries={onUpdate}
      />,
    )
    const grid = container.querySelector("[data-testid='two-way-grid-scroll-container']")
    expect(grid).toBeInTheDocument()

    fireEvent.paste(grid!, {
      clipboardData: { getData: () => "1.2\t1.3\n0.8\t0.9" },
    })

    expect(onUpdate).toHaveBeenCalledWith(expect.arrayContaining([
      expect.objectContaining({ age_band: "young", region: "north", value: 1.2 }),
      expect.objectContaining({ age_band: "young", region: "south", value: 1.3 }),
      expect.objectContaining({ age_band: "old", region: "north", value: 0.8 }),
      expect.objectContaining({ age_band: "old", region: "south", value: 0.9 }),
    ]))
  })

  it("pastes a copied table using row and column labels regardless of order", () => {
    const onUpdate = vi.fn()
    render(
      <TwoWayGrid
        table={makeTable()}
        bandingLevels={bandingLevels}
        onUpdateEntries={onUpdate}
      />,
    )

    fireEvent.paste(screen.getAllByRole("textbox")[0], {
      clipboardData: { getData: () => "age_band \\ region\tsouth\tnorth\nold\t0.85\t0.95\nyoung\t1.25\t1.35" },
    })

    expect(onUpdate).toHaveBeenCalledWith(expect.arrayContaining([
      expect.objectContaining({ age_band: "young", region: "north", value: 1.35 }),
      expect.objectContaining({ age_band: "young", region: "south", value: 1.25 }),
      expect.objectContaining({ age_band: "old", region: "north", value: 0.95 }),
      expect.objectContaining({ age_band: "old", region: "south", value: 0.85 }),
    ]))
  })

  it("does not apply a pasted grid containing non-numeric values", () => {
    const onUpdate = vi.fn()
    render(
      <TwoWayGrid
        table={makeTable()}
        bandingLevels={bandingLevels}
        onUpdateEntries={onUpdate}
      />,
    )

    fireEvent.paste(screen.getAllByRole("textbox")[0], {
      clipboardData: { getData: () => "1.2\tbad\n0.8\t0.9" },
    })

    expect(onUpdate).not.toHaveBeenCalled()
  })

  it("shows the offending cell when an unlabelled pasted matrix contains a non-numeric value", () => {
    const onUpdate = vi.fn()
    render(
      <TwoWayGrid
        table={makeTable()}
        bandingLevels={bandingLevels}
        onUpdateEntries={onUpdate}
      />,
    )

    fireEvent.paste(screen.getAllByRole("textbox")[0], {
      clipboardData: { getData: () => "1.2\tbad\n0.8\t0.9" },
    })

    expect(onUpdate).not.toHaveBeenCalled()
    expect(useToastStore.getState().toasts).toContainEqual(expect.objectContaining({
      type: "error",
      text: expect.stringContaining("pasted row 1, column 2"),
    }))
    expect(useToastStore.getState().toasts).toContainEqual(expect.objectContaining({
      type: "error",
      text: expect.stringContaining('age_band "young" and region "south"'),
    }))
  })

  it("shows the offending row and column labels when labelled pasted data contains a non-numeric value", () => {
    const onUpdate = vi.fn()
    render(
      <TwoWayGrid
        table={makeTable()}
        bandingLevels={bandingLevels}
        onUpdateEntries={onUpdate}
      />,
    )

    fireEvent.paste(screen.getAllByRole("textbox")[0], {
      clipboardData: { getData: () => "age_band \\ region\tsouth\tnorth\nold\t0.85\tbad\nyoung\t1.25\t1.35" },
    })

    expect(onUpdate).not.toHaveBeenCalled()
    expect(useToastStore.getState().toasts).toContainEqual(expect.objectContaining({
      type: "error",
      text: expect.stringContaining("pasted row 2, column 3"),
    }))
    expect(useToastStore.getState().toasts).toContainEqual(expect.objectContaining({
      type: "error",
      text: expect.stringContaining('age_band "old" and region "north"'),
    }))
  })

  it("shows the mapped cell when an invalid paste starts away from the top-left cell", () => {
    const onUpdate = vi.fn()
    render(
      <TwoWayGrid
        table={makeTable({
          entries: [
            { age_band: "young", region: "north", value: 1.1 },
            { age_band: "young", region: "central", value: 1.15 },
            { age_band: "young", region: "south", value: 0.9 },
            { age_band: "old", region: "north", value: 1.3 },
            { age_band: "old", region: "central", value: 1.05 },
            { age_band: "old", region: "south", value: 0.7 },
          ],
        })}
        bandingLevels={{ age_band: ["young", "old"], region: ["north", "central", "south"] }}
        onUpdateEntries={onUpdate}
      />,
    )

    fireEvent.paste(screen.getByRole("textbox", { name: "Relativity for age_band old and region central" }), {
      clipboardData: { getData: () => "bad" },
    })

    expect(onUpdate).not.toHaveBeenCalled()
    expect(useToastStore.getState().toasts).toContainEqual(expect.objectContaining({
      type: "error",
      text: expect.stringContaining("pasted row 1, column 1"),
    }))
    expect(useToastStore.getState().toasts).toContainEqual(expect.objectContaining({
      type: "error",
      text: expect.stringContaining('age_band "old" and region "central"'),
    }))
  })

  it("preserves internal blank pasted rows so later values do not shift upward", () => {
    const onUpdate = vi.fn()
    render(
      <TwoWayGrid
        table={makeTable()}
        bandingLevels={bandingLevels}
        onUpdateEntries={onUpdate}
      />,
    )

    fireEvent.paste(screen.getAllByRole("textbox")[0], {
      clipboardData: { getData: () => "1.2\t1.3\n\t\n0.8\t0.9\n" },
    })

    expect(onUpdate).toHaveBeenCalledWith(expect.arrayContaining([
      expect.objectContaining({ age_band: "young", region: "north", value: 1.2 }),
      expect.objectContaining({ age_band: "young", region: "south", value: 1.3 }),
      expect.objectContaining({ age_band: "old", region: "north", value: 1.3 }),
      expect.objectContaining({ age_band: "old", region: "south", value: 0.7 }),
    ]))
    expect(onUpdate).not.toHaveBeenCalledWith(expect.arrayContaining([
      expect.objectContaining({ age_band: "old", region: "north", value: 0.8 }),
    ]))
  })

  it("preserves internal blank pasted columns so later values do not shift left", () => {
    const onUpdate = vi.fn()
    render(
      <TwoWayGrid
        table={makeTable({
          entries: [
            { age_band: "young", region: "north", value: 1.1 },
            { age_band: "young", region: "central", value: 1.15 },
            { age_band: "young", region: "south", value: 0.9 },
            { age_band: "old", region: "north", value: 1.3 },
            { age_band: "old", region: "central", value: 1.05 },
            { age_band: "old", region: "south", value: 0.7 },
          ],
        })}
        bandingLevels={{ age_band: ["young", "old"], region: ["north", "central", "south"] }}
        onUpdateEntries={onUpdate}
      />,
    )

    fireEvent.paste(screen.getAllByRole("textbox")[0], {
      clipboardData: { getData: () => "1.2\t\t1.4" },
    })

    expect(onUpdate).toHaveBeenCalledWith(expect.arrayContaining([
      expect.objectContaining({ age_band: "young", region: "north", value: 1.2 }),
      expect.objectContaining({ age_band: "young", region: "central", value: 1.15 }),
      expect.objectContaining({ age_band: "young", region: "south", value: 1.4 }),
    ]))
    expect(onUpdate).not.toHaveBeenCalledWith(expect.arrayContaining([
      expect.objectContaining({ age_band: "young", region: "central", value: 1.4 }),
    ]))
  })

  it("pastes into the visible slice when used by a 3-way table", () => {
    const onUpdate = vi.fn()
    render(
      <TwoWayGrid
        table={makeTable({
          factors: ["vehicle_age_band", "channel_band", "cover_type"],
          entries: [
            { vehicle_age_band: "1-3", channel_band: "direct", cover_type: "comprehensive", value: 9 },
          ],
        })}
        bandingLevels={{
          vehicle_age_band: ["1-3"],
          channel_band: ["direct"],
          cover_type: ["third_party", "comprehensive"],
        }}
        onUpdateEntries={onUpdate}
        factorOverrides={{
          factors: ["vehicle_age_band", "channel_band"],
          sliceKey: { cover_type: "third_party" },
        }}
      />,
    )

    fireEvent.paste(screen.getAllByRole("textbox")[0], {
      clipboardData: { getData: () => "1.7" },
    })

    expect(onUpdate).toHaveBeenCalledWith(expect.arrayContaining([
      expect.objectContaining({ vehicle_age_band: "1-3", channel_band: "direct", cover_type: "comprehensive", value: 9 }),
      expect.objectContaining({ vehicle_age_band: "1-3", channel_band: "direct", cover_type: "third_party", value: 1.7 }),
    ]))
  })

  it("copies the visible table as TSV", () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText },
      configurable: true,
    })

    render(
      <TwoWayGrid
        table={makeTable()}
        bandingLevels={bandingLevels}
        onUpdateEntries={vi.fn()}
      />,
    )

    fireEvent.click(screen.getByRole("button", { name: "Copy visible table as TSV" }))

    expect(writeText).toHaveBeenCalledWith(
      "age_band \\ region\tnorth\tsouth\nyoung\t1.1\t0.9\nold\t1.3\t0.7",
    )
  })

  it("shows a toast when visible table copy fails", async () => {
    const error = new Error("clipboard denied")
    const writeText = vi.fn().mockRejectedValue(error)
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText },
      configurable: true,
    })

    render(
      <TwoWayGrid
        table={makeTable()}
        bandingLevels={bandingLevels}
        onUpdateEntries={vi.fn()}
      />,
    )

    fireEvent.click(screen.getByRole("button", { name: "Copy visible table as TSV" }))

    await waitFor(() => {
      expect(useToastStore.getState().toasts).toContainEqual(expect.objectContaining({
        type: "error",
        text: "Could not copy rating table TSV: clipboard denied",
      }))
    })
  })

  it("shows the copy table action as an icon-only control below the grid", () => {
    const { container } = render(
      <TwoWayGrid
        table={makeTable()}
        bandingLevels={bandingLevels}
        onUpdateEntries={vi.fn()}
      />,
    )

    const grid = screen.getByRole("region", { name: "age_band by region rating grid" })
    const button = screen.getByRole("button", { name: "Copy visible table as TSV" })

    expect(screen.queryByText("Copy TSV")).not.toBeInTheDocument()
    expect(button).toHaveAttribute("title", "Copy visible table as TSV")
    expect(grid.compareDocumentPosition(button) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(container.querySelector("[data-testid='two-way-grid-scroll-container'] + div button")).toBe(button)
  })

  it("selects a dragged range of editable cells and copies selected values as TSV", () => {
    const setData = vi.fn()
    render(
      <TwoWayGrid
        table={makeTable()}
        bandingLevels={bandingLevels}
        onUpdateEntries={vi.fn()}
      />,
    )

    const topLeft = screen.getByRole("textbox", { name: "Relativity for age_band young and region north" }).closest("td")!
    const bottomRight = screen.getByRole("textbox", { name: "Relativity for age_band old and region south" }).closest("td")!

    fireEvent.mouseDown(topLeft)
    fireEvent.mouseEnter(bottomRight)
    fireEvent.mouseUp(bottomRight)

    expect(topLeft).toHaveAttribute("data-selected", "true")
    expect(topLeft).toHaveAttribute("aria-selected", "true")
    expect(screen.getByRole("textbox", { name: "Relativity for age_band young and region south" }).closest("td")).toHaveAttribute("data-selected", "true")
    expect(screen.getByRole("textbox", { name: "Relativity for age_band old and region north" }).closest("td")).toHaveAttribute("data-selected", "true")
    expect(bottomRight).toHaveAttribute("data-selected", "true")

    fireEvent.copy(screen.getByRole("region", { name: "age_band by region rating grid" }), {
      clipboardData: { setData },
    })

    expect(setData).toHaveBeenCalledWith("text/plain", "1.1\t0.9\n1.3\t0.7")
  })

  it("does not override native input copy after a grid range was selected", () => {
    const setData = vi.fn()
    render(
      <TwoWayGrid
        table={makeTable()}
        bandingLevels={bandingLevels}
        onUpdateEntries={vi.fn()}
      />,
    )

    const topLeftInput = screen.getByRole("textbox", { name: "Relativity for age_band young and region north" }) as HTMLInputElement
    const bottomRight = screen.getByRole("textbox", { name: "Relativity for age_band old and region south" }).closest("td")!

    fireEvent.mouseDown(topLeftInput)
    topLeftInput.setSelectionRange(0, 3)
    fireEvent.mouseEnter(bottomRight)
    fireEvent.mouseUp(bottomRight)
    fireEvent.copy(topLeftInput, {
      clipboardData: { setData },
    })

    expect(setData).not.toHaveBeenCalled()
  })

  it("copies dragged ranges in row-major order even when dragged in reverse", () => {
    const setData = vi.fn()
    render(
      <TwoWayGrid
        table={makeTable()}
        bandingLevels={bandingLevels}
        onUpdateEntries={vi.fn()}
      />,
    )

    const topLeft = screen.getByRole("textbox", { name: "Relativity for age_band young and region north" }).closest("td")!
    const bottomRight = screen.getByRole("textbox", { name: "Relativity for age_band old and region south" }).closest("td")!

    fireEvent.mouseDown(bottomRight)
    fireEvent.mouseEnter(topLeft)
    fireEvent.mouseUp(topLeft)

    fireEvent.copy(screen.getByRole("region", { name: "age_band by region rating grid" }), {
      clipboardData: { setData },
    })

    expect(setData).toHaveBeenCalledWith("text/plain", "1.1\t0.9\n1.3\t0.7")
  })

  it("does not commit edited values while selecting cells by dragging", () => {
    const onUpdate = vi.fn()
    render(
      <TwoWayGrid
        table={makeTable()}
        bandingLevels={bandingLevels}
        onUpdateEntries={onUpdate}
      />,
    )

    const topLeft = screen.getByRole("textbox", { name: "Relativity for age_band young and region north" }).closest("td")!
    const bottomRight = screen.getByRole("textbox", { name: "Relativity for age_band old and region south" }).closest("td")!

    fireEvent.mouseDown(topLeft)
    fireEvent.mouseEnter(bottomRight)
    fireEvent.mouseUp(bottomRight)

    expect(onUpdate).not.toHaveBeenCalled()
  })

  it("clears selected editable cells with Escape", () => {
    render(
      <TwoWayGrid
        table={makeTable()}
        bandingLevels={bandingLevels}
        onUpdateEntries={vi.fn()}
      />,
    )

    const topLeft = screen.getByRole("textbox", { name: "Relativity for age_band young and region north" }).closest("td")!
    const bottomRight = screen.getByRole("textbox", { name: "Relativity for age_band old and region south" }).closest("td")!
    const grid = screen.getByRole("region", { name: "age_band by region rating grid" })

    fireEvent.mouseDown(topLeft)
    expect(document.activeElement).toBe(grid)
    fireEvent.mouseEnter(bottomRight)
    fireEvent.mouseUp(bottomRight)
    expect(topLeft).toHaveAttribute("data-selected", "true")

    fireEvent.keyDown(document.activeElement!, { key: "Escape" })

    expect(topLeft).not.toHaveAttribute("data-selected")
    expect(bottomRight).not.toHaveAttribute("data-selected")
  })

  it("does not override native copy when text inside an editable cell is selected", () => {
    const setData = vi.fn()
    render(
      <TwoWayGrid
        table={makeTable()}
        bandingLevels={bandingLevels}
        onUpdateEntries={vi.fn()}
      />,
    )

    const input = screen.getByRole("textbox", { name: "Relativity for age_band young and region north" }) as HTMLInputElement
    input.setSelectionRange(0, 3)

    fireEvent.copy(input, {
      clipboardData: { setData },
    })

    expect(setData).not.toHaveBeenCalled()
  })
})
