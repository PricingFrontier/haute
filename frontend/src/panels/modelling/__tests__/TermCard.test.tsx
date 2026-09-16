import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

import { TermCard } from "../TermCard"

afterEach(cleanup)

describe("TermCard native", () => {
  it("offers the six native types and never Expression", () => {
    render(
      <TermCard kind="native" column="age" spec={{ type: "linear" }} onChangeType={vi.fn()} onChangeField={vi.fn()} onRemove={vi.fn()} />,
    )
    const select = screen.getByRole("combobox", { name: "age term type" })
    const options = Array.from(select.querySelectorAll("option")).map((o) => o.value)
    expect(options).toEqual(["linear", "categorical", "bs", "ns", "ms", "target_encoding"])
  })

  it("shows df and degree for bs, df only for ns, prior weight for target encoding", () => {
    const { rerender } = render(
      <TermCard kind="native" column="age" spec={{ type: "bs", df: 4 }} onChangeType={vi.fn()} onChangeField={vi.fn()} onRemove={vi.fn()} />,
    )
    expect(screen.getByRole("spinbutton", { name: "age df" })).toHaveValue(4)
    expect(screen.getByRole("spinbutton", { name: "age degree" })).toBeInTheDocument()
    rerender(
      <TermCard kind="native" column="age" spec={{ type: "ns", df: 3 }} onChangeType={vi.fn()} onChangeField={vi.fn()} onRemove={vi.fn()} />,
    )
    expect(screen.getByRole("spinbutton", { name: "age df" })).toHaveValue(3)
    expect(screen.queryByRole("spinbutton", { name: "age degree" })).not.toBeInTheDocument()
    expect(screen.queryByRole("group", { name: "age monotonicity" })).not.toBeInTheDocument()
    rerender(
      <TermCard kind="native" column="brand" spec={{ type: "target_encoding" }} onChangeType={vi.fn()} onChangeField={vi.fn()} onRemove={vi.fn()} />,
    )
    expect(screen.getByRole("spinbutton", { name: "brand prior weight" })).toHaveValue(1)
  })

  it("renders three monotonicity arrows for linear and bs, two for ms", () => {
    const { rerender } = render(
      <TermCard kind="native" column="age" spec={{ type: "linear" }} onChangeType={vi.fn()} onChangeField={vi.fn()} onRemove={vi.fn()} />,
    )
    expect(screen.getAllByRole("button", { name: /^age: / })).toHaveLength(3)
    rerender(
      <TermCard kind="native" column="age" spec={{ type: "ms", df: 4, monotonicity: "increasing" }} onChangeType={vi.fn()} onChangeField={vi.fn()} onRemove={vi.fn()} />,
    )
    expect(screen.getAllByRole("button", { name: /^age: / })).toHaveLength(2)
    expect(screen.getByRole("button", { name: "age: increasing" })).toHaveAttribute("aria-pressed", "true")
  })

  it("routes edits to the callbacks", () => {
    const onChangeType = vi.fn()
    const onChangeField = vi.fn()
    const onRemove = vi.fn()
    render(
      <TermCard kind="native" column="age" spec={{ type: "bs", df: 4 }} onChangeType={onChangeType} onChangeField={onChangeField} onRemove={onRemove} />,
    )
    fireEvent.change(screen.getByRole("combobox", { name: "age term type" }), { target: { value: "ns" } })
    expect(onChangeType).toHaveBeenCalledWith("ns")
    fireEvent.change(screen.getByRole("spinbutton", { name: "age df" }), { target: { value: "6" } })
    expect(onChangeField).toHaveBeenCalledWith("df", 6)
    fireEvent.change(screen.getByRole("spinbutton", { name: "age df" }), { target: { value: "" } })
    expect(onChangeField).toHaveBeenCalledWith("df", undefined)
    fireEvent.click(screen.getByRole("button", { name: "age: decreasing" }))
    expect(onChangeField).toHaveBeenCalledWith("monotonicity", "decreasing")
    fireEvent.click(screen.getByRole("button", { name: "age: no constraint" }))
    expect(onChangeField).toHaveBeenCalledWith("monotonicity", undefined)
    fireEvent.click(screen.getByRole("button", { name: "Remove age term" }))
    expect(onRemove).toHaveBeenCalled()
  })
})

describe("TermCard expression", () => {
  it("shows a fixed Expression label, name and expression fields, and the grammar as help", () => {
    render(
      <TermCard kind="expression" termKey="age_sq" spec={{ type: "expression", expr: "age ** 2" }} onRename={() => ({ ok: true, terms: {} })} onChangeExpr={() => ({ ok: true, terms: {} })} onChangeField={vi.fn()} onRemove={vi.fn()} />,
    )
    expect(screen.getByText("Expression")).toBeInTheDocument()
    expect(screen.queryByRole("combobox")).not.toBeInTheDocument()
    expect(screen.getByRole("textbox", { name: "age_sq name" })).toHaveValue("age_sq")
    expect(screen.getByRole("textbox", { name: "age_sq expression" })).toHaveValue("age ** 2")
    expect(screen.getByRole("textbox", { name: "age_sq expression" })).toHaveAttribute("title", expect.stringContaining("Supported forms"))
  })

  it("keeps the draft and shows the reason when a rename or expression edit is refused", () => {
    const onRename = vi.fn(() => ({ ok: false as const, reason: "income is a column; expression names must not be columns." }))
    const onChangeExpr = vi.fn(() => ({ ok: false as const, reason: "Unknown column: height." }))
    render(
      <TermCard kind="expression" termKey="age_sq" spec={{ type: "expression", expr: "age ** 2" }} onRename={onRename} onChangeExpr={onChangeExpr} onChangeField={vi.fn()} onRemove={vi.fn()} />,
    )
    const name = screen.getByRole("textbox", { name: "age_sq name" })
    fireEvent.change(name, { target: { value: "income" } })
    fireEvent.blur(name)
    expect(onRename).toHaveBeenCalledWith("income")
    expect(name).toHaveValue("income")
    expect(screen.getByRole("alert")).toHaveTextContent("income is a column")
    const expr = screen.getByRole("textbox", { name: "age_sq expression" })
    fireEvent.change(expr, { target: { value: "height ** 2" } })
    fireEvent.blur(expr)
    expect(onChangeExpr).toHaveBeenCalledWith("height ** 2")
    expect(expr).toHaveValue("height ** 2")
    expect(screen.getByRole("alert")).toHaveTextContent("Unknown column: height")
  })
})

describe("TermCard slot", () => {
  it("offers only honoured fits, disabling the rest with a reason", () => {
    render(
      <TermCard kind="slot" column="age" dtype="Float64" mainSpec={{ type: "linear" }} override={undefined} onChangeFit={vi.fn()} onChangeField={vi.fn()} />,
    )
    const select = screen.getByRole("combobox", { name: "age fit in interaction" })
    const options = Array.from(select.querySelectorAll("option"))
    expect(options.map((o) => o.value)).toEqual(["main", "linear", "categorical", "bs", "ns"])
    expect(options.find((o) => o.value === "categorical")).toBeDisabled()
    expect(select).toHaveValue("main")
  })

  it("shows spline fields for a spline override and never a monotonicity control", () => {
    render(
      <TermCard kind="slot" column="age" dtype="Float64" mainSpec={null} override={{ type: "bs", df: 3 }} onChangeFit={vi.fn()} onChangeField={vi.fn()} />,
    )
    expect(screen.getByRole("combobox", { name: "age fit in interaction" })).toHaveValue("bs")
    expect(screen.getByRole("spinbutton", { name: "age df" })).toHaveValue(3)
    expect(screen.getByRole("spinbutton", { name: "age degree" })).toBeInTheDocument()
    expect(screen.queryByRole("button", { name: /^age: / })).not.toBeInTheDocument()
  })

  it("demands an explicit fit for a monotone main term", () => {
    render(
      <TermCard kind="slot" column="age" dtype="Float64" mainSpec={{ type: "ms", df: 4, monotonicity: "increasing" }} override={undefined} onChangeFit={vi.fn()} onChangeField={vi.fn()} />,
    )
    expect(screen.getByText("Monotone splines cannot be used inside interactions")).toBeInTheDocument()
    const select = screen.getByRole("combobox", { name: "age fit in interaction" })
    expect(select).toHaveValue("")
  })

  it("never emits an empty fit from the placeholder", () => {
    const onChangeFit = vi.fn()
    render(
      <TermCard kind="slot" column="age" dtype="Float64" mainSpec={{ type: "ms", df: 4, monotonicity: "increasing" }} override={undefined} onChangeFit={onChangeFit} onChangeField={vi.fn()} />,
    )
    const select = screen.getByRole("combobox", { name: "age fit in interaction" })
    const placeholder = Array.from(select.querySelectorAll("option")).find((o) => o.value === "")
    expect(placeholder).toBeDisabled()
    expect(select).toHaveValue("")
    fireEvent.change(select, { target: { value: "" } })
    expect(onChangeFit).not.toHaveBeenCalled()
  })
})
