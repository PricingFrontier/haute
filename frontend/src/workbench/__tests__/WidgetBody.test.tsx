/**
 * A component's cells (specs/workbench): the sample typed into a Collection's boxes and a
 * Table's grid, rows added and deleted across the tables a grid shows, a grid drawn as its
 * Table's options say, an output box showing what pricing the sample gave, dimmed once the
 * sample has moved on, a Table's output columns matched to its rows by key, the cells that
 * break their column's rules marked once anything is typed, while building a new quote's
 * cells, empty and inert, the underwriter app's quote typed apart from the sample, and a
 * Heading drawn as its text.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react"
import type { ReactNode } from "react"
import useWorkbenchFormStore from "../../stores/useWorkbenchFormStore"
import useWorkbenchPreviewStore from "../../stores/useWorkbenchPreviewStore"
import useWorkbenchPricingStore from "../../stores/useWorkbenchPricingStore"
import useWorkbenchViewStore from "../../stores/useWorkbenchViewStore"
import { valuesBasis } from "../../utils/sheetValues"
import { addSchemaColumn, addSchemaTable, BLANK_THEME, pricingBasis, shownFields, toggleField, updateSchemaColumn, updateWidget, type WidgetPatch } from "../../utils/workbenchForm"
import SheetCanvas from "../SheetCanvas"
import { SheetValuesContext, useNewQuoteValues, useQuoteValues, useSampleValues } from "../useSheetValues"
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
    <WidgetBody widget={widget(id)} fields={shownFields(currentForm(), widget(id))} />
  </OnSheet>
)

function renderBody(id: string, values: Values = "sample") {
  return render(body(id, values))
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
    fireEvent.change(screen.getByRole("combobox", { name: "State" }), { target: { value: "NY" } })
    expect(currentForm().sample.policy).toEqual([{ "policy.state": "NY" }])
    expect(useWorkbenchFormStore.getState().undoStack).toHaveLength(1)

    loadForm(toggleField(sheetForm(), "w_boxes", { table: "policy", column: "policy.year" }))
    cleanup()
    renderBody("w_boxes")
    const year = screen.getByRole("textbox", { name: "Year" })
    fireEvent.change(year, { target: { value: "2021" } })
    expect(currentForm().sample).toEqual({})
    fireEvent.blur(year)
    expect(currentForm().sample.policy).toEqual([{ "policy.year": "2021" }])
  })

  it("types the sample into a Table's grid row by row, keeping the rows it starts with, adding and deleting rows in every table it shows", () => {
    renderBody("w_grid")
    expect(screen.getAllByRole("row")).toHaveLength(4)

    // The first value typed keeps the grid's three starting rows, as one undo step.
    const item = screen.getByRole("textbox", { name: "Item row 2" })
    fireEvent.change(item, { target: { value: "Crane" } })
    fireEvent.keyDown(item, { key: "Enter" })
    expect(currentForm().sample.equipment).toEqual([{}, { "equipment.item": "Crane" }, {}])
    expect(useWorkbenchFormStore.getState().undoStack).toHaveLength(1)

    fireEvent.click(screen.getByRole("button", { name: "Add row" }))
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
    // Its values take no typing, and no row is added or deleted.
    expect(screen.queryByRole("textbox", { name: "Item row 1" })).not.toBeInTheDocument()
    expect(screen.queryByRole("button", { name: "Add row" })).not.toBeInTheDocument()
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

  it("draws a Table as its options say, and with its rows fixed shows every row typed but no Add row or delete", () => {
    const bodyRows = () => screen.getAllByRole("row").slice(1)
    const cellOf = (name: string) => screen.getByRole("textbox", { name }).closest("td")

    // By default: numbered, compact rows lined between them, every other one shaded, each
    // with a delete at its end.
    renderBody("w_grid")
    expect(screen.getAllByRole("columnheader")).toHaveLength(4)
    expect(screen.getAllByRole("columnheader")[0]).toHaveTextContent("#")
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
    // Every row typed shows, one more than the grid's 3, though none can be added or deleted.
    expect(bodyRows()).toHaveLength(4)
    expect(screen.getByRole("textbox", { name: "Item row 4" })).toHaveValue("D")
    expect(screen.queryByRole("button", { name: "Add row" })).not.toBeInTheDocument()
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
    expect(header()).toHaveClass("text-xs")
    expect(framed.style.border).toBe("1px solid var(--border)")
    cleanup()

    loadForm(updateWidget(sheetForm(), "w_grid", { header: "caps", frame: false }))
    const { container } = renderBody("w_grid")
    // The labels in capitals.
    expect(header()).toHaveClass("uppercase", "text-[11px]")
    // No border or background of its own: the grid sits on the sheet.
    const frame = container.firstElementChild as HTMLElement
    expect(frame.style.border).toBe("")
    expect(frame.style.background).toBe("")
  })

  it("draws a Table transposed, its fields down its side and its rows across, typed, added and deleted as its rows are", () => {
    const form = updateWidget(toggleField(sheetForm(), "w_grid", { table: "premiums", column: "premiums.premium" }), "w_grid", { transposed: true, header: "caps" })
    loadForm(form)
    renderBody("w_grid")
    const lines = () => screen.getAllByRole("row")
    const cellOf = (name: string) => screen.getByRole("textbox", { name }).closest("td")

    // The rows' numbers across the top, with an add at their end; a line per field under
    // them, labelled at its start in the header's look, holding the field's cell in each row.
    expect(lines()).toHaveLength(4)
    expect(within(lines()[0]).getAllByRole("columnheader").map((header) => header.textContent)).toEqual(["#", "1", "2", "3", ""])
    expect(screen.getAllByRole("rowheader").map((header) => header.textContent)).toEqual(["Item", "Value", "Premium"])
    expect(screen.getAllByRole("rowheader")[0]).toHaveClass("uppercase")
    expect(within(lines()[1]).getAllByRole("textbox").map((box) => box.getAttribute("aria-label"))).toEqual(["Item row 1", "Item row 2", "Item row 3"])
    // Shaded every other line and lined between them as drawn, an output field ruled off above its line.
    expect(lines()[1]).toHaveClass("even:bg-[var(--chip-rest)]")
    expect(lines()[1].style.borderBottom).toBe("1px solid var(--border-subtle)")
    expect(screen.getByTestId("priced-premiums:premiums.premium-1").style.borderTop).toBe("1px solid var(--border)")
    expect(cellOf("Value row 1")?.style.borderTop).toBe("")

    // Typed, added and deleted as its rows are, the add and the deletes saying what they do as drawn.
    const item = screen.getByRole("textbox", { name: "Item row 2" })
    fireEvent.change(item, { target: { value: "Crane" } })
    fireEvent.keyDown(item, { key: "Enter" })
    expect(currentForm().sample.equipment).toEqual([{}, { "equipment.item": "Crane" }, {}])
    expect(screen.getByRole("button", { name: "Add row" })).toHaveAttribute("title", "Add column")
    fireEvent.click(screen.getByRole("button", { name: "Add row" }))
    expect(currentForm().sample.equipment).toEqual([{}, { "equipment.item": "Crane" }, {}, {}])
    expect(within(lines()[0]).getAllByRole("columnheader")).toHaveLength(6)
    expect(screen.getByRole("button", { name: "Delete row 1" })).toHaveAttribute("title", "Delete column")
    fireEvent.click(screen.getByRole("button", { name: "Delete row 1" }))
    expect(currentForm().sample.equipment).toEqual([{ "equipment.item": "Crane" }, {}, {}])
    cleanup()

    // Fixed and unnumbered, nothing goes above the fields; a full grid lines the rows' columns, the first ruled off by the labels alone.
    loadForm(updateWidget(form, "w_grid", { fixed_rows: true, row_numbers: false, gridlines: "all" }))
    renderBody("w_grid")
    expect(screen.queryAllByRole("columnheader")).toHaveLength(0)
    expect(screen.queryByRole("button", { name: "Add row" })).not.toBeInTheDocument()
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

  it("draws the components in the sheets' colours: the font, the borders, the backgrounds and the input and output cells' fills", () => {
    let form = toggleField(sheetForm(), "w_grid", { table: "premiums", column: "premiums.premium" })
    form = toggleField(form, "w_boxes", { table: "premiums", column: "premiums.premium" })
    addTo(form, headingWidget("w_head", "Equipment schedule", { description: "By item" }))
    form.sample = { equipment: [{ "equipment.item": "Crane" }] }
    form.theme = { ...BLANK_THEME, background_color: "#059669", border_color: "#D97706", font_color: "#2563EB", input_cell_color: "#DC2626", output_cell_color: "#9333EA" }
    loadForm(form)
    useWorkbenchPricingStore.setState({
      price: { basis: pricingBasis(form), sample: { equipment: [{ item: "Crane" }] }, tables: { premiums: [{ item: "Crane", premium: 10 }] } },
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

    useWorkbenchPricingStore.setState({ price: { basis: pricingBasis(form), sample: {}, tables: { premiums: [{ premium: 1234.5 }] } } })
    rerender(body("w_boxes"))
    expect(box()).toHaveTextContent("1,234.5")
    expect(box()).not.toHaveAttribute("data-stale")

    fireEvent.change(screen.getByRole("combobox", { name: "State" }), { target: { value: "CA" } })
    rerender(body("w_boxes"))
    expect(box()).toHaveAttribute("data-stale", "true")
    expect(box()).toHaveStyle({ opacity: "0.5" })

    useWorkbenchPricingStore.setState({ price: { basis: pricingBasis(currentForm()), sample: {}, tables: { premiums: [{ premium: null }] } } })
    rerender(body("w_boxes"))
    expect(box()).toHaveTextContent("—")
    expect(box()).not.toHaveAttribute("data-stale")

    // In its column's format, once it has one.
    loadForm(updateSchemaColumn(currentForm(), "premiums", "premiums.premium", { number_format: "currency_gbp" }))
    useWorkbenchPricingStore.setState({ price: { basis: pricingBasis(currentForm()), sample: {}, tables: { premiums: [{ premium: 1234.5 }] } } })
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
      price: { basis: pricingBasis(form), sample: { equipment: [{ item: "Crane" }] }, tables: { premiums: [{ item: "Crane", premium: 1234.5 }] } },
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
        basis: pricingBasis(form),
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

  it("marks the cells that break their column's rules once anything is typed, as the sample is typed in Preview", () => {
    loadForm(toggleField(sheetForm(), "w_boxes", { table: "policy", column: "policy.year" }))
    useWorkbenchViewStore.setState({ section: "preview" })
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
    useWorkbenchPricingStore.setState({ price: { basis: pricingBasis(currentForm()), sample: {}, tables: { premiums: [{ premium: 1 }] } } })
    useWorkbenchPreviewStore.setState({
      quote: { policy: [{ "policy.state": "CA" }] },
      price: { basis: valuesBasis(currentForm().schema, new Set(), { policy: [{ "policy.state": "CA" }] }), sample: {}, tables: { premiums: [{ premium: 777 }] } },
    })
    cleanup()
    renderBody("w_boxes", "quote")
    expect(screen.getByTestId("priced-premiums:premiums.premium")).toHaveTextContent("777")
  })

  it("keeps the rows the underwriter app's quote's grid starts with as the first value is typed into it", () => {
    useWorkbenchViewStore.setState({ section: "preview" })
    renderBody("w_grid", "quote")

    const item = screen.getByRole("textbox", { name: "Item row 1" })
    fireEvent.change(item, { target: { value: "Crane" } })
    fireEvent.keyDown(item, { key: "Enter" })
    expect(useWorkbenchPreviewStore.getState().quote).toEqual({ equipment: [{ "equipment.item": "Crane" }, {}, {}] })
    expect(screen.getAllByRole("row")).toHaveLength(4)
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
    act(() => useWorkbenchViewStore.setState({ section: "preview" }))
    expect(within(screen.getByTestId("sheet-widget-w_boxes")).getByRole("combobox", { name: "State" })).toHaveValue("NY")
    expect(screen.getByTestId("sheet-widget-w_boxes").querySelector("[inert]")).toBeNull()
  })

  it("while building draws a new quote holding its columns' defaults, Preview the sample as typed", () => {
    const form = sheetForm()
    form.schema.tables[0].columns[0].default_value = "CA"
    loadForm({ ...form, sample: { policy: [{ "policy.state": "NY" }] } })
    render(<SheetCanvas />)

    expect(within(screen.getByTestId("sheet-widget-w_boxes")).getByRole("combobox", { name: "State" })).toHaveValue("CA")
    act(() => useWorkbenchViewStore.setState({ section: "preview" }))
    expect(within(screen.getByTestId("sheet-widget-w_boxes")).getByRole("combobox", { name: "State" })).toHaveValue("NY")
  })

  it("draws a Heading as its text alone, a heading of its size's level, and without text asks for some while building and shows nothing in Preview", () => {
    const form = sheetForm()
    addTo(form, headingWidget("w_head", "Equipment schedule"))
    loadForm(form)
    const resize = (size: "large" | "small") => act(() => useWorkbenchFormStore.getState().change((spec) => updateWidget(spec, "w_head", { size })))
    const preview = (on: boolean) => act(() => useWorkbenchViewStore.setState({ section: on ? "preview" : "sheets" }))
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
    act(() => useWorkbenchViewStore.setState({ section: "preview" }))
    rerender(body("w_head"))
    expect(screen.getByText("Each item insured, with its value").parentElement).toHaveClass("text-right")
    act(() => useWorkbenchViewStore.setState({ section: "sheets" }))
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

  it("shows the underwriter app's quote held read only with its cells taking no typing and no rows added or deleted", () => {
    act(() => useWorkbenchPreviewStore.getState().open({ policy: [{ "policy.state": "NY" }], equipment: [{ "equipment.item": "Crane" }] }, null, true))
    const { unmount } = renderBody("w_boxes", "quote")
    expect(screen.getByRole("combobox", { name: "State" })).toBeDisabled()
    unmount()

    renderBody("w_grid", "quote")
    expect(screen.getByRole("textbox", { name: "Item row 1" })).toBeDisabled()
    expect(screen.queryByRole("button", { name: "Add row" })).not.toBeInTheDocument()
    expect(screen.queryByRole("button", { name: "Delete row 1" })).not.toBeInTheDocument()
    act(() => useWorkbenchPreviewStore.getState().setCell("policy", 0, "policy.state", "CA"))
    expect(useWorkbenchPreviewStore.getState().quote.policy[0]["policy.state"]).toBe("NY")
  })
})
