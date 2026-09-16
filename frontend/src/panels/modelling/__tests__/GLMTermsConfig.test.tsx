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
]

function setup(config: Record<string, unknown> = {}) {
  const onUpdate = vi.fn<OnUpdateConfig>(() => ({ ok: true as const }))
  render(
    <GLMTermsConfig config={{ target: "target", algorithm: "glm", ...config }} onUpdate={onUpdate} columns={columns} />,
  )
  return onUpdate
}

function row(name: string) {
  return screen.getByRole("group", { name: `${name} feature` })
}

function StatefulTermsConfig() {
  const [config, setConfig] = useState<Record<string, unknown>>({
    target: "target",
    algorithm: "glm",
    terms: { age: { type: "bs", df: 6, degree: 2, boundary_knots: [0, 10] } },
  })
  return (
    <GLMTermsConfig
      config={config}
      columns={columns}
      onUpdate={(keyOrUpdates, value) => {
        setConfig((current) => (
          typeof keyOrUpdates === "string"
            ? { ...current, [keyOrUpdates]: value }
            : { ...current, ...keyOrUpdates }
        ))
        return { ok: true }
      }}
    />
  )
}

function StatefulInlineTermsConfig({ initialTerms }: { initialTerms: Record<string, unknown> }) {
  const [config, setConfig] = useState<Record<string, unknown>>({ target: "target", algorithm: "glm", terms: initialTerms })
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

describe("GLMTermsConfig", () => {
  beforeEach(() => vi.stubGlobal("confirm", vi.fn(() => true)))
  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
  })

  it("shows Not in model until Add term writes the dtype default", () => {
    const onUpdate = setup()
    expect(screen.getByText("0 of 3 in model")).toBeInTheDocument()
    expect(within(row("age")).getByText("Not in model")).toBeInTheDocument()
    fireEvent.click(within(row("region")).getByRole("button", { name: "Add region term" }))
    expect(onUpdate).toHaveBeenCalledWith("terms", { region: { type: "categorical" } })
  })

  it("second Add term writes a uniquely named ** 2 expression under the same row", () => {
    const onUpdate = setup({ terms: { age: { type: "linear" } } })
    expect(screen.getByText("1 of 3 in model")).toBeInTheDocument()
    fireEvent.click(within(row("age")).getByRole("button", { name: "Add age term" }))
    expect(onUpdate).toHaveBeenCalledWith("terms", {
      age: { type: "linear" },
      age_sq: { type: "expression", expr: "age ** 2" },
    })
  })

  it("adds the unused categorical encoding beneath the same raw feature and then disables Add", () => {
    render(<StatefulInlineTermsConfig initialTerms={{ region: { type: "categorical" }, region_te: { type: "target_encoding", variable: "region" } }} />)
    const region = row("region")
    fireEvent.click(within(region).getByRole("button", { name: "Add region term" }))
    expect(within(region).getByRole("combobox", { name: "region_fe term type" })).toHaveValue("frequency_encoding")
    expect(within(region).getByRole("button", { name: "Add region term" })).toBeDisabled()
    const options = (name: string) => Array.from(within(region).getByRole("combobox", { name }).querySelectorAll("option")).map((option) => option.value)
    expect(options("region term type")).toEqual(["categorical"])
    expect(options("region_te term type")).toEqual(["target_encoding"])
    expect(options("region_fe term type")).toEqual(["frequency_encoding"])
    fireEvent.click(within(region).getByRole("button", { name: "Remove region_fe term" }))
    expect(options("region_te term type")).toEqual(["target_encoding", "frequency_encoding"])
    expect(options("region term type")).toEqual(["categorical", "frequency_encoding"])
    expect(within(region).getByRole("button", { name: "Add region term" })).toBeEnabled()
  })

  it("reserves excluded upstream columns when choosing an encoding alias", () => {
    const onUpdate = vi.fn<OnUpdateConfig>(() => ({ ok: true as const }))
    render(
      <GLMTermsConfig
        config={{ target: "region_te", algorithm: "glm", terms: { region: { type: "categorical" } } }}
        onUpdate={onUpdate}
        columns={[...columns, { name: "region_te", dtype: "Float64" }]}
      />,
    )
    fireEvent.click(within(row("region")).getByRole("button", { name: "Add region term" }))
    expect(onUpdate).toHaveBeenCalledWith("terms", {
      region: { type: "categorical" },
      region_te2: { type: "target_encoding", variable: "region" },
    })
  })

  it("keeps a filtered fit type selector on every added continuous term", () => {
    render(<StatefulInlineTermsConfig initialTerms={{ age: { type: "linear" } }} />)
    fireEvent.change(screen.getByLabelText("Search features"), { target: { value: "ag" } })
    fireEvent.click(within(row("age")).getByRole("button", { name: "Add age term" }))
    const native = within(row("age")).getByRole("combobox", { name: "age term type" })
    const nativeTypes = Array.from(native.querySelectorAll("option")).map((option) => option.value)
    expect(nativeTypes).toEqual(["linear", "bs", "ns", "ms"])
    const additional = within(row("age")).getByRole("combobox", { name: "age_sq term type" })
    expect(additional).toHaveValue("expression")
    expect(Array.from(additional.querySelectorAll("option")).map((option) => option.value)).toEqual(["expression"])
    const expression = within(row("age")).getByRole("textbox", { name: "age_sq expression" })
    fireEvent.change(expression, { target: { value: "age ** 3" } })
    fireEvent.blur(expression)
    expect(expression).toHaveValue("age ** 3")
    expect(screen.getByLabelText("Search features")).toHaveValue("ag")
  })

  it("preserves saved numeric encodings and allows explicit conversion to an expression", () => {
    render(<StatefulInlineTermsConfig initialTerms={{ age: { type: "target_encoding" }, age_fe: { type: "frequency_encoding", variable: "age" } }} />)
    const native = within(row("age")).getByRole("combobox", { name: "age term type" })
    expect(native).toHaveValue("target_encoding")
    expect(Array.from(native.querySelectorAll("option")).map((option) => option.value)).not.toContain("frequency_encoding")
    const additional = within(row("age")).getByRole("combobox", { name: "age_fe term type" })
    expect(additional).toHaveValue("frequency_encoding")
    expect(Array.from(additional.querySelectorAll("option")).map((option) => option.value)).toEqual(["expression", "frequency_encoding"])
    fireEvent.change(additional, { target: { value: "expression" } })
    expect(within(row("age")).getByRole("textbox", { name: "age_fe expression" })).toHaveValue("age ** 2")
    expect(native).toHaveValue("target_encoding")
  })

  it("keeps native and expression controls inline in their feature row while editing", () => {
    render(<StatefulInlineTermsConfig initialTerms={{ age: { type: "linear" }, age_sq: { type: "expression", expr: "age ** 2" } }} />)
    const age = row("age")
    const native = within(age).getByRole("combobox", { name: "age term type" })
    expect(native).toBeInTheDocument()
    fireEvent.change(native, { target: { value: "ns" } })
    expect(native).toHaveValue("ns")
    const expression = within(age).getByRole("textbox", { name: "age_sq expression" })
    fireEvent.change(expression, { target: { value: "income * age" } })
    fireEvent.blur(expression)
    expect(expression).toHaveValue("income * age")
    expect(screen.getByLabelText("Search features")).toHaveValue("")
    expect(row("region")).toBeInTheDocument()
    expect(screen.queryByRole("button", { name: /Configure/ })).not.toBeInTheDocument()
    expect(screen.queryByRole("button", { name: "Back to features" })).not.toBeInTheDocument()
  })

  it("adds a term beneath its row without changing the feature filter", () => {
    render(<StatefulInlineTermsConfig initialTerms={{}} />)
    fireEvent.change(screen.getByLabelText("Search features"), { target: { value: "g" } })
    fireEvent.click(within(row("age")).getByRole("button", { name: "Add age term" }))
    expect(within(row("age")).getByRole("combobox", { name: "age term type" })).toHaveValue("linear")
    expect(screen.getByLabelText("Search features")).toHaveValue("g")
    expect(row("region")).toBeInTheDocument()
    expect(screen.queryByRole("group", { name: "income feature" })).not.toBeInTheDocument()
  })

  it("lists the expression card under its anchor row", () => {
    setup({ terms: { age: { type: "linear" }, age_sq: { type: "expression", expr: "age ** 2" } } })
    expect(within(row("age")).getByRole("textbox", { name: "age_sq expression" })).toBeInTheDocument()
    expect(within(row("age")).getByRole("combobox", { name: "age term type" })).toBeInTheDocument()
  })

  it("type switch keeps only the subset and ns offers no monotonicity", () => {
    const onUpdate = setup({ terms: { age: { type: "bs", df: 5, monotonicity: "increasing" } } })
    fireEvent.change(within(row("age")).getByRole("combobox", { name: "age term type" }), { target: { value: "ns" } })
    expect(onUpdate).toHaveBeenCalledWith("terms", { age: { type: "ns", df: 5 } })
  })

  it("persists spline Auto mode while retaining unrelated controls", () => {
    render(<StatefulTermsConfig />)
    fireEvent.change(within(row("age")).getByRole("combobox", { name: "age df mode" }), { target: { value: "auto" } })
    fireEvent.click(screen.getByRole("button", { name: "JSON" }))
    const saved = JSON.parse((screen.getByRole("textbox", { name: "Terms JSON" }) as HTMLTextAreaElement).value)
    expect(saved).toEqual({ age: { type: "bs", degree: 2, boundary_knots: [0, 10] } })
  })

  it("ms defaults to increasing", () => {
    const onUpdate = setup({ terms: { age: { type: "linear" } } })
    fireEvent.change(within(row("age")).getByRole("combobox", { name: "age term type" }), { target: { value: "ms" } })
    expect(onUpdate).toHaveBeenCalledWith("terms", { age: { type: "ms", monotonicity: "increasing" } })
  })

  it("refuses an expression that breaks the grammar and shows the grammar", () => {
    const onUpdate = setup({ terms: { age: { type: "linear" }, age_sq: { type: "expression", expr: "age ** 2" } } })
    const field = within(row("age")).getByRole("textbox", { name: "age_sq expression" })
    fireEvent.change(field, { target: { value: "log(age)" } })
    fireEvent.blur(field)
    expect(onUpdate).not.toHaveBeenCalledWith("terms", expect.anything())
    expect(screen.getByRole("alert")).toHaveTextContent("Supported forms")
  })

  it("moving an expression's first identifier re-anchors its card", () => {
    const onUpdate = setup({ terms: { age: { type: "linear" }, age_sq: { type: "expression", expr: "age ** 2" } } })
    const field = within(row("age")).getByRole("textbox", { name: "age_sq expression" })
    fireEvent.change(field, { target: { value: "income * age" } })
    fireEvent.blur(field)
    expect(onUpdate).toHaveBeenCalledWith("terms", {
      age: { type: "linear" },
      age_sq: { type: "expression", expr: "income * age" },
    })
  })

  it("renames an expression and refuses a column name", () => {
    const onUpdate = setup({ terms: { age: { type: "linear" }, age_sq: { type: "expression", expr: "age ** 2" } } })
    const name = within(row("age")).getByRole("textbox", { name: "age_sq name" })
    fireEvent.change(name, { target: { value: "income" } })
    fireEvent.blur(name)
    expect(screen.getByRole("alert")).toHaveTextContent("income is a column")
    fireEvent.change(name, { target: { value: "age_squared" } })
    fireEvent.blur(name)
    expect(onUpdate).toHaveBeenCalledWith("terms", {
      age: { type: "linear" },
      age_squared: { type: "expression", expr: "age ** 2" },
    })
  })

  it("removes a term without confirmation and leaves interactions alone", () => {
    const onUpdate = setup({
      terms: { age: { type: "linear" } },
      interactions: [{ factors: ["age", "region"], include_main: true }],
    })
    fireEvent.click(within(row("age")).getByRole("button", { name: "Remove age term" }))
    expect(window.confirm).not.toHaveBeenCalled()
    expect(onUpdate).toHaveBeenCalledWith("terms", {})
  })

  it("Fit all with defaults adds only missing native terms", () => {
    const onUpdate = setup({ terms: { age: { type: "bs", df: 4 } } })
    fireEvent.click(screen.getByRole("button", { name: "Fit all with defaults" }))
    expect(onUpdate).toHaveBeenCalledWith("terms", {
      age: { type: "bs", df: 4 },
      income: { type: "linear" },
      region: { type: "categorical" },
    })
  })

  it("Remove all terms confirms once and clears terms and interactions", () => {
    const onUpdate = setup({
      terms: { age: { type: "linear" } },
      interactions: [{ factors: ["age", "region"], include_main: true }],
    })
    fireEvent.click(screen.getByRole("button", { name: "Remove all terms" }))
    expect(window.confirm).toHaveBeenCalledTimes(1)
    expect(onUpdate).toHaveBeenCalledWith({ terms: {}, interactions: [] })
  })

  it("In model only hides out-of-model rows and keeps interaction-only ones", () => {
    setup({
      terms: { age: { type: "linear" } },
      interactions: [{ factors: ["region", "age"], include_main: true }],
    })
    expect(screen.getByText("2 of 3 in model")).toBeInTheDocument()
    expect(within(row("region")).getByText("Interaction only")).toBeInTheDocument()
    expect(within(row("income")).getByText("Not in model")).toBeInTheDocument()

    fireEvent.click(screen.getByRole("switch", { name: "In model only" }))
    expect(screen.queryByRole("group", { name: "income feature" })).not.toBeInTheDocument()
    expect(screen.queryByRole("group", { name: "target feature" })).not.toBeInTheDocument()
    expect(within(row("region")).getByText("Interaction only")).toBeInTheDocument()
    expect(row("age")).toBeInTheDocument()

    fireEvent.click(screen.getByRole("switch", { name: "In model only" }))
    expect(row("income")).toBeInTheDocument()
  })

  it("labels a row read only by an expression as in an expression, not out of the model", () => {
    setup({ terms: { age: { type: "linear" }, ai: { type: "expression", expr: "age * income" } } })
    expect(screen.getByText("2 of 3 in model")).toBeInTheDocument()
    expect(within(row("income")).getByText("In an expression")).toBeInTheDocument()
    expect(within(row("region")).getByText("Not in model")).toBeInTheDocument()
    expect(within(row("age")).getByRole("textbox", { name: "ai expression" })).toBeInTheDocument()
  })

  it("filters rows by search", () => {
    setup()
    fireEvent.change(screen.getByLabelText("Search features"), { target: { value: "reg" } })
    expect(screen.queryByRole("group", { name: "age feature" })).not.toBeInTheDocument()
    expect(row("region")).toBeInTheDocument()
  })

  it("repairs an unresolved expression in place and moves it into its feature row", () => {
    render(<StatefulInlineTermsConfig initialTerms={{ age: { type: "linear" }, h2: { type: "expression", expr: "height ** 2" } }} />)
    expect(screen.getByText("Unresolved terms")).toBeInTheDocument()
    const name = screen.getByRole("textbox", { name: "h2 name" })
    fireEvent.change(name, { target: { value: "age_sq" } })
    fireEvent.blur(name)
    const expression = screen.getByRole("textbox", { name: "age_sq expression" })
    fireEvent.change(expression, { target: { value: "age ** 2" } })
    fireEvent.blur(expression)
    expect(screen.queryByRole("group", { name: "Unresolved terms" })).not.toBeInTheDocument()
    expect(within(row("age")).getByRole("textbox", { name: "age_sq expression" })).toHaveValue("age ** 2")
  })

  it("JSON mode round-trips an unresolved expression", () => {
    const onUpdate = setup({ terms: { age: { type: "linear" }, h2: { type: "expression", expr: "height ** 2" } } })
    expect(screen.getByText("Unresolved terms")).toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", { name: "JSON" }))
    const area = screen.getByRole("textbox", { name: "Terms JSON" })
    expect(JSON.parse((area as HTMLTextAreaElement).value)).toEqual({
      age: { type: "linear" },
      h2: { type: "expression", expr: "height ** 2" },
    })
    fireEvent.change(area, { target: { value: JSON.stringify({ age: { type: "bs", df: 4 } }) } })
    fireEvent.blur(area)
    expect(onUpdate).toHaveBeenCalledWith("terms", { age: { type: "bs", df: 4 } })
  })

  it("keeps an unresolved encoding visible and removable", () => {
    const onUpdate = setup({ terms: { bad_target: { type: "target_encoding", variable: "height" } } })
    expect(screen.getByText("Unresolved terms")).toBeInTheDocument()
    expect(screen.getByRole("combobox", { name: "bad_target term type" })).toHaveValue("target_encoding")
    fireEvent.click(screen.getByRole("button", { name: "Remove bad_target term" }))
    expect(onUpdate).toHaveBeenCalledWith("terms", {})
  })

  it.each([
    [JSON.stringify({ bad: null }), 'Term "bad" must be an object with a string "type"'],
    [JSON.stringify({ bad: { df: 3 } }), 'Term "bad" must be an object with a string "type"'],
    [JSON.stringify({ bad: [1, 2] }), 'Term "bad" must be an object with a string "type"'],
  ])("refuses malformed term JSON %s, keeps the draft, and writes nothing", (draft, message) => {
    const onUpdate = setup({ terms: { age: { type: "linear" } } })
    fireEvent.click(screen.getByRole("button", { name: "JSON" }))
    const area = screen.getByRole("textbox", { name: "Terms JSON" })
    fireEvent.change(area, { target: { value: draft } })
    fireEvent.blur(area)
    expect(screen.getByText(message)).toBeInTheDocument()
    expect((area as HTMLTextAreaElement).value).toBe(draft)
    expect(onUpdate).not.toHaveBeenCalled()
  })

  // The null entry is the one that threw outright, and it is the case that
  // fails without the render guard; a select whose value matches no option
  // still reads as the first one, so the other two pin the no-throw contract
  // for the shapes the JSON guard now keeps out of new configs.
  it.each([
    ["a type-less object", { df: 3 }, { type: "ns", df: 3 }],
    ["a non-string type", { type: 42 }, { type: "ns" }],
    ["a null entry", null, { type: "ns" }],
  ])("renders %s from a stale config as a linear card instead of throwing", (_label, spec, repaired) => {
    const onUpdate = setup({ terms: { age: spec } })
    const select = within(row("age")).getByRole("combobox", { name: "age term type" })
    expect((select as HTMLSelectElement).value).toBe("linear")
    // Picking a valid fit repairs the type and retains only supported fields.
    fireEvent.change(select, { target: { value: "ns" } })
    expect(onUpdate).toHaveBeenCalledWith("terms", { age: repaired })
  })

  it("never writes exclude, monotone_constraints, or all_factors", () => {
    const onUpdate = setup({ exclude: ["age"], monotone_constraints: { age: 1 }, all_factors: true })
    fireEvent.click(within(row("age")).getByRole("button", { name: "Add age term" }))
    fireEvent.click(screen.getByRole("button", { name: "Fit all with defaults" }))
    for (const call of onUpdate.mock.calls) {
      const keys = typeof call[0] === "string" ? [call[0]] : Object.keys(call[0] as object)
      expect(keys).not.toContain("exclude")
      expect(keys).not.toContain("monotone_constraints")
      expect(keys).not.toContain("all_factors")
    }
    expect(within(row("age")).queryByRole("button", { name: /include|exclude/i })).not.toBeInTheDocument()
  })
})
