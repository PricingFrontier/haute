import { describe, it, expect, vi, afterEach } from "vitest"
import { render, screen, fireEvent, cleanup } from "@testing-library/react"
import { CategoricalValuePicker } from "../CategoricalValuePicker"

const ACCENT = "#f97316"

describe("CategoricalValuePicker", () => {
  afterEach(cleanup)

  it("renders 'Connect data to see values' when empty", () => {
    render(
      <CategoricalValuePicker
        availableValues={[]}
        existingValues={[]}
        onAddValue={vi.fn()}
        accentColor={ACCENT}
      />,
    )
    expect(screen.getByText("Connect data to see values")).toBeInTheDocument()
  })

  it("renders value chips with counts", () => {
    render(
      <CategoricalValuePicker
        availableValues={[
          { value: "Car", count: 50 },
          { value: "Bike", count: 30 },
        ]}
        existingValues={[]}
        onAddValue={vi.fn()}
        accentColor={ACCENT}
      />,
    )
    expect(screen.getByText("Car (50)")).toBeInTheDocument()
    expect(screen.getByText("Bike (30)")).toBeInTheDocument()
  })

  it("already-used values are visually distinct", () => {
    const { container } = render(
      <CategoricalValuePicker
        availableValues={[
          { value: "Car", count: 50 },
          { value: "Bike", count: 30 },
        ]}
        existingValues={["Car"]}
        onAddValue={vi.fn()}
        accentColor={ACCENT}
      />,
    )
    // "Car" chip should have opacity or a check indicator
    const carChip = screen.getByText("Car (50)").closest("button")
    expect(carChip).toHaveStyle({ opacity: "0.5" })
    // "Bike" chip should not be dimmed
    const bikeChip = screen.getByText("Bike (30)").closest("button")
    expect(bikeChip).not.toHaveStyle({ opacity: "0.5" })
  })

  it("clicking unused value calls onAddValue", () => {
    const onAdd = vi.fn()
    render(
      <CategoricalValuePicker
        availableValues={[
          { value: "Car", count: 50 },
          { value: "Bike", count: 30 },
        ]}
        existingValues={["Car"]}
        onAddValue={onAdd}
        accentColor={ACCENT}
      />,
    )
    fireEvent.click(screen.getByText("Bike (30)"))
    expect(onAdd).toHaveBeenCalledWith("Bike")
  })

  it("clicking used value does NOT call onAddValue", () => {
    const onAdd = vi.fn()
    render(
      <CategoricalValuePicker
        availableValues={[
          { value: "Car", count: 50 },
        ]}
        existingValues={["Car"]}
        onAddValue={onAdd}
        accentColor={ACCENT}
      />,
    )
    fireEvent.click(screen.getByText("Car (50)"))
    expect(onAdd).not.toHaveBeenCalled()
  })

  it("search filter works when > 10 values", () => {
    const values = Array.from({ length: 15 }, (_, i) => ({
      value: `Value_${i}`,
      count: i * 10,
    }))
    render(
      <CategoricalValuePicker
        availableValues={values}
        existingValues={[]}
        onAddValue={vi.fn()}
        accentColor={ACCENT}
      />,
    )
    // Search input should be present when > 10 values
    const searchInput = screen.getByPlaceholderText("Filter values...")
    expect(searchInput).toBeInTheDocument()

    // Type a filter
    fireEvent.change(searchInput, { target: { value: "Value_1" } })
    // Should show Value_1, Value_10, Value_11, etc. but not Value_2
    expect(screen.getByText("Value_1 (10)")).toBeInTheDocument()
    expect(screen.queryByText("Value_2 (20)")).not.toBeInTheDocument()
  })

  it("search filter is not shown when <= 10 values", () => {
    const values = Array.from({ length: 5 }, (_, i) => ({
      value: `Val_${i}`,
      count: i,
    }))
    render(
      <CategoricalValuePicker
        availableValues={values}
        existingValues={[]}
        onAddValue={vi.fn()}
        accentColor={ACCENT}
      />,
    )
    expect(screen.queryByPlaceholderText("Filter values...")).not.toBeInTheDocument()
  })

  it("handles special characters in values", () => {
    render(
      <CategoricalValuePicker
        availableValues={[
          { value: "A&B <C>", count: 5 },
          { value: 'He said "hello"', count: 3 },
        ]}
        existingValues={[]}
        onAddValue={vi.fn()}
        accentColor={ACCENT}
      />,
    )
    expect(screen.getByText("A&B <C> (5)")).toBeInTheDocument()
    expect(screen.getByText('He said "hello" (3)')).toBeInTheDocument()
  })
})
