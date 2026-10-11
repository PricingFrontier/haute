/**
 * A component's cells (specs/workbench): the sample typed into a Collection's boxes and a
 * Table's grid while building, rows added and deleted across the tables a grid shows, a
 * grid drawn as its Table's options say,
 * an output box showing what pricing the sample gave, dimmed once the sample has moved
 * on, a Table's output columns matched to its rows by key, in Preview the quote
 * typed apart from the sample with the cells that break their column's rules marked, and
 * a Heading drawn as its text.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react"
import type { ReactNode } from "react"
import useWorkbenchFormStore from "../../stores/useWorkbenchFormStore"
import useWorkbenchPreviewStore from "../../stores/useWorkbenchPreviewStore"
import useWorkbenchPricingStore from "../../stores/useWorkbenchPricingStore"
import useWorkbenchViewStore from "../../stores/useWorkbenchViewStore"
import { valuesBasis } from "../../utils/sheetValues"
import { pricingBasis, shownFields, toggleField, updateSchemaColumn, updateWidget, type WidgetPatch } from "../../utils/workbenchForm"
import SheetCanvas from "../SheetCanvas"
import { SheetValuesContext, usePreviewValues, useSampleValues } from "../useSheetValues"
import WidgetBody from "../WidgetBody"
import { column, currentForm, headingWidget, loadForm, releasePointer, sheetForm, stubLayout } from "./fixtures"

const widget = (id: string) => {
  const found = currentForm().pages[0].widgets.find((candidate) => candidate.id === id)
  if (found === undefined) throw new Error(`no ${id}`)
  return found
}

/** The sheet's values around a component: the sample, or the quote in Preview. */
function OnSheet({ preview = false, children }: { preview?: boolean; children: ReactNode }) {
  const sample = useSampleValues()
  const quote = usePreviewValues()
  return <SheetValuesContext value={preview ? quote : sample}>{children}</SheetValuesContext>
}

const body = (id: string, preview = false) => (
  <OnSheet preview={preview}>
    <WidgetBody widget={widget(id)} fields={shownFields(currentForm(), widget(id))} />
  </OnSheet>
)

function renderBody(id: string, preview = false) {
  return render(body(id, preview))
}

describe("WidgetBody", () => {
  beforeEach(() => {
    stubLayout()
    loadForm()
    useWorkbenchPricingStore.setState({ price: null, error: null })
    useWorkbenchPreviewStore.setState({ quote: {}, checked: false, price: null, error: null, pricing: false })
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

  it("types the sample into a Table's grid row by row, adding and deleting rows in every table it shows", () => {
    renderBody("w_grid")
    expect(screen.getAllByRole("row")).toHaveLength(4)

    const item = screen.getByRole("textbox", { name: "Item row 2" })
    fireEvent.change(item, { target: { value: "Crane" } })
    fireEvent.keyDown(item, { key: "Enter" })
    expect(currentForm().sample.equipment).toEqual([{}, { "equipment.item": "Crane" }])

    fireEvent.click(screen.getByRole("button", { name: "Add row" }))
    expect(currentForm().sample.equipment).toEqual([{}, { "equipment.item": "Crane" }, {}, {}])
    expect(screen.getAllByRole("row")).toHaveLength(5)

    fireEvent.click(screen.getByRole("button", { name: "Delete row 1" }))
    expect(currentForm().sample.equipment).toEqual([{ "equipment.item": "Crane" }, {}, {}])
    expect(screen.getAllByRole("row")).toHaveLength(4)
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
    expect(currentForm().sample.equipment).toEqual([{ "equipment.value": "250000" }])
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
    form.sample = { equipment: [{ "equipment.item": "Crane", "equipment.value": "1234567.4" }] }
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
    expect(currentForm().sample.equipment).toEqual([{ "equipment.item": "Crane", "equipment.value": "2500" }])
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
    expect(currentForm().sample.equipment).toEqual([{ "equipment.value": "0.125" }])
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

  it("in Preview, types the quote apart from the sample, and marks the cells that break their column's rules once checked", () => {
    useWorkbenchViewStore.setState({ section: "preview" })
    const { rerender } = renderBody("w_boxes", true)

    fireEvent.change(screen.getByRole("combobox", { name: "State" }), { target: { value: "NY" } })
    expect(useWorkbenchPreviewStore.getState().quote).toEqual({ policy: [{ "policy.state": "NY" }] })
    expect(currentForm().sample).toEqual({})
    expect(useWorkbenchFormStore.getState().undoStack).toEqual([])
    expect(screen.getByRole("combobox", { name: "State" })).not.toHaveAttribute("aria-invalid")

    // Price pressed with the state blank: the required cell is marked, and cleared as soon as it is filled.
    useWorkbenchPreviewStore.setState({ quote: {}, checked: true })
    rerender(body("w_boxes", true))
    const state = screen.getByRole("combobox", { name: "State" })
    expect(state).toHaveAttribute("aria-invalid", "true")
    expect(state).toHaveAttribute("title", "Required")
    fireEvent.change(state, { target: { value: "CA" } })
    rerender(body("w_boxes", true))
    expect(screen.getByRole("combobox", { name: "State" })).not.toHaveAttribute("aria-invalid")

    // An output box shows the quote's price, not the sample's.
    loadForm(toggleField(sheetForm(), "w_boxes", { table: "premiums", column: "premiums.premium" }))
    useWorkbenchPricingStore.setState({ price: { basis: pricingBasis(currentForm()), sample: {}, tables: { premiums: [{ premium: 1 }] } } })
    useWorkbenchPreviewStore.setState({
      quote: { policy: [{ "policy.state": "CA" }] },
      price: { basis: valuesBasis(currentForm().schema, { policy: [{ "policy.state": "CA" }] }), sample: {}, tables: { premiums: [{ premium: 777 }] } },
    })
    cleanup()
    renderBody("w_boxes", true)
    expect(screen.getByTestId("priced-premiums:premiums.premium")).toHaveTextContent("777")
  })

  it("selects the component when a cell is pressed, without moving it", () => {
    render(<SheetCanvas />)
    const frame = screen.getByTestId("sheet-widget-w_boxes")
    const cell = within(frame).getByRole("combobox", { name: "State" })

    cell.dispatchEvent(new MouseEvent("pointerdown", { bubbles: true, button: 0, clientX: 100, clientY: 100 }))
    window.dispatchEvent(new MouseEvent("pointermove", { bubbles: true, clientX: 160, clientY: 140 }))
    window.dispatchEvent(new MouseEvent("pointerup", { bubbles: true, clientX: 160, clientY: 140 }))

    expect(useWorkbenchViewStore.getState().selectedId).toBe("w_boxes")
    expect(widget("w_boxes")).toMatchObject({ x: 16, y: 16 })
    expect(useWorkbenchFormStore.getState().undoStack).toEqual([])
  })

  it("draws a Heading as its text alone, a heading of its size's level, and without text asks for some while building and shows nothing in Preview", () => {
    const form = sheetForm()
    form.pages[0].widgets.push(headingWidget("w_head", "Equipment schedule"))
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
    rerender(body("w_head", true))
    expect(heading(4)).toBeInTheDocument()
    preview(false)

    // Only spaces are no text: it asks for some on one line while building, and is nothing in Preview.
    act(() => useWorkbenchFormStore.getState().change((spec) => updateWidget(spec, "w_head", { text: "  " })))
    rerender(body("w_head"))
    expect(screen.queryByRole("heading")).not.toBeInTheDocument()
    expect(screen.getByText("Type its text in the panel on the right")).toHaveClass("truncate")
    preview(true)
    rerender(body("w_head", true))
    expect(container).toBeEmptyDOMElement()
  })

  it("draws a Heading's description under it, both aligned as it says, and no description while it has no text", () => {
    const form = sheetForm()
    form.pages[0].widgets.push(headingWidget("w_head", "Equipment schedule", { description: "Each item insured, with its value" }))
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
    rerender(body("w_head", true))
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
})
