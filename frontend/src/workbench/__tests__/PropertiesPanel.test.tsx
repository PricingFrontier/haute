/**
 * The properties panel (specs/workbench): the selected component's title, Show when and
 * layout, a Table's fixed rows and look, a Chart's chart, the order of its fields, and the
 * fields ticked from the schema tables of its kind (a Chart's from every table, the columns
 * it cannot plot disabled), with tables whose rows do not line up greyed out; a Heading's
 * text, description, size, alignment and Show when; and the showing sheet's own panel, its
 * title and its Show when.
 */
import { afterEach, beforeEach, describe, expect, it } from "vitest"
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react"
import useWorkbenchFormStore from "../../stores/useWorkbenchFormStore"
import useWorkbenchViewStore from "../../stores/useWorkbenchViewStore"
import PropertiesPanel from "../PropertiesPanel"
import type { FieldRef, FormSpec } from "../../api/types"
import { chartWidget, column, coverForm, currentForm, fieldsWidget, headingWidget, loadForm, sheetForm, table } from "./fixtures"

const grid = () => fieldsWidget(currentForm(), "w_grid")
const select = (id: string | null) => useWorkbenchViewStore.setState({ selectedId: id })
const chart = () => fieldsWidget(currentForm(), "w_chart")

/** `sheetForm` with a row-number index on equipment and a Chart, w_chart, plotting `fields`. */
function chartForm(fields: FieldRef[]): FormSpec {
  const form = sheetForm()
  form.schema.tables[1].columns.unshift(column("equipment.row", "row", { type: "int", index: true }))
  form.pages[0].widgets.push(chartWidget("w_chart", fields))
  return form
}

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

  it("sets a Table's fixed rows and its look, which a Collection does not have", () => {
    select("w_grid")
    const { rerender } = render(<PropertiesPanel />)

    fireEvent.click(screen.getByRole("checkbox", { name: "Fixed rows" }))
    const look = screen.getByRole("region", { name: "Look" })
    fireEvent.change(within(look).getByRole("combobox", { name: "Density" }), { target: { value: "comfortable" } })
    fireEvent.change(within(look).getByRole("combobox", { name: "Gridlines" }), { target: { value: "none" } })
    fireEvent.click(within(look).getByRole("checkbox", { name: "Row numbers" }))
    fireEvent.click(within(look).getByRole("checkbox", { name: "Striped rows" }))
    expect(grid()).toMatchObject({ fixed_rows: true, density: "comfortable", gridlines: "none", row_numbers: false, striped: false })

    select("w_boxes")
    rerender(<PropertiesPanel />)
    expect(screen.queryByRole("region", { name: "Look" })).not.toBeInTheDocument()
    expect(screen.queryByRole("checkbox", { name: "Fixed rows" })).not.toBeInTheDocument()
  })

  it("sets a Chart's type, what labels its rows, its legend, stacked, horizontal and data labels, each one undo step", () => {
    loadForm(chartForm([]))
    select("w_chart")
    const { rerender } = render(<PropertiesPanel />)
    expect(screen.getByText("Chart")).toBeInTheDocument()
    expect(screen.queryByRole("region", { name: "Layout" })).not.toBeInTheDocument()
    // Nothing to chart: no settings until it plots a field.
    expect(screen.queryByRole("region", { name: "Chart" })).not.toBeInTheDocument()

    loadForm(chartForm([{ table: "equipment", column: "equipment.value" }]))
    select("w_chart")
    rerender(<PropertiesPanel />)
    expect(screen.queryByRole("region", { name: "Layout" })).not.toBeInTheDocument()
    const settings = screen.getByRole("region", { name: "Chart" })
    const undoSteps = () => useWorkbenchFormStore.getState().undoStack.length

    // Rows: numbered, or labelled by a column of a table lined up with it, other than an index.
    const rows = within(settings).getByRole("combobox", { name: "Label rows by" })
    expect(rows).toHaveValue("")
    expect(within(rows).getAllByRole("group").map((group) => group.getAttribute("label"))).toEqual(["equipment", "premiums"])
    expect(within(rows).getAllByRole("option").map((option) => option.textContent)).toEqual(["Row number", "Item", "Value", "Item", "Premium"])
    fireEvent.change(rows, { target: { value: JSON.stringify(["equipment", "equipment.item"]) } })
    expect(chart()).toMatchObject({ category: { table: "equipment", column: "equipment.item" } })
    expect(undoSteps()).toBe(1)
    fireEvent.change(rows, { target: { value: "" } })
    expect(chart()).toMatchObject({ category: null })
    expect(undoSteps()).toBe(2)

    fireEvent.change(within(settings).getByRole("combobox", { name: "Legend" }), { target: { value: "right" } })
    expect(chart()).toMatchObject({ legend: "right" })
    fireEvent.click(within(settings).getByRole("checkbox", { name: "Stacked" }))
    fireEvent.click(within(settings).getByRole("checkbox", { name: "Horizontal" }))
    fireEvent.click(within(settings).getByRole("checkbox", { name: "Data labels" }))
    expect(chart()).toMatchObject({ stacked: true, orientation: "horizontal", data_labels: true })
    expect(undoSteps()).toBe(6)

    // A pie is neither stacked nor horizontal; it still takes data labels.
    fireEvent.change(within(settings).getByRole("combobox", { name: "Chart type" }), { target: { value: "pie" } })
    expect(chart()).toMatchObject({ chart_type: "pie" })
    expect(undoSteps()).toBe(7)
    expect(within(settings).getByRole("checkbox", { name: "Stacked" })).toBeDisabled()
    expect(within(settings).getByRole("checkbox", { name: "Horizontal" })).toBeDisabled()
    expect(within(settings).getByRole("checkbox", { name: "Data labels" })).toBeEnabled()

    // Plotting a one-row table, its one row is not labelled.
    loadForm(chartForm([{ table: "policy", column: "policy.year" }]))
    select("w_chart")
    rerender(<PropertiesPanel />)
    expect(screen.getByRole("combobox", { name: "Chart type" })).toBeInTheDocument()
    expect(screen.queryByRole("combobox", { name: "Label rows by" })).not.toBeInTheDocument()
  })

  it("ticks a Chart's fields from every table, the columns it cannot plot disabled and the tables out of line greyed out", () => {
    loadForm(chartForm([]))
    select("w_chart")
    render(<PropertiesPanel />)
    const fields = screen.getByRole("region", { name: "Fields" })
    const cannotPlot = "A Chart plots number columns other than an index"

    for (const id of ["policy", "equipment", "premiums"]) expect(within(fields).getByTestId(`fields-table-${id}`)).toHaveStyle({ opacity: "1" })
    const state = within(fields).getByRole("checkbox", { name: "Show policy state" })
    expect(state).toBeDisabled()
    expect(state.closest("label")).toHaveAttribute("title", cannotPlot)
    const index = within(fields).getByRole("checkbox", { name: "Show equipment row" })
    expect(index).toBeDisabled()
    expect(index.closest("label")).toHaveAttribute("title", cannotPlot)
    expect(within(fields).getByRole("checkbox", { name: "Show premiums premium" })).toBeEnabled()
    expect(within(fields).getByRole("checkbox", { name: "Show premiums premium" }).closest("label")).not.toHaveAttribute("title")

    // Once it plots a one-row column, the many-row tables do not line up with it.
    fireEvent.click(within(fields).getByRole("checkbox", { name: "Show policy year" }))
    expect(chart().fields).toEqual([{ table: "policy", column: "policy.year" }])
    expect(screen.getByTestId("fields-table-policy")).toHaveStyle({ opacity: "1" })
    const equipment = screen.getByTestId("fields-table-equipment")
    expect(equipment).toHaveStyle({ opacity: "0.5" })
    expect(equipment).toHaveAttribute("title", "Its rows don't line up with the fields already chosen")
    expect(within(equipment).getByRole("checkbox", { name: "Show equipment value" })).toBeDisabled()
    expect(screen.getByTestId("fields-table-premiums")).toHaveStyle({ opacity: "0.5" })
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

  it("sets when a component shows, each change one undo step", () => {
    select("w_grid")
    render(<PropertiesPanel />)
    const showWhen = screen.getByRole("region", { name: "Show when" })

    expect(within(showWhen).getByText("Always")).toBeInTheDocument()
    fireEvent.click(within(showWhen).getByRole("button", { name: "Add condition" }))
    fireEvent.change(within(showWhen).getByRole("combobox", { name: "Condition 1 value" }), { target: { value: "NY" } })

    expect(grid().show_when).toEqual({ match: "all", conditions: [{ table: "policy", column: "policy.state", op: "equals", values: ["NY"] }] })
    expect(useWorkbenchFormStore.getState().undoStack).toHaveLength(2)
  })

  it("edits a Heading's text and description as typed, its size, alignment and when it shows, each one undo step, with no title, layout or fields", () => {
    const form = sheetForm()
    form.pages[0].widgets.push(headingWidget("w_head", "Cover"))
    loadForm(form)
    select("w_head")
    render(<PropertiesPanel />)
    const heading = () => currentForm().pages[0].widgets[2]
    const undoSteps = () => useWorkbenchFormStore.getState().undoStack.length

    expect(screen.getByTestId("workbench-properties")).toHaveTextContent("Heading")
    expect(screen.queryByRole("textbox", { name: "Title" })).not.toBeInTheDocument()
    for (const section of ["Layout", "Order", "Fields"]) expect(screen.queryByRole("region", { name: section })).not.toBeInTheDocument()

    // Its text as typed, spaces and all, and cleared when emptied: a blank Heading is a problem, not refused.
    const text = screen.getByRole("textbox", { name: "Heading text" })
    expect(text).toHaveValue("Cover")
    expect(text).toHaveAttribute("placeholder", "Name a section of the sheet")
    fireEvent.change(text, { target: { value: " Equipment schedule " } })
    fireEvent.keyDown(text, { key: "Enter" })
    expect(heading()).toMatchObject({ text: " Equipment schedule ", size: "medium" })
    fireEvent.change(text, { target: { value: "" } })
    fireEvent.blur(text)
    expect(heading()).toMatchObject({ text: "" })
    expect(text).toHaveValue("")
    expect(undoSteps()).toBe(2)

    // Its description, as typed, under it: none until one is typed.
    const description = within(screen.getByRole("region", { name: "Description" })).getByRole("textbox", { name: "Heading description" })
    expect(description).toHaveValue("")
    expect(description).toHaveAttribute("placeholder", "None")
    fireEvent.change(description, { target: { value: " Each item insured " } })
    fireEvent.keyDown(description, { key: "Enter" })
    expect(heading()).toMatchObject({ description: " Each item insured " })
    expect(undoSteps()).toBe(3)

    // Large, Medium or Small, each the size it names.
    const look = screen.getByRole("region", { name: "Look" })
    const size = within(look).getByRole("combobox", { name: "Size" })
    expect([...size.querySelectorAll("option")].map((option) => [option.value, option.textContent])).toEqual([
      ["large", "Large"],
      ["medium", "Medium"],
      ["small", "Small"],
    ])
    expect(size).toHaveValue("medium")
    fireEvent.change(size, { target: { value: "large" } })
    expect(heading()).toMatchObject({ size: "large" })
    expect(size).toHaveValue("large")
    expect(undoSteps()).toBe(4)

    // Left, Centre or Right, one chosen, as a radio group; choosing it again changes nothing.
    const align = within(look).getByRole("radiogroup", { name: "Align" })
    const chosen = () => within(align).getAllByRole("radio").filter((radio) => radio.getAttribute("aria-checked") === "true").map((radio) => radio.textContent)
    expect(within(align).getAllByRole("radio").map((radio) => radio.textContent)).toEqual(["Left", "Centre", "Right"])
    expect(chosen()).toEqual(["Left"])
    fireEvent.click(within(align).getByRole("radio", { name: "Centre" }))
    expect(heading()).toMatchObject({ align: "center" })
    expect(chosen()).toEqual(["Centre"])
    fireEvent.click(within(align).getByRole("radio", { name: "Centre" }))
    // The arrow keys choose the next, the key kept from the sheet's own keys, which would move the Heading.
    expect(fireEvent.keyDown(within(align).getByRole("radio", { name: "Centre" }), { key: "ArrowRight" })).toBe(false)
    expect(heading()).toMatchObject({ align: "right", x: 16 })
    expect(chosen()).toEqual(["Right"])
    expect(undoSteps()).toBe(6)

    // When it shows, so a section's Heading can hide with the section.
    const showWhen = screen.getByRole("region", { name: "Show when" })
    expect(within(showWhen).getByText("Always")).toBeInTheDocument()
    fireEvent.click(within(showWhen).getByRole("button", { name: "Add condition" }))
    fireEvent.change(within(showWhen).getByRole("combobox", { name: "Condition 1 value" }), { target: { value: "NY" } })
    expect(heading().show_when).toEqual({ match: "all", conditions: [{ table: "policy", column: "policy.state", op: "equals", values: ["NY"] }] })
    expect(undoSteps()).toBe(8)
  })

  it("is the showing sheet's own panel while the sheet is selected: its title and when it shows", () => {
    loadForm(coverForm())
    useWorkbenchViewStore.setState({ pageId: "p2", sheetSelected: true })
    render(<PropertiesPanel />)
    const sheet = () => currentForm().pages[1]

    expect(screen.getByTestId("workbench-properties")).toHaveTextContent("Sheet")
    const title = screen.getByRole("textbox", { name: "Sheet title" })
    expect(title).toHaveValue("Cover")
    fireEvent.change(title, { target: { value: "  " } })
    fireEvent.blur(title)
    expect(sheet().title).toBe("Cover")
    fireEvent.change(title, { target: { value: " Business interruption " } })
    fireEvent.blur(title)
    expect(sheet().title).toBe("Business interruption")

    const showWhen = screen.getByRole("region", { name: "Show when" })
    fireEvent.change(within(showWhen).getByRole("combobox", { name: "Condition 1 test" }), { target: { value: "not_ticked" } })
    expect(sheet().show_when.conditions).toEqual([{ table: "policy", column: "policy.cover", op: "not_ticked", values: [] }])

    act(() => useWorkbenchViewStore.getState().select("w_year"))
    expect(screen.getByRole("textbox", { name: "Title" })).toHaveValue("Year")
    expect(useWorkbenchViewStore.getState().sheetSelected).toBe(false)
  })
})
