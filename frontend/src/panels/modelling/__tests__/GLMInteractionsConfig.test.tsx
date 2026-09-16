import { cleanup, fireEvent, render, screen, within } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

import { GLMInteractionsConfig } from "../GLMInteractionsConfig"

const columns = [
  { name: "target", dtype: "Float64" },
  { name: "age", dtype: "Int64" },
  { name: "income", dtype: "Float64" },
  { name: "region", dtype: "String" },
  { name: "brand", dtype: "String" },
]

function setup(config: Record<string, unknown> = {}) {
  const onUpdate = vi.fn(() => ({ ok: true as const }))
  render(<GLMInteractionsConfig config={{ target: "target", algorithm: "glm", ...config }} onUpdate={onUpdate} columns={columns} />)
  return onUpdate
}

const card = (n: number) => screen.getByRole("group", { name: `Interaction ${n}` })

describe("GLMInteractionsConfig", () => {
  afterEach(cleanup)

  it("Add interaction appends two empty slots with include_main true", () => {
    const onUpdate = setup()
    fireEvent.click(screen.getByRole("button", { name: "Add interaction" }))
    expect(onUpdate).toHaveBeenCalledWith("interactions", [{ factors: ["", ""], include_main: true }])
  })

  it("picking a column writes factors[i] and no specs entry", () => {
    const onUpdate = setup({ interactions: [{ factors: ["", ""], include_main: true }] })
    fireEvent.change(within(card(1)).getByRole("combobox", { name: "Interaction 1 feature 1" }), { target: { value: "age" } })
    expect(onUpdate).toHaveBeenCalledWith("interactions", [{ factors: ["age", ""], include_main: true }])
  })

  it("As main term is offered only for columns with a non-monotone native term", () => {
    setup({
      terms: { age: { type: "linear" }, income: { type: "ms", df: 4, monotonicity: "increasing" } },
      interactions: [{ factors: ["age", "income"], include_main: true }],
    })
    const ageFit = within(card(1)).getByRole("combobox", { name: "age fit in interaction" })
    expect(ageFit).toHaveValue("main")
    const incomeFit = within(card(1)).getByRole("combobox", { name: "income fit in interaction" })
    expect(incomeFit).toHaveValue("")
    expect(within(card(1)).getByText("Monotone splines cannot be used inside interactions")).toBeInTheDocument()
  })

  it("Linear and splines are refused for string columns and categorical main terms", () => {
    setup({ terms: { region: { type: "categorical" } }, interactions: [{ factors: ["region", "brand"], include_main: true }] })
    for (const column of ["region", "brand"]) {
      const select = within(card(1)).getByRole("combobox", { name: `${column} fit in interaction` })
      for (const value of ["linear", "bs", "ns"]) {
        expect(select.querySelector(`option[value="${value}"]`)).toBeDisabled()
      }
      expect(select.querySelector('option[value="categorical"]')).not.toBeDisabled()
    }
  })

  it("Categorical is refused for non-categorical main terms", () => {
    setup({ terms: { age: { type: "linear" } }, interactions: [{ factors: ["age", "region"], include_main: true }] })
    const select = within(card(1)).getByRole("combobox", { name: "age fit in interaction" })
    expect(select.querySelector('option[value="categorical"]')).toBeDisabled()
  })

  it("B-spline override writes type, df and degree and never monotonicity; As main term deletes it", () => {
    const onUpdate = setup({ terms: { age: { type: "linear" } }, interactions: [{ factors: ["age", "region"], include_main: true }] })
    fireEvent.change(within(card(1)).getByRole("combobox", { name: "age fit in interaction" }), { target: { value: "bs" } })
    expect(onUpdate).toHaveBeenCalledWith("interactions", [
      { factors: ["age", "region"], specs: { age: { type: "bs" } }, include_main: true },
    ])
    cleanup()
    const onUpdate2 = setup({
      terms: { age: { type: "linear" } },
      interactions: [{ factors: ["age", "region"], specs: { age: { type: "bs" } }, include_main: true }],
    })
    expect(within(card(1)).queryByRole("button", { name: /^age: / })).not.toBeInTheDocument()
    fireEvent.change(within(card(1)).getByRole("spinbutton", { name: "age df" }), { target: { value: "5" } })
    expect(onUpdate2).toHaveBeenCalledWith("interactions", [
      { factors: ["age", "region"], specs: { age: { type: "bs", df: 5 } }, include_main: true },
    ])
    fireEvent.change(within(card(1)).getByRole("combobox", { name: "age fit in interaction" }), { target: { value: "main" } })
    expect(onUpdate2).toHaveBeenCalledWith("interactions", [{ factors: ["age", "region"], include_main: true }])
  })

  it("replacing a slot's column deletes the old override", () => {
    const onUpdate = setup({
      interactions: [{ factors: ["age", "region"], specs: { age: { type: "bs", df: 3 } }, include_main: true }],
    })
    fireEvent.change(within(card(1)).getByRole("combobox", { name: "Interaction 1 feature 1" }), { target: { value: "income" } })
    expect(onUpdate).toHaveBeenCalledWith("interactions", [{ factors: ["income", "region"], include_main: true }])
  })

  it("+ feature adds a third slot and a slot can be removed only above two", () => {
    const onUpdate = setup({ interactions: [{ factors: ["age", "region"], include_main: true }] })
    expect(within(card(1)).queryByRole("button", { name: /Remove feature/ })).not.toBeInTheDocument()
    fireEvent.click(within(card(1)).getByRole("button", { name: "+ feature" }))
    expect(onUpdate).toHaveBeenCalledWith("interactions", [{ factors: ["age", "region", ""], include_main: true }])
    cleanup()
    const onUpdate2 = setup({ interactions: [{ factors: ["age", "region", "income"], include_main: true }] })
    fireEvent.click(within(card(1)).getByRole("button", { name: "Remove feature 3" }))
    expect(onUpdate2).toHaveBeenCalledWith("interactions", [{ factors: ["age", "region"], include_main: true }])
  })

  it("Include main effects appears only while a picked column has no native term", () => {
    setup({ terms: { age: { type: "linear" }, region: { type: "categorical" } }, interactions: [{ factors: ["age", "region"], include_main: true }] })
    expect(screen.queryByRole("checkbox", { name: "Include main effects" })).not.toBeInTheDocument()
    cleanup()
    const onUpdate = setup({ terms: { age: { type: "linear" } }, interactions: [{ factors: ["age", "region"], include_main: true }] })
    const box = screen.getByRole("checkbox", { name: "Include main effects" })
    expect(box).toBeChecked()
    fireEvent.click(box)
    expect(onUpdate).toHaveBeenCalledWith("interactions", [{ factors: ["age", "region"], include_main: false }])
  })

  it("a card with one column shows the incomplete note", () => {
    setup({ interactions: [{ factors: ["age", ""], include_main: true }] })
    expect(within(card(1)).getByText("Pick at least two features")).toBeInTheDocument()
  })

  it("the same column cannot be picked twice in one card", () => {
    setup({ interactions: [{ factors: ["age", ""], include_main: true }] })
    const second = within(card(1)).getByRole("combobox", { name: "Interaction 1 feature 2" })
    expect(second.querySelector('option[value="age"]')).toBeNull()
  })

  it("columns with a target-encoding main term are not offered in slots", () => {
    setup({ terms: { brand: { type: "target_encoding" } }, interactions: [{ factors: ["", ""], include_main: true }] })
    const first = within(card(1)).getByRole("combobox", { name: "Interaction 1 feature 1" })
    expect(first.querySelector('option[value="brand"]')).toBeNull()
    expect(first).toHaveAttribute("title", expect.stringContaining("Target-encoded features cannot be interacted"))
  })

  it("a card duplicating another's factor set shows Duplicate interaction", () => {
    setup({
      interactions: [
        { factors: ["age", "region"], include_main: true },
        { factors: ["region", "age"], include_main: true },
      ],
    })
    expect(within(card(1)).queryByText("Duplicate interaction")).not.toBeInTheDocument()
    expect(within(card(2)).getByText("Duplicate interaction")).toBeInTheDocument()
  })

  it("removing a card removes its entry", () => {
    const onUpdate = setup({
      interactions: [
        { factors: ["age", "region"], include_main: true },
        { factors: ["income", "region"], include_main: true },
      ],
    })
    fireEvent.click(within(card(1)).getByRole("button", { name: "Remove interaction 1" }))
    expect(onUpdate).toHaveBeenCalledWith("interactions", [{ factors: ["income", "region"], include_main: true }])
  })
})
