import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { render, screen, cleanup, fireEvent, waitFor, within } from "@testing-library/react"
import { useState } from "react"

vi.mock("../../../../api/client", () => ({
  renderPolarsSteps: vi.fn(),
}))

import { renderPolarsSteps } from "../../../../api/client"
import PolarsStepsEditor from "../PolarsStepsEditor"
import type { Step } from "../types"
import type { InputSource } from "../../_shared"

const mockRender = vi.mocked(renderPolarsSteps)

const quotes: InputSource = { sourceNodeId: "q", name: "quotes", sourceLabel: "Quotes", edgeId: "e1" }
const rates: InputSource = { sourceNodeId: "r", name: "rates", sourceLabel: "Rates", edgeId: "e2" }
const COLUMNS = [
  { name: "premium", dtype: "Float64" },
  { name: "region", dtype: "String" },
]

const source: Step = { id: "s", kind: "source", input: "quotes" }
const filter: Step = {
  id: "f",
  kind: "filter",
  match: "all",
  conditions: [{ column: "premium", operator: "gt", value: { kind: "literal", type: "number", value: 100 } }],
}
const limit: Step = { id: "l", kind: "limit", n: 5 }

function okFor(steps: unknown[]) {
  const code = steps.map((_, i) => (i === 0 ? "df = quotes" : `df = df.step_${i}()`)).join("\n")
  return { ok: true, code, step_lines: steps.map((_, i) => [i + 1, i + 1]), step_index: null, message: "" }
}

/** The most recent steps written to the config; the debounced code write may land after it. */
function lastSteps(spy: ReturnType<typeof vi.fn>): unknown {
  const call = [...spy.mock.calls].reverse().find((c) => c[0] === "steps")
  return call?.[1]
}

/** Keeps config in state so updates re-render like the node panel does. */
function Harness({
  initial,
  inputSources,
  onReplace = vi.fn(),
  errorLine,
  runError,
  spy,
}: {
  initial: Record<string, unknown>
  inputSources: InputSource[]
  onReplace?: (config: Record<string, unknown>) => void
  errorLine?: number | null
  runError?: string | null
  spy?: (key: string, value: unknown) => void
}) {
  const [config, setConfig] = useState(initial)
  return (
    <PolarsStepsEditor
      config={config}
      onUpdate={(keyOrUpdates, value) => {
        if (typeof keyOrUpdates === "string") {
          spy?.(keyOrUpdates, value)
          setConfig((c) => ({ ...c, [keyOrUpdates]: value }))
        } else {
          setConfig((c) => ({ ...c, ...keyOrUpdates }))
        }
        return { ok: true }
      }}
      onReplaceConfig={(next) => {
        onReplace(next)
        setConfig(next)
        return { ok: true }
      }}
      inputSources={inputSources}
      errorLine={errorLine}
      runError={runError}
      upstreamColumns={COLUMNS}
    />
  )
}

describe("PolarsStepsEditor", () => {
  beforeEach(() => {
    mockRender.mockReset()
    mockRender.mockImplementation(async ({ steps }) => okFor(steps))
  })
  afterEach(cleanup)

  it("seeds the start input and adds the first step from the chooser under it", async () => {
    const spy = vi.fn()
    render(<Harness initial={{ steps: [] }} inputSources={[quotes]} spy={spy} />)
    expect(screen.getByLabelText("Start from input")).toHaveValue("quotes")
    expect(screen.queryByRole("list", { name: "Steps" })).not.toBeInTheDocument()
    // The only input is written as the start step at once, so the node
    // already renders `df = quotes` and can be previewed.
    await waitFor(() => expect(spy).toHaveBeenCalledWith("steps", [expect.objectContaining({ kind: "source", input: "quotes" })]), { timeout: 5000 })
    await waitFor(() => expect(spy).toHaveBeenCalledWith("code", "df = quotes"), { timeout: 5000 })
    fireEvent.click(screen.getByRole("button", { name: "Add step" }))
    fireEvent.click(screen.getByRole("menuitem", { name: "Filter rows" }))
    await waitFor(() => expect(lastSteps(spy)).toHaveLength(2), { timeout: 5000 })
    const steps = lastSteps(spy) as Step[]
    expect(steps[0]).toMatchObject({ kind: "source", input: "quotes" })
    expect(steps[1]).toMatchObject({ kind: "filter" })
    expect(screen.getByRole("button", { name: "Step 1: Filter rows" })).toHaveAttribute("aria-expanded", "true")
  })

  it("adds a step from the inline chooser, whose tooltips explain each kind, and writes the rendered code into the config", async () => {
    const spy = vi.fn()
    render(<Harness initial={{ steps: [source] }} inputSources={[quotes, rates]} spy={spy} />)
    fireEvent.click(screen.getByRole("button", { name: "Add step" }))
    const menu = screen.getByRole("menu", { name: "Add step" })
    expect(within(menu).getAllByRole("menuitem")).toHaveLength(16)
    expect(within(menu).getAllByRole("group").map((g) => g.getAttribute("aria-label"))).toEqual(["Rows", "Columns", "Combine", "Values"])
    expect(within(within(menu).getByRole("group", { name: "Combine" })).getAllByRole("menuitem")).toHaveLength(5)
    expect(within(menu).getByRole("menuitem", { name: "Limit rows" })).toHaveAttribute("title", "Keep the first N rows")
    expect(screen.queryByRole("button", { name: "Add step" })).not.toBeInTheDocument()
    fireEvent.click(within(menu).getByRole("menuitem", { name: "Limit rows" }))
    expect(screen.getByRole("button", { name: "Add step" })).toBeInTheDocument()
    await waitFor(() => expect(screen.getByRole("button", { name: "Step 1: Limit rows" })).toBeInTheDocument(), { timeout: 5000 })
    await waitFor(() => expect(spy).toHaveBeenCalledWith("code", "df = quotes\ndf = df.step_1()"), { timeout: 5000 })
    expect(screen.getByTestId("polars-generated-code")).toHaveTextContent("df = df.step_1()")
  })

  it("opens one card at a time through the disclosure, collapses on Escape, deletes and reorders", async () => {
    const spy = vi.fn()
    render(<Harness initial={{ steps: [source, filter, limit] }} inputSources={[quotes]} spy={spy} />)
    const filterButton = screen.getByRole("button", { name: "Step 1: Filter rows" })
    const limitButton = screen.getByRole("button", { name: "Step 2: Limit rows" })
    expect(filterButton).toHaveAttribute("aria-expanded", "false")
    fireEvent.click(filterButton)
    expect(filterButton).toHaveAttribute("aria-expanded", "true")
    expect(screen.getByLabelText("Filter condition 1 column")).toHaveValue("premium")
    fireEvent.click(limitButton)
    expect(filterButton).toHaveAttribute("aria-expanded", "false")
    expect(limitButton).toHaveAttribute("aria-expanded", "true")
    fireEvent.keyDown(screen.getByLabelText("Row limit"), { key: "Escape" })
    expect(limitButton).toHaveAttribute("aria-expanded", "false")

    fireEvent.click(screen.getByRole("button", { name: "Move Step 1: Filter rows down" }))
    await waitFor(() => expect(screen.getByRole("button", { name: "Step 1: Limit rows" })).toBeInTheDocument(), { timeout: 5000 })
    expect(lastSteps(spy)).toEqual([source, limit, filter])

    fireEvent.click(screen.getByRole("button", { name: "Delete Step 1: Limit rows" }))
    await waitFor(() => expect(screen.queryByRole("button", { name: /Limit rows/ })).not.toBeInTheDocument(), { timeout: 5000 })
    expect(lastSteps(spy)).toEqual([source, filter])
  })

  it("badges the failing step without moving the user and offers Go to error", async () => {
    mockRender.mockImplementation(async () => ({
      ok: false,
      code: "",
      step_lines: [],
      step_index: 1,
      message: "Add at least one condition.",
    }))
    render(<Harness initial={{ steps: [source, filter, limit] }} inputSources={[quotes]} runError="This transform has no code yet. Step 1: Add at least one condition." />)
    const limitButton = screen.getByRole("button", { name: "Step 2: Limit rows" })
    fireEvent.click(limitButton)
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("Step 1: Add at least one condition."), { timeout: 5000 })
    expect(limitButton).toHaveAttribute("aria-expanded", "true")
    const filterButton = screen.getByRole("button", { name: "Step 1: Filter rows" })
    expect(filterButton).toHaveAttribute("aria-expanded", "false")
    expect(screen.getAllByRole("status")[0]).toHaveTextContent("Add at least one condition.")
    expect(screen.getByRole("button", { name: "Switch to code" })).toBeDisabled()
    fireEvent.click(screen.getByRole("button", { name: "Go to error" }))
    expect(filterButton).toHaveAttribute("aria-expanded", "true")
  })

  it("labels an error on the source step as Start from", async () => {
    mockRender.mockImplementation(async () => ({
      ok: false,
      code: "",
      step_lines: [],
      step_index: 0,
      message: "Unknown input 'policies'; connected inputs: quotes.",
    }))
    render(<Harness initial={{ steps: [{ ...source, input: "policies" }, limit] }} inputSources={[quotes]} runError="failed" />)
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("Start from: Unknown input 'policies'"), { timeout: 5000 })
  })

  it("keeps a half-built step quiet until the pipeline has run", async () => {
    mockRender.mockImplementation(async () => ({
      ok: false,
      code: "",
      step_lines: [],
      step_index: 1,
      message: "Column name must be a non-empty string.",
    }))
    const { rerender } = render(<Harness initial={{ steps: [source, { id: "w", kind: "with_column", name: "", expr: { type: "operand", operand: { kind: "column", name: "premium" } } }] }} inputSources={[quotes]} />)
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("Step 1 is not finished yet; it will be checked when the pipeline runs."), { timeout: 5000 })
    expect(screen.queryByRole("alert")).not.toBeInTheDocument()
    expect(screen.queryByText("Column name must be a non-empty string.")).not.toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Switch to code" })).toBeDisabled()
    rerender(<Harness initial={{ steps: [source, { id: "w", kind: "with_column", name: "", expr: { type: "operand", operand: { kind: "column", name: "premium" } } }] }} inputSources={[quotes]} runError="Step 1: Column name must be a non-empty string." />)
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("Step 1: Column name must be a non-empty string."), { timeout: 5000 })
  })

  it("maps a preview execution line to its step", () => {
    render(<Harness initial={{ steps: [source, filter, limit] }} inputSources={[quotes]} errorLine={3} />)
    const limitCard = screen.getByRole("button", { name: "Step 2: Limit rows" }).closest("[data-testid='polars-step-card']")
    expect(limitCard).toHaveTextContent("Failed when the pipeline ran")
  })

  it("switches to code only after a successful render and asks for confirmation", async () => {
    const onReplace = vi.fn()
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true)
    render(<Harness initial={{ steps: [source, limit], selected_columns: ["premium"] }} inputSources={[quotes]} onReplace={onReplace} />)
    const button = screen.getByRole("button", { name: "Switch to code" })
    expect(button).toBeDisabled()
    await waitFor(() => expect(button).toBeEnabled(), { timeout: 5000 })
    fireEvent.click(button)
    expect(confirm).toHaveBeenCalledOnce()
    expect(onReplace).toHaveBeenCalledWith({ selected_columns: ["premium"], code: "df = quotes\ndf = df.step_1()" })
    confirm.mockRestore()
  })

  it("shows a malformed persisted step as an invalid card instead of crashing", async () => {
    const spy = vi.fn()
    const malformed = { id: "bad", kind: "filter", match: "all" } as unknown as Step
    const nested = {
      id: "bad2",
      kind: "with_column",
      name: "n",
      expr: { type: "function", fn: "round", operand: { kind: "column", name: "premium" } },
    } as unknown as Step
    const badMembership = {
      id: "bad3",
      kind: "filter",
      match: "all",
      conditions: [{ column: "region", operator: "is_in", values: [{ kind: "literal", type: "string", value: "north" }] }],
    } as unknown as Step
    render(<Harness initial={{ steps: [source, malformed, nested, badMembership, limit] }} inputSources={[quotes]} spy={spy} />)
    expect(screen.getByRole("button", { name: "Step 3: Invalid step" }).closest("[data-testid='polars-step-card']")).toHaveTextContent("unsupported value type")
    const card = screen.getByRole("button", { name: "Step 1: Invalid step" })
    expect(card.closest("[data-testid='polars-step-card']")).toHaveTextContent('missing its "conditions" setting')
    const nestedCard = screen.getByRole("button", { name: "Step 2: Invalid step" })
    expect(nestedCard.closest("[data-testid='polars-step-card']")).toHaveTextContent("malformed function arguments")
    expect(screen.getByRole("button", { name: "Step 4: Limit rows" })).toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", { name: "Delete Step 1: Invalid step" }))
    await waitFor(() => expect(lastSteps(spy)).toEqual([source, nested, badMembership, limit]), { timeout: 5000 })
  })

  it("lets a new zero-input node switch to empty code without a render", () => {
    const onReplace = vi.fn()
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true)
    render(<Harness initial={{ steps: [] }} inputSources={[]} onReplace={onReplace} />)
    expect(screen.getByText(/Connect an input to start building steps/)).toBeInTheDocument()
    expect(screen.queryByRole("button", { name: "Add step" })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", { name: "Switch to code" }))
    expect(onReplace).toHaveBeenCalledWith({ code: "" })
    expect(mockRender).not.toHaveBeenCalled()
    confirm.mockRestore()
  })
})
