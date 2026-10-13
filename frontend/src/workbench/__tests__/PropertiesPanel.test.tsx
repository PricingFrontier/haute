/**
 * The properties panel (specs/workbench): the selected component's title, Show when and
 * layout, a Table's fixed rows and look, a Chart's chart, the order of its fields, and the
 * fields ticked from the schema tables of its kind (a Chart's from every table, the columns
 * it cannot plot disabled), with tables whose rows do not line up greyed out; a Heading's
 * text, description, size, alignment and Show when; the showing sheet's own panel, its
 * title and its Show when; and a section's panel, its title, its Show when, its height and
 * Fit, a section added below it, and its deletion.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react"
import useWorkbenchFormStore from "../../stores/useWorkbenchFormStore"
import useWorkbenchViewStore from "../../stores/useWorkbenchViewStore"
import { findSection, findWidget } from "../../utils/workbenchForm"
import PropertiesPanel from "../PropertiesPanel"
import type { FieldRef, FormSpec, Section } from "../../api/types"
import { COMPONENT_COLOR } from "../widgetKinds"
import { addTo, chartWidget, column, coverForm, currentForm, fieldsWidget, headingWidget, loadForm, sectionedForm, sheetForm, table, widgetsOf } from "./fixtures"

const grid = () => fieldsWidget(currentForm(), "w_grid")
const select = (id: string | null) => useWorkbenchViewStore.setState({ selectedId: id })
const chart = () => fieldsWidget(currentForm(), "w_chart")
/** Open a section's panel, as pressing its header does. */
const openSection = (id: string) => act(() => useWorkbenchViewStore.getState().selectSection(id))
/** Show a Table's or a Collection's pane, as pressing its tab does. */
const showPane = (name: "Config" | "Display") => fireEvent.click(screen.getByRole("tab", { name }))
const sectionIds = () => currentForm().pages[0].sections.map((candidate) => candidate.id)

/** The form's section `id`, as it is now. */
function sectionOf(id: string): Section {
  const found = findSection(currentForm(), id)
  if (found === null) throw new Error(`The form has no section ${id}`)
  return found.section
}

/** `sheetForm` with a row-number index on equipment and a Chart, w_chart, plotting `fields`. */
function chartForm(fields: FieldRef[]): FormSpec {
  const form = sheetForm()
  form.schema.tables[1].columns.unshift(column("equipment.row", "row", { type: "int", index: true }))
  addTo(form, chartWidget("w_chart", fields))
  return form
}

describe("PropertiesPanel", () => {
  beforeEach(() => loadForm())
  afterEach(() => {
    cleanup()
    vi.restoreAllMocks()
  })

  it("shows nothing until a component is selected, then its kind, title and layout", () => {
    const { rerender } = render(<PropertiesPanel />)
    expect(screen.queryByTestId("workbench-properties")).not.toBeInTheDocument()

    select("w_grid")
    rerender(<PropertiesPanel />)
    expect(screen.getByText("Table")).toBeInTheDocument()
    showPane("Display")
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

  it("puts a Table's and a Collection's settings in two panes, Config with the data it shows and Display with how, keeping each component's pane", () => {
    select("w_grid")
    const { rerender } = render(<PropertiesPanel />)
    const pane = () => screen.getByRole("tabpanel")
    const regions = () => within(pane()).getAllByRole("region").map((region) => region.getAttribute("aria-label"))

    // Config first, in the component's colour: its order and its fields.
    expect(within(screen.getByRole("tablist", { name: "Table panes" })).getAllByRole("tab").map((tab) => tab.textContent)).toEqual(["Config", "Display"])
    const config = screen.getByRole("tab", { name: "Config" })
    expect(config).toHaveAttribute("aria-selected", "true")
    expect(config).toHaveStyle({ color: COMPONENT_COLOR })
    expect(pane()).toHaveAttribute("aria-labelledby", "component-config-tab")
    expect(regions()).toEqual(["Order", "Fields"])
    // Display: its title, when it shows, its layout and its look.
    showPane("Display")
    expect(regions()).toEqual(["Title", "Show when", "Layout", "Look"])

    // Each component keeps its own: a Collection opens on Config.
    select("w_boxes")
    rerender(<PropertiesPanel />)
    expect(screen.getByRole("tablist", { name: "Collection panes" })).toBeInTheDocument()
    expect(screen.getByRole("tab", { name: "Config" })).toHaveAttribute("aria-selected", "true")
    showPane("Display")
    expect(regions()).toEqual(["Title", "Show when", "Layout", "Look"])
    select("w_grid")
    rerender(<PropertiesPanel />)
    expect(screen.getByRole("tab", { name: "Display" })).toHaveAttribute("aria-selected", "true")
    // The pane is the view's, not the form's: nothing to undo.
    expect(useWorkbenchFormStore.getState().undoStack).toEqual([])
  })

  it("starts a Table's grid in the sample again at the rows it is given, keeping every value typed, each one undo step", () => {
    const form = sheetForm()
    form.sample = { equipment: [{}, { "equipment.item": "Crane" }, {}, {}, {}] }
    loadForm(form)
    select("w_grid")
    render(<PropertiesPanel />)
    showPane("Display")
    const setRows = (text: string) => {
      const rows = screen.getByTestId("widget-rows")
      fireEvent.change(rows, { target: { value: text } })
      fireEvent.blur(rows)
    }

    // Fewer: the empty rows after the last typed go, down to it.
    setRows("1")
    expect(grid()).toMatchObject({ rows: 1 })
    expect(currentForm().sample.equipment).toEqual([{}, { "equipment.item": "Crane" }])
    // More: empty rows make them up.
    setRows("4")
    expect(currentForm().sample.equipment).toEqual([{}, { "equipment.item": "Crane" }, {}, {}])
    expect(useWorkbenchFormStore.getState().undoStack).toHaveLength(2)
    act(() => useWorkbenchFormStore.getState().undo())
    expect(grid()).toMatchObject({ rows: 1 })
    expect(currentForm().sample.equipment).toEqual([{}, { "equipment.item": "Crane" }])
  })

  it("sets a Table's fixed rows and its look, and a Collection's look, each with its own", () => {
    select("w_grid")
    const { rerender } = render(<PropertiesPanel />)
    showPane("Display")

    fireEvent.click(screen.getByRole("checkbox", { name: "Fixed rows" }))
    const look = screen.getByRole("region", { name: "Look" })
    fireEvent.change(within(look).getByRole("combobox", { name: "Density" }), { target: { value: "comfortable" } })
    fireEvent.change(within(look).getByRole("combobox", { name: "Gridlines" }), { target: { value: "none" } })
    const header = within(look).getByRole("combobox", { name: "Header" })
    expect([...header.querySelectorAll("option")].map((option) => [option.value, option.textContent])).toEqual([
      ["plain", "As written"],
      ["caps", "Capitals"],
    ])
    fireEvent.change(header, { target: { value: "caps" } })
    fireEvent.click(within(look).getByRole("checkbox", { name: "Row numbers" }))
    fireEvent.click(within(look).getByRole("checkbox", { name: "Striped rows" }))
    fireEvent.click(within(look).getByRole("checkbox", { name: "Frame" }))
    expect(grid()).toMatchObject({ fixed_rows: true, density: "comfortable", gridlines: "none", header: "caps", row_numbers: false, striped: false, frame: false })
    expect(useWorkbenchFormStore.getState().undoStack).toHaveLength(7)

    // A Collection's look is its labels, its density and its frame, none of a Table's own.
    select("w_boxes")
    rerender(<PropertiesPanel />)
    showPane("Display")
    expect(screen.getByTestId("widget-columns")).toHaveValue("2")
    expect(screen.queryByRole("checkbox", { name: "Fixed rows" })).not.toBeInTheDocument()
    const boxesLook = screen.getByRole("region", { name: "Look" })
    expect(within(boxesLook).queryByRole("combobox", { name: "Gridlines" })).not.toBeInTheDocument()
    expect(within(boxesLook).queryByRole("combobox", { name: "Header" })).not.toBeInTheDocument()
    const labels = within(boxesLook).getByRole("combobox", { name: "Labels" })
    expect([...labels.querySelectorAll("option")].map((option) => [option.value, option.textContent])).toEqual([
      ["above", "Above"],
      ["beside", "Beside"],
    ])
    expect(labels).toHaveValue("above")
    expect(within(boxesLook).getByRole("combobox", { name: "Density" })).toHaveValue("comfortable")
    fireEvent.change(labels, { target: { value: "beside" } })
    fireEvent.change(within(boxesLook).getByRole("combobox", { name: "Density" }), { target: { value: "compact" } })
    fireEvent.click(within(boxesLook).getByRole("checkbox", { name: "Frame" }))
    expect(fieldsWidget(currentForm(), "w_boxes")).toMatchObject({ labels: "beside", density: "compact", frame: false })
    expect(useWorkbenchFormStore.getState().undoStack).toHaveLength(10)
  })

  it("sets a Chart's type, what labels its rows, its legend, stacked, horizontal and data labels, each one undo step", () => {
    loadForm(chartForm([]))
    select("w_chart")
    const { rerender } = render(<PropertiesPanel />)
    expect(screen.getByText("Chart")).toBeInTheDocument()
    // A Chart's settings are one list, with no panes.
    expect(screen.queryByRole("tablist")).not.toBeInTheDocument()
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
    showPane("Display")
    const showWhen = screen.getByRole("region", { name: "Show when" })

    expect(within(showWhen).getByText("Always")).toBeInTheDocument()
    fireEvent.click(within(showWhen).getByRole("button", { name: "Add condition" }))
    fireEvent.change(within(showWhen).getByRole("combobox", { name: "Condition 1 value" }), { target: { value: "NY" } })

    expect(grid().show_when).toEqual({ match: "all", conditions: [{ table: "policy", column: "policy.state", op: "equals", values: ["NY"] }] })
    expect(useWorkbenchFormStore.getState().undoStack).toHaveLength(2)
  })

  it("edits a Heading's text and description as typed, its size, alignment and when it shows, each one undo step, with no title, layout or fields", () => {
    const form = sheetForm()
    addTo(form, headingWidget("w_head", "Cover"))
    loadForm(form)
    select("w_head")
    render(<PropertiesPanel />)
    const heading = () => widgetsOf(currentForm())[2]
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
    showPane("Display")
    expect(screen.getByRole("textbox", { name: "Title" })).toHaveValue("Year")
    expect(useWorkbenchViewStore.getState().sheetSelected).toBe(false)
  })

  it("is a section's own panel while the section is selected: its title, a blank one kept out, and when it shows, each one undo step", () => {
    loadForm(sectionedForm())
    openSection("s_flood")
    render(<PropertiesPanel />)
    const undoSteps = () => useWorkbenchFormStore.getState().undoStack.length

    expect(screen.getByText("Section")).toBeInTheDocument()
    const title = screen.getByRole("textbox", { name: "Section title" })
    expect(title).toHaveValue("Flood")
    fireEvent.change(title, { target: { value: "  " } })
    fireEvent.blur(title)
    expect(sectionOf("s_flood").title).toBe("Flood")
    expect(title).toHaveValue("Flood")
    fireEvent.change(title, { target: { value: " Flood cover " } })
    fireEvent.keyDown(title, { key: "Enter" })
    expect(sectionOf("s_flood").title).toBe("Flood cover")
    expect(undoSteps()).toBe(1)

    // Its own conditions, apart from its sheet's and its components'.
    const showWhen = screen.getByRole("region", { name: "Show when" })
    expect(within(showWhen).getByRole("combobox", { name: "Condition 1 column" })).toHaveValue("policy:policy.cover")
    fireEvent.change(within(showWhen).getByRole("combobox", { name: "Condition 1 test" }), { target: { value: "not_ticked" } })
    expect(sectionOf("s_flood").show_when).toEqual({ match: "all", conditions: [{ table: "policy", column: "policy.cover", op: "not_ticked", values: [] }] })
    expect(currentForm().pages[0].show_when.conditions).toEqual([])
    expect(grid().show_when.conditions).toEqual([])
    expect(undoSteps()).toBe(2)

    fireEvent.click(screen.getByTitle("Close"))
    expect(useWorkbenchViewStore.getState().sectionId).toBeNull()
    expect(screen.queryByTestId("workbench-properties")).not.toBeInTheDocument()
  })

  it("sets a section's height in whole pixels, refusing less than its components reach, and fits it to them", () => {
    loadForm(sectionedForm())
    openSection("s_flood")
    render(<PropertiesPanel />)
    const height = within(screen.getByRole("region", { name: "Layout" })).getByTestId("section-height")
    expect(height).toHaveValue("232")

    // Its Table, 16 from its top and 200 tall, reaches 216 down it.
    for (const typed of ["200", "240.5", "tall"]) {
      fireEvent.change(height, { target: { value: typed } })
      fireEvent.blur(height)
      expect(screen.getByTestId("section-height-error")).toHaveTextContent("Enter a whole number of pixels, 216 or more.")
      expect(sectionOf("s_flood").height).toBe(232)
    }
    fireEvent.change(height, { target: { value: "216" } })
    fireEvent.blur(height)
    expect(sectionOf("s_flood").height).toBe(216)
    expect(screen.queryByTestId("section-height-error")).not.toBeInTheDocument()

    // Fit leaves 16 under its lowest component.
    fireEvent.click(screen.getByRole("button", { name: "Fit" }))
    expect(sectionOf("s_flood").height).toBe(232)
    expect(height).toHaveValue("232")
    expect(useWorkbenchFormStore.getState().undoStack).toHaveLength(2)
    expect(currentForm().pages[0].sections.map((section) => section.height)).toEqual([152, 232, 72])
  })

  it("refuses a section's height of digits alone past the safe whole numbers, which no form could save, and takes the least its components reach", () => {
    loadForm(sectionedForm())
    openSection("s_notes")
    render(<PropertiesPanel />)
    const height = within(screen.getByRole("region", { name: "Layout" })).getByTestId("section-height")
    expect(height).toHaveValue("72")

    // 400 nines are Infinity as a number, sent as null; 2^53 is the first whole number past
    // the safe ones.
    for (const typed of ["9".repeat(400), "9007199254740992"]) {
      fireEvent.change(height, { target: { value: typed } })
      fireEvent.blur(height)
      expect(screen.getByTestId("section-height-error")).toHaveTextContent("Enter a whole number of pixels, 56 or more.")
      expect(sectionOf("s_notes").height).toBe(72)
    }
    expect(useWorkbenchFormStore.getState().undoStack).toHaveLength(0)

    // Its Heading, 16 from its top and 40 tall, reaches 56 down it: the least it can be.
    fireEvent.change(height, { target: { value: "56" } })
    fireEvent.blur(height)
    expect(sectionOf("s_notes").height).toBe(56)
    expect(height).toHaveValue("56")
    expect(screen.queryByTestId("section-height-error")).not.toBeInTheDocument()
    expect(useWorkbenchFormStore.getState().undoStack).toHaveLength(1)
  })

  it("shows a section its own height, never a draft refused for another section as tall", () => {
    const form = sectionedForm()
    // "Notes" as tall as "Flood", though its Heading reaches only 56 down it.
    form.pages[0].sections[2].height = 232
    loadForm(form)
    openSection("s_flood")
    render(<PropertiesPanel />)
    const height = () => screen.getByTestId("section-height")

    fireEvent.change(height(), { target: { value: "200" } })
    fireEvent.blur(height())
    expect(screen.getByTestId("section-height-error")).toHaveTextContent("Enter a whole number of pixels, 216 or more.")

    // On to "Notes": its panel shows its own height, and what was typed for "Flood" never
    // reaches it, though 200 would do for it.
    openSection("s_notes")
    expect(height()).toHaveValue("232")
    expect(screen.queryByTestId("section-height-error")).not.toBeInTheDocument()
    fireEvent.focus(height())
    fireEvent.blur(height())
    expect(sectionOf("s_notes").height).toBe(232)
    expect(useWorkbenchFormStore.getState().undoStack).toHaveLength(0)
  })

  it("adds a section below it, 160 tall and named in turn, and selects the new one", () => {
    loadForm(sectionedForm())
    openSection("s_policy")
    render(<PropertiesPanel />)

    fireEvent.click(screen.getByRole("button", { name: "Add section below" }))
    const added = currentForm().pages[0].sections[1]
    expect(sectionIds()).toEqual(["s_policy", added.id, "s_flood", "s_notes"])
    expect(added).toMatchObject({ title: "Section 4", height: 160, show_when: { match: "all", conditions: [] }, widgets: [] })
    expect(useWorkbenchViewStore.getState()).toMatchObject({ sectionId: added.id, selectedId: null, sheetSelected: false })
    expect(useWorkbenchFormStore.getState().undoStack).toHaveLength(1)

    // Its panel shows now: empty, it can be as little as 48 tall, which Fit makes it.
    expect(screen.getByRole("textbox", { name: "Section title" })).toHaveValue("Section 4")
    const height = screen.getByTestId("section-height")
    expect(height).toHaveValue("160")
    fireEvent.change(height, { target: { value: "40" } })
    fireEvent.blur(height)
    expect(screen.getByTestId("section-height-error")).toHaveTextContent("Enter a whole number of pixels, 48 or more.")
    fireEvent.click(screen.getByRole("button", { name: "Fit" }))
    expect(sectionOf(added.id).height).toBe(48)
  })

  it("deletes a section with its components, asking first while it holds any, and closes its panel", () => {
    const form = sectionedForm()
    form.pages[0].sections.push({ id: "s_empty", title: "Empty", height: 48, show_when: { match: "all", conditions: [] }, widgets: [] })
    loadForm(form)
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false)
    openSection("s_flood")
    render(<PropertiesPanel />)

    fireEvent.click(screen.getByRole("button", { name: "Delete section" }))
    expect(confirm).toHaveBeenCalledWith('Delete "Flood" and the 1 component in it? The schema and the sample stay.')
    expect(sectionIds()).toEqual(["s_policy", "s_flood", "s_notes", "s_empty"])
    expect(screen.getByRole("textbox", { name: "Section title" })).toHaveValue("Flood")

    confirm.mockReturnValue(true)
    fireEvent.click(screen.getByRole("button", { name: "Delete section" }))
    expect(sectionIds()).toEqual(["s_policy", "s_notes", "s_empty"])
    expect(findWidget(currentForm(), "w_grid")).toBeNull()
    // The schema stays.
    expect(currentForm().schema.tables).toHaveLength(3)
    expect(useWorkbenchFormStore.getState().undoStack).toHaveLength(1)
    expect(useWorkbenchViewStore.getState().sectionId).toBeNull()
    expect(screen.queryByTestId("workbench-properties")).not.toBeInTheDocument()

    // An empty section goes without asking.
    confirm.mockClear()
    openSection("s_empty")
    fireEvent.click(screen.getByRole("button", { name: "Delete section" }))
    expect(confirm).not.toHaveBeenCalled()
    expect(sectionIds()).toEqual(["s_policy", "s_notes"])
    expect(screen.queryByTestId("workbench-properties")).not.toBeInTheDocument()
  })

  it("never deletes a sheet's only section: its Delete section is disabled", () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true)
    openSection("s_p1")
    render(<PropertiesPanel />)

    const remove = screen.getByRole("button", { name: "Delete section" })
    expect(remove).toBeDisabled()
    expect(remove).toHaveAttribute("title", "A sheet keeps at least one section")
    fireEvent.click(remove)
    expect(confirm).not.toHaveBeenCalled()
    expect(sectionIds()).toEqual(["s_p1"])
    expect(widgetsOf(currentForm())).toHaveLength(2)
    expect(useWorkbenchViewStore.getState().sectionId).toBe("s_p1")
  })

  it("shows another section's panel as it is selected, and closes as a component is", () => {
    loadForm(sectionedForm())
    openSection("s_flood")
    render(<PropertiesPanel />)
    expect(screen.getByRole("textbox", { name: "Section title" })).toHaveValue("Flood")

    openSection("s_notes")
    expect(screen.getByRole("textbox", { name: "Section title" })).toHaveValue("Notes")
    expect(screen.getByTestId("section-height")).toHaveValue("72")

    act(() => useWorkbenchViewStore.getState().select("w_grid"))
    expect(screen.queryByRole("textbox", { name: "Section title" })).not.toBeInTheDocument()
    showPane("Display")
    expect(screen.getByRole("textbox", { name: "Title" })).toHaveValue("Equipment")
    expect(useWorkbenchViewStore.getState()).toMatchObject({ selectedId: "w_grid", sectionId: null })
  })
})
