/**
 * A sheet's, a section's or a component's Show when (specs/workbench): "Always" without
 * conditions; a condition added on the first one-row input column; its column, test and
 * value chosen, the value the control its column's cell is; conditions removed; all, any or
 * at least a number of two or more; groups of conditions; a many-row table's column in any or every row, and
 * its number of rows; a condition whose column is gone or whose table switched between one
 * row and many; each change one undo step.
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
const group = (n: number) => screen.getByRole("group", { name: `Group ${n}` })
const options = (select: HTMLElement) => within(select).getAllByRole("option").map((option) => option.textContent)

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

  it("takes a date condition's day once it is committed, not as each digit of its year is typed", () => {
    render(<Editor />)
    fireEvent.click(screen.getByRole("button", { name: "Add condition" }))
    fireEvent.change(screen.getByRole("combobox", { name: "Condition 1 column" }), { target: { value: "policy:policy.start" } })
    const steps = undoSteps()
    const day = screen.getByLabelText("Condition 1 value")

    // A year typed digit by digit reads as 0002, 0020, 0202 and then 2026.
    for (const value of ["0002-01-01", "0020-01-01", "0202-01-01", "2026-01-01"]) fireEvent.change(day, { target: { value } })
    expect(undoSteps()).toBe(steps)
    fireEvent.blur(day)

    expect(showWhen().conditions[0]).toMatchObject({ values: ["2026-01-01"] })
    expect(undoSteps()).toBe(steps + 1)
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
    expect(showWhen().conditions[0]).toMatchObject({ values: ["CA", "NY"] })
    fireEvent.click(screen.getByRole("checkbox", { name: "NY" }))
    expect(showWhen().conditions[0]).toMatchObject({ values: ["CA"] })
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
    const day = screen.getByLabelText("Condition 1 value")
    fireEvent.change(day, { target: { value: "2026-04-01" } })
    fireEvent.blur(day)
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

  it("asks for at least a number of conditions, from 1 to as many as there are, saying so while one is left", () => {
    render(<Editor />)
    for (let n = 0; n < 3; n++) fireEvent.click(screen.getByRole("button", { name: "Add condition" }))
    expect(screen.queryByRole("combobox", { name: "How many conditions must hold" })).not.toBeInTheDocument()

    fireEvent.change(screen.getByRole("combobox", { name: "Which conditions must hold" }), { target: { value: "at_least" } })
    expect(showWhen().match).toBe(2)
    const howMany = screen.getByRole("combobox", { name: "How many conditions must hold" })
    expect(options(howMany)).toEqual(["1", "2", "3"])
    fireEvent.change(howMany, { target: { value: "3" } })
    expect(showWhen().match).toBe(3)
    expect(undoSteps()).toBe(5)

    // Under at least, a new group needs all, and a group asks for at least a number as the Show when does.
    fireEvent.click(screen.getByRole("button", { name: "Add group" }))
    expect(showWhen().conditions[3]).toEqual({ match: "all", conditions: [showWhen().conditions[0]] })
    fireEvent.click(within(group(4)).getByRole("button", { name: "Add condition to group 4" }))
    fireEvent.change(screen.getByRole("combobox", { name: "Which conditions of group 4 must hold" }), { target: { value: "at_least" } })
    expect(showWhen().conditions[3]).toMatchObject({ match: 2 })
    expect(options(screen.getByRole("combobox", { name: "How many conditions of group 4 must hold" }))).toEqual(["1", "2"])
    fireEvent.click(screen.getByRole("button", { name: "Remove group 4" }))

    // Fewer conditions than it asks for: the number stays, to be put right, and reads so with one left.
    fireEvent.click(within(condition(3)).getByRole("button", { name: "Remove condition 3" }))
    fireEvent.click(within(condition(2)).getByRole("button", { name: "Remove condition 2" }))
    expect(showWhen().match).toBe(3)
    expect(options(screen.getByRole("combobox", { name: "How many conditions must hold" }))).toEqual(["1", "2", "3"])
    fireEvent.change(screen.getByRole("combobox", { name: "Which conditions must hold" }), { target: { value: "all" } })
    expect(showWhen().match).toBe("all")
    expect(screen.queryByRole("combobox", { name: "Which conditions must hold" })).not.toBeInTheDocument()
  })

  it("adds a group needing what the Show when does not, with its own conditions, match and removal, each one undo step", () => {
    render(<Editor />)
    const fresh = { table: "policy", column: "policy.state", op: "equals", values: [] }
    fireEvent.click(screen.getByRole("button", { name: "Add group" }))
    expect(showWhen()).toEqual({ match: "all", conditions: [{ match: "any", conditions: [fresh] }] })
    expect(undoSteps()).toBe(1)
    expect(screen.queryByRole("combobox", { name: "Which conditions of group 1 must hold" })).not.toBeInTheDocument()

    fireEvent.click(within(group(1)).getByRole("button", { name: "Add condition to group 1" }))
    expect(within(group(1)).getByText("of these are true")).toBeInTheDocument()
    fireEvent.change(screen.getByRole("combobox", { name: "Which conditions of group 1 must hold" }), { target: { value: "all" } })
    fireEvent.change(screen.getByRole("combobox", { name: "Group 1 condition 2 column" }), { target: { value: "policy:policy.year" } })
    expect(showWhen()).toEqual({ match: "all", conditions: [{ match: "all", conditions: [fresh, { table: "policy", column: "policy.year", op: "equals", values: [] }] }] })
    expect(undoSteps()).toBe(4)

    // Under a Show when needing any, a new group needs all.
    fireEvent.click(screen.getByRole("button", { name: "Add condition" }))
    fireEvent.change(screen.getByRole("combobox", { name: "Which conditions must hold" }), { target: { value: "any" } })
    fireEvent.click(screen.getByRole("button", { name: "Add group" }))
    expect(showWhen().conditions[2]).toEqual({ match: "all", conditions: [fresh] })

    // Its last condition removed, a group goes with it; its cross removes it whole.
    fireEvent.click(within(group(3)).getByRole("button", { name: "Remove group 3 condition 1" }))
    expect(showWhen().conditions).toHaveLength(2)
    fireEvent.click(screen.getByRole("button", { name: "Remove group 1" }))
    expect(showWhen()).toEqual({ match: "any", conditions: [fresh] })
  })

  it("tests a many-row table's column in any row or every row, and counts its rows in a whole number", () => {
    render(<Editor />)
    fireEvent.click(screen.getByRole("button", { name: "Add condition" }))
    const column = screen.getByRole("combobox", { name: "Condition 1 column" })
    expect(screen.queryByRole("combobox", { name: "Condition 1 rows" })).not.toBeInTheDocument()

    fireEvent.change(column, { target: { value: "equipment:equipment.value" } })
    expect(showWhen().conditions[0]).toEqual({ table: "equipment", column: "equipment.value", rows: "any", op: "equals", values: [] })
    const rows = screen.getByRole("combobox", { name: "Condition 1 rows" })
    expect(options(rows)).toEqual(["Any row", "Every row"])
    fireEvent.change(rows, { target: { value: "every" } })
    expect(showWhen().conditions[0]).toMatchObject({ rows: "every" })

    fireEvent.change(column, { target: { value: "equipment#rows" } })
    expect(showWhen().conditions[0]).toEqual({ table: "equipment", op: "at_least", count: 1 })
    expect(column).toHaveDisplayValue("Number of rows in equipment")
    expect(screen.queryByRole("combobox", { name: "Condition 1 rows" })).not.toBeInTheDocument()
    expect(options(screen.getByRole("combobox", { name: "Condition 1 test" }))).toEqual([
      "equals",
      "does not equal",
      "is greater than",
      "is at least",
      "is less than",
      "is at most",
    ])
    const count = screen.getByRole("textbox", { name: "Condition 1 value" })
    const steps = undoSteps()
    fireEvent.change(count, { target: { value: "2.5" } })
    fireEvent.blur(count)
    expect(screen.getByText("Enter a whole number of rows, 0 or more.")).toBeInTheDocument()
    expect(undoSteps()).toBe(steps)
    fireEvent.change(count, { target: { value: "3" } })
    fireEvent.blur(count)
    expect(showWhen().conditions[0]).toEqual({ table: "equipment", op: "at_least", count: 3 })
    expect(undoSteps()).toBe(steps + 1)

    // Moved to a column, it keeps its test while the column's type takes it, with no value.
    fireEvent.change(column, { target: { value: "policy:policy.year" } })
    expect(showWhen().conditions[0]).toEqual({ table: "policy", column: "policy.year", op: "at_least", values: [] })
  })

  it("fixes a condition whose table switched between one row and many from its rows select", () => {
    const form = currentForm()
    widgetsOf(form)[0].show_when = {
      match: "all",
      conditions: [
        { table: "equipment", column: "equipment.value", op: "filled", values: [] },
        { table: "policy", column: "policy.year", rows: "every", op: "filled", values: [] },
      ],
    }
    loadForm(form)
    render(<Editor />)

    expect(within(condition(1)).getByText("Tests value, which is in a table with many rows")).toBeInTheDocument()
    const first = screen.getByRole("combobox", { name: "Condition 1 rows" })
    expect(first).toHaveDisplayValue("Choose…")
    fireEvent.change(first, { target: { value: "any" } })
    expect(showWhen().conditions[0]).toEqual({ table: "equipment", column: "equipment.value", rows: "any", op: "filled", values: [] })
    expect(within(condition(1)).queryByText(/Tests value/)).not.toBeInTheDocument()

    expect(within(condition(2)).getByText("Tests year in every row, which is in a table with one row")).toBeInTheDocument()
    const second = screen.getByRole("combobox", { name: "Condition 2 rows" })
    expect(options(second)).toEqual(["The row", "Any row", "Every row"])
    fireEvent.change(second, { target: { value: "one" } })
    expect(showWhen().conditions[1]).toEqual({ table: "policy", column: "policy.year", op: "filled", values: [] })
    expect(screen.queryByRole("combobox", { name: "Condition 2 rows" })).not.toBeInTheDocument()
  })

  it("keeps a condition whose column is gone, saying so", () => {
    const form = currentForm()
    widgetsOf(form)[0].show_when = { match: "all", conditions: [{ table: "policy", column: "policy.gone", op: "filled", values: [] }] }
    loadForm(form)
    render(<Editor />)

    expect(screen.getByRole("combobox", { name: "Condition 1 column" })).toHaveDisplayValue("Missing column")
    expect(within(condition(1)).getByText("Tests a column that is no longer in the schema")).toBeInTheDocument()
  })

  it("adds nothing while no input table has a column to test, saying why", () => {
    const form = currentForm()
    form.schema.tables = [table("equipment", "equipment", { rows: "many", columns: [column("equipment.row", "row", { type: "int", index: true })] })]
    loadForm(form)
    render(<Editor />)

    expect(screen.getByRole("button", { name: "Add condition" })).toBeDisabled()
    expect(screen.getByRole("button", { name: "Add group" })).toBeDisabled()
    expect(screen.getByText("Conditions test input tables; the schema has none with a column to test.")).toBeInTheDocument()
  })
})
