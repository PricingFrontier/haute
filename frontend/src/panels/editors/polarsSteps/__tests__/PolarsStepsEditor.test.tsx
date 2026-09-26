import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { render, renderHook, screen, cleanup, fireEvent, waitFor, within, act } from "@testing-library/react"
import type { Edge, Node } from "@xyflow/react"
import { readFileSync } from "node:fs"
import path from "node:path"
import { fileURLToPath } from "node:url"
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
import GeneratedCodePanel, { PENDING_FADE_MS } from "../GeneratedCodePanel"
import PolarsStepsEditor from "../PolarsStepsEditor"
import useKeyboardShortcuts from "../../../../hooks/useKeyboardShortcuts"
import useToastStore from "../../../../stores/useToastStore"
import useUIStore from "../../../../stores/useUIStore"
import type { NodeTypeValue } from "../../../../utils/nodeTypes"
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
  inputNames = inputSources.map((source) => source.name),
  start = "input",
  onReplace = vi.fn(),
  errorLine,
  runError,
  spy,
}: {
  initial: Record<string, unknown>
  inputSources: InputSource[]
  inputNames?: string[]
  start?: "input" | "frame"
  onReplace?: (config: Record<string, unknown>) => void
  errorLine?: number | null
  runError?: string | null
  spy?: (key: string, value: unknown) => void
}) {
  const [config, setConfig] = useState(initial)
  return (
    <PolarsStepsEditor
      start={start}
      inputNames={inputNames}
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
    start="input"
    config={node.data.config as Record<string, unknown>}
    inputSources={[quotes]}
    inputNames={["quotes"]}
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

// This case drives a debounced editor through several edits, renders, undos and
// redos. It fits the 30s default comfortably on its own; a whole parallel suite
// run can starve it past that without making it wrong.
const UNDO_REDO_TIMEOUT_MS = 90_000

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
  }, UNDO_REDO_TIMEOUT_MS)

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

  it("says what a half-built step needs, neutrally, until the pipeline has run", async () => {
    mockRender.mockImplementation(async () => ({
      ok: false,
      code: "",
      step_lines: [],
      step_index: 1,
      message: "Column name must be a non-empty string.",
    }))
    const { rerender } = render(<Harness initial={{ steps: [source, { id: "w", kind: "with_column", name: "", expr: { type: "operand", operand: { kind: "column", name: "premium" } } }] }} inputSources={[quotes]} />)
    await waitFor(() => expect(screen.getByTestId("polars-code-note")).toHaveTextContent("Step 1 isn't finished: it needs a name for the new column."), { timeout: 5000 })
    expect(screen.queryByRole("alert")).not.toBeInTheDocument()
    const card = screen.getByRole("button", { name: "Step 1: Add column" }).closest("[data-testid='polars-step-card']")
    expect(card).toHaveTextContent("Needs a name for the new column.")
    expect(screen.getByRole("button", { name: "Switch to code" })).toBeDisabled()
    rerender(<Harness initial={{ steps: [source, { id: "w", kind: "with_column", name: "", expr: { type: "operand", operand: { kind: "column", name: "premium" } } }] }} inputSources={[quotes]} runError="Step 1: Column name must be a non-empty string." />)
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("Step 1: Column name must be a non-empty string."), { timeout: 5000 })
    expect(screen.queryByTestId("polars-code-note")).not.toBeInTheDocument()
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

  it.each([null, { kind: "source", input: "quotes" }, { id: "bad", kind: "source", input: {} }])("keeps an invalid first step visible and deletable: %j", (first) => {
    const spy = vi.fn()
    render(<Harness initial={{ steps: [first, limit] }} inputSources={[quotes]} spy={spy} />)
    expect(screen.getByRole("button", { name: "Invalid start step" })).toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", { name: "Delete Invalid start step" }))
    expect(lastSteps(spy)).toEqual([limit])
  })

  it.each([[quotes], [quotes, rates]])("repairs a missing start step without dropping the first authored operation (%j)", (...inputSources) => {
    const spy = vi.fn()
    render(<Harness initial={{ steps: [filter, limit] }} inputSources={inputSources} spy={spy} />)
    expect(screen.getByRole("button", { name: "Invalid start step" })).toBeInTheDocument()
    expect(screen.getByLabelText("Start from input")).toHaveValue("")
    fireEvent.change(screen.getByLabelText("Start from input"), { target: { value: "quotes" } })
    expect(lastSteps(spy)).toEqual([expect.objectContaining({ kind: "source", input: "quotes" }), filter, limit])
    expect(screen.getByRole("button", { name: "Step 1: Filter rows" })).toBeInTheDocument()
  })

  it("shows a disconnected start input until the user explicitly chooses its replacement", () => {
    const spy = vi.fn()
    render(<Harness initial={{ steps: [{ ...source, input: "removed" }, limit] }} inputSources={[quotes]} spy={spy} />)
    expect(screen.getByLabelText("Start from input")).toHaveValue("removed")
    expect(screen.getByRole("option", { name: "removed (not connected)" })).toBeDisabled()
    expect(screen.getByRole("button", { name: "Add step" })).toBeDisabled()
    fireEvent.change(screen.getByLabelText("Start from input"), { target: { value: "quotes" } })
    expect(lastSteps(spy)).toEqual([{ ...source, input: "quotes" }, limit])
    expect(screen.getByRole("button", { name: "Add step" })).toBeEnabled()
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

describe("PolarsStepsEditor in frame mode", () => {
  beforeEach(() => {
    resetGraphStoreForTests()
    mockRender.mockReset()
    mockRender.mockImplementation(async ({ steps }) => ({
      ok: true,
      code: steps.map((_, i) => `df = df.step_${i + 1}()`).join("\n"),
      step_lines: steps.map((_, i) => [i + 1, i + 1]),
      step_index: null,
      message: "",
    }))
  })
  afterEach(cleanup)

  it("shows no start card and no input selector", () => {
    render(<Harness initial={{ steps: [] }} inputSources={[]} start="frame" />)
    expect(screen.queryByRole("group", { name: "Start from" })).not.toBeInTheDocument()
    expect(screen.queryByText("Start from")).not.toBeInTheDocument()
    expect(screen.queryByLabelText("Start from input")).not.toBeInTheDocument()
    expect(screen.queryByText("Connect an input to start building steps, or switch to code.")).not.toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Add step" })).toBeEnabled()
    expect(mockRender).not.toHaveBeenCalled()
  })

  it("adds Step 1 without a source step and renders with start frame and no input names", async () => {
    const spy = vi.fn()
    render(<Harness initial={{ steps: [] }} inputSources={[]} start="frame" spy={spy} />)
    fireEvent.click(screen.getByRole("button", { name: "Add step" }))
    const menu = screen.getByRole("menu", { name: "Add step" })
    expect(within(menu).queryByRole("menuitem", { name: "Join another input" })).not.toBeInTheDocument()
    expect(within(menu).queryByRole("menuitem", { name: "Append inputs" })).not.toBeInTheDocument()
    const combine = within(menu).getByRole("group", { name: "Combine" })
    expect(within(combine).getAllByRole("menuitem")).toHaveLength(3)
    expect(within(menu).getAllByRole("menuitem")).toHaveLength(15)
    fireEvent.click(within(menu).getByRole("menuitem", { name: "Limit rows" }))
    await waitFor(() => expect(lastSteps(spy)).toHaveLength(1), { timeout: 5000 })
    expect((lastSteps(spy) as Step[])[0]).toMatchObject({ kind: "limit" })
    expect(screen.getByRole("button", { name: "Step 1: Limit rows" })).toHaveAttribute("aria-expanded", "true")
    await waitFor(() => expect(mockRender).toHaveBeenCalled(), { timeout: 5000 })
    expect(mockRender.mock.calls[0][0]).toMatchObject({ start: "frame", inputNames: [] })
    await waitFor(() => expect(spy).toHaveBeenCalledWith("code", "df = df.step_1()"), { timeout: 5000 })
  })

  it("numbers every card from Step 1, moves the first card, and opens it from Go to error", async () => {
    const spy = vi.fn()
    render(<Harness initial={{ steps: [filter, limit] }} inputSources={[]} start="frame" spy={spy} />)
    expect(screen.getByRole("button", { name: "Step 1: Filter rows" })).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Step 2: Limit rows" })).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Move Step 1: Filter rows up" })).toBeDisabled()
    fireEvent.click(screen.getByRole("button", { name: "Move Step 2: Limit rows up" }))
    await waitFor(() => expect(lastSteps(spy)).toEqual([limit, filter]), { timeout: 5000 })
    expect(screen.getByRole("button", { name: "Step 1: Limit rows" })).toBeInTheDocument()

    mockRender.mockImplementation(async () => ({ ok: false, code: "", step_lines: [], step_index: 0, message: "Row limit must be a whole number greater than zero." }))
    cleanup()
    render(<Harness initial={{ steps: [limit, filter] }} inputSources={[]} start="frame" runError="failed" />)
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("Step 1: Row limit must be a whole number greater than zero."), { timeout: 5000 })
    const limitButton = screen.getByRole("button", { name: "Step 1: Limit rows" })
    expect(limitButton).toHaveAttribute("aria-expanded", "false")
    fireEvent.click(screen.getByRole("button", { name: "Go to error" }))
    expect(limitButton).toHaveAttribute("aria-expanded", "true")
  })

  it("shows a persisted source step as an invalid card that can only be deleted", async () => {
    const spy = vi.fn()
    render(<Harness initial={{ steps: [source, limit] }} inputSources={[]} start="frame" spy={spy} />)
    const invalid = screen.getByRole("button", { name: "Step 1: Invalid step" })
    expect(invalid.closest("[data-testid='polars-step-card']")).toHaveTextContent("This node starts from df; delete this step.")
    expect(screen.getByRole("button", { name: "Step 2: Limit rows" })).toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", { name: "Delete Step 1: Invalid step" }))
    await waitFor(() => expect(lastSteps(spy)).toEqual([limit]), { timeout: 5000 })
  })

  it("switches an empty list to empty code without a render", () => {
    const onReplace = vi.fn()
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true)
    render(<Harness initial={{ steps: [], path: "quotes.parquet" }} inputSources={[]} start="frame" onReplace={onReplace} />)
    fireEvent.click(screen.getByRole("button", { name: "Switch to code" }))
    expect(confirm).toHaveBeenCalledOnce()
    expect(onReplace).toHaveBeenCalledWith({ path: "quotes.parquet", code: "" })
    expect(mockRender).not.toHaveBeenCalled()
    confirm.mockRestore()
  })
})

/* The step editor's experience: keys, focus, notes and linked code. */

const quotesTyped: InputSource = { ...quotes, columns: COLUMNS }
const cardOf = (name: string) => screen.getByRole("button", { name }).closest("[data-testid='polars-step-card']") as HTMLElement
const lineOf = (n: number) => document.querySelector(`[data-line="${n}"]`) as HTMLElement
const addGross: Step = {
  id: "w",
  kind: "with_column",
  name: "gross",
  expr: { type: "binary", left: { kind: "column", name: "premium" }, op: "*", right: { kind: "literal", type: "number", value: 2 }, text: "premium * 2" },
}

function mountCanvasShortcuts(nodes: Node[]) {
  const params = {
    handleSave: vi.fn(),
    setNodes: vi.fn(),
    setEdges: vi.fn(),
    setNodesAndEdges: vi.fn(),
    undo: vi.fn(),
    redo: vi.fn(),
    fitView: vi.fn(),
    graphRef: { current: { nodes, edges: [] as Edge[] } },
    clipboard: { current: { nodes: [{ id: "copied", position: { x: 0, y: 0 }, data: { label: "Copied" } } as Node], edges: [] as Edge[] } },
    nodeIdCounter: { current: 0 },
    setSelectedNode: vi.fn(),
    setPreviewData: vi.fn(),
    clearTrace: vi.fn(),
    closePanel: vi.fn(),
    isInsideSubmodel: false,
    readOnly: false,
    existingSingletonTypes: new Set<NodeTypeValue>(),
    resolveGraphIdentities: vi.fn(async (n: readonly Node[], e: readonly Edge[]) => ({ nodes: [...n], edges: [...e] })),
  }
  renderHook(() => useKeyboardShortcuts(params))
  return params
}

describe("PolarsStepsEditor keys and focus", () => {
  beforeEach(() => {
    resetGraphStoreForTests()
    mockRender.mockReset()
    mockRender.mockImplementation(async ({ steps }) => okFor(steps))
    useUIStore.setState({ shortcutsOpen: false, submodelDialog: null, nodeSearchOpen: false })
    useToastStore.setState({ toasts: [], _toastCounter: 0 })
  })
  afterEach(cleanup)

  it("keeps the canvas's graph shortcuts from acting while a step card's control has focus", () => {
    const selected = [
      { id: "n1", position: { x: 0, y: 0 }, data: { label: "Claims" }, selected: true } as Node,
      { id: "n2", position: { x: 0, y: 0 }, data: { label: "Quotes" }, selected: true } as Node,
    ]
    const params = mountCanvasShortcuts(selected)
    render(<Harness initial={{ steps: [source, filter] }} inputSources={[quotes]} />)
    fireEvent.click(screen.getByRole("button", { name: "Step 1: Filter rows" }))
    const select = screen.getByLabelText("Filter condition 1 operator")
    select.focus()
    const clipboard = params.clipboard.current
    fireEvent.keyDown(select, { key: "Delete" })
    fireEvent.keyDown(select, { key: "Backspace" })
    fireEvent.keyDown(select, { key: "a", ctrlKey: true })
    fireEvent.keyDown(select, { key: "c", ctrlKey: true })
    fireEvent.keyDown(select, { key: "v", ctrlKey: true })
    fireEvent.keyDown(select, { key: "g", ctrlKey: true })
    expect(params.setNodesAndEdges).not.toHaveBeenCalled()
    expect(params.setNodes).not.toHaveBeenCalled()
    expect(params.clipboard.current).toBe(clipboard)
    expect(params.resolveGraphIdentities).not.toHaveBeenCalled()
    expect(useUIStore.getState().submodelDialog).toBeNull()
    expect(useToastStore.getState().toasts).toHaveLength(0)
    // Document and window shortcuts stay global.
    fireEvent.keyDown(select, { key: "s", ctrlKey: true })
    expect(params.handleSave).toHaveBeenCalledOnce()
    fireEvent.keyDown(select, { key: "z", ctrlKey: true })
    expect(params.undo).toHaveBeenCalledOnce()
    // Outside the editor the same key still deletes the selection: the handler is live.
    fireEvent.keyDown(document.body, { key: "Delete" })
    expect(params.setNodesAndEdges).toHaveBeenCalledOnce()
  })

  it("moves focus into a new step's first field, and Alt+arrows move a card keeping its focus", async () => {
    render(<Harness initial={{ steps: [source, limit] }} inputSources={[quotes]} />)
    fireEvent.click(screen.getByRole("button", { name: "Add step" }))
    fireEvent.click(screen.getByRole("menuitem", { name: "Filter rows" }))
    await waitFor(() => expect(document.activeElement).toBe(screen.getByRole("combobox", { name: "Filter condition 1 column" })), { timeout: 5000 })
    const header = screen.getByRole("button", { name: "Step 1: Limit rows" })
    header.focus()
    fireEvent.keyDown(header, { key: "ArrowDown", altKey: true })
    await waitFor(() => expect(screen.getByRole("button", { name: "Step 2: Limit rows" })).toBe(document.activeElement), { timeout: 5000 })
    fireEvent.keyDown(document.activeElement as HTMLElement, { key: "ArrowUp", altKey: true })
    await waitFor(() => expect(screen.getByRole("button", { name: "Step 1: Limit rows" })).toBe(document.activeElement), { timeout: 5000 })
  })

  it("opens a new Add column step on its name, with the formula box showing its example", async () => {
    render(<Harness initial={{ steps: [source] }} inputSources={[quotes]} />)
    fireEvent.click(screen.getByRole("button", { name: "Add step" }))
    fireEvent.click(screen.getByRole("menuitem", { name: "Add column" }))
    await waitFor(() => expect(document.activeElement).toBe(screen.getByLabelText("Column name")), { timeout: 5000 })
    expect(screen.getByLabelText("Expression type")).toHaveValue("binary")
    expect(screen.getByRole("combobox", { name: "Formula" })).toHaveAttribute("placeholder", "e.g. (premium + commission) * tax / 12")
  })
})

describe("PolarsStepsEditor notes and linked code", () => {
  beforeEach(() => {
    resetGraphStoreForTests()
    mockRender.mockReset()
    mockRender.mockImplementation(async ({ steps }) => okFor(steps))
  })
  afterEach(cleanup)

  it("dims code the steps no longer produce, names what is unfinished, and blames no card when the render itself failed", async () => {
    render(<Harness initial={{ steps: [source, limit] }} inputSources={[quotes]} />)
    const code = () => screen.getByTestId("polars-generated-code")
    await waitFor(() => expect(code()).toHaveTextContent("df = df.step_1()"), { timeout: 5000 })
    expect(code()).not.toHaveAttribute("data-stale")
    mockRender.mockImplementation(async () => ({ ok: false, code: "", step_lines: [], step_index: 1, message: "Row limit must be at least 1." }))
    fireEvent.click(screen.getByRole("button", { name: "Step 1: Limit rows" }))
    const input = screen.getByLabelText("Row limit")
    fireEvent.change(input, { target: { value: "7" } })
    fireEvent.blur(input)
    await waitFor(() => expect(code()).toHaveAttribute("data-stale", "true"), { timeout: 5000 })
    expect(code()).toHaveTextContent("df = df.step_1()")
    expect(screen.getByTestId("polars-code-note")).toHaveTextContent("Step 1 isn't finished: Row limit must be at least 1.")
    expect(cardOf("Step 1: Limit rows")).toHaveTextContent("Needs: Row limit must be at least 1.")
    expect(screen.queryByRole("alert")).not.toBeInTheDocument()

    mockRender.mockImplementation(async () => {
      throw new Error("Failed to fetch")
    })
    fireEvent.change(input, { target: { value: "8" } })
    fireEvent.blur(input)
    await waitFor(() => expect(screen.getByTestId("polars-code-note")).toHaveTextContent("Could not render steps: Failed to fetch"), { timeout: 5000 })
    expect(screen.queryByText(/^Needs:/)).not.toBeInTheDocument()
    expect(code()).toHaveTextContent("df = df.step_1()")

    mockRender.mockImplementation(async ({ steps }) => okFor(steps))
    fireEvent.change(input, { target: { value: "9" } })
    fireEvent.blur(input)
    await waitFor(() => expect(screen.queryByTestId("polars-code-note")).not.toBeInTheDocument(), { timeout: 5000 })
    expect(code()).not.toHaveAttribute("data-stale")
  })

  it("puts a failed run's message on the card whose lines failed, with Go to error", async () => {
    const message = 'unable to find column "quot_id"; valid columns: ["premium"]\n\nResolved plan until failure: ...'
    render(<Harness initial={{ steps: [source, filter, limit] }} inputSources={[quotes]} errorLine={2} runError={message} />)
    await waitFor(() => expect(cardOf("Step 1: Filter rows")).toHaveTextContent('unable to find column "quot_id"; valid columns: ["premium"]'), { timeout: 5000 })
    expect(cardOf("Step 1: Filter rows")).not.toHaveTextContent("Resolved plan")
    expect(screen.getByRole("alert")).toHaveTextContent('Step 1: unable to find column "quot_id"')
    fireEvent.click(screen.getByRole("button", { name: "Go to error" }))
    expect(screen.getByRole("button", { name: "Step 1: Filter rows" })).toHaveAttribute("aria-expanded", "true")
  })

  it("marks a collapsed card naming a column the data does not have at its step, including after a reorder", async () => {
    const typo: Step = { id: "t", kind: "filter", match: "all", conditions: [{ column: "premum", operator: "is_null" }] }
    const usesGross: Step = { id: "u", kind: "filter", match: "all", conditions: [{ column: "gross", operator: "is_null" }] }
    render(<Harness initial={{ steps: [source, typo, addGross, usesGross] }} inputSources={[quotesTyped]} />)
    expect(cardOf("Step 1: Filter rows")).toHaveTextContent("Not in the data at this step: premum")
    expect(cardOf("Step 3: Filter rows")).not.toHaveTextContent("Not in the data")
    fireEvent.click(screen.getByRole("button", { name: "Move Step 3: Filter rows up" }))
    await waitFor(() => expect(cardOf("Step 2: Filter rows")).toHaveTextContent("Not in the data at this step: gross"), { timeout: 5000 })
  })

  it("summarises collapsed cards in parts, with the change each step makes to the columns", () => {
    const dropRegion: Step = { id: "d", kind: "drop", columns: ["region"] }
    const total: Step = { id: "g", kind: "group_by", keys: [], aggregations: [{ column: "premium", agg: "sum", name: "total" }] }
    const fresh: Step = { id: "f2", kind: "filter", match: "all", conditions: [{ column: "", operator: "eq", value: { kind: "literal", type: "number", value: 0 } }] }
    render(<Harness initial={{ steps: [source, addGross, dropRegion, total, fresh] }} inputSources={[quotesTyped]} />)
    expect(cardOf("Step 1: Add column")).toHaveTextContent("+gross")
    expect(within(cardOf("Step 1: Add column")).getByText("gross").tagName).toBe("CODE")
    expect(cardOf("Step 2: Drop columns")).toHaveTextContent("−region")
    expect(cardOf("Step 3: Group and aggregate")).toHaveTextContent("total = sum of premium")
    expect(cardOf("Step 3: Group and aggregate")).toHaveTextContent("→ 1 column")
    expect(cardOf("Step 4: Filter rows")).toHaveTextContent("Choose a column…")
  })

  it("states no column change where the columns are not exactly known", () => {
    const keepByType: Step = { id: "k", kind: "select", columns: ["premium"], dtypes: ["String"] }
    render(<Harness initial={{ steps: [source, freeCode, { ...addGross, id: "w1" }, keepByType, { ...addGross, id: "w2", name: "net" }] }} inputSources={[quotesTyped]} />)
    expect(cardOf("Step 2: Add column")).not.toHaveTextContent("+gross")
    expect(cardOf("Step 3: Keep columns")).not.toHaveTextContent("−")
    expect(cardOf("Step 4: Add column")).not.toHaveTextContent("+net")
  })

  it("keeps three action slots on every card and lines the summary up with the label", () => {
    render(<Harness initial={{ steps: [source, filter, limit, { ...limit, id: "l2", n: 9 }] }} inputSources={[quotes]} />)
    expect(screen.getByRole("button", { name: "Move Step 1: Filter rows up" })).toBeDisabled()
    expect(screen.getByRole("button", { name: "Move Step 1: Filter rows down" })).toBeEnabled()
    expect(screen.getByRole("button", { name: "Move Step 2: Limit rows up" })).toBeEnabled()
    expect(screen.getByRole("button", { name: "Move Step 3: Limit rows down" })).toBeDisabled()
    for (const n of [1, 2, 3]) expect(screen.getByRole("button", { name: new RegExp(`^Delete Step ${n}:`) })).toBeInTheDocument()
    const number = screen.getAllByTestId("step-number")[0]
    expect(number).toHaveTextContent("1")
    expect(number.style.background).toBe("")
    expect(screen.getAllByTestId("step-summary")[0].parentElement).toBe(screen.getAllByTestId("step-label")[0].parentElement)
  })

  it("links cards and their code lines both ways, and colours the code like code mode", async () => {
    render(<Harness initial={{ steps: [source, filter, limit] }} inputSources={[quotes]} />)
    await waitFor(() => expect(lineOf(3)).toBeInTheDocument(), { timeout: 5000 })
    fireEvent.mouseEnter(cardOf("Step 1: Filter rows"))
    expect(lineOf(2)).toHaveAttribute("data-active", "true")
    expect(lineOf(1)).not.toHaveAttribute("data-active")
    expect(lineOf(3)).not.toHaveAttribute("data-active")
    fireEvent.mouseLeave(cardOf("Step 1: Filter rows"))
    fireEvent.mouseEnter(lineOf(3))
    expect(cardOf("Step 2: Limit rows")).toHaveAttribute("data-highlighted", "true")
    fireEvent.click(lineOf(3))
    expect(screen.getByRole("button", { name: "Step 2: Limit rows" })).toHaveAttribute("aria-expanded", "true")
    expect(lineOf(1).querySelector("span[style*='--syntax']")).not.toBeNull()
  })

  it("tints every line of a step that spans several", async () => {
    mockRender.mockImplementation(async () => ({ ok: true, code: 'df = quotes\ndf = df.group_by(\n    ["region"],\n)', step_lines: [[1, 1], [2, 4]], step_index: null, message: "" }))
    render(<Harness initial={{ steps: [source, limit] }} inputSources={[quotes]} />)
    await waitFor(() => expect(lineOf(4)).toBeInTheDocument(), { timeout: 5000 })
    fireEvent.mouseEnter(cardOf("Step 1: Limit rows"))
    for (const n of [2, 3, 4]) expect(lineOf(n)).toHaveAttribute("data-active", "true")
    expect(lineOf(1)).not.toHaveAttribute("data-active")
  })

  it("draws the step number, label and summary in colours that reach 4.5:1 on the card", () => {
    const css = readFileSync(path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../../../index.css"), "utf8")
    const token = (name: string) => {
      const match = new RegExp(`^\\s*--${name}:\\s*(#[0-9a-fA-F]{6})\\s*;`, "m").exec(css)
      if (!match) throw new Error(`no hex token --${name}`)
      return match[1]
    }
    const luminance = (hex: string) => {
      const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255).map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4))
      return 0.2126 * r + 0.7152 * g + 0.0722 * b
    }
    const contrast = (a: string, b: string) => {
      const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x)
      return (hi + 0.05) / (lo + 0.05)
    }
    const card = token("bg-elevated")
    // The number and muted words, the label, and the summary text.
    for (const name of ["text-muted", "text-primary", "text-secondary"]) expect(contrast(token(name), card)).toBeGreaterThanOrEqual(4.5)
  })
})

describe("GeneratedCodePanel", () => {
  afterEach(() => {
    cleanup()
    vi.useRealTimers()
  })

  it("fades the code only when a render is still pending after 400 ms", () => {
    vi.useFakeTimers()
    const props = { start: "input" as const, code: "df = quotes", error: null, switchEnabled: false, onSwitchToCode: vi.fn() }
    const { rerender } = render(<GeneratedCodePanel {...props} pending />)
    const pre = () => screen.getByTestId("polars-generated-code")
    expect(pre().style.opacity).toBe("1")
    act(() => vi.advanceTimersByTime(PENDING_FADE_MS - 1))
    expect(pre().style.opacity).toBe("1")
    act(() => vi.advanceTimersByTime(1))
    expect(pre().style.opacity).toBe("0.5")
    expect(screen.getByText("rendering…")).toBeInTheDocument()
    rerender(<GeneratedCodePanel {...props} pending={false} />)
    expect(pre().style.opacity).toBe("1")
    rerender(<GeneratedCodePanel {...props} pending />)
    act(() => vi.advanceTimersByTime(200))
    rerender(<GeneratedCodePanel {...props} pending={false} />)
    act(() => vi.advanceTimersByTime(PENDING_FADE_MS))
    expect(pre().style.opacity).toBe("1")
    expect(screen.queryByText("rendering…")).not.toBeInTheDocument()
  })
})
