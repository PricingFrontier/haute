import { describe, it, expect, vi, afterEach } from "vitest"
import { render, screen, cleanup, fireEvent, within } from "@testing-library/react"
import { useState } from "react"

import { literal } from "../catalogue"
import { completionMatches } from "../completion"
import { ColumnListField, ColumnPicker, ConditionRow, NumberField, OperandField } from "../fields"
import { StepSchemaContext, schemaFor } from "../stepSchema"
import type { Condition, Operand } from "../types"

const COLUMNS = ["premium", "premium_net", "region", "Rate"]

function optionTexts(): string[] {
  return within(screen.getByRole("listbox")).getAllByRole("option").map((o) => o.textContent)
}

describe("column-name completion", () => {
  afterEach(cleanup)

  it("matches by prefix regardless of case, leaves out the exact match, and caps the list", () => {
    expect(completionMatches(COLUMNS, "pre")).toEqual(["premium", "premium_net"])
    expect(completionMatches(COLUMNS, "PRE")).toEqual(["premium", "premium_net"])
    expect(completionMatches(COLUMNS, "premium")).toEqual(["premium_net"])
    expect(completionMatches(COLUMNS, "", ["region"])).toEqual(["premium", "premium_net", "Rate"])
    expect(completionMatches(Array.from({ length: 20 }, (_, i) => `c${i}`), "c")).toHaveLength(8)
  })

  it("a column picker lists matches while typing, moves with the arrows and completes with Tab", () => {
    const onCommit = vi.fn()
    render(<ColumnPicker value="" onCommit={onCommit} suggestions={COLUMNS} ariaLabel="Sort key 1 column" />)
    const input = screen.getByRole("combobox", { name: "Sort key 1 column" })
    fireEvent.focus(input)
    expect(optionTexts()).toEqual(COLUMNS)
    fireEvent.change(input, { target: { value: "pre" } })
    expect(optionTexts()).toEqual(["premium", "premium_net"])
    fireEvent.keyDown(input, { key: "ArrowDown" })
    expect(within(screen.getByRole("listbox")).getByRole("option", { name: "premium_net" })).toHaveAttribute("aria-selected", "true")
    fireEvent.keyDown(input, { key: "ArrowUp" })
    fireEvent.keyDown(input, { key: "ArrowUp" })
    expect(within(screen.getByRole("listbox")).getByRole("option", { name: "premium_net" })).toHaveAttribute("aria-selected", "true")
    fireEvent.keyDown(input, { key: "Tab" })
    expect(onCommit).toHaveBeenLastCalledWith("premium_net")
    expect(input).toHaveValue("premium_net")
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument()
  })

  it("a column picker still accepts a free-text name and closes the list on Escape", () => {
    const onCommit = vi.fn()
    render(<ColumnPicker value="" onCommit={onCommit} suggestions={COLUMNS} ariaLabel="Cast 1 column" />)
    const input = screen.getByRole("combobox", { name: "Cast 1 column" })
    fireEvent.change(input, { target: { value: "re" } })
    expect(screen.getByRole("listbox")).toBeInTheDocument()
    fireEvent.keyDown(input, { key: "Escape" })
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument()
    fireEvent.change(input, { target: { value: "brand_new" } })
    fireEvent.keyDown(input, { key: "Enter" })
    expect(onCommit).toHaveBeenLastCalledWith("brand_new")
  })

  it("a column list adds the completed name as a chip and never suggests a chip already present", () => {
    const onChange = vi.fn()
    render(<ColumnListField columns={["region"]} onChange={onChange} suggestions={COLUMNS} ariaLabel="Group by" />)
    const input = screen.getByRole("combobox", { name: "Group by: add" })
    fireEvent.focus(input)
    expect(optionTexts()).toEqual(["premium", "premium_net", "Rate"])
    fireEvent.change(input, { target: { value: "pr" } })
    fireEvent.keyDown(input, { key: "Tab" })
    expect(onChange).toHaveBeenLastCalledWith(["region", "premium"])
    expect(input).toHaveValue("")
    fireEvent.change(input, { target: { value: "typed" } })
    fireEvent.keyDown(input, { key: "Enter" })
    expect(onChange).toHaveBeenLastCalledWith(["region", "typed"])
  })

  it("clicking a completion picks it", () => {
    const onCommit = vi.fn()
    render(<ColumnPicker value="" onCommit={onCommit} suggestions={COLUMNS} ariaLabel="Window column" />)
    const input = screen.getByRole("combobox", { name: "Window column" })
    fireEvent.change(input, { target: { value: "r" } })
    fireEvent.mouseDown(within(screen.getByRole("listbox")).getByRole("option", { name: "Rate" }))
    expect(onCommit).toHaveBeenLastCalledWith("Rate")
  })

  it("keeps focus in the box through a completion and a commit", () => {
    function Picker() {
      const [value, setValue] = useState("")
      return <ColumnPicker value={value} onCommit={setValue} suggestions={COLUMNS} ariaLabel="Probe" />
    }
    render(<Picker />)
    const input = screen.getByRole("combobox", { name: "Probe" })
    input.focus()
    fireEvent.change(input, { target: { value: "pre" } })
    fireEvent.keyDown(input, { key: "Tab" })
    expect(input).toHaveValue("premium")
    expect(document.activeElement).toBe(input)
    fireEvent.change(input, { target: { value: "typed" } })
    fireEvent.keyDown(input, { key: "Enter" })
    expect(input).toHaveValue("typed")
    expect(document.activeElement).toBe(input)
  })
})

describe("number fields", () => {
  afterEach(cleanup)

  it("validates the whole value before committing and recovers after a valid edit", () => {
    const onCommit = vi.fn()
    const { rerender } = render(<NumberField value={2} integer min={1} onCommit={onCommit} ariaLabel="Count" />)
    const input = screen.getByRole("spinbutton", { name: "Count" })
    const reject = (value: string) => {
      fireEvent.change(input, { target: { value } })
      fireEvent.blur(input)
      expect(onCommit).not.toHaveBeenCalled()
      expect(input).toHaveAttribute("aria-invalid", "true")
      expect(screen.getByRole("alert")).toBeInTheDocument()
    }
    reject("1.5")
    rerender(<NumberField value={3} integer min={1} onCommit={onCommit} ariaLabel="Count" />)
    expect(input).not.toHaveAttribute("aria-invalid")
    expect(screen.queryByRole("alert")).not.toBeInTheDocument()
    reject("")
    reject("Infinity")
    reject("0")
    fireEvent.change(input, { target: { value: "1e3" } })
    fireEvent.blur(input)
    expect(onCommit).toHaveBeenCalledWith(1000)
    rerender(<NumberField value={1000} integer min={1} onCommit={onCommit} ariaLabel="Count" />)
    expect(input).not.toHaveAttribute("aria-invalid")
    expect(screen.queryByRole("alert")).not.toBeInTheDocument()
  })

  it("accepts decimals when the field is not an integer", () => {
    const onCommit = vi.fn()
    render(<NumberField value={2} onCommit={onCommit} ariaLabel="Rate" />)
    const input = screen.getByRole("spinbutton", { name: "Rate" })
    fireEvent.change(input, { target: { value: "1.5" } })
    fireEvent.blur(input)
    expect(onCommit).toHaveBeenCalledWith(1.5)
  })
})

/** A step schema over typed columns; a column without a type is one a step made. */
function schema(columns: Array<[string, string | null]>, complete = true) {
  return schemaFor({ columns: columns.map(([name, dtype]) => ({ name, dtype, made: dtype === null })), complete, exact: complete })
}

describe("completion takes only what the analyst chose", () => {
  afterEach(cleanup)

  it("an empty column box lists every name with none active, so Tab adds nothing and moves on", () => {
    const onChange = vi.fn()
    render(<ColumnListField columns={[]} onChange={onChange} suggestions={COLUMNS} ariaLabel="Group by" />)
    const input = screen.getByRole("combobox", { name: "Group by: add" })
    fireEvent.focus(input)
    expect(optionTexts()).toEqual(COLUMNS)
    expect(within(screen.getByRole("listbox")).queryAllByRole("option", { selected: true })).toHaveLength(0)
    expect(fireEvent.keyDown(input, { key: "Tab" })).toBe(true)
    fireEvent.blur(input)
    expect(onChange).not.toHaveBeenCalled()
  })

  it("typing a prefix makes the first match active, and Enter takes it rather than the prefix", () => {
    const onChange = vi.fn()
    render(<ColumnListField columns={[]} onChange={onChange} suggestions={["quote_id", "quote_date"]} ariaLabel="Group by" />)
    const input = screen.getByRole("combobox", { name: "Group by: add" })
    fireEvent.change(input, { target: { value: "quo" } })
    expect(within(screen.getByRole("listbox")).getByRole("option", { name: "quote_id" })).toHaveAttribute("aria-selected", "true")
    fireEvent.keyDown(input, { key: "Enter" })
    expect(onChange).toHaveBeenLastCalledWith(["quote_id"])
  })

  it("a single-column picker takes the highlighted name on Enter", () => {
    const onCommit = vi.fn()
    render(<ColumnPicker value="" onCommit={onCommit} suggestions={["quote_id", "quote_date"]} ariaLabel="Aggregation 1 column" />)
    const input = screen.getByRole("combobox", { name: "Aggregation 1 column" })
    fireEvent.change(input, { target: { value: "quo" } })
    fireEvent.keyDown(input, { key: "Enter" })
    expect(onCommit).toHaveBeenLastCalledWith("quote_id")
  })

  it("an exact name is shown as matched and kept by Tab, even when a longer name exists", () => {
    const onCommit = vi.fn()
    render(<ColumnPicker value="" onCommit={onCommit} suggestions={COLUMNS} ariaLabel="Probe" />)
    const input = screen.getByRole("combobox", { name: "Probe" })
    fireEvent.change(input, { target: { value: "premium" } })
    expect(screen.getByText("matched")).toBeInTheDocument()
    expect(within(screen.getByRole("listbox")).queryAllByRole("option", { selected: true })).toHaveLength(0)
    expect(fireEvent.keyDown(input, { key: "Tab" })).toBe(true)
    fireEvent.blur(input)
    expect(onCommit).toHaveBeenLastCalledWith("premium")
    expect(onCommit).not.toHaveBeenCalledWith("premium_net")
  })

  it("a picker already holding a name keeps it through focus and Tab", () => {
    const onCommit = vi.fn()
    render(<ColumnPicker value="premium" onCommit={onCommit} suggestions={COLUMNS} ariaLabel="Probe" />)
    const input = screen.getByRole("combobox", { name: "Probe" })
    fireEvent.focus(input)
    expect(fireEvent.keyDown(input, { key: "Tab" })).toBe(true)
    fireEvent.blur(input)
    expect(onCommit).not.toHaveBeenCalled()
    expect(input).toHaveValue("premium")
  })

  it("shows each column's type, or new for a column a step made, and tints the active row", () => {
    render(
      <StepSchemaContext.Provider value={schema([["premium", "Float64"], ["gross", null]])}>
        <ColumnPicker value="" onCommit={vi.fn()} suggestions={["premium", "gross"]} ariaLabel="Probe" />
      </StepSchemaContext.Provider>,
    )
    const input = screen.getByRole("combobox", { name: "Probe" })
    fireEvent.change(input, { target: { value: "pre" } })
    const option = within(screen.getByRole("listbox")).getByRole("option", { name: "premium" })
    expect(option).toHaveTextContent("Float64")
    expect(option.getAttribute("style")).toContain("var(--accent-soft)")
    fireEvent.change(input, { target: { value: "" } })
    expect(within(screen.getByRole("listbox")).getByRole("option", { name: "gross" })).toHaveTextContent("new")
  })
})

describe("a column name the step does not have", () => {
  afterEach(cleanup)

  it("is marked on its chip with the closest known name offered", () => {
    const onChange = vi.fn()
    render(
      <StepSchemaContext.Provider value={schema([["quote_id", "Int64"], ["premium", "Float64"]])}>
        <ColumnListField columns={["quot_id", "premium"]} onChange={onChange} suggestions={["quote_id", "premium"]} ariaLabel="Group by" />
      </StepSchemaContext.Provider>,
    )
    expect(screen.getByTitle("Not in the data at this step")).toHaveTextContent("quot_id")
    expect(screen.getByRole("status")).toHaveTextContent("quot_id isn't in the data at this step")
    fireEvent.click(screen.getByRole("button", { name: "Use quote_id" }))
    expect(onChange).toHaveBeenCalledWith(["quote_id", "premium"])
  })

  it("is marked in a single picker, and the offer replaces it", () => {
    const onCommit = vi.fn()
    render(
      <StepSchemaContext.Provider value={schema([["amount_paid", "Float64"]])}>
        <ColumnPicker value="amount_payd" onCommit={onCommit} suggestions={["amount_paid"]} ariaLabel="Aggregation 1 column" />
      </StepSchemaContext.Provider>,
    )
    expect(screen.getByRole("combobox", { name: "Aggregation 1 column" })).toHaveAccessibleDescription(/isn.t in the data at this step/)
    fireEvent.click(screen.getByRole("button", { name: "Use amount_paid" }))
    expect(onCommit).toHaveBeenCalledWith("amount_paid")
  })

  it("is not judged while the columns at the step are not complete", () => {
    render(
      <StepSchemaContext.Provider value={schema([["quote_id", "Int64"]], false)}>
        <ColumnListField columns={["quot_id"]} onChange={vi.fn()} suggestions={["quote_id"]} ariaLabel="Group by" />
      </StepSchemaContext.Provider>,
    )
    expect(screen.queryByRole("status")).not.toBeInTheDocument()
    expect(screen.queryByTitle("Not in the data at this step")).not.toBeInTheDocument()
  })
})

describe("a condition's value starts in its column's type", () => {
  afterEach(cleanup)

  function Row({ initial, onChange }: { initial: Condition; onChange: (next: Condition) => void }) {
    const [condition, setCondition] = useState(initial)
    return (
      <StepSchemaContext.Provider value={schema([["region", "String"], ["start_date", "Date"], ["flag", "Boolean"], ["premium", "Float64"], ["gross", null]])}>
        <ConditionRow
          condition={condition}
          onChange={(next) => {
            setCondition(next)
            onChange(next)
          }}
          columns={["region", "start_date", "flag", "premium", "gross"]}
          variables={[]}
          ariaLabel="Filter condition 1"
        />
      </StepSchemaContext.Provider>
    )
  }
  const pick = (name: string) => {
    const input = screen.getByRole("combobox", { name: "Filter condition 1 column" })
    fireEvent.change(input, { target: { value: name } })
    fireEvent.blur(input)
  }

  it.each([
    ["region", literal("text", "")],
    ["start_date", literal("date", "2026-01-01")],
    ["flag", literal("boolean", true)],
    ["premium", literal("number", 0)],
    ["gross", literal("number", 0)],
  ])("picking %s gives a fresh condition a value of its type", (column, value) => {
    const onChange = vi.fn()
    render(<Row initial={{ column: "", operator: "eq", value: literal("number", 0) }} onChange={onChange} />)
    pick(column)
    expect(onChange).toHaveBeenLastCalledWith({ column, operator: "eq", value })
  })

  it("leaves a value the analyst has already edited", () => {
    const onChange = vi.fn()
    render(<Row initial={{ column: "", operator: "eq", value: literal("number", 5) }} onChange={onChange} />)
    pick("region")
    expect(onChange).toHaveBeenLastCalledWith({ column: "region", operator: "eq", value: literal("number", 5) })
  })
})

describe("a value is one control", () => {
  afterEach(cleanup)

  it("shows one kind marker over the allowed kinds, and switching kind writes a fresh value of it", () => {
    const onChange = vi.fn<(next: Operand) => void>()
    render(
      <OperandField value={literal("number", 3)} onChange={onChange} sources={["literal", "column"]} literalTypes={["number", "text"]} columns={["premium"]} variables={[]} ariaLabel="Value" />,
    )
    const marker = screen.getByRole("combobox", { name: "Value kind" })
    expect(within(marker).getAllByRole("option").map((o) => o.textContent)).toEqual(["Number", "Text", "Column"])
    expect(screen.getAllByRole("combobox")).toHaveLength(1)
    fireEvent.change(marker, { target: { value: "text" } })
    expect(onChange).toHaveBeenLastCalledWith(literal("text", ""))
    fireEvent.change(marker, { target: { value: "column" } })
    expect(onChange).toHaveBeenLastCalledWith({ kind: "column", name: "" })
  })

  it("leaves the marker out when only one kind is allowed", () => {
    render(<OperandField value={literal("text", "a")} onChange={vi.fn()} sources={["literal"]} literalTypes={["text"]} columns={[]} variables={[]} ariaLabel="Value" />)
    expect(screen.queryByRole("combobox", { name: "Value kind" })).not.toBeInTheDocument()
  })
})
