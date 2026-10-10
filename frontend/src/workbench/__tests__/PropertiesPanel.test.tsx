/**
 * The properties panel (specs/workbench): the selected component's title and layout, the
 * order of its fields, and the fields ticked from the schema tables of its kind, with
 * tables whose rows do not line up greyed out.
 */
import { afterEach, beforeEach, describe, expect, it } from "vitest"
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react"
import useWorkbenchViewStore from "../../stores/useWorkbenchViewStore"
import PropertiesPanel from "../PropertiesPanel"
import { column, currentForm, loadForm, sheetForm, table } from "./fixtures"

const grid = () => currentForm().pages[0].widgets[1]
const select = (id: string | null) => useWorkbenchViewStore.setState({ selectedId: id })

describe("PropertiesPanel", () => {
  beforeEach(() => loadForm())
  afterEach(cleanup)

  it("shows nothing until a component is selected, then its kind, title and layout", () => {
    const { rerender } = render(<PropertiesPanel />)
    expect(screen.queryByTestId("workbench-properties")).not.toBeInTheDocument()

    select("w_grid")
    rerender(<PropertiesPanel />)
    expect(screen.getByText("Table")).toBeInTheDocument()
    const title = screen.getByRole("textbox", { name: "Title" })
    expect(title).toHaveValue("Equipment")
    fireEvent.change(title, { target: { value: "Schedule" } })
    fireEvent.blur(title)
    expect(grid().title).toBe("Schedule")

    const rows = screen.getByTestId("widget-rows")
    fireEvent.change(rows, { target: { value: "60" } })
    fireEvent.blur(rows)
    expect(screen.getByTestId("widget-rows-error")).toHaveTextContent("Enter a whole number from 1 to 50.")
    expect(grid()).toMatchObject({ rows: 3 })
    fireEvent.change(rows, { target: { value: "5" } })
    fireEvent.blur(rows)
    expect(grid()).toMatchObject({ rows: 5 })

    fireEvent.click(screen.getByTitle("Close"))
    expect(useWorkbenchViewStore.getState().selectedId).toBeNull()
  })

  it("ticks the fields from the schema tables of the component's kind, in the order ticked", () => {
    select("w_grid")
    render(<PropertiesPanel />)
    const fields = screen.getByRole("region", { name: "Fields" })

    // A Table shows many-row tables: equipment and premiums, not policy.
    expect(within(fields).queryByTestId("fields-table-policy")).not.toBeInTheDocument()
    expect(within(fields).getByText("2/2")).toBeInTheDocument()
    fireEvent.click(within(fields).getByRole("checkbox", { name: "Show premiums premium" }))
    expect(grid().fields.map((field) => field.column)).toEqual(["equipment.item", "equipment.value", "premiums.premium"])
    fireEvent.click(within(fields).getByRole("checkbox", { name: "Show equipment item" }))
    expect(grid().fields.map((field) => field.column)).toEqual(["equipment.value", "premiums.premium"])
    expect(screen.getByText("2 shown")).toBeInTheDocument()
  })

  it("greys out a table whose rows do not line up with the fields already chosen", () => {
    const form = sheetForm()
    form.schema.tables.push(table("claims", "claims", { rows: "many", columns: [column("claims.id", "id", { key: true })] }))
    loadForm(form)
    select("w_grid")
    render(<PropertiesPanel />)

    const claims = screen.getByTestId("fields-table-claims")
    expect(claims).toHaveStyle({ opacity: "0.5" })
    expect(claims).toHaveAttribute("title", "Its rows don't line up with the fields already chosen")
    expect(within(claims).getByRole("checkbox", { name: "Show claims id" })).toBeDisabled()
    expect(screen.getByTestId("fields-table-premiums")).toHaveStyle({ opacity: "1" })
  })

  it("orders the fields: Alt+Down moves one, and a cross stops showing it", () => {
    select("w_grid")
    render(<PropertiesPanel />)
    const order = screen.getByRole("region", { name: "Order" })

    const rows = within(order).getAllByRole("listitem")
    expect(rows.map((row) => row.getAttribute("aria-label"))).toEqual(["1. Item", "2. Value"])
    fireEvent.keyDown(rows[0], { key: "ArrowDown", altKey: true })
    expect(grid().fields.map((field) => field.column)).toEqual(["equipment.value", "equipment.item"])

    fireEvent.click(within(order).getByRole("button", { name: "Stop showing Value" }))
    expect(grid().fields.map((field) => field.column)).toEqual(["equipment.item"])
  })

  it("points at the schema when it has no tables of the component's kind", () => {
    const form = sheetForm()
    form.schema.tables = form.schema.tables.filter((candidate) => candidate.rows !== "one")
    loadForm(form)
    select("w_boxes")
    render(<PropertiesPanel />)

    expect(screen.getByText(/The schema has no one-row tables yet/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", { name: "Open the schema" }))
    expect(useWorkbenchViewStore.getState().section).toBe("schema")
  })
})
