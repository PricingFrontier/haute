import { useState } from "react"
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

import type { OnUpdateConfig } from "../../editors/_shared"
import { GLMInteractionsConfig } from "../GLMInteractionsConfig"

const columns = [
  { name: "target", dtype: "Float64" },
  { name: "age", dtype: "Int64" },
  { name: "income", dtype: "Float64" },
  { name: "region", dtype: "String" },
  { name: "brand", dtype: "String" },
  { name: "flag", dtype: "Boolean" },
]

function setup(config: Record<string, unknown> = {}) {
  const onUpdate = vi.fn<OnUpdateConfig>(() => ({ ok: true as const }))
  render(<GLMInteractionsConfig config={{ target: "target", algorithm: "glm", ...config }} onUpdate={onUpdate} columns={columns} />)
  return onUpdate
}

function setupStateful(initial: Record<string, unknown>) {
  const latest: { config: Record<string, unknown> } = { config: {} }
  function Harness() {
    const [config, setConfig] = useState<Record<string, unknown>>({ target: "target", algorithm: "glm", ...initial })
    latest.config = config
    return (
      <GLMInteractionsConfig
        config={config}
        columns={columns}
        onUpdate={(keyOrUpdates, value) => {
          setConfig((current) => typeof keyOrUpdates === "string" ? { ...current, [keyOrUpdates]: value } : { ...current, ...keyOrUpdates })
          return { ok: true }
        }}
      />
    )
  }
  render(<Harness />)
  return latest
}

const card = (n: number) => screen.getByRole("group", { name: `Interaction ${n}` })
const optionValues = (select: HTMLElement) => Array.from(select.querySelectorAll("option")).map((option) => option.value)
const optionLabels = (select: HTMLElement) => Array.from(select.querySelectorAll("option")).map((option) => option.textContent)

describe("GLMInteractionsConfig", () => {
  afterEach(cleanup)

  it("Add interaction appends two empty slots and picking a column writes only the factor", () => {
    const latest = setupStateful({})
    fireEvent.click(screen.getByRole("button", { name: "Add interaction" }))
    expect(latest.config.interactions).toEqual([{ factors: ["", ""], include_main: true }])
    fireEvent.change(within(card(1)).getByRole("combobox", { name: "Interaction 1 feature 1" }), { target: { value: "age" } })
    expect(latest.config.interactions).toEqual([{ factors: ["age", ""], include_main: true }])
  })

  it("slot menus match the resolution rules", () => {
    setup({
      terms: { age: { type: "linear" }, income: { type: "ms", df: 4 }, region: { type: "categorical" } },
      interactions: [
        { factors: ["age", "income"], include_main: true },
        { factors: ["region", "brand"], include_main: true },
      ],
    })
    const ageFit = within(card(1)).getByRole("combobox", { name: "age fit in interaction" })
    expect(ageFit).toHaveValue("main")
    // The partner has no usable fit yet, so product target encoding is not offered.
    expect(optionLabels(ageFit)).toEqual(["As main (Linear)", "Linear", "B-spline", "Nat. spline"])
    expect(within(card(1)).getByRole("combobox", { name: "income fit in interaction" })).toHaveValue("")
    expect(within(card(1)).getByText("Monotone splines cannot be used inside interactions")).toBeInTheDocument()
    expect(optionValues(within(card(2)).getByRole("combobox", { name: "region fit in interaction" }))).toEqual(["main", "categorical"])
    expect(optionValues(within(card(2)).getByRole("combobox", { name: "brand fit in interaction" }))).toEqual(["categorical"])
  })

  it("writes a spline override and switches its mode through the spline transition", () => {
    const latest = setupStateful({ terms: { age: { type: "linear" } }, interactions: [{ factors: ["age", "region"], include_main: true }] })
    fireEvent.change(within(card(1)).getByRole("combobox", { name: "age fit in interaction" }), { target: { value: "bs" } })
    expect(latest.config.interactions).toEqual([{ factors: ["age", "region"], specs: { age: { type: "bs" } }, include_main: true }])
    expect(within(card(1)).queryByRole("group", { name: "age monotonicity" })).not.toBeInTheDocument()
    fireEvent.change(within(card(1)).getByRole("combobox", { name: "age df mode" }), { target: { value: "fixed" } })
    expect(latest.config.interactions).toEqual([{ factors: ["age", "region"], specs: { age: { type: "bs", df: 5 } }, include_main: true }])
    const df = within(card(1)).getByRole("spinbutton", { name: "age df" })
    fireEvent.change(df, { target: { value: "7" } })
    fireEvent.blur(df)
    expect(latest.config.interactions).toEqual([{ factors: ["age", "region"], specs: { age: { type: "bs", df: 7 } }, include_main: true }])
    fireEvent.change(within(card(1)).getByRole("combobox", { name: "age fit in interaction" }), { target: { value: "main" } })
    expect(latest.config.interactions).toEqual([{ factors: ["age", "region"], include_main: true }])
  })

  it("removing an interaction keeps the remaining card's draft and disclosure state", () => {
    const latest = setupStateful({
      interactions: [
        { factors: ["income", "region"], specs: { income: { type: "bs", df: 5 } }, include_main: true },
        { factors: ["age", "brand"], specs: { age: { type: "bs", df: 6 } }, include_main: true },
      ],
    })
    const advanced = within(card(2)).getByRole("button", { name: "Advanced" })
    fireEvent.click(advanced)
    const df = within(card(2)).getByRole("spinbutton", { name: "age df" })
    fireEvent.change(df, { target: { value: "30" } })
    fireEvent.click(within(card(1)).getByRole("button", { name: "Remove interaction 1" }))
    expect(latest.config.interactions).toEqual([
      { factors: ["age", "brand"], specs: { age: { type: "bs", df: 6 } }, include_main: true },
    ])
    expect(within(card(1)).getByRole("button", { name: "Advanced" })).toHaveAttribute("aria-expanded", "true")
    expect(within(card(1)).getByRole("spinbutton", { name: "age df" })).toHaveValue(30)
    expect(within(card(1)).getByRole("alert")).toHaveTextContent("Enter an integer from 4 to 20")
  })

  it("removing a slot keeps the remaining slots' state", () => {
    const latest = setupStateful({
      interactions: [{ factors: ["income", "age", "flag"], specs: { age: { type: "bs", df: 6 } }, include_main: true }],
    })
    fireEvent.click(within(card(1)).getByRole("button", { name: "Advanced" }))
    fireEvent.click(within(card(1)).getByRole("button", { name: "Remove feature 1" }))
    expect(latest.config.interactions).toEqual([{ factors: ["age", "flag"], specs: { age: { type: "bs", df: 6 } }, include_main: true }])
    expect(within(card(1)).getByRole("button", { name: "Advanced" })).toHaveAttribute("aria-expanded", "true")
  })

  it("a malformed interaction renders an error card with a remove control", () => {
    const latest = setupStateful({
      interactions: [null, { factors: "income" }, { factors: ["age", "region"], include_main: true }],
    })
    expect(within(card(1)).getByText("Interaction 1 cannot be edited")).toBeInTheDocument()
    expect(within(card(1)).getByRole("alert")).toHaveTextContent("This interaction is not an object. Remove it, or fix it in the node's JSON.")
    expect(within(card(2)).getByRole("alert")).toHaveTextContent("This interaction has no list of features.")
    expect(within(card(3)).getByRole("combobox", { name: "Interaction 3 feature 1" })).toHaveValue("age")
    fireEvent.click(within(card(1)).getByRole("button", { name: "Remove interaction 1" }))
    expect(latest.config.interactions).toEqual([{ factors: "income" }, { factors: ["age", "region"], include_main: true }])
    fireEvent.change(within(card(2)).getByRole("combobox", { name: "Interaction 2 feature 1" }), { target: { value: "flag" } })
    expect(latest.config.interactions).toEqual([{ factors: "income" }, { factors: ["flag", "region"], include_main: true }])
  })

  it("an unavailable saved factor stays selected with a warning", () => {
    const onUpdate = setup({ interactions: [{ factors: ["target", "ghost"], include_main: true }] })
    const first = within(card(1)).getByRole("combobox", { name: "Interaction 1 feature 1" })
    expect(first).toHaveValue("target")
    expect(optionLabels(first)).toContain("target (unavailable)")
    expect(within(card(1)).getByText("target is the target column; choose another feature.")).toBeInTheDocument()
    expect(within(card(1)).getByText("ghost is not in the upstream data; choose another feature.")).toBeInTheDocument()
    expect(within(card(1)).queryByRole("combobox", { name: "target fit in interaction" })).not.toBeInTheDocument()
    expect(onUpdate).not.toHaveBeenCalled()
  })

  it("joint encodings need integer, boolean, or categorical features", () => {
    const onUpdate = setup({ interactions: [{ factors: ["income", "region"], encoding: "target_encoding", include_main: false }] })
    expect(within(card(1)).getByText("Joint encodings need integer, boolean, or categorical features.")).toBeInTheDocument()
    const second = within(card(1)).getByRole("combobox", { name: "Interaction 1 feature 2" })
    expect(optionValues(second)).toEqual(["", "age", "region", "brand", "flag"])
    expect(optionValues(within(card(1)).getByRole("combobox", { name: "Interaction 1 encoding" }))).toEqual(["product", "target_encoding"])
    expect(onUpdate).not.toHaveBeenCalled()
  })

  it("a target-encoding interaction writes its settings and a mode change drops product fits", () => {
    const latest = setupStateful({
      interactions: [{ factors: ["region", "brand"], specs: { region: { type: "categorical" } }, include_main: false }],
    })
    fireEvent.change(within(card(1)).getByRole("combobox", { name: "Interaction 1 encoding" }), { target: { value: "target_encoding" } })
    expect(latest.config.interactions).toEqual([{ factors: ["region", "brand"], include_main: false, encoding: "target_encoding" }])
    expect(within(card(1)).getByText("Encodes the combination of raw feature values.")).toBeInTheDocument()
    fireEvent.click(within(card(1)).getByRole("button", { name: "Advanced" }))
    const permutations = within(card(1)).getByRole("spinbutton", { name: "Interaction 1 n permutations" })
    fireEvent.change(permutations, { target: { value: "6" } })
    fireEvent.blur(permutations)
    expect(latest.config.interactions).toEqual([{ factors: ["region", "brand"], include_main: false, encoding: "target_encoding", n_permutations: 6 }])
  })

  it("writes an explicit target encoding for a categorical factor with a linear partner", () => {
    const latest = setupStateful({ interactions: [{ factors: ["region", "income"], include_main: false }] })
    fireEvent.change(within(card(1)).getByRole("combobox", { name: "region fit in interaction" }), { target: { value: "target_encoding" } })
    expect(latest.config.interactions).toEqual([{ factors: ["region", "income"], specs: { region: { type: "target_encoding" } }, include_main: false }])
    expect(within(card(1)).getByText("Target encoding includes its main effect, even when Include main effects is off.")).toBeInTheDocument()
  })

  it("shows conflicting materialisations on every card involved", () => {
    setup({
      interactions: [
        { factors: ["income", "region"], specs: { income: { type: "bs", df: 5 } }, include_main: true },
        { factors: ["income", "brand"], include_main: true },
      ],
    })
    for (const n of [1, 2]) {
      expect(within(card(n)).getByText(/^Interactions 1, 2 add different main effects for income/)).toBeInTheDocument()
    }
  })

  it("shows the Include main effects help beside the checkbox and writes the flag", () => {
    const onUpdate = setup({ terms: { age: { type: "linear" } }, interactions: [{ factors: ["age", "region"], include_main: true }] })
    expect(within(card(1)).getByText("Adds a main effect for features that have none; features with a term keep it.")).toBeVisible()
    fireEvent.click(within(card(1)).getByRole("checkbox", { name: "Include main effects" }))
    expect(onUpdate).toHaveBeenCalledWith("interactions", [{ factors: ["age", "region"], include_main: false }])
  })

  it("marks a duplicate factor set", () => {
    setup({ interactions: [{ factors: ["age", "region"], include_main: true }, { factors: ["region", "age"], include_main: true }] })
    expect(within(card(2)).getByText("Duplicate interaction: another card fits these features the same way.")).toBeInTheDocument()
    expect(within(card(1)).queryByText(/Duplicate interaction/)).not.toBeInTheDocument()
  })
})
