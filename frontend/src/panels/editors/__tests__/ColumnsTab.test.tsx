import { describe, it, expect, vi, afterEach } from "vitest"
import { render, screen, fireEvent, cleanup, within } from "@testing-library/react"
import ColumnsTab from "../ColumnsTab"

const COLUMNS = [
  { name: "a", dtype: "Int64" },
  { name: "b", dtype: "Float64" },
  { name: "c", dtype: "String" },
]

const rowByName = (name: string) =>
  screen.getAllByTestId("columns-row").find((r) => r.getAttribute("data-incoming-name") === name)!

describe("ColumnsTab (shared ColumnSelector)", () => {
  afterEach(cleanup)

  it("renders columns via the shared selector, all kept by default", () => {
    render(<ColumnsTab config={{}} onUpdate={vi.fn()} availableColumns={COLUMNS} columns={COLUMNS} />)
    expect(screen.getAllByTestId("columns-row")).toHaveLength(3)
    expect(
      screen.getAllByTestId("columns-select").every((cb) => (cb as HTMLInputElement).checked),
    ).toBe(true)
  })

  it("reflects selected_columns from config (excluded column unticked)", () => {
    render(
      <ColumnsTab
        config={{ selected_columns: ["a", "c"] }}
        onUpdate={vi.fn()}
        availableColumns={COLUMNS}
        columns={COLUMNS}
      />,
    )
    expect(within(rowByName("b")).getByTestId("columns-select")).not.toBeChecked()
    expect(within(rowByName("a")).getByTestId("columns-select")).toBeChecked()
  })

  it("writes selected_columns + column_renames atomically on a toggle", () => {
    const onUpdate = vi.fn()
    render(<ColumnsTab config={{}} onUpdate={onUpdate} availableColumns={COLUMNS} columns={COLUMNS} />)
    fireEvent.click(within(rowByName("b")).getByTestId("columns-select"))
    expect(onUpdate).toHaveBeenCalledWith({ selected_columns: ["a", "c"], column_renames: {} })
  })

  it("writes a column_renames entry on rename commit", () => {
    const onUpdate = vi.fn()
    render(<ColumnsTab config={{}} onUpdate={onUpdate} availableColumns={COLUMNS} columns={COLUMNS} />)
    const input = within(rowByName("a")).getByTestId("columns-rename")
    fireEvent.change(input, { target: { value: "alpha" } })
    fireEvent.blur(input)
    expect(onUpdate).toHaveBeenCalledWith({ selected_columns: [], column_renames: { a: "alpha" } })
  })

  it("'None' keeps the first column only", () => {
    const onUpdate = vi.fn()
    render(<ColumnsTab config={{}} onUpdate={onUpdate} availableColumns={COLUMNS} columns={COLUMNS} />)
    fireEvent.click(screen.getByTestId("columns-select-none"))
    expect(onUpdate).toHaveBeenCalledWith({ selected_columns: ["a"], column_renames: {} })
  })

  it("offers a column filter", () => {
    render(<ColumnsTab config={{}} onUpdate={vi.fn()} availableColumns={COLUMNS} columns={COLUMNS} />)
    fireEvent.change(screen.getByTestId("columns-search"), { target: { value: "b" } })
    expect(screen.getAllByTestId("columns-row")).toHaveLength(1)
  })

  it("shows the empty state when no columns are available", () => {
    render(<ColumnsTab config={{}} onUpdate={vi.fn()} availableColumns={[]} columns={[]} />)
    expect(screen.getByText(/Preview or run/)).toBeInTheDocument()
  })
})
