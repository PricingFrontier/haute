/**
 * A component's cells (specs/workbench): the sample typed into a Collection's boxes and a
 * Table's grid while building, rows added and deleted across the tables a grid shows,
 * an output box showing what pricing the sample gave, dimmed once the sample has moved
 * on, a Table's output columns matched to its rows by key, and in Preview the quote
 * typed apart from the sample with the cells that break their column's rules marked.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react"
import type { ReactNode } from "react"
import useWorkbenchFormStore from "../../stores/useWorkbenchFormStore"
import useWorkbenchPreviewStore from "../../stores/useWorkbenchPreviewStore"
import useWorkbenchPricingStore from "../../stores/useWorkbenchPricingStore"
import useWorkbenchViewStore from "../../stores/useWorkbenchViewStore"
import { valuesBasis } from "../../utils/sheetValues"
import { pricingBasis, shownFields, toggleField } from "../../utils/workbenchForm"
import SheetCanvas from "../SheetCanvas"
import { SheetValuesContext, usePreviewValues, useSampleValues } from "../useSheetValues"
import WidgetBody from "../WidgetBody"
import { currentForm, loadForm, releasePointer, sheetForm, stubLayout } from "./fixtures"

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
})
