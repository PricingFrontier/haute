/**
 * A component's cells while building (specs/workbench): the sample typed into a
 * Collection's boxes and a Table's grid, rows added and deleted across the tables a grid
 * shows, and an output box showing what pricing the sample gave, dimmed once the sample
 * has moved on.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react"
import useWorkbenchFormStore from "../../stores/useWorkbenchFormStore"
import useWorkbenchPricingStore from "../../stores/useWorkbenchPricingStore"
import useWorkbenchViewStore from "../../stores/useWorkbenchViewStore"
import { pricingBasis, shownFields, toggleField } from "../../utils/workbenchForm"
import SheetCanvas from "../SheetCanvas"
import WidgetBody from "../WidgetBody"
import { currentForm, loadForm, releasePointer, sheetForm, stubLayout } from "./fixtures"

const widget = (id: string) => {
  const found = currentForm().pages[0].widgets.find((candidate) => candidate.id === id)
  if (found === undefined) throw new Error(`no ${id}`)
  return found
}

function renderBody(id: string) {
  const form = currentForm()
  return render(<WidgetBody widget={widget(id)} fields={shownFields(form, widget(id))} />)
}

describe("WidgetBody", () => {
  beforeEach(() => {
    stubLayout()
    loadForm()
    useWorkbenchPricingStore.setState({ price: null, error: null })
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

    useWorkbenchPricingStore.setState({ price: { basis: pricingBasis(form), tables: { premiums: [{ premium: 1234.5 }] } } })
    rerender(<WidgetBody widget={widget("w_boxes")} fields={shownFields(currentForm(), widget("w_boxes"))} />)
    expect(box()).toHaveTextContent("1,234.5")
    expect(box()).not.toHaveAttribute("data-stale")

    fireEvent.change(screen.getByRole("combobox", { name: "State" }), { target: { value: "CA" } })
    rerender(<WidgetBody widget={widget("w_boxes")} fields={shownFields(currentForm(), widget("w_boxes"))} />)
    expect(box()).toHaveAttribute("data-stale", "true")
    expect(box()).toHaveStyle({ opacity: "0.5" })

    useWorkbenchPricingStore.setState({ price: { basis: pricingBasis(currentForm()), tables: { premiums: [{ premium: null }] } } })
    rerender(<WidgetBody widget={widget("w_boxes")} fields={shownFields(currentForm(), widget("w_boxes"))} />)
    expect(box()).toHaveTextContent("—")
    expect(box()).not.toHaveAttribute("data-stale")
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
