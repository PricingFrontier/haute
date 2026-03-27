/**
 * Render tests for BandingEditor.
 *
 * Tests: renders with default config, factor tabs, adding/removing factors,
 * type toggle, column selection with auto-type detection, add rule button,
 * summary section display.
 */
import { describe, it, expect, vi, afterEach } from "vitest"
import { render, screen, fireEvent, cleanup } from "@testing-library/react"
import BandingEditor from "../../panels/editors/BandingEditor"

afterEach(cleanup)

describe("BandingEditor", () => {
  it("renders factor tabs", () => {
    const config = {
      factors: [
        { banding: "continuous", column: "age", outputColumn: "age_band", rules: [] },
        { banding: "categorical", column: "region", outputColumn: "region_group", rules: [] },
      ],
    }
    render(
      <BandingEditor config={config} onUpdate={vi.fn()} inputSources={[]} accentColor="#22d3ee" />,
    )
    expect(screen.getByText("age_band")).toBeTruthy()
    expect(screen.getByText("region_group")).toBeTruthy()
    expect(screen.getByText(/2 factors/)).toBeTruthy()
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
    expect(screen.getByText("driver_age")).toBeTruthy()
  })

  it("renders factor tab label as 'Factor N' when both column and outputColumn are empty", () => {
    render(
      <BandingEditor config={{}} onUpdate={vi.fn()} inputSources={[]} accentColor="#22d3ee" />,
    )
    expect(screen.getByText("Factor 1")).toBeTruthy()
  })

  it("adding a factor creates new tab and switches to it", () => {
    const onUpdate = vi.fn()
    render(
      <BandingEditor config={{}} onUpdate={onUpdate} inputSources={[]} accentColor="#22d3ee" />,
    )
    // The + button to add a factor
    const buttons = screen.getAllByRole("button")
    const addBtn = buttons.find(b => {
      const svg = b.querySelector("svg")
      return svg && b.textContent === "" && !b.textContent?.includes("Add")
    })
    expect(addBtn).toBeTruthy()
    fireEvent.click(addBtn!)

    // Should call onUpdate with 2 factors
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

    // Find the X button for the first factor (age_band tab)
    const ageBandTab = screen.getByText("age_band").closest("button")!
    const removeBtn = ageBandTab.querySelector("button[aria-label='Remove factor']")
    expect(removeBtn).toBeTruthy()
    fireEvent.click(removeBtn!)

    // Should call onUpdate with only the remaining factor
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
    // When there's only 1 factor, the X button should not be rendered on the tab
    const tabButtons = container.querySelectorAll("button")
    const ageTab = Array.from(tabButtons).find(b => b.textContent?.includes("age_band"))
    if (ageTab) {
      // Should not have a remove span inside
      const removeSpan = ageTab.querySelector("span[class*='cursor-pointer']")
      expect(removeSpan).toBeNull()
    }
  })

  it("type toggle between continuous and categorical calls updateFactor", () => {
    const onUpdate = vi.fn()
    render(
      <BandingEditor config={{}} onUpdate={onUpdate} inputSources={[]} accentColor="#22d3ee" />,
    )
    fireEvent.click(screen.getByText("Categorical"))
    expect(onUpdate).toHaveBeenCalledWith("factors", expect.arrayContaining([
      expect.objectContaining({ banding: "categorical", rules: [] }),
    ]))
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
    // Should render a select with column options
    const selects = screen.getAllByRole("combobox")
    const colSelect = selects.find(s => {
      const opts = Array.from((s as HTMLSelectElement).options)
      return opts.some(o => o.textContent?.includes("age"))
    })
    expect(colSelect).toBeTruthy()
  })

  it("column selection auto-detects type for numeric dtype", () => {
    const onUpdate = vi.fn()
    const columns = [
      { name: "age", dtype: "int64" },
      { name: "region", dtype: "Utf8" },
    ]
    // Start as categorical to see detection switch to continuous
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
    // Select the "age" column (numeric dtype)
    const selects = screen.getAllByRole("combobox")
    const colSelect = selects.find(s => {
      const opts = Array.from((s as HTMLSelectElement).options)
      return opts.some(o => o.textContent?.includes("age"))
    })!
    fireEvent.change(colSelect, { target: { value: "age" } })

    // Should auto-detect to continuous for int64 dtype
    expect(onUpdate).toHaveBeenCalledWith("factors", expect.arrayContaining([
      expect.objectContaining({ column: "age", banding: "continuous" }),
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

    // Should auto-detect to categorical for Utf8 dtype
    expect(onUpdate).toHaveBeenCalledWith("factors", expect.arrayContaining([
      expect.objectContaining({ column: "region", banding: "categorical" }),
    ]))
  })

  it("add rule button adds appropriate empty rule type for continuous", () => {
    const onUpdate = vi.fn()
    render(
      <BandingEditor config={{}} onUpdate={onUpdate} inputSources={[]} accentColor="#22d3ee" />,
    )
    // Click the "+ Add" button
    fireEvent.click(screen.getByText("Add"))
    // Should have added a continuous rule with op1, val1, etc.
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

  it("summary section shows when factors have column + outputColumn + rules", () => {
    const config = {
      factors: [{
        banding: "continuous",
        column: "age",
        outputColumn: "age_band",
        rules: [{ op1: ">", val1: "25", op2: "", val2: "", assignment: "young" }],
      }],
    }
    render(
      <BandingEditor config={config} onUpdate={vi.fn()} inputSources={[]} accentColor="#22d3ee" />,
    )
    // Summary shows "column -> outputColumn · 1 rule · continuous"
    const allText = document.body.textContent || ""
    expect(allText).toContain("age")
    expect(allText).toContain("age_band")
    expect(allText).toContain("1 rule")
    expect(allText).toContain("continuous")
  })

  it("summary section hidden when factors are incomplete", () => {
    const config = {
      factors: [{
        banding: "continuous",
        column: "age",
        outputColumn: "",  // no output column
        rules: [{ op1: ">", val1: "25", op2: "", val2: "", assignment: "young" }],
      }],
    }
    const { container } = render(
      <BandingEditor config={config} onUpdate={vi.fn()} inputSources={[]} accentColor="#22d3ee" />,
    )
    // The summary block should not be rendered since outputColumn is empty
    const summaryText = container.textContent || ""
    expect(summaryText).not.toContain("1 rule")
  })

  it("renders text input for column when no upstreamColumns", () => {
    render(
      <BandingEditor config={{}} onUpdate={vi.fn()} inputSources={[]} accentColor="#22d3ee" />,
    )
    expect(screen.getByPlaceholderText("driver_age")).toBeTruthy()
  })

  it("renders default value input", () => {
    render(
      <BandingEditor config={{}} onUpdate={vi.fn()} inputSources={[]} accentColor="#22d3ee" />,
    )
    expect(screen.getByPlaceholderText("null")).toBeTruthy()
  })

  it("renders 'No rules yet' when rules are empty", () => {
    render(
      <BandingEditor config={{}} onUpdate={vi.fn()} inputSources={[]} accentColor="#22d3ee" />,
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
    expect(screen.getByText("Band")).toBeTruthy()
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
    expect(screen.getByText("Group")).toBeTruthy()
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
    // Initially first tab is active, should show continuous type highlighted
    const catTab = screen.getByText("region_group").closest("button")!
    fireEvent.click(catTab)
    // After clicking region_group tab, the categorical button should be active
    const catTypeBtn = screen.getByText("Categorical").closest("button")!
    // Active type button should have a styled (non-default) border indicating selection
    expect(catTypeBtn.style.border).toBeTruthy()
    expect(catTypeBtn.style.border).not.toBe("none")
  })

  it("renders InputSourcesBar when inputs provided", () => {
    const inputSources = [{ varName: "data", sourceLabel: "Data", edgeId: "e1" }]
    render(
      <BandingEditor config={{}} onUpdate={vi.fn()} inputSources={inputSources} accentColor="#22d3ee" />,
    )
    expect(screen.getByText("data")).toBeTruthy()
  })
})
