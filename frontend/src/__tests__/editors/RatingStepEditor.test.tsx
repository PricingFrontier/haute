/**
 * Render tests for RatingStepEditor.
 *
 * Tests: renders with default config, "select at least one factor" message,
 * adding a factor shows OneWayEditor, adding second factor shows TwoWayGrid,
 * adding/removing tables, operation select for 2+ tables, rebuild button.
 */
import { describe, it, expect, vi, afterEach } from "vitest"
import { render, screen, fireEvent, cleanup } from "@testing-library/react"
import RatingStepEditor from "../../panels/editors/RatingStepEditor"
import type { SimpleNode } from "../../panels/editors/_shared"

// ─── Helpers ──────────────────────────────────────────────────────

/** Create a banding node that provides levels for the rating editor. */
function makeBandingNode(outputColumn: string, assignments: string[]): SimpleNode {
  return {
    id: `banding_${outputColumn}`,
    data: {
      label: `Banding ${outputColumn}`,
      description: "",
      nodeType: "banding",
      config: {
        factors: [{
          banding: "continuous",
          column: outputColumn,
          outputColumn,
          rules: assignments.map(a => ({ op1: ">", val1: "0", op2: "", val2: "", assignment: a })),
        }],
      },
    },
  }
}

const BANDING_NODES: SimpleNode[] = [
  makeBandingNode("age_band", ["young", "mid", "old"]),
  makeBandingNode("region", ["north", "south"]),
  makeBandingNode("vehicle_type", ["car", "van", "truck"]),
]

afterEach(cleanup)

// ─── Tests ────────────────────────────────────────────────────────

describe("RatingStepEditor", () => {
  it("renders with default empty table", () => {
    render(
      <RatingStepEditor
        config={{}}
        onUpdate={vi.fn()}
        inputSources={[]}
        allNodes={[]}
        accentColor="#14b8a6"
      />,
    )
    expect(screen.getByText("Rating Tables · 1 table")).toBeTruthy()
    // Table 1 tab should be visible
    expect(screen.getByText("Table 1")).toBeTruthy()
    // Table name input should have "Table 1"
    const nameInput = screen.getByPlaceholderText("Age Factor") as HTMLInputElement
    expect(nameInput.value).toBe("Table 1")
  })

  it("'Select at least one factor' shown when no factors", () => {
    render(
      <RatingStepEditor
        config={{}}
        onUpdate={vi.fn()}
        inputSources={[]}
        allNodes={[]}
        accentColor="#14b8a6"
      />,
    )
    expect(screen.getByText("Select at least one factor to populate the rating table")).toBeTruthy()
  })

  it("adding a factor shows OneWayEditor (1 factor)", () => {
    const config = {
      tables: [{
        name: "T1",
        factors: ["age_band"],
        outputColumn: "af",
        defaultValue: "1.0",
        entries: [
          { age_band: "young", value: 1.1 },
          { age_band: "mid", value: 1.0 },
          { age_band: "old", value: 0.9 },
        ],
      }],
    }
    render(
      <RatingStepEditor
        config={config}
        onUpdate={vi.fn()}
        inputSources={[]}
        allNodes={BANDING_NODES}
        accentColor="#14b8a6"
      />,
    )
    // OneWayEditor renders "age_band" column header and "Relativity" header
    expect(screen.getByText("age_band")).toBeTruthy()
    expect(screen.getByText("Relativity")).toBeTruthy()
    // The "select at least one factor" message should NOT be shown
    expect(screen.queryByText("Select at least one factor to populate the rating table")).toBeNull()
  })

  it("adding second factor shows TwoWayGrid (2 factors)", () => {
    const config = {
      tables: [{
        name: "T1",
        factors: ["age_band", "region"],
        outputColumn: "combined",
        defaultValue: "1.0",
        entries: [
          { age_band: "young", region: "north", value: 1.1 },
          { age_band: "young", region: "south", value: 1.0 },
          { age_band: "mid", region: "north", value: 1.0 },
          { age_band: "mid", region: "south", value: 0.9 },
          { age_band: "old", region: "north", value: 0.8 },
          { age_band: "old", region: "south", value: 0.7 },
        ],
      }],
    }
    render(
      <RatingStepEditor
        config={config}
        onUpdate={vi.fn()}
        inputSources={[]}
        allNodes={BANDING_NODES}
        accentColor="#14b8a6"
      />,
    )
    // Factors count should show 2/3
    expect(screen.getByText("Factors (2/3)")).toBeTruthy()
    // TwoWayGrid shows column headers for the region levels
    expect(screen.getByText("north")).toBeTruthy()
    expect(screen.getByText("south")).toBeTruthy()
  })

  it("adding a table creates new tab", () => {
    const onUpdate = vi.fn()
    render(
      <RatingStepEditor
        config={{}}
        onUpdate={onUpdate}
        inputSources={[]}
        allNodes={[]}
        accentColor="#14b8a6"
      />,
    )
    // Find the "+" button for adding a table
    const addButtons = screen.getAllByRole("button")
    const addTableBtn = addButtons.find(b => {
      const svg = b.querySelector("svg")
      // The add-table button has a Plus icon and dashed border
      return svg && b.style.border?.includes("dashed")
    })
    expect(addTableBtn).toBeTruthy()
    fireEvent.click(addTableBtn!)

    // Should call onUpdate with tables array containing 2 tables
    expect(onUpdate).toHaveBeenCalledWith("tables", expect.arrayContaining([
      expect.objectContaining({ name: "Table 1" }),
      expect.objectContaining({ name: "Table 2" }),
    ]))
  })

  it("removing a table when >1 tables", () => {
    const onUpdate = vi.fn()
    const config = {
      tables: [
        { name: "Table A", factors: [], outputColumn: "", defaultValue: "1.0", entries: [] },
        { name: "Table B", factors: [], outputColumn: "", defaultValue: "1.0", entries: [] },
      ],
    }
    render(
      <RatingStepEditor
        config={config}
        onUpdate={onUpdate}
        inputSources={[]}
        allNodes={[]}
        accentColor="#14b8a6"
      />,
    )
    // Both tabs should be visible
    expect(screen.getByText("Table A")).toBeTruthy()
    expect(screen.getByText("Table B")).toBeTruthy()

    // Find the remove button (X icon) inside Table A tab
    const tableATab = screen.getByText("Table A").closest("[role='tab']")!
    const removeBtn = tableATab.querySelector("button[aria-label='Remove table']")
    expect(removeBtn).toBeTruthy()
    fireEvent.click(removeBtn!)

    // Should call onUpdate with only Table B remaining
    expect(onUpdate).toHaveBeenCalledWith("tables", [
      expect.objectContaining({ name: "Table B" }),
    ])
  })

  it("cannot remove last table", () => {
    const config = {
      tables: [
        { name: "Only Table", factors: [], outputColumn: "", defaultValue: "1.0", entries: [] },
      ],
    }
    render(
      <RatingStepEditor
        config={config}
        onUpdate={vi.fn()}
        inputSources={[]}
        allNodes={[]}
        accentColor="#14b8a6"
      />,
    )
    // The X remove icon should not be rendered when only 1 table
    const tableTab = screen.getByText("Only Table").closest("[role='tab']")!
    const removeSpan = tableTab.querySelector("button[aria-label='Remove table']")
    expect(removeSpan).toBeNull()
  })

  it("operation select (multiply/add/min/max) shown when 2+ tables", () => {
    const config = {
      tables: [
        { name: "T1", factors: [], outputColumn: "af", defaultValue: "1.0", entries: [] },
        { name: "T2", factors: [], outputColumn: "rf", defaultValue: "1.0", entries: [] },
      ],
    }
    render(
      <RatingStepEditor
        config={config}
        onUpdate={vi.fn()}
        inputSources={[]}
        allNodes={[]}
        accentColor="#14b8a6"
      />,
    )
    expect(screen.getByText("Combine")).toBeTruthy()
    // The operation select should have all 4 options
    const operationSelect = screen.getByDisplayValue("× Multiply (relativities)") as HTMLSelectElement
    expect(operationSelect).toBeTruthy()
    const optionTexts = Array.from(operationSelect.options).map(o => o.text)
    expect(optionTexts).toContain("× Multiply (relativities)")
    expect(optionTexts).toContain("+ Add (loadings)")
    expect(optionTexts).toContain("↓ Min")
    expect(optionTexts).toContain("↑ Max")
  })

  it("operation select not shown when only 1 table", () => {
    render(
      <RatingStepEditor
        config={{}}
        onUpdate={vi.fn()}
        inputSources={[]}
        allNodes={[]}
        accentColor="#14b8a6"
      />,
    )
    expect(screen.queryByText("Combine")).toBeNull()
  })

  it("changing operation calls onUpdate", () => {
    const onUpdate = vi.fn()
    const config = {
      tables: [
        { name: "T1", factors: [], outputColumn: "af", defaultValue: "1.0", entries: [] },
        { name: "T2", factors: [], outputColumn: "rf", defaultValue: "1.0", entries: [] },
      ],
    }
    render(
      <RatingStepEditor
        config={config}
        onUpdate={onUpdate}
        inputSources={[]}
        allNodes={[]}
        accentColor="#14b8a6"
      />,
    )
    const operationSelect = screen.getByDisplayValue("× Multiply (relativities)")
    fireEvent.change(operationSelect, { target: { value: "add" } })
    expect(onUpdate).toHaveBeenCalledWith("operation", "add")
  })

  it("rebuild button shown when factors selected", () => {
    const config = {
      tables: [{
        name: "T1",
        factors: ["age_band"],
        outputColumn: "af",
        defaultValue: "1.0",
        entries: [],
      }],
    }
    render(
      <RatingStepEditor
        config={config}
        onUpdate={vi.fn()}
        inputSources={[]}
        allNodes={BANDING_NODES}
        accentColor="#14b8a6"
      />,
    )
    expect(screen.getByText(/Rebuild from banding levels/)).toBeTruthy()
  })

  it("rebuild button not shown when no factors selected", () => {
    render(
      <RatingStepEditor
        config={{}}
        onUpdate={vi.fn()}
        inputSources={[]}
        allNodes={[]}
        accentColor="#14b8a6"
      />,
    )
    expect(screen.queryByText(/Rebuild from banding levels/)).toBeNull()
  })

  it("rebuild button triggers onUpdate with rebuilt entries", () => {
    const onUpdate = vi.fn()
    const config = {
      tables: [{
        name: "T1",
        factors: ["age_band"],
        outputColumn: "af",
        defaultValue: "1.0",
        entries: [],
      }],
    }
    render(
      <RatingStepEditor
        config={config}
        onUpdate={onUpdate}
        inputSources={[]}
        allNodes={BANDING_NODES}
        accentColor="#14b8a6"
      />,
    )
    fireEvent.click(screen.getByText(/Rebuild from banding levels/))
    // Should call onUpdate with tables containing rebuilt entries
    expect(onUpdate).toHaveBeenCalledWith("tables", expect.arrayContaining([
      expect.objectContaining({
        factors: ["age_band"],
        entries: expect.arrayContaining([
          expect.objectContaining({ age_band: "young" }),
          expect.objectContaining({ age_band: "mid" }),
          expect.objectContaining({ age_band: "old" }),
        ]),
      }),
    ]))
  })

  it("renders table tab buttons", () => {
    const config = {
      tables: [
        { name: "Table A", factors: [], outputColumn: "", defaultValue: "1.0", entries: [] },
        { name: "Table B", factors: [], outputColumn: "", defaultValue: "1.0", entries: [] },
      ],
    }
    render(
      <RatingStepEditor
        config={config}
        onUpdate={vi.fn()}
        inputSources={[]}
        allNodes={[]}
        accentColor="#14b8a6"
      />,
    )
    expect(screen.getByText("Table A")).toBeTruthy()
    expect(screen.getByText("Table B")).toBeTruthy()
  })

  it("shows factor count label", () => {
    render(
      <RatingStepEditor
        config={{}}
        onUpdate={vi.fn()}
        inputSources={[]}
        allNodes={BANDING_NODES}
        accentColor="#14b8a6"
      />,
    )
    expect(screen.getByText("Factors (0/3)")).toBeTruthy()
  })

  it("shows factor dropdown with available banding columns", () => {
    render(
      <RatingStepEditor
        config={{}}
        onUpdate={vi.fn()}
        inputSources={[]}
        allNodes={BANDING_NODES}
        accentColor="#14b8a6"
      />,
    )
    const addSelect = screen.getByRole("combobox") as HTMLSelectElement
    const options = Array.from(addSelect.options).map(o => o.textContent)
    expect(options).toContain("+ Add factor...")
    expect(options.some(o => o?.includes("age_band"))).toBe(true)
    expect(options.some(o => o?.includes("region"))).toBe(true)
  })

  it("shows entry count in summary", () => {
    const config = {
      tables: [{
        name: "T1",
        factors: ["age_band"],
        outputColumn: "age_factor",
        defaultValue: "1.0",
        entries: [
          { age_band: "young", value: 1.1 },
          { age_band: "old", value: 0.9 },
        ],
      }],
    }
    render(
      <RatingStepEditor
        config={config}
        onUpdate={vi.fn()}
        inputSources={[]}
        allNodes={BANDING_NODES}
        accentColor="#14b8a6"
      />,
    )
    expect(screen.getByText("age_factor")).toBeTruthy()
    expect(screen.getByText(/2 entries/)).toBeTruthy()
  })

  it("renders input sources bar when inputs provided", () => {
    render(
      <RatingStepEditor
        config={{}}
        onUpdate={vi.fn()}
        inputSources={[{ varName: "source_data", sourceLabel: "Source Data", edgeId: "e1" }]}
        allNodes={[]}
        accentColor="#14b8a6"
      />,
    )
    expect(screen.getByText("source_data")).toBeTruthy()
  })

  it("renders combination formula summary when 2+ tables have output columns", () => {
    const config = {
      tables: [
        { name: "T1", factors: [], outputColumn: "age_factor", defaultValue: "1.0", entries: [] },
        { name: "T2", factors: [], outputColumn: "region_factor", defaultValue: "1.0", entries: [] },
      ],
      operation: "multiply",
      combinedColumn: "combined",
    }
    render(
      <RatingStepEditor
        config={config}
        onUpdate={vi.fn()}
        inputSources={[]}
        allNodes={[]}
        accentColor="#14b8a6"
      />,
    )
    // Formula should show: combined = age_factor x region_factor
    const bodyText = document.body.textContent || ""
    expect(bodyText).toContain("combined")
    expect(bodyText).toContain("age_factor")
    expect(bodyText).toContain("region_factor")
  })

  it("adding a factor via select calls onUpdate with tables", () => {
    const onUpdate = vi.fn()
    render(
      <RatingStepEditor
        config={{}}
        onUpdate={onUpdate}
        inputSources={[]}
        allNodes={BANDING_NODES}
        accentColor="#14b8a6"
      />,
    )
    const addSelect = screen.getByRole("combobox")
    fireEvent.change(addSelect, { target: { value: "age_band" } })
    // Should update tables with the new factor
    expect(onUpdate).toHaveBeenCalledWith("tables", expect.arrayContaining([
      expect.objectContaining({ factors: ["age_band"] }),
    ]))
  })

  it("renders 3-way editor with slice dimension when 3 factors", () => {
    const config = {
      tables: [{
        name: "T1",
        factors: ["age_band", "region", "vehicle_type"],
        outputColumn: "combined",
        defaultValue: "1.0",
        entries: [
          { age_band: "young", region: "north", vehicle_type: "car", value: 1.0 },
          { age_band: "young", region: "south", vehicle_type: "car", value: 1.1 },
          { age_band: "mid", region: "north", vehicle_type: "car", value: 0.9 },
          { age_band: "mid", region: "south", vehicle_type: "car", value: 1.0 },
          { age_band: "old", region: "north", vehicle_type: "car", value: 0.8 },
          { age_band: "old", region: "south", vehicle_type: "car", value: 0.7 },
        ],
      }],
    }
    render(
      <RatingStepEditor
        config={config}
        onUpdate={vi.fn()}
        inputSources={[]}
        allNodes={BANDING_NODES}
        accentColor="#14b8a6"
      />,
    )
    // Factors count should show 3/3
    expect(screen.getByText("Factors (3/3)")).toBeTruthy()
    // The 3rd factor (vehicle_type) should appear as the slice selector label
    expect(screen.getByText("vehicle_type")).toBeTruthy()
  })
})
