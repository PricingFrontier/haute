import { describe, it, expect, vi, afterEach } from "vitest"
import { render, screen, fireEvent, cleanup, waitFor } from "@testing-library/react"
import { BreakpointGrid } from "../BreakpointGrid"
import useToastStore from "../../../../stores/useToastStore"

const ACCENT = "#f97316"
const originalClipboardDescriptor = Object.getOwnPropertyDescriptor(navigator, "clipboard")

function restoreClipboard(): void {
  if (originalClipboardDescriptor) {
    Object.defineProperty(navigator, "clipboard", originalClipboardDescriptor)
    return
  }
  Reflect.deleteProperty(navigator, "clipboard")
}

describe("BreakpointGrid", () => {
  afterEach(() => {
    cleanup()
    useToastStore.setState({ toasts: [], _toastCounter: 0 })
    restoreClipboard()
  })

  it("renders empty state with helpful guidance when no breakpoints", () => {
    render(
      <BreakpointGrid
        breakpoints={[]}
        onUpdate={vi.fn()}
        accentColor={ACCENT}
      />,
    )
    expect(screen.getByText("Define boundaries to split values into bands.")).toBeInTheDocument()
    expect(screen.getByText("Example: for age, add boundaries at 25, 35, 45, 65")).toBeInTheDocument()
  })

  it("renders breakpoints with 'Up to' and 'Band name' column headers", () => {
    render(
      <BreakpointGrid
        breakpoints={[
          { boundary: "18", label: "Young" },
          { boundary: "25", label: "Mid" },
        ]}
        onUpdate={vi.fn()}
        accentColor={ACCENT}
      />,
    )
    expect(screen.getByText("(incl.)")).toBeInTheDocument()
    expect(screen.getByText("Band name")).toBeInTheDocument()
    expect(screen.getByDisplayValue("18")).toBeInTheDocument()
    expect(screen.getByDisplayValue("25")).toBeInTheDocument()
    expect(screen.getByDisplayValue("Young")).toBeInTheDocument()
    expect(screen.getByDisplayValue("Mid")).toBeInTheDocument()
  })

  it("add button creates new breakpoint", () => {
    const onUpdate = vi.fn()
    render(
      <BreakpointGrid
        breakpoints={[{ boundary: "10", label: "Low" }]}
        onUpdate={onUpdate}
        accentColor={ACCENT}
      />,
    )
    fireEvent.click(screen.getByText("Add"))
    expect(onUpdate).toHaveBeenCalledTimes(1)
    const newBreakpoints = onUpdate.mock.calls[0][0]
    expect(newBreakpoints.length).toBe(2)
    expect(newBreakpoints[0]).toEqual({ boundary: "10", label: "Low" })
    expect(newBreakpoints[1]).toEqual({ boundary: "", label: "" })
  })

  it("newly added breakpoint renders as editable number input", () => {
    render(
      <BreakpointGrid
        breakpoints={[
          { boundary: "25", label: "Young" },
          { boundary: "", label: "" },
        ]}
        onUpdate={vi.fn()}
        accentColor={ACCENT}
      />,
    )
    // Both rows should have number inputs
    const numberInputs = screen.getAllByRole("spinbutton")
    expect(numberInputs).toHaveLength(2)
  })

  it("delete button removes breakpoint", () => {
    const onUpdate = vi.fn()
    render(
      <BreakpointGrid
        breakpoints={[
          { boundary: "10", label: "Low" },
          { boundary: "20", label: "Mid" },
        ]}
        onUpdate={onUpdate}
        accentColor={ACCENT}
      />,
    )
    const deleteButtons = screen.getAllByLabelText("Delete breakpoint")
    fireEvent.click(deleteButtons[0])
    expect(onUpdate).toHaveBeenCalledTimes(1)
    const result = onUpdate.mock.calls[0][0]
    expect(result).toHaveLength(1)
    expect(result[0].label).toBe("Mid")
  })

  it("editing boundary value calls onUpdate", () => {
    const onUpdate = vi.fn()
    render(
      <BreakpointGrid
        breakpoints={[{ boundary: "10", label: "Low" }]}
        onUpdate={onUpdate}
        accentColor={ACCENT}
      />,
    )
    const boundaryInput = screen.getByDisplayValue("10")
    fireEvent.change(boundaryInput, { target: { value: "15" } })
    expect(onUpdate).toHaveBeenCalledTimes(1)
    expect(onUpdate.mock.calls[0][0][0].boundary).toBe("15")
  })

  it("editing label calls onUpdate", () => {
    const onUpdate = vi.fn()
    render(
      <BreakpointGrid
        breakpoints={[{ boundary: "10", label: "Low" }]}
        onUpdate={onUpdate}
        accentColor={ACCENT}
      />,
    )
    const labelInput = screen.getByDisplayValue("Low")
    fireEvent.change(labelInput, { target: { value: "Very Low" } })
    expect(onUpdate).toHaveBeenCalledTimes(1)
    expect(onUpdate.mock.calls[0][0][0].label).toBe("Very Low")
  })

  it("keeps boxed inputs but tightens cell spacing and removes row divider lines", () => {
    const { container } = render(
      <BreakpointGrid
        breakpoints={[{ boundary: "10", label: "Low" }]}
        onUpdate={vi.fn()}
        accentColor={ACCENT}
      />,
    )

    const firstEditableCell = screen.getByLabelText("Breakpoint 1 boundary").closest("td")
    expect(firstEditableCell).toHaveClass("px-0.5")
    expect(firstEditableCell).toHaveClass("py-0.5")
    expect(screen.getByLabelText("Breakpoint 1 boundary")).toHaveClass("rounded")
    expect(screen.getByLabelText("Breakpoint 1 boundary")).not.toHaveClass("rounded-none")
    expect(screen.getByLabelText("Breakpoint 1 boundary").style.border).toBe("1px solid var(--border)")

    const dataRow = container.querySelector("tbody tr") as HTMLTableRowElement
    expect(dataRow.style.borderBottom).toBe("")
  })

  it("pastes TSV into a breakpoint range and creates missing rows", () => {
    const onUpdate = vi.fn()
    render(
      <BreakpointGrid
        breakpoints={[{ boundary: "10", label: "Low" }]}
        onUpdate={onUpdate}
        accentColor={ACCENT}
      />,
    )

    fireEvent.paste(screen.getByLabelText("Breakpoint 1 boundary"), {
      clipboardData: { getData: () => "20\tYoung\n40\tMiddle" },
    })

    expect(onUpdate).toHaveBeenCalledTimes(1)
    expect(onUpdate.mock.calls[0][0]).toEqual([
      { boundary: "20", label: "Young" },
      { boundary: "40", label: "Middle" },
    ])
  })

  it("copies the whole breakpoint banding as TSV", () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText },
      configurable: true,
    })
    render(
      <BreakpointGrid
        breakpoints={[{ boundary: "10", label: "Low" }, { boundary: "20", label: "Mid" }]}
        onUpdate={vi.fn()}
        accentColor={ACCENT}
      />,
    )

    fireEvent.click(screen.getByRole("button", { name: "Copy banding as TSV" }))

    expect(writeText).toHaveBeenCalledWith("Up to\tBand name\n10\tLow\n20\tMid")
  })

  it("shows a toast when the clipboard API is unavailable", async () => {
    Object.defineProperty(navigator, "clipboard", {
      value: undefined,
      configurable: true,
    })
    render(
      <BreakpointGrid
        breakpoints={[{ boundary: "10", label: "Low" }]}
        onUpdate={vi.fn()}
        accentColor={ACCENT}
      />,
    )

    fireEvent.click(screen.getByRole("button", { name: "Copy banding as TSV" }))

    await waitFor(() => {
      expect(useToastStore.getState().toasts).toContainEqual(
        expect.objectContaining({
          type: "error",
          text: "Could not copy banding TSV: Clipboard API is not available",
        }),
      )
    })
  })

  it("flags breakpoint boundaries that are out of sequence", () => {
    render(
      <BreakpointGrid
        breakpoints={[
          { boundary: "10", label: "Low" },
          { boundary: "30", label: "High" },
          { boundary: "20", label: "Middle" },
          { boundary: "25", label: "Upper Middle" },
        ]}
        onUpdate={vi.fn()}
        accentColor={ACCENT}
      />,
    )

    const warning = screen.getByRole("img", {
      name: "Breakpoint 3 is out of order; enter a value greater than 30.",
    })
    expect(warning).toBeInTheDocument()
    expect(warning).toHaveAttribute("title", "Breakpoint 3 is out of order; enter a value greater than 30.")
    expect(warning).not.toHaveClass("pointer-events-none")
    expect(screen.getByLabelText("Breakpoint 3 boundary")).toHaveAttribute("aria-invalid", "true")
    expect(screen.getByLabelText("Breakpoint 3 boundary")).toHaveAttribute("aria-describedby", "breakpoint-3-order-warning")
    expect(screen.getByLabelText("Breakpoint 3 boundary")).toHaveAttribute("title", "Breakpoint 3 is out of order; enter a value greater than 30.")
    expect(screen.getByLabelText("Breakpoint 3 boundary").style.border).toBe("1px solid var(--warning-border-emphasis)")
    expect(screen.getByRole("img", {
      name: "Breakpoint 4 is out of order; enter a value greater than 30.",
    })).toBeInTheDocument()
    expect(screen.getByLabelText("Breakpoint 4 boundary")).toHaveAttribute("aria-invalid", "true")
    expect(screen.queryByRole("img", { name: /Breakpoint 2 is out of order/ })).not.toBeInTheDocument()
  })

  it("flags duplicate breakpoint boundaries as not sequential", () => {
    render(
      <BreakpointGrid
        breakpoints={[
          { boundary: "10", label: "Low" },
          { boundary: "10", label: "Duplicate" },
        ]}
        onUpdate={vi.fn()}
        accentColor={ACCENT}
      />,
    )

    expect(screen.getByRole("img", {
      name: "Breakpoint 2 is out of order; enter a value greater than 10.",
    })).toBeInTheDocument()
    expect(screen.getByLabelText("Breakpoint 2 boundary")).toHaveAttribute("aria-invalid", "true")
  })

  it("does not flag ordered breakpoints or blank rows still being edited", () => {
    render(
      <BreakpointGrid
        breakpoints={[
          { boundary: "10", label: "Low" },
          { boundary: "20", label: "Mid" },
          { boundary: "", label: "" },
        ]}
        onUpdate={vi.fn()}
        accentColor={ACCENT}
      />,
    )

    expect(screen.queryByRole("img", { name: /out of order/ })).not.toBeInTheDocument()
    expect(screen.getByLabelText("Breakpoint 1 boundary")).not.toHaveAttribute("aria-invalid")
    expect(screen.getByLabelText("Breakpoint 2 boundary")).not.toHaveAttribute("aria-invalid")
    expect(screen.getByLabelText("Breakpoint 3 boundary")).not.toHaveAttribute("aria-invalid")
  })

  it("match counts display when provided", () => {
    render(
      <BreakpointGrid
        breakpoints={[
          { boundary: "10", label: "Low" },
          { boundary: "20", label: "Mid" },
        ]}
        onUpdate={vi.fn()}
        accentColor={ACCENT}
        matchCounts={[5, 12]}
      />,
    )
    expect(screen.getByText("Matches")).toBeInTheDocument()
    expect(screen.getByText("5")).toBeInTheDocument()
    expect(screen.getByText("12")).toBeInTheDocument()
  })

  it("match counts column is hidden when not provided", () => {
    render(
      <BreakpointGrid
        breakpoints={[{ boundary: "10", label: "Low" }]}
        onUpdate={vi.fn()}
        accentColor={ACCENT}
      />,
    )
    expect(screen.queryByText("Matches")).not.toBeInTheDocument()
  })

  it("inputs have aria-labels for accessibility", () => {
    render(
      <BreakpointGrid
        breakpoints={[
          { boundary: "18", label: "Young" },
          { boundary: "25", label: "Mid" },
        ]}
        onUpdate={vi.fn()}
        accentColor={ACCENT}
      />,
    )
    expect(screen.getByLabelText("Breakpoint 1 boundary")).toBeInTheDocument()
    expect(screen.getByLabelText("Breakpoint 1 label")).toBeInTheDocument()
    expect(screen.getByLabelText("Breakpoint 2 boundary")).toBeInTheDocument()
    expect(screen.getByLabelText("Breakpoint 2 label")).toBeInTheDocument()
  })

  it("shows (excl.) when rightClosed is false", () => {
    render(
      <BreakpointGrid
        breakpoints={[{ boundary: "10", label: "Low" }]}
        onUpdate={vi.fn()}
        accentColor={ACCENT}
        rightClosed={false}
      />,
    )
    expect(screen.getByText("(excl.)")).toBeInTheDocument()
    expect(screen.queryByText("(incl.)")).not.toBeInTheDocument()
  })

  it("does not render interval notation or catch-all rows", () => {
    render(
      <BreakpointGrid
        breakpoints={[{ boundary: "18", label: "Young" }]}
        onUpdate={vi.fn()}
        accentColor={ACCENT}
      />,
    )
    expect(screen.queryByText("Interval")).not.toBeInTheDocument()
    expect(screen.queryByText("(a, b]")).not.toBeInTheDocument()
    expect(screen.queryByText(/Everything above/)).not.toBeInTheDocument()
    expect(screen.queryByText("catch-all")).not.toBeInTheDocument()
  })

  it("pastes TSV into a breakpoint cell range and creates missing rows", () => {
    const onUpdate = vi.fn()
    render(
      <BreakpointGrid
        breakpoints={[{ boundary: "18", label: "Young" }]}
        onUpdate={onUpdate}
        accentColor={ACCENT}
      />,
    )

    fireEvent.paste(screen.getByLabelText("Breakpoint 1 label"), {
      clipboardData: { getData: () => "Youth\nAdult" },
    })

    expect(onUpdate).toHaveBeenCalledTimes(1)
    expect(onUpdate.mock.calls[0][0]).toEqual([
      { boundary: "18", label: "Youth" },
      { boundary: "", label: "Adult" },
    ])
  })

})
