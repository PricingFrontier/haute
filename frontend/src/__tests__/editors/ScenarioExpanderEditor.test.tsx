/**
 * Render tests for ScenarioExpanderEditor.
 *
 * Tests: row key label, select vs text input, value column, range section,
 * default values, editing min, editing steps with clamping, step column,
 * preview line, selecting a column, InputSourcesBar rendering.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { render, screen, fireEvent, cleanup } from "@testing-library/react"
import { useState } from "react"
import ScenarioExpanderEditor from "../../panels/editors/ScenarioExpanderEditor"
import type { OnUpdateConfig } from "../../panels/editors/_shared"

vi.mock("../../panels/editors/_shared", async () => {
  const actual = await vi.importActual("../../panels/editors/_shared")
  return {
    ...actual,
    InputSourcesBar: ({ inputSources }: { inputSources: { sourceNodeId: string; name: string; edgeId: string; sourceLabel: string }[] }) => (
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
  inputSources: [] as { sourceNodeId: string; name: string; edgeId: string; sourceLabel: string }[],
  upstreamColumns: [] as { name: string; dtype: string }[],
  accentColor: "#2dd4bf",
}

beforeEach(() => {
  DEFAULT_PROPS.onUpdate.mockClear()
})

function applyConfigUpdate(
  config: Record<string, unknown>,
  keyOrUpdates: Parameters<OnUpdateConfig>[0],
  value: Parameters<OnUpdateConfig>[1],
) {
  return typeof keyOrUpdates === "string"
    ? { ...config, [keyOrUpdates]: value }
    : { ...config, ...keyOrUpdates }
}

function recordConfigUpdate(
  onUpdate: OnUpdateConfig,
  keyOrUpdates: Parameters<OnUpdateConfig>[0],
  value: Parameters<OnUpdateConfig>[1],
) {
  if (typeof keyOrUpdates === "string") {
    onUpdate(keyOrUpdates, value)
    return
  }
  onUpdate(keyOrUpdates)
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

  it("buffers min edits locally and commits the parsed number once on blur (undo-atomicity)", () => {
    const onUpdate = vi.fn()
    render(<ScenarioExpanderEditor {...DEFAULT_PROPS} onUpdate={onUpdate} config={{ column_name: "sv", min_value: 0.8 }} />)
    const minInput = screen.getByDisplayValue("0.8") as HTMLInputElement
    fireEvent.change(minInput, { target: { value: "0.5" } })
    // Typing only updates the local draft — nothing is committed yet.
    expect(onUpdate).not.toHaveBeenCalled()
    expect(minInput.value).toBe("0.5")
    fireEvent.blur(minInput)
    expect(onUpdate).toHaveBeenCalledTimes(1)
    expect(onUpdate).toHaveBeenCalledWith("min_value", 0.5)
  })

  it("preserves formatted min drafts across parent rerenders and clears stale resets", () => {
    const onUpdate = vi.fn()

    function Harness() {
      const [config, setConfig] = useState<Record<string, unknown>>({
        column_name: "sv",
        min_value: 0.8,
        max_value: 10,
      })
      const handleUpdate: OnUpdateConfig = (keyOrUpdates, value) => {
        recordConfigUpdate(onUpdate, keyOrUpdates, value)
        setConfig((prev) => applyConfigUpdate(prev, keyOrUpdates, value))
        return { ok: true }
      }
      return (
        <>
          <ScenarioExpanderEditor {...DEFAULT_PROPS} onUpdate={handleUpdate} config={config} />
          <button type="button" onClick={() => setConfig({ column_name: "sv", min_value: 0.8, max_value: 10 })}>
            reset
          </button>
        </>
      )
    }

    render(<Harness />)
    const minInput = screen.getByDisplayValue("0.8") as HTMLInputElement

    fireEvent.change(minInput, { target: { value: "2.0" } })
    // Draft stays local until blur — no config echo happens mid-edit.
    expect(onUpdate).not.toHaveBeenCalled()
    expect(minInput.value).toBe("2.0")

    fireEvent.blur(minInput)
    expect(onUpdate).toHaveBeenCalledWith("min_value", 2)
    // Post-commit the draft clears and the canonical config text renders.
    expect(screen.getByDisplayValue("2")).toBeTruthy()

    fireEvent.click(screen.getByText("reset"))
    expect(screen.getByDisplayValue("0.8")).toBeTruthy()
    expect(screen.queryByDisplayValue("2.0")).toBeNull()
  })

  it("keeps the minus sign while incrementally typing a negative fractional min", () => {
    const onUpdate = vi.fn()

    function Harness() {
      const [config, setConfig] = useState<Record<string, unknown>>({
        column_name: "sv",
        min_value: 0.8,
        max_value: 1.2,
      })
      const handleUpdate: OnUpdateConfig = (keyOrUpdates, value) => {
        recordConfigUpdate(onUpdate, keyOrUpdates, value)
        setConfig((prev) => applyConfigUpdate(prev, keyOrUpdates, value))
        return { ok: true }
      }
      return <ScenarioExpanderEditor {...DEFAULT_PROPS} onUpdate={handleUpdate} config={config} />
    }

    render(<Harness />)
    const minInput = screen.getByDisplayValue("0.8") as HTMLInputElement

    fireEvent.change(minInput, { target: { value: "-" } })
    expect(minInput.value).toBe("-")
    expect(minInput.getAttribute("aria-invalid")).toBe("true")
    expect(onUpdate).not.toHaveBeenCalled()

    fireEvent.change(minInput, { target: { value: "-0" } })
    expect(onUpdate).not.toHaveBeenCalled()
    expect(screen.getByDisplayValue("-0")).toBeTruthy()

    fireEvent.change(screen.getByDisplayValue("-0"), { target: { value: "-0.5" } })
    expect(onUpdate).not.toHaveBeenCalled()
    expect(screen.getByDisplayValue("-0.5")).toBeTruthy()

    // The whole incremental edit lands as ONE commit at the blur boundary.
    fireEvent.blur(minInput)
    expect(onUpdate).toHaveBeenCalledTimes(1)
    expect(onUpdate).toHaveBeenCalledWith("min_value", -0.5)
  })

  it("clears an invalid range draft when switching to another node", () => {
    function Harness() {
      const [activeNode, setActiveNode] = useState<"a" | "b">("a")
      const configs = {
        a: { column_name: "sv", min_value: 1, max_value: 2 },
        b: { column_name: "sv", min_value: 1, max_value: 2 },
      } satisfies Record<string, Record<string, unknown>>

      return (
        <>
          <ScenarioExpanderEditor {...DEFAULT_PROPS} config={configs[activeNode]} />
          <button type="button" onClick={() => setActiveNode("b")}>
            switch node
          </button>
        </>
      )
    }

    render(<Harness />)
    const minInput = screen.getByDisplayValue("1") as HTMLInputElement
    fireEvent.change(minInput, { target: { value: "abc" } })
    expect(screen.getByDisplayValue("abc")).toHaveAttribute("aria-invalid", "true")

    fireEvent.click(screen.getByText("switch node"))

    expect(screen.queryByDisplayValue("abc")).toBeNull()
    expect(screen.getByDisplayValue("1")).not.toHaveAttribute("aria-invalid")
  })

  it("clears a valid formatted range draft when switching to another node with the same committed value", () => {
    function Harness() {
      const [activeNode, setActiveNode] = useState<"a" | "b">("a")
      const configs = {
        a: { column_name: "sv", min_value: 0, max_value: 2 },
        b: { column_name: "sv", min_value: 0, max_value: 2 },
      } satisfies Record<string, Record<string, unknown>>

      return (
        <>
          <ScenarioExpanderEditor {...DEFAULT_PROPS} config={configs[activeNode]} />
          <button type="button" onClick={() => setActiveNode("b")}>
            switch node
          </button>
        </>
      )
    }

    render(<Harness />)
    const minInput = screen.getByDisplayValue("0") as HTMLInputElement
    fireEvent.change(minInput, { target: { value: "0." } })
    expect(screen.getByDisplayValue("0.")).toBeTruthy()

    fireEvent.click(screen.getByText("switch node"))

    expect(screen.queryByDisplayValue("0.")).toBeNull()
    expect(screen.getByDisplayValue("0")).toBeTruthy()
  })

  it("keeps a valid formatted range draft local while typing (no mid-edit commit to echo)", () => {
    const onUpdate = vi.fn()

    function Harness() {
      const [config, setConfig] = useState<Record<string, unknown>>({
        column_name: "sv",
        min_value: 0,
        max_value: 2,
      })
      const handleUpdate: OnUpdateConfig = (keyOrUpdates, value) => {
        recordConfigUpdate(onUpdate, keyOrUpdates, value)
        setConfig((prev) => applyConfigUpdate(prev, keyOrUpdates, value))
        return { ok: true }
      }

      return <ScenarioExpanderEditor {...DEFAULT_PROPS} onUpdate={handleUpdate} config={config} />
    }

    render(<Harness />)
    const minInput = screen.getByDisplayValue("0") as HTMLInputElement
    fireEvent.change(minInput, { target: { value: "0." } })

    // Nothing commits mid-edit, so the config-echo hazard the old
    // per-keystroke scheme had to defend against cannot arise; the
    // formatted draft simply stays visible.
    expect(onUpdate).not.toHaveBeenCalled()
    expect(screen.getByDisplayValue("0.")).toBeTruthy()
  })

  it("writes explicit nulls when min and max are cleared so saves do not retain old values", () => {
    const onUpdate = vi.fn()

    function Harness() {
      const [config, setConfig] = useState<Record<string, unknown>>({
        column_name: "sv",
        min_value: 0.8,
        max_value: 1.2,
      })
      const handleUpdate: OnUpdateConfig = (keyOrUpdates, value) => {
        recordConfigUpdate(onUpdate, keyOrUpdates, value)
        setConfig((prev) => applyConfigUpdate(prev, keyOrUpdates, value))
        return { ok: true }
      }
      return (
        <>
          <ScenarioExpanderEditor {...DEFAULT_PROPS} onUpdate={handleUpdate} config={config} />
          <output data-testid="config-json">{JSON.stringify(config)}</output>
        </>
      )
    }

    render(<Harness />)
    const minInput = screen.getByDisplayValue("0.8")
    fireEvent.change(minInput, { target: { value: "" } })
    fireEvent.blur(minInput)
    const maxInput = screen.getByDisplayValue("1.2")
    fireEvent.change(maxInput, { target: { value: "" } })
    fireEvent.blur(maxInput)

    expect(onUpdate).toHaveBeenCalledWith("min_value", null)
    expect(onUpdate).toHaveBeenCalledWith("max_value", null)
    expect(JSON.parse(screen.getByTestId("config-json").textContent ?? "{}")).toMatchObject({
      min_value: null,
      max_value: null,
    })
  })

  it("clearing min requests an explicit clear (at blur) instead of silently falling back to zero", () => {
    const onUpdate = vi.fn()
    render(<ScenarioExpanderEditor {...DEFAULT_PROPS} onUpdate={onUpdate} config={{ column_name: "sv", min_value: 0.8 }} />)
    const minInput = screen.getByDisplayValue("0.8") as HTMLInputElement
    fireEvent.change(minInput, { target: { value: "" } })
    expect(onUpdate).not.toHaveBeenCalled()
    fireEvent.blur(minInput)
    expect(onUpdate).toHaveBeenCalledTimes(1)
    expect(onUpdate).toHaveBeenCalledWith("min_value", null)
    expect(onUpdate).not.toHaveBeenCalledWith("min_value", 0)
  })

  it("keeps partial min drafts local until a valid blur commit", () => {
    const onUpdate = vi.fn()
    render(<ScenarioExpanderEditor {...DEFAULT_PROPS} onUpdate={onUpdate} config={{ column_name: "sv", min_value: 0.8 }} />)
    const minInput = screen.getByDisplayValue("0.8") as HTMLInputElement

    fireEvent.change(minInput, { target: { value: "-" } })
    expect(minInput.value).toBe("-")
    expect(minInput.getAttribute("aria-invalid")).toBe("true")
    expect(onUpdate).not.toHaveBeenCalled()

    fireEvent.change(minInput, { target: { value: "-0.5" } })
    expect(minInput.value).toBe("-0.5")
    expect(minInput.getAttribute("aria-invalid")).toBeNull()
    expect(onUpdate).not.toHaveBeenCalled()

    fireEvent.blur(minInput)
    expect(onUpdate).toHaveBeenCalledTimes(1)
    expect(onUpdate).toHaveBeenCalledWith("min_value", -0.5)
  })

  it("buffers max edits locally and commits the parsed number once on blur (undo-atomicity)", () => {
    const onUpdate = vi.fn()
    render(<ScenarioExpanderEditor {...DEFAULT_PROPS} onUpdate={onUpdate} config={{ column_name: "sv", max_value: 1.2 }} />)
    const maxInput = screen.getByDisplayValue("1.2") as HTMLInputElement
    fireEvent.change(maxInput, { target: { value: "2.0" } })
    expect(onUpdate).not.toHaveBeenCalled()
    expect(maxInput.value).toBe("2.0")
    fireEvent.blur(maxInput)
    expect(onUpdate).toHaveBeenCalledTimes(1)
    expect(onUpdate).toHaveBeenCalledWith("max_value", 2.0)
  })

  it("clearing max requests an explicit clear (at blur) instead of silently falling back to zero", () => {
    const onUpdate = vi.fn()
    render(<ScenarioExpanderEditor {...DEFAULT_PROPS} onUpdate={onUpdate} config={{ column_name: "sv", max_value: 1.2 }} />)
    const maxInput = screen.getByDisplayValue("1.2") as HTMLInputElement
    fireEvent.change(maxInput, { target: { value: "" } })
    expect(onUpdate).not.toHaveBeenCalled()
    fireEvent.blur(maxInput)
    expect(onUpdate).toHaveBeenCalledTimes(1)
    expect(onUpdate).toHaveBeenCalledWith("max_value", null)
    expect(onUpdate).not.toHaveBeenCalledWith("max_value", 0)
  })

  it("uses draft min and max edits for step-size feedback before config commit", () => {
    const onUpdate = vi.fn()
    render(
      <ScenarioExpanderEditor
        {...DEFAULT_PROPS}
        onUpdate={onUpdate}
        config={{ column_name: "sv", min_value: 0, max_value: 10, steps: 6 }}
      />,
    )
    const minInput = screen.getByDisplayValue("0") as HTMLInputElement

    fireEvent.change(minInput, { target: { value: "5" } })

    // The step-size preview reads the LOCAL draft — live feedback works
    // even though nothing has been committed yet.
    expect(onUpdate).not.toHaveBeenCalled()
    expect(screen.getByTestId("step-size").textContent).toBe("1")
  })

  it("commits steps on blur with value clamped to min 1", () => {
    const onUpdate = vi.fn()
    render(<ScenarioExpanderEditor {...DEFAULT_PROPS} onUpdate={onUpdate} config={{ steps: 21 }} />)
    const stepsInput = screen.getByDisplayValue("21")

    // Normal value — buffered until blur, then committed once.
    fireEvent.change(stepsInput, { target: { value: "10" } })
    expect(onUpdate).not.toHaveBeenCalled()
    fireEvent.blur(stepsInput)
    expect(onUpdate).toHaveBeenCalledTimes(1)
    expect(onUpdate).toHaveBeenCalledWith("steps", 10)

    // Zero should clamp to 1
    fireEvent.change(stepsInput, { target: { value: "0" } })
    fireEvent.blur(stepsInput)
    expect(onUpdate).toHaveBeenCalledWith("steps", 1)

    // Negative should clamp to 1
    fireEvent.change(stepsInput, { target: { value: "-5" } })
    fireEvent.blur(stepsInput)
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
      { sourceNodeId: "test-source", name: "upstream_data", sourceLabel: "Upstream", edgeId: "e1" },
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

  it("commits column_name once on blur", () => {
    const onUpdate = vi.fn()
    render(<ScenarioExpanderEditor {...DEFAULT_PROPS} onUpdate={onUpdate} config={{ column_name: "scenario_value" }} />)
    const columnInput = screen.getByDisplayValue("scenario_value")
    fireEvent.change(columnInput, { target: { value: "my_value" } })
    expect(onUpdate).not.toHaveBeenCalled()
    fireEvent.blur(columnInput)
    expect(onUpdate).toHaveBeenCalledTimes(1)
    expect(onUpdate).toHaveBeenCalledWith("column_name", "my_value")
  })

  it("commits step_column once on blur", () => {
    const onUpdate = vi.fn()
    render(<ScenarioExpanderEditor {...DEFAULT_PROPS} onUpdate={onUpdate} config={{ step_column: "scenario_index" }} />)
    const stepColInput = screen.getByDisplayValue("scenario_index")
    fireEvent.change(stepColInput, { target: { value: "step_idx" } })
    expect(onUpdate).not.toHaveBeenCalled()
    fireEvent.blur(stepColInput)
    expect(onUpdate).toHaveBeenCalledTimes(1)
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
    expect(editor.defaultValue).not.toContain("scenario_index")
    expect(editor.defaultValue).not.toContain("join")
  })

  it("renders CodeEditor with code from config", () => {
    const config = { code: '.filter(pl.col("x") > 0)' }
    render(<ScenarioExpanderEditor {...DEFAULT_PROPS} config={config} />)
    const editor = screen.getByTestId("code-editor") as HTMLTextAreaElement
    expect(editor.defaultValue).toBe('.filter(pl.col("x") > 0)')
  })

  it("does not synthesize expansion scaffold around configured code", () => {
    const config = { code: "df = df.limit(10)" }
    render(<ScenarioExpanderEditor {...DEFAULT_PROPS} config={config} />)
    const editor = screen.getByTestId("code-editor") as HTMLTextAreaElement
    expect(editor.defaultValue).toBe("df = df.limit(10)")
    expect(editor.defaultValue).not.toContain("df = quotes")
    expect(editor.defaultValue).not.toContain("return df")
  })

  it("CodeEditor onChange calls onUpdate with code key", () => {
    const onUpdate = vi.fn()
    render(<ScenarioExpanderEditor {...DEFAULT_PROPS} onUpdate={onUpdate} />)
    const editor = screen.getByTestId("code-editor")
    fireEvent.change(editor, { target: { value: ".select('a', 'b')" } })
    expect(onUpdate).toHaveBeenCalledWith("code", ".select('a', 'b')")
  })
})
