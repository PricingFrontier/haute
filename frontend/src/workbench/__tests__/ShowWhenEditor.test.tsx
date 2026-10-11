/**
 * A sheet's, a section's or a component's Show when (specs/workbench): "Always" without
 * conditions; a condition added on the first one-row input column; its column, test and
 * value chosen, the value the control its column's cell is; conditions removed; all or any
 * of two or more; a condition whose column is gone; each change one undo step.
 */
import { afterEach, beforeEach, describe, expect, it } from "vitest"
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react"
import useWorkbenchFormStore from "../../stores/useWorkbenchFormStore"
import { updateWidget } from "../../utils/workbenchForm"
import { ShowWhenEditor } from "../ShowWhenEditor"
import { column, coverForm, currentForm, loadForm, table, widgetsOf } from "./fixtures"

/** The Collection on the first sheet, whose Show when the editor edits through the store. */
function Editor() {
  const form = useWorkbenchFormStore((s) => s.form)
  if (form === null) throw new Error("no form")
  const widget = widgetsOf(form)[0]
  return (
    <ShowWhenEditor
      form={form}
      showWhen={widget.show_when}
      onChange={(next) => useWorkbenchFormStore.getState().change((spec) => updateWidget(spec, widget.id, { show_when: next }))}
    />
  )
}

const showWhen = () => widgetsOf(currentForm())[0].show_when
const undoSteps = () => useWorkbenchFormStore.getState().undoStack.length
const condition = (n: number) => screen.getByRole("group", { name: `Condition ${n}` })

describe("ShowWhenEditor", () => {
  beforeEach(() => {
    const form = coverForm()
    form.schema.tables[0].columns.push(column("policy.name", "name"), column("policy.start", "start", { type: "date" }))
    loadForm(form)
  })
  afterEach(cleanup)

  it("reads Always without conditions, and adds one on the first one-row input column as one undo step", () => {
    render(<Editor />)
    expect(screen.getByText("Always")).toBeInTheDocument()

    fireEvent.click(screen.getByRole("button", { name: "Add condition" }))

    expect(showWhen()).toEqual({ match: "all", conditions: [{ table: "policy", column: "policy.state", op: "equals", values: [] }] })
    expect(undoSteps()).toBe(1)
    expect(screen.queryByText("Always")).not.toBeInTheDocument()
    expect(within(condition(1)).getByText("Needs a value for its test of state")).toBeInTheDocument()
  })

  it("chooses a value from the allowed values, and a box for each of them for one of many", () => {
    render(<Editor />)
    fireEvent.click(screen.getByRole("button", { name: "Add condition" }))

    fireEvent.change(screen.getByRole("combobox", { name: "Condition 1 value" }), { target: { value: "NY" } })
    expect(showWhen().conditions[0]).toMatchObject({ op: "equals", values: ["NY"] })
    expect(within(condition(1)).queryByText(/Needs a value/)).not.toBeInTheDocument()

    fireEvent.change(screen.getByRole("combobox", { name: "Condition 1 test" }), { target: { value: "one_of" } })
    expect(showWhen().conditions[0]).toMatchObject({ op: "one_of", values: ["NY"] })
    fireEvent.click(screen.getByRole("checkbox", { name: "CA" }))
    expect(showWhen().conditions[0].values).toEqual(["CA", "NY"])
    fireEvent.click(screen.getByRole("checkbox", { name: "NY" }))
    expect(showWhen().conditions[0].values).toEqual(["CA"])
    expect(undoSteps()).toBe(5)
  })

  it("moves a condition to another column, keeping the test while the column's type takes it and the values while they fit", () => {
    render(<Editor />)
    fireEvent.click(screen.getByRole("button", { name: "Add condition" }))
    fireEvent.change(screen.getByRole("combobox", { name: "Condition 1 value" }), { target: { value: "NY" } })

    fireEvent.change(screen.getByRole("combobox", { name: "Condition 1 column" }), { target: { value: "policy:policy.year" } })
    expect(showWhen().conditions[0]).toEqual({ table: "policy", column: "policy.year", op: "equals", values: [] })
    fireEvent.change(screen.getByRole("combobox", { name: "Condition 1 column" }), { target: { value: "policy:policy.cover" } })
    expect(showWhen().conditions[0]).toEqual({ table: "policy", column: "policy.cover", op: "ticked", values: [] })
    // A tick box's tests take no value, so none is asked for.
    expect(screen.getAllByRole("option", { name: "is not ticked" })).toHaveLength(1)
    expect(screen.queryByRole("textbox", { name: "Condition 1 value" })).not.toBeInTheDocument()
  })

  it("takes a number, a day and values typed comma-separated in the controls their columns' cells are", () => {
    render(<Editor />)
    fireEvent.click(screen.getByRole("button", { name: "Add condition" }))
    const column = screen.getByRole("combobox", { name: "Condition 1 column" })

    fireEvent.change(column, { target: { value: "policy:policy.year" } })
    fireEvent.change(screen.getByRole("combobox", { name: "Condition 1 test" }), { target: { value: "above" } })
    const number = screen.getByRole("textbox", { name: "Condition 1 value" })
    expect(number).toHaveAttribute("inputmode", "decimal")
    fireEvent.change(number, { target: { value: " 2020 " } })
    fireEvent.blur(number)
    expect(showWhen().conditions[0]).toMatchObject({ op: "above", values: ["2020"] })

    fireEvent.change(column, { target: { value: "policy:policy.start" } })
    expect(screen.getAllByRole("option", { name: "is after" })).toHaveLength(1)
    fireEvent.change(screen.getByLabelText("Condition 1 value"), { target: { value: "2026-04-01" } })
    expect(showWhen().conditions[0]).toMatchObject({ column: "policy.start", op: "above", values: ["2026-04-01"] })

    fireEvent.change(column, { target: { value: "policy:policy.name" } })
    fireEvent.change(screen.getByRole("combobox", { name: "Condition 1 test" }), { target: { value: "not_one_of" } })
    const values = screen.getByRole("textbox", { name: "Condition 1 value" })
    fireEvent.change(values, { target: { value: "Acme,  , Brown Ltd " } })
    fireEvent.blur(values)
    expect(showWhen().conditions[0]).toMatchObject({ op: "not_one_of", values: ["Acme", "Brown Ltd"] })
  })

  it("removes a condition, and with two or more asks whether all or any must hold", () => {
    render(<Editor />)
    fireEvent.click(screen.getByRole("button", { name: "Add condition" }))
    expect(screen.queryByRole("combobox", { name: "Which conditions must hold" })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", { name: "Add condition" }))

    expect(screen.getByText("of these are true")).toBeInTheDocument()
    fireEvent.change(screen.getByRole("combobox", { name: "Which conditions must hold" }), { target: { value: "any" } })
    expect(showWhen().match).toBe("any")

    fireEvent.click(within(condition(2)).getByRole("button", { name: "Remove condition 2" }))
    expect(showWhen()).toMatchObject({ match: "any", conditions: [{ column: "policy.state" }] })
  })

  it("keeps a condition whose column is gone, saying so", () => {
    const form = currentForm()
    widgetsOf(form)[0].show_when = { match: "all", conditions: [{ table: "policy", column: "policy.gone", op: "filled", values: [] }] }
    loadForm(form)
    render(<Editor />)

    expect(screen.getByRole("combobox", { name: "Condition 1 column" })).toHaveDisplayValue("Missing column")
    expect(within(condition(1)).getByText("Tests a column that is no longer in the schema")).toBeInTheDocument()
  })

  it("adds nothing while no one-row input table has a column, saying why", () => {
    const form = currentForm()
    form.schema.tables = [table("equipment", "equipment", { rows: "many", columns: [column("equipment.item", "item", { key: true })] })]
    loadForm(form)
    render(<Editor />)

    expect(screen.getByRole("button", { name: "Add condition" })).toBeDisabled()
    expect(screen.getByText("Conditions test one-row input tables; the schema has none with a column.")).toBeInTheDocument()
  })
})
