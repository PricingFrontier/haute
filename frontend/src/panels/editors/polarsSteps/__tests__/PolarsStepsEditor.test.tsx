import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { render, screen, cleanup, fireEvent, waitFor, within, act } from "@testing-library/react"
import { useState } from "react"

vi.mock("../../../../api/client", () => ({
  renderPolarsSteps: vi.fn(),
}))

vi.mock("../../CodeEditor", () => ({
  CodeEditor: ({ defaultValue, onChange }: { defaultValue: string; onChange: (value: string) => void }) => (
    <textarea aria-label="Free code" defaultValue={defaultValue} onChange={(event) => onChange(event.target.value)} />
  ),
}))

import { renderPolarsSteps } from "../../../../api/client"
import PolarsStepsEditor from "../PolarsStepsEditor"
import type { Step } from "../types"
import type { InputSource } from "../../_shared"
import useGraphStore, { resetGraphStoreForTests } from "../../../../stores/useGraphStore"
import { makeNode } from "../../../../test-utils/factories"

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
const freeCode: Step = { id: "code", kind: "free_code", code: "# prepare\ndf = df.with_columns(\n  pl.col('premium') * 2\n)" }

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

/** The real graph transaction used by node-panel config updates. */
function HistoryHarness() {
  const node = useGraphStore((state) => state.nodes[0])
  return <PolarsStepsEditor
    config={node.data.config as Record<string, unknown>}
    inputSources={[quotes]}
    onUpdate={(keyOrUpdates, value) => {
      const state = useGraphStore.getState()
      const updates = typeof keyOrUpdates === "string" ? { [keyOrUpdates]: value } : keyOrUpdates
      state.setNodesAndEdgesAndSubmodels(
        state.nodes.map((current) => ({ ...current, data: { ...current.data, config: { ...(current.data.config as object), ...updates } } })),
        state.edges,
        state.submodels,
      )
      return { ok: true }
    }}
  />
}

describe("PolarsStepsEditor", () => {
  beforeEach(() => {
    resetGraphStoreForTests()
    mockRender.mockReset()
    mockRender.mockImplementation(async ({ steps }) => okFor(steps))
  })
  afterEach(cleanup)

  it.each([false, true])("keeps one undo per step edit and preserves redo after rendering (pending prior edit: %s)", async (pendingPriorEdit) => {
    const codeFor = (n: number) => `df = quotes\ndf = df.head(${n})`
    mockRender.mockImplementation(async ({ steps }) => ({ ...okFor(steps), code: codeFor((steps[1] as Extract<Step, { kind: "limit" }>).n) }))
    useGraphStore.getState().setNodesRaw([makeNode("transform", "polars", { data: { config: { steps: [source, { ...limit, n: 10 }], code: codeFor(10) } } })])
    useGraphStore.getState().markSaved()
    render(<HistoryHarness />)
    await waitFor(() => expect(mockRender).toHaveBeenCalled(), { timeout: 5000 })
    fireEvent.click(screen.getByRole("button", { name: "Step 1: Limit rows" }))
    const field = screen.getByRole("spinbutton", { name: "Row limit" })
    if (pendingPriorEdit) {
      fireEvent.change(field, { target: { value: "15" } })
      fireEvent.blur(field)
    }
    fireEvent.change(field, { target: { value: "20" } })
    fireEvent.blur(field)
    const current = () => useGraphStore.getState().nodes[0].data.config as { code: string; steps: Step[] }
    await waitFor(() => expect(current().code).toBe(codeFor(20)), { timeout: 5000 })
    expect(useGraphStore.getState().undoStack).toHaveLength(pendingPriorEdit ? 2 : 1)
    await act(() => useGraphStore.getState().undo())
    const restored = pendingPriorEdit ? 15 : 10
    expect(field).toHaveValue(restored)
    await waitFor(() => expect(current().code).toBe(codeFor(restored)), { timeout: 5000 })
    expect(useGraphStore.getState().redoStack).toHaveLength(1)
    expect(useGraphStore.getState().dirty).toBe(pendingPriorEdit)
    await act(() => useGraphStore.getState().redo())
    expect(field).toHaveValue(20)
    await waitFor(() => expect(current().code).toBe(codeFor(20)), { timeout: 5000 })
    expect(useGraphStore.getState().undoStack).toHaveLength(pendingPriorEdit ? 2 : 1)
  })

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
    expect(within(menu).getAllByRole("menuitem")).toHaveLength(17)
    expect(within(menu).getAllByRole("group").map((g) => g.getAttribute("aria-label"))).toEqual(["Rows", "Columns", "Combine", "Values", "Code"])
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

  it("shows a card's summary only while it is collapsed", () => {
    render(<Harness initial={{ steps: [source, filter, limit] }} inputSources={[quotes]} />)
    const limitCard = screen.getByRole("button", { name: "Step 2: Limit rows" }).closest("[data-testid='polars-step-card']") as HTMLElement
    expect(limitCard).toHaveTextContent("5 rows")
    fireEvent.click(screen.getByRole("button", { name: "Step 2: Limit rows" }))
    expect(limitCard).not.toHaveTextContent("5 rows")
  })

  it("reorders steps by dragging a card onto another", async () => {
    const spy = vi.fn()
    const cast: Step = { id: "c", kind: "cast", casts: [{ column: "premium", dtype: "Float64" }] }
    render(<Harness initial={{ steps: [source, filter, limit, cast] }} inputSources={[quotes]} spy={spy} />)
    const cardOf = (name: string) => screen.getByRole("button", { name }).closest("[data-testid='polars-step-card']") as HTMLElement
    const header = screen.getByRole("button", { name: "Step 3: Change types" }).parentElement as HTMLElement
    expect(header).toHaveAttribute("draggable", "true")
    expect(header).toHaveAttribute("title", "Drag to reorder")
    const dataTransfer = { setData: vi.fn(), effectAllowed: "", dropEffect: "" }
    fireEvent.dragStart(header, { dataTransfer })
    fireEvent.dragOver(cardOf("Step 1: Filter rows"), { dataTransfer })
    expect(cardOf("Step 1: Filter rows")).toHaveAttribute("data-drop-target", "true")
    fireEvent.drop(cardOf("Step 1: Filter rows"), { dataTransfer })
    await waitFor(() => expect(lastSteps(spy)).toEqual([source, cast, filter, limit]), { timeout: 5000 })
    expect(screen.getByRole("button", { name: "Step 1: Change types" })).toBeInTheDocument()
    expect(screen.queryByText("[data-drop-target='true']")).not.toBeInTheDocument()
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
    await waitFor(() => expect(mockRender).toHaveBeenCalled(), { timeout: 5000 })
    expect(screen.queryByRole("status")).not.toBeInTheDocument()
    expect(screen.queryByRole("alert")).not.toBeInTheDocument()
    expect(screen.queryByText("Column name must be a non-empty string.")).not.toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Switch to code" })).toBeDisabled()
    rerender(<Harness initial={{ steps: [source, { id: "w", kind: "with_column", name: "", expr: { type: "operand", operand: { kind: "column", name: "premium" } } }] }} inputSources={[quotes]} runError="Step 1: Column name must be a non-empty string." />)
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("Step 1: Column name must be a non-empty string."), { timeout: 5000 })
  })

  it("maps a preview execution line to its single-line step", async () => {
    render(<Harness initial={{ steps: [source, filter, limit] }} inputSources={[quotes]} errorLine={3} />)
    await waitFor(() => expect(mockRender).toHaveBeenCalled(), { timeout: 5000 })
    const limitCard = screen.getByRole("button", { name: "Step 2: Limit rows" }).closest("[data-testid='polars-step-card']")
    await waitFor(() => expect(limitCard).toHaveTextContent("Failed when the pipeline ran"), { timeout: 5000 })
  })

  it("adds and edits multiline free code as a step, then switches using its rendered text", async () => {
    mockRender.mockImplementation(async ({ steps }) => ({
      ok: true,
      code: (steps as Step[]).map((step) => step.kind === "source" ? "df = quotes" : step.kind === "free_code" ? step.code : "df = df.step()").join("\n"),
      step_lines: [[1, 1], [2, 5]],
      step_index: null,
      message: "",
    }))
    const spy = vi.fn()
    const onReplace = vi.fn()
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true)
    render(<Harness initial={{ steps: [source] }} inputSources={[quotes]} spy={spy} onReplace={onReplace} />)
    fireEvent.click(screen.getByRole("button", { name: "Add step" }))
    fireEvent.click(screen.getByRole("menuitem", { name: "Free code" }))
    fireEvent.change(screen.getByRole("textbox", { name: "Free code" }), { target: { value: "# prepare\ndf = df.with_columns(\n  pl.col('premium') * 2\n)" } })
    await waitFor(() => expect(lastSteps(spy)).toEqual([source, expect.objectContaining({ kind: "free_code", code: "# prepare\ndf = df.with_columns(\n  pl.col('premium') * 2\n)" })]), { timeout: 5000 })
    expect(onReplace).not.toHaveBeenCalled()
    await waitFor(() => expect(screen.getByRole("button", { name: "Switch to code" })).toBeEnabled(), { timeout: 5000 })
    fireEvent.click(screen.getByRole("button", { name: "Switch to code" }))
    expect(onReplace).toHaveBeenCalledWith({ code: "df = quotes\n# prepare\ndf = df.with_columns(\n  pl.col('premium') * 2\n)" })
    confirm.mockRestore()
  })

  it("maps an execution line in a multiline free-code range to its card, then maps the next range to the following card", async () => {
    mockRender.mockResolvedValue({
      ok: true,
      code: "df = quotes\n# prepare\ndf = df.with_columns(\n  pl.col('premium') * 2\n)\ndf = df.head(5)",
      step_lines: [[1, 1], [2, 5], [6, 6]],
      step_index: null,
      message: "",
    })
    const { rerender } = render(<Harness initial={{ steps: [source, freeCode, limit] }} inputSources={[quotes]} errorLine={4} />)
    await waitFor(() => expect(screen.getByRole("button", { name: "Step 1: Free code" }).closest("[data-testid='polars-step-card']")).toHaveTextContent("Failed when the pipeline ran"), { timeout: 5000 })
    expect(screen.getByRole("button", { name: "Step 2: Limit rows" }).closest("[data-testid='polars-step-card']")).not.toHaveTextContent("Failed when the pipeline ran")
    rerender(<Harness initial={{ steps: [source, freeCode, limit] }} inputSources={[quotes]} errorLine={6} />)
    expect(screen.getByRole("button", { name: "Step 2: Limit rows" }).closest("[data-testid='polars-step-card']")).toHaveTextContent("Failed when the pipeline ran")
    expect(screen.getByRole("button", { name: "Step 1: Free code" }).closest("[data-testid='polars-step-card']")).not.toHaveTextContent("Failed when the pipeline ran")
  })

  it("reorders and deletes free code around a regular step", async () => {
    const spy = vi.fn()
    render(<Harness initial={{ steps: [source, freeCode, limit] }} inputSources={[quotes]} spy={spy} />)
    fireEvent.click(screen.getByRole("button", { name: "Move Step 1: Free code down" }))
    await waitFor(() => expect(lastSteps(spy)).toEqual([source, limit, freeCode]), { timeout: 5000 })
    fireEvent.click(screen.getByRole("button", { name: "Delete Step 2: Free code" }))
    await waitFor(() => expect(lastSteps(spy)).toEqual([source, limit]), { timeout: 5000 })
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

  it("writes the chosen start input when the node has several inputs", async () => {
    const spy = vi.fn()
    const frames: InputSource[] = ["output_1", "output_2"].map((name) => ({
      sourceNodeId: "Inputs_alias",
      sourceLabel: "Inputs display",
      name,
      edgeId: `edge_${name}`,
    }))
    const { rerender } = render(<Harness initial={{ steps: [] }} inputSources={frames} spy={spy} />)
    const selector = screen.getByLabelText("Start from input")
    expect(selector).toHaveValue("")
    expect(within(selector).getAllByRole("option").map((option) => option.textContent))
      .toEqual(["Choose an input", "output_1", "output_2"])
    expect(screen.getByRole("button", { name: "Add step" })).toBeDisabled()
    fireEvent.change(selector, { target: { value: "output_2" } })
    await waitFor(() => expect(lastSteps(spy)).toEqual([expect.objectContaining({ kind: "source", input: "output_2" })]), { timeout: 5000 })
    await waitFor(() => expect(mockRender).toHaveBeenLastCalledWith(expect.objectContaining({
      steps: [expect.objectContaining({ kind: "source", input: "output_2" })],
      inputNames: ["output_1", "output_2"],
    })), { timeout: 5000 })
    expect(screen.getByRole("button", { name: "Add step" })).toBeEnabled()

    rerender(<Harness initial={{ steps: [] }} inputSources={frames.map((frame) => ({
      ...frame, sourceNodeId: "renamed_alias", sourceLabel: "Renamed display",
    }))} spy={spy} />)
    expect(selector).toHaveValue("output_2")
    expect(within(selector).getAllByRole("option").map((option) => option.textContent))
      .toEqual(["output_1", "output_2"])
    expect(lastSteps(spy)).toEqual([expect.objectContaining({ kind: "source", input: "output_2" })])
  })

  it("keeps the open card on its step when another card is dragged past it", async () => {
    const spy = vi.fn()
    const cast: Step = { id: "c", kind: "cast", casts: [{ column: "premium", dtype: "Float64" }] }
    render(<Harness initial={{ steps: [source, filter, limit, cast] }} inputSources={[quotes]} spy={spy} />)
    fireEvent.click(screen.getByRole("button", { name: "Step 2: Limit rows" }))
    const cardOf = (name: string) => screen.getByRole("button", { name }).closest("[data-testid='polars-step-card']") as HTMLElement
    const header = screen.getByRole("button", { name: "Step 1: Filter rows" }).parentElement as HTMLElement
    const dataTransfer = { setData: vi.fn(), effectAllowed: "", dropEffect: "" }
    fireEvent.dragStart(header, { dataTransfer })
    fireEvent.dragOver(cardOf("Step 3: Change types"), { dataTransfer })
    fireEvent.drop(cardOf("Step 3: Change types"), { dataTransfer })
    await waitFor(() => expect(lastSteps(spy)).toEqual([source, limit, cast, filter]), { timeout: 5000 })
    expect(screen.getByRole("button", { name: "Step 1: Limit rows" })).toHaveAttribute("aria-expanded", "true")
    expect(screen.getByRole("button", { name: "Step 3: Filter rows" })).toHaveAttribute("aria-expanded", "false")
  })

  it("disables the switch to code in the no-input state while the persisted steps cannot render", async () => {
    mockRender.mockImplementation(async () => ({
      ok: false,
      code: "",
      step_lines: [],
      step_index: 0,
      message: "Unknown input 'quotes'; connected inputs: none.",
    }))
    const onReplace = vi.fn()
    render(<Harness initial={{ steps: [source] }} inputSources={[]} onReplace={onReplace} />)
    const button = screen.getByRole("button", { name: "Switch to code" })
    expect(button).toBeDisabled()
    await waitFor(() => expect(button).toHaveAttribute("title", "Fix Start from first"), { timeout: 5000 })
    expect(button).toBeDisabled()
    fireEvent.click(button)
    expect(onReplace).not.toHaveBeenCalled()
  })
})
