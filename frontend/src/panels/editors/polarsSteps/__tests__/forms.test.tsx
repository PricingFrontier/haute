import { describe, it, expect, vi, afterEach } from "vitest"
import { render, screen, cleanup, fireEvent } from "@testing-library/react"
import { useState } from "react"

import { StepForm, type StepFormContext } from "../forms"
import type { Step } from "../types"

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
    expect(latest.expr).toEqual({ type: "window", agg: "row_number", column: "premium", over: ["region"], orderBy: [{ column: "premium", descending: true }] })
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
      parts: [{ kind: "column", name: "region" }, { kind: "column", name: "premium" }, { kind: "column", name: "premium" }],
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
    expect(latest.aggregations[0].where).toEqual({
      match: "all",
      conditions: [{ column: "premium", operator: "eq", value: { kind: "literal", type: "number", value: 0 } }],
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
    const step: Step = {
      id: "w",
      kind: "with_column",
      name: "rate",
      expr: { type: "binary", left: { kind: "column", name: "premium" }, op: "/", right: { kind: "literal", type: "number", value: 1 } },
    }
    render(<Stateful initial={step} spy={spy} />)
    expect(optionValues(screen.getByLabelText("Right operand source"))).toEqual(["literal", "column", "variable", "expr"])
    fireEvent.change(screen.getByLabelText("Right operand source"), { target: { value: "expr" } })
    let latest = spy.mock.calls.at(-1)?.[0] as Extract<Step, { kind: "with_column" }>
    expect(latest.expr).toEqual({
      ...step.expr,
      right: { kind: "expr", expr: { type: "binary", left: { kind: "column", name: "premium" }, op: "*", right: { kind: "literal", type: "number", value: 1 } } },
    })
    expect(screen.getByRole("group", { name: "Right operand expression" })).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText("Right operand expression type"), { target: { value: "function" } })
    latest = spy.mock.calls.at(-1)?.[0] as Extract<Step, { kind: "with_column" }>
    expect(latest.expr).toMatchObject({ right: { kind: "expr", expr: { type: "function", fn: "abs" } } })
    // The deepest level the renderer accepts offers no further nesting.
    cleanup()
    let operand: Extract<Step, { kind: "with_column" }>["expr"] = { type: "binary", left: { kind: "column", name: "premium" }, op: "+", right: { kind: "literal", type: "number", value: 1 } }
    for (let i = 0; i < 5; i += 1) operand = { type: "binary", left: { kind: "expr", expr: operand }, op: "+", right: { kind: "literal", type: "number", value: 1 } }
    render(<StepForm step={{ id: "d", kind: "with_column", name: "deep", expr: operand }} onChange={vi.fn()} ctx={ctx} />)
    const sources = screen.getAllByLabelText(/source$/).map((el) => optionValues(el))
    expect(sources.some((s) => s.includes("expr"))).toBe(true)
    expect(sources.filter((s) => !s.includes("expr")).length).toBeGreaterThan(0)
    cleanup()
    const variable: Step = { id: "v", kind: "variable", name: "rate", value: { kind: "literal", type: "number", value: 1 } }
    render(<StepForm step={variable} onChange={vi.fn()} ctx={ctx} />)
    expect(screen.queryByLabelText("Variable value source")).not.toBeInTheDocument()
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
})
