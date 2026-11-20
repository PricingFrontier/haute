/**
 * Render tests for RatingStepEditor.
 *
 * Tests: renders with default config, "select at least one factor" message,
 * adding a factor shows OneWayEditor, adding second factor shows TwoWayGrid,
 * adding/removing tables, operation select for 2+ tables, rebuild button.
 */
import { describe, it, expect, vi, afterEach } from "vitest"
import { render as rtlRender, screen, fireEvent, cleanup, within } from "@testing-library/react"
import RatingStepEditor from "../../panels/editors/RatingStepEditor"
import type { SimpleNode, SimpleEdge } from "../../panels/editors/_shared"
import { GraphProvider } from "../../panels/GraphContext"
import useUIStore from "../../stores/useUIStore"
import { documentRequestScope } from "../../utils/requestGateway"

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
    <GraphProvider requestScope={documentRequestScope} allNodes={opts.allNodes ?? []} edges={opts.edges ?? []}>
      {element}
    </GraphProvider>,
  )
}

// Helpers

/** Opens a factor dropdown and reads its options, each as "name (N levels)", then closes it. */
function factorOptions(label: string): string[] {
  fireEvent.click(screen.getByRole("button", { name: label }))
  const menu = screen.getByRole("listbox", { name: `${label} options` })
  const options = within(menu).queryAllByRole("option").map(option => `${option.children[0]?.textContent} (${option.children[1]?.textContent})`)
  fireEvent.keyDown(screen.getByRole("combobox", { name: `Search ${label}` }), { key: "Escape" })
  return options
}

/** Chooses a column in a factor dropdown by clicking its option. */
function chooseFactor(label: string, column: string) {
  fireEvent.click(screen.getByRole("button", { name: label }))
  // An option reads its name, then its count of levels: match the name.
  const option = screen.getAllByRole("option").find(candidate => candidate.firstElementChild?.textContent === column)
  if (!option) throw new Error(`No ${column} option`)
  fireEvent.click(option)
}

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
          banding: "categorical",
          column: outputColumn,
          output_column: outputColumn,
          rules: assignments.map(a => ({ value: a, assignment: a })),
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
          output_column: outputColumn,
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
  useUIStore.setState({ explorePanes: {}, explorePreviewPanes: {} })
})

// Tests

describe("RatingStepEditor", () => {
  it("warns once for configured zero-level Banding outputs without restoring stale levels", () => {
    render(
      <RatingStepEditor
        section="tables"
        config={{
          tables: [{
            factors: ["empty_one"], output_column: "factor", default_value: "1.0",
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
              { banding: "breakpoints", output_column: "empty_one", rules: [] },
              { banding: "categorical", output_column: "empty_two", rules: [{}] },
              { banding: "breakpoints", output_column: "healthy", rules: [{ label: "Known" }] },
            ] },
          },
        },
      ] },
    )
    expect(screen.getByRole("alert")).toHaveTextContent(/empty_one.*empty_two/)
    const offered = factorOptions("Add factor")
    expect(offered.some(option => option.startsWith("healthy "))).toBe(true)
    expect(offered.some(option => option.startsWith("empty_one "))).toBe(false)
  })

  it("renders with default empty table", () => {
    render(
      <RatingStepEditor
        section="tables"
        config={{}}
        onUpdate={vi.fn()}
        inputSources={[]}

        accentColor="#14b8a6"
      />,
      { allNodes: [] }
    )
    expect(screen.queryByText(/Rating Tables/)).toBeNull()
    expect(screen.getByRole("button", { name: /^Table 1 problem$/ })).toBeTruthy()
    expect(screen.queryByText("Table Name")).toBeNull()
    expect(screen.queryByPlaceholderText("Age Factor")).toBeNull()
  })

  it("shows healthy and problem status markers in the rating table selector", () => {
    render(
      <RatingStepEditor
        section="tables"
        config={{
          tables: [
            {
              factors: ["age_band"],
              output_column: "age_factor",
              default_value: "1.0",
              entries: [{ age_band: "young", value: 1.1 }],
            },
            {
              factors: ["age_band"],
              output_column: " ",
              default_value: "1.0",
              entries: [{ age_band: "young", value: 1.1 }],
            },
            {
              factors: [],
              output_column: "region_factor",
              default_value: "1.0",
              entries: [],
            },
            {
              factors: ["region"],
              output_column: "duplicate_factor",
              default_value: "1.0",
              entries: [{ region: "north", value: 1.0 }],
            },
            {
              factors: ["region"],
              output_column: "duplicate_factor",
              default_value: "1.0",
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
    // The dot leads the row, beside the row's button.
    const row = (name: RegExp) => within(selector).getByRole("button", { name }).parentElement!
    expect(row(/^age_factor healthy$/i)).toContainHTML("var(--success)")
    expect(row(/^Table 2 problem$/i)).toContainHTML("var(--warning-strong)")
    expect(row(/^region_factor problem$/i)).toContainHTML("var(--warning-strong)")
    for (const duplicate of within(selector).getAllByRole("button", { name: /^duplicate_factor problem$/i })) {
      expect(duplicate.parentElement!).toContainHTML("var(--warning-strong)")
    }
  })

  describe("the table list", () => {
    const rated = (output_column: string, entries: Record<string, string | number>[]) => ({
      factors: ["age_band"],
      output_column,
      default_value: "1.0",
      entries,
    })
    const THREE = {
      tables: [
        rated("age_factor", [{ age_band: "young", value: 1.1 }]),
        rated("region_factor", [{ age_band: "young", value: 1.2 }, { age_band: "old", value: 0.9 }]),
        rated("cover_factor", []),
      ],
    }
    const tableRow = (name: string) =>
      within(screen.getByRole("group", { name: "Rating tables" }))
        .getByRole("button", { name: new RegExp(`^${name} (healthy|problem)$`) })
    const outputs = (onUpdate: ReturnType<typeof vi.fn>) =>
      (onUpdate.mock.lastCall![1] as { output_column: string }[]).map((table) => table.output_column)

    it("spells out each table's factors and entries, as Banding does its rules", () => {
      render(
        <RatingStepEditor section="tables" config={THREE} onUpdate={vi.fn()} inputSources={[]} accentColor="#14b8a6" />,
        { allNodes: BANDING_NODES },
      )
      expect(tableRow("age_factor")).toHaveTextContent("1 factor1 entry")
      expect(tableRow("region_factor")).toHaveTextContent("1 factor2 entries")
      expect(tableRow("cover_factor")).toHaveTextContent("1 factor0 entries")
    })

    it("adds a table with the Add table button under the list", () => {
      const onUpdate = vi.fn()
      render(
        <RatingStepEditor section="tables" config={THREE} onUpdate={onUpdate} inputSources={[]} accentColor="#14b8a6" />,
        { allNodes: BANDING_NODES },
      )
      const add = screen.getByRole("button", { name: "Add table" })
      expect(screen.getByRole("group", { name: "Rating tables" }).nextElementSibling).toBe(add)
      fireEvent.click(add)
      expect(onUpdate.mock.lastCall![0]).toBe("tables")
      expect(onUpdate.mock.lastCall![1]).toHaveLength(4)
    })

    it("moves a table dragged onto another, keeping it selected", () => {
      const onUpdate = vi.fn()
      const { rerender } = render(
        <RatingStepEditor section="tables" config={THREE} onUpdate={onUpdate} inputSources={[]} accentColor="#14b8a6" />,
        { allNodes: BANDING_NODES },
      )
      const row = (name: string) => tableRow(name).parentElement as HTMLElement
      expect(row("age_factor")).toHaveAttribute("draggable", "true")
      expect(within(row("age_factor")).getByTitle("Drag to reorder")).toBeInTheDocument()
      const dataTransfer = { setData: vi.fn(), effectAllowed: "", dropEffect: "" }
      fireEvent.dragStart(row("age_factor"), { dataTransfer })
      fireEvent.dragOver(row("cover_factor"), { dataTransfer })
      fireEvent.drop(row("cover_factor"), { dataTransfer })

      expect(onUpdate.mock.lastCall![0]).toBe("tables")
      expect(outputs(onUpdate)).toEqual(["region_factor", "cover_factor", "age_factor"])
      rerender(
        <GraphProvider requestScope={documentRequestScope} allNodes={BANDING_NODES} edges={[]}>
          <RatingStepEditor
            section="tables"
            config={{ tables: onUpdate.mock.lastCall![1] }}
            onUpdate={onUpdate}
            inputSources={[]}
            accentColor="#14b8a6"
          />
        </GraphProvider>,
      )
      expect(tableRow("age_factor")).toHaveAttribute("aria-pressed", "true")
    })

    it("renames a table's output column in its row: a double-click opens it, Enter keeps it", () => {
      const onUpdate = vi.fn()
      render(
        <RatingStepEditor section="tables" config={THREE} onUpdate={onUpdate} inputSources={[]} accentColor="#14b8a6" />,
        { allNodes: BANDING_NODES },
      )
      fireEvent.doubleClick(tableRow("region_factor"))
      const box = screen.getByRole("textbox", { name: "Rename region_factor" })
      expect(box).toHaveValue("region_factor")
      expect(box).toHaveFocus()
      // While the name is typed, a drag in the box selects text rather than moving the row.
      expect(box.closest("[draggable]")).toHaveAttribute("draggable", "false")
      fireEvent.change(box, { target: { value: "  area_factor " } })
      fireEvent.keyDown(box, { key: "Enter" })
      fireEvent.blur(box)

      expect(onUpdate).toHaveBeenCalledTimes(1)
      expect(outputs(onUpdate)).toEqual(["age_factor", "area_factor", "cover_factor"])
      expect(screen.queryByRole("textbox", { name: /^Rename / })).toBeNull()
    })

    it("opens the selected table's name with a click in its box, while a click on another row only selects it", () => {
      const onUpdate = vi.fn()
      render(
        <RatingStepEditor section="tables" config={THREE} onUpdate={onUpdate} inputSources={[]} accentColor="#14b8a6" />,
        { allNodes: BANDING_NODES },
      )
      const nameOf = (name: string) => within(tableRow(name)).getByText(name)
      fireEvent.click(nameOf("region_factor"))
      expect(tableRow("region_factor")).toHaveAttribute("aria-pressed", "true")
      expect(screen.queryByRole("textbox", { name: /^Rename / })).toBeNull()

      fireEvent.click(nameOf("region_factor"))
      const box = screen.getByRole("textbox", { name: "Rename region_factor" })
      // It opens in the name's place: the row keeps its badges.
      expect(box.closest("[draggable]")).toHaveTextContent("1 factor")
      fireEvent.keyDown(box, { key: "Escape" })
      // A click elsewhere on the selected row (its badges) does not rename.
      fireEvent.click(within(tableRow("region_factor")).getByText("1 factor"))
      expect(screen.queryByRole("textbox", { name: /^Rename / })).toBeNull()
      expect(onUpdate).not.toHaveBeenCalled()
    })

    it("opens the name with F2 and keeps the old one on Escape, a blank one or the same one", () => {
      const onUpdate = vi.fn()
      render(
        <RatingStepEditor section="tables" config={THREE} onUpdate={onUpdate} inputSources={[]} accentColor="#14b8a6" />,
        { allNodes: BANDING_NODES },
      )
      const rename = (keys: (box: HTMLElement) => void) => {
        fireEvent.keyDown(tableRow("age_factor"), { key: "F2" })
        keys(screen.getByRole("textbox", { name: "Rename age_factor" }))
        expect(screen.queryByRole("textbox", { name: /^Rename / })).toBeNull()
      }
      rename((box) => {
        fireEvent.change(box, { target: { value: "other" } })
        fireEvent.keyDown(box, { key: "Escape" })
      })
      rename((box) => {
        fireEvent.change(box, { target: { value: "   " } })
        fireEvent.keyDown(box, { key: "Enter" })
      })
      rename((box) => fireEvent.blur(box))
      expect(onUpdate).not.toHaveBeenCalled()
    })

    it("starts an unnamed table's box empty, never from its stand-in name", () => {
      const onUpdate = vi.fn()
      const config = { tables: [THREE.tables[0], { ...THREE.tables[1], output_column: "" }] }
      render(
        <RatingStepEditor section="tables" config={config} onUpdate={onUpdate} inputSources={[]} accentColor="#14b8a6" />,
        { allNodes: BANDING_NODES },
      )
      fireEvent.doubleClick(tableRow("Table 2"))
      const box = screen.getByRole("textbox", { name: "Rename Table 2" })
      expect(box).toHaveValue("")
      fireEvent.keyDown(box, { key: "Enter" })
      expect(onUpdate).not.toHaveBeenCalled()

      fireEvent.doubleClick(tableRow("Table 2"))
      fireEvent.change(screen.getByRole("textbox", { name: "Rename Table 2" }), { target: { value: "region_factor" } })
      fireEvent.blur(screen.getByRole("textbox", { name: "Rename Table 2" }))
      expect(outputs(onUpdate)).toEqual(["age_factor", "region_factor"])
    })

    it("takes a name another table has, and marks both", () => {
      const onUpdate = vi.fn()
      render(
        <RatingStepEditor section="tables" config={THREE} onUpdate={onUpdate} inputSources={[]} accentColor="#14b8a6" />,
        { allNodes: BANDING_NODES },
      )
      fireEvent.doubleClick(tableRow("cover_factor"))
      const box = screen.getByRole("textbox", { name: "Rename cover_factor" })
      fireEvent.change(box, { target: { value: "age_factor" } })
      expect(box).not.toHaveAttribute("aria-invalid")
      fireEvent.keyDown(box, { key: "Enter" })
      expect(outputs(onUpdate)).toEqual(["age_factor", "region_factor", "age_factor"])
    })

    it("moves a table with Alt+Up/Down from the keyboard", () => {
      const onUpdate = vi.fn()
      render(
        <RatingStepEditor section="tables" config={THREE} onUpdate={onUpdate} inputSources={[]} accentColor="#14b8a6" />,
        { allNodes: BANDING_NODES },
      )
      fireEvent.keyDown(tableRow("region_factor"), { key: "ArrowDown", altKey: true })
      expect(outputs(onUpdate)).toEqual(["age_factor", "cover_factor", "region_factor"])
      fireEvent.keyDown(tableRow("age_factor"), { key: "ArrowUp", altKey: true })
      // Already first: nothing to move.
      expect(onUpdate).toHaveBeenCalledTimes(1)
    })

    it("looks the same with one table as with several: the grip and the bin's place stay", () => {
      const onUpdate = vi.fn()
      render(
        <RatingStepEditor section="tables" config={{ tables: [THREE.tables[0]] }} onUpdate={onUpdate} inputSources={[]} accentColor="#14b8a6" />,
        { allNodes: BANDING_NODES },
      )
      const row = tableRow("age_factor").parentElement as HTMLElement
      expect(row).toHaveAttribute("draggable", "true")
      expect(within(row).getByTitle("Drag to reorder")).toBeInTheDocument()
      // The last table cannot be removed, so its bin is only a kept place.
      expect(within(row).queryByRole("button", { name: /^Remove / })).toBeNull()
      expect(row.lastElementChild).toHaveAttribute("aria-hidden", "true")
      fireEvent.keyDown(tableRow("age_factor"), { key: "ArrowDown", altKey: true })
      expect(onUpdate).not.toHaveBeenCalled()
    })
  })

  it("summarises why a selected rating table has problems", () => {
    render(
      <RatingStepEditor
        section="tables"
        config={{
          tables: [
            { factors: [], output_column: " ", entries: [] },
            {
              factors: ["region"],
              output_column: "duplicate_factor",
              default_value: "1.0",
              entries: [{ region: "north", value: 1.0 }],
            },
            {
              factors: ["region"],
              output_column: "duplicate_factor",
              default_value: "1.0",
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

    // With no Output Column field, the summary says what is wrong with the name.
    expect(screen.queryByLabelText("Output Column")).toBeNull()
    expect(screen.getByText("Output column name must be unique")).toBeTruthy()
  })

  it("searches and selects rating tables when many are configured", () => {
    const tables = Array.from({ length: 24 }, (_, idx) => ({
      factors: ["age_band"],
      output_column: idx === 19 ? "telematics_adjustment" : `factor_${String(idx + 1).padStart(2, "0")}`,
      default_value: "1.0",
      entries: [{ age_band: "young", value: 1.0 }],
    }))

    render(
      <RatingStepEditor
        section="tables"
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

    fireEvent.click(within(selector).getByRole("button", { name: /^telematics_adjustment healthy$/i }))

    expect(within(selector).getByRole("button", { name: /^telematics_adjustment healthy$/i })).toHaveAttribute("aria-pressed", "true")
  })

  it("shows Default as undo leaves it, and a blur then commits nothing", () => {
    const table = (value: string) => ({ factors: [], output_column: "age_factor", default_value: value, entries: [] })
    const onUpdate = vi.fn()
    const view = render(
      <RatingStepEditor section="tables" config={{ tables: [table("1.5")] }} onUpdate={onUpdate} inputSources={[]} accentColor="#14b8a6" />,
    )
    expect((screen.getByLabelText("Default") as HTMLInputElement).value).toBe("1.5")

    // An undo (or a broadcast) changes the value while the field is on screen.
    view.rerender(
      <GraphProvider requestScope={documentRequestScope} allNodes={[]} edges={[]}>
        <RatingStepEditor section="tables" config={{ tables: [table("2.5")] }} onUpdate={onUpdate} inputSources={[]} accentColor="#14b8a6" />
      </GraphProvider>,
    )
    expect((screen.getByLabelText("Default") as HTMLInputElement).value).toBe("2.5")
    fireEvent.blur(screen.getByLabelText("Default"))
    expect(onUpdate).not.toHaveBeenCalled()
  })

  it.each([
    { label: "Combined Output Column", key: "output_column", before: "technical_price", after: "loaded_price" },
    { label: "Base Value", key: "base_value", before: "1.5", after: "2.5" },
  ])("shows the combined output's $label as undo leaves it, and a blur commits nothing", ({ label, key, before, after }) => {
    const combined = (value: string) => ({ output_column: "technical_price", operation: "multiply", base_value: "1.0", [key]: value })
    const onUpdate = vi.fn()
    const config = (value: string) => ({
      tables: [{ factors: [], output_column: "age_factor", default_value: "1.0", entries: [] }],
      combined_outputs: [combined(value)],
    })
    const view = render(
      <RatingStepEditor section="combined" config={config(before)} onUpdate={onUpdate} inputSources={[]} accentColor="#14b8a6" />,
    )
    expect((screen.getByLabelText(label) as HTMLInputElement).value).toBe(before)
    view.rerender(
      <GraphProvider requestScope={documentRequestScope} allNodes={[]} edges={[]}>
        <RatingStepEditor section="combined" config={config(after)} onUpdate={onUpdate} inputSources={[]} accentColor="#14b8a6" />
      </GraphProvider>,
    )
    expect((screen.getByLabelText(label) as HTMLInputElement).value).toBe(after)
    fireEvent.blur(screen.getByLabelText(label))
    expect(onUpdate).not.toHaveBeenCalled()
  })

  describe("On miss and Default, under the table", () => {
    const table = (extra: Record<string, unknown> = {}) => ({ factors: [], output_column: "af", entries: [], ...extra })
    const editor = (config: Record<string, unknown>, onUpdate = vi.fn()) => {
      render(
        <RatingStepEditor section="tables" config={config} onUpdate={onUpdate} inputSources={[]} accentColor="#14b8a6" />,
        { allNodes: [] },
      )
      return onUpdate
    }

    it("offers Leave empty and Use default, never Stop the run", () => {
      editor({ tables: [table()] })
      const onMiss = screen.getByLabelText("On miss") as HTMLSelectElement
      expect(Array.from(onMiss.options).map((option) => option.textContent)).toEqual(["Leave empty", "Use default"])
    })

    it("starts a table without a default on Leave empty, its Default box off and empty", () => {
      editor({ tables: [table()] })
      expect(screen.getByLabelText("On miss")).toHaveValue("empty")
      expect(screen.getByLabelText("Default")).toBeDisabled()
      expect(screen.getByLabelText("Default")).toHaveValue("")
    })

    it.each([["1.0"], [1.25]])("reads a table whose default is %j as Use default, as the engine does", (defaultValue) => {
      editor({ tables: [table({ default_value: defaultValue })] })
      expect(screen.getByLabelText("On miss")).toHaveValue("default")
      expect(screen.getByLabelText("Default")).toBeEnabled()
      expect(screen.getByLabelText("Default")).toHaveValue(String(Number(defaultValue)))
    })

    it("writes a default of 1 on Use default, as a number, and removes it on Leave empty", () => {
      const onUpdate = editor({ tables: [table()] })
      fireEvent.change(screen.getByLabelText("On miss"), { target: { value: "default" } })
      expect(onUpdate).toHaveBeenLastCalledWith("tables", [table({ default_value: 1 })])

      cleanup()
      const second = editor({ tables: [table({ default_value: "1.3" })] })
      fireEvent.change(screen.getByLabelText("On miss"), { target: { value: "empty" } })
      expect(second).toHaveBeenLastCalledWith("tables", [table()])
    })

    // RTG-03: a decimal comma is refused at the field in words, in any browser language,
    // never saved as fifteen; a Default is written as a number.
    it.each([["1,5"], ["1 000,50"]])("refuses a Default typed %j in words and saves nothing", (typed) => {
      const onUpdate = editor({ tables: [table({ default_value: 1 })] })
      const field = screen.getByLabelText("Default")
      fireEvent.change(field, { target: { value: typed } })
      fireEvent.blur(field)
      expect(field).toHaveValue(typed)
      expect(screen.getByRole("alert")).toHaveTextContent("Use a point for decimals.")
      expect(onUpdate).not.toHaveBeenCalled()
    })

    it("writes a Default typed with thousands as the number it is", () => {
      const onUpdate = editor({ tables: [table({ default_value: 1 })] })
      const field = screen.getByLabelText("Default")
      fireEvent.change(field, { target: { value: "1,000.5" } })
      fireEvent.blur(field)
      expect(onUpdate).toHaveBeenLastCalledWith("tables", [table({ default_value: 1000.5 })])
    })

    it("sits under the table, after its rates", () => {
      editor({
        tables: [{ factors: ["age_band"], output_column: "af", entries: [{ age_band: "young", value: 1.1 }] }],
      })
      const onMiss = screen.getByLabelText("On miss")
      const rates = screen.getByText("n=1")
      const rebuild = screen.getByRole("button", { name: /Rebuild from factor levels/ })
      const follows = (a: Node, b: Node) => Boolean(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING)
      // The table, then Rebuild from factor levels, then On miss and Default.
      expect(follows(screen.getByRole("region", { name: "age_band rating grid" }), rebuild)).toBe(true)
      expect(follows(rates, rebuild)).toBe(true)
      expect(follows(rebuild, onMiss)).toBe(true)
    })
  })

  it("clearing Default removes it, so the table's misses are left empty", () => {
    const onUpdate = vi.fn()
    render(
      <RatingStepEditor
        section="tables"
        config={{ tables: [{ factors: [], output_column: "af", default_value: "1.0", entries: [] }] }}
        onUpdate={onUpdate}
        inputSources={[]}
        accentColor="#14b8a6"
      />,
      { allNodes: [] }
    )

    const defaultInput = screen.getByLabelText("Default")
    fireEvent.change(defaultInput, { target: { value: "" } })
    fireEvent.blur(defaultInput)

    expect(onUpdate).toHaveBeenLastCalledWith("tables", [
      { factors: [], output_column: "af", entries: [] },
    ])
  })

  describe("the node panel's Tables and Combined tabs", () => {
    const config = {
      tables: [
        { factors: [], output_column: "age_factor", default_value: "1.0", entries: [] },
        { factors: [], output_column: "region_factor", default_value: "1.0", entries: [] },
      ],
      combined_outputs: [{ output_column: "technical_price", operation: "multiply", base_value: "1.0" }],
    }
    const input = [{ sourceNodeId: "src", name: "source_data", sourceLabel: "Source Data", edgeId: "e1" }]
    const editor = (section: "tables" | "combined" | "all") => (
      <RatingStepEditor section={section} config={config} onUpdate={vi.fn()} inputSources={input} accentColor="#14b8a6" />
    )

    it("shows the input and the tables on Tables, and no combined output", () => {
      render(editor("tables"), { allNodes: [] })
      expect(screen.getByText("source_data")).toBeTruthy()
      expect(screen.getByRole("group", { name: "Rating tables" })).toBeTruthy()
      expect(screen.queryByRole("tablist", { name: "Combined outputs" })).toBeNull()
      expect(screen.queryByRole("radiogroup", { name: "Rating section" })).toBeNull()
    })

    it("shows only the combined outputs on Combined", () => {
      render(editor("combined"), { allNodes: [] })
      expect(screen.getByRole("tablist", { name: "Combined outputs" })).toBeTruthy()
      expect(screen.getByLabelText("Combined Output Column")).toHaveValue("technical_price")
      expect(screen.queryByRole("group", { name: "Rating tables" })).toBeNull()
      expect(screen.queryByText("source_data")).toBeNull()
    })

    it("shows both in the comparison view, which cannot switch tabs", () => {
      render(editor("all"), { allNodes: [] })
      expect(screen.getByRole("group", { name: "Rating tables" })).toBeTruthy()
      expect(screen.getByRole("tablist", { name: "Combined outputs" })).toBeTruthy()
    })

    it("keeps the selected table across a visit to Combined", () => {
      const view = render(editor("tables"), { allNodes: [] })
      fireEvent.click(screen.getByRole("button", { name: /^region_factor (healthy|problem)$/ }))
      const rerender = (section: "tables" | "combined") =>
        view.rerender(<GraphProvider requestScope={documentRequestScope} allNodes={[]} edges={[]}>{editor(section)}</GraphProvider>)
      rerender("combined")
      rerender("tables")
      expect(screen.getByRole("button", { name: /^region_factor (healthy|problem)$/ })).toHaveAttribute("aria-pressed", "true")
    })
  })

  it("'Select at least one factor' shown when no factors", () => {
    render(
      <RatingStepEditor
        section="tables"
        config={{}}
        onUpdate={vi.fn()}
        inputSources={[]}

        accentColor="#14b8a6"
      />,
      { allNodes: [] }
    )
    expect(screen.getByText("Select at least one factor to populate the rating table")).toBeTruthy()
  })

  it("says a blank table output column is required, with no Output Column field", () => {
    render(
      <RatingStepEditor
        section="tables"
        config={{}}
        onUpdate={vi.fn()}
        inputSources={[]}

        accentColor="#14b8a6"
      />,
      { allNodes: [] }
    )

    expect(screen.queryByLabelText("Output Column")).toBeNull()
    expect(screen.getByText("Output column is required")).toBeTruthy()
  })

  it("does not flag a populated table output column", () => {
    render(
      <RatingStepEditor
        section="tables"
        config={{
          tables: [{ name: "T1", factors: [], output_column: "age_factor", default_value: "1.0", entries: [] }],
        }}
        onUpdate={vi.fn()}
        inputSources={[]}

        accentColor="#14b8a6"
      />,
      { allNodes: [] }
    )

    expect(screen.queryByText("Output column is required")).toBeNull()
  })

  it("allows the Combined section to have no configured output", () => {
    render(
      <RatingStepEditor
        section="combined"
        config={{
          tables: [
            { name: "T1", factors: [], output_column: "age_factor", default_value: "1.0", entries: [] },
            { name: "T2", factors: [], output_column: "region_factor", default_value: "1.0", entries: [] },
          ],
        }}
        onUpdate={vi.fn()}
        inputSources={[]}

        accentColor="#14b8a6"
      />,
      { allNodes: [] }
    )

    expect(screen.getByText("No combined output")).toBeTruthy()
    expect(screen.queryByLabelText("Combined Output Column")).toBeNull()
    expect(screen.queryByText("Combined output column is required")).toBeNull()
  })

  it("flags an explicitly blank combined output column and does not show placeholder text", () => {
    render(
      <RatingStepEditor
        section="combined"
        config={{
          combined_outputs: [{ output_column: "", operation: "multiply", base_value: "1.0" }],
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
        section="combined"
        config={{
          combined_outputs: [
            { output_column: "", operation: "divide" },
            { output_column: "missing_base", operation: "multiply" },
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
        section="tables"
        config={{
          tables: [
            { name: "T1", factors: [], output_column: "   ", default_value: "1.0", entries: [] },
            { name: "T2", factors: [], output_column: "region_factor", default_value: "1.0", entries: [] },
          ],
        }}
        onUpdate={vi.fn()}
        inputSources={[]}

        accentColor="#14b8a6"
      />,
      { allNodes: [] }
    )

    expect(screen.getByText("Output column is required")).toBeTruthy()
  })

  it("treats whitespace-only combined output columns as blank", () => {
    render(
      <RatingStepEditor
        section="combined"
        config={{
          combined_outputs: [{ output_column: "\t ", operation: "multiply", base_value: "1.0" }],
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
        output_column: "af",
        default_value: "1.0",
        entries: [
          { age_band: "young", value: 1.1 },
          { age_band: "mid", value: 1.0 },
          { age_band: "old", value: 0.9 },
        ],
      }],
    }
    render(
      <RatingStepEditor
        section="tables"
        config={config}
        onUpdate={vi.fn()}
        inputSources={[]}

        accentColor="#14b8a6"
      />,
      { allNodes: BANDING_NODES }
    )
    // OneWayEditor renders "age_band" column header and "Relativity" header
    // The one-way editor's heading, not the factor's own option.
    expect(screen.getByText("age_band", { ignore: "option, button span" })).toBeTruthy()
    expect(screen.getByText("Relativity")).toBeTruthy()
    // The "select at least one factor" message should NOT be shown
    expect(screen.queryByText("Select at least one factor to populate the rating table")).toBeNull()
  })

  it("adding second factor shows TwoWayGrid (2 factors)", () => {
    const config = {
      tables: [{
        name: "T1",
        factors: ["age_band", "region"],
        output_column: "combined",
        default_value: "1.0",
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
        section="tables"
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
        section="tables"
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
      expect.objectContaining({ output_column: "" }),
      expect.objectContaining({ output_column: "" }),
    ]))
    // A new table has no default, so a level it does not list stops the run.
    const added = onUpdate.mock.calls.at(-1)?.[1] as Record<string, unknown>[]
    expect(added.at(-1)).toEqual({ factors: [], output_column: "", entries: [] })
  })

  it("removing a table when >1 tables", () => {
    const onUpdate = vi.fn()
    const config = {
      tables: [
        { factors: [], output_column: "age_factor", default_value: "1.0", entries: [] },
        { factors: [], output_column: "region_factor", default_value: "1.0", entries: [] },
      ],
    }
    render(
      <RatingStepEditor
        section="tables"
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
      expect.objectContaining({ output_column: "region_factor" }),
    ])
  })

  it("cannot remove last table", () => {
    const config = {
      tables: [
        { factors: [], output_column: "only_factor", default_value: "1.0", entries: [] },
      ],
    }
    render(
      <RatingStepEditor
        section="tables"
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
        { name: "T1", factors: [], output_column: "af", default_value: "1.0", entries: [] },
        { name: "T2", factors: [], output_column: "rf", default_value: "1.0", entries: [] },
      ],
      combined_outputs: [
        { output_column: "combined", operation: "multiply", base_value: "1.0" },
      ],
    }
    render(
      <RatingStepEditor
        section="combined"
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
        section="combined"
        config={{
          tables: [
            { name: "T1", factors: [], output_column: "af", default_value: "1.0", entries: [] },
          ],
          combined_outputs: [
            { output_column: "combined", operation: "multiply", base_value: "1.0" },
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
        { name: "T1", factors: [], output_column: "af", default_value: "1.0", entries: [] },
        { name: "T2", factors: [], output_column: "rf", default_value: "1.0", entries: [] },
      ],
      combined_outputs: [
        { output_column: "combined", operation: "multiply", base_value: "1.0" },
      ],
    }
    render(
      <RatingStepEditor
        section="combined"
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
      combined_outputs: [expect.objectContaining({ operation: "add", base_value: 0 })],
    })
  })

  it("removing the last combined output leaves the combined section empty", () => {
    const onUpdate = vi.fn()
    render(
      <RatingStepEditor
        section="combined"
        config={{
          combined_outputs: [
            { output_column: "combined", operation: "multiply", base_value: "1.0" },
          ],
        }}
        onUpdate={onUpdate}
        inputSources={[]}
        accentColor="#14b8a6"
      />,
      { allNodes: [] }
    )

    fireEvent.click(screen.getByLabelText("Remove combined output"))
    expect(onUpdate).toHaveBeenCalledWith({ combined_outputs: [] })
  })

  it("supports multiple combined outputs with a banding-style selector row", () => {
    const onUpdate = vi.fn()
    const config = {
      tables: [
        { name: "T1", factors: [], output_column: "age_factor", default_value: "1.0", entries: [] },
        { name: "T2", factors: [], output_column: "region_factor", default_value: "1.0", entries: [] },
      ],
      combined_outputs: [
        { output_column: "technical_price", operation: "multiply", base_value: "1.0" },
        { output_column: "loaded_price", operation: "add", base_value: "100.0" },
      ],
    }

    render(
      <RatingStepEditor
        section="combined"
        config={config}
        onUpdate={onUpdate}
        inputSources={[]}
        accentColor="#14b8a6"
      />,
      { allNodes: [] }
    )

    const tablist = screen.getByRole("tablist", { name: "Combined outputs" })
    expect(tablist.className).toContain("overflow-x-auto")
    expect(screen.getAllByText("technical_price").length).toBeGreaterThan(0)
    expect(screen.getByText("loaded_price")).toBeTruthy()
    expect(screen.getByLabelText("Base Value")).toHaveValue("1")

    fireEvent.keyDown(screen.getByRole("tab", { name: /technical_price/ }), { key: "ArrowRight" })
    expect(screen.getByRole("tab", { name: /loaded_price/ })).toHaveAttribute("aria-selected", "true")

    const baseValueInput = screen.getByLabelText("Base Value")
    fireEvent.change(baseValueInput, { target: { value: "275.50" } })
    fireEvent.blur(baseValueInput)
    expect(onUpdate).toHaveBeenCalledWith(expect.objectContaining({
      combined_outputs: expect.arrayContaining([
        expect.objectContaining({ output_column: "loaded_price", base_value: 275.5 }),
      ]),
    }))

    fireEvent.click(screen.getByLabelText("Add combined output"))
    expect(onUpdate).toHaveBeenCalledWith(expect.objectContaining({
      combined_outputs: expect.arrayContaining([
        expect.objectContaining({ output_column: "combined_3" }),
      ]),
    }))
  })

  it("flags duplicate combined output names and auto-generates unused names", () => {
    const onUpdate = vi.fn()
    const config = {
      tables: [
        { name: "T1", factors: [], output_column: "age_factor", default_value: "1.0", entries: [] },
      ],
      combined_outputs: [
        { output_column: "age_factor", operation: "multiply", base_value: "1.0" },
        { output_column: "combined_3", operation: "multiply", base_value: "1.0" },
      ],
    }

    render(
      <RatingStepEditor
        section="combined"
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
      combined_outputs: expect.arrayContaining([
        expect.objectContaining({ output_column: "combined_4" }),
      ]),
    }))
  })

  it("rebuild button shown when factors selected", () => {
    const config = {
      tables: [{
        name: "T1",
        factors: ["age_band"],
        output_column: "af",
        default_value: "1.0",
        entries: [],
      }],
    }
    render(
      <RatingStepEditor
        section="tables"
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
        section="tables"
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
        output_column: "af",
        default_value: "1.0",
        entries: [],
      }],
    }
    render(
      <RatingStepEditor
        section="tables"
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
        section="tables"
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
        section="tables"
        config={{}}
        onUpdate={vi.fn()}
        inputSources={[]}

        accentColor="#14b8a6"
      />,
      { allNodes: BANDING_NODES }
    )
    // With no factor yet, Add factor is the first factor's slot.
    expect(screen.getByRole("button", { name: "Add factor" })).toHaveTextContent("Select factor...")
    const options = factorOptions("Add factor")
    expect(options.some(o => o?.includes("age_band"))).toBe(true)
    expect(options.some(o => o?.includes("region"))).toBe(true)
  })

  it("offers a factor's slot the columns with levels the table's other factors do not use, each with its count of levels", () => {
    render(
      <RatingStepEditor
        section="tables"
        config={{ tables: [{ factors: ["age_band", "region"], output_column: "f", default_value: "1.0", entries: [] }] }}
        onUpdate={vi.fn()}
        inputSources={[]}
        accentColor="#14b8a6"
      />,
      { allNodes: [...BANDING_NODES, makeBandingNode("single", ["only"])] },
    )

    expect(factorOptions("Factor 1")).toEqual(["age_band (3 levels)", "vehicle_type (3 levels)", "single (1 level)"])
    expect(screen.getByRole("button", { name: "Factor 2" })).toHaveAttribute("title", "region (2 levels)")
  })

  it("lays the factors out on one line with + Add factor after them, until there are three", () => {
    const table = (factors: string[]) => ({ tables: [{ factors, output_column: "f", default_value: "1.0", entries: [] }] })
    const view = render(
      <RatingStepEditor section="tables" config={table(["age_band"])} onUpdate={vi.fn()} inputSources={[]} accentColor="#14b8a6" />,
      { allNodes: BANDING_NODES },
    )
    const first = screen.getByRole("button", { name: "Factor 1" })
    const add = screen.getByRole("button", { name: "Add factor" })
    // One line: the factor's slot and Add factor share a row.
    expect(add.parentElement!.parentElement).toBe(first.parentElement!.parentElement!.parentElement)
    expect(add).toHaveTextContent("Add factor")

    view.rerender(
      <GraphProvider requestScope={documentRequestScope} allNodes={BANDING_NODES} edges={[]}>
        <RatingStepEditor section="tables" config={table(["age_band", "region", "vehicle_type"])} onUpdate={vi.fn()} inputSources={[]} accentColor="#14b8a6" />
      </GraphProvider>,
    )
    expect(screen.getAllByRole("button", { name: /^Factor \d$/ })).toHaveLength(3)
    expect(screen.queryByRole("button", { name: "Add factor" })).toBeNull()
  })

  it("shows raw string/categorical preview columns in the add factor dropdown but hides numeric columns", () => {
    render(
      <RatingStepEditor
        section="tables"
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

    const options = factorOptions("Add factor")
    expect(options.some(o => o?.includes("age_band"))).toBe(true)
    expect(options.some(o => o?.includes("channel (2 levels)"))).toBe(true)
    expect(options.some(o => o?.includes("segment (2 levels)"))).toBe(true)
    expect(options.some(o => o?.includes("premium"))).toBe(false)
  })

  it("adding a raw string factor rebuilds entries from its distinct preview values", () => {
    const onUpdate = vi.fn()
    render(
      <RatingStepEditor
        section="tables"
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

    chooseFactor("Add factor", "channel")

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

  it("refuses a column named value as a factor, saying why, and writes nothing (BUG-46)", () => {
    // Each row keeps its rate under "value", so the factor's levels would be overwritten.
    const onUpdate = vi.fn()
    render(
      <RatingStepEditor
        section="tables"
        config={{}}
        onUpdate={onUpdate}
        inputSources={[]}
        accentColor="#14b8a6"
        previewRows={[{ value: "high" }, { value: "low" }]}
        upstreamColumns={[{ name: "value", dtype: "String" }]}
      />,
      { allNodes: BANDING_NODES }
    )

    chooseFactor("Add factor", "value")

    expect(onUpdate).not.toHaveBeenCalled()
    expect(screen.getByTestId("rating-factor-limit")).toHaveTextContent(
      "A column named value cannot be a factor: every rating row keeps its rate under value. " +
        "Rename the column upstream, in a Transform node for example.",
    )
  })

  it("uses preview-derived raw string factor levels without showing an explanatory warning", () => {
    render(
      <RatingStepEditor
        section="tables"
        config={{
          tables: [{
            name: "Channel Factor",
            factors: ["channel"],
            output_column: "channel_factor",
            default_value: "1.0",
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
        section="tables"
        config={{
          tables: [{
            name: "Channel Factor",
            factors: ["channel"],
            output_column: "channel_factor",
            default_value: "1.0",
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
        section="tables"
        config={{
          tables: [{
            name: "Channel Cover Factor",
            factors: ["age_band", "channel", "cover_type"],
            output_column: "channel_cover_factor",
            default_value: "1.25",
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
        section="tables"
        config={{
          tables: [{
            name: "Age Factor",
            factors: ["age_band"],
            output_column: "age_factor",
            default_value: "1.0",
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
        section="tables"
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

    const options = factorOptions("Add factor")
    expect(options.some(o => o?.includes("proposer_age_band"))).toBe(true)
    expect(options.some(o => o?.includes("vehicle_age_band"))).toBe(true)
    expect(options.some(o => o?.includes("channel_band"))).toBe(true)
  })

  it("shows entry count in summary", () => {
    const config = {
      tables: [{
        name: "T1",
        factors: ["age_band"],
        output_column: "age_factor",
        default_value: "1.0",
        entries: [
          { age_band: "young", value: 1.1 },
          { age_band: "old", value: 0.9 },
        ],
      }],
    }
    render(
      <RatingStepEditor
        section="tables"
        config={config}
        onUpdate={vi.fn()}
        inputSources={[]}

        accentColor="#14b8a6"
      />,
      { allNodes: BANDING_NODES }
    )
    expect(screen.getByRole("button", { name: /^age_factor healthy$/ })).toBeTruthy()
    // The grid's own stats line counts the rates; no second line names the table and its range.
    expect(screen.getByText("n=2")).toBeTruthy()
    expect(screen.queryByText(/entries · range/)).toBeNull()
  })

  it("renders input sources bar when inputs provided", () => {
    render(
      <RatingStepEditor
        section="tables"
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
        { name: "T1", factors: [], output_column: "age_factor", default_value: "1.0", entries: [] },
        { name: "T2", factors: [], output_column: "region_factor", default_value: "1.0", entries: [] },
      ],
      combined_outputs: [{ output_column: "combined", operation: "multiply", base_value: "1.0" }],
    }
    render(
      <RatingStepEditor
        section="combined"
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
        section="tables"
        config={{}}
        onUpdate={onUpdate}
        inputSources={[]}

        accentColor="#14b8a6"
      />,
      { allNodes: BANDING_NODES }
    )
    chooseFactor("Add factor", "age_band")
    // Should update tables with the new factor
    expect(onUpdate).toHaveBeenCalledWith("tables", expect.arrayContaining([
      expect.objectContaining({ factors: ["age_band"] }),
    ]))
  })

  it("retains selected factor dtype metadata when adding a factor", () => {
    const onUpdate = vi.fn()
    render(
      <RatingStepEditor
        section="tables"
        config={{
          tables: [{
            name: "T1",
            factors: ["age_band"],
            factor_dtypes: { age_band: { kind: "Categorical" } },
            output_column: "age_factor",
            default_value: "1.0",
            entries: [],
          }],
        }}
        onUpdate={onUpdate}
        inputSources={[]}
        accentColor="#14b8a6"
      />,
      { allNodes: BANDING_NODES },
    )

    chooseFactor("Add factor", "region")

    expect(onUpdate).toHaveBeenCalledWith("tables", expect.arrayContaining([
      expect.objectContaining({
        factors: ["age_band", "region"],
        factor_dtypes: { age_band: { kind: "Categorical" } },
      }),
    ]))
  })

  it("removes deselected factor dtype metadata with the factor", () => {
    const onUpdate = vi.fn()
    const { container } = render(
      <RatingStepEditor
        section="tables"
        config={{
          tables: [{
            name: "T1",
            factors: ["age_band", "region"],
            factor_dtypes: {
              age_band: { kind: "Categorical" },
              region: { kind: "String" },
            },
            output_column: "age_factor",
            default_value: "1.0",
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
        factor_dtypes: { region: { kind: "String" } },
      }),
    ]))
  })

  it("renders 3-way editor with slice dimension when 3 factors", () => {
    const config = {
      tables: [{
        name: "T1",
        factors: ["age_band", "region", "vehicle_type"],
        output_column: "combined",
        default_value: "1.0",
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
        section="tables"
        config={config}
        onUpdate={vi.fn()}
        inputSources={[]}

        accentColor="#14b8a6"
      />,
      { allNodes: BANDING_NODES }
    )
    // Factors count should show 3/3
    expect(screen.getByText("Factors (3/3)")).toBeTruthy()
    expect(screen.getByRole("button", { name: "Factor 1" })).toHaveTextContent("age_band")
    expect(screen.getByRole("button", { name: "Factor 2" })).toHaveTextContent("region")
    expect(screen.getByRole("button", { name: "Factor 3" })).toHaveTextContent("vehicle_type")
    // The 3rd factor (vehicle_type) should appear as the slice selector label
    // The slice's label, not the factor's own option.
    expect(screen.getByText("vehicle_type", { ignore: "option, button span" })).toBeTruthy()
    expect(screen.getByRole("combobox", { name: "vehicle_type slice" })).toBeInTheDocument()
  })
})
