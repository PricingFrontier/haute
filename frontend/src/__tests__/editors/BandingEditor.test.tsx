/**
 * Render tests for BandingEditor.
 *
 * Tests: renders with default config, the factor list, adding/removing factors,
 * type toggle, column selection with auto-type detection, add rule button,
 * no cross-factor summary, breakpoints mode, stash-and-restore, accessibility,
 * match counts, validation warnings, histogram, categorical value picker, and
 * Numeric bands on Date and Datetime columns.
 */
import { describe, it, expect, vi, afterEach } from "vitest"
import { render, screen, fireEvent, cleanup, within } from "@testing-library/react"
import type { ReactElement } from "react"
import BandingEditor from "../../panels/editors/BandingEditor"
import { GraphProvider } from "../../panels/GraphContext"

/**
 * The editor reads the data its node is banding, so it needs the graph the
 * node lives in. These tests render it without a node id, so it asks for
 * nothing and works from the preview rows they pass.
 */
function renderEditor(ui: ReactElement) {
  return render(
    <GraphProvider allNodes={[]} edges={[]} submodels={{}} preamble="">
      {ui}
    </GraphProvider>,
  )
}

afterEach(cleanup)

/** How the histogram's formatter, if the editor gives it one, shows the data's ends. */
function endLabels(props: Record<string, unknown>): string[] | null {
  const bins = props.bins as { lower: number; upper: number }[]
  const format = props.formatValue as ((value: number) => string) | undefined
  if (!format || !bins.length) return null
  return [format(bins[0].lower), format(bins[bins.length - 1].upper)]
}

// Mock child components that we don't need to test internals of
vi.mock("../../panels/editors/banding/BreakpointGrid", () => ({
  BreakpointGrid: (props: Record<string, unknown>) => (
    <div
      data-testid="breakpoint-grid"
      data-breakpoints={JSON.stringify(props.breakpoints)}
      data-match-counts={JSON.stringify(props.matchCounts ?? null)}
      data-temporal={String(props.temporal ?? false)}
    />
  ),
}))

vi.mock("../../panels/editors/banding/BandingHistogram", () => ({
  BandingHistogram: (props: Record<string, unknown>) => (
    <div
      data-testid="banding-histogram"
      data-bins={JSON.stringify(props.bins)}
      data-boundaries={JSON.stringify(props.boundaries)}
      data-end-labels={JSON.stringify(endLabels(props))}
    />
  ),
}))

vi.mock("../../panels/editors/banding/GenerateBandsDialog", () => ({
  GenerateBandsDialog: (props: Record<string, unknown>) => (
    <div
      data-testid="generate-bands-dialog"
      data-initial={JSON.stringify(props.initial ?? null)}
      data-temporal={String(props.temporal ?? false)}
      data-min={JSON.stringify(props.dataMin ?? null)}
      data-max={JSON.stringify(props.dataMax ?? null)}
    >
      <button onClick={props.onClose as () => void}>Cancel</button>
    </div>
  ),
}))

vi.mock("../../panels/editors/banding/CategoricalValuePicker", () => ({
  CategoricalValuePicker: (props: Record<string, unknown>) => (
    <div data-testid="categorical-value-picker" data-values={JSON.stringify(props.availableValues)} />
  ),
}))

/** The factor list: one row per factor, named by its output column. */
function columnList() {
  return screen.getByRole("group", { name: "Banding columns" })
}

/** A factor's row button: its name, then whether it is complete. */
function rowOf(name: string) {
  return within(columnList()).getByRole("button", { name: new RegExp(`^${name} (complete|incomplete)$`) })
}

describe("BandingEditor", () => {
  // ─── Existing tests (updated for terminology changes) ─────────

  it("lists each factor by its output column", () => {
    const config = {
      factors: [
        { banding: "breakpoints", column: "age", outputColumn: "age_band", rules: [] },
        { banding: "categorical", column: "region", outputColumn: "region_group", rules: [] },
      ],
    }
    renderEditor(
      <BandingEditor config={config} onUpdate={vi.fn()} inputSources={[]} accentColor="#22d3ee" />,
    )
    expect(rowOf("age_band")).toHaveAttribute("aria-pressed", "true")
    expect(rowOf("region_group")).toHaveAttribute("aria-pressed", "false")
  })

  it("names a factor by its input column while its output column is empty", () => {
    const config = {
      factors: [
        { banding: "breakpoints", column: "driver_age", outputColumn: "", rules: [] },
      ],
    }
    renderEditor(
      <BandingEditor config={config} onUpdate={vi.fn()} inputSources={[]} accentColor="#22d3ee" />,
    )
    expect(rowOf("driver_age")).toBeInTheDocument()
  })

  it("shows a placeholder 'Column 1' row on a new node, so the first column does not move the layout", () => {
    renderEditor(
      <BandingEditor config={{}} onUpdate={vi.fn()} inputSources={[]} accentColor="#22d3ee" />,
    )
    expect(rowOf("Column 1")).toHaveAttribute("aria-pressed", "true")
    expect(rowOf("Column 1")).toHaveAccessibleName("Column 1 incomplete")
  })

  it("adding a factor appends one and selects it", () => {
    const onUpdate = vi.fn()
    const config = {
      factors: [{ banding: "breakpoints", column: "age", outputColumn: "age_band", rules: [] }],
    }
    const { rerender } = renderEditor(
      <BandingEditor config={config} onUpdate={onUpdate} inputSources={[]} accentColor="#22d3ee" />,
    )
    fireEvent.click(screen.getByRole("button", { name: "Add column" }))

    const factors = onUpdate.mock.calls[0][1]
    expect(factors).toHaveLength(2)
    expect(factors[1]).toMatchObject({ column: "", outputColumn: "" })
    rerender(
      <GraphProvider allNodes={[]} edges={[]} submodels={{}} preamble="">
        <BandingEditor config={{ factors }} onUpdate={onUpdate} inputSources={[]} accentColor="#22d3ee" />
      </GraphProvider>,
    )
    expect(rowOf("Column 2")).toHaveAttribute("aria-pressed", "true")
  })

  it("adds a new factor as Numeric, whatever type the others are", () => {
    const onUpdate = vi.fn()
    const config = {
      factors: [{ banding: "categorical", column: "region", outputColumn: "region_group", rules: [] }],
    }
    const { rerender } = renderEditor(
      <BandingEditor config={config} onUpdate={onUpdate} inputSources={[]} accentColor="#22d3ee" />,
    )
    fireEvent.click(screen.getByRole("button", { name: "Add column" }))

    const factors = onUpdate.mock.calls[0][1]
    expect(factors[1]).toEqual({ banding: "breakpoints", column: "", outputColumn: "", rules: [], default: null })
    rerender(
      <GraphProvider allNodes={[]} edges={[]} submodels={{}} preamble="">
        <BandingEditor config={{ factors }} onUpdate={onUpdate} inputSources={[]} accentColor="#22d3ee" />
      </GraphProvider>,
    )
    expect(screen.getByRole("radio", { name: "Numeric" })).toHaveAttribute("aria-checked", "true")
    expect(screen.getByText("No breakpoints yet.")).toBeInTheDocument()
  })

  it("removing a factor when >1 factors removes its row", () => {
    const onUpdate = vi.fn()
    const config = {
      factors: [
        { banding: "breakpoints", column: "age", outputColumn: "age_band", rules: [] },
        { banding: "categorical", column: "region", outputColumn: "region_group", rules: [] },
      ],
    }
    renderEditor(
      <BandingEditor config={config} onUpdate={onUpdate} inputSources={[]} accentColor="#22d3ee" />,
    )

    fireEvent.click(within(columnList()).getByRole("button", { name: "Remove age_band column" }))

    expect(onUpdate).toHaveBeenCalledWith("factors", [
      expect.objectContaining({ column: "region", outputColumn: "region_group" }),
    ])
  })

  it("cannot remove last factor (single factor)", () => {
    const config = {
      factors: [
        { banding: "breakpoints", column: "age", outputColumn: "age_band", rules: [] },
      ],
    }
    renderEditor(
      <BandingEditor config={config} onUpdate={vi.fn()} inputSources={[]} accentColor="#22d3ee" />,
    )
    expect(within(columnList()).queryByRole("button", { name: /^Remove / })).toBeNull()
  })

  it("type toggle from Numeric to Categorical calls updateFactor", () => {
    const onUpdate = vi.fn()
    const config = {
      factors: [{ banding: "breakpoints", column: "age", outputColumn: "age_band", rules: [] }],
    }
    renderEditor(
      <BandingEditor config={config} onUpdate={onUpdate} inputSources={[]} accentColor="#22d3ee" />,
    )
    fireEvent.click(screen.getByText("Categorical"))
    expect(onUpdate).toHaveBeenCalled()
    const call = onUpdate.mock.calls.find(
      (c: unknown[]) => (c[1] as Record<string, unknown>[])?.[0]?.banding === "categorical"
    )
    expect(call).toBeTruthy()
  })

  it("shows the Type row on a new node with Numeric chosen", () => {
    renderEditor(
      <BandingEditor config={{}} onUpdate={vi.fn()} inputSources={[]} accentColor="#22d3ee" />,
    )
    expect(screen.getByRole("radio", { name: "Numeric" })).toHaveAttribute("aria-checked", "true")
    expect(screen.getByRole("radio", { name: "Categorical" })).toHaveAttribute("aria-checked", "false")
  })

  it("column selection with upstream columns renders dropdown", () => {
    const columns = [
      { name: "age", dtype: "int64" },
      { name: "region", dtype: "Utf8" },
    ]
    renderEditor(
      <BandingEditor
        config={{}}
        onUpdate={vi.fn()}
        inputSources={[]}
        upstreamColumns={columns}
        accentColor="#22d3ee"
      />,
    )
    const selects = screen.getAllByRole("combobox")
    const colSelect = selects.find(s => {
      const opts = Array.from((s as HTMLSelectElement).options)
      return opts.some(o => o.textContent?.includes("age"))
    })
    expect(colSelect).toBeTruthy()
  })

  it("column selection auto-detects type for numeric dtype -> breakpoints", () => {
    const onUpdate = vi.fn()
    const columns = [
      { name: "age", dtype: "int64" },
      { name: "region", dtype: "Utf8" },
    ]
    const config = {
      factors: [{ banding: "categorical", column: "", outputColumn: "", rules: [] }],
    }
    renderEditor(
      <BandingEditor
        config={config}
        onUpdate={onUpdate}
        inputSources={[]}
        upstreamColumns={columns}
        accentColor="#22d3ee"
      />,
    )
    const selects = screen.getAllByRole("combobox")
    const colSelect = selects.find(s => {
      const opts = Array.from((s as HTMLSelectElement).options)
      return opts.some(o => o.textContent?.includes("age"))
    })!
    fireEvent.change(colSelect, { target: { value: "age" } })

    // Should auto-detect to breakpoints for int64 dtype
    expect(onUpdate).toHaveBeenCalledWith("factors", expect.arrayContaining([
      expect.objectContaining({ column: "age", banding: "breakpoints" }),
    ]))
  })

  it("column selection switches a new Numeric factor to categorical for a string dtype", () => {
    const onUpdate = vi.fn()
    const columns = [
      { name: "age", dtype: "int64" },
      { name: "region", dtype: "Utf8" },
    ]
    const config = {
      factors: [{ banding: "breakpoints", column: "", outputColumn: "", rules: [] }],
    }
    renderEditor(
      <BandingEditor
        config={config}
        onUpdate={onUpdate}
        inputSources={[]}
        upstreamColumns={columns}
        accentColor="#22d3ee"
      />,
    )
    const selects = screen.getAllByRole("combobox")
    const colSelect = selects.find(s => {
      const opts = Array.from((s as HTMLSelectElement).options)
      return opts.some(o => o.textContent?.includes("region"))
    })!
    fireEvent.change(colSelect, { target: { value: "region" } })

    expect(onUpdate).toHaveBeenCalledWith("factors", expect.arrayContaining([
      expect.objectContaining({ column: "region", banding: "categorical" }),
    ]))
  })

  it("add rule button appends an empty rule after the existing categorical rules", () => {
    const onUpdate = vi.fn()
    const existing = { value: "London", assignment: "South" }
    const config = {
      factors: [{ banding: "categorical", column: "region", outputColumn: "region_group", rules: [existing] }],
    }
    renderEditor(
      <BandingEditor config={config} onUpdate={onUpdate} inputSources={[]} accentColor="#22d3ee" />,
    )
    fireEvent.click(screen.getByText("Add"))
    expect(onUpdate).toHaveBeenCalledWith("factors", [
      expect.objectContaining({
        banding: "categorical",
        rules: [existing, { value: "", assignment: "" }],
      }),
    ])
  })

  it("add rule button adds appropriate empty rule type for categorical", () => {
    const onUpdate = vi.fn()
    const config = {
      factors: [{ banding: "categorical", column: "region", outputColumn: "region_group", rules: [] }],
    }
    renderEditor(
      <BandingEditor config={config} onUpdate={onUpdate} inputSources={[]} accentColor="#22d3ee" />,
    )
    fireEvent.click(screen.getByText("Add"))
    expect(onUpdate).toHaveBeenCalledWith("factors", expect.arrayContaining([
      expect.objectContaining({
        banding: "categorical",
        rules: [expect.objectContaining({ value: "", assignment: "" })],
      }),
    ]))
  })

  it("does not list every factor in a summary below the default when 2+ factors", () => {
    const config = {
      factors: [
        { banding: "breakpoints", column: "age", outputColumn: "age_band", rules: [{ boundary: "25", label: "young" }] },
        { banding: "categorical", column: "region", outputColumn: "region_group", rules: [{ value: "London", assignment: "South" }] },
      ],
    }
    renderEditor(
      <BandingEditor config={config} onUpdate={vi.fn()} inputSources={[]} accentColor="#22d3ee" />,
    )
    expect(screen.queryByTestId("banding-summary")).toBeNull()
    // Each factor's rule count is a badge on its own row in the list, and
    // appears nowhere else.
    const counts = screen.getAllByText("1 rule")
    expect(counts).toHaveLength(2)
    expect(counts.every((badge) => columnList().contains(badge))).toBe(true)
  })

  it("renders text input for column when no upstreamColumns", () => {
    renderEditor(
      <BandingEditor config={{}} onUpdate={vi.fn()} inputSources={[]} accentColor="#22d3ee" />,
    )
    // Text input for column should be present (no placeholder)
    const inputs = screen.getAllByRole("textbox")
    expect(inputs.length).toBeGreaterThan(0)
  })

  it("renders default value input", () => {
    renderEditor(
      <BandingEditor config={{}} onUpdate={vi.fn()} inputSources={[]} accentColor="#22d3ee" />,
    )
    expect(screen.getByText(/Default/)).toBeTruthy()
  })

  it("offers a new node's breakpoints empty state rather than a rules table", () => {
    renderEditor(
      <BandingEditor config={{}} onUpdate={vi.fn()} inputSources={[]} accentColor="#22d3ee" />,
    )
    expect(screen.getByText("No breakpoints yet.")).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Generate even bands" })).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Add manually" })).toBeInTheDocument()
    expect(screen.queryByText("No rules yet")).toBeNull()
    expect(screen.queryByRole("button", { name: /^Add$/ })).toBeNull()
  })

  it("renders categorical rules grid headers when categorical rules exist", () => {
    const config = {
      factors: [{
        banding: "categorical",
        column: "region",
        outputColumn: "region_group",
        rules: [{ value: "London", assignment: "South" }],
      }],
    }
    renderEditor(
      <BandingEditor config={config} onUpdate={vi.fn()} inputSources={[]} accentColor="#22d3ee" />,
    )
    expect(screen.getByText("Maps To")).toBeTruthy()
  })

  it("clicking a factor row switches the active factor", () => {
    const config = {
      factors: [
        { banding: "breakpoints", column: "age", outputColumn: "age_band", rules: [] },
        { banding: "categorical", column: "region", outputColumn: "region_group", rules: [] },
      ],
    }
    renderEditor(
      <BandingEditor config={config} onUpdate={vi.fn()} inputSources={[]} accentColor="#22d3ee" />,
    )
    expect(screen.getByRole("radio", { name: "Numeric" })).toHaveAttribute("aria-checked", "true")
    fireEvent.click(rowOf("region_group"))
    expect(screen.getByRole("radio", { name: "Categorical" })).toHaveAttribute("aria-checked", "true")
    expect(screen.getByLabelText("Output Column")).toHaveValue("region_group")
  })

  describe("factor list", () => {
    const factor = (column: string, outputColumn: string) => ({
      banding: "categorical", column, outputColumn, rules: [{ value: "a", assignment: "A" }],
    })
    const THREE = {
      factors: [factor("age", "first"), factor("region", "second"), factor("policy_cover_type", "cover_band")],
    }
    const outputs = (onUpdate: ReturnType<typeof vi.fn>) =>
      (onUpdate.mock.lastCall![1] as { outputColumn: string }[]).map((f) => f.outputColumn)

    it("searches by output or input column name, selecting what stays in view", () => {
      renderEditor(<BandingEditor config={THREE} onUpdate={vi.fn()} inputSources={[]} accentColor="#22d3ee" />)
      fireEvent.change(screen.getByRole("searchbox", { name: "Search banding columns" }), {
        target: { value: "policy" },
      })
      const rows = within(columnList()).getAllByRole("button", { name: /complete$/ })
      expect(rows.map((row) => row.getAttribute("aria-label"))).toEqual(["cover_band complete"])
      // The selected factor was filtered out, so the one in view is edited.
      expect(rowOf("cover_band")).toHaveAttribute("aria-pressed", "true")
      expect(screen.getByLabelText("Output Column")).toHaveValue("cover_band")
    })

    it("shows only incomplete factors under Issues", () => {
      const config = { factors: [factor("age", "first"), { ...factor("region", "second"), rules: [] }] }
      renderEditor(<BandingEditor config={config} onUpdate={vi.fn()} inputSources={[]} accentColor="#22d3ee" />)
      fireEvent.click(screen.getByRole("button", { name: "Issues 1" }))
      const rows = within(columnList()).getAllByRole("button", { name: /complete$/ })
      expect(rows.map((row) => row.getAttribute("aria-label"))).toEqual(["second incomplete"])
      expect(rows[0]).toHaveAttribute("title", "No rules yet")
    })

    it("reorders the factors when one is dragged onto another, keeping it selected", () => {
      const onUpdate = vi.fn()
      const { rerender } = renderEditor(
        <BandingEditor config={THREE} onUpdate={onUpdate} inputSources={[]} accentColor="#22d3ee" />,
      )
      const row = (name: string) => rowOf(name).parentElement as HTMLElement
      expect(row("first")).toHaveAttribute("draggable", "true")
      expect(within(row("first")).getByTitle("Drag to reorder")).toBeInTheDocument()
      const dataTransfer = { setData: vi.fn(), effectAllowed: "", dropEffect: "" }
      fireEvent.dragStart(row("first"), { dataTransfer })
      fireEvent.dragOver(row("cover_band"), { dataTransfer })
      fireEvent.drop(row("cover_band"), { dataTransfer })

      expect(outputs(onUpdate)).toEqual(["second", "cover_band", "first"])
      rerender(
        <GraphProvider allNodes={[]} edges={[]} submodels={{}} preamble="">
          <BandingEditor
            config={{ factors: onUpdate.mock.lastCall![1] }}
            onUpdate={onUpdate}
            inputSources={[]}
            accentColor="#22d3ee"
          />
        </GraphProvider>,
      )
      expect(rowOf("first")).toHaveAttribute("aria-pressed", "true")
    })

    it("moves a factor with Alt+Up/Down from the keyboard", () => {
      const onUpdate = vi.fn()
      renderEditor(<BandingEditor config={THREE} onUpdate={onUpdate} inputSources={[]} accentColor="#22d3ee" />)
      fireEvent.keyDown(rowOf("second"), { key: "ArrowDown", altKey: true })
      expect(outputs(onUpdate)).toEqual(["first", "cover_band", "second"])
      fireEvent.keyDown(rowOf("first"), { key: "ArrowUp", altKey: true })
      // Already first: nothing to move.
      expect(onUpdate).toHaveBeenCalledTimes(1)
    })
  })

  it("renders InputSourcesBar when inputs provided", () => {
    const inputSources = [{ sourceNodeId: "test-source", name: "data", sourceLabel: "Data", edgeId: "e1" }]
    renderEditor(
      <BandingEditor config={{}} onUpdate={vi.fn()} inputSources={inputSources} accentColor="#22d3ee" />,
    )
    expect(screen.getByText("data")).toBeTruthy()
  })

  // ─── Feature 1: Terminology cleanup ───────────────────────────

  it("does not show detected type hint label", () => {
    const config = {
      factors: [{ banding: "breakpoints", column: "age", outputColumn: "age_band", rules: [] }],
    }
    renderEditor(
      <BandingEditor
        config={config}
        onUpdate={vi.fn()}
        inputSources={[]}
        upstreamColumns={[{ name: "age", dtype: "Float64" }]}
        accentColor="#22d3ee"
      />,
    )
    expect(screen.queryByText("(detected: numeric)")).toBeNull()
    expect(screen.queryByText("(detected: text)")).toBeNull()
  })

  // ─── Feature 2: Numeric/Categorical type toggle ──────────────

  it("Numeric option appears in type toggle when factor is configured", () => {
    const config = {
      factors: [{ banding: "breakpoints", column: "age", outputColumn: "age_band", rules: [] }],
    }
    renderEditor(
      <BandingEditor config={config} onUpdate={vi.fn()} inputSources={[]} accentColor="#22d3ee" />,
    )
    expect(screen.getByText("Numeric")).toBeTruthy()
    expect(screen.getByText("Categorical")).toBeTruthy()
  })

  it("greys out Numeric for a text column and says why", () => {
    const config = {
      factors: [
        { banding: "categorical", column: "policy_cover_type", outputColumn: "cover_band", rules: [] },
      ],
    }
    const onUpdate = vi.fn()
    renderEditor(
      <BandingEditor
        config={config}
        onUpdate={onUpdate}
        inputSources={[]}
        upstreamColumns={[{ name: "policy_cover_type", dtype: "String" }]}
        accentColor="#22d3ee"
      />,
    )
    const numeric = screen.getByText("Numeric").closest("button")!
    expect(numeric).toBeDisabled()
    expect(numeric).toHaveAttribute(
      "title",
      "policy_cover_type is a String column; numeric bands need a number or date column.",
    )
    fireEvent.click(numeric)
    expect(onUpdate).not.toHaveBeenCalled()
    expect(screen.getByText("Categorical").closest("button")).toBeEnabled()
  })

  it.each([
    ["a numeric column", [{ name: "age", dtype: "Float64" }]],
    ["a column whose dtype is unknown", undefined],
  ])("keeps Numeric available for %s", (_case, upstreamColumns) => {
    const config = {
      factors: [{ banding: "categorical", column: "age", outputColumn: "age_band", rules: [] }],
    }
    renderEditor(
      <BandingEditor
        config={config}
        onUpdate={vi.fn()}
        inputSources={[]}
        upstreamColumns={upstreamColumns}
        accentColor="#22d3ee"
      />,
    )
    expect(screen.getByText("Numeric").closest("button")).toBeEnabled()
  })

  it("auto-detect selects 'breakpoints' for numeric columns", () => {
    const onUpdate = vi.fn()
    const columns = [{ name: "age", dtype: "int64" }]
    const config = {
      factors: [{ banding: "categorical", column: "", outputColumn: "", rules: [] }],
    }
    renderEditor(
      <BandingEditor config={config} onUpdate={onUpdate} inputSources={[]} upstreamColumns={columns} accentColor="#22d3ee" />,
    )
    const selects = screen.getAllByRole("combobox")
    const colSelect = selects.find(s => {
      const opts = Array.from((s as HTMLSelectElement).options)
      return opts.some(o => o.textContent?.includes("age"))
    })!
    fireEvent.change(colSelect, { target: { value: "age" } })
    expect(onUpdate).toHaveBeenCalledWith("factors", expect.arrayContaining([
      expect.objectContaining({ banding: "breakpoints" }),
    ]))
  })

  // ─── Feature 3: Type toggle stash-and-restore ─────────────────

  it("type toggle stash-and-restore preserves rules", () => {
    const onUpdate = vi.fn()
    const existingRules = [{ boundary: "20", label: "band1" }, { boundary: "", label: "band2" }]
    const config = {
      factors: [{ banding: "breakpoints", column: "age", outputColumn: "age_band", rules: existingRules }],
    }
    const { rerender } = renderEditor(
      <BandingEditor config={config} onUpdate={onUpdate} inputSources={[]} accentColor="#22d3ee" />,
    )
    // Switch to categorical — should stash the breakpoints
    fireEvent.click(screen.getByText("Categorical"))
    const switched = (onUpdate.mock.lastCall![1] as Record<string, unknown>[])[0]
    expect(switched.banding).toBe("categorical")
    expect((switched._prevRules as Record<string, unknown>).breakpoints).toEqual(existingRules)
    expect(switched.rules).toEqual([])

    // Switching back restores them.
    rerender(
      <GraphProvider allNodes={[]} edges={[]} submodels={{}} preamble="">
        <BandingEditor config={{ factors: [switched] }} onUpdate={onUpdate} inputSources={[]} accentColor="#22d3ee" />
      </GraphProvider>,
    )
    fireEvent.click(screen.getByText("Numeric"))
    const restored = (onUpdate.mock.lastCall![1] as Record<string, unknown>[])[0]
    expect(restored.banding).toBe("breakpoints")
    expect(restored.rules).toEqual(existingRules)
  })

  // ─── Feature 4: Breakpoints mode rendering ────────────────────

  it("BreakpointGrid renders when banding is 'breakpoints' with existing rules", () => {
    const config = {
      factors: [{
        banding: "breakpoints",
        column: "age",
        outputColumn: "age_band",
        rules: [{ boundary: "25", label: "young" }, { boundary: "65", label: "mid" }],
      }],
    }
    renderEditor(
      <BandingEditor config={config} onUpdate={vi.fn()} inputSources={[]} accentColor="#22d3ee" />,
    )
    expect(screen.getByTestId("breakpoint-grid")).toBeTruthy()
  })

  // ─── Feature 5: Output column auto-suggest ────────────────────

  it("output column auto-suggests from input column", () => {
    const onUpdate = vi.fn()
    const columns = [{ name: "driver_age", dtype: "int64" }]
    const config = {
      factors: [{ banding: "categorical", column: "", outputColumn: "", rules: [] }],
    }
    renderEditor(
      <BandingEditor config={config} onUpdate={onUpdate} inputSources={[]} upstreamColumns={columns} accentColor="#22d3ee" />,
    )
    const selects = screen.getAllByRole("combobox")
    const colSelect = selects.find(s => {
      const opts = Array.from((s as HTMLSelectElement).options)
      return opts.some(o => o.textContent?.includes("driver_age"))
    })!
    fireEvent.change(colSelect, { target: { value: "driver_age" } })
    expect(onUpdate).toHaveBeenCalledWith("factors", expect.arrayContaining([
      expect.objectContaining({ outputColumn: "driver_age_band" }),
    ]))
  })

  // ─── Feature 6: No duplicate action ───────────────────────────

  it("offers no duplicate action on a factor row, only remove", () => {
    const rules = [{ boundary: "10", label: "young" }]
    const config = {
      factors: [
        { banding: "breakpoints", column: "age", outputColumn: "age_band", rules },
        { banding: "breakpoints", column: "bonus", outputColumn: "bonus_band", rules },
      ],
    }
    renderEditor(
      <BandingEditor config={config} onUpdate={vi.fn()} inputSources={[]} accentColor="#22d3ee" />,
    )
    const row = rowOf("age_band").parentElement as HTMLElement
    expect(within(row).getAllByRole("button").map((b) => b.getAttribute("aria-label"))).toEqual([
      "age_band complete",
      "Remove age_band column",
    ])
    // Removing is shown as a bin, not a cross.
    const remove = within(row).getByRole("button", { name: "Remove age_band column" })
    expect(remove.querySelector("svg[class*='trash']")).not.toBeNull()
    expect(remove.querySelector("svg[class*='lucide-x']")).toBeNull()
  })

  // ─── Feature 7: Accessibility ─────────────────────────────────

  it("roves focus through the factor rows with Up/Down, selecting as it goes", () => {
    const config = {
      factors: [
        { banding: "breakpoints", column: "age", outputColumn: "age_band", rules: [] },
        { banding: "categorical", column: "region", outputColumn: "region_group", rules: [] },
      ],
    }
    const onUpdate = vi.fn()
    renderEditor(
      <BandingEditor config={config} onUpdate={onUpdate} inputSources={[]} accentColor="#22d3ee" />,
    )
    expect(rowOf("age_band")).toHaveAttribute("tabindex", "0")
    expect(rowOf("region_group")).toHaveAttribute("tabindex", "-1")
    fireEvent.keyDown(rowOf("age_band"), { key: "ArrowDown" })
    expect(rowOf("region_group")).toHaveAttribute("aria-pressed", "true")
    // Selecting is not an edit.
    expect(onUpdate).not.toHaveBeenCalled()
  })

  // ─── Feature 8: Match counts + unmatched counter ──────────────

  it.each([
    ["categorical", {
      banding: "categorical",
      column: "region",
      outputColumn: "region_group",
      rules: [
        { value: "London", assignment: "South" },
        { value: "Manchester", assignment: "North" },
      ],
    }],
    ["breakpoints", {
      banding: "breakpoints",
      column: "age",
      outputColumn: "age_band",
      rules: [
        { boundary: "30", label: "young" },
        { boundary: "", label: "older" },
      ],
    }],
  ])("never shows counts, a total or a histogram from the preview rows (%s)", (_mode, factor) => {
    const previewRows = [
      { region: "London", age: 20 },
      { region: "London", age: 40 },
      { region: "Birmingham", age: 60 },
    ]
    renderEditor(
      <BandingEditor config={{ factors: [factor] }} onUpdate={vi.fn()} inputSources={[]} accentColor="#22d3ee" previewRows={previewRows} />,
    )
    expect(document.body.textContent).not.toMatch(/of 3 rows/)
    expect(screen.queryByTestId("banding-histogram")).toBeNull()
    const numericCells = Array.from(document.querySelectorAll("td")).filter((cell) =>
      /^\d+$/.test(cell.textContent?.trim() ?? ""),
    )
    expect(numericCells).toEqual([])
    const grid = screen.queryByTestId("breakpoint-grid")
    if (grid) expect(grid.getAttribute("data-match-counts")).toBe("null")
  })

  // ─── Feature 9: Validation warnings ───────────────────────────

  it("warns about a categorical value named by more than one rule", () => {
    const config = {
      factors: [{
        banding: "categorical",
        column: "region",
        outputColumn: "region_group",
        rules: [
          { value: "London", assignment: "South" },
          { value: "Leeds", assignment: "North" },
          { value: "London", assignment: "Capital" },
        ],
      }],
    }
    renderEditor(
      <BandingEditor config={config} onUpdate={vi.fn()} inputSources={[]} accentColor="#22d3ee" />,
    )
    expect(screen.getByText('Duplicate value "London" in rules 1, 3')).toBeInTheDocument()
  })


  // ─── Feature 14: Generate bands — prominent empty state ───────

  it("shows prominent empty state with Generate/Add manually when breakpoints empty", () => {
    const config = {
      factors: [{
        banding: "breakpoints",
        column: "age",
        outputColumn: "age_band",
        rules: [],
      }],
    }
    renderEditor(
      <BandingEditor config={config} onUpdate={vi.fn()} inputSources={[]} accentColor="#22d3ee" />,
    )
    expect(screen.getByText("No breakpoints yet.")).toBeTruthy()
    expect(screen.getByText("Generate even bands")).toBeTruthy()
    expect(screen.getByText("Add manually")).toBeTruthy()
  })

  it("generate bands dialog opens from prominent empty state button", () => {
    const config = {
      factors: [{
        banding: "breakpoints",
        column: "age",
        outputColumn: "age_band",
        rules: [],
      }],
    }
    renderEditor(
      <BandingEditor config={config} onUpdate={vi.fn()} inputSources={[]} accentColor="#22d3ee" />,
    )
    fireEvent.click(screen.getByText("Generate even bands"))
    expect(screen.getByTestId("generate-bands-dialog")).toBeTruthy()
    // The options take the prompt's place rather than opening below it.
    expect(screen.queryByText("No breakpoints yet.")).toBeNull()
    fireEvent.click(screen.getByText("Cancel"))
    expect(screen.getByText("No breakpoints yet.")).toBeInTheDocument()
  })

  it("generate bands dialog opens from button when breakpoints exist", () => {
    const config = {
      factors: [{
        banding: "breakpoints",
        column: "age",
        outputColumn: "age_band",
        rules: [{ boundary: "25", label: "young" }],
      }],
    }
    renderEditor(
      <BandingEditor config={config} onUpdate={vi.fn()} inputSources={[]} accentColor="#22d3ee" />,
    )
    const genBtn = screen.getByText("Generate")
    expect(genBtn).toBeTruthy()
    fireEvent.click(genBtn)
    const dialog = screen.getByTestId("generate-bands-dialog")
    // The options open under the button, above the breakpoints table.
    const following = (a: Node, b: Node) =>
      Boolean(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING)
    expect(following(genBtn, dialog)).toBe(true)
    expect(following(dialog, screen.getByTestId("breakpoint-grid"))).toBe(true)
  })

  it("starts Generate from the settings the field's breakpoints were generated with", () => {
    const generated = {
      factors: [{
        banding: "breakpoints",
        column: "premium",
        outputColumn: "premium_band",
        rules: [
          { boundary: "5200", label: "4000–5200" },
          { boundary: "6400", label: "5201–6400" },
          { boundary: "7000", label: "6401–7000" },
        ],
      }],
    }
    renderEditor(
      <BandingEditor config={generated} onUpdate={vi.fn()} inputSources={[]} accentColor="#22d3ee" />,
    )
    fireEvent.click(screen.getByText("Generate"))
    expect(JSON.parse(screen.getByTestId("generate-bands-dialog").getAttribute("data-initial")!)).toEqual({
      start: 4000,
      end: 7000,
      step: 1200,
    })
  })

  // ─── Feature 11: CategoricalValuePicker ───────────────────────

  it("offers the preview's values to pick from, without counts", () => {
    const config = {
      factors: [{
        banding: "categorical",
        column: "region",
        outputColumn: "region_group",
        rules: [],
      }],
    }
    const previewRows = [
      { region: "Manchester" },
      { region: "London" },
      { region: "London" },
    ]
    renderEditor(
      <BandingEditor config={config} onUpdate={vi.fn()} inputSources={[]} accentColor="#22d3ee" previewRows={previewRows} />,
    )
    expect(JSON.parse(screen.getByTestId("categorical-value-picker").getAttribute("data-values")!)).toEqual([
      { value: "London" },
      { value: "Manchester" },
    ])
  })

  // ─── Feature 10: Histogram ────────────────────────────────────

  // ─── Feature: breakpoints match counts ──────────────────────────

  // ─── Feature 15: onAddRule wiring ─────────────────────────────

  it("passes onAddRule to BandingRulesGrid for categorical mode", () => {
    const config = {
      factors: [{
        banding: "categorical",
        column: "region",
        outputColumn: "region_group",
        rules: [],
      }],
    }
    renderEditor(
      <BandingEditor config={config} onUpdate={vi.fn()} inputSources={[]} accentColor="#22d3ee" />,
    )
    expect(screen.getByText("No rules yet")).toBeTruthy()
  })

  // ─── Feature 16: Add manually button in breakpoints empty state ──

  it("'Add manually' creates a single empty breakpoint rule", () => {
    const onUpdate = vi.fn()
    const config = {
      factors: [{
        banding: "breakpoints",
        column: "age",
        outputColumn: "age_band",
        rules: [],
      }],
    }
    renderEditor(
      <BandingEditor config={config} onUpdate={onUpdate} inputSources={[]} accentColor="#22d3ee" />,
    )
    fireEvent.click(screen.getByText("Add manually"))
    expect(onUpdate).toHaveBeenCalledWith("factors", expect.arrayContaining([
      expect.objectContaining({
        banding: "breakpoints",
        rules: [expect.objectContaining({ boundary: "", label: "" })],
      }),
    ]))
  })

  // ─── Feature 17: Default value input ──────────────────────────────

  it("default value input dispatches update with null for empty string", () => {
    const onUpdate = vi.fn()
    const config = {
      factors: [{
        banding: "breakpoints",
        column: "age",
        outputColumn: "age_band",
        rules: [],
        default: "fallback",
      }],
    }
    renderEditor(
      <BandingEditor config={config} onUpdate={onUpdate} inputSources={[]} accentColor="#22d3ee" />,
    )
    const inputs = screen.getAllByRole("textbox")
    // The default input is the last text input
    const defaultInput = inputs[inputs.length - 1]
    fireEvent.change(defaultInput, { target: { value: "" } })
    expect(onUpdate).not.toHaveBeenCalled()
    fireEvent.blur(defaultInput)
    expect(onUpdate).toHaveBeenCalledWith("factors", expect.arrayContaining([
      expect.objectContaining({ default: null }),
    ]))
  })

  // ─── Feature 18: Accessibility — labels linked to inputs ──────────

  it("Input Column label is linked to its input via htmlFor", () => {
    const config = {
      factors: [{ banding: "breakpoints", column: "age", outputColumn: "age_band", rules: [] }],
    }
    renderEditor(
      <BandingEditor config={config} onUpdate={vi.fn()} inputSources={[]} accentColor="#22d3ee" />,
    )
    const label = screen.getByText("Input Column")
    expect(label.getAttribute("for")).toBeTruthy()
  })

  it("Output Column label is linked to its input via htmlFor", () => {
    const config = {
      factors: [{ banding: "breakpoints", column: "age", outputColumn: "age_band", rules: [] }],
    }
    renderEditor(
      <BandingEditor config={config} onUpdate={vi.fn()} inputSources={[]} accentColor="#22d3ee" />,
    )
    const label = screen.getByText("Output Column")
    expect(label.getAttribute("for")).toBeTruthy()
  })

  it("Add column button has aria-label", () => {
    const config = {
      factors: [{ banding: "breakpoints", column: "age", outputColumn: "age_band", rules: [] }],
    }
    renderEditor(
      <BandingEditor config={config} onUpdate={vi.fn()} inputSources={[]} accentColor="#22d3ee" />,
    )
    expect(screen.getByLabelText("Add column")).toBeTruthy()
  })

  // ─── Feature 19: No dangling ARIA references ─────────────────────

  it("labels nothing by an element that does not exist", () => {
    renderEditor(
      <BandingEditor config={{}} onUpdate={vi.fn()} inputSources={[]} accentColor="#22d3ee" />,
    )
    for (const element of Array.from(document.querySelectorAll("[aria-labelledby]"))) {
      expect(document.getElementById(element.getAttribute("aria-labelledby")!)).toBeTruthy()
    }
  })


  // ─── Feature 21: Categorical value picker not shown when no previewRows ──

  it("CategoricalValuePicker not shown without previewRows", () => {
    const config = {
      factors: [{
        banding: "categorical",
        column: "region",
        outputColumn: "region_group",
        rules: [],
      }],
    }
    renderEditor(
      <BandingEditor config={config} onUpdate={vi.fn()} inputSources={[]} accentColor="#22d3ee" />,
    )
    expect(screen.queryByTestId("categorical-value-picker")).toBeNull()
  })

  // ─── Feature 22: Histogram not shown for categorical mode ────────

  it("histogram not shown for categorical mode", () => {
    const config = {
      factors: [{
        banding: "categorical",
        column: "region",
        outputColumn: "region_group",
        rules: [{ value: "London", assignment: "South" }],
      }],
    }
    const previewRows = [{ region: "London" }, { region: "Manchester" }]
    renderEditor(
      <BandingEditor config={config} onUpdate={vi.fn()} inputSources={[]} accentColor="#22d3ee" previewRows={previewRows} />,
    )
    expect(screen.queryByTestId("banding-histogram")).toBeNull()
  })

  // ─── Feature 23: Match counts undefined when no previewRows ──────

  it("unmatched counter not shown when no previewRows", () => {
    const config = {
      factors: [{
        banding: "categorical",
        column: "region",
        outputColumn: "region_group",
        rules: [{ value: "London", assignment: "South" }],
      }],
    }
    renderEditor(
      <BandingEditor config={config} onUpdate={vi.fn()} inputSources={[]} accentColor="#22d3ee" />,
    )
    // The "N of M rows" counter should not appear when there are no previewRows
    expect(screen.queryByText(/\d+ of \d+ rows/)).toBeNull()
  })
})

describe("BandingEditor on a date column", () => {
  const DATE_RULES = [
    { boundary: "2024-01-31", label: "January" },
    { boundary: "2024-02-29", label: "February" },
    { boundary: "", label: "Later" },
  ]
  const dateFactor = (column: string, rules: { boundary: string; label: string }[]) => ({
    factors: [{ banding: "breakpoints", column, outputColumn: `${column}_band`, rules, rightClosed: true }],
  })

  it.each([
    ["start_date", "Date"],
    ["quoted_at", "Datetime(time_unit='us', time_zone='Europe/London')"],
  ])("offers Numeric for a %s column and selects it when that column is chosen", (column, dtype) => {
    const onUpdate = vi.fn()
    const columns = [
      { name: "region", dtype: "String" },
      { name: column, dtype },
    ]
    const config = {
      factors: [{ banding: "categorical", column: "region", outputColumn: "region_band", rules: [] }],
    }
    const { rerender } = renderEditor(
      <BandingEditor config={config} onUpdate={onUpdate} inputSources={[]} upstreamColumns={columns} accentColor="#22d3ee" />,
    )
    expect(screen.getByText("Numeric").closest("button")).toBeDisabled()

    fireEvent.change(screen.getByLabelText("Input Column"), { target: { value: column } })
    const factors = onUpdate.mock.calls[0][1]
    expect(factors[0]).toMatchObject({ column, banding: "breakpoints" })

    rerender(
      <GraphProvider allNodes={[]} edges={[]} submodels={{}} preamble="">
        <BandingEditor config={{ factors }} onUpdate={onUpdate} inputSources={[]} upstreamColumns={columns} accentColor="#22d3ee" />
      </GraphProvider>,
    )
    const numeric = screen.getByText("Numeric").closest("button")!
    expect(numeric).toBeEnabled()
    expect(numeric).toHaveAttribute("aria-checked", "true")
  })

  it("reads a factor whose breakpoints are dates as dates when its column's dtype is unknown", () => {
    renderEditor(
      <BandingEditor
        config={dateFactor("start_date", DATE_RULES)}
        onUpdate={vi.fn()}
        inputSources={[]}
        accentColor="#22d3ee"
        previewRows={[{ start_date: "2024-01-15" }, { start_date: "2024-02-15" }]}
      />,
    )
    expect(screen.getByTestId("breakpoint-grid")).toHaveAttribute("data-temporal", "true")
  })

  it("starts Generate from the calendar step the date breakpoints were made with", () => {
    renderEditor(
      <BandingEditor
        config={dateFactor("start_date", [
          { boundary: "2024-01-31", label: "2024-01-01–2024-01-31" },
          { boundary: "2024-02-29", label: "2024-02-01–2024-02-29" },
          { boundary: "2024-03-31", label: "2024-03-01–2024-03-31" },
        ])}
        onUpdate={vi.fn()}
        inputSources={[]}
        upstreamColumns={[{ name: "start_date", dtype: "Date" }]}
        accentColor="#22d3ee"
      />,
    )
    fireEvent.click(screen.getByText("Generate"))
    const dialog = screen.getByTestId("generate-bands-dialog")
    expect(dialog).toHaveAttribute("data-temporal", "true")
    expect(JSON.parse(dialog.getAttribute("data-initial")!)).toEqual({
      start: "2024-01-01",
      end: "2024-03-31",
      step: 1,
      unit: "months",
    })
  })

  it("offers the data's first and last dates to Generate on a date column without breakpoints", () => {
    renderEditor(
      <BandingEditor
        config={dateFactor("quoted_at", [])}
        onUpdate={vi.fn()}
        inputSources={[]}
        upstreamColumns={[{ name: "quoted_at", dtype: "Datetime(time_unit='us', time_zone='Europe/Paris')" }]}
        accentColor="#22d3ee"
        previewRows={[
          { quoted_at: "2024-03-20T23:30:00+01:00" },
          { quoted_at: "2024-01-05T00:15:00+01:00" },
          { quoted_at: null },
        ]}
      />,
    )
    fireEvent.click(screen.getByText("Generate even bands"))
    const dialog = screen.getByTestId("generate-bands-dialog")
    expect(dialog).toHaveAttribute("data-temporal", "true")
    expect(JSON.parse(dialog.getAttribute("data-min")!)).toBe("2024-01-05")
    expect(JSON.parse(dialog.getAttribute("data-max")!)).toBe("2024-03-20")
    expect(JSON.parse(dialog.getAttribute("data-initial")!)).toBeNull()
  })
})
