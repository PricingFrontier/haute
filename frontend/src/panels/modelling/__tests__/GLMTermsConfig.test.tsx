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

  it("JSON mode round-trips a non-column expression key and lists an unresolvable one", () => {
    const onUpdate = setup({
      terms: { age: { type: "linear" }, h2: { type: "expression", expr: "height ** 2" } },
    })
    expect(screen.getByText("Unresolved expressions")).toBeInTheDocument()
    expect(screen.getByRole("textbox", { name: "h2 expression" })).toBeInTheDocument()
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
    ["a type-less object", { df: 3 }],
    ["a non-string type", { type: 42 }],
    ["a null entry", null],
  ])("renders %s from a stale config as a linear card instead of throwing", (_label, spec) => {
    const onUpdate = setup({ terms: { age: spec } })
    const select = within(row("age")).getByRole("combobox", { name: "age term type" })
    expect((select as HTMLSelectElement).value).toBe("linear")
    // Picking a type repairs the entry rather than layering onto the junk.
    fireEvent.change(select, { target: { value: "categorical" } })
    expect(onUpdate).toHaveBeenCalledWith("terms", { age: { type: "categorical" } })
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
