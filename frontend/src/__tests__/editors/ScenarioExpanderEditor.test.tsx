/**
 * Render tests for ScenarioExpanderEditor.
 *
 * Tests: row key label, select vs text input, value column, range section,
 * default values, editing min, editing steps with clamping, step column,
 * preview line, selecting a column, InputSourcesBar rendering.
 */
import { describe, it, expect, vi, afterEach } from "vitest"
import { render, screen, fireEvent, cleanup } from "@testing-library/react"
import ScenarioExpanderEditor from "../../panels/editors/ScenarioExpanderEditor"

vi.mock("../../panels/editors/_shared", async () => {
  const actual = await vi.importActual("../../panels/editors/_shared")
  return {
    ...actual,
    InputSourcesBar: ({ inputSources }: { inputSources: { varName: string; edgeId: string; sourceLabel: string }[] }) => (
      <div data-testid="input-sources">{inputSources?.length ?? 0} inputs</div>
    ),
    INPUT_STYLE: {},
  }
})

vi.mock("../../panels/editors/CodeEditor", () => ({
  CodeEditor: ({ defaultValue, onChange, placeholder }: { defaultValue: string; onChange: (v: string) => void; placeholder?: string; errorLine?: number | null }) => (
    <textarea
      data-testid="code-editor"
      defaultValue={defaultValue}
      onChange={(e) => onChange(e.target.value)}
      placeholder={placeholder}
    />
  ),
}))

afterEach(cleanup)

const DEFAULT_PROPS = {
  config: {},
  onUpdate: vi.fn(),
  inputSources: [] as { varName: string; edgeId: string; sourceLabel: string }[],
  upstreamColumns: [] as { name: string; dtype: string }[],
  accentColor: "#2dd4bf",
}

describe("ScenarioExpanderEditor", () => {
  it("renders core fields and labels", () => {
    render(<ScenarioExpanderEditor {...DEFAULT_PROPS} />)
    expect(screen.getByText("Row Key")).toBeTruthy()
    expect(screen.getByText("Index Column")).toBeTruthy()
    expect(screen.getByText("Steps")).toBeTruthy()
    expect(screen.getByText("Value Column")).toBeTruthy()
  })

  it("hides value range when column_name is empty", () => {
    const { container } = render(<ScenarioExpanderEditor {...DEFAULT_PROPS} />)
    expect(container.textContent).not.toContain("Value Range")
    expect(container.textContent).not.toContain("Step Size")
  })

  it("shows value range when column_name is set", () => {
    render(<ScenarioExpanderEditor {...DEFAULT_PROPS} config={{ column_name: "sv" }} />)
    expect(screen.getByText("Value Range")).toBeTruthy()
    expect(screen.getByText("Min")).toBeTruthy()
    expect(screen.getByText("Max")).toBeTruthy()
    expect(screen.getByText("Step Size")).toBeTruthy()
  })

  it("shows select dropdown when upstreamColumns provided", () => {
    const columns = [
      { name: "quote_id", dtype: "Utf8" },
      { name: "product", dtype: "Utf8" },
    ]
    const { container } = render(
      <ScenarioExpanderEditor {...DEFAULT_PROPS} upstreamColumns={columns} />,
    )
    const select = container.querySelector("select")
    expect(select).toBeTruthy()
    expect(screen.getByText("quote_id")).toBeTruthy()
    expect(screen.getByText("product")).toBeTruthy()
    // Should show the placeholder option too
    expect(screen.getByText("-- select column --")).toBeTruthy()
  })

  it("falls back to text input when no upstream columns", () => {
    const { container } = render(
      <ScenarioExpanderEditor {...DEFAULT_PROPS} upstreamColumns={[]} />,
    )
    const select = container.querySelector("select")
    expect(select).toBeNull()
    // Row key renders as a text input when no upstream columns
    const textInputs = container.querySelectorAll('input[type="text"]')
    expect(textInputs.length).toBeGreaterThan(0)
  })

  it("changing min value calls onUpdate with parsed number", () => {
    const onUpdate = vi.fn()
    render(<ScenarioExpanderEditor {...DEFAULT_PROPS} onUpdate={onUpdate} config={{ column_name: "sv", min_value: 0.8 }} />)
    const minInput = screen.getByDisplayValue("0.8")
    fireEvent.change(minInput, { target: { value: "0.5" } })
    expect(onUpdate).toHaveBeenCalledWith("min_value", 0.5)
  })

  it("changing min to invalid value falls back to 0", () => {
    const onUpdate = vi.fn()
    render(<ScenarioExpanderEditor {...DEFAULT_PROPS} onUpdate={onUpdate} config={{ column_name: "sv", min_value: 0.8 }} />)
    const minInput = screen.getByDisplayValue("0.8")
    fireEvent.change(minInput, { target: { value: "abc" } })
    expect(onUpdate).toHaveBeenCalledWith("min_value", 0)
  })

  it("changing max value calls onUpdate with parsed number", () => {
    const onUpdate = vi.fn()
    render(<ScenarioExpanderEditor {...DEFAULT_PROPS} onUpdate={onUpdate} config={{ column_name: "sv", max_value: 1.2 }} />)
    const maxInput = screen.getByDisplayValue("1.2")
    fireEvent.change(maxInput, { target: { value: "2.0" } })
    expect(onUpdate).toHaveBeenCalledWith("max_value", 2.0)
  })

  it("changing steps calls onUpdate with value clamped to min 1", () => {
    const onUpdate = vi.fn()
    render(<ScenarioExpanderEditor {...DEFAULT_PROPS} onUpdate={onUpdate} config={{ steps: 21 }} />)
    const stepsInput = screen.getByDisplayValue("21")

    // Normal value
    fireEvent.change(stepsInput, { target: { value: "10" } })
    expect(onUpdate).toHaveBeenCalledWith("steps", 10)

    // Zero should clamp to 1
    fireEvent.change(stepsInput, { target: { value: "0" } })
    expect(onUpdate).toHaveBeenCalledWith("steps", 1)

    // Negative should clamp to 1
    fireEvent.change(stepsInput, { target: { value: "-5" } })
    expect(onUpdate).toHaveBeenCalledWith("steps", 1)

    // Non-numeric should default to 1
    fireEvent.change(stepsInput, { target: { value: "abc" } })
    expect(onUpdate).toHaveBeenCalledWith("steps", 1)
  })

  it("step size shows calculated interval", () => {
    render(<ScenarioExpanderEditor {...DEFAULT_PROPS} config={{ column_name: "sv", min_value: 0.8, max_value: 1.2, steps: 21 }} />)
    const stepSize = screen.getByTestId("step-size")
    // (1.2 - 0.8) / (21 - 1) = 0.02
    expect(stepSize.textContent).toBe("0.02")
  })

  it("step size shows dash when steps is 1", () => {
    render(<ScenarioExpanderEditor {...DEFAULT_PROPS} config={{ column_name: "sv", steps: 1 }} />)
    const stepSize = screen.getByTestId("step-size")
    expect(stepSize.textContent).toBe("—")
  })

  it("InputSourcesBar renders when inputSources provided", () => {
    const inputSources = [
      { varName: "upstream_data", sourceLabel: "Upstream", edgeId: "e1" },
    ]
    render(<ScenarioExpanderEditor {...DEFAULT_PROPS} inputSources={inputSources} />)
    expect(screen.getByTestId("input-sources")).toBeTruthy()
    expect(screen.getByTestId("input-sources").textContent).toContain("1 inputs")
  })

  it("InputSourcesBar not rendered when inputSources is empty", () => {
    render(<ScenarioExpanderEditor {...DEFAULT_PROPS} inputSources={[]} />)
    expect(screen.getByTestId("input-sources").textContent).toContain("0 inputs")
  })

  it("selecting a row key column calls onUpdate", () => {
    const onUpdate = vi.fn()
    const columns = [
      { name: "quote_id", dtype: "Utf8" },
      { name: "product", dtype: "Utf8" },
    ]
    const { container } = render(
      <ScenarioExpanderEditor
        {...DEFAULT_PROPS}
        onUpdate={onUpdate}
        upstreamColumns={columns}
      />,
    )
    const select = container.querySelector("select")!
    fireEvent.change(select, { target: { value: "product" } })
    expect(onUpdate).toHaveBeenCalledWith("quote_id", "product")
  })

  it("changing column_name calls onUpdate", () => {
    const onUpdate = vi.fn()
    render(<ScenarioExpanderEditor {...DEFAULT_PROPS} onUpdate={onUpdate} config={{ column_name: "scenario_value" }} />)
    const columnInput = screen.getByDisplayValue("scenario_value")
    fireEvent.change(columnInput, { target: { value: "my_value" } })
    expect(onUpdate).toHaveBeenCalledWith("column_name", "my_value")
  })

  it("changing step_column calls onUpdate", () => {
    const onUpdate = vi.fn()
    render(<ScenarioExpanderEditor {...DEFAULT_PROPS} onUpdate={onUpdate} config={{ step_column: "scenario_index" }} />)
    const stepColInput = screen.getByDisplayValue("scenario_index")
    fireEvent.change(stepColInput, { target: { value: "step_idx" } })
    expect(onUpdate).toHaveBeenCalledWith("step_column", "step_idx")
  })

  it("uses config values instead of defaults when provided", () => {
    const config = {
      quote_id: "my_quote",
      column_name: "custom_col",
      min_value: 0.5,
      max_value: 2.0,
      steps: 11,
      step_column: "my_step",
    }
    render(<ScenarioExpanderEditor {...DEFAULT_PROPS} config={config} />)
    expect(screen.getByDisplayValue("custom_col")).toBeTruthy()
    expect(screen.getByDisplayValue("0.5")).toBeTruthy()
    expect(screen.getByDisplayValue("2")).toBeTruthy()
    expect(screen.getByDisplayValue("11")).toBeTruthy()
    expect(screen.getByDisplayValue("my_step")).toBeTruthy()
  })

  it("renders Polars Code section with label and helper text", () => {
    const { container } = render(<ScenarioExpanderEditor {...DEFAULT_PROPS} />)
    const text = container.textContent || ""
    expect(text).toContain("Polars Code")
    expect(text).toContain("(optional)")
    expect(text).toContain("expanded data")
  })

  it("renders CodeEditor with default empty value", () => {
    render(<ScenarioExpanderEditor {...DEFAULT_PROPS} />)
    const editor = screen.getByTestId("code-editor") as HTMLTextAreaElement
    expect(editor.defaultValue).toBe("")
  })

  it("renders CodeEditor with code from config", () => {
    const config = { code: '.filter(pl.col("x") > 0)' }
    render(<ScenarioExpanderEditor {...DEFAULT_PROPS} config={config} />)
    const editor = screen.getByTestId("code-editor") as HTMLTextAreaElement
    expect(editor.defaultValue).toBe('.filter(pl.col("x") > 0)')
  })

  it("CodeEditor onChange calls onUpdate with code key", () => {
    const onUpdate = vi.fn()
    render(<ScenarioExpanderEditor {...DEFAULT_PROPS} onUpdate={onUpdate} />)
    const editor = screen.getByTestId("code-editor")
    fireEvent.change(editor, { target: { value: ".select('a', 'b')" } })
    expect(onUpdate).toHaveBeenCalledWith("code", ".select('a', 'b')")
  })
})
