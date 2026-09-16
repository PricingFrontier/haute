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

  it("Linear and splines are omitted for string columns and categorical main terms", () => {
    setup({ terms: { region: { type: "categorical" } }, interactions: [{ factors: ["region", "brand"], include_main: true }] })
    for (const column of ["region", "brand"]) {
      const select = within(card(1)).getByRole("combobox", { name: `${column} fit in interaction` })
      for (const value of ["linear", "bs", "ns"]) {
        expect(select.querySelector(`option[value="${value}"]`)).toBeNull()
      }
      expect(select.querySelector('option[value="categorical"]')).not.toBeDisabled()
    }
  })

  it("Categorical is omitted for non-categorical main terms", () => {
    setup({ terms: { age: { type: "linear" } }, interactions: [{ factors: ["age", "region"], include_main: true }] })
    const select = within(card(1)).getByRole("combobox", { name: "age fit in interaction" })
    expect(select.querySelector('option[value="categorical"]')).toBeNull()
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
    fireEvent.change(within(card(1)).getByRole("combobox", { name: "age df mode" }), { target: { value: "fixed" } })
    expect(onUpdate2).toHaveBeenCalledWith("interactions", [
      { factors: ["age", "region"], specs: { age: { type: "bs", df: 5 } }, include_main: true },
    ])
    fireEvent.change(within(card(1)).getByRole("combobox", { name: "age fit in interaction" }), { target: { value: "main" } })
    expect(onUpdate2).toHaveBeenCalledWith("interactions", [{ factors: ["age", "region"], include_main: true }])
  })

  it("writes an explicit target encoding for a categorical factor with a linear partner", () => {
    const onUpdate = setup({
      terms: { region: { type: "categorical" }, age: { type: "linear" } },
      interactions: [{ factors: ["region", "age"], include_main: false }],
    })
    const regionFit = within(card(1)).getByRole("combobox", { name: "region fit in interaction" })
    expect(regionFit.querySelector('option[value="target_encoding"]')).not.toBeNull()
    fireEvent.change(regionFit, { target: { value: "target_encoding" } })
    expect(onUpdate).toHaveBeenCalledWith("interactions", [{
      factors: ["region", "age"], specs: { region: { type: "target_encoding" } }, include_main: false,
    }])
  })

  it("uses shared named target encoding settings and can reset a stale override", () => {
    const onUpdate = setup({
      terms: { region_te: { type: "target_encoding", variable: "region" }, age: { type: "linear" } },
      interactions: [{ factors: ["region", "age"], specs: { region: { type: "target_encoding", prior_weight: 2, n_permutations: 7 } }, include_main: true }],
    })
    const group = within(card(1))
    expect(group.getByText("Uses target encoding settings from region_te.")).toBeInTheDocument()
    expect(group.queryByRole("combobox", { name: "region prior weight mode" })).not.toBeInTheDocument()
    fireEvent.click(group.getByRole("button", { name: "Use shared settings" }))
    expect(onUpdate).toHaveBeenCalledWith("interactions", [{
      factors: ["region", "age"], specs: { region: { type: "target_encoding" } }, include_main: true,
    }])
  })

  it("keeps interaction editing available when an unrelated saved term is malformed", () => {
    setup({
      terms: { broken: null, region: { type: "categorical" } },
      interactions: [{ factors: ["region", "age"], include_main: false }],
    })
    expect(within(card(1)).getByRole("combobox", { name: "region fit in interaction" })
      .querySelector('option[value="target_encoding"]')).not.toBeNull()
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

  it("offers an encoded native feature in product slots when a compatible linear partner is selected", () => {
    setup({ terms: { brand: { type: "target_encoding" } }, interactions: [{ factors: ["", ""], include_main: true }] })
    const first = within(card(1)).getByRole("combobox", { name: "Interaction 1 feature 1" })
    expect(first.querySelector('option[value="brand"]')).not.toBeNull()
  })

  it("keeps an invalid selected encoded product factor visible and explained without writing", () => {
    const onUpdate = setup({ terms: { brand: { type: "target_encoding" } }, interactions: [{ factors: ["brand", "region"], include_main: true }] })
    const group = within(card(1))
    const feature = group.getByRole("combobox", { name: "Interaction 1 feature 1" })
    expect(feature).toHaveValue("brand")
    expect(feature.querySelector('option[value="brand"]')).toBeDisabled()
    expect(group.getAllByRole("alert")[0]).toHaveTextContent("This feature has no compatible fit")
    expect(onUpdate).not.toHaveBeenCalled()
  })

  it("omits incompatible candidates but retains a selected one for repair", () => {
    setup({ terms: { brand: { type: "target_encoding" } }, interactions: [{ factors: ["", "region"], include_main: true }] })
    expect(within(card(1)).getByRole("combobox", { name: "Interaction 1 feature 1" }).querySelector('option[value="brand"]')).toBeNull()
  })

  it("switches joint modes atomically and hides product fit controls", () => {
    const onUpdate = setup({ terms: { brand: { type: "target_encoding" }, region: { type: "frequency_encoding" } }, interactions: [{ factors: ["brand", "region"], specs: { region: { type: "categorical" } }, include_main: true }] })
    const group = within(card(1))
    fireEvent.change(group.getByRole("combobox", { name: "Interaction 1 encoding" }), { target: { value: "target_encoding" } })
    expect(onUpdate).toHaveBeenCalledWith("interactions", [{ factors: ["brand", "region"], encoding: "target_encoding", include_main: true }])
    cleanup()
    const next = setup({ terms: { brand: { type: "target_encoding" }, region: { type: "frequency_encoding" } }, interactions: [{ factors: ["brand", "region"], encoding: "target_encoding", include_main: true }] })
    expect(within(card(1)).getByText("Encodes the combination of raw feature values.")).toBeInTheDocument()
    expect(within(card(1)).queryByRole("combobox", { name: "brand fit in interaction" })).not.toBeInTheDocument()
    expect(within(card(1)).getByRole("combobox", { name: "Interaction 1 feature 1" }).querySelector('option[value="brand"]')).not.toBeNull()
    fireEvent.change(within(card(1)).getByRole("combobox", { name: "Interaction 1 prior weight mode" }), { target: { value: "fixed" } })
    expect(next).toHaveBeenCalledWith("interactions", [{ factors: ["brand", "region"], encoding: "target_encoding", prior_weight: 1, include_main: true }])
  })

  it("offers only Product mode when an interaction contains numeric factors", () => {
    setup({ interactions: [{ factors: ["age", "region"], include_main: true }] })
    const encoding = within(card(1)).getByRole("combobox", { name: "Interaction 1 encoding" })
    expect(Array.from(encoding.querySelectorAll("option"), (option) => option.value)).toEqual(["product"])
  })

  it("omits numeric features from joint mode choices", () => {
    setup({ interactions: [{ factors: ["region", ""], encoding: "frequency_encoding", include_main: true }] })
    const feature = within(card(1)).getByRole("combobox", { name: "Interaction 1 feature 2" })
    expect(feature.querySelector('option[value="age"]')).toBeNull()
    expect(feature.querySelector('option[value="income"]')).toBeNull()
    expect(feature.querySelector('option[value="brand"]')).not.toBeNull()
  })

  it("omits a mode already used by another interaction with the same factors while retaining the current mode", () => {
    setup({
      interactions: [
        { factors: ["brand", "region"], include_main: true },
        { factors: ["region", "brand"], encoding: "target_encoding", include_main: true },
      ],
    })
    const encoding = within(card(2)).getByRole("combobox", { name: "Interaction 2 encoding" })
    expect(encoding.querySelector('option[value="target_encoding"]')).not.toBeNull()
    expect(encoding.querySelector('option[value="product"]')).toBeNull()
  })

  it("keeps a legacy numeric joint interaction visible, warns, and does not write on render", () => {
    const onUpdate = setup({ interactions: [{ factors: ["age", "region"], encoding: "target_encoding", include_main: true }] })
    const group = within(card(1))
    expect(group.getByRole("combobox", { name: "Interaction 1 encoding" })).toHaveValue("target_encoding")
    expect(group.getByRole("combobox", { name: "Interaction 1 feature 1" }).querySelector('option[value="age"]')).toBeDisabled()
    expect(group.getByText("Joint encodings require categorical features")).toBeInTheDocument()
    expect(onUpdate).not.toHaveBeenCalled()
  })

  it("a target-encoding interaction exposes Advanced controls and writes permutations", () => {
    const onUpdate = setup({ interactions: [{ factors: ["brand", "region"], encoding: "target_encoding", include_main: true }] })
    const group = within(card(1))
    fireEvent.click(group.getByRole("button", { name: "Advanced" }))
    fireEvent.change(group.getByRole("spinbutton", { name: "Interaction 1 n permutations" }), { target: { value: "6" } })
    expect(onUpdate).toHaveBeenCalledWith("interactions", [{ factors: ["brand", "region"], encoding: "target_encoding", n_permutations: 6, include_main: true }])
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
