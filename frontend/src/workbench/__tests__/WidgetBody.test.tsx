/**
 * A component's cells (specs/workbench): the sample typed into a Collection's boxes and a
 * Table's grid, rows added and deleted across the tables a grid shows, a grid drawn as its
 * Table's options say and boxes as their Collection's look says, an output box showing what pricing the sample gave, dimmed once the
 * sample has moved on, a Table's output columns matched to its rows by key, the cells that
 * break their column's rules marked once anything is typed, while building a new quote's
 * cells, empty and inert, the underwriter app's quote typed apart from the sample, and a
 * Heading drawn as its text.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react"
import type { ReactNode } from "react"
import type { ShowWhen } from "../../api/types"
import useWorkbenchFormStore from "../../stores/useWorkbenchFormStore"
import useWorkbenchPreviewStore from "../../stores/useWorkbenchPreviewStore"
import useWorkbenchPricingStore, { previewBasis } from "../../stores/useWorkbenchPricingStore"
import useWorkbenchViewStore from "../../stores/useWorkbenchViewStore"
import { valuesBasis } from "../../utils/sheetValues"
import { addSchemaColumn, addSchemaTable, BLANK_THEME, createWidget, setColumnLook, toggleField, updateSchemaColumn, updateWidget, type WidgetPatch } from "../../utils/workbenchForm"
import SheetCanvas from "../SheetCanvas"
import { SheetValuesContext, useNewQuoteValues, useQuoteValues, useSampleValues } from "../useSheetValues"
import { everyField, spacersShown } from "../sheet/fieldCells"
import WidgetBody from "../WidgetBody"
import { addTo, column, coverForm, currentForm, headingWidget, hiddenPerilsForm, loadForm, releasePointer, sheetForm, stubLayout, table, widgetsOf } from "./fixtures"

const widget = (id: string) => {
  const found = widgetsOf(currentForm()).find((candidate) => candidate.id === id)
  if (found === undefined) throw new Error(`no ${id}`)
  return found
}

/** Whose values the sheet holds: a new quote's while building, the sample in Preview, or the underwriter app's quote. */
type Values = "new" | "sample" | "quote"

/** The sheet's values around a component. */
function OnSheet({ values, children }: { values: Values; children: ReactNode }) {
  const newQuote = useNewQuoteValues()
  const sample = useSampleValues()
  const quote = useQuoteValues()
  return <SheetValuesContext value={values === "new" ? newQuote : values === "quote" ? quote : sample}>{children}</SheetValuesContext>
}

const body = (id: string, values: Values = "sample") => (
  <OnSheet values={values}>
    <WidgetBody widget={widget(id)} drawn={everyField(currentForm().schema, widget(id))} />
  </OnSheet>
)

function renderBody(id: string, values: Values = "sample") {
  return render(body(id, values))
}

/** A right-click on a grid's cell, then each of `entries` in the menu it opens, clicked in turn. */
function fromRowMenu(cell: HTMLElement, ...entries: string[]) {
  fireEvent.contextMenu(cell)
  for (const entry of entries) fireEvent.click(screen.getByRole("menuitem", { name: entry }))
}

/** A right-click on a cell of a grid whose rows are fixed: the browser's own menu, not the grid's. */
function expectNoRowMenu(cell: HTMLElement) {
  expect(fireEvent.contextMenu(cell)).toBe(true)
  expect(screen.queryByRole("menu")).not.toBeInTheDocument()
}

describe("WidgetBody", () => {
  beforeEach(() => {
    stubLayout()
    loadForm()
    useWorkbenchPricingStore.setState({ price: null, error: null })
    useWorkbenchPreviewStore.setState({ quote: {}, price: null, error: null, pricing: false, readOnly: false })
  })

  afterEach(() => {
    releasePointer()
    cleanup()
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
  })

  it("types the sample into a Collection's boxes, through the control each column calls for, as one undo step each", () => {
    renderBody("w_boxes")

    // A column with allowed values is a dropdown; a number is typed and committed on blur.
    // The state is required, and says so to a screen reader.
    expect(screen.getByRole("combobox", { name: "State" })).toHaveAttribute("aria-required", "true")
    fireEvent.change(screen.getByRole("combobox", { name: "State" }), { target: { value: "NY" } })
    expect(currentForm().sample.policy).toEqual([{ "policy.state": "NY" }])
    expect(useWorkbenchFormStore.getState().undoStack).toHaveLength(1)

    loadForm(toggleField(sheetForm(), "w_boxes", { table: "policy", column: "policy.year" }))
    cleanup()
    renderBody("w_boxes")
    const year = screen.getByRole("textbox", { name: "Year" })
    expect(year).not.toHaveAttribute("aria-required")
    fireEvent.change(year, { target: { value: "2021" } })
    expect(currentForm().sample).toEqual({})
    fireEvent.blur(year)
    expect(currentForm().sample.policy).toEqual([{ "policy.year": "2021" }])
  })

  it("types the sample into a Table's grid row by row, keeping the rows it starts with, inserting and deleting rows in every table it shows", () => {
    renderBody("w_grid")
    expect(screen.getAllByRole("row")).toHaveLength(4)

    // The first value typed keeps the grid's three starting rows, as one undo step.
    const item = screen.getByRole("textbox", { name: "Item row 2" })
    fireEvent.change(item, { target: { value: "Crane" } })
    fireEvent.keyDown(item, { key: "Enter" })
    expect(currentForm().sample.equipment).toEqual([{}, { "equipment.item": "Crane" }, {}])
    expect(useWorkbenchFormStore.getState().undoStack).toHaveLength(1)

    fromRowMenu(screen.getByRole("textbox", { name: "Item row 3" }), "Insert", "Insert Row Below")
    expect(currentForm().sample.equipment).toEqual([{}, { "equipment.item": "Crane" }, {}, {}])
    expect(screen.getAllByRole("row")).toHaveLength(5)

    fireEvent.click(screen.getByRole("button", { name: "Delete row 1" }))
    expect(currentForm().sample.equipment).toEqual([{ "equipment.item": "Crane" }, {}, {}])
    expect(screen.getAllByRole("row")).toHaveLength(4)

    // Below the rows it starts with; deleting the last starts it again.
    fireEvent.click(screen.getByRole("button", { name: "Delete row 3" }))
    fireEvent.click(screen.getByRole("button", { name: "Delete row 2" }))
    expect(currentForm().sample.equipment).toEqual([{ "equipment.item": "Crane" }])
    expect(screen.getAllByRole("row")).toHaveLength(2)
    fireEvent.click(screen.getByRole("button", { name: "Delete row 1" }))
    expect(currentForm().sample.equipment).toEqual([])
    expect(screen.getAllByRole("row")).toHaveLength(4)
    cleanup()

    // A starting row deletes while nothing is typed.
    loadForm()
    renderBody("w_grid")
    fireEvent.click(screen.getByRole("button", { name: "Delete row 2" }))
    expect(currentForm().sample.equipment).toEqual([{}, {}])
    expect(screen.getAllByRole("row")).toHaveLength(3)
  })

  it("inserts rows above and below a row through its right-click menu, one or as many as typed, each as one undo step, and deletes it, in every table the grid shows", () => {
    let form = addSchemaTable(sheetForm(), table("hire", "hire", { rows: "many", columns: [column("hire.days", "days", { type: "int" })] }))
    form = toggleField(form, "w_grid", { table: "hire", column: "hire.days" })
    form.sample = { equipment: [{ "equipment.item": "Crane" }, { "equipment.item": "Lift" }], hire: [{ "hire.days": "1" }, { "hire.days": "2" }] }
    loadForm(form)
    renderBody("w_grid")
    const item = (row: number) => screen.getByRole("textbox", { name: `Item row ${row}` })
    const items = () => currentForm().sample.equipment.map((row) => row["equipment.item"] ?? "")
    const days = () => currentForm().sample.hire.map((row) => row["hire.days"] ?? "")
    const undoSteps = () => useWorkbenchFormStore.getState().undoStack.length

    // Its menu at the right-click, Insert opening a menu of its own.
    expect(fireEvent.contextMenu(item(2))).toBe(false)
    const menu = screen.getByRole("menu", { name: "Row 2" })
    expect(within(menu).getAllByRole("menuitem").map((entry) => entry.textContent)).toEqual(["Insert", "Delete Row"])
    expect(within(menu).getByRole("menuitem", { name: "Insert" })).toHaveFocus()
    fireEvent.click(within(menu).getByRole("menuitem", { name: "Insert" }))
    expect(within(menu).getByRole("menuitem", { name: "Insert" })).toHaveAttribute("aria-expanded", "true")
    expect(within(screen.getByRole("menu", { name: "Insert" })).getAllByRole("menuitem").map((entry) => entry.textContent)).toEqual([
      "Insert Row Above",
      "Insert Row Below",
      "Insert Multiple Rows Above",
      "Insert Multiple Rows Below",
    ])
    expect(screen.getByRole("menuitem", { name: "Insert Row Above" })).toHaveFocus()

    // One row, above or below, in each table, empty.
    fireEvent.click(screen.getByRole("menuitem", { name: "Insert Row Above" }))
    expect(screen.queryByRole("menu")).not.toBeInTheDocument()
    expect(items()).toEqual(["Crane", "", "Lift"])
    expect(days()).toEqual(["1", "", "2"])
    expect(undoSteps()).toBe(1)
    fromRowMenu(item(3), "Insert", "Insert Row Below")
    expect(items()).toEqual(["Crane", "", "Lift", ""])
    expect(days()).toEqual(["1", "", "2", ""])
    expect(undoSteps()).toBe(2)

    // As many as typed into the box it asks in, a whole number from 1.
    fromRowMenu(item(1), "Insert", "Insert Multiple Rows Above")
    expect(screen.queryByRole("menu")).not.toBeInTheDocument()
    const box = screen.getByRole("dialog", { name: "Insert Rows Above" })
    const count = within(box).getByRole("spinbutton", { name: "Rows" })
    const insert = within(box).getByRole("button", { name: "Insert" })
    expect(count).toHaveFocus()
    expect(insert).toBeDisabled()
    for (const refused of ["0", "1.5"]) {
      fireEvent.change(count, { target: { value: refused } })
      expect(insert).toBeDisabled()
    }
    fireEvent.change(count, { target: { value: "2" } })
    fireEvent.click(insert)
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument()
    expect(items()).toEqual(["", "", "Crane", "", "Lift", ""])
    expect(days()).toEqual(["", "", "1", "", "2", ""])
    expect(undoSteps()).toBe(3)
    fromRowMenu(item(5), "Insert", "Insert Multiple Rows Below")
    fireEvent.change(screen.getByRole("spinbutton", { name: "Rows" }), { target: { value: "3" } })
    fireEvent.click(screen.getByRole("button", { name: "Insert" }))
    expect(items()).toEqual(["", "", "Crane", "", "Lift", "", "", "", ""])
    expect(screen.getAllByRole("row")).toHaveLength(10)

    // Cancelled, the box inserts nothing.
    fromRowMenu(item(1), "Insert", "Insert Multiple Rows Below")
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }))
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument()
    expect(undoSteps()).toBe(4)

    // Delete Row takes the row out of each table.
    fromRowMenu(item(3), "Delete Row")
    expect(items()).toEqual(["", "", "", "Lift", "", "", "", ""])
    expect(days()).toEqual(["", "", "", "2", "", "", "", ""])
    expect(undoSteps()).toBe(5)
  })

  it("moves through a row's menu from the keyboard, and closes it on Escape, giving the focus back, or on a press outside it", () => {
    renderBody("w_grid")
    const cell = screen.getByRole("textbox", { name: "Item row 1" })
    const focused = () => document.activeElement as HTMLElement
    cell.focus()
    fireEvent.contextMenu(cell)
    expect(screen.getByRole("menuitem", { name: "Insert" })).toHaveFocus()
    fireEvent.keyDown(focused(), { key: "ArrowDown" })
    expect(screen.getByRole("menuitem", { name: "Delete Row" })).toHaveFocus()
    fireEvent.keyDown(focused(), { key: "ArrowDown" })
    expect(screen.getByRole("menuitem", { name: "Insert" })).toHaveFocus()

    // Right opens Insert's menu at its first entry, left closes it.
    fireEvent.keyDown(focused(), { key: "ArrowRight" })
    expect(screen.getByRole("menuitem", { name: "Insert Row Above" })).toHaveFocus()
    fireEvent.keyDown(focused(), { key: "ArrowUp" })
    expect(screen.getByRole("menuitem", { name: "Insert Multiple Rows Below" })).toHaveFocus()
    fireEvent.keyDown(focused(), { key: "ArrowLeft" })
    expect(screen.queryByRole("menu", { name: "Insert" })).not.toBeInTheDocument()
    expect(screen.getByRole("menuitem", { name: "Insert" })).toHaveFocus()

    fireEvent.keyDown(focused(), { key: "Escape" })
    expect(screen.queryByRole("menu")).not.toBeInTheDocument()
    expect(cell).toHaveFocus()

    fireEvent.contextMenu(cell)
    fireEvent.pointerDown(document.body)
    expect(screen.queryByRole("menu")).not.toBeInTheDocument()
    expect(currentForm().sample).toEqual({})
  })

  it("shows every row the values hold once they hold any, fewer than the grid starts with too, a grid of fixed rows never fewer than those", () => {
    const form = sheetForm()
    form.sample = { equipment: [{ "equipment.item": "Crane" }] }
    loadForm(form)
    renderBody("w_grid")
    expect(screen.getAllByRole("row")).toHaveLength(2)
    cleanup()

    loadForm(updateWidget(form, "w_grid", { fixed_rows: true }))
    renderBody("w_grid")
    expect(screen.getAllByRole("row")).toHaveLength(4)
    expect(screen.getByRole("textbox", { name: "Item row 1" })).toHaveValue("Crane")
  })

  it("shows the rows a column gives its table whatever the grid starts with, each value as text, and writes them all as a value is typed", () => {
    const given = updateSchemaColumn(sheetForm(), "equipment", "equipment.item", {
      options: ["Crane", "Digger", "Lift", "Hoist"],
      options_source: { file: "plant.csv", column: "item" },
      row_per_value: true,
    })
    // A value held for the lift, as a quote opened or a refresh may leave it.
    given.sample = { equipment: [{ "equipment.item": "Lift", "equipment.value": "3" }] }
    loadForm(updateWidget(given, "w_grid", { rows: 2 }))
    renderBody("w_grid")
    const bodyRows = () => screen.getAllByRole("row").slice(1)

    expect(bodyRows().map((row) => row.children[1].textContent)).toEqual(["Crane", "Digger", "Lift", "Hoist"])
    expect(screen.getByRole("textbox", { name: "Value row 3" })).toHaveValue("3")
    // Its values take no typing, and no row is inserted or deleted.
    expect(screen.queryByRole("textbox", { name: "Item row 1" })).not.toBeInTheDocument()
    expectNoRowMenu(screen.getByRole("textbox", { name: "Value row 1" }))
    expect(screen.queryByRole("button", { name: "Delete row 1" })).not.toBeInTheDocument()

    const value = screen.getByRole("textbox", { name: "Value row 1" })
    fireEvent.change(value, { target: { value: "5000" } })
    fireEvent.blur(value)
    expect(currentForm().sample.equipment).toEqual([
      { "equipment.item": "Crane", "equipment.value": "5000" },
      { "equipment.item": "Digger" },
      { "equipment.item": "Lift", "equipment.value": "3" },
      { "equipment.item": "Hoist" },
    ])
    expect(useWorkbenchFormStore.getState().undoStack).toHaveLength(1)
    cleanup()

    // The underwriter app's quote the same, a new one starting with the rows alone.
    renderBody("w_grid", "quote")
    expect(bodyRows().map((row) => row.children[1].textContent)).toEqual(["Crane", "Digger", "Lift", "Hoist"])
    const quoted = screen.getByRole("textbox", { name: "Value row 2" })
    fireEvent.change(quoted, { target: { value: "70" } })
    fireEvent.blur(quoted)
    expect(useWorkbenchPreviewStore.getState().quote.equipment).toEqual([
      { "equipment.item": "Crane" },
      { "equipment.item": "Digger", "equipment.value": "70" },
      { "equipment.item": "Lift" },
      { "equipment.item": "Hoist" },
    ])
  })

  it("shows every row of a table whose rows differ beside the rows a column gives, a table's cells past its own rows taking no typing", () => {
    let given = updateSchemaColumn(sheetForm(), "equipment", "equipment.item", {
      options: ["Crane", "Lift"],
      options_source: { file: "plant.csv", column: "item" },
      row_per_value: true,
    })
    given = addSchemaTable(given, table("hire", "hire", { rows: "many", columns: [column("hire.item", "item", { key: true }), column("hire.days", "days", { type: "int" })] }))
    given = toggleField(given, "w_grid", { table: "hire", column: "hire.days" })
    // The equipment's index, numbering its own rows alone.
    given = addSchemaColumn(given, "equipment", { ...column("equipment.row", "row_number", { type: "int" }), index: true }, 0)
    given = toggleField(given, "w_grid", { table: "equipment", column: "equipment.row" })
    given.sample = { hire: [{ "hire.days": "1" }, { "hire.days": "2" }, { "hire.days": "3" }] }
    loadForm(given)
    renderBody("w_grid")

    const bodyRows = screen.getAllByRole("row").slice(1)
    expect(bodyRows.map((row) => row.children[1].textContent)).toEqual(["Crane", "Lift", ""])
    expect(bodyRows.map((row) => row.children[4].textContent)).toEqual(["1", "2", ""])
    expect(screen.getByRole("textbox", { name: "Value row 2" })).toBeInTheDocument()
    expect(screen.queryByRole("textbox", { name: "Value row 3" })).not.toBeInTheDocument()
    expect(screen.getByRole("textbox", { name: "Days row 3" })).toHaveValue("3")
  })

  it("draws a Table as its options say, and with its rows fixed shows every row typed but no row menu or delete", () => {
    const bodyRows = () => screen.getAllByRole("row").slice(1)
    const cellOf = (name: string) => screen.getByRole("textbox", { name }).closest("td")

    // By default: numbered, compact rows lined between them, every other one shaded, each
    // with a delete at its end.
    renderBody("w_grid")
    expect(screen.getAllByRole("columnheader")).toHaveLength(4)
    expect(screen.getAllByRole("columnheader")[0]).toHaveTextContent("#")
    // The number's and the fields' headers each head their column.
    expect(screen.getAllByRole("columnheader").slice(0, 3).map((header) => header.getAttribute("scope"))).toEqual(["col", "col", "col"])
    expect(bodyRows()[0].children).toHaveLength(4)
    expect(bodyRows()[0].children[0]).toHaveTextContent("1")
    expect(bodyRows()[0]).toHaveClass("even:bg-[var(--chip-rest)]")
    expect(bodyRows()[0].style.borderBottom).toBe("1px solid var(--border-subtle)")
    expect(screen.getByRole("textbox", { name: "Item row 1" })).toHaveClass("h-7")
    expect(cellOf("Value row 1")?.style.borderLeft).toBe("")
    cleanup()

    const form = updateWidget(sheetForm(), "w_grid", { row_numbers: false, density: "comfortable", gridlines: "all", striped: false, fixed_rows: true })
    form.sample = { equipment: [{ "equipment.item": "A" }, {}, {}, { "equipment.item": "D" }] }
    loadForm(form)
    renderBody("w_grid")
    expect(screen.getAllByRole("columnheader").map((header) => header.textContent)).toEqual(["Item", "Value"])
    expect(bodyRows()[0].children).toHaveLength(2)
    expect(bodyRows()[0]).not.toHaveClass("even:bg-[var(--chip-rest)]")
    expect(screen.getByRole("textbox", { name: "Item row 1" })).toHaveClass("h-9")
    // A line between the columns as well as the rows, but none at the grid's left edge.
    expect(cellOf("Value row 1")?.style.borderLeft).toBe("1px solid var(--border-subtle)")
    expect(cellOf("Item row 1")?.style.borderLeft).toBe("")
    // Every row typed shows, one more than the grid's 3, though none can be inserted or deleted.
    expect(bodyRows()).toHaveLength(4)
    expect(screen.getByRole("textbox", { name: "Item row 4" })).toHaveValue("D")
    expectNoRowMenu(screen.getByRole("textbox", { name: "Item row 1" }))
    expect(screen.queryByRole("button", { name: "Delete row 1" })).not.toBeInTheDocument()
    cleanup()

    // No gridlines: no line between the rows, nor before an output column.
    loadForm(updateWidget(toggleField(sheetForm(), "w_grid", { table: "premiums", column: "premiums.premium" }), "w_grid", { gridlines: "none" }))
    renderBody("w_grid")
    expect(bodyRows()[0].style.borderBottom).toBe("")
    expect(screen.getByTestId("priced-premiums:premiums.premium-1").style.borderLeft).toBe("")
  })

  it("draws a Table's header as written by default, in capitals as its options say, and a Table without its frame", () => {
    // By default its labels as written, at the cells' size, in its frame.
    const framed = renderBody("w_grid").container.firstElementChild as HTMLElement
    const header = () => screen.getAllByRole("row")[0]
    expect(header()).not.toHaveClass("uppercase")
    expect(header().style.fontSize).toBe("12px")
    expect(framed.style.border).toBe("1px solid var(--border)")
    cleanup()

    loadForm(updateWidget(sheetForm(), "w_grid", { header: "caps", frame: false }))
    const { container } = renderBody("w_grid")
    // The labels in capitals.
    expect(header()).toHaveClass("uppercase")
    expect(header().style.fontSize).toBe("11px")
    // No border or background of its own: the grid sits on the sheet.
    const frame = container.firstElementChild as HTMLElement
    expect(frame.style.border).toBe("")
    expect(frame.style.background).toBe("")
  })

  it("draws a Table transposed, its fields down its side and its rows across, typed, inserted and deleted as its rows are", () => {
    const form = updateWidget(toggleField(sheetForm(), "w_grid", { table: "premiums", column: "premiums.premium" }), "w_grid", { transposed: true, header: "caps" })
    loadForm(form)
    renderBody("w_grid")
    const lines = () => screen.getAllByRole("row")
    const cellOf = (name: string) => screen.getByRole("textbox", { name }).closest("td")

    // The rows' numbers across the top; a line per field under them, labelled at its start
    // in the header's look, holding the field's cell in each row.
    expect(lines()).toHaveLength(4)
    expect(within(lines()[0]).getAllByRole("columnheader").map((header) => header.textContent)).toEqual(["#", "1", "2", "3"])
    expect(screen.getAllByRole("rowheader").map((header) => header.textContent)).toEqual(["Item", "Value", "Premium"])
    expect(screen.getAllByRole("rowheader")[0]).toHaveClass("uppercase")
    expect(within(lines()[1]).getAllByRole("textbox").map((box) => box.getAttribute("aria-label"))).toEqual(["Item row 1", "Item row 2", "Item row 3"])
    // Shaded every other line and lined between them as drawn, an output field ruled off above its line.
    expect(lines()[1]).toHaveClass("even:bg-[var(--chip-rest)]")
    expect(lines()[1].style.borderBottom).toBe("1px solid var(--border-subtle)")
    expect(screen.getByTestId("priced-premiums:premiums.premium-1").style.borderTop).toBe("1px solid var(--border)")
    expect(cellOf("Value row 1")?.style.borderTop).toBe("")

    // Typed, inserted and deleted as its rows are, the menu and the deletes saying what they do as drawn.
    const item = screen.getByRole("textbox", { name: "Item row 2" })
    fireEvent.change(item, { target: { value: "Crane" } })
    fireEvent.keyDown(item, { key: "Enter" })
    expect(currentForm().sample.equipment).toEqual([{}, { "equipment.item": "Crane" }, {}])
    // The menu opens on a row's number as on its cells.
    fromRowMenu(within(lines()[0]).getAllByRole("columnheader")[3], "Insert")
    expect(within(screen.getByRole("menu", { name: "Column 3" })).getAllByRole("menuitem").map((entry) => entry.textContent)).toEqual([
      "Insert",
      "Insert Column Left",
      "Insert Column Right",
      "Insert Multiple Columns Left",
      "Insert Multiple Columns Right",
      "Delete Column",
    ])
    fireEvent.click(screen.getByRole("menuitem", { name: "Insert Column Right" }))
    expect(currentForm().sample.equipment).toEqual([{}, { "equipment.item": "Crane" }, {}, {}])
    expect(within(lines()[0]).getAllByRole("columnheader")).toHaveLength(5)
    fromRowMenu(screen.getByTestId("priced-premiums:premiums.premium-1"), "Insert", "Insert Multiple Columns Left")
    expect(screen.getByRole("dialog", { name: "Insert Columns Left" })).toBeInTheDocument()
    fireEvent.change(screen.getByRole("spinbutton", { name: "Columns" }), { target: { value: "2" } })
    fireEvent.click(screen.getByRole("button", { name: "Insert" }))
    expect(currentForm().sample.equipment).toEqual([{}, {}, {}, { "equipment.item": "Crane" }, {}, {}])
    fromRowMenu(screen.getByRole("textbox", { name: "Item row 2" }), "Delete Column")
    fromRowMenu(screen.getByRole("textbox", { name: "Item row 1" }), "Delete Column")
    expect(currentForm().sample.equipment).toEqual([{}, { "equipment.item": "Crane" }, {}, {}])
    expect(screen.getByRole("button", { name: "Delete row 1" })).toHaveAttribute("title", "Delete column")
    fireEvent.click(screen.getByRole("button", { name: "Delete row 1" }))
    expect(currentForm().sample.equipment).toEqual([{ "equipment.item": "Crane" }, {}, {}])
    cleanup()

    // Fixed and unnumbered, nothing goes above the fields; a full grid lines the rows' columns, the first ruled off by the labels alone.
    loadForm(updateWidget(form, "w_grid", { fixed_rows: true, row_numbers: false, gridlines: "all" }))
    renderBody("w_grid")
    expect(screen.queryAllByRole("columnheader")).toHaveLength(0)
    expectNoRowMenu(screen.getByRole("textbox", { name: "Item row 1" }))
    expect(screen.queryByRole("button", { name: "Delete row 1" })).not.toBeInTheDocument()
    expect(cellOf("Item row 1")?.style.borderLeft).toBe("")
    expect(cellOf("Item row 2")?.style.borderLeft).toBe("1px solid var(--border-subtle)")
  })

  it("heads a Table's fields side by side in a schema group with one cell naming it, over their labels or beside them transposed", () => {
    let form = toggleField(sheetForm(), "w_grid", { table: "premiums", column: "premiums.premium" })
    form = updateSchemaColumn(form, "equipment", "equipment.item", { group: "Asset" })
    form = updateSchemaColumn(form, "equipment", "equipment.value", { group: "Asset" })
    form = updateSchemaColumn(form, "premiums", "premiums.premium", { group: "Price" })
    const headerRows = () => screen.getAllByRole("row").filter((row) => row.closest("thead") !== null)
    const spans = (row: HTMLElement) => [...row.children].map((cell) => [cell.textContent, (cell as HTMLTableCellElement).colSpan])

    // Without groups shown, by default, the labels alone head the grid.
    loadForm(form)
    renderBody("w_grid")
    expect(headerRows()).toHaveLength(1)
    cleanup()

    // Shown: a row over the labels, a cell spanning each run of fields in a group and ruled
    // under its name, blank over the numbers and the deletes, an output's run ruled before it.
    const grouped = updateWidget(form, "w_grid", { groups: true })
    loadForm(grouped)
    renderBody("w_grid")
    expect(headerRows()).toHaveLength(2)
    expect(spans(headerRows()[0])).toEqual([["", 1], ["Asset", 2], ["Price", 1], ["", 1]])
    const asset = headerRows()[0].children[1] as HTMLElement
    expect((asset.firstElementChild as HTMLElement).style.borderBottom).toBe("1px solid var(--border-bright)")
    expect((headerRows()[0].children[2] as HTMLElement).style.borderLeft).toBe("1px solid var(--border)")
    cleanup()

    // A field in no group has a blank cell of its own; with no field in a group, no row.
    loadForm(updateSchemaColumn(grouped, "equipment", "equipment.item", { group: "" }))
    renderBody("w_grid")
    expect(spans(headerRows()[0])).toEqual([["", 1], ["", 1], ["Asset", 1], ["Price", 1], ["", 1]])
    cleanup()
    loadForm(updateWidget(sheetForm(), "w_grid", { groups: true }))
    renderBody("w_grid")
    expect(headerRows()).toHaveLength(1)
    cleanup()

    // Transposed, a cell beside each run's labels spans their lines, the corner over both, the
    // labels kept in view beside the groups, over their column's last pixel.
    loadForm(updateWidget(grouped, "w_grid", { transposed: true }))
    renderBody("w_grid")
    const headers = screen.getAllByRole("rowheader") as HTMLTableCellElement[]
    expect(headers.map((header) => [header.textContent, header.rowSpan])).toEqual([
      ["Asset", 2],
      ["Item", 1],
      ["Value", 1],
      ["Price", 1],
      ["Premium", 1],
    ])
    expect((screen.getAllByRole("columnheader")[0] as HTMLTableCellElement).colSpan).toBe(2)
    expect(headers[1].style.left).toBe("95px")
  })

  it("puts a Collection's tick box in a box as its other fields are, in the input cells' fill", () => {
    const form = coverForm()
    loadForm(form)
    renderBody("w_boxes")
    const tick = screen.getByRole("checkbox", { name: "Cover" })
    const box = tick.parentElement as HTMLElement
    const state = screen.getByRole("combobox", { name: "State" })
    // The same fill, border and height as the box beside it.
    expect(box.style.background).toBe(state.style.background)
    expect(box.style.border).toBe(state.style.border)
    expect(box).toHaveClass("h-9", "rounded-md")
    cleanup()

    loadForm({ ...form, theme: { ...BLANK_THEME, input_cell_color: "#DC2626" } })
    renderBody("w_boxes")
    expect(screen.getByRole("checkbox", { name: "Cover" }).parentElement).toHaveStyle({ background: "#DC2626" })
  })


  it("draws a Collection as its options say: its labels beside its boxes, compact boxes and no frame", () => {
    loadForm(toggleField(sheetForm(), "w_boxes", { table: "premiums", column: "premiums.premium" }))
    const box = () => screen.getByRole("combobox", { name: "State" })
    const field = () => box().closest("[title]") as HTMLElement
    const { container } = renderBody("w_boxes")

    // By default each label above its box, comfortable boxes, in its frame.
    expect(field()).toHaveClass("flex-col")
    expect(box()).toHaveClass("h-9")
    expect(screen.getByTestId("priced-premiums:premiums.premium")).toHaveClass("h-9")
    expect((container.firstElementChild as HTMLElement).style.border).toBe("1px solid var(--border)")
    cleanup()

    loadForm(updateWidget(currentForm(), "w_boxes", { labels: "beside", density: "compact", frame: false }))
    const framed = renderBody("w_boxes").container.firstElementChild as HTMLElement
    // Each label beside its box, on one row with it.
    expect(field()).not.toHaveClass("flex-col")
    expect(field()).toHaveClass("items-center")
    expect(field().firstElementChild).toHaveTextContent("State")
    // Compact boxes, an output's too, and closer together.
    expect(box()).toHaveClass("h-7")
    expect(screen.getByTestId("priced-premiums:premiums.premium")).toHaveClass("h-7")
    expect(field().parentElement).toHaveClass("gap-y-2")
    expect(framed.style.border).toBe("")
  })

  it("draws a Collection's own look over the sheets' colours, its text, its labels, its boxes and its frame, and a field's own look over the Collection's", () => {
    const state = { table: "policy", column: "policy.state" }
    const premium = { table: "premiums", column: "premiums.premium" }
    let form = toggleField(sheetForm(), "w_boxes", premium)
    form.theme = { ...BLANK_THEME, border_color: "#D97706", font_color: "#2563EB", input_cell_color: "#DC2626" }
    form = updateWidget(form, "w_boxes", {
      labels: "beside",
      font_size: 14,
      header: "caps",
      header_font_size: 16,
      header_bold: true,
      header_wrap: true,
      header_color: "#1E293B",
      header_font_color: "#FFFFFF",
      header_align: "left",
      label_width: 60,
      box_style: "dashed",
      box_weight: "thick",
      box_color: "#000000",
      box_align: "center",
      frame_color: "#9333EA",
      frame_weight: "medium",
    })
    loadForm(form)
    const { container } = renderBody("w_boxes")
    const label = () => screen.getByTestId("label-policy:policy.state")
    const box = () => screen.getByRole("combobox", { name: "State" })
    const output = () => screen.getByTestId("priced-premiums:premiums.premium")

    // Its frame in its own colour and weight, the rule under its title in its colour.
    const root = container.firstElementChild as HTMLElement
    expect(root.style.border).toBe("2px solid rgb(147, 51, 234)")
    expect(screen.getByText("Policy").style.borderBottom).toBe("1px solid rgb(147, 51, 234)")
    // Its labels filled, drawn as a cell as tall as the box beside it, in its font, its width,
    // capitals, size and weight, wrapping rather than cut off; never so wide that its box is
    // narrower than a box's narrowest width, 24 pixels, after the 8 between them.
    expect(label()).toHaveStyle({ background: "#1E293B", color: "#FFFFFF", width: "60%", maxWidth: "calc(100% - 32px)" })
    expect(label()).toHaveClass("rounded-md", "px-2.5", "self-stretch")
    const labelText = label().firstElementChild as HTMLElement
    expect(labelText).toHaveClass("uppercase", "break-words")
    expect(labelText).not.toHaveClass("truncate")
    expect(labelText).toHaveStyle({ fontSize: "16px", fontWeight: "600", textAlign: "left" })
    // Its boxes, input and output alike, bordered in its own style, weight and colour over the
    // theme's, their values at its size and centred apart from their labels; an input box in
    // the input cells' fill and the font.
    expect(box().style.border).toBe("3px dashed rgb(0, 0, 0)")
    expect(output().style.border).toBe("3px dashed rgb(0, 0, 0)")
    expect(box()).toHaveStyle({ fontSize: "14px", background: "#DC2626", color: "#2563EB", textAlign: "center" })
    expect(output()).toHaveStyle({ fontSize: "14px", justifyContent: "center" })
    cleanup()

    // A field's own look over the Collection's: its label's fill, font and place, its box's
    // fill, over the input and output fills, its font, weight and place; a field without a
    // place of its own keeps the Collection's.
    form = setColumnLook(form, "w_boxes", state, {
      header_color: "#DC2626",
      header_font_color: "#000000",
      header_align: "center",
      cell_color: "#D1D5DB",
      font_color: "#059669",
      bold: true,
      align: "right",
    })
    loadForm(setColumnLook(form, "w_boxes", premium, { cell_color: "#FFFFFF", font_color: "#000000", align: "left" }))
    renderBody("w_boxes")
    expect(label()).toHaveStyle({ background: "#DC2626", color: "#000000" })
    expect(label().firstElementChild).toHaveStyle({ textAlign: "center" })
    expect(box()).toHaveStyle({ background: "#D1D5DB", color: "#059669", fontWeight: "600", textAlign: "right" })
    expect(screen.getByTestId("label-premiums:premiums.premium").firstElementChild).toHaveStyle({ textAlign: "left" })
    expect(output()).toHaveStyle({ background: "#FFFFFF", color: "#000000", justifyContent: "flex-start" })
    cleanup()

    // Unfilled, as by default, a label is its text alone, cut off, as written and not bold.
    loadForm(sheetForm())
    renderBody("w_boxes")
    expect(label()).not.toHaveClass("rounded-md")
    expect(label().style.background).toBe("")
    expect(label().firstElementChild).toHaveClass("truncate")
    expect(label().firstElementChild).toHaveStyle({ fontSize: "12px", fontWeight: "400" })
  })

  it("sets a Collection's labels and boxes to their widths: a box of a set width leaves the rest to its label, and sits under its label as its field aligns", () => {
    const state = { table: "policy", column: "policy.state" }
    const premium = { table: "premiums", column: "premiums.premium" }
    const base = updateWidget(toggleField(sheetForm(), "w_boxes", premium), "w_boxes", { labels: "beside" })
    const label = () => screen.getByTestId("label-policy:policy.state")
    // The box an input's control sits in, and an output's box.
    const box = () => screen.getByRole("combobox", { name: "State" }).parentElement as HTMLElement
    const output = () => screen.getByTestId("priced-premiums:premiums.premium")
    const draw = (patch: WidgetPatch, form = base) => {
      cleanup()
      loadForm(updateWidget(form, "w_boxes", patch))
      renderBody("w_boxes")
    }

    // Neither set: two fifths of the field for its label, the rest for its box.
    draw({})
    expect(label().style.width).toBe("40%")
    expect(box().style.width).toBe("")
    expect(box().style.flexGrow).toBe("1")
    // A box of a set width, an output's too, leaves the rest to its label.
    draw({ box_width: 96 })
    expect(box()).toHaveStyle({ width: "96px", maxWidth: "100%" })
    expect(output()).toHaveStyle({ width: "96px" })
    expect(label().style.width).toBe("")
    expect(label().style.flexGrow).toBe("1")
    // A label of a set share of its field keeps it; a field's own width wins over the Collection's.
    draw({ box_width: 96, label_width: 30 }, setColumnLook(base, "w_boxes", state, { width: 64 }))
    expect(label().style.width).toBe("30%")
    expect(box().style.width).toBe("64px")
    expect(output().style.width).toBe("96px")

    // Above its box, a narrower box sits at the field's start, else as its field aligns.
    draw({ labels: "above", box_width: 96 })
    expect(box().style.marginLeft).toBe("")
    draw({ labels: "above", box_width: 96 }, setColumnLook(base, "w_boxes", state, { align: "right" }))
    expect(box().style.marginLeft).toBe("auto")
    expect(box().style.marginRight).toBe("")
    draw({ labels: "above", box_width: 96 }, setColumnLook(base, "w_boxes", state, { align: "center" }))
    expect(box()).toHaveStyle({ marginLeft: "auto", marginRight: "auto" })
  })

  it("places a Collection's tick box across its box as its field aligns", () => {
    const cover = { table: "policy", column: "policy.cover" }
    loadForm(setColumnLook(updateWidget(coverForm(), "w_boxes", { box_width: 48 }), "w_boxes", cover, { align: "center" }))
    renderBody("w_boxes")
    const tick = screen.getByRole("checkbox", { name: "Cover" })
    expect(tick.parentElement).toHaveStyle({ justifyContent: "center" })
    expect(tick.parentElement?.parentElement).toHaveStyle({ width: "48px" })
  })

  it("leaves a gap across a Collection's whole width where each of its spacers sits, the fields after it starting a new row", () => {
    // State, cover and year, two across, a spacer after State and two after the last field.
    const fields = [
      { table: "policy", column: "policy.state" },
      { table: "policy", column: "policy.cover" },
      { table: "policy", column: "policy.year" },
    ]
    loadForm(updateWidget(coverForm(), "w_boxes", { fields, spacers: [1, 3, 3] }))
    renderBody("w_boxes")
    const grid = screen.getByRole("combobox", { name: "State" }).closest("[title]")!.parentElement!
    const drawn = () => [...grid.children].map((child) => (child.getAttribute("data-testid") === "collection-spacer" ? "gap" : child.getAttribute("title")))

    expect(drawn()).toEqual(["policy.state", "gap", "policy.cover", "policy.year", "gap", "gap"])
    for (const gap of screen.getAllByTestId("collection-spacer")) {
      // Across every column, as tall as the gaps between the boxes, and nothing to a screen reader.
      expect(gap.style.gridColumn).toBe("1 / -1")
      expect(gap).toHaveClass("h-3")
      expect(gap).toHaveAttribute("aria-hidden", "true")
      expect(gap).toBeEmptyDOMElement()
    }
    cleanup()

    // Compact, closer.
    loadForm(updateWidget(currentForm(), "w_boxes", { density: "compact" }))
    renderBody("w_boxes")
    expect(screen.getAllByTestId("collection-spacer")[0]).toHaveClass("h-2")
  })

  it("closes a Collection up in Preview around a field that hides, a spacer going with the fields after it, and fades the field while building, its eye naming its conditions", () => {
    const year = { table: "policy", column: "policy.year" }
    const covered: ShowWhen = { match: "all", conditions: [{ table: "policy", column: "policy.cover", op: "ticked", values: [] }] }
    const base = coverForm()
    const fields = [{ table: "policy", column: "policy.state" }, { table: "policy", column: "policy.cover" }, year]
    loadForm(setColumnLook(updateWidget(base, "w_boxes", { fields, spacers: [2] }), "w_boxes", year, { show_when: covered }))
    render(<SheetCanvas />)
    const boxes = () => screen.getByTestId("sheet-widget-w_boxes")

    // While building a new quote hides it: faded, its eye struck through, its spacer kept.
    const eye = within(boxes()).getByRole("img", { name: "Hidden for a new quote. Shows when Cover is ticked" })
    expect(eye.closest("[data-hidden]")).toBe(within(boxes()).getByRole("textbox", { name: "Year" }).closest("[data-hidden]"))
    expect(within(boxes()).getAllByTestId("collection-spacer")).toHaveLength(1)

    // In Preview the sample decides: neither the year nor its spacer is drawn until cover is ticked.
    act(() => useWorkbenchViewStore.setState({ mode: "preview" }))
    expect(within(boxes()).queryByRole("textbox", { name: "Year" })).not.toBeInTheDocument()
    expect(within(boxes()).queryAllByTestId("collection-spacer")).toHaveLength(0)
    expect(within(boxes()).queryByRole("img", { name: /Shows when/ })).not.toBeInTheDocument()
    fireEvent.click(within(boxes()).getByRole("checkbox", { name: "Cover" }))
    expect(within(boxes()).getByRole("textbox", { name: "Year" })).toBeInTheDocument()
    expect(within(boxes()).getAllByTestId("collection-spacer")).toHaveLength(1)
  })

  it("leaves a Table's column that hides out of its grid in Preview, transposed its line, its rows still moving with every table it shows", () => {
    const value = { table: "equipment", column: "equipment.value" }
    const note = { table: "extras", column: "extras.note" }
    const covered: ShowWhen = { match: "all", conditions: [{ table: "policy", column: "policy.cover", op: "ticked", values: [] }] }
    const base = coverForm()
    base.schema.tables.push(table("extras", "extras", { rows: "many", columns: [column("extras.item", "item", { key: true }), column("extras.note", "note")] }))
    let form = updateWidget(base, "w_grid", { fields: [{ table: "equipment", column: "equipment.item" }, value, note] })
    form = setColumnLook(setColumnLook(form, "w_grid", value, { show_when: covered }), "w_grid", note, { show_when: covered })
    loadForm({ ...form, sample: { equipment: [{ "equipment.item": "Crane" }, { "equipment.item": "Lift" }], extras: [{ "extras.note": "first" }, { "extras.note": "second" }] } })
    render(<SheetCanvas />)
    const grid = () => screen.getByTestId("sheet-widget-w_grid")

    // While building, faded, its eye in its header.
    expect(within(grid()).getByRole("columnheader", { name: /^Value/ })).toHaveAttribute("data-hidden", "true")
    expect(within(grid()).getAllByRole("img", { name: "Hidden for a new quote. Shows when Cover is ticked" })).toHaveLength(2)

    act(() => useWorkbenchViewStore.setState({ mode: "preview" }))
    expect(within(grid()).getAllByRole("columnheader").map((header) => header.textContent)).toEqual(["#", "Item", ""])
    // A row deleted goes from every table the grid shows, the one whose columns all hide too.
    fireEvent.click(within(grid()).getByRole("button", { name: "Delete row 1" }))
    expect(currentForm().sample.equipment).toEqual([{ "equipment.item": "Lift" }])
    expect(currentForm().sample.extras).toEqual([{ "extras.note": "second" }])

    // Transposed, the field's line goes.
    act(() => useWorkbenchFormStore.getState().change((spec) => updateWidget(spec, "w_grid", { transposed: true })))
    expect(within(grid()).getAllByRole("rowheader").map((header) => header.textContent)).toEqual(["Item"])
  })

  it("draws the components in the sheets' colours: the font, the borders, the backgrounds and the input and output cells' fills", () => {
    let form = toggleField(sheetForm(), "w_grid", { table: "premiums", column: "premiums.premium" })
    form = toggleField(form, "w_boxes", { table: "premiums", column: "premiums.premium" })
    addTo(form, headingWidget("w_head", "Equipment schedule", { description: "By item" }))
    form.sample = { equipment: [{ "equipment.item": "Crane" }] }
    form.theme = { ...BLANK_THEME, background_color: "#059669", border_color: "#D97706", font_color: "#2563EB", input_cell_color: "#DC2626", output_cell_color: "#9333EA" }
    loadForm(form)
    useWorkbenchPricingStore.setState({
      price: { basis: previewBasis(form), sample: { equipment: [{ item: "Crane" }] }, tables: { premiums: [{ item: "Crane", premium: 10 }] } },
    })
    // The borders' #D97706 as the page holds it in a style.
    const border = "1px solid rgb(217, 119, 6)"
    const { container } = renderBody("w_grid")

    // Its frame in the borders' colour round the components' background; the font on its title, labels and values.
    const root = container.firstElementChild as HTMLElement
    expect(root).toHaveStyle({ background: "#059669" })
    expect(root.style.border).toBe(border)
    expect(screen.getByText("Equipment")).toHaveStyle({ color: "#2563EB" })
    expect(screen.getByText("Equipment").style.borderBottom).toBe(border)
    const header = screen.getAllByRole("columnheader")[1]
    expect(header).toHaveStyle({ color: "#2563EB" })
    // Ruled under by its shadow, which stays with it as the rows scroll under.
    expect(header.style.boxShadow).toBe("inset 0 -1px 0 #D97706")
    const item = screen.getByRole("textbox", { name: "Item row 1" })
    expect(item).toHaveStyle({ color: "#2563EB" })
    expect(item.closest("tr")?.style.borderBottom).toBe(border)
    // The input cells' fill on its input cells, the output cells' on its output cells, its value in the font.
    expect(item.closest("td")).toHaveStyle({ background: "#DC2626" })
    const premium = screen.getByTestId("priced-premiums:premiums.premium-1")
    expect(premium).toHaveStyle({ background: "#9333EA", color: "#2563EB" })
    expect(premium.style.borderLeft).toBe(border)
    cleanup()

    // A Collection's input boxes take the input cells' fill, the borders and the font, its output box the output cells'.
    renderBody("w_boxes")
    const state = screen.getByRole("combobox", { name: "State" })
    expect(state).toHaveStyle({ background: "#DC2626", color: "#2563EB" })
    expect(state.style.border).toBe(border)
    expect(state.closest("[title]")?.firstElementChild).toHaveStyle({ color: "#2563EB" })
    const output = screen.getByTestId("priced-premiums:premiums.premium")
    expect(output).toHaveStyle({ background: "#9333EA" })
    expect(output.style.border).toBe(border)
    cleanup()

    // A Heading in the font, its description too.
    renderBody("w_head")
    expect(screen.getByRole("heading", { name: "Equipment schedule" })).toHaveStyle({ color: "#2563EB" })
    expect(screen.getByText("By item")).toHaveStyle({ color: "#2563EB" })
    cleanup()

    // A Table's header strip in the components' background.
    renderBody("w_grid")
    expect(screen.getAllByRole("rowgroup")[0]).toHaveStyle({ background: "#059669" })
    cleanup()
    // Unframed, in the sheet's background.
    loadForm(updateWidget({ ...form, theme: { ...form.theme, sheet_color: "#1E293B" } }, "w_grid", { frame: false }))
    renderBody("w_grid")
    expect(screen.getAllByRole("rowgroup")[0]).toHaveStyle({ background: "#1E293B" })
    cleanup()

    // Unframed, it sits on the sheet; Automatic, each is the editor's own.
    loadForm(updateWidget({ ...form, theme: BLANK_THEME }, "w_grid", { frame: false }))
    const unframed = renderBody("w_grid").container.firstElementChild as HTMLElement
    expect(unframed.style.border).toBe("")
    expect(unframed.style.background).toBe("")
    expect(screen.getByTestId("priced-premiums:premiums.premium-1").style.background).toBe("var(--workbench-output-cell-bg)")
    expect(screen.getAllByRole("columnheader")[1].style.boxShadow).toBe("inset 0 -1px 0 var(--border-bright)")
    expect(screen.getAllByRole("rowgroup")[0].style.background).toBe("var(--bg-elevated)")
  })

  it("draws a Table's own look over the sheets' colours, its lines, its header, its text and its frame, and a column's own look over the Table's", () => {
    const value = { table: "equipment", column: "equipment.value" }
    let form = toggleField(sheetForm(), "w_grid", { table: "premiums", column: "premiums.premium" })
    form.sample = { equipment: [{ "equipment.item": "Crane", "equipment.value": "5" }] }
    form.theme = { ...BLANK_THEME, border_color: "#D97706", font_color: "#2563EB", input_cell_color: "#DC2626" }
    form = updateWidget(form, "w_grid", {
      gridlines: "all",
      grid_color: "#000000",
      grid_weight: "thick",
      grid_style: "dashed",
      frame_color: "#9333EA",
      frame_weight: "medium",
      font_size: 14,
      header_font_size: 16,
      header_bold: false,
      header_color: "#1E293B",
      header_font_color: "#FFFFFF",
    })
    form = setColumnLook(form, "w_grid", value, {
      header_color: "#DC2626",
      header_font_color: "#000000",
      cell_color: "#D1D5DB",
      font_color: "#059669",
      bold: true,
      align: "center",
      width: 120,
    })
    loadForm(form)
    const { container } = renderBody("w_grid")

    // Its frame in its own colour and weight, the rule under its title in its colour.
    const root = container.firstElementChild as HTMLElement
    expect(root.style.border).toBe("2px solid rgb(147, 51, 234)")
    expect(screen.getByText("Equipment").style.borderBottom).toBe("1px solid rgb(147, 51, 234)")
    // Its header strip in its fill, its labels in its font, at its size and weight, ruled
    // under in its lines' colour and weight, solid whatever the gridlines' style.
    expect(screen.getAllByRole("rowgroup")[0]).toHaveStyle({ background: "#1E293B" })
    expect(screen.getAllByRole("row")[0]).toHaveStyle({ fontSize: "16px" })
    const [, item, valueLabel, premium] = screen.getAllByRole("columnheader")
    // Each label cell its own weight, as a header cell's own bold would win over its row's.
    for (const label of [item, valueLabel, premium]) expect(label.style.fontWeight).toBe("400")
    expect(item).toHaveStyle({ color: "#FFFFFF" })
    expect(item.style.boxShadow).toBe("inset 0 -3px 0 #000000")
    // A column's own label fill and font over the Table's, placed as it says, at its width.
    expect(valueLabel).toHaveStyle({ background: "#DC2626", color: "#000000", textAlign: "center", width: "120px", minWidth: "120px", maxWidth: "120px" })
    expect(valueLabel).not.toHaveClass("min-w-32")
    // Its gridlines in its colour, weight and style; the line before an output solid.
    const crane = screen.getByRole("textbox", { name: "Item row 1" })
    expect(crane.closest("tr")?.style.borderBottom).toBe("3px dashed rgb(0, 0, 0)")
    expect(valueLabel.style.borderLeft).toBe("3px dashed rgb(0, 0, 0)")
    expect(premium.style.borderLeft).toBe("3px solid rgb(0, 0, 0)")
    // Its values at its size: a plain column's in the theme's font and fill, the column's
    // own in its fill, font, weight and place.
    expect(crane).toHaveStyle({ fontSize: "14px", color: "#2563EB" })
    expect(crane.closest("td")).toHaveStyle({ background: "#DC2626" })
    const five = screen.getByRole("textbox", { name: "Value row 1" })
    expect(five).toHaveStyle({ fontSize: "14px", color: "#059669", fontWeight: "600", textAlign: "center" })
    expect(five.closest("td")).toHaveStyle({ background: "#D1D5DB", textAlign: "center" })
    // As wide as its frame while a column takes the width the grid gives it.
    expect(screen.getByRole("table")).toHaveClass("w-full")
    cleanup()

    // An output column's own fill over its shade.
    loadForm(setColumnLook(form, "w_grid", { table: "premiums", column: "premiums.premium" }, { cell_color: "#FFFFFF", bold: true }))
    renderBody("w_grid")
    expect(screen.getByTestId("priced-premiums:premiums.premium-1")).toHaveStyle({ background: "#FFFFFF", fontWeight: "600", fontSize: "14px" })
    cleanup()

    // An index's numbers in its column's own font, which they take over their muted grey;
    // a group's name in the header's weight, as its labels are.
    const indexed = sheetForm()
    indexed.schema.tables[1].columns.unshift(column("equipment.row", "row", { type: "int", index: true }))
    indexed.schema.tables[1].columns[1].group = "Asset"
    const index = { table: "equipment", column: "equipment.row" }
    loadForm(updateWidget(setColumnLook(toggleField(indexed, "w_grid", index), "w_grid", index, { font_color: "#DC2626" }), "w_grid", { groups: true, header_bold: false }))
    renderBody("w_grid")
    expect(within(screen.getAllByRole("row")[2]).getAllByRole("cell")[3]).toHaveStyle({ color: "#DC2626" })
    expect(within(screen.getAllByRole("row")[2]).getAllByRole("cell")[3]).toHaveTextContent("1")
    expect(screen.getByRole("columnheader", { name: "Asset" }).style.fontWeight).toBe("400")
  })

  it("turns a Table's labels as its header says, wraps them, fits the grid to its columns' widths, and draws a column's look along its line transposed", () => {
    const item = { table: "equipment", column: "equipment.item" }
    const value = { table: "equipment", column: "equipment.value" }
    const label = () => screen.getAllByRole("columnheader")[1]
    const turned = () => within(label()).getByText("Item")
    // Level by default, cut off across its column.
    renderBody("w_grid")
    expect(label()).toHaveClass("truncate", "min-w-32")
    expect(turned()).toBe(label())
    cleanup()

    // Turned up, it reads upwards from the header's foot, centred across its column, with no least width.
    loadForm(updateWidget(sheetForm(), "w_grid", { header_orientation: "up" }))
    renderBody("w_grid")
    expect(label()).toHaveClass("align-bottom")
    expect(label()).not.toHaveClass("truncate")
    expect(label()).not.toHaveClass("min-w-32")
    expect(label()).toHaveStyle({ textAlign: "center" })
    expect(turned()).toHaveStyle({ writingMode: "vertical-rl", transform: "rotate(180deg)", whiteSpace: "nowrap" })
    cleanup()
    // Down, and stacked, its letters upright one under another; wrapped, it starts a line beside past its length.
    loadForm(updateWidget(sheetForm(), "w_grid", { header_orientation: "down" }))
    renderBody("w_grid")
    expect(turned().style.writingMode).toBe("vertical-rl")
    expect(turned().style.transform).toBe("")
    cleanup()
    loadForm(updateWidget(sheetForm(), "w_grid", { header_orientation: "stacked", header_wrap: true }))
    renderBody("w_grid")
    expect(turned()).toHaveStyle({ writingMode: "vertical-rl", textOrientation: "upright", whiteSpace: "normal", maxHeight: "128px" })
    cleanup()
    // Level and wrapped, it wraps across its column rather than cutting off.
    loadForm(updateWidget(sheetForm(), "w_grid", { header_wrap: true }))
    renderBody("w_grid")
    expect(label()).not.toHaveClass("truncate")
    expect(label()).toHaveClass("break-words")
    cleanup()

    // Every column given a width: the grid is as wide as they are, not its frame.
    loadForm(setColumnLook(setColumnLook(sheetForm(), "w_grid", item, { width: 80 }), "w_grid", value, { width: 100 }))
    renderBody("w_grid")
    expect(screen.getByRole("table")).toHaveClass("w-max")
    cleanup()

    // Transposed, a column's look is its line's: its label's fill and font, its cells' fill
    // and place; it has no width, and its label turns as the header says.
    let form = setColumnLook(sheetForm(), "w_grid", value, { header_color: "#DC2626", header_font_color: "#FFFFFF", cell_color: "#D1D5DB", align: "right", width: 100 })
    form = updateWidget(form, "w_grid", { transposed: true, header_orientation: "up" })
    loadForm(form)
    renderBody("w_grid")
    const line = screen.getAllByRole("rowheader")[1]
    expect(line).toHaveStyle({ background: "#DC2626", color: "#FFFFFF", textAlign: "right" })
    expect(within(line).getByText("Value")).toHaveStyle({ writingMode: "vertical-rl" })
    const cell = screen.getByRole("textbox", { name: "Value row 1" }).closest("td") as HTMLElement
    expect(cell).toHaveStyle({ background: "#D1D5DB", textAlign: "right" })
    expect(cell.style.width).toBe("")
    cleanup()

    // A column's label given a place of its own sits there, apart from its cells, level and transposed.
    form = setColumnLook(sheetForm(), "w_grid", value, { header_align: "left", align: "right" })
    loadForm(form)
    renderBody("w_grid")
    expect(screen.getAllByRole("columnheader")[2]).toHaveStyle({ textAlign: "left" })
    expect(screen.getByRole("textbox", { name: "Value row 1" }).closest("td")).toHaveStyle({ textAlign: "right" })
    cleanup()
    loadForm(updateWidget(form, "w_grid", { transposed: true }))
    renderBody("w_grid")
    expect(screen.getAllByRole("rowheader")[1]).toHaveStyle({ textAlign: "left" })
  })

  it("gives a checkbox's cell in a grid its row's height at either density", () => {
    const form = sheetForm()
    form.schema.tables[1].columns.push(column("equipment.insured", "insured", { type: "bool" }))
    const grid = toggleField(form, "w_grid", { table: "equipment", column: "equipment.insured" })
    loadForm(grid)
    renderBody("w_grid")
    expect(screen.getByRole("checkbox", { name: "Insured row 1" }).closest("td")).toHaveClass("h-7")
    cleanup()

    loadForm(updateWidget(grid, "w_grid", { density: "comfortable" }))
    renderBody("w_grid")
    expect(screen.getByRole("checkbox", { name: "Insured row 1" }).closest("td")).toHaveClass("h-9")
  })

  it("leaves an empty dropdown blank or showing its column's placeholder, and shows a number's allowed values in its format", () => {
    const options = (name: string) => within(screen.getByRole("combobox", { name })).getAllByRole("option")
    renderBody("w_boxes")
    expect(options("State").map((option) => option.textContent)).toEqual(["", "CA", "NY"])
    cleanup()

    let form = updateSchemaColumn(sheetForm(), "policy", "policy.state", { placeholder: "Pick a state" })
    form = updateSchemaColumn(form, "equipment", "equipment.value", { options: ["1000", "250000"], number_format: "currency_gbp", decimal_places: 0 })
    loadForm(form)
    renderBody("w_boxes")
    expect(options("State")[0]).toHaveTextContent("Pick a state")
    cleanup()

    // Each allowed value shows in the format but is held as typed.
    renderBody("w_grid")
    expect(options("Value row 1").map((option) => [option.getAttribute("value"), option.textContent])).toEqual([
      ["", ""],
      ["1000", "£1,000"],
      ["250000", "£250,000"],
    ])
    fireEvent.change(screen.getByRole("combobox", { name: "Value row 1" }), { target: { value: "250000" } })
    expect(currentForm().sample.equipment).toEqual([{ "equipment.value": "250000" }, {}, {}])
  })

  it("shows what pricing the sample gave an output box, dimmed once the sample has moved on", () => {
    const form = toggleField(sheetForm(), "w_boxes", { table: "premiums", column: "premiums.premium" })
    loadForm(form)
    const { rerender } = renderBody("w_boxes")
    const box = () => screen.getByTestId("priced-premiums:premiums.premium")
    expect(box()).toHaveTextContent("—")

    useWorkbenchPricingStore.setState({ price: { basis: previewBasis(form), sample: {}, tables: { premiums: [{ premium: 1234.5 }] } } })
    rerender(body("w_boxes"))
    expect(box()).toHaveTextContent("1,234.5")
    expect(box()).not.toHaveAttribute("data-stale")

    fireEvent.change(screen.getByRole("combobox", { name: "State" }), { target: { value: "CA" } })
    rerender(body("w_boxes"))
    expect(box()).toHaveAttribute("data-stale", "true")
    expect(box()).toHaveStyle({ opacity: "0.5" })

    useWorkbenchPricingStore.setState({ price: { basis: previewBasis(currentForm()), sample: {}, tables: { premiums: [{ premium: null }] } } })
    rerender(body("w_boxes"))
    expect(box()).toHaveTextContent("—")
    expect(box()).not.toHaveAttribute("data-stale")

    // In its column's format, once it has one.
    loadForm(updateSchemaColumn(currentForm(), "premiums", "premiums.premium", { number_format: "currency_gbp" }))
    useWorkbenchPricingStore.setState({ price: { basis: previewBasis(currentForm()), sample: {}, tables: { premiums: [{ premium: 1234.5 }] } } })
    rerender(body("w_boxes"))
    expect(box()).toHaveTextContent("£1,234.50")
  })

  it("shows numbers in their column's format, a cell as typed while typing, and a column's placeholder and help", () => {
    let form = toggleField(sheetForm(), "w_grid", { table: "premiums", column: "premiums.premium" })
    form = updateSchemaColumn(form, "equipment", "equipment.value", { number_format: "number", decimal_places: 0, help: "Replacement value" })
    form = updateSchemaColumn(form, "equipment", "equipment.item", { placeholder: "e.g. Crane" })
    form = updateSchemaColumn(form, "premiums", "premiums.premium", { number_format: "currency_gbp" })
    form.sample = { equipment: [{ "equipment.item": "Crane", "equipment.value": "1234567.4" }, {}, {}] }
    loadForm(form)
    useWorkbenchPricingStore.setState({
      price: { basis: previewBasis(form), sample: { equipment: [{ item: "Crane" }] }, tables: { premiums: [{ item: "Crane", premium: 1234.5 }] } },
    })
    renderBody("w_grid")

    const premium = screen.getByTestId("priced-premiums:premiums.premium-1")
    expect(premium).toHaveTextContent("£1,234.50")
    // An output column is lined off from the inputs and tinted green.
    expect(premium.style.borderLeft).toBe("1px solid var(--border)")
    expect(premium.style.background).toBe("var(--workbench-output-cell-bg)")
    // An empty number cell is blank, with no hint that reads as a value.
    expect(screen.getByRole("textbox", { name: "Value row 2" })).not.toHaveAttribute("placeholder")
    const value = screen.getByRole("textbox", { name: "Value row 1" })
    expect(value).toHaveValue("1,234,567")
    fireEvent.focus(value)
    expect(value).toHaveValue("1234567.4")
    fireEvent.change(value, { target: { value: "2500" } })
    fireEvent.blur(value)
    expect(currentForm().sample.equipment).toEqual([{ "equipment.item": "Crane", "equipment.value": "2500" }, {}, {}])
    expect(value).toHaveValue("2,500")

    expect(screen.getByRole("textbox", { name: "Item row 2" })).toHaveAttribute("placeholder", "e.g. Crane")
    expect(screen.getByTitle("Replacement value")).toHaveTextContent("Replacement value")
  })

  it("takes a percent as the fraction typed, 0.125, and shows it as 12.5% once typed", () => {
    loadForm(updateSchemaColumn(sheetForm(), "equipment", "equipment.value", { number_format: "percent", decimal_places: 1 }))
    renderBody("w_grid")
    const value = screen.getByRole("textbox", { name: "Value row 1" })

    fireEvent.focus(value)
    fireEvent.change(value, { target: { value: "0.125" } })
    fireEvent.blur(value)
    expect(currentForm().sample.equipment).toEqual([{ "equipment.value": "0.125" }, {}, {}])
    expect(value).toHaveValue("12.5%")
    fireEvent.focus(value)
    expect(value).toHaveValue("0.125")
  })

  it("shows a Table's output columns matched to its rows by key, as the server typed the keys", () => {
    const form = toggleField(sheetForm(), "w_grid", { table: "premiums", column: "premiums.premium" })
    form.sample = { equipment: [{ "equipment.item": "Crane" }, {}, { "equipment.item": "Paver" }] }
    loadForm(form)
    useWorkbenchPricingStore.setState({
      price: {
        basis: previewBasis(form),
        sample: { equipment: [{ item: "Crane" }, { item: "Paver" }] },
        tables: { premiums: [{ item: "Paver", premium: 20 }, { item: "Crane", premium: 10 }] },
      },
    })
    renderBody("w_grid")
    const cell = (row: number) => screen.getByTestId(`priced-premiums:premiums.premium-${row}`)

    expect(cell(1)).toHaveTextContent("10")
    expect(cell(2)).toHaveTextContent("—")
    expect(cell(3)).toHaveTextContent("20")
    expect(cell(1)).not.toHaveAttribute("data-stale")

    // Typed since: the answer is for other values, dimmed, until the next pricing lines
    // the rows up again.
    const item = screen.getByRole("textbox", { name: "Item row 2" })
    fireEvent.change(item, { target: { value: "Dozer" } })
    fireEvent.keyDown(item, { key: "Enter" })
    cleanup()
    renderBody("w_grid")
    expect(cell(1)).toHaveAttribute("data-stale", "true")
    expect(cell(2)).toHaveAttribute("data-stale", "true")
    expect(cell(3)).toHaveTextContent("—")
  })

  it("shows a Table of output columns alone as its starting rows of dashes until priced, then a row for each of the answer's rows, taking no typing", () => {
    const form = sheetForm()
    const results = {
      ...createWidget("tableInput", { x: 16, y: 380, w: 480, h: 160 }),
      id: "w_results",
      title: "Premiums",
      fields: [
        { table: "premiums", column: "premiums.item" },
        { table: "premiums", column: "premiums.premium" },
      ],
    }
    addTo(form, results)
    loadForm(form)
    const { rerender } = renderBody("w_results")
    const cell = (name: string, row: number) => screen.queryByTestId(`priced-premiums:premiums.${name}-${row}`)

    // Nothing priced: its three starting rows, each cell a dash, so it reads as not yet connected.
    expect(cell("premium", 3)).toHaveTextContent("—")
    expect(cell("premium", 4)).toBeNull()
    expectNoRowMenu(cell("premium", 1) as HTMLElement)
    expect(screen.queryByRole("button", { name: "Delete row 1" })).toBeNull()

    // Priced: a row for each of the answer's, as a Chart of the same columns plots them.
    act(() =>
      useWorkbenchPricingStore.setState({
        price: {
          basis: previewBasis(form),
          sample: {},
          tables: { premiums: [{ item: "Crane", premium: 10 }, { item: "Paver", premium: 20 }, { item: "Dozer", premium: 30 }, { item: "Lift", premium: 40 }] },
        },
      }),
    )
    rerender(body("w_results"))
    expect([1, 2, 3, 4].map((row) => cell("item", row)?.textContent)).toEqual(["Crane", "Paver", "Dozer", "Lift"])
    expect(cell("premium", 4)).toHaveTextContent("40")
    expect(cell("premium", 5)).toBeNull()
    expect(screen.queryByRole("textbox")).toBeNull()
  })

  it("keeps a Table of output columns alone on its first field's rows in Preview while that field hides", () => {
    const premium = { table: "premiums", column: "premiums.premium" }
    const covered: ShowWhen = { match: "all", conditions: [{ table: "policy", column: "policy.cover", op: "ticked", values: [] }] }
    const form = coverForm()
    form.schema.tables.push(
      table("fees", "fees", { role: "output", rows: "many", columns: [column("fees.item", "item", { key: true }), column("fees.fee", "fee", { type: "float" })] }),
    )
    addTo(form, {
      ...createWidget("tableInput", { x: 16, y: 380, w: 480, h: 160 }),
      id: "w_results",
      title: "Premiums",
      fields: [premium, { table: "fees", column: "fees.item" }, { table: "fees", column: "fees.fee" }],
    })
    loadForm(setColumnLook(form, "w_results", premium, { show_when: covered }))
    useWorkbenchViewStore.setState({ mode: "preview" })
    render(<SheetCanvas />)
    act(() =>
      useWorkbenchPricingStore.setState({
        price: {
          basis: previewBasis(currentForm()),
          sample: {},
          tables: {
            premiums: [{ item: "Crane", premium: 10 }, { item: "Lift", premium: 20 }],
            fees: [{ item: "Lift", fee: 200 }, { item: "Crane", fee: 100 }, { item: "Dozer", fee: 300 }],
          },
        },
      }),
    )
    const grid = within(screen.getByTestId("sheet-widget-w_results"))

    // The premium's column goes, but the fees stay lined up with the premiums' rows.
    expect(grid.getAllByRole("columnheader").map((header) => header.textContent)).toEqual(["#", "Item", "Fee"])
    expect([1, 2, 3].map((row) => screen.queryByTestId(`priced-fees:fees.fee-${row}`)?.textContent)).toEqual(["100", "200", undefined])
  })

  it("takes a date once it is committed, one edit, not as each digit of its year is typed", () => {
    const form = toggleField(sheetForm(), "w_boxes", { table: "policy", column: "policy.year" })
    loadForm(updateSchemaColumn(form, "policy", "policy.year", { type: "date" }))
    useWorkbenchViewStore.setState({ mode: "preview" })
    renderBody("w_boxes")
    const year = screen.getByLabelText("Year")

    for (const value of ["0002-01-01", "0020-01-01", "0202-01-01", "2026-01-01"]) fireEvent.change(year, { target: { value } })
    expect(currentForm().sample.policy?.[0]?.["policy.year"]).toBeUndefined()
    fireEvent.blur(year)

    expect(currentForm().sample.policy[0]["policy.year"]).toBe("2026-01-01")
    expect(useWorkbenchFormStore.getState().undoStack).toHaveLength(1)
  })

  it("shows a held value its select no longer allows, and a tick in a column since retyped, each marked", () => {
    loadForm(toggleField({ ...sheetForm(), sample: { policy: [{ "policy.state": "TX", "policy.year": true }] } }, "w_boxes", { table: "policy", column: "policy.year" }))
    useWorkbenchViewStore.setState({ mode: "preview" })
    renderBody("w_boxes")

    // Priced as held, so shown as held, not as a blank select.
    const state = screen.getByRole("combobox", { name: "State" }) as HTMLSelectElement
    expect(state.value).toBe("TX")
    expect(within(state).getByRole("option", { name: "TX" })).toBeInTheDocument()
    expect(state).toHaveAttribute("title", "Not one of the allowed values")
    expect(screen.getByRole("textbox", { name: "Year" })).toHaveAttribute("title", "A tick, not a value")
  })

  it("marks the cells that break their column's rules once anything is typed, as the sample is typed in Preview", () => {
    loadForm(toggleField(sheetForm(), "w_boxes", { table: "policy", column: "policy.year" }))
    useWorkbenchViewStore.setState({ mode: "preview" })
    const { rerender } = renderBody("w_boxes")

    // Nothing typed: nothing is marked, though the state is required.
    expect(screen.getByRole("combobox", { name: "State" })).not.toHaveAttribute("aria-invalid")

    // A year typed with the state blank: the required cell is marked, and cleared as soon as it is filled.
    const year = screen.getByRole("textbox", { name: "Year" })
    fireEvent.change(year, { target: { value: "2021" } })
    fireEvent.blur(year)
    rerender(body("w_boxes"))
    const state = screen.getByRole("combobox", { name: "State" })
    expect(state).toHaveAttribute("aria-invalid", "true")
    expect(state).toHaveAttribute("title", "Required")
    fireEvent.change(state, { target: { value: "CA" } })
    rerender(body("w_boxes"))
    expect(screen.getByRole("combobox", { name: "State" })).not.toHaveAttribute("aria-invalid")
  })

  it("marks nothing in the underwriter app's quote while its only value is in a row its table's rows leave out, the column giving them hidden", () => {
    loadForm(hiddenPerilsForm())
    useWorkbenchPreviewStore.setState({ quote: { coverage: [{ "coverage.peril": "NW", "coverage.limit": "5000" }] } })
    const { rerender } = renderBody("w_boxes", "quote")
    expect(screen.getByRole("combobox", { name: "State" })).not.toHaveAttribute("aria-invalid")

    act(() => useWorkbenchPreviewStore.setState({ quote: { coverage: [{ "coverage.peril": "EQ", "coverage.limit": "5000" }] } }))
    rerender(body("w_boxes", "quote"))
    expect(screen.getByRole("combobox", { name: "State" })).toHaveAttribute("aria-invalid", "true")
  })

  it("types the underwriter app's quote apart from the sample, without history, its cells marked once anything is typed, and shows its price", () => {
    loadForm(toggleField(sheetForm(), "w_boxes", { table: "policy", column: "policy.year" }))
    const { rerender } = renderBody("w_boxes", "quote")

    const year = screen.getByRole("textbox", { name: "Year" })
    fireEvent.change(year, { target: { value: "2021" } })
    fireEvent.blur(year)
    expect(useWorkbenchPreviewStore.getState().quote).toEqual({ policy: [{ "policy.year": "2021" }] })
    expect(currentForm().sample).toEqual({})
    expect(useWorkbenchFormStore.getState().undoStack).toEqual([])
    rerender(body("w_boxes", "quote"))
    expect(screen.getByRole("combobox", { name: "State" })).toHaveAttribute("title", "Required")

    // An output box shows the quote's price, not the sample's.
    loadForm(toggleField(sheetForm(), "w_boxes", { table: "premiums", column: "premiums.premium" }))
    useWorkbenchPricingStore.setState({ price: { basis: previewBasis(currentForm()), sample: {}, tables: { premiums: [{ premium: 1 }] } } })
    useWorkbenchPreviewStore.setState({
      quote: { policy: [{ "policy.state": "CA" }] },
      price: { basis: valuesBasis(currentForm().schema, new Set(), { policy: [{ "policy.state": "CA" }] }), sample: {}, tables: { premiums: [{ premium: 777 }] } },
    })
    cleanup()
    renderBody("w_boxes", "quote")
    expect(screen.getByTestId("priced-premiums:premiums.premium")).toHaveTextContent("777")
  })

  it("keeps the rows the underwriter app's quote's grid starts with as the first value is typed into it, and inserts and deletes its rows", () => {
    useWorkbenchViewStore.setState({ mode: "preview" })
    renderBody("w_grid", "quote")

    const item = screen.getByRole("textbox", { name: "Item row 1" })
    fireEvent.change(item, { target: { value: "Crane" } })
    fireEvent.keyDown(item, { key: "Enter" })
    expect(useWorkbenchPreviewStore.getState().quote).toEqual({ equipment: [{ "equipment.item": "Crane" }, {}, {}] })
    expect(screen.getAllByRole("row")).toHaveLength(4)

    fromRowMenu(item, "Insert", "Insert Row Above")
    expect(useWorkbenchPreviewStore.getState().quote).toEqual({ equipment: [{}, { "equipment.item": "Crane" }, {}, {}] })
    fromRowMenu(screen.getByRole("textbox", { name: "Item row 3" }), "Delete Row")
    expect(useWorkbenchPreviewStore.getState().quote).toEqual({ equipment: [{}, { "equipment.item": "Crane" }, {}] })
    expect(currentForm().sample).toEqual({})
  })

  it("while building draws a component's cells as a new quote's, empty and inert, so a press on one is its frame's", () => {
    loadForm({ ...sheetForm(), sample: { policy: [{ "policy.state": "NY" }] } })
    render(<SheetCanvas />)
    const frame = screen.getByTestId("sheet-widget-w_boxes")
    const cell = within(frame).getByRole("combobox", { name: "State" })

    // The sample is Preview's: the cell is empty, as a new quote's is, and not disabled.
    expect(cell).toHaveValue("")
    expect(cell).toBeEnabled()
    expect(cell.closest("[inert]")).not.toBeNull()
    frame.dispatchEvent(new MouseEvent("pointerdown", { bubbles: true, button: 0, clientX: 100, clientY: 100 }))
    window.dispatchEvent(new MouseEvent("pointerup", { bubbles: true, clientX: 100, clientY: 100 }))
    expect(useWorkbenchViewStore.getState().selectedId).toBe("w_boxes")
    expect(useWorkbenchFormStore.getState().undoStack).toEqual([])

    // In Preview the cells take typing: the sample's.
    act(() => useWorkbenchViewStore.setState({ mode: "preview" }))
    expect(within(screen.getByTestId("sheet-widget-w_boxes")).getByRole("combobox", { name: "State" })).toHaveValue("NY")
    expect(screen.getByTestId("sheet-widget-w_boxes").querySelector("[inert]")).toBeNull()
  })

  it("while building draws a new quote holding its columns' defaults, Preview the sample as typed", () => {
    const form = sheetForm()
    form.schema.tables[0].columns[0].default_value = "CA"
    loadForm({ ...form, sample: { policy: [{ "policy.state": "NY" }] } })
    render(<SheetCanvas />)

    expect(within(screen.getByTestId("sheet-widget-w_boxes")).getByRole("combobox", { name: "State" })).toHaveValue("CA")
    act(() => useWorkbenchViewStore.setState({ mode: "preview" }))
    expect(within(screen.getByTestId("sheet-widget-w_boxes")).getByRole("combobox", { name: "State" })).toHaveValue("NY")
  })

  it("draws a Heading as its text alone, a heading of its size's level, and without text asks for some while building and shows nothing in Preview", () => {
    const form = sheetForm()
    addTo(form, headingWidget("w_head", "Equipment schedule"))
    loadForm(form)
    const resize = (size: "large" | "small") => act(() => useWorkbenchFormStore.getState().change((spec) => updateWidget(spec, "w_head", { size })))
    const preview = (on: boolean) => act(() => useWorkbenchViewStore.setState({ mode: on ? "preview" : "build" }))
    const heading = (level: number) => screen.getByRole("heading", { level, name: "Equipment schedule" })
    const { container, rerender } = renderBody("w_head")

    // A section's heading: its text on the sheet itself, with no frame or background of its own.
    expect(heading(3)).toHaveClass("text-base", "font-semibold")
    expect(heading(3)).toHaveStyle({ color: "var(--text-primary)" })
    expect(container.querySelector("[style*='border'], [style*='background'], [class*='border'], [class*='bg-']")).toBeNull()
    // A title a level up, a label a level down in the secondary colour, each semibold.
    resize("large")
    rerender(body("w_head"))
    expect(heading(2)).toHaveClass("text-xl", "font-semibold")
    expect(heading(2)).toHaveStyle({ color: "var(--text-primary)" })
    resize("small")
    rerender(body("w_head"))
    expect(heading(4)).toHaveClass("text-xs", "font-semibold")
    expect(heading(4)).toHaveStyle({ color: "var(--text-secondary)" })
    // The same in Preview, where an underwriter reads it.
    preview(true)
    rerender(body("w_head"))
    expect(heading(4)).toBeInTheDocument()
    preview(false)

    // Only spaces are no text: it asks for some on one line while building, and is nothing in Preview.
    act(() => useWorkbenchFormStore.getState().change((spec) => updateWidget(spec, "w_head", { text: "  " })))
    rerender(body("w_head"))
    expect(screen.queryByRole("heading")).not.toBeInTheDocument()
    expect(screen.getByText("Type its text in the panel on the right")).toHaveClass("truncate")
    preview(true)
    rerender(body("w_head"))
    expect(container).toBeEmptyDOMElement()
  })

  it("draws a Heading's description under it, both aligned as it says, and no description while it has no text", () => {
    const form = sheetForm()
    addTo(form, headingWidget("w_head", "Equipment schedule", { description: "Each item insured, with its value" }))
    loadForm(form)
    const change = (patch: WidgetPatch) => act(() => useWorkbenchFormStore.getState().change((spec) => updateWidget(spec, "w_head", patch)))
    const { container, rerender } = renderBody("w_head")

    // Under the heading in regular type and the secondary colour, read after it.
    const heading = screen.getByRole("heading", { level: 3, name: "Equipment schedule" })
    const description = screen.getByText("Each item insured, with its value")
    expect(description.tagName).toBe("P")
    expect(description).toHaveClass("text-xs")
    expect(description).not.toHaveClass("font-semibold")
    expect(description).toHaveStyle({ color: "var(--text-secondary)" })
    expect(heading.compareDocumentPosition(description) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    // Both aligned left, centred or right, as one.
    expect(heading.parentElement).toBe(description.parentElement)
    expect(heading.parentElement).toHaveClass("text-left")
    for (const [align, className] of [["center", "text-center"], ["right", "text-right"]] as const) {
      change({ align })
      rerender(body("w_head"))
      expect(screen.getByRole("heading", { name: "Equipment schedule" }).parentElement).toHaveClass(className)
    }
    // The same for the underwriter in Preview.
    act(() => useWorkbenchViewStore.setState({ mode: "preview" }))
    rerender(body("w_head"))
    expect(screen.getByText("Each item insured, with its value").parentElement).toHaveClass("text-right")
    act(() => useWorkbenchViewStore.setState({ mode: "build", schemaOpen: false }))
    rerender(body("w_head"))

    // Only spaces are no description; and a Heading with no text shows its hint alone.
    change({ description: "   " })
    rerender(body("w_head"))
    expect(container.querySelector("p")).toBeNull()
    change({ text: " ", description: "Each item insured, with its value" })
    rerender(body("w_head"))
    expect(screen.queryByText("Each item insured, with its value")).not.toBeInTheDocument()
    expect(screen.getByText("Type its text in the panel on the right")).toBeInTheDocument()
  })

  it("shows the underwriter app's quote held read only with its cells taking no typing and no rows inserted or deleted", () => {
    act(() => useWorkbenchPreviewStore.getState().open({ policy: [{ "policy.state": "NY" }], equipment: [{ "equipment.item": "Crane" }] }, null, true))
    const { unmount } = renderBody("w_boxes", "quote")
    expect(screen.getByRole("combobox", { name: "State" })).toBeDisabled()
    unmount()

    renderBody("w_grid", "quote")
    expect(screen.getByRole("textbox", { name: "Item row 1" })).toBeDisabled()
    expectNoRowMenu(screen.getByRole("textbox", { name: "Item row 1" }))
    expect(screen.queryByRole("button", { name: "Delete row 1" })).not.toBeInTheDocument()
    act(() => useWorkbenchPreviewStore.getState().setCell("policy", 0, "policy.state", "CA"))
    expect(useWorkbenchPreviewStore.getState().quote.policy[0]["policy.state"]).toBe("NY")
  })
})

describe("spacersShown", () => {
  it.each([
    // Five fields, a spacer after the third: the fields after it hide, so it goes with them.
    { spacers: [3], shows: [true, true, true, false, false], placed: [] },
    // The fields before it hide: it stays, among the fields that show.
    { spacers: [3], shows: [true, false, false, true, true], placed: [1] },
    // Two at one place: the first's run is empty and stays; the second goes with its fields.
    { spacers: [2, 2], shows: [true, true, false, false], placed: [2] },
    // After the last field it has no fields of its own, and stays at the end.
    { spacers: [2], shows: [false, true], placed: [1] },
    // Nothing hides: every spacer where it sits.
    { spacers: [0, 1, 3], shows: [true, true, true], placed: [0, 1, 3] },
  ])("places $spacers among $shows at $placed", ({ spacers, shows, placed }) => {
    expect(spacersShown(spacers, shows)).toEqual(placed)
  })
})
