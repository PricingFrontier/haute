/**
 * A sheet's, a section's or a component's Show when (specs/workbench): "Always" without
 * conditions; a condition added on the first one-row input column; its column, test and
 * value chosen, the value the control its column's cell is; conditions removed; all, any or
 * at least a number of two or more; groups of conditions; a many-row table's rows in any
 * or every row passing tests of a row, how many and a total over them; a condition whose
 * column is gone or whose table switched between one row and many; each change one undo
 * step.
 */
import { afterEach, beforeEach, describe, expect, it } from "vitest"
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react"
import useWorkbenchFormStore from "../../stores/useWorkbenchFormStore"
import { takesNothingTyped } from "../../utils/sheetVisibility"
import { updateWidget } from "../../utils/workbenchForm"
import { ShowWhenEditor } from "../ShowWhenEditor"
import { addTo, column, coverForm, currentForm, headingWidget, loadForm, table, widgetsOf } from "./fixtures"

/** The Collection on the first sheet, whose Show when the editor edits through the store. */
function Editor() {
  const form = useWorkbenchFormStore((s) => s.form)
  if (form === null) throw new Error("no form")
  const widget = widgetsOf(form)[0]
  return (
    <ShowWhenEditor
      form={form}
      showWhen={widget.show_when}
      priced={takesNothingTyped(form, widget)}
      onChange={(next) => useWorkbenchFormStore.getState().change((spec) => updateWidget(spec, widget.id, { show_when: next }))}
    />
  )
}

/** The Show when of the first sheet's component at `at`, as its panel edits it. */
function EditorOf({ at }: { at: number }) {
  const form = useWorkbenchFormStore((s) => s.form)
  if (form === null) throw new Error("no form")
  const widget = widgetsOf(form)[at]
  return (
    <ShowWhenEditor
      form={form}
      showWhen={widget.show_when}
      priced={takesNothingTyped(form, widget)}
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

    expect(showWhen()).toEqual({ match: "all", conditions: [{ table: "policy", column: "policy.state", op: "equals", values: [], field: null, days: 0 }] })
    expect(undoSteps()).toBe(1)
    expect(screen.queryByText("Always")).not.toBeInTheDocument()
    expect(within(condition(1)).getByText("Needs a value for its test of state")).toBeInTheDocument()
  })

  it("never tests the quote's reference or option, leading a table: a new condition takes the column after them", () => {
    const form = currentForm()
    form.schema.tables[0].columns.unshift(column("quote_reference", "reference"), column("quote_option", "option", { type: "int" }))
    loadForm(form)
    render(<Editor />)

    fireEvent.click(screen.getByRole("button", { name: "Add condition" }))

    expect(showWhen().conditions).toEqual([{ table: "policy", column: "policy.state", op: "equals", values: [], field: null, days: 0 }])
    const offered = options(screen.getByRole("combobox", { name: "Condition 1 column" }))
    expect(offered.filter((name) => /reference|option/i.test(name ?? ""))).toEqual([])
    expect(offered.length).toBeGreaterThan(0)
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
    expect(showWhen().conditions[0]).toMatchObject({ op: "equals", values: ["NY"], field: null, days: 0 })
    expect(within(condition(1)).queryByText(/Needs a value/)).not.toBeInTheDocument()

    fireEvent.change(screen.getByRole("combobox", { name: "Condition 1 test" }), { target: { value: "one_of" } })
    expect(showWhen().conditions[0]).toMatchObject({ op: "one_of", values: ["NY"], field: null, days: 0 })
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
    expect(showWhen().conditions[0]).toEqual({ table: "policy", column: "policy.year", op: "equals", values: [], field: null, days: 0 })
    fireEvent.change(screen.getByRole("combobox", { name: "Condition 1 column" }), { target: { value: "policy:policy.cover" } })
    expect(showWhen().conditions[0]).toEqual({ table: "policy", column: "policy.cover", op: "ticked", values: [], field: null, days: 0 })
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
    expect(showWhen().conditions[0]).toMatchObject({ op: "above", values: ["2020"], field: null, days: 0 })

    fireEvent.change(column, { target: { value: "policy:policy.start" } })
    expect(screen.getAllByRole("option", { name: "is after" })).toHaveLength(1)
    const day = screen.getByLabelText("Condition 1 value")
    fireEvent.change(day, { target: { value: "2026-04-01" } })
    fireEvent.blur(day)
    expect(showWhen().conditions[0]).toMatchObject({ column: "policy.start", op: "above", values: ["2026-04-01"], field: null, days: 0 })

    fireEvent.change(column, { target: { value: "policy:policy.name" } })
    fireEvent.change(screen.getByRole("combobox", { name: "Condition 1 test" }), { target: { value: "not_one_of" } })
    const values = screen.getByRole("textbox", { name: "Condition 1 value" })
    fireEvent.change(values, { target: { value: "Acme,  , Brown Ltd " } })
    fireEvent.blur(values)
    expect(showWhen().conditions[0]).toMatchObject({ op: "not_one_of", values: ["Acme", "Brown Ltd"], field: null, days: 0 })
  })

  it("refuses a number written with its thousands separator in a number column's list, which the comma would split", () => {
    render(<Editor />)
    fireEvent.click(screen.getByRole("button", { name: "Add condition" }))
    fireEvent.change(screen.getByRole("combobox", { name: "Condition 1 column" }), { target: { value: "policy:policy.year" } })
    fireEvent.change(screen.getByRole("combobox", { name: "Condition 1 test" }), { target: { value: "one_of" } })
    const values = screen.getByRole("textbox", { name: "Condition 1 value" })
    const steps = undoSteps()

    for (const typed of ["1,000", "2020, 1,000,000.5"]) {
      fireEvent.change(values, { target: { value: typed } })
      fireEvent.blur(values)
      expect(values).toHaveValue(typed)
      expect(showWhen().conditions[0]).toMatchObject({ op: "one_of", values: [], field: null, days: 0 })
      expect(undoSteps()).toBe(steps)
    }
    expect(screen.getByText("Write 1000000.5, not 1,000,000.5: a comma separates the values.")).toBeInTheDocument()

    fireEvent.change(values, { target: { value: "1000, 2500,10" } })
    fireEvent.blur(values)
    expect(showWhen().conditions[0]).toMatchObject({ op: "one_of", values: ["1000", "2500", "10"], field: null, days: 0 })
    expect(undoSteps()).toBe(steps + 1)
  })

  it("tests a column against another field of its type, a Date's with days added, and goes back to a value", () => {
    const form = coverForm()
    form.schema.tables[0].columns.push(column("policy.name", "name"), column("policy.start", "start", { type: "date" }), column("policy.end", "end", { type: "date" }))
    loadForm(form)
    render(<Editor />)
    fireEvent.click(screen.getByRole("button", { name: "Add condition" }))
    fireEvent.change(screen.getByRole("combobox", { name: "Condition 1 column" }), { target: { value: "policy:policy.end" } })
    fireEvent.change(screen.getByRole("combobox", { name: "Condition 1 test" }), { target: { value: "above" } })
    const against = screen.getByRole("combobox", { name: "Condition 1 against" })
    // A Date is offered the other Dates alone, never itself.
    expect(options(against)).toEqual(["Value", "Start"])
    const steps = undoSteps()

    fireEvent.change(against, { target: { value: "policy:policy.start" } })
    const later = { table: "policy", column: "policy.end", op: "above", values: [], field: { table: "policy", column: "policy.start" }, days: 0 }
    expect(showWhen().conditions[0]).toEqual(later)
    expect(screen.queryByLabelText("Condition 1 value")).not.toBeInTheDocument()
    const days = screen.getByRole("textbox", { name: "Condition 1 days" })
    fireEvent.change(days, { target: { value: "365" } })
    fireEvent.blur(days)
    expect(showWhen().conditions[0]).toEqual({ ...later, days: 365 })
    fireEvent.change(days, { target: { value: "40000" } })
    fireEvent.blur(days)
    expect(screen.getByText("Enter a whole number of days from -36500 to 36500.")).toBeInTheDocument()
    expect(showWhen().conditions[0]).toEqual({ ...later, days: 365 })
    fireEvent.change(days, { target: { value: "-30" } })
    fireEvent.blur(days)
    expect(showWhen().conditions[0]).toEqual({ ...later, days: -30 })
    expect(undoSteps()).toBe(steps + 3)

    // Back to a value typed, the field and its days let go.
    const value = (within(against).getByRole("option", { name: "Value" }) as HTMLOptionElement).value
    fireEvent.change(against, { target: { value } })
    expect(showWhen().conditions[0]).toEqual({ ...later, field: null })
    expect(screen.getByLabelText("Condition 1 value")).toBeInTheDocument()
    // Text is offered the other Text columns, and a test of no value nothing to test against.
    fireEvent.change(screen.getByRole("combobox", { name: "Condition 1 column" }), { target: { value: "policy:policy.name" } })
    fireEvent.change(screen.getByRole("combobox", { name: "Condition 1 test" }), { target: { value: "contains" } })
    expect(options(screen.getByRole("combobox", { name: "Condition 1 against" }))).toEqual(["Value", "State"])
    fireEvent.change(screen.getByRole("combobox", { name: "Condition 1 test" }), { target: { value: "filled" } })
    expect(screen.queryByRole("combobox", { name: "Condition 1 against" })).not.toBeInTheDocument()
  })

  it("tests a row's column against another column of the same row", () => {
    const form = coverForm()
    form.schema.tables.find((each) => each.id === "equipment")?.columns.push(column("equipment.limit", "limit", { type: "float" }))
    loadForm(form)
    render(<Editor />)
    fireEvent.click(screen.getByRole("button", { name: "Add condition" }))
    fireEvent.change(screen.getByRole("combobox", { name: "Condition 1 column" }), { target: { value: "equipment#rows" } })
    fireEvent.change(screen.getByRole("combobox", { name: "Condition 1 where 1 column" }), { target: { value: "equipment.value" } })
    fireEvent.change(screen.getByRole("combobox", { name: "Condition 1 where 1 test" }), { target: { value: "above" } })
    const against = screen.getByRole("combobox", { name: "Condition 1 where 1 against" })
    expect(options(against)).toEqual(["Value", "Limit"])

    fireEvent.change(against, { target: { value: "equipment.limit" } })

    const test = { column: "equipment.value", op: "above", values: [], field: "equipment.limit", days: 0 }
    expect(showWhen().conditions[0]).toEqual({ table: "equipment", rows: "any", where: [test] })
    // Moved to a column of another type, its test is no longer against the limit.
    fireEvent.change(screen.getByRole("combobox", { name: "Condition 1 where 1 column" }), { target: { value: "equipment.item" } })
    expect(showWhen().conditions[0]).toEqual({ table: "equipment", rows: "any", where: [{ column: "equipment.item", op: "equals", values: [], field: null, days: 0 }] })
  })

  it("offers the price only on what takes nothing typed, against values typed alone, and names one elsewhere", () => {
    const form = coverForm()
    addTo(form, headingWidget("w_note", "Refer"))
    loadForm(form)
    const at = widgetsOf(form).length - 1
    const view = render(<EditorOf at={at} />)
    fireEvent.click(screen.getByRole("button", { name: "Add condition" }))
    const columns = screen.getByRole("combobox", { name: "Condition 1 column" })
    expect(within(columns).getByRole("option", { name: "Rows of premiums" })).toBeInTheDocument()

    fireEvent.change(columns, { target: { value: "premiums#rows" } })
    const item = { column: "premiums.item", op: "equals", values: [], field: null, days: 0 }
    expect(widgetsOf(currentForm())[at].show_when.conditions[0]).toEqual({ table: "premiums", rows: "any", where: [item] })
    // Tested on the price, it takes a value typed, never another field.
    expect(screen.queryByRole("combobox", { name: "Condition 1 where 1 against" })).not.toBeInTheDocument()
    view.unmount()

    // The policy's boxes take values typed: the price is not offered there, and one there is named.
    render(<Editor />)
    fireEvent.click(screen.getByRole("button", { name: "Add condition" }))
    expect(within(screen.getByRole("combobox", { name: "Condition 1 column" })).queryByRole("option", { name: "Rows of premiums" })).not.toBeInTheDocument()
    const crane = { ...item, op: "equals" as const, values: ["Crane"] }
    act(() => useWorkbenchFormStore.getState().change((spec) => updateWidget(spec, "w_boxes", { show_when: { match: "all", conditions: [{ table: "premiums", rows: "any", where: [crane] }] } })))
    expect(within(condition(1)).getByText("Tests the rows of premiums, a part of the price, which hides only what takes nothing typed: here it is never applied")).toBeInTheDocument()
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
    const fresh = { table: "policy", column: "policy.state", op: "equals", values: [], field: null, days: 0 }
    fireEvent.click(screen.getByRole("button", { name: "Add group" }))
    expect(showWhen()).toEqual({ match: "all", conditions: [{ match: "any", conditions: [fresh] }] })
    expect(undoSteps()).toBe(1)
    expect(screen.queryByRole("combobox", { name: "Which conditions of group 1 must hold" })).not.toBeInTheDocument()

    fireEvent.click(within(group(1)).getByRole("button", { name: "Add condition to group 1" }))
    expect(within(group(1)).getByText("of these are true")).toBeInTheDocument()
    fireEvent.change(screen.getByRole("combobox", { name: "Which conditions of group 1 must hold" }), { target: { value: "all" } })
    fireEvent.change(screen.getByRole("combobox", { name: "Group 1 condition 2 column" }), { target: { value: "policy:policy.year" } })
    expect(showWhen()).toEqual({ match: "all", conditions: [{ match: "all", conditions: [fresh, { table: "policy", column: "policy.year", op: "equals", values: [], field: null, days: 0 }] }] })
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

  it("tests a table's rows in any or every row passing tests of a row, counts them and totals a column over them", () => {
    render(<Editor />)
    fireEvent.click(screen.getByRole("button", { name: "Add condition" }))
    const column = screen.getByRole("combobox", { name: "Condition 1 column" })
    expect(screen.queryByRole("combobox", { name: "Condition 1 rows" })).not.toBeInTheDocument()

    fireEvent.change(column, { target: { value: "equipment#rows" } })
    const item = { column: "equipment.item", op: "equals", values: [], field: null, days: 0 }
    expect(showWhen().conditions[0]).toEqual({ table: "equipment", rows: "any", where: [item] })
    expect(column).toHaveDisplayValue("Rows of equipment")
    const rows = screen.getByRole("combobox", { name: "Condition 1 rows" })
    expect(options(rows)).toEqual(["Any row", "Every row", "Number of rows", "Sum", "Smallest", "Largest", "Average"])
    // Any row needs a test, so its only one has no cross.
    expect(screen.queryByRole("button", { name: "Remove condition 1 where 1" })).not.toBeInTheDocument()

    // A second test of the same row, then every row, each one undo step.
    fireEvent.click(screen.getByRole("button", { name: "Add test to condition 1" }))
    fireEvent.change(screen.getByRole("combobox", { name: "Condition 1 where 2 column" }), { target: { value: "equipment.value" } })
    fireEvent.change(screen.getByRole("combobox", { name: "Condition 1 where 2 test" }), { target: { value: "above" } })
    const value = screen.getByRole("textbox", { name: "Condition 1 where 2 value" })
    fireEvent.change(value, { target: { value: "1000" } })
    fireEvent.blur(value)
    fireEvent.change(rows, { target: { value: "every" } })
    const above = { column: "equipment.value", op: "above", values: ["1000"], field: null, days: 0 }
    expect(showWhen().conditions[0]).toEqual({ table: "equipment", rows: "every", where: [item, above] })
    expect(undoSteps()).toBe(7)
    fireEvent.click(screen.getByRole("button", { name: "Remove condition 1 where 1" }))
    expect(showWhen().conditions[0]).toEqual({ table: "equipment", rows: "every", where: [above] })

    // How many rows pass: a whole number committed, anything else refused.
    fireEvent.change(rows, { target: { value: "count" } })
    expect(showWhen().conditions[0]).toEqual({ table: "equipment", where: [above], op: "at_least", count: 1 })
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
    expect(showWhen().conditions[0]).toEqual({ table: "equipment", where: [above], op: "at_least", count: 3 })
    expect(undoSteps()).toBe(steps + 1)
    // A count's only test can go: without tests it counts every row.
    fireEvent.click(screen.getByRole("button", { name: "Remove condition 1 where 1" }))
    expect(showWhen().conditions[0]).toEqual({ table: "equipment", where: [], op: "at_least", count: 3 })

    // A total of a number column: a number committed, anything else refused.
    fireEvent.change(rows, { target: { value: "sum" } })
    expect(showWhen().conditions[0]).toEqual({ table: "equipment", total: "sum", column: "equipment.value", where: [], op: "above", value: "" })
    expect(options(screen.getByRole("combobox", { name: "Condition 1 total of" }))).toEqual(["Value"])
    expect(within(condition(1)).getByText("Needs a number to compare its total of value with")).toBeInTheDocument()
    const total = screen.getByRole("textbox", { name: "Condition 1 value" })
    fireEvent.change(total, { target: { value: "lots" } })
    fireEvent.blur(total)
    expect(screen.getByText("Enter a number.")).toBeInTheDocument()
    fireEvent.change(total, { target: { value: "£5,000" } })
    fireEvent.blur(total)
    fireEvent.change(rows, { target: { value: "average" } })
    expect(showWhen().conditions[0]).toEqual({ table: "equipment", total: "average", column: "equipment.value", where: [], op: "above", value: "£5,000" })

    // Moved to a one-row table's column, with no test of it, it starts on that column's first test.
    fireEvent.change(column, { target: { value: "policy:policy.year" } })
    expect(showWhen().conditions[0]).toEqual({ table: "policy", column: "policy.year", op: "equals", values: [], field: null, days: 0 })
  })

  it("puts right a condition whose table switched between one row and many from its column select", () => {
    const form = currentForm()
    widgetsOf(form)[0].show_when = {
      match: "all",
      conditions: [
        { table: "equipment", column: "equipment.value", op: "filled", values: [], field: null, days: 0 },
        { table: "policy", rows: "every", where: [{ column: "policy.year", op: "filled", values: [], field: null, days: 0 }] },
      ],
    }
    loadForm(form)
    render(<Editor />)

    expect(within(condition(1)).getByText("Tests value, which is in a table with many rows")).toBeInTheDocument()
    const first = screen.getByRole("combobox", { name: "Condition 1 column" })
    expect(first).toHaveDisplayValue("Value")
    fireEvent.change(first, { target: { value: "equipment#rows" } })
    expect(showWhen().conditions[0]).toEqual({ table: "equipment", rows: "any", where: [{ column: "equipment.value", op: "filled", values: [], field: null, days: 0 }] })
    expect(within(condition(1)).queryByText(/Tests value/)).not.toBeInTheDocument()

    expect(within(condition(2)).getByText("Tests the rows of policy, which has one row")).toBeInTheDocument()
    const second = screen.getByRole("combobox", { name: "Condition 2 column" })
    expect(second).toHaveDisplayValue("Rows of policy")
    fireEvent.change(second, { target: { value: "policy:policy.year" } })
    expect(showWhen().conditions[1]).toEqual({ table: "policy", column: "policy.year", op: "filled", values: [], field: null, days: 0 })
    expect(within(condition(2)).queryByText(/Tests the rows/)).not.toBeInTheDocument()
  })

  it("keeps a condition whose column is gone, saying so", () => {
    const form = currentForm()
    widgetsOf(form)[0].show_when = { match: "all", conditions: [{ table: "policy", column: "policy.gone", op: "filled", values: [], field: null, days: 0 }] }
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
