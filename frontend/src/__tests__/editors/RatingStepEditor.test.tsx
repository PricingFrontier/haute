/**
 * Render tests for RatingStepEditor.
 *
 * Tests: renders with default config, "select at least one factor" message,
 * adding a factor shows OneWayEditor, adding second factor shows TwoWayGrid,
 * adding/removing tables, operation select for 2+ tables, rebuild button.
 */
import { describe, it, expect, vi, afterEach } from "vitest"
import { render as rtlRender, screen, fireEvent, cleanup, within, waitFor } from "@testing-library/react"
import RatingStepEditor from "../../panels/editors/RatingStepEditor"
import type { SimpleNode, SimpleEdge } from "../../panels/editors/_shared"
import { GraphProvider } from "../../panels/GraphContext"
import useUIStore from "../../stores/useUIStore"

/**
 * Renders RatingStepEditor wrapped in a GraphProvider.  Accepts `allNodes` /
 * `edges` overrides that flow via context rather than direct props (post
 * Phase 2 Package 3C - graph data lives in context).
 */
function render(
  element: React.ReactElement,
  opts: { allNodes?: SimpleNode[]; edges?: SimpleEdge[] } = {},
) {
  return rtlRender(
    <GraphProvider allNodes={opts.allNodes ?? []} edges={opts.edges ?? []}>
      {element}
    </GraphProvider>,
  )
}

function getCodeEditorText(container: HTMLElement) {
  return container.querySelector(".cm-content")?.textContent ?? ""
}

// Helpers

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

function makeBreakpointBandingNode(outputColumn: string, labels: string[]): SimpleNode {
  return {
    id: `banding_${outputColumn}`,
    data: {
      label: `Banding ${outputColumn}`,
      description: "",
      nodeType: "banding",
      config: {
        factors: [{
          banding: "breakpoints",
          column: outputColumn.replace(/_band$/, ""),
          outputColumn,
          rules: labels.map((label, i) => ({
            boundary: i === labels.length - 1 ? "" : String((i + 1) * 10),
            label,
          })),
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

afterEach(() => {
  cleanup()
  useUIStore.setState({ ratingStepEditorSections: {}, explorePanes: {}, explorePreviewPanes: {} })
})

// Tests

describe("RatingStepEditor", () => {
  it("warns once for configured zero-level Banding outputs without restoring stale levels", () => {
    render(
      <RatingStepEditor
        config={{
          tables: [{
            factors: ["empty_one"], outputColumn: "factor", defaultValue: "1.0",
            entries: [{ empty_one: "stale", value: 1.2 }],
          }],
        }}
        onUpdate={vi.fn()}
        inputSources={[]}
        accentColor="#14b8a6"
      />,
      { allNodes: [
        {
          id: "banding_empty", data: {
            label: "Banding", description: "", nodeType: "banding", config: { factors: [
              { banding: "continuous", outputColumn: "empty_one", rules: [] },
              { banding: "categorical", outputColumn: "empty_two", rules: [{}] },
              { banding: "breakpoints", outputColumn: "healthy", rules: [{ label: "Known" }] },
            ] },
          },
        },
      ] },
    )
    expect(screen.getByRole("alert")).toHaveTextContent(/empty_one.*empty_two/)
    const addFactor = screen.getByRole("combobox", { name: "Add factor" })
    expect(Array.from((addFactor as HTMLSelectElement).options).map(option => option.value)).toContain("healthy")
    expect(Array.from((addFactor as HTMLSelectElement).options).map(option => option.value)).not.toContain("empty_one")
  })

  it("renders with default empty table", () => {
    render(
      <RatingStepEditor
        config={{}}
        onUpdate={vi.fn()}
        inputSources={[]}

        accentColor="#14b8a6"
      />,
      { allNodes: [] }
    )
    expect(screen.getByText(/Rating Tables\s+.\s+1 table/)).toBeTruthy()
    expect(screen.getByRole("button", { name: /^Table 1 problem$/ })).toBeTruthy()
    expect(screen.queryByText("Table Name")).toBeNull()
    expect(screen.queryByPlaceholderText("Age Factor")).toBeNull()
  })

  it("shows healthy and problem status markers in the rating table selector", () => {
    render(
      <RatingStepEditor
        config={{
          tables: [
            {
              factors: ["age_band"],
              outputColumn: "age_factor",
              defaultValue: "1.0",
              entries: [{ age_band: "young", value: 1.1 }],
            },
            {
              factors: ["age_band"],
              outputColumn: " ",
              defaultValue: "1.0",
              entries: [{ age_band: "young", value: 1.1 }],
            },
            {
              factors: [],
              outputColumn: "region_factor",
              defaultValue: "1.0",
              entries: [],
            },
            {
              factors: ["region"],
              outputColumn: "duplicate_factor",
              defaultValue: "1.0",
              entries: [{ region: "north", value: 1.0 }],
            },
            {
              factors: ["region"],
              outputColumn: "duplicate_factor",
              defaultValue: "1.0",
              entries: [{ region: "south", value: 1.0 }],
            },
          ],
        }}
        onUpdate={vi.fn()}
        inputSources={[]}
        accentColor="#14b8a6"
      />,
      { allNodes: BANDING_NODES },
    )

    const selector = screen.getByRole("group", { name: "Rating tables" })
    expect(within(selector).getByRole("button", { name: /^age_factor healthy$/i })).toContainHTML("var(--success)")
    expect(within(selector).getByRole("button", { name: /^Table 2 problem$/i })).toContainHTML("var(--warning-strong)")
    expect(within(selector).getByRole("button", { name: /^region_factor problem$/i })).toContainHTML("var(--warning-strong)")
    for (const duplicate of within(selector).getAllByRole("button", { name: /^duplicate_factor problem$/i })) {
      expect(duplicate).toContainHTML("var(--warning-strong)")
    }
  })

  it("summarises why a selected rating table has problems", () => {
    render(
      <RatingStepEditor
        config={{
          tables: [
            { factors: [], outputColumn: " ", defaultValue: "1.0", entries: [] },
            {
              factors: ["region"],
              outputColumn: "duplicate_factor",
              defaultValue: "1.0",
              entries: [{ region: "north", value: 1.0 }],
            },
            {
              factors: ["region"],
              outputColumn: "duplicate_factor",
              defaultValue: "1.0",
              entries: [{ region: "south", value: 1.0 }],
            },
          ],
        }}
        onUpdate={vi.fn()}
        inputSources={[]}
        accentColor="#14b8a6"
      />,
      { allNodes: BANDING_NODES },
    )

    expect(screen.getByText("Output column is required")).toBeTruthy()
    expect(screen.getByText(/Add at least one factor/i)).toBeTruthy()
    expect(screen.getByText(/Add at least one rating entry/i)).toBeTruthy()

    fireEvent.click(screen.getAllByRole("button", { name: /^duplicate_factor problem$/i })[0])

    expect(screen.getByLabelText("Output Column")).toHaveAttribute("aria-invalid", "true")
    expect(screen.getByText("Output column name must be unique")).toBeTruthy()
  })

  it("searches and selects rating tables when many are configured", () => {
    const tables = Array.from({ length: 24 }, (_, idx) => ({
      factors: ["age_band"],
      outputColumn: idx === 19 ? "telematics_adjustment" : `factor_${String(idx + 1).padStart(2, "0")}`,
      defaultValue: "1.0",
      entries: [{ age_band: "young", value: 1.0 }],
    }))

    render(
      <RatingStepEditor
        config={{ tables }}
        onUpdate={vi.fn()}
        inputSources={[]}
        accentColor="#14b8a6"
      />,
      { allNodes: BANDING_NODES },
    )

    expect(screen.queryByRole("tablist", { name: "Rating tables" })).toBeNull()

    const search = screen.getByRole("searchbox", { name: "Search rating tables" })
    fireEvent.change(search, { target: { value: "telematics" } })

    const selector = screen.getByRole("group", { name: "Rating tables" })
    expect(within(selector).getByRole("button", { name: /^telematics_adjustment healthy$/i })).toBeTruthy()
    expect(within(selector).queryByRole("button", { name: /factor_01/i })).toBeNull()
    expect(within(selector).getByRole("button", { name: /^telematics_adjustment healthy$/i })).toHaveAttribute("aria-pressed", "true")
    expect(screen.getByLabelText("Output Column")).toHaveValue("telematics_adjustment")

    fireEvent.click(within(selector).getByRole("button", { name: /^telematics_adjustment healthy$/i }))

    expect(screen.getByLabelText("Output Column")).toHaveValue("telematics_adjustment")
  })

  it("persists only the output column on blur", () => {
    const onUpdate = vi.fn()
    render(
      <RatingStepEditor
        config={{
          tables: [{ factors: [], outputColumn: "", defaultValue: "1.0", entries: [] }],
        }}
        onUpdate={onUpdate}
        inputSources={[]}
        accentColor="#14b8a6"
      />,
      { allNodes: [] }
    )

    const outputColumnInput = screen.getByLabelText("Output Column")
    fireEvent.change(outputColumnInput, { target: { value: "age_factor" } })
    fireEvent.blur(outputColumnInput)

    expect(onUpdate).toHaveBeenCalledWith("tables", [
      expect.objectContaining({ outputColumn: "age_factor" }),
    ])
  })

  it("renders the top rating section selector without persisting section changes", () => {
    const onUpdate = vi.fn()
    render(
      <RatingStepEditor
        config={{}}
        onUpdate={onUpdate}
        inputSources={[]}
        accentColor="#14b8a6"
      />,
      { allNodes: [] }
    )

    expect(screen.getByRole("radiogroup", { name: "Rating section" })).toBeTruthy()
    expect(screen.getByRole("radio", { name: /Tables/ })).toHaveAttribute("aria-checked", "true")
    expect(screen.getByRole("radio", { name: /Combined/ })).toBeTruthy()
    expect(screen.getByRole("radio", { name: /Code/ })).toBeTruthy()

    fireEvent.click(screen.getByRole("radio", { name: /Combined/ }))
    expect(screen.getByRole("radio", { name: /Combined/ })).toHaveAttribute("aria-checked", "true")
    expect(onUpdate).not.toHaveBeenCalledWith("mode", "combined")
  })

  it("restores the last selected rating section when reopening the same node", () => {
    const config = {
      code: "df = df.with_columns(pl.lit(1).alias('manual_factor'))",
      tables: [
        { name: "Age Factor", factors: [], outputColumn: "age_factor", defaultValue: "1.0", entries: [] },
      ],
    }

    const { unmount } = render(
      <RatingStepEditor
        config={config}
        onUpdate={vi.fn()}
        inputSources={[]}
        upstreamColumns={[{ name: "age", dtype: "Float64" }]}
        accentColor="#14b8a6"
        nodeId="rating_1"
      />,
      { allNodes: [] },
    )

    expect(screen.getByRole("radio", { name: /Code set/ })).toHaveAttribute("aria-checked", "true")

    fireEvent.click(screen.getByRole("radio", { name: /Tables/ }))
    expect(screen.getByRole("radio", { name: /Tables/ })).toHaveAttribute("aria-checked", "true")
    expect(screen.getByText(/Rating Tables/)).toBeTruthy()

    unmount()

    render(
      <RatingStepEditor
        config={config}
        onUpdate={vi.fn()}
        inputSources={[]}
        upstreamColumns={[{ name: "age", dtype: "Float64" }]}
        accentColor="#14b8a6"
        nodeId="rating_1"
      />,
      { allNodes: [] },
    )

    expect(screen.getByRole("radio", { name: /Tables/ })).toHaveAttribute("aria-checked", "true")
    expect(screen.getByText(/Rating Tables/)).toBeTruthy()
    expect(screen.queryByText("Polars Code")).toBeNull()
  })

  it("scopes the remembered rating section to the node id", () => {
    const config = {
      code: "df = df.with_columns(pl.lit(1).alias('manual_factor'))",
      tables: [
        { name: "Age Factor", factors: [], outputColumn: "age_factor", defaultValue: "1.0", entries: [] },
      ],
    }

    const { unmount } = render(
      <RatingStepEditor
        config={config}
        onUpdate={vi.fn()}
        inputSources={[]}
        upstreamColumns={[{ name: "age", dtype: "Float64" }]}
        accentColor="#14b8a6"
        nodeId="rating_1"
      />,
      { allNodes: [] },
    )

    fireEvent.click(screen.getByRole("radio", { name: /Tables/ }))
    unmount()

    render(
      <RatingStepEditor
        config={config}
        onUpdate={vi.fn()}
        inputSources={[]}
        upstreamColumns={[{ name: "age", dtype: "Float64" }]}
        accentColor="#14b8a6"
        nodeId="rating_2"
      />,
      { allNodes: [] },
    )

    expect(screen.getByRole("radio", { name: /Code set/ })).toHaveAttribute("aria-checked", "true")
    expect(screen.getByText("Polars Code")).toBeTruthy()
  })

  it("'Select at least one factor' shown when no factors", () => {
    render(
      <RatingStepEditor
        config={{}}
        onUpdate={vi.fn()}
        inputSources={[]}

        accentColor="#14b8a6"
      />,
      { allNodes: [] }
    )
    expect(screen.getByText("Select at least one factor to populate the rating table")).toBeTruthy()
  })

  it("flags a blank table output column and does not show placeholder text", () => {
    render(
      <RatingStepEditor
        config={{}}
        onUpdate={vi.fn()}
        inputSources={[]}

        accentColor="#14b8a6"
      />,
      { allNodes: [] }
    )

    const outputColumnInput = screen.getByLabelText("Output Column")
    expect(outputColumnInput.getAttribute("placeholder")).toBeNull()
    expect(outputColumnInput).toHaveAttribute("aria-invalid", "true")
    expect(screen.getByText("Output column is required")).toBeTruthy()
  })

  it("does not flag a populated table output column", () => {
    render(
      <RatingStepEditor
        config={{
          tables: [{ name: "T1", factors: [], outputColumn: "age_factor", defaultValue: "1.0", entries: [] }],
        }}
        onUpdate={vi.fn()}
        inputSources={[]}

        accentColor="#14b8a6"
      />,
      { allNodes: [] }
    )

    const outputColumnInput = screen.getByLabelText("Output Column")
    expect(outputColumnInput.getAttribute("placeholder")).toBeNull()
    expect(outputColumnInput).toHaveAttribute("aria-invalid", "false")
    expect(screen.queryByText("Output column is required")).toBeNull()
  })

  it("allows the Combined section to have no configured output", () => {
    render(
      <RatingStepEditor
        config={{
          tables: [
            { name: "T1", factors: [], outputColumn: "age_factor", defaultValue: "1.0", entries: [] },
            { name: "T2", factors: [], outputColumn: "region_factor", defaultValue: "1.0", entries: [] },
          ],
        }}
        onUpdate={vi.fn()}
        inputSources={[]}

        accentColor="#14b8a6"
      />,
      { allNodes: [] }
    )

    fireEvent.click(screen.getByRole("radio", { name: /Combined/ }))
    expect(screen.getByText("No combined output")).toBeTruthy()
    expect(screen.queryByLabelText("Combined Output Column")).toBeNull()
    expect(screen.queryByText("Combined output column is required")).toBeNull()
  })

  it("flags an explicitly blank combined output column and does not show placeholder text", () => {
    render(
      <RatingStepEditor
        config={{
          combinedOutputs: [{ outputColumn: "", operation: "multiply", baseValue: "1.0" }],
        }}
        onUpdate={vi.fn()}
        inputSources={[]}
        accentColor="#14b8a6"
      />,
      { allNodes: [] }
    )

    const combinedOutputInput = screen.getByLabelText("Combined Output Column")
    expect(combinedOutputInput.getAttribute("placeholder")).toBeNull()
    expect(combinedOutputInput).toHaveAttribute("aria-invalid", "true")
    expect(screen.getByText("Combined output column is required")).toBeTruthy()
  })

  it("surfaces invalid configured combined outputs without masking them", () => {
    render(
      <RatingStepEditor
        config={{
          combinedOutputs: [
            { outputColumn: "", operation: "divide" },
            { outputColumn: "missing_base", operation: "multiply" },
          ],
        }}
        onUpdate={vi.fn()}
        inputSources={[]}
        accentColor="#14b8a6"
      />,
      { allNodes: [] }
    )

    expect(screen.getByLabelText("Combined Output Column")).toHaveAttribute("aria-invalid", "true")
    expect(screen.getByText("Combined output column is required")).toBeTruthy()
    expect(screen.getByDisplayValue("divide")).toHaveAttribute("aria-invalid", "true")
    expect(screen.getByText("Operation is not supported")).toBeTruthy()
    expect(screen.getByLabelText("Base Value")).toHaveAttribute("aria-invalid", "true")
    expect(screen.getByText("Base value is required")).toBeTruthy()
    expect(screen.queryByText("combined_1")).toBeNull()

    expect(screen.getByRole("tab", { name: /missing_base/ })).toContainHTML("var(--warning-strong)")
  })

  it("treats whitespace-only table output columns as blank", () => {
    render(
      <RatingStepEditor
        config={{
          tables: [
            { name: "T1", factors: [], outputColumn: "   ", defaultValue: "1.0", entries: [] },
            { name: "T2", factors: [], outputColumn: "region_factor", defaultValue: "1.0", entries: [] },
          ],
        }}
        onUpdate={vi.fn()}
        inputSources={[]}

        accentColor="#14b8a6"
      />,
      { allNodes: [] }
    )

    expect(screen.getByLabelText("Output Column")).toHaveAttribute("aria-invalid", "true")
    expect(screen.getByText("Output column is required")).toBeTruthy()
  })

  it("treats whitespace-only combined output columns as blank", () => {
    render(
      <RatingStepEditor
        config={{
          combinedOutputs: [{ outputColumn: "\t ", operation: "multiply", baseValue: "1.0" }],
        }}
        onUpdate={vi.fn()}
        inputSources={[]}

        accentColor="#14b8a6"
      />,
      { allNodes: [] }
    )

    expect(screen.getByLabelText("Combined Output Column")).toHaveAttribute("aria-invalid", "true")
    expect(screen.getByText("Combined output column is required")).toBeTruthy()
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

        accentColor="#14b8a6"
      />,
      { allNodes: BANDING_NODES }
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

        accentColor="#14b8a6"
      />,
      { allNodes: BANDING_NODES }
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

        accentColor="#14b8a6"
      />,
      { allNodes: [] }
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
      expect.objectContaining({ outputColumn: "" }),
      expect.objectContaining({ outputColumn: "" }),
    ]))
  })

  it("removing a table when >1 tables", () => {
    const onUpdate = vi.fn()
    const config = {
      tables: [
        { factors: [], outputColumn: "age_factor", defaultValue: "1.0", entries: [] },
        { factors: [], outputColumn: "region_factor", defaultValue: "1.0", entries: [] },
      ],
    }
    render(
      <RatingStepEditor
        config={config}
        onUpdate={onUpdate}
        inputSources={[]}

        accentColor="#14b8a6"
      />,
      { allNodes: [] }
    )
    expect(screen.getByRole("button", { name: /^age_factor problem$/ })).toBeTruthy()
    expect(screen.getByRole("button", { name: /^region_factor problem$/ })).toBeTruthy()

    const selectionBtn = screen.getByRole("button", { name: /^age_factor problem$/ })
    expect(within(selectionBtn).queryByRole("button", { name: /Remove/ })).toBeNull()

    const removeBtn = screen.getByRole("button", { name: "Remove age_factor table" })
    expect(removeBtn).toBeTruthy()
    fireEvent.click(removeBtn)

    // Should call onUpdate with only region_factor remaining.
    expect(onUpdate).toHaveBeenCalledWith("tables", [
      expect.objectContaining({ outputColumn: "region_factor" }),
    ])
  })

  it("cannot remove last table", () => {
    const config = {
      tables: [
        { factors: [], outputColumn: "only_factor", defaultValue: "1.0", entries: [] },
      ],
    }
    render(
      <RatingStepEditor
        config={config}
        onUpdate={vi.fn()}
        inputSources={[]}

        accentColor="#14b8a6"
      />,
      { allNodes: [] }
    )
    expect(screen.getByRole("button", { name: /^only_factor problem$/ })).toBeTruthy()
    expect(screen.queryByRole("button", { name: "Remove only_factor table" })).toBeNull()
  })

  it("operation select (multiply/add/min/max) shown when 2+ tables", () => {
    const config = {
      tables: [
        { name: "T1", factors: [], outputColumn: "af", defaultValue: "1.0", entries: [] },
        { name: "T2", factors: [], outputColumn: "rf", defaultValue: "1.0", entries: [] },
      ],
      combinedOutputs: [
        { outputColumn: "combined", operation: "multiply", baseValue: "1.0" },
      ],
    }
    render(
      <RatingStepEditor
        config={config}
        onUpdate={vi.fn()}
        inputSources={[]}

        accentColor="#14b8a6"
      />,
      { allNodes: [] }
    )
    expect(screen.getByRole("tablist", { name: "Combined outputs" })).toBeTruthy()
    expect(screen.queryByRole("tablist", { name: "Rating tables" })).toBeNull()
    // The operation select should have all 4 options
    const operationSelect = screen.getByDisplayValue("× Multiply") as HTMLSelectElement
    expect(operationSelect).toBeTruthy()
    const optionTexts = Array.from(operationSelect.options).map(o => o.text)
    expect(optionTexts).toContain("× Multiply")
    expect(optionTexts).toContain("+ Add")
    expect(optionTexts).toContain("Min")
    expect(optionTexts).toContain("Max")
    expect(optionTexts).not.toContain("× Multiply (relativities)")
    expect(optionTexts).not.toContain("+ Add (loadings)")
  })

  it("shows operation select for a configured one-table combined output", () => {
    render(
      <RatingStepEditor
        config={{
          tables: [
            { name: "T1", factors: [], outputColumn: "af", defaultValue: "1.0", entries: [] },
          ],
          combinedOutputs: [
            { outputColumn: "combined", operation: "multiply", baseValue: "1.0" },
          ],
        }}
        onUpdate={vi.fn()}
        inputSources={[]}

        accentColor="#14b8a6"
      />,
      { allNodes: [] }
    )
    expect(screen.getByRole("tablist", { name: "Combined outputs" })).toBeTruthy()
    expect(screen.getByDisplayValue("× Multiply")).toBeTruthy()
  })

  it("changing operation calls onUpdate", () => {
    const onUpdate = vi.fn()
    const config = {
      tables: [
        { name: "T1", factors: [], outputColumn: "af", defaultValue: "1.0", entries: [] },
        { name: "T2", factors: [], outputColumn: "rf", defaultValue: "1.0", entries: [] },
      ],
      combinedOutputs: [
        { outputColumn: "combined", operation: "multiply", baseValue: "1.0" },
      ],
    }
    render(
      <RatingStepEditor
        config={config}
        onUpdate={onUpdate}
        inputSources={[]}

        accentColor="#14b8a6"
      />,
      { allNodes: [] }
    )
    const operationSelect = screen.getByDisplayValue("× Multiply")
    fireEvent.change(operationSelect, { target: { value: "add" } })
    expect(onUpdate).toHaveBeenCalledWith({
      combinedOutputs: [expect.objectContaining({ operation: "add", baseValue: "0.0" })],
    })
  })

  it("removing the last combined output leaves the combined section empty", () => {
    const onUpdate = vi.fn()
    render(
      <RatingStepEditor
        config={{
          combinedOutputs: [
            { outputColumn: "combined", operation: "multiply", baseValue: "1.0" },
          ],
        }}
        onUpdate={onUpdate}
        inputSources={[]}
        accentColor="#14b8a6"
      />,
      { allNodes: [] }
    )

    fireEvent.click(screen.getByLabelText("Remove combined output"))
    expect(onUpdate).toHaveBeenCalledWith({ combinedOutputs: [] })
  })

  it("supports multiple combined outputs with a banding-style selector row", () => {
    const onUpdate = vi.fn()
    const config = {
      tables: [
        { name: "T1", factors: [], outputColumn: "age_factor", defaultValue: "1.0", entries: [] },
        { name: "T2", factors: [], outputColumn: "region_factor", defaultValue: "1.0", entries: [] },
      ],
      combinedOutputs: [
        { outputColumn: "technical_price", operation: "multiply", baseValue: "1.0" },
        { outputColumn: "loaded_price", operation: "add", baseValue: "100.0" },
      ],
    }

    render(
      <RatingStepEditor
        config={config}
        onUpdate={onUpdate}
        inputSources={[]}
        accentColor="#14b8a6"
      />,
      { allNodes: [] }
    )

    const tablist = screen.getByRole("tablist", { name: "Combined outputs" })
    expect(screen.getByRole("radio", { name: /Combined set/ })).toBeTruthy()
    expect(tablist.className).toContain("overflow-x-auto")
    expect(screen.getAllByText("technical_price").length).toBeGreaterThan(0)
    expect(screen.getByText("loaded_price")).toBeTruthy()
    expect(screen.getByLabelText("Base Value")).toHaveValue(1)

    fireEvent.keyDown(screen.getByRole("tab", { name: /technical_price/ }), { key: "ArrowRight" })
    expect(screen.getByRole("tab", { name: /loaded_price/ })).toHaveAttribute("aria-selected", "true")

    const baseValueInput = screen.getByLabelText("Base Value")
    fireEvent.change(baseValueInput, { target: { value: "275.50" } })
    fireEvent.blur(baseValueInput)
    expect(onUpdate).toHaveBeenCalledWith(expect.objectContaining({
      combinedOutputs: expect.arrayContaining([
        expect.objectContaining({ outputColumn: "loaded_price", baseValue: "275.50" }),
      ]),
    }))

    fireEvent.click(screen.getByLabelText("Add combined output"))
    expect(onUpdate).toHaveBeenCalledWith(expect.objectContaining({
      combinedOutputs: expect.arrayContaining([
        expect.objectContaining({ outputColumn: "combined_3" }),
      ]),
    }))
  })

  it("flags duplicate combined output names and auto-generates unused names", () => {
    const onUpdate = vi.fn()
    const config = {
      tables: [
        { name: "T1", factors: [], outputColumn: "age_factor", defaultValue: "1.0", entries: [] },
      ],
      combinedOutputs: [
        { outputColumn: "age_factor", operation: "multiply", baseValue: "1.0" },
        { outputColumn: "combined_3", operation: "multiply", baseValue: "1.0" },
      ],
    }

    render(
      <RatingStepEditor
        config={config}
        onUpdate={onUpdate}
        inputSources={[]}
        accentColor="#14b8a6"
      />,
      { allNodes: [] }
    )

    expect(screen.getByLabelText("Combined Output Column")).toHaveAttribute("aria-invalid", "true")
    expect(screen.getByText("Output column name must be unique")).toBeTruthy()

    fireEvent.click(screen.getByLabelText("Add combined output"))
    expect(onUpdate).toHaveBeenCalledWith(expect.objectContaining({
      combinedOutputs: expect.arrayContaining([
        expect.objectContaining({ outputColumn: "combined_4" }),
      ]),
    }))
  })

  it("renders a Polars Code box in code mode", async () => {
    const { container } = render(
      <RatingStepEditor
        config={{ code: "df = df.with_columns(pl.lit(1).alias('x'))" }}
        onUpdate={vi.fn()}
        inputSources={[]}
        upstreamColumns={[{ name: "age", dtype: "Float64" }]}
        accentColor="#14b8a6"
      />,
      { allNodes: [] }
    )

    expect(screen.getByText("Polars Code")).toBeTruthy()
    expect(screen.getByRole("radio", { name: /Code set/ })).toBeTruthy()
    expect(screen.getByTestId("code-editor-wrapper")).toBeTruthy()
    await waitFor(() => expect(getCodeEditorText(container)).toContain("df = df.with_columns"))
    expect(getCodeEditorText(container)).not.toContain("apply_rating_step_from_config")
  })

  it("does not synthesize rating scaffold in an empty code box", () => {
    const { container } = render(
      <RatingStepEditor
        config={{}}
        onUpdate={vi.fn()}
        inputSources={[]}
        upstreamColumns={[{ name: "age", dtype: "Float64" }]}
        accentColor="#14b8a6"
      />,
      { allNodes: [] }
    )

    fireEvent.click(screen.getByRole("radio", { name: /Code/ }))
    const editorText = getCodeEditorText(container)
    expect(screen.getByTestId("code-editor-wrapper")).toBeTruthy()
    expect(editorText).not.toContain("apply_rating_step_from_config")
    expect(editorText).not.toContain("return df")
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

        accentColor="#14b8a6"
      />,
      { allNodes: BANDING_NODES }
    )
    expect(screen.getByText(/Rebuild from factor levels/)).toBeTruthy()
  })

  it("rebuild button not shown when no factors selected", () => {
    render(
      <RatingStepEditor
        config={{}}
        onUpdate={vi.fn()}
        inputSources={[]}

        accentColor="#14b8a6"
      />,
      { allNodes: [] }
    )
    expect(screen.queryByText(/Rebuild from factor levels/)).toBeNull()
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

        accentColor="#14b8a6"
      />,
      { allNodes: BANDING_NODES }
    )
    fireEvent.click(screen.getByText(/Rebuild from factor levels/))
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

  it("shows factor count label", () => {
    render(
      <RatingStepEditor
        config={{}}
        onUpdate={vi.fn()}
        inputSources={[]}

        accentColor="#14b8a6"
      />,
      { allNodes: BANDING_NODES }
    )
    expect(screen.getByText("Factors (0/3)")).toBeTruthy()
  })

  it("shows factor dropdown with available banding columns", () => {
    render(
      <RatingStepEditor
        config={{}}
        onUpdate={vi.fn()}
        inputSources={[]}

        accentColor="#14b8a6"
      />,
      { allNodes: BANDING_NODES }
    )
    const addSelect = screen.getByRole("combobox") as HTMLSelectElement
    const options = Array.from(addSelect.options).map(o => o.textContent)
    expect(options).toContain("+ Add factor...")
    expect(options.some(o => o?.includes("age_band"))).toBe(true)
    expect(options.some(o => o?.includes("region"))).toBe(true)
  })

  it("shows raw string/categorical preview columns in the add factor dropdown but hides numeric columns", () => {
    render(
      <RatingStepEditor
        config={{}}
        onUpdate={vi.fn()}
        inputSources={[]}
        accentColor="#14b8a6"
        previewRows={[
          { channel: "direct", segment: "retail", premium: 100.25 },
          { channel: "broker", segment: "fleet", premium: 140.5 },
          { channel: "direct", segment: "retail", premium: 99.99 },
        ]}
        upstreamColumns={[
          { name: "channel", dtype: "String" },
          { name: "segment", dtype: "Categorical" },
          { name: "premium", dtype: "Float64" },
        ]}
      />,
      { allNodes: BANDING_NODES }
    )

    const addSelect = screen.getByRole("combobox") as HTMLSelectElement
    const options = Array.from(addSelect.options).map(o => o.textContent)
    expect(options.some(o => o?.includes("age_band"))).toBe(true)
    expect(options.some(o => o?.includes("channel (2 levels)"))).toBe(true)
    expect(options.some(o => o?.includes("segment (2 levels)"))).toBe(true)
    expect(options.some(o => o?.includes("premium"))).toBe(false)
  })

  it("adding a raw string factor rebuilds entries from its distinct preview values", () => {
    const onUpdate = vi.fn()
    render(
      <RatingStepEditor
        config={{}}
        onUpdate={onUpdate}
        inputSources={[]}
        accentColor="#14b8a6"
        previewRows={[
          { channel: "direct", premium: 100.25 },
          { channel: "broker", premium: 140.5 },
          { channel: "direct", premium: 99.99 },
        ]}
        upstreamColumns={[
          { name: "channel", dtype: "String" },
          { name: "premium", dtype: "Float64" },
        ]}
      />,
      { allNodes: BANDING_NODES }
    )

    fireEvent.change(screen.getByRole("combobox"), { target: { value: "channel" } })

    expect(onUpdate).toHaveBeenCalledWith("tables", expect.arrayContaining([
      expect.objectContaining({
        factors: ["channel"],
        entries: [
          { channel: "direct", value: 1.0 },
          { channel: "broker", value: 1.0 },
        ],
      }),
    ]))
  })

  it("uses preview-derived raw string factor levels without showing an explanatory warning", () => {
    render(
      <RatingStepEditor
        config={{
          tables: [{
            name: "Channel Factor",
            factors: ["channel"],
            outputColumn: "channel_factor",
            defaultValue: "1.0",
            entries: [
              { channel: "direct", value: 1.0 },
              { channel: "broker", value: 1.0 },
            ],
          }],
        }}
        onUpdate={vi.fn()}
        inputSources={[]}
        accentColor="#14b8a6"
        previewRows={[
          { channel: "direct" },
          { channel: "broker" },
        ]}
        upstreamColumns={[
          { name: "channel", dtype: "String" },
        ]}
      />,
      { allNodes: BANDING_NODES }
    )

    expect(screen.getByText("direct")).toBeTruthy()
    expect(screen.getByText("broker")).toBeTruthy()
    expect(screen.queryByText(/Raw levels for/)).not.toBeInTheDocument()
    expect(screen.queryByText(/Values not listed will use the default/)).not.toBeInTheDocument()
  })

  it("uses saved raw factor entries as levels before preview rows are available", () => {
    render(
      <RatingStepEditor
        config={{
          tables: [{
            name: "Channel Factor",
            factors: ["channel"],
            outputColumn: "channel_factor",
            defaultValue: "1.0",
            entries: [
              { channel: "direct", value: 1.05 },
              { channel: "broker", value: 0.95 },
            ],
          }],
        }}
        onUpdate={vi.fn()}
        inputSources={[]}
        accentColor="#14b8a6"
      />,
      { allNodes: BANDING_NODES }
    )

    expect(screen.getByText("direct")).toBeTruthy()
    expect(screen.getByText("broker")).toBeTruthy()
    expect(screen.queryByText(/Raw levels for/)).not.toBeInTheDocument()
  })

  it("uses preview and saved entries for unbanded channel and cover_type levels without raw-level helper UI", () => {
    render(
      <RatingStepEditor
        config={{
          tables: [{
            name: "Channel Cover Factor",
            factors: ["age_band", "channel", "cover_type"],
            outputColumn: "channel_cover_factor",
            defaultValue: "1.25",
            entries: [
              { age_band: "young", channel: "direct", cover_type: "third_party", value: 1.05 },
              { age_band: "young", channel: "aggregator", cover_type: "comprehensive", value: 0.95 },
            ],
          }],
        }}
        onUpdate={vi.fn()}
        inputSources={[]}
        accentColor="#14b8a6"
        previewRows={[
          { age_band: "young", channel: "direct", cover_type: "third_party" },
          { age_band: "mid", channel: "broker", cover_type: "comprehensive" },
        ]}
        upstreamColumns={[
          { name: "age_band", dtype: "String" },
          { name: "channel", dtype: "String" },
          { name: "cover_type", dtype: "String" },
        ]}
      />,
      { allNodes: BANDING_NODES }
    )

    expect(screen.getByText("third_party")).toBeTruthy()
    expect(screen.getByText("direct")).toBeTruthy()
    expect(screen.getByText("broker")).toBeTruthy()
    expect(screen.getByText("aggregator")).toBeTruthy()

    const unseenCombination = screen.getByRole("textbox", {
      name: "Relativity for age_band young and channel broker",
    }) as HTMLInputElement
    expect(unseenCombination.value).toBe("1.25")
    expect(screen.queryByText(/Raw levels for/)).not.toBeInTheDocument()
    expect(screen.queryByText(/Values not listed will use the default/)).not.toBeInTheDocument()
  })

  it("does not keep stale saved entries as levels for configured banding factors", () => {
    const onUpdate = vi.fn()
    render(
      <RatingStepEditor
        config={{
          tables: [{
            name: "Age Factor",
            factors: ["age_band"],
            outputColumn: "age_factor",
            defaultValue: "1.0",
            entries: [
              { age_band: "young", value: 1.1 },
              { age_band: "retired", value: 1.4 },
            ],
          }],
        }}
        onUpdate={onUpdate}
        inputSources={[]}
        accentColor="#14b8a6"
      />,
      { allNodes: BANDING_NODES }
    )

    expect(screen.getByText("young")).toBeTruthy()
    expect(screen.getByText("mid")).toBeTruthy()
    expect(screen.queryByText("retired")).toBeNull()

    fireEvent.click(screen.getByText(/Rebuild from factor levels/))
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
    const rebuiltTables = onUpdate.mock.calls.at(-1)?.[1] as { entries: Record<string, unknown>[] }[] | undefined
    const rebuiltTable = rebuiltTables?.[0]
    expect(rebuiltTable).toBeTruthy()
    expect(rebuiltTable!.entries.some((entry: Record<string, unknown>) => entry.age_band === "retired")).toBe(false)
  })

  it("shows breakpoint banding columns in the add factor dropdown", () => {
    render(
      <RatingStepEditor
        config={{}}
        onUpdate={vi.fn()}
        inputSources={[]}

        accentColor="#14b8a6"
      />,
      {
        allNodes: [
          makeBreakpointBandingNode("proposer_age_band", ["20-27", "28-34", "35+"]),
          makeBreakpointBandingNode("vehicle_age_band", ["1-3", "4-5", "6+"]),
          makeBandingNode("channel_band", ["direct", "broker"]),
        ],
      }
    )

    const addSelect = screen.getByRole("combobox") as HTMLSelectElement
    const options = Array.from(addSelect.options).map(o => o.textContent)
    expect(options.some(o => o?.includes("proposer_age_band"))).toBe(true)
    expect(options.some(o => o?.includes("vehicle_age_band"))).toBe(true)
    expect(options.some(o => o?.includes("channel_band"))).toBe(true)
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

        accentColor="#14b8a6"
      />,
      { allNodes: BANDING_NODES }
    )
    expect(screen.getByRole("button", { name: /^age_factor healthy$/ })).toBeTruthy()
    expect(screen.getByText(/2 entries/)).toBeTruthy()
  })

  it("renders input sources bar when inputs provided", () => {
    render(
      <RatingStepEditor
        config={{}}
        onUpdate={vi.fn()}
        inputSources={[{ sourceNodeId: "test-source", name: "source_data", sourceLabel: "Source Data", edgeId: "e1" }]}

        accentColor="#14b8a6"
      />,
      { allNodes: [] }
    )
    expect(screen.getByText("source_data")).toBeTruthy()
  })

  it("renders combination formula summary when 2+ tables have output columns", () => {
    const config = {
      tables: [
        { name: "T1", factors: [], outputColumn: "age_factor", defaultValue: "1.0", entries: [] },
        { name: "T2", factors: [], outputColumn: "region_factor", defaultValue: "1.0", entries: [] },
      ],
      combinedOutputs: [{ outputColumn: "combined", operation: "multiply", baseValue: "1.0" }],
    }
    render(
      <RatingStepEditor
        config={config}
        onUpdate={vi.fn()}
        inputSources={[]}

        accentColor="#14b8a6"
      />,
      { allNodes: [] }
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

        accentColor="#14b8a6"
      />,
      { allNodes: BANDING_NODES }
    )
    const addSelect = screen.getByRole("combobox")
    fireEvent.change(addSelect, { target: { value: "age_band" } })
    // Should update tables with the new factor
    expect(onUpdate).toHaveBeenCalledWith("tables", expect.arrayContaining([
      expect.objectContaining({ factors: ["age_band"] }),
    ]))
  })

  it("retains selected factor dtype metadata when adding a factor", () => {
    const onUpdate = vi.fn()
    render(
      <RatingStepEditor
        config={{
          tables: [{
            name: "T1",
            factors: ["age_band"],
            factorDtypes: { age_band: { kind: "Categorical" } },
            outputColumn: "age_factor",
            defaultValue: "1.0",
            entries: [],
          }],
        }}
        onUpdate={onUpdate}
        inputSources={[]}
        accentColor="#14b8a6"
      />,
      { allNodes: BANDING_NODES },
    )

    fireEvent.change(screen.getAllByRole("combobox").at(-1)!, { target: { value: "region" } })

    expect(onUpdate).toHaveBeenCalledWith("tables", expect.arrayContaining([
      expect.objectContaining({
        factors: ["age_band", "region"],
        factorDtypes: { age_band: { kind: "Categorical" } },
      }),
    ]))
  })

  it("removes deselected factor dtype metadata with the factor", () => {
    const onUpdate = vi.fn()
    const { container } = render(
      <RatingStepEditor
        config={{
          tables: [{
            name: "T1",
            factors: ["age_band", "region"],
            factorDtypes: {
              age_band: { kind: "Categorical" },
              region: { kind: "String" },
            },
            outputColumn: "age_factor",
            defaultValue: "1.0",
            entries: [],
          }],
        }}
        onUpdate={onUpdate}
        inputSources={[]}
        accentColor="#14b8a6"
      />,
      { allNodes: BANDING_NODES },
    )

    fireEvent.click(container.querySelector("button.icon-danger-btn")!)

    expect(onUpdate).toHaveBeenCalledWith("tables", expect.arrayContaining([
      expect.objectContaining({
        factors: ["region"],
        factorDtypes: { region: { kind: "String" } },
      }),
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

        accentColor="#14b8a6"
      />,
      { allNodes: BANDING_NODES }
    )
    // Factors count should show 3/3
    expect(screen.getByText("Factors (3/3)")).toBeTruthy()
    expect(screen.getByRole("combobox", { name: "Factor 1" })).toHaveValue("age_band")
    expect(screen.getByRole("combobox", { name: "Factor 2" })).toHaveValue("region")
    expect(screen.getByRole("combobox", { name: "Factor 3" })).toHaveValue("vehicle_type")
    // The 3rd factor (vehicle_type) should appear as the slice selector label
    expect(screen.getByText("vehicle_type")).toBeTruthy()
    expect(screen.getByRole("combobox", { name: "vehicle_type slice" })).toBeInTheDocument()
  })
})
