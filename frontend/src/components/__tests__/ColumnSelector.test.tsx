import { describe, it, expect, vi, afterEach } from "vitest"
import { render, screen, fireEvent, cleanup, within } from "@testing-library/react"
import ColumnSelector from "../ColumnSelector"
import type { ColumnInfo } from "../../types/node"
import type { ColumnSelection } from "../../utils/columnSelection"

const AVAIL: ColumnInfo[] = [
  { name: "quote_id", dtype: "int" },
  { name: "premium", dtype: "float" },
  { name: "region", dtype: "str" },
]

function renderSelector(over: Partial<React.ComponentProps<typeof ColumnSelector>> = {}) {
  const onChange = vi.fn<(next: ColumnSelection) => void>()
  render(
    <ColumnSelector
      availableColumns={AVAIL}
      selectedColumns={[]}
      columnRenames={{}}
      onChange={onChange}
      {...over}
    />,
  )
  return { onChange, lastCall: () => onChange.mock.calls.at(-1)?.[0] as ColumnSelection }
}

describe("ColumnSelector", () => {
  afterEach(cleanup)

  it("renders one row per column with its incoming order, name, and type", () => {
    renderSelector()
    const rows = screen.getAllByTestId("column-row")
    expect(rows).toHaveLength(3)
    expect(rows[0]).toHaveTextContent("quote_id")
    expect(rows[0]).toHaveTextContent("int")
    expect(within(rows[2]).getByText("3")).toBeInTheDocument() // incoming order
    // empty selection means everything starts ticked
    expect(screen.getAllByTestId("column-select").every((cb) => (cb as HTMLInputElement).checked)).toBe(true)
  })

  it("unticking a column drops it from the selection (in display order)", () => {
    const { onChange, lastCall } = renderSelector()
    fireEvent.click(within(screen.getAllByTestId("column-row")[0]).getByTestId("column-select"))
    expect(onChange).toHaveBeenCalledOnce()
    expect(lastCall().selectedColumns).toEqual(["premium", "region"])
  })

  it("committing a rename emits a column_renames entry keyed by the incoming name", () => {
    const { lastCall } = renderSelector()
    const input = within(screen.getAllByTestId("column-row")[0]).getByTestId("column-rename")
    fireEvent.change(input, { target: { value: "id" } })
    fireEvent.blur(input)
    expect(lastCall().columnRenames).toEqual({ quote_id: "id" })
    // all still kept in natural order → no explicit selected_columns needed
    expect(lastCall().selectedColumns).toEqual([])
  })

  it("dragging a row reorders the selection", () => {
    const { lastCall } = renderSelector()
    const rows = screen.getAllByTestId("column-row")
    fireEvent.dragStart(within(rows[0]).getByTestId("column-grip"))
    fireEvent.dragOver(rows[2])
    fireEvent.drop(rows[2])
    // quote_id moved to the end; all kept but reordered → explicit list
    expect(lastCall().selectedColumns).toEqual(["premium", "region", "quote_id"])
  })

  it("'None' keeps at least the first column so the frame never empties", () => {
    const { lastCall } = renderSelector()
    fireEvent.click(screen.getByTestId("column-select-none"))
    expect(lastCall().selectedColumns).toEqual(["quote_id"])
  })

  it("surfaces a saved column absent upstream as a stale 'not found' row", () => {
    renderSelector({ selectedColumns: ["quote_id", "ghost_col"] })
    const ghost = screen.getAllByTestId("column-row").find((r) =>
      r.getAttribute("data-incoming-name") === "ghost_col",
    )
    expect(ghost).toBeDefined()
    expect(ghost).toHaveTextContent("not found")
  })
})
