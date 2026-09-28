import { describe, it, expect, vi, afterEach } from "vitest"
import { render, screen, cleanup, fireEvent, within } from "@testing-library/react"
import { useState } from "react"

import { summarizeStep } from "../summary"
import { MAX_EXPR_DEPTH, createStep, stepProblem } from "../catalogue"
import { parseFormula } from "../formula"
import { StepForm, type StepFormContext } from "../forms"
import { schemaFor } from "../stepSchema"
import type { Expr, Step } from "../types"

/** Keeps the step in state so form edits re-render like the editor does. */
function Stateful({ initial, spy }: { initial: Step; spy: (next: Step) => void }) {
  const [step, setStep] = useState(initial)
  return (
    <StepForm
      step={step}
      onChange={(next) => {
        spy(next)
        setStep(next)
      }}
      ctx={ctx}
    />
  )
}

const ctx: StepFormContext = {
  columns: ["premium", "region"],
  variables: ["rate"],
  inputNames: ["quotes", "rates"],
  firstFieldId: "first",
}

function optionValues(select: HTMLElement): string[] {
  return Array.from((select as HTMLSelectElement).options).map((o) => o.value)
}

describe("step forms only build schema-valid payloads", () => {
  afterEach(cleanup)

  it("keeps variable references structured after their definition is removed or moved later", () => {
    const step: Step = { id: "w", kind: "with_column", name: "total", expr: parseFormula("rate + 1", ["rate"]) }
    const onChange = vi.fn()
    const { rerender } = render(<StepForm step={step} onChange={onChange} ctx={ctx} />)
    expect(screen.getByRole("combobox", { name: "Formula" })).toHaveValue("rate + 1")

    rerender(<StepForm step={step} onChange={onChange} ctx={{ ...ctx, variables: [] }} />)

    expect(screen.queryByRole("combobox", { name: "Formula" })).not.toBeInTheDocument()
    expect(screen.getByLabelText("Left operand variable")).toHaveValue("")
    expect(onChange).not.toHaveBeenCalled()
  })

  it.each([0, 1, MAX_EXPR_DEPTH - 1])("keeps an over-depth formula editable under %i enclosing expressions", (enclosing) => {
    const spy = vi.fn()
    let expr: Expr = parseFormula("premium + 1")
    for (let i = 0; i < enclosing; i += 1) {
      expr = { type: "function", fn: "abs", operand: { kind: "expr", expr }, args: [] }
    }
    render(<Stateful initial={{ id: "w", kind: "with_column", name: "total", expr }} spy={spy} />)
    const field = screen.getByRole("combobox", { name: "Formula" })
    const tooDeep = Array(MAX_EXPR_DEPTH + 2 - enclosing).fill("premium").join(" + ")
    fireEvent.change(field, { target: { value: tooDeep } })
    fireEvent.blur(field)
    expect(spy).not.toHaveBeenCalled()
    expect(field).toHaveValue(tooDeep)
    expect(screen.getByRole("alert")).toHaveTextContent(/12 levels/)

    const atLimit = Array(MAX_EXPR_DEPTH + 1 - enclosing).fill("premium").join(" + ")
    fireEvent.change(field, { target: { value: atLimit } })
    fireEvent.keyDown(field, { key: "Enter" })
    expect(spy).toHaveBeenCalledTimes(1)
    expect(stepProblem(spy.mock.calls[0][0])).toBeNull()
    expect(screen.queryByRole("alert")).not.toBeInTheDocument()
    expect(field).toHaveValue(atLimit)
  })

  it("variable definitions offer plain values of number, text or true/false only", () => {
    const onChange = vi.fn()
    const step: Step = { id: "v", kind: "variable", name: "rate", value: { kind: "literal", type: "number", value: 0.12 } }
    render(<StepForm step={step} onChange={onChange} ctx={ctx} />)
    expect(optionValues(screen.getByLabelText("Variable value kind"))).toEqual(["number", "text", "boolean"])
    fireEvent.change(screen.getByLabelText("Variable value kind"), { target: { value: "text" } })
    expect(onChange).toHaveBeenLastCalledWith({ ...step, value: { kind: "literal", type: "text", value: "" } })
  })

  it("membership values share one type for the whole list and can switch to numbers", () => {
    const spy = vi.fn()
    const step: Step = {
      id: "f",
      kind: "filter",
      match: "all",
      conditions: [{ column: "region", operator: "is_in", values: [{ kind: "literal", type: "text", value: "north" }] }],
    }
    render(<Stateful initial={step} spy={spy} />)
    expect(screen.getByLabelText("Filter condition 1 values type")).toHaveValue("text")
    fireEvent.change(screen.getByLabelText("Filter condition 1 values new value"), { target: { value: "south" } })
    fireEvent.blur(screen.getByLabelText("Filter condition 1 values new value"))
    fireEvent.click(screen.getByRole("button", { name: "Filter condition 1 values: add value" }))
    let latest = spy.mock.calls.at(-1)?.[0] as Extract<Step, { kind: "filter" }>
    expect(latest.conditions[0].values).toEqual([
      { kind: "literal", type: "text", value: "north" },
      { kind: "literal", type: "text", value: "south" },
    ])

    fireEvent.change(screen.getByLabelText("Filter condition 1 values type"), { target: { value: "number" } })
    latest = spy.mock.calls.at(-1)?.[0] as Extract<Step, { kind: "filter" }>
    expect(latest.conditions[0].values).toEqual([])
    expect(screen.getByLabelText("Filter condition 1 values type")).toHaveValue("number")

    const input = screen.getByLabelText("Filter condition 1 values new value")
    expect(input).toHaveAttribute("type", "number")
    fireEvent.change(input, { target: { value: "5" } })
    fireEvent.blur(input)
    fireEvent.click(screen.getByRole("button", { name: "Filter condition 1 values: add value" }))
    latest = spy.mock.calls.at(-1)?.[0] as Extract<Step, { kind: "filter" }>
    expect(latest.conditions[0].values).toEqual([{ kind: "literal", type: "number", value: 5 }])
    expect(screen.getByLabelText("Filter condition 1 values type")).toHaveValue("number")
  })

  it("string operators offer text values only, while comparisons offer every type", () => {
    const onChange = vi.fn()
    const step: Step = {
      id: "f",
      kind: "filter",
      match: "all",
      conditions: [{ column: "region", operator: "eq", value: { kind: "literal", type: "number", value: 1 } }],
    }
    render(<StepForm step={step} onChange={onChange} ctx={ctx} />)
    expect(optionValues(screen.getByLabelText("Filter condition 1 value kind"))).toEqual(["number", "text", "boolean", "date", "column", "variable", "expr"])
    fireEvent.change(screen.getByLabelText("Filter condition 1 operator"), { target: { value: "contains" } })
    const next = onChange.mock.calls.at(-1)?.[0] as Extract<Step, { kind: "filter" }>
    expect(next.conditions[0]).toEqual({ column: "region", operator: "contains", value: { kind: "literal", type: "text", value: "" } })
    cleanup()
    render(<StepForm step={next} onChange={onChange} ctx={ctx} />)
    expect(optionValues(screen.getByLabelText("Filter condition 1 value kind"))).toEqual(["text", "column", "variable", "expr"])
  })

  it("function arguments are labelled and typed per function", () => {
    const onChange = vi.fn()
    const step: Step = {
      id: "w",
      kind: "with_column",
      name: "r",
      expr: { type: "function", fn: "round", operand: { kind: "column", name: "premium" }, args: [{ kind: "literal", type: "number", value: 2 }] },
    }
    render(<StepForm step={step} onChange={onChange} ctx={ctx} />)
    const places = screen.getByLabelText("Decimal places")
    expect(places).toHaveAttribute("type", "number")
    expect(places).toHaveAttribute("step", "1")
    fireEvent.change(screen.getByLabelText("Function"), { target: { value: "clip" } })
    const clipped = onChange.mock.calls.at(-1)?.[0] as Extract<Step, { kind: "with_column" }>
    expect(clipped.expr).toEqual({
      type: "function",
      fn: "clip",
      operand: { kind: "column", name: "premium" },
      args: [
        { kind: "literal", type: "number", value: 0 },
        { kind: "literal", type: "number", value: 0 },
      ],
    })
    cleanup()
    render(<StepForm step={clipped} onChange={onChange} ctx={ctx} />)
    expect(screen.getByLabelText("Minimum")).toBeInTheDocument()
    expect(screen.getByLabelText("Maximum")).toBeInTheDocument()
  })

  it("cast offers the cast types through a Type select", () => {
    const onChange = vi.fn()
    const step: Step = {
      id: "w",
      kind: "with_column",
      name: "p",
      expr: { type: "function", fn: "cast", operand: { kind: "column", name: "premium" }, args: [{ kind: "literal", type: "text", value: "Float64" }] },
    }
    render(<StepForm step={step} onChange={onChange} ctx={ctx} />)
    // The common types first, each with its plain meaning, then the other widths.
    expect(optionValues(screen.getByLabelText("Type"))).toEqual(["Int64", "Float64", "String", "Boolean", "Date", "Datetime", "Categorical", "Int8", "Int16", "Int32", "UInt8", "UInt16", "UInt32", "UInt64", "Float32"])
    expect(within(screen.getByLabelText("Type")).getByRole("option", { name: "Int64 (whole number)" })).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText("Type"), { target: { value: "Int64" } })
    const next = onChange.mock.calls.at(-1)?.[0] as Extract<Step, { kind: "with_column" }>
    expect(next.expr).toMatchObject({ fn: "cast", args: [{ kind: "literal", type: "text", value: "Int64" }] })
  })

  it("membership lists never offer the null type, while expression operands do", () => {
    const step: Step = {
      id: "f",
      kind: "filter",
      match: "all",
      conditions: [{ column: "region", operator: "is_in", values: [] }],
    }
    render(<StepForm step={step} onChange={vi.fn()} ctx={ctx} />)
    expect(optionValues(screen.getByLabelText("Filter condition 1 values type"))).toEqual(["number", "text", "boolean", "date"])
    cleanup()
    const spy = vi.fn()
    const conditional: Step = {
      id: "w",
      kind: "with_column",
      name: "masked",
      expr: {
        type: "conditional",
        match: "all",
        conditions: [{ column: "region", operator: "eq", value: { kind: "literal", type: "text", value: "north" } }],
        then: { kind: "column", name: "premium" },
        otherwise: { kind: "literal", type: "number", value: 0 },
      },
    }
    render(<Stateful initial={conditional} spy={spy} />)
    expect(optionValues(screen.getByLabelText("Otherwise value kind"))).toEqual(["number", "text", "boolean", "date", "null", "column", "variable", "expr"])
    fireEvent.change(screen.getByLabelText("Otherwise value kind"), { target: { value: "null" } })
    const latest = spy.mock.calls.at(-1)?.[0] as Extract<Step, { kind: "with_column" }>
    expect(latest.expr).toMatchObject({ otherwise: { kind: "literal", type: "null", value: null } })
    expect(screen.getByLabelText("Otherwise value value")).toHaveValue("null")
  })

  it("window expressions offer the window-only aggregates, an in-group order, and a rank direction", () => {
    const spy = vi.fn()
    const step: Step = { id: "w", kind: "with_column", name: "rn", expr: { type: "window", agg: "sum", column: "premium", over: ["region"] } }
    render(<Stateful initial={step} spy={spy} />)
    expect(optionValues(screen.getByLabelText("Window aggregate"))).toContain("row_number")
    fireEvent.change(screen.getByLabelText("Window aggregate"), { target: { value: "row_number" } })
    expect(screen.queryByLabelText("Window column")).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", { name: "Add order column" }))
    fireEvent.change(screen.getByLabelText("Window order direction"), { target: { value: "desc" } })
    let latest = spy.mock.calls.at(-1)?.[0] as Extract<Step, { kind: "with_column" }>
    expect(latest.expr).toEqual({ type: "window", agg: "row_number", column: "premium", over: ["region"], orderBy: [{ column: "", descending: true }] })
    fireEvent.click(screen.getByRole("button", { name: "Remove window order 1" }))
    latest = spy.mock.calls.at(-1)?.[0] as Extract<Step, { kind: "with_column" }>
    expect(latest.expr).toEqual({ type: "window", agg: "row_number", column: "premium", over: ["region"] })
    fireEvent.change(screen.getByLabelText("Window aggregate"), { target: { value: "dense_rank" } })
    fireEvent.change(screen.getByLabelText("Rank order"), { target: { value: "desc" } })
    latest = spy.mock.calls.at(-1)?.[0] as Extract<Step, { kind: "with_column" }>
    expect(latest.expr).toEqual({ type: "window", agg: "dense_rank", column: "premium", over: ["region"], descending: true })
    fireEvent.change(screen.getByLabelText("Window aggregate"), { target: { value: "quantile" } })
    latest = spy.mock.calls.at(-1)?.[0] as Extract<Step, { kind: "with_column" }>
    expect(latest.expr).toEqual({ type: "window", agg: "quantile", column: "premium", over: ["region"], quantile: 0.5 })
  })

  it("text joins keep at least two parts and a separator", () => {
    const spy = vi.fn()
    const step: Step = {
      id: "w",
      kind: "with_column",
      name: "key",
      expr: { type: "concat", parts: [{ kind: "column", name: "region" }, { kind: "column", name: "premium" }], separator: " " },
    }
    render(<Stateful initial={step} spy={spy} />)
    expect(screen.queryByRole("button", { name: "Remove part 1" })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", { name: "Add part" }))
    fireEvent.change(screen.getByLabelText("Separator"), { target: { value: "|" } })
    fireEvent.blur(screen.getByLabelText("Separator"))
    const latest = spy.mock.calls.at(-1)?.[0] as Extract<Step, { kind: "with_column" }>
    expect(latest.expr).toEqual({
      type: "concat",
      parts: [{ kind: "column", name: "region" }, { kind: "column", name: "premium" }, { kind: "column", name: "" }],
      separator: "|",
    })
    expect(screen.getByRole("button", { name: "Remove part 3" })).toBeInTheDocument()
  })

  it("group-by aggregations take an optional quantile and row filter", () => {
    const spy = vi.fn()
    const step: Step = { id: "g", kind: "group_by", keys: [], aggregations: [{ column: "premium", agg: "sum", name: "total" }] }
    render(<Stateful initial={step} spy={spy} />)
    fireEvent.change(screen.getByLabelText("Aggregation 1 function"), { target: { value: "quantile" } })
    let latest = spy.mock.calls.at(-1)?.[0] as Extract<Step, { kind: "group_by" }>
    expect(latest.aggregations[0]).toEqual({ column: "premium", agg: "quantile", name: "total", quantile: 0.5 })
    fireEvent.change(screen.getByLabelText("Aggregation 1 function"), { target: { value: "sum" } })
    latest = spy.mock.calls.at(-1)?.[0] as Extract<Step, { kind: "group_by" }>
    expect(latest.aggregations[0]).toEqual({ column: "premium", agg: "sum", name: "total" })
    // The row filter sits behind More options on a step that does not use one yet.
    expect(screen.queryByRole("button", { name: /Only rows where/ })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", { name: "More options" }))
    fireEvent.click(screen.getByRole("button", { name: /Only rows where/ }))
    latest = spy.mock.calls.at(-1)?.[0] as Extract<Step, { kind: "group_by" }>
    expect(latest.aggregations[0]).toMatchObject({
      where: { match: "all", conditions: [{ column: "", operator: "eq", value: { kind: "literal", type: "number", value: 0 } }] },
    })
    fireEvent.click(screen.getByRole("button", { name: "Aggregate every row" }))
    latest = spy.mock.calls.at(-1)?.[0] as Extract<Step, { kind: "group_by" }>
    expect(latest.aggregations[0]).toEqual({ column: "premium", agg: "sum", name: "total" })
  })

  it("join validation and row order are optional and cleared for a cross join", () => {
    const spy = vi.fn()
    const step: Step = { id: "j", kind: "join", input: "rates", how: "left", leftOn: ["region"], rightOn: ["region"], suffix: "_r" }
    render(<Stateful initial={step} spy={spy} />)
    fireEvent.change(screen.getByLabelText("Join validation"), { target: { value: "m:1" } })
    fireEvent.change(screen.getByLabelText("Join row order"), { target: { value: "left" } })
    let latest = spy.mock.calls.at(-1)?.[0] as Extract<Step, { kind: "join" }>
    expect(latest).toEqual({ ...step, validate: "m:1", maintainOrder: "left" })
    fireEvent.change(screen.getByLabelText("Join validation"), { target: { value: "off" } })
    latest = spy.mock.calls.at(-1)?.[0] as Extract<Step, { kind: "join" }>
    expect(latest).toEqual({ ...step, maintainOrder: "left" })
    fireEvent.change(screen.getByLabelText("Join validation"), { target: { value: "1:1" } })
    fireEvent.change(screen.getByLabelText("Join type"), { target: { value: "semi" } })
    latest = spy.mock.calls.at(-1)?.[0] as Extract<Step, { kind: "join" }>
    expect(latest).toEqual({ ...step, how: "semi", maintainOrder: "left" })
    expect(screen.queryByLabelText("Join validation")).not.toBeInTheDocument()
    fireEvent.change(screen.getByLabelText("Join type"), { target: { value: "full" } })
    fireEvent.change(screen.getByLabelText("Join validation"), { target: { value: "1:1" } })
    fireEvent.change(screen.getByLabelText("Join type"), { target: { value: "cross" } })
    latest = spy.mock.calls.at(-1)?.[0] as Extract<Step, { kind: "join" }>
    expect(latest).toEqual({ ...step, how: "cross", leftOn: [], rightOn: [], maintainOrder: "left" })
    expect(screen.queryByLabelText("Join validation")).not.toBeInTheDocument()
  })

  it("an operand can become a nested expression with its own editor", () => {
    const spy = vi.fn()
    const step: Step = { id: "w", kind: "with_column", name: "size", expr: { type: "function", fn: "abs", operand: { kind: "column", name: "premium" }, args: [] } }
    render(<Stateful initial={step} spy={spy} />)
    expect(optionValues(screen.getByLabelText("Function operand kind"))).toEqual(["number", "text", "boolean", "date", "null", "column", "variable", "expr"])
    fireEvent.change(screen.getByLabelText("Function operand kind"), { target: { value: "expr" } })
    let latest = spy.mock.calls.at(-1)?.[0] as Extract<Step, { kind: "with_column" }>
    expect(latest.expr).toEqual({
      ...step.expr,
      operand: { kind: "expr", expr: { type: "binary", left: { kind: "column", name: "" }, op: "*", right: { kind: "literal", type: "number", value: 1 }, text: "" } },
    })
    expect(screen.getByRole("group", { name: "Function operand expression" })).toBeInTheDocument()
    // a new formula box starts empty, showing its example as a placeholder and a tooltip
    expect(screen.getByLabelText("Formula")).toHaveValue("")
    expect(screen.getByLabelText("Formula")).toHaveAttribute("placeholder", "e.g. (premium + commission) * tax / 12")
    expect(screen.getByLabelText("Formula")).toHaveAttribute("title", "example: (premium + commission) * tax / 12")
    expect(screen.getByRole("img", { name: "example: (premium + commission) * tax / 12" })).toHaveAttribute("title", "example: (premium + commission) * tax / 12")
    fireEvent.change(screen.getByLabelText("Function operand expression type"), { target: { value: "function" } })
    latest = spy.mock.calls.at(-1)?.[0] as Extract<Step, { kind: "with_column" }>
    expect(latest.expr).toMatchObject({ operand: { kind: "expr", expr: { type: "function", fn: "abs" } } })
    // The deepest level the renderer accepts offers no further nesting.
    cleanup()
    let nested: Extract<Step, { kind: "with_column" }>["expr"] = { type: "function", fn: "abs", operand: { kind: "column", name: "premium" }, args: [] }
    for (let i = 0; i < 11; i += 1) nested = { type: "function", fn: "abs", operand: { kind: "expr", expr: nested }, args: [] }
    render(<StepForm step={{ id: "d", kind: "with_column", name: "deep", expr: nested }} onChange={vi.fn()} ctx={ctx} />)
    const kinds = screen.getAllByLabelText(/kind$/).map((el) => optionValues(el))
    expect(kinds).toHaveLength(12)
    expect(kinds.filter((s) => s.includes("expr"))).toHaveLength(11)
    // Variables and function arguments stay plain values.
    cleanup()
    const variable: Step = { id: "v", kind: "variable", name: "rate", value: { kind: "literal", type: "number", value: 1 } }
    render(<StepForm step={variable} onChange={vi.fn()} ctx={ctx} />)
    expect(optionValues(screen.getByLabelText("Variable value kind"))).toEqual(["number", "text", "boolean"])
  })

  it("membership lists add booleans and other select-typed values through the Add action", () => {
    const spy = vi.fn()
    const step: Step = { id: "f", kind: "filter", match: "all", conditions: [{ column: "region", operator: "is_in", values: [] }] }
    render(<Stateful initial={step} spy={spy} />)
    fireEvent.change(screen.getByLabelText("Filter condition 1 values type"), { target: { value: "boolean" } })
    fireEvent.click(screen.getByRole("button", { name: "Filter condition 1 values: add value" }))
    fireEvent.change(screen.getByLabelText("Filter condition 1 values new value"), { target: { value: "false" } })
    fireEvent.click(screen.getByRole("button", { name: "Filter condition 1 values: add value" }))
    const latest = spy.mock.calls.at(-1)?.[0] as Extract<Step, { kind: "filter" }>
    expect(latest.conditions[0].values).toEqual([
      { kind: "literal", type: "boolean", value: true },
      { kind: "literal", type: "boolean", value: false },
    ])
  })

  it("select and drop take column types alongside named columns", () => {
    const spy = vi.fn()
    const step: Step = { id: "s", kind: "select", columns: ["region"] }
    render(<Stateful initial={step} spy={spy} />)
    // The type-wide choice sits behind More options until a step uses it.
    expect(screen.queryByLabelText("Keep only types: add")).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", { name: "More options" }))
    const input = screen.getByLabelText("Keep only types: add")
    fireEvent.change(input, { target: { value: "Float64" } })
    fireEvent.keyDown(input, { key: "Enter" })
    let latest = spy.mock.calls.at(-1)?.[0] as Extract<Step, { kind: "select" }>
    expect(latest).toEqual({ ...step, dtypes: ["Float64"] })
    fireEvent.click(screen.getByRole("button", { name: "Remove Float64" }))
    latest = spy.mock.calls.at(-1)?.[0] as Extract<Step, { kind: "select" }>
    expect(latest).toEqual(step)
  })

  it("a group-by aggregation can target every column of a type, and its row filter has no nesting", () => {
    const spy = vi.fn()
    const step: Step = { id: "g", kind: "group_by", keys: ["region"], aggregations: [{ column: "premium", agg: "sum", name: "total" }] }
    render(<Stateful initial={step} spy={spy} />)
    fireEvent.click(screen.getByRole("button", { name: "More options" }))
    fireEvent.click(screen.getByRole("button", { name: /Only rows where/ }))
    expect(optionValues(screen.getByLabelText("Aggregation 1 filter condition 1 value kind"))).toEqual(["number", "text", "boolean", "date", "column", "variable"])
    // The type-wide choice is an entry after the column names in the column box.
    const column = screen.getByRole("combobox", { name: "Aggregation 1 column" })
    fireEvent.change(column, { target: { value: "every float" } })
    fireEvent.mouseDown(within(screen.getByRole("listbox")).getByRole("option", { name: "every Float64 column" }))
    let latest = spy.mock.calls.at(-1)?.[0] as Extract<Step, { kind: "group_by" }>
    expect(latest.aggregations[0]).toEqual({ dtype: "Float64", agg: "sum", suffix: "_sum" })
    fireEvent.change(screen.getByLabelText("Aggregation 1 suffix"), { target: { value: "_total" } })
    fireEvent.blur(screen.getByLabelText("Aggregation 1 suffix"))
    fireEvent.change(screen.getByLabelText("Aggregation 1 column type"), { target: { value: "Int64" } })
    latest = spy.mock.calls.at(-1)?.[0] as Extract<Step, { kind: "group_by" }>
    expect(latest.aggregations[0]).toEqual({ dtype: "Int64", agg: "sum", suffix: "_total" })
    expect(optionValues(screen.getByLabelText("Aggregation 1 function"))).not.toContain("len")
    fireEvent.change(screen.getByLabelText("Aggregation 1 column type"), { target: { value: "column" } })
    latest = spy.mock.calls.at(-1)?.[0] as Extract<Step, { kind: "group_by" }>
    expect(latest.aggregations[0]).toEqual({ column: "", agg: "sum", name: "" })
  })

  it("switching an aggregation's target keeps its quantile", () => {
    const spy = vi.fn()
    const step: Step = { id: "g", kind: "group_by", keys: [], aggregations: [{ column: "premium", agg: "quantile", name: "p90", quantile: 0.9 }] }
    render(<Stateful initial={step} spy={spy} />)
    fireEvent.change(screen.getByRole("combobox", { name: "Aggregation 1 column" }), { target: { value: "every" } })
    fireEvent.mouseDown(within(screen.getByRole("listbox")).getByRole("option", { name: "every Float64 column" }))
    let latest = spy.mock.calls.at(-1)?.[0] as Extract<Step, { kind: "group_by" }>
    expect(latest.aggregations[0]).toEqual({ dtype: "Float64", agg: "quantile", suffix: "_quantile", quantile: 0.9 })
    fireEvent.change(screen.getByLabelText("Aggregation 1 column type"), { target: { value: "column" } })
    latest = spy.mock.calls.at(-1)?.[0] as Extract<Step, { kind: "group_by" }>
    expect(latest.aggregations[0]).toEqual({ column: "", agg: "quantile", name: "", quantile: 0.9 })
  })

  it("changing the pivot value type converts every row and keeps the names", () => {
    const spy = vi.fn()
    const step: Step = {
      id: "p",
      kind: "pivot",
      index: ["region"],
      on: "year",
      columns: [
        { value: { kind: "literal", type: "text", value: "2024" }, name: "y2024" },
        { value: { kind: "literal", type: "text", value: "2025" }, name: "y2025" },
      ],
      values: "premium",
      agg: "sum",
    }
    render(<Stateful initial={step} spy={spy} />)
    fireEvent.change(screen.getByLabelText("Pivot column 1 value kind"), { target: { value: "number" } })
    const latest = spy.mock.calls.at(-1)?.[0] as Extract<Step, { kind: "pivot" }>
    expect(latest.columns).toEqual([
      { value: { kind: "literal", type: "number", value: 0 }, name: "y2024" },
      { value: { kind: "literal", type: "number", value: 0 }, name: "y2025" },
    ])
  })

  it("a filter condition may nest twelve levels, the renderer's limit for that position", () => {
    let nested: Extract<Step, { kind: "with_column" }>["expr"] = { type: "function", fn: "abs", operand: { kind: "column", name: "premium" }, args: [] }
    for (let i = 0; i < 11; i += 1) nested = { type: "function", fn: "abs", operand: { kind: "expr", expr: nested }, args: [] }
    const step: Step = { id: "f", kind: "filter", match: "all", conditions: [{ column: "premium", operator: "gt", value: { kind: "expr", expr: nested } }] }
    render(<StepForm step={step} onChange={vi.fn()} ctx={ctx} />)
    const kinds = screen.getAllByLabelText(/kind$/).map((el) => optionValues(el))
    // the condition value plus twelve function operands; only the innermost, at level twelve, cannot nest further
    expect(kinds).toHaveLength(13)
    expect(kinds.filter((s) => s.includes("expr"))).toHaveLength(12)
  })

  it("a formula is edited as text and parsed into the nested schema", () => {
    const spy = vi.fn()
    const step: Step = {
      id: "w",
      kind: "with_column",
      name: "rate",
      expr: { type: "binary", left: { kind: "column", name: "premium" }, op: "/", right: { kind: "literal", type: "number", value: 12 } },
    }
    render(<Stateful initial={step} spy={spy} />)
    expect(optionValues(screen.getByLabelText("Expression type"))[0]).toBe("operand")
    const input = screen.getByLabelText("Formula")
    expect(input).toHaveValue("premium / 12")
    fireEvent.change(input, { target: { value: "(premium + rate) * 1.05 / 12" } })
    fireEvent.blur(input)
    let latest = spy.mock.calls.at(-1)?.[0] as Extract<Step, { kind: "with_column" }>
    expect(latest.expr).toEqual({
      type: "binary",
      text: "(premium + rate) * 1.05 / 12",
      left: {
        kind: "expr",
        expr: {
          type: "binary",
          left: { kind: "expr", expr: { type: "binary", left: { kind: "column", name: "premium" }, op: "+", right: { kind: "variable", name: "rate" } } },
          op: "*",
          right: { kind: "literal", type: "number", value: 1.05 },
        },
      },
      op: "/",
      right: { kind: "literal", type: "number", value: 12 },
    })
    expect(screen.getByLabelText("Formula")).toHaveValue("(premium + rate) * 1.05 / 12")
    // Redundant brackets survive a collapse and re-open exactly as typed.
    fireEvent.change(screen.getByLabelText("Formula"), { target: { value: "premium + (rate * 2)" } })
    fireEvent.blur(screen.getByLabelText("Formula"))
    expect(screen.getByLabelText("Formula")).toHaveValue("premium + (rate * 2)")
    expect(summarizeStep(spy.mock.calls.at(-1)?.[0] as Step)).toBe("rate = premium + (rate * 2)")
    // A function typed as a formula stays a formula.
    fireEvent.change(screen.getByLabelText("Formula"), { target: { value: "round((premium + 1), 2)" } })
    fireEvent.blur(screen.getByLabelText("Formula"))
    latest = spy.mock.calls.at(-1)?.[0] as Extract<Step, { kind: "with_column" }>
    expect(latest.expr).toMatchObject({ type: "function", fn: "round", text: "round((premium + 1), 2)" })
    expect(screen.getByLabelText("Expression type")).toHaveValue("binary")
    expect(screen.getByLabelText("Formula")).toHaveValue("round((premium + 1), 2)")
    fireEvent.change(screen.getByLabelText("Formula"), { target: { value: "(premium + 1" } })
    fireEvent.keyDown(screen.getByLabelText("Formula"), { key: "Enter" })
    expect(screen.getByText(/Not understood: Expected "\)" after number 1/)).toBeInTheDocument()
    // Where reading stopped is marked under the box: the end of the text here.
    expect(screen.getByTestId("formula-problem-position")).toHaveTextContent("(premium + 1␣")
    latest = spy.mock.calls.at(-1)?.[0] as Extract<Step, { kind: "with_column" }>
    expect(latest.expr).toMatchObject({ type: "function", fn: "round", text: "round((premium + 1), 2)" })
  })

  it("completes column names in the formula box with the arrow keys and Tab", () => {
    const spy = vi.fn()
    const step: Step = {
      id: "w",
      kind: "with_column",
      name: "x",
      expr: { type: "binary", left: { kind: "column", name: "a" }, op: "+", right: { kind: "literal", type: "number", value: 1 }, text: "" },
    }
    const wide: StepFormContext = { ...ctx, columns: ["premium", "premium_net", "region", "rate"], variables: ["rate_var"] }
    render(
      <StepForm
        step={step}
        onChange={(next) => {
          spy(next)
        }}
        ctx={wide}
      />,
    )
    const input = screen.getByRole("combobox", { name: "Formula" })
    fireEvent.change(input, { target: { value: "pre" } })
    const list = screen.getByRole("listbox", { name: "Matching columns and functions" })
    expect(within(list).getAllByRole("option").map((o) => o.textContent)).toEqual(["premium", "premium_net"])
    expect(within(list).getByRole("option", { name: "premium" })).toHaveAttribute("aria-selected", "true")
    fireEvent.keyDown(input, { key: "ArrowDown" })
    expect(within(list).getByRole("option", { name: "premium_net" })).toHaveAttribute("aria-selected", "true")
    fireEvent.keyDown(input, { key: "Tab" })
    expect(input).toHaveValue("premium_net")
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument()
    // keeps typing: the word under the caret drives the list, variables included
    fireEvent.change(input, { target: { value: "premium_net * ra" } })
    const words = within(screen.getByRole("listbox"))
    expect(words.getAllByRole("option")).toHaveLength(2)
    expect(words.getByRole("option", { name: "rate" })).toBeInTheDocument()
    expect(words.getByRole("option", { name: "rate_var" })).toHaveTextContent("variable")
    fireEvent.keyDown(input, { key: "Escape" })
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument()
    fireEvent.keyDown(input, { key: "Enter" })
    expect(spy).toHaveBeenLastCalledWith(expect.objectContaining({ expr: expect.objectContaining({ text: "premium_net * ra" }) }))
  })

  it("completes colliding columns as columns and plain variables as variables", () => {
    const spy = vi.fn()
    const step: Step = {
      id: "w",
      kind: "with_column",
      name: "x",
      expr: { type: "binary", left: { kind: "column", name: "premium" }, op: "+", right: { kind: "literal", type: "number", value: 1 }, text: "" },
    }
    const formulaCtx: StepFormContext = { ...ctx, columns: ["true", "rate"], variables: ["rate", "rate_var"] }
    render(<StepForm step={step} onChange={spy} ctx={formulaCtx} />)
    const input = screen.getByRole("combobox", { name: "Formula" })

    fireEvent.change(input, { target: { value: "tr" } })
    fireEvent.keyDown(input, { key: "Tab" })
    expect(input).toHaveValue("`true`")
    fireEvent.keyDown(input, { key: "Enter" })
    expect(spy).toHaveBeenLastCalledWith(expect.objectContaining({ expr: { type: "operand", operand: { kind: "column", name: "true" }, text: "`true`" } }))

    fireEvent.change(input, { target: { value: "ra" } })
    fireEvent.keyDown(input, { key: "Tab" })
    expect(input).toHaveValue("`rate`")
    fireEvent.keyDown(input, { key: "Enter" })
    expect(spy).toHaveBeenLastCalledWith(expect.objectContaining({ expr: { type: "operand", operand: { kind: "column", name: "rate" }, text: "`rate`" } }))

    fireEvent.change(input, { target: { value: "rate_v" } })
    fireEvent.keyDown(input, { key: "Tab" })
    expect(input).toHaveValue("rate_var")
    fireEvent.keyDown(input, { key: "Enter" })
    expect(spy).toHaveBeenLastCalledWith(expect.objectContaining({ expr: { type: "operand", operand: { kind: "variable", name: "rate_var" }, text: "rate_var" } }))
  })

  it("keeps the last expression when a formula number is non-finite", () => {
    const spy = vi.fn()
    const step: Step = {
      id: "w",
      kind: "with_column",
      name: "x",
      expr: { type: "operand", operand: { kind: "column", name: "premium" }, text: "premium" },
    }
    render(<Stateful initial={step} spy={spy} />)
    const input = screen.getByRole("combobox", { name: "Formula" })
    fireEvent.change(input, { target: { value: "1e999" } })
    fireEvent.keyDown(input, { key: "Enter" })
    expect(input).toHaveValue("1e999")
    expect(screen.getByRole("alert")).toHaveTextContent(/finite number/)
    expect(spy).not.toHaveBeenCalled()
  })

  it("a bare column name typed as a formula stays in the formula box", () => {
    const spy = vi.fn()
    const step: Step = {
      id: "w",
      kind: "with_column",
      name: "x",
      expr: { type: "binary", left: { kind: "column", name: "a" }, op: "+", right: { kind: "literal", type: "number", value: 1 }, text: "" },
    }
    render(<Stateful initial={step} spy={spy} />)
    const input = screen.getByRole("combobox", { name: "Formula" })
    fireEvent.change(input, { target: { value: "premium" } })
    fireEvent.blur(input)
    const latest = spy.mock.calls.at(-1)?.[0] as Extract<Step, { kind: "with_column" }>
    expect(latest.expr).toEqual({ type: "operand", operand: { kind: "column", name: "premium" }, text: "premium" })
    expect(screen.getByLabelText("Expression type")).toHaveValue("binary")
    expect(screen.getByRole("combobox", { name: "Formula" })).toHaveValue("premium")
  })

  it("says when no column names are known yet", () => {
    const step: Step = {
      id: "w",
      kind: "with_column",
      name: "x",
      expr: { type: "binary", left: { kind: "column", name: "a" }, op: "+", right: { kind: "literal", type: "number", value: 1 }, text: "" },
    }
    render(<StepForm step={step} onChange={vi.fn()} ctx={{ ...ctx, columns: [], variables: [] }} />)
    // A prefix no function starts with either.
    fireEvent.change(screen.getByRole("combobox", { name: "Formula" }), { target: { value: "qu" } })
    expect(screen.getByText(/No column names known yet/)).toBeInTheDocument()
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument()
  })

  it("pivot columns pair a value with a suggested name and share one type", () => {
    const spy = vi.fn()
    const step: Step = { id: "p", kind: "pivot", index: ["region"], on: "channel", columns: [], values: "premium", agg: "mean" }
    render(<Stateful initial={step} spy={spy} />)
    fireEvent.click(screen.getByRole("button", { name: "Add column" }))
    fireEvent.change(screen.getByLabelText("Pivot column 1 value value"), { target: { value: "web" } })
    fireEvent.blur(screen.getByLabelText("Pivot column 1 value value"))
    let latest = spy.mock.calls.at(-1)?.[0] as Extract<Step, { kind: "pivot" }>
    expect(latest.columns).toEqual([{ value: { kind: "literal", type: "text", value: "web" }, name: "web" }])
    fireEvent.click(screen.getByRole("button", { name: "Add column" }))
    expect(optionValues(screen.getByLabelText("Pivot column 1 value kind"))).toEqual(["number", "text", "boolean", "date"])
    expect(screen.queryByLabelText("Pivot column 2 value kind")).not.toBeInTheDocument()
    fireEvent.change(screen.getByLabelText("Pivot column 2 name"), { target: { value: "by_phone" } })
    fireEvent.blur(screen.getByLabelText("Pivot column 2 name"))
    latest = spy.mock.calls.at(-1)?.[0] as Extract<Step, { kind: "pivot" }>
    expect(latest.columns[1]).toEqual({ value: { kind: "literal", type: "text", value: "" }, name: "by_phone" })
  })

  it("unpivot keeps index suggestions apart from the stacked columns", () => {
    const step: Step = { id: "u", kind: "unpivot", on: ["premium"], index: [], variableName: "measure", valueName: "value" }
    render(<StepForm step={step} onChange={vi.fn()} ctx={ctx} />)
    expect(screen.getByLabelText("Unpivot name column")).toHaveValue("measure")
    expect(screen.getByText(/Row order after unpivoting is not guaranteed/)).toBeInTheDocument()
  })

  it("text-join parts may be null, like any expression operand", () => {
    const step: Step = {
      id: "w",
      kind: "with_column",
      name: "key",
      expr: { type: "concat", parts: [{ kind: "column", name: "region" }, { kind: "literal", type: "text", value: "-" }], separator: "" },
    }
    render(<StepForm step={step} onChange={vi.fn()} ctx={ctx} />)
    expect(optionValues(screen.getByLabelText("Part 2 kind"))).toEqual(["number", "text", "boolean", "date", "null", "column", "variable", "expr"])
  })

  it("a variable operand is offered only when an earlier variable exists", () => {
    const step: Step = {
      id: "f",
      kind: "filter",
      match: "all",
      conditions: [{ column: "premium", operator: "gt", value: { kind: "literal", type: "number", value: 1 } }],
    }
    render(<StepForm step={step} onChange={vi.fn()} ctx={{ ...ctx, variables: [] }} />)
    expect(optionValues(screen.getByLabelText("Filter condition 1 value kind"))).toEqual(["number", "text", "boolean", "date", "column", "expr"])
  })

  it("keeps focus in the formula box after Enter and clears the error once the text is the committed formula again", () => {
    const step: Step = {
      id: "w",
      kind: "with_column",
      name: "x",
      expr: { type: "binary", left: { kind: "column", name: "premium" }, op: "*", right: { kind: "literal", type: "number", value: 1 }, text: "" },
    }
    render(<Stateful initial={step} spy={vi.fn()} />)
    const input = screen.getByLabelText("Formula")
    input.focus()
    fireEvent.change(input, { target: { value: "premium * 2" } })
    fireEvent.keyDown(input, { key: "Enter" })
    expect(input).toHaveValue("premium * 2")
    expect(document.activeElement).toBe(input)
    fireEvent.change(input, { target: { value: "premium * (" } })
    fireEvent.keyDown(input, { key: "Enter" })
    expect(screen.getByText(/Not understood/)).toBeInTheDocument()
    fireEvent.change(input, { target: { value: "premium * 2" } })
    fireEvent.keyDown(input, { key: "Enter" })
    expect(screen.queryByText(/Not understood/)).not.toBeInTheDocument()
    expect(document.activeElement).toBe(input)
  })

  it("rename rows pick a source column, take a new name, and can be added and removed", () => {
    const spy = vi.fn()
    render(<Stateful initial={{ id: "r", kind: "rename", renames: [{ from: "", to: "" }] }} spy={spy} />)
    expect(screen.queryByRole("button", { name: "Remove rename 1" })).not.toBeInTheDocument()
    fireEvent.change(screen.getByLabelText("Rename 1 from"), { target: { value: "premium" } })
    fireEvent.keyDown(screen.getByLabelText("Rename 1 from"), { key: "Enter" })
    fireEvent.change(screen.getByLabelText("Rename 1 to"), { target: { value: "net" } })
    fireEvent.blur(screen.getByLabelText("Rename 1 to"))
    expect(spy).toHaveBeenLastCalledWith({ id: "r", kind: "rename", renames: [{ from: "premium", to: "net" }] })
    fireEvent.click(screen.getByRole("button", { name: "Add rename" }))
    expect(screen.getByRole("group", { name: "Rename 2" })).toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", { name: "Remove rename 2" }))
    expect(spy).toHaveBeenLastCalledWith({ id: "r", kind: "rename", renames: [{ from: "premium", to: "net" }] })
    expect(summarizeStep(spy.mock.calls.at(-1)?.[0] as Step)).toBe("premium → net")
  })

  it("cast rows pair a column with a type and can be added", () => {
    const spy = vi.fn()
    render(<Stateful initial={{ id: "c", kind: "cast", casts: [{ column: "premium", dtype: "Float64" }] }} spy={spy} />)
    fireEvent.change(screen.getByLabelText("Cast 1 type"), { target: { value: "Int64" } })
    expect(spy).toHaveBeenLastCalledWith({ id: "c", kind: "cast", casts: [{ column: "premium", dtype: "Int64" }] })
    fireEvent.click(screen.getByRole("button", { name: "Add column" }))
    expect(spy).toHaveBeenLastCalledWith({ id: "c", kind: "cast", casts: [{ column: "premium", dtype: "Int64" }, { column: "", dtype: "Float64" }] })
    expect(screen.getByRole("button", { name: "Remove cast 1" })).toBeInTheDocument()
  })

  it("sort keys take a direction and nulls-last, and can be added and removed", () => {
    const spy = vi.fn()
    render(<Stateful initial={{ id: "s", kind: "sort", keys: [{ column: "premium", descending: false }], nullsLast: false }} spy={spy} />)
    fireEvent.change(screen.getByLabelText("Sort key 1 direction"), { target: { value: "desc" } })
    expect(spy).toHaveBeenLastCalledWith({ id: "s", kind: "sort", keys: [{ column: "premium", descending: true }], nullsLast: false })
    fireEvent.click(screen.getByLabelText("Missing values last"))
    expect(spy).toHaveBeenLastCalledWith({ id: "s", kind: "sort", keys: [{ column: "premium", descending: true }], nullsLast: true })
    fireEvent.click(screen.getByRole("button", { name: "Add sort column" }))
    expect(screen.getByRole("group", { name: "Sort key 2" })).toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", { name: "Remove sort key 2" }))
    expect(spy).toHaveBeenLastCalledWith({ id: "s", kind: "sort", keys: [{ column: "premium", descending: true }], nullsLast: true })
  })

  it("unique takes its key columns as chips and a keep policy", () => {
    const spy = vi.fn()
    render(<Stateful initial={{ id: "u", kind: "unique", columns: [], keep: "first" }} spy={spy} />)
    fireEvent.change(screen.getByLabelText("Keep"), { target: { value: "none" } })
    expect(spy).toHaveBeenLastCalledWith({ id: "u", kind: "unique", columns: [], keep: "none" })
    const add = screen.getByRole("combobox", { name: "Unique by: add" })
    fireEvent.change(add, { target: { value: "region" } })
    fireEvent.keyDown(add, { key: "Enter" })
    expect(spy).toHaveBeenLastCalledWith({ id: "u", kind: "unique", columns: ["region"], keep: "none" })
  })

  it("concat appends the ticked inputs in tick order and chooses how columns line up", () => {
    const spy = vi.fn()
    render(<Stateful initial={{ id: "a", kind: "concat", inputs: [], how: "vertical" }} spy={spy} />)
    fireEvent.click(screen.getByLabelText("rates"))
    fireEvent.click(screen.getByLabelText("quotes"))
    expect(spy).toHaveBeenLastCalledWith({ id: "a", kind: "concat", inputs: ["rates", "quotes"], how: "vertical" })
    fireEvent.change(screen.getByLabelText("Concat type"), { target: { value: "diagonal" } })
    expect(spy).toHaveBeenLastCalledWith({ id: "a", kind: "concat", inputs: ["rates", "quotes"], how: "diagonal" })
    fireEvent.click(screen.getByLabelText("rates"))
    expect(spy).toHaveBeenLastCalledWith({ id: "a", kind: "concat", inputs: ["quotes"], how: "diagonal" })
  })

  it("fill null takes a value of any source, or a strategy", () => {
    const spy = vi.fn()
    render(<Stateful initial={{ id: "n", kind: "fill_null", columns: [], fill: { kind: "value", value: { kind: "literal", type: "number", value: 0 } } }} spy={spy} />)
    expect(optionValues(screen.getByLabelText("Fill value kind"))).toEqual(["number", "text", "boolean", "date", "column", "variable", "expr"])
    fireEvent.change(screen.getByLabelText("Fill value kind"), { target: { value: "column" } })
    expect(spy).toHaveBeenLastCalledWith({ id: "n", kind: "fill_null", columns: [], fill: { kind: "value", value: { kind: "column", name: "" } } })
    // One select: a value, or a strategy in plain words with its Polars name.
    const fillWith = screen.getByLabelText("Fill with")
    expect(optionValues(fillWith)).toEqual(["value", "forward", "backward", "min", "max", "mean", "zero", "one"])
    expect(within(fillWith).getByRole("option", { name: "the previous row's value (forward)" })).toBeInTheDocument()
    fireEvent.change(fillWith, { target: { value: "forward" } })
    expect(spy).toHaveBeenLastCalledWith({ id: "n", kind: "fill_null", columns: [], fill: { kind: "strategy", strategy: "forward" } })
    expect(screen.queryByLabelText("Fill value kind")).not.toBeInTheDocument()
    fireEvent.change(screen.getByLabelText("Fill with"), { target: { value: "mean" } })
    expect(spy).toHaveBeenLastCalledWith({ id: "n", kind: "fill_null", columns: [], fill: { kind: "strategy", strategy: "mean" } })
    expect(summarizeStep(spy.mock.calls.at(-1)?.[0] as Step)).toBe("all columns with the column's mean (mean)")
  })

  it("a formula holding a window is edited in the structured left/operator/right form", () => {
    const spy = vi.fn()
    const step: Step = {
      id: "w",
      kind: "with_column",
      name: "share",
      expr: {
        type: "binary",
        left: { kind: "expr", expr: { type: "window", agg: "sum", column: "premium", over: ["region"] } },
        op: "/",
        right: { kind: "literal", type: "number", value: 12 },
      },
    }
    render(<Stateful initial={step} spy={spy} />)
    expect(screen.queryByLabelText("Formula")).not.toBeInTheDocument()
    expect(screen.getByLabelText("Left operand kind")).toHaveValue("expr")
    expect(screen.getByLabelText("Window aggregate")).toHaveValue("sum")
    fireEvent.change(screen.getByLabelText("Operator"), { target: { value: "*" } })
    const latest = spy.mock.calls.at(-1)?.[0] as Extract<Step, { kind: "with_column" }>
    expect(latest.expr).toMatchObject({ type: "binary", op: "*" })
    expect(summarizeStep(latest)).toBe("share = sum of premium over region * 12")
  })

  it("an if-then expression edits its conditions and both branches", () => {
    const spy = vi.fn()
    const step: Step = {
      id: "b",
      kind: "with_column",
      name: "band",
      expr: {
        type: "conditional",
        match: "all",
        conditions: [{ column: "premium", operator: "gt", value: { kind: "literal", type: "number", value: 100 } }],
        then: { kind: "literal", type: "text", value: "high" },
        otherwise: { kind: "literal", type: "text", value: "low" },
      },
    }
    render(<Stateful initial={step} spy={spy} />)
    expect(screen.getByLabelText("Expression type")).toHaveValue("conditional")
    expect(screen.getByLabelText("If condition 1 column")).toHaveValue("premium")
    fireEvent.change(screen.getByLabelText("Then value value"), { target: { value: "top" } })
    fireEvent.blur(screen.getByLabelText("Then value value"))
    expect((spy.mock.calls.at(-1)?.[0] as Extract<Step, { kind: "with_column" }>).expr).toMatchObject({ then: { kind: "literal", type: "text", value: "top" } })
    fireEvent.change(screen.getByLabelText("Otherwise value kind"), { target: { value: "column" } })
    expect((spy.mock.calls.at(-1)?.[0] as Extract<Step, { kind: "with_column" }>).expr).toMatchObject({ otherwise: { kind: "column", name: "" } })
    fireEvent.click(screen.getByRole("button", { name: "Add condition" }))
    const latest = spy.mock.calls.at(-1)?.[0] as Extract<Step, { kind: "with_column" }>
    expect(latest.expr).toMatchObject({ type: "conditional", conditions: [expect.anything(), { column: "", operator: "eq" }] })
    expect(screen.getByLabelText("If match")).toHaveValue("all")
    expect(summarizeStep(latest)).toBe("band = if premium is greater than 100 and column equals 0 then 'top' else column")
  })
})

/** Keeps the step in state under a given form context. */
function StatefulIn({ initial, spy, context }: { initial: Step; spy: (next: Step) => void; context: StepFormContext }) {
  const [step, setStep] = useState(initial)
  return (
    <StepForm
      step={step}
      onChange={(next) => {
        spy(next)
        setStep(next)
      }}
      ctx={context}
    />
  )
}

/** Whether `first` comes before `second` in the document. */
function precedes(first: Element, second: Element): boolean {
  return Boolean(first.compareDocumentPosition(second) & Node.DOCUMENT_POSITION_FOLLOWING)
}

const emptyFormula: Step = {
  id: "w",
  kind: "with_column",
  name: "x",
  expr: { type: "binary", left: { kind: "column", name: "a" }, op: "+", right: { kind: "literal", type: "number", value: 1 }, text: "" },
}

describe("the formula box shows what it can do", () => {
  afterEach(cleanup)

  it("lists the catalogue's functions with what they do, and a chosen function arrives as a call with the caret inside", () => {
    const spy = vi.fn()
    render(<Stateful initial={emptyFormula} spy={spy} />)
    const input = screen.getByRole("combobox", { name: "Formula" }) as HTMLTextAreaElement
    fireEvent.change(input, { target: { value: "rou" } })
    const option = within(screen.getByRole("listbox")).getByRole("option", { name: "round" })
    expect(option).toHaveTextContent("ƒ")
    fireEvent.mouseDown(option)
    expect(input).toHaveValue("round()")
    expect(input.selectionStart).toBe(6)
    fireEvent.change(input, { target: { value: "round(premium, 2)" } })
    fireEvent.keyDown(input, { key: "Enter" })
    expect((spy.mock.calls.at(-1)?.[0] as Extract<Step, { kind: "with_column" }>).expr).toMatchObject({ type: "function", fn: "round", text: "round(premium, 2)" })
  })

  it("names a call's arguments while the caret is inside it, the current one bold", () => {
    render(<Stateful initial={emptyFormula} spy={vi.fn()} />)
    fireEvent.change(screen.getByRole("combobox", { name: "Formula" }), { target: { value: "round(premium, " } })
    const tip = screen.getByTestId("formula-argument-tip")
    expect(tip).toHaveTextContent("round(value, decimal places)")
    expect(within(tip).getByText("decimal places").tagName).toBe("STRONG")
  })

  it("accepts a function name in any case and keeps the catalogue's spelling", () => {
    const spy = vi.fn()
    render(<Stateful initial={emptyFormula} spy={spy} />)
    const input = screen.getByRole("combobox", { name: "Formula" })
    fireEvent.change(input, { target: { value: "ROUND(premium, 2)" } })
    fireEvent.keyDown(input, { key: "Enter" })
    expect((spy.mock.calls.at(-1)?.[0] as Extract<Step, { kind: "with_column" }>).expr).toMatchObject({ type: "function", fn: "round", text: "round(premium, 2)" })
    expect(input).toHaveValue("round(premium, 2)")
  })

  it("completes a column that shares a function's name as a column", () => {
    const spy = vi.fn()
    render(<StatefulIn initial={emptyFormula} spy={spy} context={{ ...ctx, columns: ["round"] }} />)
    const input = screen.getByRole("combobox", { name: "Formula" })
    fireEvent.change(input, { target: { value: "rou" } })
    const [column, fn] = within(screen.getByRole("listbox")).getAllByRole("option", { name: "round" })
    expect(fn).toHaveTextContent("ƒ")
    expect(column).not.toHaveTextContent("ƒ")
    fireEvent.mouseDown(column)
    expect(input).toHaveValue("round")
    fireEvent.keyDown(input, { key: "Enter" })
    expect((spy.mock.calls.at(-1)?.[0] as Extract<Step, { kind: "with_column" }>).expr).toEqual({ type: "operand", operand: { kind: "column", name: "round" }, text: "round" })
  })

  it("keeps a fully typed column name through Tab and Enter", () => {
    const spy = vi.fn()
    render(<StatefulIn initial={emptyFormula} spy={spy} context={{ ...ctx, columns: ["premium", "premium_net"] }} />)
    const input = screen.getByRole("combobox", { name: "Formula" })
    fireEvent.change(input, { target: { value: "premium" } })
    expect(fireEvent.keyDown(input, { key: "Tab" })).toBe(true)
    expect(input).toHaveValue("premium")
    fireEvent.keyDown(input, { key: "Enter" })
    expect((spy.mock.calls.at(-1)?.[0] as Extract<Step, { kind: "with_column" }>).expr).toMatchObject({ text: "premium" })
  })

  it("renames only the column when the offer is taken, not matching quoted text", () => {
    const spy = vi.fn()
    const step: Step = { id: "w", kind: "with_column", name: "x", expr: parseFormula('replace(primium, "primium", "discount")') }
    const schema = schemaFor({ columns: [{ name: "premium", dtype: "String", made: false }], complete: true, exact: true })
    render(<StatefulIn initial={step} spy={spy} context={{ ...ctx, columns: ["premium"], schema }} />)
    fireEvent.click(screen.getByRole("button", { name: "Use premium" }))
    expect((spy.mock.calls.at(-1)?.[0] as Extract<Step, { kind: "with_column" }>).expr).toMatchObject({
      type: "function",
      fn: "replace",
      text: 'replace(premium, "primium", "discount")',
    })
  })

  it("names a column the step does not have, and the offer rewrites the formula", () => {
    const spy = vi.fn()
    const step: Step = { id: "w", kind: "with_column", name: "x", expr: parseFormula("premum * 2") }
    const schema = schemaFor({ columns: [{ name: "premium", dtype: "Float64", made: false }], complete: true, exact: true })
    render(<StatefulIn initial={step} spy={spy} context={{ ...ctx, columns: ["premium"], schema }} />)
    expect(screen.getByRole("status")).toHaveTextContent("premum isn't in the data at this step. Did you mean premium?")
    fireEvent.click(screen.getByRole("button", { name: "Use premium" }))
    expect((spy.mock.calls.at(-1)?.[0] as Extract<Step, { kind: "with_column" }>).expr).toMatchObject({ text: "premium * 2" })
    expect(screen.queryByRole("status")).not.toBeInTheDocument()
  })
})

describe("an aggregation reads as one sentence", () => {
  afterEach(cleanup)
  const claims: StepFormContext = { ...ctx, columns: ["quote_id", "amount_paid"] }

  it("lays out name = function of column, and names itself until a name is typed", () => {
    const spy = vi.fn()
    const step: Step = { id: "g", kind: "group_by", keys: ["quote_id"], aggregations: [{ column: "", agg: "sum", name: "" }] }
    render(<StatefulIn initial={step} spy={spy} context={claims} />)
    const name = screen.getByLabelText("Aggregation 1 name")
    const fn = screen.getByLabelText("Aggregation 1 function")
    const column = screen.getByRole("combobox", { name: "Aggregation 1 column" })
    expect(precedes(name, fn) && precedes(fn, column)).toBe(true)
    fireEvent.change(column, { target: { value: "amount_paid" } })
    fireEvent.keyDown(column, { key: "Enter" })
    const latest = () => (spy.mock.calls.at(-1)?.[0] as Extract<Step, { kind: "group_by" }>).aggregations[0]
    expect(latest()).toEqual({ column: "amount_paid", agg: "sum", name: "amount_paid_sum" })
    fireEvent.change(fn, { target: { value: "max" } })
    expect(latest()).toEqual({ column: "amount_paid", agg: "max", name: "amount_paid_max" })
    fireEvent.change(screen.getByLabelText("Aggregation 1 name"), { target: { value: "largest_claim" } })
    fireEvent.blur(screen.getByLabelText("Aggregation 1 name"))
    fireEvent.change(fn, { target: { value: "min" } })
    expect(latest()).toEqual({ column: "amount_paid", agg: "min", name: "largest_claim" })
  })

  it("says what the two counts do, and a new row starts from the previous row's column", () => {
    const spy = vi.fn()
    const step: Step = { id: "g", kind: "group_by", keys: [], aggregations: [{ column: "amount_paid", agg: "sum", name: "total" }] }
    render(<StatefulIn initial={step} spy={spy} context={claims} />)
    const fn = screen.getByLabelText("Aggregation 1 function")
    expect(within(fn).getByRole("option", { name: "count of values (skips missing values)" })).toBeInTheDocument()
    expect(within(fn).getByRole("option", { name: "row count (every row, missing values included)" })).toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", { name: "Add aggregation" }))
    expect((spy.mock.calls.at(-1)?.[0] as Extract<Step, { kind: "group_by" }>).aggregations[1]).toEqual({ column: "amount_paid", agg: "sum", name: "" })
  })

  it("opens More options by itself when a saved aggregation filters its rows", () => {
    const step: Step = {
      id: "g",
      kind: "group_by",
      keys: [],
      aggregations: [{ column: "amount_paid", agg: "sum", name: "t", where: { match: "all", conditions: [{ column: "amount_paid", operator: "gt", value: { kind: "literal", type: "number", value: 0 } }] } }],
    }
    render(<StepForm step={step} onChange={vi.fn()} ctx={claims} />)
    expect(screen.getByRole("button", { name: "More options" })).toHaveAttribute("aria-expanded", "true")
    expect(screen.getByRole("button", { name: "Aggregate every row" })).toBeInTheDocument()
  })
})

describe("a join", () => {
  afterEach(cleanup)
  const joining: StepFormContext = {
    ...ctx,
    columns: ["premium", "region"],
    inputNames: ["quotes", "rates"],
    inputColumns: { quotes: [{ name: "premium", dtype: "Float64" }, { name: "region", dtype: "String" }], rates: [{ name: "region", dtype: "String" }, { name: "factor", dtype: "Float64" }] },
  }
  const fresh: Step = { id: "j", kind: "join", input: "rates", how: "left", leftOn: [], rightOn: [], suffix: "_right" }

  it("completes right keys from the joined input, and pairs a key with the same name there", () => {
    const spy = vi.fn()
    render(<StatefulIn initial={fresh} spy={spy} context={joining} />)
    fireEvent.focus(screen.getByRole("combobox", { name: "Join key 1 right" }))
    expect(within(screen.getByRole("listbox")).getAllByRole("option").map((o) => o.getAttribute("id") && o.textContent?.replace(/String|Float64/, ""))).toEqual(["region", "factor"])
    fireEvent.blur(screen.getByRole("combobox", { name: "Join key 1 right" }))
    const left = screen.getByRole("combobox", { name: "Join key 1 left" })
    fireEvent.change(left, { target: { value: "region" } })
    fireEvent.keyDown(left, { key: "Enter" })
    expect(spy).toHaveBeenLastCalledWith({ ...fresh, leftOn: ["region"], rightOn: ["region"] })
    fireEvent.click(screen.getByRole("button", { name: "Add key" }))
    expect(screen.getByRole("group", { name: "Join key 2" })).toBeInTheDocument()
  })

  it("says which rows each kind keeps, and keeps its rarely used options behind More options", () => {
    render(<StepForm step={fresh} onChange={vi.fn()} ctx={joining} />)
    expect(within(screen.getByLabelText("Join type")).getByRole("option", { name: "left: every row here, with matches added" })).toBeInTheDocument()
    expect(screen.queryByLabelText("Join row order")).not.toBeInTheDocument()
    expect(screen.queryByLabelText("Suffix")).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", { name: "More options" }))
    expect(screen.getByLabelText("Join row order")).toBeInTheDocument()
    expect(screen.getByLabelText("Suffix")).toHaveValue("_right")
    expect(screen.getByLabelText("Join validation")).toBeInTheDocument()
  })

  it("marks a right key the joined input does not have", () => {
    render(<StepForm step={{ ...fresh, leftOn: ["region"], rightOn: ["regoin"] }} onChange={vi.fn()} ctx={joining} />)
    expect(screen.getByRole("button", { name: "Use region" })).toBeInTheDocument()
  })

  it("with one input connected, starts unset and says to connect the table to join", () => {
    render(<StepForm step={{ ...fresh, input: "" }} onChange={vi.fn()} ctx={{ ...joining, inputNames: ["quotes"] }} />)
    expect(screen.getByLabelText("Join input")).toHaveValue("")
    expect(screen.getByText("Connect the table to join on the canvas.")).toBeInTheDocument()
  })
})

describe("forms read as sentences", () => {
  afterEach(cleanup)

  it("limit, rename and sort read in sentence order and keep their accessible names", () => {
    render(<StepForm step={{ id: "l", kind: "limit", n: 100 }} onChange={vi.fn()} ctx={ctx} />)
    expect(precedes(screen.getByText("Keep the first"), screen.getByLabelText("Row limit"))).toBe(true)
    expect(precedes(screen.getByLabelText("Row limit"), screen.getByText("rows"))).toBe(true)
    cleanup()
    render(<StepForm step={{ id: "r", kind: "rename", renames: [{ from: "premium", to: "net" }] }} onChange={vi.fn()} ctx={ctx} />)
    expect(precedes(screen.getByText("Rename"), screen.getByLabelText("Rename 1 from"))).toBe(true)
    expect(precedes(screen.getByLabelText("Rename 1 from"), screen.getByText("to"))).toBe(true)
    expect(precedes(screen.getByText("to"), screen.getByLabelText("Rename 1 to"))).toBe(true)
    cleanup()
    render(<StepForm step={{ id: "s", kind: "sort", keys: [{ column: "premium", descending: false }], nullsLast: false }} onChange={vi.fn()} ctx={ctx} />)
    expect(precedes(screen.getByText("Sort by"), screen.getByLabelText("Sort key 1 column"))).toBe(true)
    expect(precedes(screen.getByLabelText("Sort key 1 column"), screen.getByLabelText("Sort key 1 direction"))).toBe(true)
  })

  it.each([
    ["an if-then", "If", (conditions: Array<{ column: string; operator: "is_null" }>): Step => ({
      id: "w",
      kind: "with_column",
      name: "band",
      expr: { type: "conditional", match: "all", conditions, then: { kind: "literal", type: "number", value: 1 }, otherwise: { kind: "literal", type: "number", value: 0 } },
    })],
    ["an aggregation's row filter", "Aggregation 1 filter", (conditions: Array<{ column: string; operator: "is_null" }>): Step => ({
      id: "g",
      kind: "group_by",
      keys: [],
      aggregations: [{ column: "premium", agg: "sum", name: "t", where: { match: "all", conditions } }],
    })],
  ])("the conditions of %s say when, with all or any in the sentence", (_label, list, build) => {
    const spy = vi.fn()
    render(<Stateful initial={build([{ column: "premium", operator: "is_null" }, { column: "region", operator: "is_null" }])} spy={spy} />)
    expect(screen.getByText("When")).toBeInTheDocument()
    expect(screen.getByText("of these are true")).toBeInTheDocument()
    expect(screen.queryByText(/Keep rows/)).not.toBeInTheDocument()
    fireEvent.change(screen.getByLabelText(`${list} match`), { target: { value: "any" } })
    expect(JSON.stringify(spy.mock.calls.at(-1)?.[0])).toContain('"match":"any"')
    expect(screen.getByLabelText(`${list} match`)).toHaveValue("any")
  })

  it("a filter keeps rows where its conditions are true", () => {
    render(<StepForm step={{ id: "f", kind: "filter", match: "any", conditions: [{ column: "premium", operator: "is_null" }, { column: "region", operator: "is_null" }] }} onChange={vi.fn()} ctx={ctx} />)
    expect(screen.getByText("Keep rows where")).toBeInTheDocument()
    expect(screen.getByLabelText("Filter match")).toHaveValue("any")
  })

  it("labels read in sentence case with their notes beside them", () => {
    render(<StepForm step={{ id: "g", kind: "group_by", keys: [], aggregations: [{ column: "premium", agg: "sum", name: "t" }] }} onChange={vi.fn()} ctx={ctx} />)
    expect(screen.getByText("Group by")).toBeInTheDocument()
    expect(screen.getByText("empty = summarise the whole frame")).toBeInTheDocument()
  })
})

describe("a new Add column step", () => {
  it("opens in Formula mode with an empty formula box", () => {
    expect(createStep("with_column", "w")).toEqual({
      id: "w",
      kind: "with_column",
      name: "",
      expr: { type: "binary", left: { kind: "column", name: "" }, op: "*", right: { kind: "literal", type: "number", value: 1 }, text: "" },
    })
  })
})
