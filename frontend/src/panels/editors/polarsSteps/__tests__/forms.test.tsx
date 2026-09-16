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
    expect(optionValues(screen.getByLabelText("Filter condition 1 value source"))).toEqual(["literal", "column", "variable"])
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
    expect(optionValues(screen.getByLabelText("Type"))).toEqual(["Int64", "Float64", "String", "Boolean", "Date", "Datetime", "Categorical"])
    fireEvent.change(screen.getByLabelText("Type"), { target: { value: "Int64" } })
    const next = onChange.mock.calls.at(-1)?.[0] as Extract<Step, { kind: "with_column" }>
    expect(next.expr).toMatchObject({ fn: "cast", args: [{ kind: "literal", type: "text", value: "Int64" }] })
  })

  it("a variable operand is offered only when an earlier variable exists", () => {
    const step: Step = {
      id: "f",
      kind: "filter",
      match: "all",
      conditions: [{ column: "premium", operator: "gt", value: { kind: "literal", type: "number", value: 1 } }],
    }
    render(<StepForm step={step} onChange={vi.fn()} ctx={{ ...ctx, variables: [] }} />)
    expect(optionValues(screen.getByLabelText("Filter condition 1 value source"))).toEqual(["literal", "column"])
  })
})
