import { describe, it, expect, vi, afterEach } from "vitest"
import { render, screen, cleanup, fireEvent, within } from "@testing-library/react"
import { useState } from "react"

import { summarizeStep } from "../summary"
import { MAX_EXPR_DEPTH, stepProblem } from "../catalogue"
import { parseFormula } from "../formula"
import { StepForm, type StepFormContext } from "../forms"
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
    expect(screen.queryByLabelText("Variable value source")).not.toBeInTheDocument()
    expect(optionValues(screen.getByLabelText("Variable value type"))).toEqual(["number", "text", "boolean"])
    fireEvent.change(screen.getByLabelText("Variable value type"), { target: { value: "text" } })
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
    expect(optionValues(screen.getByLabelText("Filter condition 1 value type"))).toEqual(["number", "text", "boolean", "date"])
    fireEvent.change(screen.getByLabelText("Filter condition 1 operator"), { target: { value: "contains" } })
    const next = onChange.mock.calls.at(-1)?.[0] as Extract<Step, { kind: "filter" }>
    expect(next.conditions[0]).toEqual({ column: "region", operator: "contains", value: { kind: "literal", type: "text", value: "" } })
    cleanup()
    render(<StepForm step={next} onChange={onChange} ctx={ctx} />)
    expect(screen.queryByLabelText("Filter condition 1 value type")).not.toBeInTheDocument()
    expect(optionValues(screen.getByLabelText("Filter condition 1 value source"))).toEqual(["literal", "column", "variable", "expr"])
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
    expect(optionValues(screen.getByLabelText("Type"))).toEqual(["Int8", "Int16", "Int32", "Int64", "UInt8", "UInt16", "UInt32", "UInt64", "Float32", "Float64", "String", "Boolean", "Date", "Datetime", "Categorical"])
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
    expect(optionValues(screen.getByLabelText("Otherwise value type"))).toEqual(["number", "text", "boolean", "date", "null"])
    fireEvent.change(screen.getByLabelText("Otherwise value type"), { target: { value: "null" } })
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
    fireEvent.click(screen.getByRole("button", { name: /Only some rows/ }))
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
    expect(optionValues(screen.getByLabelText("Function operand source"))).toEqual(["literal", "column", "variable", "expr"])
    fireEvent.change(screen.getByLabelText("Function operand source"), { target: { value: "expr" } })
    let latest = spy.mock.calls.at(-1)?.[0] as Extract<Step, { kind: "with_column" }>
    expect(latest.expr).toEqual({
      ...step.expr,
      operand: { kind: "expr", expr: { type: "binary", left: { kind: "column", name: "" }, op: "*", right: { kind: "literal", type: "number", value: 1 }, text: "" } },
    })
    expect(screen.getByRole("group", { name: "Function operand expression" })).toBeInTheDocument()
    // a new formula box starts empty, with an example as its tooltip only
    expect(screen.getByLabelText("Formula")).toHaveValue("")
    expect(screen.getByLabelText("Formula")).not.toHaveAttribute("placeholder")
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
    const sources = screen.getAllByLabelText(/source$/).map((el) => optionValues(el))
    expect(sources).toHaveLength(12)
    expect(sources.filter((s) => s.includes("expr"))).toHaveLength(11)
    // Variables and function arguments stay plain values.
    cleanup()
    const variable: Step = { id: "v", kind: "variable", name: "rate", value: { kind: "literal", type: "number", value: 1 } }
    render(<StepForm step={variable} onChange={vi.fn()} ctx={ctx} />)
    expect(screen.queryByLabelText("Variable value source")).not.toBeInTheDocument()
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
    fireEvent.click(screen.getByRole("button", { name: /Only some rows/ }))
    expect(optionValues(screen.getByLabelText("Aggregation 1 filter condition 1 value source"))).toEqual(["literal", "column", "variable"])
    fireEvent.change(screen.getByLabelText("Aggregation 1 target"), { target: { value: "dtype" } })
    let latest = spy.mock.calls.at(-1)?.[0] as Extract<Step, { kind: "group_by" }>
    expect(latest.aggregations[0]).toEqual({ dtype: "Float64", agg: "sum", suffix: "" })
    fireEvent.change(screen.getByLabelText("Aggregation 1 suffix"), { target: { value: "_total" } })
    fireEvent.blur(screen.getByLabelText("Aggregation 1 suffix"))
    fireEvent.change(screen.getByLabelText("Aggregation 1 column type"), { target: { value: "Int64" } })
    latest = spy.mock.calls.at(-1)?.[0] as Extract<Step, { kind: "group_by" }>
    expect(latest.aggregations[0]).toEqual({ dtype: "Int64", agg: "sum", suffix: "_total" })
    expect(optionValues(screen.getByLabelText("Aggregation 1 function"))).not.toContain("len")
    fireEvent.change(screen.getByLabelText("Aggregation 1 target"), { target: { value: "column" } })
    latest = spy.mock.calls.at(-1)?.[0] as Extract<Step, { kind: "group_by" }>
    expect(latest.aggregations[0]).toEqual({ column: "", agg: "sum", name: "" })
  })

  it("switching an aggregation's target keeps its quantile", () => {
    const spy = vi.fn()
    const step: Step = { id: "g", kind: "group_by", keys: [], aggregations: [{ column: "premium", agg: "quantile", name: "p90", quantile: 0.9 }] }
    render(<Stateful initial={step} spy={spy} />)
    fireEvent.change(screen.getByLabelText("Aggregation 1 target"), { target: { value: "dtype" } })
    let latest = spy.mock.calls.at(-1)?.[0] as Extract<Step, { kind: "group_by" }>
    expect(latest.aggregations[0]).toEqual({ dtype: "Float64", agg: "quantile", suffix: "", quantile: 0.9 })
    fireEvent.change(screen.getByLabelText("Aggregation 1 target"), { target: { value: "column" } })
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
    fireEvent.change(screen.getByLabelText("Pivot column 1 value type"), { target: { value: "number" } })
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
    const sources = screen.getAllByLabelText(/source$/).map((el) => optionValues(el))
    // the condition value plus twelve function operands; only the innermost, at level twelve, cannot nest further
    expect(sources).toHaveLength(13)
    expect(sources.filter((s) => s.includes("expr"))).toHaveLength(12)
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
    expect(screen.getByText(/Not understood: Missing a closing bracket/)).toBeInTheDocument()
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
    const list = screen.getByRole("listbox", { name: "Matching columns" })
    expect(within(list).getAllByRole("option").map((o) => o.textContent)).toEqual(["premium", "premium_net"])
    expect(within(list).getByRole("option", { name: "premium" })).toHaveAttribute("aria-selected", "true")
    fireEvent.keyDown(input, { key: "ArrowDown" })
    expect(within(list).getByRole("option", { name: "premium_net" })).toHaveAttribute("aria-selected", "true")
    fireEvent.keyDown(input, { key: "Tab" })
    expect(input).toHaveValue("premium_net")
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument()
    // keeps typing: the word under the caret drives the list, variables included
    fireEvent.change(input, { target: { value: "premium_net * ra" } })
    expect(within(screen.getByRole("listbox")).getAllByRole("option").map((o) => o.textContent)).toEqual(["rate", "rate_var"])
    fireEvent.keyDown(input, { key: "Escape" })
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument()
    fireEvent.keyDown(input, { key: "Enter" })
    expect(spy).toHaveBeenLastCalledWith(expect.objectContaining({ expr: expect.objectContaining({ text: "premium_net * ra" }) }))
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
    fireEvent.change(screen.getByRole("combobox", { name: "Formula" }), { target: { value: "tot" } })
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
    expect(optionValues(screen.getByLabelText("Pivot column 1 value type"))).toEqual(["number", "text", "boolean", "date"])
    expect(screen.queryByLabelText("Pivot column 2 value type")).not.toBeInTheDocument()
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
    expect(optionValues(screen.getByLabelText("Part 2 type"))).toEqual(["number", "text", "boolean", "date", "null"])
  })

  it("a variable operand is offered only when an earlier variable exists", () => {
    const step: Step = {
      id: "f",
      kind: "filter",
      match: "all",
      conditions: [{ column: "premium", operator: "gt", value: { kind: "literal", type: "number", value: 1 } }],
    }
    render(<StepForm step={step} onChange={vi.fn()} ctx={{ ...ctx, variables: [] }} />)
    expect(optionValues(screen.getByLabelText("Filter condition 1 value source"))).toEqual(["literal", "column", "expr"])
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
    const add = screen.getByRole("combobox", { name: "Unique by (empty = all columns): add" })
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
    expect(optionValues(screen.getByLabelText("Fill value source"))).toEqual(["literal", "column", "variable", "expr"])
    expect(optionValues(screen.getByLabelText("Fill value type"))).toEqual(["number", "text", "boolean", "date"])
    fireEvent.change(screen.getByLabelText("Fill value source"), { target: { value: "column" } })
    expect(spy).toHaveBeenLastCalledWith({ id: "n", kind: "fill_null", columns: [], fill: { kind: "value", value: { kind: "column", name: "" } } })
    fireEvent.change(screen.getByLabelText("Fill kind"), { target: { value: "strategy" } })
    expect(spy).toHaveBeenLastCalledWith({ id: "n", kind: "fill_null", columns: [], fill: { kind: "strategy", strategy: "forward" } })
    fireEvent.change(screen.getByLabelText("Fill strategy"), { target: { value: "mean" } })
    expect(spy).toHaveBeenLastCalledWith({ id: "n", kind: "fill_null", columns: [], fill: { kind: "strategy", strategy: "mean" } })
    expect(summarizeStep(spy.mock.calls.at(-1)?.[0] as Step)).toBe("all columns with mean")
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
    expect(screen.getByLabelText("Left operand source")).toHaveValue("expr")
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
    fireEvent.change(screen.getByLabelText("Otherwise value source"), { target: { value: "column" } })
    expect((spy.mock.calls.at(-1)?.[0] as Extract<Step, { kind: "with_column" }>).expr).toMatchObject({ otherwise: { kind: "column", name: "" } })
    fireEvent.click(screen.getByRole("button", { name: "Add condition" }))
    const latest = spy.mock.calls.at(-1)?.[0] as Extract<Step, { kind: "with_column" }>
    expect(latest.expr).toMatchObject({ type: "conditional", conditions: [expect.anything(), { column: "", operator: "eq" }] })
    expect(screen.getByLabelText("If match")).toHaveValue("all")
    expect(summarizeStep(latest)).toBe("band = if premium is greater than 100 and ? equals 0 then 'top' else ?")
  })
})
