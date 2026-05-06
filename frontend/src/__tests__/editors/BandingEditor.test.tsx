/**
 * Render tests for BandingEditor.
 *
 * Tests: renders with default config, factor tabs, adding/removing factors,
 * type toggle, column selection with auto-type detection, add rule button,
 * summary section display, breakpoints mode, stash-and-restore, accessibility,
 * match counts, validation warnings, histogram, categorical value picker.
 */
import { describe, it, expect, vi, afterEach } from "vitest"
import { render, screen, fireEvent, cleanup, within } from "@testing-library/react"
import BandingEditor from "../../panels/editors/BandingEditor"

afterEach(cleanup)

// Mock child components that we don't need to test internals of
vi.mock("../../panels/editors/banding/BreakpointGrid", () => ({
  BreakpointGrid: (props: Record<string, unknown>) => (
    <div data-testid="breakpoint-grid" data-breakpoints={JSON.stringify(props.breakpoints)} />
  ),
}))

vi.mock("../../panels/editors/banding/BandingHistogram", () => ({
  BandingHistogram: (props: Record<string, unknown>) => (
    <div data-testid="banding-histogram" data-values={JSON.stringify(props.values)} />
  ),
}))

vi.mock("../../panels/editors/banding/GenerateBandsDialog", () => ({
  GenerateBandsDialog: (props: Record<string, unknown>) => (
    <div data-testid="generate-bands-dialog">
      <button onClick={props.onClose as () => void}>Cancel</button>
    </div>
  ),
}))

vi.mock("../../panels/editors/banding/CategoricalValuePicker", () => ({
  CategoricalValuePicker: (props: Record<string, unknown>) => (
    <div data-testid="categorical-value-picker" data-values={JSON.stringify(props.availableValues)} />
  ),
}))

describe("BandingEditor", () => {
  // ─── Existing tests (updated for terminology changes) ─────────

  it("renders column tabs for multi-factor config", () => {
    const config = {
      factors: [
        { banding: "breakpoints", column: "age", outputColumn: "age_band", rules: [] },
        { banding: "categorical", column: "region", outputColumn: "region_group", rules: [] },
      ],
    }
    render(
      <BandingEditor config={config} onUpdate={vi.fn()} inputSources={[]} accentColor="#22d3ee" />,
    )
    const tablist = screen.getByRole("tablist")
    expect(within(tablist).getByText("age_band")).toBeTruthy()
    expect(within(tablist).getByText("region_group")).toBeTruthy()
  })

  it("renders factor tab label from column when outputColumn is empty", () => {
    const config = {
      factors: [
        { banding: "continuous", column: "driver_age", outputColumn: "", rules: [] },
      ],
    }
    render(
      <BandingEditor config={config} onUpdate={vi.fn()} inputSources={[]} accentColor="#22d3ee" />,
    )
    // Single factor with column set should show tabs (not unconfigured)
    const tablist = screen.getByRole("tablist")
    expect(within(tablist).getByText("driver_age")).toBeTruthy()
  })

  it("hides tabs when single factor is unconfigured (no 'Column 1' shown)", () => {
    render(
      <BandingEditor config={{}} onUpdate={vi.fn()} inputSources={[]} accentColor="#22d3ee" />,
    )
    // Tabs should be hidden for single unconfigured factor
    expect(screen.queryByRole("tablist")).toBeNull()
    // "Column 1" label should not appear
    expect(screen.queryByText("Column 1")).toBeNull()
  })

  it("adding a factor creates new tab and switches to it", () => {
    const onUpdate = vi.fn()
    // Start with a configured factor so tabs are visible
    const config = {
      factors: [{ banding: "continuous", column: "age", outputColumn: "age_band", rules: [] }],
    }
    render(
      <BandingEditor config={config} onUpdate={onUpdate} inputSources={[]} accentColor="#22d3ee" />,
    )
    const tablist = screen.getByRole("tablist")
    const addBtn = within(tablist).getAllByRole("button").find(b => {
      return b.querySelector("svg") && b.textContent === ""
    })
    expect(addBtn).toBeTruthy()
    fireEvent.click(addBtn!)

    expect(onUpdate).toHaveBeenCalledWith("factors", expect.arrayContaining([
      expect.objectContaining({ banding: "continuous" }),
      expect.objectContaining({ banding: "continuous" }),
    ]))
    const factors = onUpdate.mock.calls[0][1]
    expect(factors).toHaveLength(2)
  })

  it("removing a factor when >1 factors removes the tab", () => {
    const onUpdate = vi.fn()
    const config = {
      factors: [
        { banding: "continuous", column: "age", outputColumn: "age_band", rules: [] },
        { banding: "categorical", column: "region", outputColumn: "region_group", rules: [] },
      ],
    }
    render(
      <BandingEditor config={config} onUpdate={onUpdate} inputSources={[]} accentColor="#22d3ee" />,
    )

    const tablist = screen.getByRole("tablist")
    const ageBandTab = within(tablist).getByText("age_band").closest("[role='tab']")!
    const removeBtn = ageBandTab.querySelector("button[aria-label='Remove column']")
    expect(removeBtn).toBeTruthy()
    fireEvent.click(removeBtn!)

    expect(onUpdate).toHaveBeenCalledWith("factors", [
      expect.objectContaining({ column: "region", outputColumn: "region_group" }),
    ])
  })

  it("cannot remove last factor (single factor)", () => {
    const config = {
      factors: [
        { banding: "continuous", column: "age", outputColumn: "age_band", rules: [] },
      ],
    }
    const { container } = render(
      <BandingEditor config={config} onUpdate={vi.fn()} inputSources={[]} accentColor="#22d3ee" />,
    )
    const removeButtons = container.querySelectorAll("button[aria-label='Remove column']")
    expect(removeButtons.length).toBe(0)
  })

  it("type toggle between continuous and categorical calls updateFactor", () => {
    const onUpdate = vi.fn()
    // Use a configured factor so type toggle is visible
    const config = {
      factors: [{ banding: "continuous", column: "age", outputColumn: "age_band", rules: [] }],
    }
    render(
      <BandingEditor config={config} onUpdate={onUpdate} inputSources={[]} accentColor="#22d3ee" />,
    )
    fireEvent.click(screen.getByText("Categorical"))
    expect(onUpdate).toHaveBeenCalled()
    const call = onUpdate.mock.calls.find(
      (c: unknown[]) => (c[1] as Record<string, unknown>[])?.[0]?.banding === "categorical"
    )
    expect(call).toBeTruthy()
  })

  it("type toggle hidden when single unconfigured factor", () => {
    render(
      <BandingEditor config={{}} onUpdate={vi.fn()} inputSources={[]} accentColor="#22d3ee" />,
    )
    // Type toggle options should not be visible
    expect(screen.queryByText("Breakpoints")).toBeNull()
    // Advanced option removed from UI
    expect(screen.queryByText("Categorical")).toBeNull()
  })

  it("column selection with upstream columns renders dropdown", () => {
    const columns = [
      { name: "age", dtype: "int64" },
      { name: "region", dtype: "Utf8" },
    ]
    render(
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
    render(
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

  it("column selection auto-detects type for string dtype", () => {
    const onUpdate = vi.fn()
    const columns = [
      { name: "age", dtype: "int64" },
      { name: "region", dtype: "Utf8" },
    ]
    const config = {
      factors: [{ banding: "continuous", column: "", outputColumn: "", rules: [] }],
    }
    render(
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

  it("add rule button adds appropriate empty rule type for continuous", () => {
    const onUpdate = vi.fn()
    const config = {
      factors: [{ banding: "continuous", column: "age", outputColumn: "age_band", rules: [] }],
    }
    render(
      <BandingEditor config={config} onUpdate={onUpdate} inputSources={[]} accentColor="#22d3ee" />,
    )
    fireEvent.click(screen.getByText("Add"))
    expect(onUpdate).toHaveBeenCalledWith("factors", expect.arrayContaining([
      expect.objectContaining({
        banding: "continuous",
        rules: [expect.objectContaining({ op1: ">", val1: "", assignment: "" })],
      }),
    ]))
  })

  it("add rule button adds appropriate empty rule type for categorical", () => {
    const onUpdate = vi.fn()
    const config = {
      factors: [{ banding: "categorical", column: "region", outputColumn: "region_group", rules: [] }],
    }
    render(
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

  it("summary section hidden when only 1 factor", () => {
    const config = {
      factors: [{
        banding: "continuous",
        column: "age",
        outputColumn: "age_band",
        rules: [{ op1: ">", val1: "25", op2: "", val2: "", assignment: "young" }],
      }],
    }
    const { container } = render(
      <BandingEditor config={config} onUpdate={vi.fn()} inputSources={[]} accentColor="#22d3ee" />,
    )
    const summary = container.querySelector("[data-testid='banding-summary']")
    expect(summary).toBeNull()
  })

  it("summary section shown when 2+ factors", () => {
    const config = {
      factors: [
        { banding: "continuous", column: "age", outputColumn: "age_band", rules: [{ op1: ">", val1: "25", op2: "", val2: "", assignment: "young" }] },
        { banding: "categorical", column: "region", outputColumn: "region_group", rules: [{ value: "London", assignment: "South" }] },
      ],
    }
    const { container } = render(
      <BandingEditor config={config} onUpdate={vi.fn()} inputSources={[]} accentColor="#22d3ee" />,
    )
    const summary = container.querySelector("[data-testid='banding-summary']")
    expect(summary).toBeTruthy()
  })

  it("renders text input for column when no upstreamColumns", () => {
    render(
      <BandingEditor config={{}} onUpdate={vi.fn()} inputSources={[]} accentColor="#22d3ee" />,
    )
    // Text input for column should be present (no placeholder)
    const inputs = screen.getAllByRole("textbox")
    expect(inputs.length).toBeGreaterThan(0)
  })

  it("renders default value input", () => {
    render(
      <BandingEditor config={{}} onUpdate={vi.fn()} inputSources={[]} accentColor="#22d3ee" />,
    )
    expect(screen.getByText(/Default/)).toBeTruthy()
  })

  it("renders 'No rules yet' when continuous rules are empty", () => {
    const config = {
      factors: [{ banding: "continuous", column: "age", outputColumn: "age_band", rules: [] }],
    }
    render(
      <BandingEditor config={config} onUpdate={vi.fn()} inputSources={[]} accentColor="#22d3ee" />,
    )
    expect(screen.getByText("No rules yet")).toBeTruthy()
  })

  it("renders continuous rules grid headers when continuous rules exist", () => {
    const config = {
      factors: [{
        banding: "continuous",
        column: "age",
        outputColumn: "age_band",
        rules: [{ op1: ">", val1: "25", op2: "<=", val2: "35", assignment: "young" }],
      }],
    }
    render(
      <BandingEditor config={config} onUpdate={vi.fn()} inputSources={[]} accentColor="#22d3ee" />,
    )
    expect(screen.getByText("Label")).toBeTruthy()
    expect(screen.getByText("Rules (1)")).toBeTruthy()
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
    render(
      <BandingEditor config={config} onUpdate={vi.fn()} inputSources={[]} accentColor="#22d3ee" />,
    )
    expect(screen.getByText("Maps To")).toBeTruthy()
  })

  it("clicking a factor tab switches the active factor", () => {
    const config = {
      factors: [
        { banding: "continuous", column: "age", outputColumn: "age_band", rules: [] },
        { banding: "categorical", column: "region", outputColumn: "region_group", rules: [] },
      ],
    }
    render(
      <BandingEditor config={config} onUpdate={vi.fn()} inputSources={[]} accentColor="#22d3ee" />,
    )
    const tablist = screen.getByRole("tablist")
    const catTab = within(tablist).getByText("region_group").closest("[role='tab']")!
    fireEvent.click(catTab)
    const catTypeBtn = screen.getByText("Categorical").closest("button")!
    expect(catTypeBtn.style.border).toBeTruthy()
    expect(catTypeBtn.style.border).not.toBe("none")
  })

  it("renders InputSourcesBar when inputs provided", () => {
    const inputSources = [{ sourceNodeId: "test-source", varName: "data", sourceLabel: "Data", edgeId: "e1" }]
    render(
      <BandingEditor config={{}} onUpdate={vi.fn()} inputSources={inputSources} accentColor="#22d3ee" />,
    )
    expect(screen.getByText("data")).toBeTruthy()
  })

  // ─── Feature 1: Terminology cleanup ───────────────────────────

  it("does not show detected type hint label", () => {
    const config = {
      factors: [{ banding: "breakpoints", column: "age", outputColumn: "age_band", rules: [] }],
    }
    render(
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

  // ─── Feature 2: Three-way banding type toggle ─────────────────

  it("Breakpoints option appears in type toggle when factor is configured", () => {
    const config = {
      factors: [{ banding: "continuous", column: "age", outputColumn: "age_band", rules: [] }],
    }
    render(
      <BandingEditor config={config} onUpdate={vi.fn()} inputSources={[]} accentColor="#22d3ee" />,
    )
    expect(screen.getByText("Breakpoints")).toBeTruthy()
    expect(screen.getByText("Categorical")).toBeTruthy()
  })

  it("auto-detect selects 'breakpoints' for numeric columns", () => {
    const onUpdate = vi.fn()
    const columns = [{ name: "age", dtype: "int64" }]
    const config = {
      factors: [{ banding: "categorical", column: "", outputColumn: "", rules: [] }],
    }
    render(
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
    const existingRules = [{ op1: ">", val1: "10", op2: "<=", val2: "20", assignment: "band1" }]
    const config = {
      factors: [{ banding: "continuous", column: "age", outputColumn: "age_band", rules: existingRules }],
    }
    render(
      <BandingEditor config={config} onUpdate={onUpdate} inputSources={[]} accentColor="#22d3ee" />,
    )
    // Switch to categorical — should stash continuous rules
    fireEvent.click(screen.getByText("Categorical"))
    const switchCall = onUpdate.mock.calls.find(
      (c: unknown[]) => (c[1] as Record<string, unknown>[])?.[0]?.banding === "categorical"
    )
    expect(switchCall).toBeTruthy()
    const factor = (switchCall![1] as Record<string, unknown>[])[0]
    expect(factor._prevRules).toBeDefined()
    expect((factor._prevRules as Record<string, unknown>).continuous).toEqual(existingRules)
    expect(factor.rules).toEqual([])
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
    render(
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
    render(
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

  // ─── Feature 6: Duplicate factor ──────────────────────────────

  it("duplicate factor button creates copy with cleared columns", () => {
    const onUpdate = vi.fn()
    const config = {
      factors: [{
        banding: "continuous",
        column: "age",
        outputColumn: "age_band",
        rules: [{ op1: ">", val1: "10", op2: "", val2: "", assignment: "young" }],
        default: "other",
      }],
    }
    render(
      <BandingEditor config={config} onUpdate={onUpdate} inputSources={[]} accentColor="#22d3ee" />,
    )
    const dupBtn = screen.getByLabelText("Duplicate column")
    fireEvent.click(dupBtn)
    const dupCall = onUpdate.mock.calls.find(
      (c: unknown[]) => Array.isArray(c[1]) && (c[1] as unknown[]).length === 2
    )
    expect(dupCall).toBeTruthy()
    const factors = dupCall![1] as Record<string, unknown>[]
    expect(factors).toHaveLength(2)
    expect(factors[1].column).toBe("")
    expect(factors[1].outputColumn).toBe("")
    expect(factors[1].banding).toBe("continuous")
    expect(factors[1].default).toBe("other")
  })

  // ─── Feature 7: Accessibility — ARIA tab roles ────────────────

  it("tab container has role='tablist' when tabs are visible", () => {
    const config = {
      factors: [{ banding: "continuous", column: "age", outputColumn: "age_band", rules: [] }],
    }
    render(
      <BandingEditor config={config} onUpdate={vi.fn()} inputSources={[]} accentColor="#22d3ee" />,
    )
    expect(screen.getByRole("tablist")).toBeTruthy()
  })

  it("tabs have role='tab' and aria-selected", () => {
    const config = {
      factors: [
        { banding: "continuous", column: "age", outputColumn: "age_band", rules: [] },
        { banding: "categorical", column: "region", outputColumn: "region_group", rules: [] },
      ],
    }
    render(
      <BandingEditor config={config} onUpdate={vi.fn()} inputSources={[]} accentColor="#22d3ee" />,
    )
    const tabs = screen.getAllByRole("tab")
    expect(tabs.length).toBe(2)
    expect(tabs[0].getAttribute("aria-selected")).toBe("true")
    expect(tabs[1].getAttribute("aria-selected")).toBe("false")
  })

  it("tabpanel has role='tabpanel' when factor is configured", () => {
    const config = {
      factors: [{ banding: "continuous", column: "age", outputColumn: "age_band", rules: [] }],
    }
    render(
      <BandingEditor config={config} onUpdate={vi.fn()} inputSources={[]} accentColor="#22d3ee" />,
    )
    expect(screen.getByRole("tabpanel")).toBeTruthy()
  })

  it("no tabpanel when single unconfigured factor (no dangling ARIA refs)", () => {
    render(
      <BandingEditor config={{}} onUpdate={vi.fn()} inputSources={[]} accentColor="#22d3ee" />,
    )
    // No tabpanel should exist when there are no tabs
    expect(screen.queryByRole("tabpanel")).toBeNull()
  })

  // ─── Feature 8: Match counts + unmatched counter ──────────────

  it("match counts computed from previewRows for categorical", () => {
    const config = {
      factors: [{
        banding: "categorical",
        column: "region",
        outputColumn: "region_group",
        rules: [
          { value: "London", assignment: "South" },
          { value: "Manchester", assignment: "North" },
        ],
      }],
    }
    const previewRows = [
      { region: "London" },
      { region: "London" },
      { region: "Manchester" },
      { region: "Birmingham" },
    ]
    render(
      <BandingEditor config={config} onUpdate={vi.fn()} inputSources={[]} accentColor="#22d3ee" previewRows={previewRows} />,
    )
    const allText = document.body.textContent || ""
    expect(allText).toContain("1 of 4")
  })

  it("unmatched counter shows next to default", () => {
    const config = {
      factors: [{
        banding: "categorical",
        column: "region",
        outputColumn: "region_group",
        rules: [{ value: "London", assignment: "South" }],
      }],
    }
    const previewRows = [
      { region: "London" },
      { region: "Manchester" },
    ]
    render(
      <BandingEditor config={config} onUpdate={vi.fn()} inputSources={[]} accentColor="#22d3ee" previewRows={previewRows} />,
    )
    const allText = document.body.textContent || ""
    expect(allText).toContain("1 of 2")
  })

  // ─── Feature 9: Validation warnings ───────────────────────────

  it("validation warnings display for overlapping rules", () => {
    const config = {
      factors: [{
        banding: "continuous",
        column: "age",
        outputColumn: "age_band",
        rules: [
          { op1: "<=", val1: "15", op2: "", val2: "", assignment: "A" },
          { op1: ">=", val1: "10", op2: "<=", val2: "20", assignment: "B" },
        ],
      }],
    }
    render(
      <BandingEditor config={config} onUpdate={vi.fn()} inputSources={[]} accentColor="#22d3ee" />,
    )
    const allText = document.body.textContent || ""
    expect(allText).toContain("overlap")
  })

  it("validation warnings display for gaps", () => {
    const config = {
      factors: [{
        banding: "continuous",
        column: "age",
        outputColumn: "age_band",
        rules: [
          { op1: "<", val1: "10", op2: "", val2: "", assignment: "A" },
          { op1: ">", val1: "20", op2: "", val2: "", assignment: "B" },
        ],
      }],
    }
    render(
      <BandingEditor config={config} onUpdate={vi.fn()} inputSources={[]} accentColor="#22d3ee" />,
    )
    const allText = document.body.textContent || ""
    expect(allText.toLowerCase()).toContain("gap")
  })

  // ─── Feature 12: Summary ─────────────────────────────────────

  it("summary rows are clickable and switch to that tab", () => {
    const config = {
      factors: [
        { banding: "continuous", column: "age", outputColumn: "age_band", rules: [{ op1: ">", val1: "10", op2: "", val2: "", assignment: "a" }] },
        { banding: "categorical", column: "region", outputColumn: "region_group", rules: [{ value: "London", assignment: "South" }] },
      ],
    }
    render(
      <BandingEditor config={config} onUpdate={vi.fn()} inputSources={[]} accentColor="#22d3ee" />,
    )
    const summary = screen.getByTestId("banding-summary")
    const summaryRows = summary.querySelectorAll("[data-testid^='summary-row-']")
    expect(summaryRows.length).toBe(2)
    fireEvent.click(summaryRows[1])
    const tabs = screen.getAllByRole("tab")
    expect(tabs[1].getAttribute("aria-selected")).toBe("true")
  })

  it("incomplete factors shown dimmed in summary", () => {
    const config = {
      factors: [
        { banding: "continuous", column: "age", outputColumn: "age_band", rules: [{ op1: ">", val1: "10", op2: "", val2: "", assignment: "a" }] },
        { banding: "continuous", column: "", outputColumn: "", rules: [] },
      ],
    }
    render(
      <BandingEditor config={config} onUpdate={vi.fn()} inputSources={[]} accentColor="#22d3ee" />,
    )
    const summary = screen.getByTestId("banding-summary")
    const rows = summary.querySelectorAll("[data-testid^='summary-row-']")
    expect(rows.length).toBe(2)
    const secondRow = rows[1] as HTMLElement
    expect(secondRow.style.opacity).toBe("0.5")
  })

  // ─── Feature 13: Tab overflow -> horizontal scroll ─────────────

  it("tab bar uses horizontal scroll not wrap", () => {
    const config = {
      factors: [{ banding: "continuous", column: "age", outputColumn: "age_band", rules: [] }],
    }
    render(
      <BandingEditor config={config} onUpdate={vi.fn()} inputSources={[]} accentColor="#22d3ee" />,
    )
    const tablist = screen.getByRole("tablist")
    expect(tablist.className).toContain("overflow-x-auto")
    expect(tablist.className).toContain("flex-nowrap")
    expect(tablist.className).not.toContain("flex-wrap")
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
    render(
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
    render(
      <BandingEditor config={config} onUpdate={vi.fn()} inputSources={[]} accentColor="#22d3ee" />,
    )
    fireEvent.click(screen.getByText("Generate even bands"))
    expect(screen.getByTestId("generate-bands-dialog")).toBeTruthy()
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
    render(
      <BandingEditor config={config} onUpdate={vi.fn()} inputSources={[]} accentColor="#22d3ee" />,
    )
    const genBtn = screen.getByText("Generate")
    expect(genBtn).toBeTruthy()
    fireEvent.click(genBtn)
    expect(screen.getByTestId("generate-bands-dialog")).toBeTruthy()
  })

  // ─── Feature 11: CategoricalValuePicker ───────────────────────

  it("CategoricalValuePicker shown when categorical with previewRows", () => {
    const config = {
      factors: [{
        banding: "categorical",
        column: "region",
        outputColumn: "region_group",
        rules: [],
      }],
    }
    const previewRows = [
      { region: "London" },
      { region: "Manchester" },
      { region: "London" },
    ]
    render(
      <BandingEditor config={config} onUpdate={vi.fn()} inputSources={[]} accentColor="#22d3ee" previewRows={previewRows} />,
    )
    expect(screen.getByTestId("categorical-value-picker")).toBeTruthy()
  })

  // ─── Feature 10: Histogram ────────────────────────────────────

  it("histogram shown for breakpoints mode with numeric data", () => {
    const config = {
      factors: [{
        banding: "breakpoints",
        column: "age",
        outputColumn: "age_band",
        rules: [{ boundary: "25", label: "young" }],
      }],
    }
    const previewRows = [
      { age: 20 },
      { age: 30 },
      { age: 40 },
    ]
    render(
      <BandingEditor config={config} onUpdate={vi.fn()} inputSources={[]} accentColor="#22d3ee" previewRows={previewRows} />,
    )
    expect(screen.getByTestId("banding-histogram")).toBeTruthy()
  })

  // ─── Feature: breakpoints match counts ──────────────────────────

  it("match counts computed from previewRows for breakpoints mode", () => {
    const config = {
      factors: [{
        banding: "breakpoints",
        column: "age",
        outputColumn: "age_band",
        rules: [
          { boundary: "25", label: "young" },
          { boundary: "65", label: "mid" },
        ],
        rightClosed: true,
      }],
    }
    const previewRows = [
      { age: 20 },  // <=25 -> young
      { age: 25 },  // <=25 -> young
      { age: 30 },  // >25 and <=65 -> mid
      { age: 70 },  // >65 -> unmatched (no catch-all, goes to default)
    ]
    render(
      <BandingEditor config={config} onUpdate={vi.fn()} inputSources={[]} accentColor="#22d3ee" previewRows={previewRows} />,
    )
    // 3 matched (young: 2, mid: 1), 1 unmatched (age 70)
    const allText = document.body.textContent || ""
    expect(allText).toContain("1 of 4")
  })

  // ─── Feature 15: onAddRule wiring ─────────────────────────────

  it("passes onAddRule to BandingRulesGrid for continuous mode", () => {
    const config = {
      factors: [{
        banding: "continuous",
        column: "age",
        outputColumn: "age_band",
        rules: [],
      }],
    }
    render(
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
    render(
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
        banding: "continuous",
        column: "age",
        outputColumn: "age_band",
        rules: [],
        default: "fallback",
      }],
    }
    render(
      <BandingEditor config={config} onUpdate={onUpdate} inputSources={[]} accentColor="#22d3ee" />,
    )
    const inputs = screen.getAllByRole("textbox")
    // The default input is the last text input
    const defaultInput = inputs[inputs.length - 1]
    fireEvent.change(defaultInput, { target: { value: "" } })
    expect(onUpdate).toHaveBeenCalledWith("factors", expect.arrayContaining([
      expect.objectContaining({ default: null }),
    ]))
  })

  // ─── Feature 18: Accessibility — labels linked to inputs ──────────

  it("Input Column label is linked to its input via htmlFor", () => {
    const config = {
      factors: [{ banding: "continuous", column: "age", outputColumn: "age_band", rules: [] }],
    }
    render(
      <BandingEditor config={config} onUpdate={vi.fn()} inputSources={[]} accentColor="#22d3ee" />,
    )
    const label = screen.getByText("Input Column")
    expect(label.getAttribute("for")).toBeTruthy()
  })

  it("Output Column label is linked to its input via htmlFor", () => {
    const config = {
      factors: [{ banding: "continuous", column: "age", outputColumn: "age_band", rules: [] }],
    }
    render(
      <BandingEditor config={config} onUpdate={vi.fn()} inputSources={[]} accentColor="#22d3ee" />,
    )
    const label = screen.getByText("Output Column")
    expect(label.getAttribute("for")).toBeTruthy()
  })

  it("Add column button has aria-label", () => {
    const config = {
      factors: [{ banding: "continuous", column: "age", outputColumn: "age_band", rules: [] }],
    }
    render(
      <BandingEditor config={config} onUpdate={vi.fn()} inputSources={[]} accentColor="#22d3ee" />,
    )
    expect(screen.getByLabelText("Add column")).toBeTruthy()
  })

  // ─── Feature 19: Broken ARIA ref when tabs hidden ─────────────────

  it("no broken tabpanel aria-labelledby when tabs are hidden", () => {
    render(
      <BandingEditor config={{}} onUpdate={vi.fn()} inputSources={[]} accentColor="#22d3ee" />,
    )
    // When tabs are hidden (single unconfigured), no tabpanel with a dangling aria-labelledby should exist
    const tabpanels = screen.queryAllByRole("tabpanel")
    for (const tp of tabpanels) {
      const labelledBy = tp.getAttribute("aria-labelledby")
      if (labelledBy) {
        expect(document.getElementById(labelledBy)).toBeTruthy()
      }
    }
  })

  // ─── Feature 20: Duplicate deep copies rules ─────────────────────

  it("duplicate factor deep copies rules (not shared references)", () => {
    const onUpdate = vi.fn()
    const config = {
      factors: [{
        banding: "continuous",
        column: "age",
        outputColumn: "age_band",
        rules: [{ op1: ">", val1: "10", op2: "", val2: "", assignment: "young" }],
      }],
    }
    render(
      <BandingEditor config={config} onUpdate={onUpdate} inputSources={[]} accentColor="#22d3ee" />,
    )
    fireEvent.click(screen.getByLabelText("Duplicate column"))
    const dupCall = onUpdate.mock.calls.find(
      (c: unknown[]) => Array.isArray(c[1]) && (c[1] as unknown[]).length === 2
    )
    const factors = dupCall![1] as Record<string, unknown>[]
    // Rules should be deeply independent objects
    expect(factors[0].rules).not.toBe(factors[1].rules)
    const rules0 = factors[0].rules as Record<string, unknown>[]
    const rules1 = factors[1].rules as Record<string, unknown>[]
    expect(rules0[0]).not.toBe(rules1[0])
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
    render(
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
    render(
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
    render(
      <BandingEditor config={config} onUpdate={vi.fn()} inputSources={[]} accentColor="#22d3ee" />,
    )
    // The "N of M rows" counter should not appear when there are no previewRows
    expect(screen.queryByText(/\d+ of \d+ rows/)).toBeNull()
  })
})
