import { describe, it, expect, vi, afterEach } from "vitest"
import { render, screen, fireEvent, cleanup } from "@testing-library/react"
import { BreakpointGrid } from "../BreakpointGrid"

const ACCENT = "#f97316"

describe("BreakpointGrid", () => {
  afterEach(cleanup)

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
})
