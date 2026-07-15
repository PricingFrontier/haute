/**
 * Render tests for ConstantEditor.
 *
 * Tests: values label, empty state, row rendering, add/remove/edit rows.
 */
import { describe, it, expect, vi, afterEach } from "vitest"
import { render, screen, fireEvent, cleanup } from "@testing-library/react"
import ConstantEditor from "../../panels/editors/ConstantEditor"

afterEach(cleanup)

const DEFAULT_PROPS = {
  config: {},
  onUpdate: vi.fn(),
}

describe("ConstantEditor", () => {
  it("renders Values label", () => {
    render(<ConstantEditor {...DEFAULT_PROPS} />)
    expect(screen.getByText("Values")).toBeTruthy()
  })

  it("renders empty state when config has no values", () => {
    render(<ConstantEditor {...DEFAULT_PROPS} />)
    expect(screen.queryByPlaceholderText("name")).toBeNull()
    expect(screen.queryByPlaceholderText("value")).toBeNull()
    expect(screen.getByText("Add value")).toBeTruthy()
  })

  it("renders rows from config values", () => {
    const config = {
      values: [
        { name: "rate", value: "0.05" },
        { name: "cap", value: "100" },
      ],
    }
    render(<ConstantEditor config={config} onUpdate={vi.fn()} />)
    expect(screen.getByDisplayValue("rate")).toBeTruthy()
    expect(screen.getByDisplayValue("0.05")).toBeTruthy()
    expect(screen.getByDisplayValue("cap")).toBeTruthy()
    expect(screen.getByDisplayValue("100")).toBeTruthy()
  })

  it("add button adds a new row with default name constant_N+1", () => {
    const onUpdate = vi.fn()
    const existing = [{ name: "rate", value: "0.05" }]
    render(<ConstantEditor config={{ values: existing }} onUpdate={onUpdate} />)
    fireEvent.click(screen.getByText("Add value"))
    expect(onUpdate).toHaveBeenCalledWith("values", [
      ...existing,
      { name: "constant_2", value: "0" },
    ])
  })

  it("editing a name commits the updated array once on blur (undo-atomicity)", () => {
    const onUpdate = vi.fn()
    const values = [{ name: "rate", value: "0.05" }]
    render(<ConstantEditor config={{ values }} onUpdate={onUpdate} />)
    const nameInput = screen.getByDisplayValue("rate")
    fireEvent.change(nameInput, { target: { value: "new_rate" } })
    expect(onUpdate).not.toHaveBeenCalled()
    fireEvent.blur(nameInput)
    expect(onUpdate).toHaveBeenCalledTimes(1)
    expect(onUpdate).toHaveBeenCalledWith("values", [
      { name: "new_rate", value: "0.05" },
    ])
  })

  it("editing a value commits the updated array once on blur (undo-atomicity)", () => {
    const onUpdate = vi.fn()
    const values = [{ name: "rate", value: "0.05" }]
    render(<ConstantEditor config={{ values }} onUpdate={onUpdate} />)
    const valueInput = screen.getByDisplayValue("0.05")
    fireEvent.change(valueInput, { target: { value: "0.10" } })
    expect(onUpdate).not.toHaveBeenCalled()
    fireEvent.blur(valueInput)
    expect(onUpdate).toHaveBeenCalledTimes(1)
    expect(onUpdate).toHaveBeenCalledWith("values", [
      { name: "rate", value: "0.10" },
    ])
  })

  it("remove button calls onUpdate with filtered array", () => {
    const onUpdate = vi.fn()
    const values = [
      { name: "rate", value: "0.05" },
      { name: "cap", value: "100" },
    ]
    render(<ConstantEditor config={{ values }} onUpdate={onUpdate} />)
    const removeButtons = screen.getAllByTitle("Remove")
    fireEvent.click(removeButtons[0])
    expect(onUpdate).toHaveBeenCalledWith("values", [
      { name: "cap", value: "100" },
    ])
  })

  it("multiple adds increment the counter", () => {
    const onUpdate = vi.fn()
    render(<ConstantEditor config={{ values: [] }} onUpdate={onUpdate} />)
    fireEvent.click(screen.getByText("Add value"))
    expect(onUpdate).toHaveBeenCalledWith("values", [
      { name: "constant_1", value: "0" },
    ])

    // Re-render with the result of the first add
    cleanup()
    onUpdate.mockClear()
    render(
      <ConstantEditor
        config={{ values: [{ name: "constant_1", value: "0" }] }}
        onUpdate={onUpdate}
      />,
    )
    fireEvent.click(screen.getByText("Add value"))
    expect(onUpdate).toHaveBeenCalledWith("values", [
      { name: "constant_1", value: "0" },
      { name: "constant_2", value: "0" },
    ])
  })
})
