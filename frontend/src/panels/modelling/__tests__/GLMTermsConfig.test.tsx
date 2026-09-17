import { useState } from "react"
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import type { OnUpdateConfig } from "../../editors/_shared"
import { GLMTermsConfig } from "../GLMTermsConfig"

const columns = [
  { name: "target", dtype: "Float64" },
  { name: "age", dtype: "Int64" },
  { name: "income", dtype: "Float64" },
  { name: "region", dtype: "String" },
  { name: "flag", dtype: "Boolean" },
  { name: "start", dtype: "Date" },
  { name: "annual mileage", dtype: "Float64" },
  { name: "constructor", dtype: "Float64" },
]

function setup(config: Record<string, unknown> = {}) {
  const onUpdate = vi.fn<OnUpdateConfig>(() => ({ ok: true as const }))
  render(<GLMTermsConfig config={{ target: "target", algorithm: "glm", ...config }} onUpdate={onUpdate} columns={columns} />)
  return onUpdate
}

/** Renders the pane over real config state and exposes the latest config. */
function setupStateful(initial: Record<string, unknown>) {
  const latest: { config: Record<string, unknown> } = { config: {} }
  function Harness() {
    const [config, setConfig] = useState<Record<string, unknown>>({ target: "target", algorithm: "glm", ...initial })
    latest.config = config
    return (
      <GLMTermsConfig
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

const row = (name: string) => screen.getByRole("group", { name: `${name} feature` })
const unresolvedGroup = () => screen.getByRole("group", { name: "Unresolved terms" })
const optionValues = (select: HTMLElement) => Array.from(select.querySelectorAll("option")).map((option) => option.value)

describe("GLMTermsConfig", () => {
  beforeEach(() => vi.stubGlobal("confirm", vi.fn(() => true)))
  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
  })

  it("counts eligible columns and Add term writes the dtype default", () => {
    const onUpdate = setup()
    expect(screen.getByText("0 of 6 in model")).toBeInTheDocument()
    expect(within(row("age")).getByText("Not in model")).toBeInTheDocument()
    fireEvent.click(within(row("flag")).getByRole("button", { name: "Add flag term" }))
    expect(onUpdate).toHaveBeenCalledWith("terms", { flag: { type: "categorical" } })
  })

  it("hides unsupported columns and says how many", () => {
    setup()
    expect(screen.queryByRole("group", { name: "start feature" })).not.toBeInTheDocument()
    expect(screen.queryByRole("group", { name: "target feature" })).not.toBeInTheDocument()
    expect(screen.getByText("1 column is hidden because GLM fits do not support its dtype: start (Date).")).toBeInTheDocument()
  })

  it("lists unresolved role, missing, unsupported, and malformed terms with reasons and removal", () => {
    const onUpdate = setup({
      terms: {
        target: { type: "linear" },
        ghost: { type: "bs", df: 5 },
        start: { type: "categorical" },
        h2: { type: "expression", expr: "height ** 2" },
      },
    })
    const group = unresolvedGroup()
    expect(within(group).getByText("target is the target column")).toBeInTheDocument()
    expect(within(group).getByText("ghost is not in the upstream data")).toBeInTheDocument()
    expect(within(group).getByText("start has dtype Date, which GLM fits do not support")).toBeInTheDocument()
    expect(within(group).getByText("height is not in the upstream data")).toBeInTheDocument()
    expect(within(group).getByText("B-spline")).toBeInTheDocument()
    // An unresolved expression stays editable so it can be repaired in place.
    expect(within(group).getByRole("textbox", { name: "h2 expression" })).toHaveValue("height ** 2")
    fireEvent.click(within(group).getByRole("button", { name: "Remove ghost term" }))
    expect(onUpdate).toHaveBeenCalledWith("terms", {
      target: { type: "linear" },
      start: { type: "categorical" },
      h2: { type: "expression", expr: "height ** 2" },
    })
  })

  it("never shows a malformed entry as a linear term and repairs it with a valid fit", () => {
    const latest = setupStateful({ terms: { age: null, region: { type: "poly" } } })
    expect(within(row("age")).getByText("Not in model")).toBeInTheDocument()
    const group = unresolvedGroup()
    expect(within(group).getByText("This entry has no fit type")).toBeInTheDocument()
    expect(within(group).getByText("Fit type poly is not supported")).toBeInTheDocument()
    const repair = within(group).getByRole("combobox", { name: "age term type" })
    expect(optionValues(repair)).toEqual(["", "linear", "categorical", "bs", "ns", "ms", "target_encoding", "frequency_encoding"])
    fireEvent.change(repair, { target: { value: "bs" } })
    expect(latest.config.terms).toEqual({ age: { type: "bs" }, region: { type: "poly" } })
    expect(within(row("age")).getByRole("combobox", { name: "age term type" })).toHaveValue("bs")
  })

  it("a constructor column behaves as an ordinary column", () => {
    const onUpdate = setup()
    expect(within(row("constructor")).getByText("Not in model")).toBeInTheDocument()
    fireEvent.click(within(row("constructor")).getByRole("button", { name: "Add constructor term" }))
    expect(onUpdate).toHaveBeenCalledWith("terms", { constructor: { type: "linear" } })
  })

  it("non-identifier columns offer no expression and Add term says why", () => {
    setup({ terms: { "annual mileage": { type: "linear" } } })
    const add = within(row("annual mileage")).getByRole("button", { name: "Add annual mileage term" })
    expect(add).toBeDisabled()
    const reason = "annual mileage cannot appear in an expression: rename it upstream to letters, digits, and underscores."
    expect(add.parentElement).toHaveAttribute("title", reason)
    expect(add).toHaveAttribute("aria-description", reason)
  })

  it("integer columns offer categorical and encoding fits", () => {
    setup({ terms: { age: { type: "linear" } } })
    expect(optionValues(within(row("age")).getByRole("combobox", { name: "age term type" })))
      .toEqual(["linear", "categorical", "bs", "ns", "ms", "target_encoding", "frequency_encoding"])
  })

  it("second Add term writes a uniquely named expression and each encoding once", () => {
    const latest = setupStateful({ terms: { age: { type: "linear" }, region: { type: "categorical" } } })
    fireEvent.click(within(row("age")).getByRole("button", { name: "Add age term" }))
    expect(latest.config.terms).toMatchObject({ age_sq: { type: "expression", expr: "age ** 2" } })
    const region = row("region")
    fireEvent.click(within(region).getByRole("button", { name: "Add region term" }))
    fireEvent.click(within(region).getByRole("button", { name: "Add region term" }))
    expect(latest.config.terms).toMatchObject({
      region_te: { type: "target_encoding", variable: "region" },
      region_fe: { type: "frequency_encoding", variable: "region" },
    })
    expect(within(region).getByRole("button", { name: "Add region term" })).toBeDisabled()
    expect(optionValues(within(region).getByRole("combobox", { name: "region term type" }))).toEqual(["categorical"])
  })

  it("tags materialised main effects and interaction-registered encodings", () => {
    setup({
      terms: { region: { type: "categorical" } },
      interactions: [
        { factors: ["income", "region"], include_main: true },
        { factors: ["age", "income"], specs: { age: { type: "target_encoding" } }, include_main: false },
        { factors: ["flag", "region"], include_main: false },
      ],
    })
    expect(within(row("income")).getByText("Main effect from Interaction 1 (Linear)")).toBeInTheDocument()
    expect(within(row("age")).getByText("Target encoding from Interaction 2")).toBeInTheDocument()
    expect(within(row("flag")).getByText("Interaction only")).toBeInTheDocument()
    expect(screen.getByText("4 of 6 in model")).toBeInTheDocument()
  })

  it("ignores a malformed interaction entry instead of failing the pane", () => {
    setup({ terms: { age: { type: "linear" } }, interactions: [null, { factors: "income" }] })
    expect(screen.getByText("1 of 6 in model")).toBeInTheDocument()
  })

  it("clearing a spline df field keeps Fixed mode and sibling settings", () => {
    const initial = { age: { type: "bs", df: 6, degree: 2, boundary_knots: [0, 90] } }
    const latest = setupStateful({ terms: initial })
    const df = within(row("age")).getByRole("spinbutton", { name: "age df" })
    fireEvent.change(df, { target: { value: "" } })
    fireEvent.blur(df)
    expect(latest.config.terms).toEqual(initial)
    expect(within(row("age")).getByRole("combobox", { name: "age df mode" })).toHaveValue("fixed")
    fireEvent.change(df, { target: { value: "8" } })
    fireEvent.blur(df)
    expect(latest.config.terms).toEqual({ age: { type: "bs", df: 8, degree: 2, boundary_knots: [0, 90] } })
    fireEvent.change(within(row("age")).getByRole("combobox", { name: "age df mode" }), { target: { value: "auto" } })
    expect(latest.config.terms).toEqual({ age: { type: "bs", degree: 2, boundary_knots: [0, 90] } })
  })

  it("an untouched Advanced field never rewrites the saved term", () => {
    const initial = { age: { type: "bs", df: 8, degree: 2 } }
    const latest = setupStateful({ terms: initial })
    fireEvent.click(within(row("age")).getByRole("button", { name: "Advanced" }))
    for (const name of ["age knots", "age boundary knots"]) {
      const field = within(row("age")).getByRole("textbox", { name })
      fireEvent.focus(field)
      fireEvent.blur(field)
    }
    expect(latest.config.terms).toEqual(initial)
  })

  it("reference level and levels are exclusive", () => {
    const latest = setupStateful({ terms: { region: { type: "categorical", levels: ["north"] } } })
    fireEvent.click(within(row("region")).getByRole("button", { name: "Advanced" }))
    const reference = within(row("region")).getByRole("textbox", { name: "region reference level" })
    fireEvent.change(reference, { target: { value: "south" } })
    fireEvent.blur(reference)
    expect(latest.config.terms).toEqual({ region: { type: "categorical", reference: "south" } })
    expect(within(row("region")).getByRole("textbox", { name: "region levels" })).toHaveValue("")
  })

  it("edits expressions through the grammar and refuses non-numeric operands", () => {
    const latest = setupStateful({ terms: { age: { type: "linear" }, age_sq: { type: "expression", expr: "age ** 2" } } })
    const expression = within(row("age")).getByRole("textbox", { name: "age_sq expression" })
    fireEvent.change(expression, { target: { value: "age * income" } })
    fireEvent.blur(expression)
    expect(latest.config.terms).toMatchObject({ age_sq: { type: "expression", expr: "age * income" } })
    fireEvent.change(expression, { target: { value: "age * region" } })
    fireEvent.blur(expression)
    expect(within(row("age")).getByRole("alert")).toHaveTextContent("region is a categorical column; expressions need numbers.")
    expect(latest.config.terms).toMatchObject({ age_sq: { type: "expression", expr: "age * income" } })
  })

  it("Fit all with defaults adds eligible columns only and Remove all clears terms and interactions", () => {
    const onUpdate = setup({ terms: { age: { type: "bs", df: 4 } } })
    fireEvent.click(screen.getByRole("button", { name: "Fit all with defaults" }))
    expect(onUpdate).toHaveBeenCalledWith("terms", {
      age: { type: "bs", df: 4 },
      income: { type: "linear" },
      region: { type: "categorical" },
      flag: { type: "categorical" },
      "annual mileage": { type: "linear" },
      constructor: { type: "linear" },
    })
    fireEvent.click(screen.getByRole("button", { name: "Remove all terms" }))
    expect(onUpdate).toHaveBeenCalledWith({ terms: {}, interactions: [] })
  })

  it("filters rows by search and In model only", () => {
    setup({ terms: { age: { type: "linear" } } })
    fireEvent.change(screen.getByRole("textbox", { name: "Search features" }), { target: { value: "INC" } })
    expect(screen.getByRole("group", { name: "income feature" })).toBeInTheDocument()
    expect(screen.queryByRole("group", { name: "age feature" })).not.toBeInTheDocument()
    fireEvent.change(screen.getByRole("textbox", { name: "Search features" }), { target: { value: "" } })
    fireEvent.click(screen.getByRole("switch", { name: "In model only" }))
    expect(screen.getAllByRole("group", { name: / feature$/ }).map((group) => group.getAttribute("aria-label"))).toEqual(["age feature"])
  })

  it("JSON mode refuses a malformed terms dict and saves a valid one on blur", () => {
    const onUpdate = setup({ terms: { age: { type: "linear" } } })
    fireEvent.click(screen.getByRole("button", { name: "JSON" }))
    const editor = screen.getByRole("textbox", { name: "Terms JSON" })
    fireEvent.change(editor, { target: { value: '{"age": {"df": 3}}' } })
    fireEvent.blur(editor)
    expect(screen.getByRole("alert")).toHaveTextContent('Term "age" must be an object with a string "type"')
    expect(onUpdate).not.toHaveBeenCalled()
    fireEvent.change(editor, { target: { value: '{"income": {"type": "ns", "df": 4}}' } })
    fireEvent.blur(editor)
    expect(onUpdate).toHaveBeenCalledWith("terms", { income: { type: "ns", df: 4 } })
  })
})
