import { useState, type ComponentProps } from "react"
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import useUIStore from "../../stores/useUIStore"
import ExplorePivotsConfig from "../../panels/editors/ExplorePivotsConfig"
import type { OnUpdateConfig } from "../../panels/editors/_shared"
import {
  createExploreChart,
  seedValueEncodings,
} from "../../panels/explore/chartConfig"
import type {
  ExplorePivotConfig,
  PivotAxisPlacement,
  PivotFormulaPlacement,
  PivotValuePlacement,
} from "../../panels/explore/pivotConfig"

afterEach(() => {
  cleanup()
  vi.useRealTimers()
})

const upstreamColumns = [
  { name: "region", dtype: "String" },
  { name: "year", dtype: "Int64" },
  { name: "claims", dtype: "Float64" },
]

type FixtureAxisPlacement = {
  id: string
  field: string
  sort?: PivotAxisPlacement["sort"]
  number_format?: PivotAxisPlacement["number_format"]
  decimal_places?: PivotAxisPlacement["decimal_places"]
  use_grouping?: PivotAxisPlacement["use_grouping"]
}
type FixtureValuePlacement = {
  id: string
  field: string
  aggregation: PivotValuePlacement["aggregation"]
  reference: string
  display_name: string
  sort_rows?: PivotValuePlacement["sort_rows"]
  color_scale?: PivotValuePlacement["color_scale"]
  color_scale_split_by?: PivotValuePlacement["color_scale_split_by"]
  number_format?: PivotValuePlacement["number_format"]
  decimal_places?: PivotValuePlacement["decimal_places"]
  use_grouping?: PivotValuePlacement["use_grouping"]
}
type FixtureFormulaPlacement = {
  id: string
  reference: string
  display_name: string
  expression: string
  number_format?: PivotFormulaPlacement["number_format"]
  decimal_places?: PivotFormulaPlacement["decimal_places"]
  use_grouping?: PivotFormulaPlacement["use_grouping"]
}
type FixturePivotOverrides = {
  version?: ExplorePivotConfig["version"]
  id?: string
  name?: string
  enabled?: boolean
  filters?: ExplorePivotConfig["filters"]
  columns?: FixtureAxisPlacement[]
  rows?: FixtureAxisPlacement[]
  values?: FixtureValuePlacement[]
  formulas?: FixtureFormulaPlacement[]
  value_order?: string[]
  options?: ExplorePivotConfig["options"]
}

function fullPivot(overrides: FixturePivotOverrides = {}): ExplorePivotConfig {
  const { columns = [], rows = [], values = [], formulas = [], ...rest } = overrides
  const formattedColumns = columns.map((placement): PivotAxisPlacement => ({
    ...placement,
    number_format: placement.number_format ?? "general",
    decimal_places: placement.decimal_places ?? null,
    use_grouping: placement.use_grouping ?? true,
  }))
  const formattedRows = rows.map((placement): PivotAxisPlacement => ({
    ...placement,
    number_format: placement.number_format ?? "general",
    decimal_places: placement.decimal_places ?? null,
    use_grouping: placement.use_grouping ?? true,
  }))
  const formattedValues = values.map((placement): PivotValuePlacement => ({
    ...placement,
    color_scale_split_by: placement.color_scale_split_by ?? null,
    number_format: placement.number_format ?? "general",
    decimal_places: placement.decimal_places ?? null,
    use_grouping: placement.use_grouping ?? true,
  }))
  const formattedFormulas = formulas.map((placement): PivotFormulaPlacement => ({
    ...placement,
    number_format: placement.number_format ?? "general",
    decimal_places: placement.decimal_places ?? null,
    use_grouping: placement.use_grouping ?? true,
  }))
  return {
    version: 1,
    id: "pivot_1",
    name: "Pivot 1",
    enabled: true,
    filters: [],
    columns: formattedColumns,
    rows: formattedRows,
    values: formattedValues,
    formulas: formattedFormulas,
    options: { row_grand_totals: true, column_grand_totals: true, sort_by: null },
    ...rest,
    value_order: rest.value_order ?? [
      ...formattedValues.map(({ id }) => id),
      ...formattedFormulas.map(({ id }) => id),
    ],
  }
}

function createDragDataTransfer() {
  return {
    dropEffect: "none",
    effectAllowed: "uninitialized",
    setData: vi.fn(),
  }
}

function PivotConfigHarness({
  initialConfig = {},
  columns = upstreamColumns,
  onCommittedUpdate,
  loadFilterMembers,
  currentConfigHash = "hash",
}: {
  initialConfig?: Record<string, unknown>
  columns?: typeof upstreamColumns
  onCommittedUpdate?: () => void
  loadFilterMembers?: ComponentProps<typeof ExplorePivotsConfig>["loadFilterMembers"]
  currentConfigHash?: string | null
}) {
  const [config, setConfig] = useState(initialConfig)
  const onUpdate: OnUpdateConfig = (keyOrUpdates, value) => {
    setConfig((current) =>
      typeof keyOrUpdates === "string"
        ? { ...current, [keyOrUpdates]: value }
        : { ...current, ...keyOrUpdates },
    )
    onCommittedUpdate?.()
    return { ok: true }
  }

  return (
    <>
      <ExplorePivotsConfig
        config={config}
        onUpdate={onUpdate}
        nodeId="explore_1"
        upstreamColumns={columns}
        loadFilterMembers={loadFilterMembers}
        currentConfigHash={currentConfigHash}
      />
      <output data-testid="persisted-config">{JSON.stringify(config)}</output>
    </>
  )
}

describe("ExplorePivotsConfig", () => {
  beforeEach(() => {
    useUIStore.setState({
      exploreConfiguredChartIds: {},
      exploreConfiguredPivotIds: {},
      explorePreviewPanes: {},
      explorePanes: {},
    })
  })

  it("stores the configured pivot without touching the preview, and clears on Back", () => {
    // Seed a non-default preview pane so the untouched assertions below prove
    // Configure/Back leave it alone rather than clearing it.
    useUIStore.setState({ explorePreviewPanes: { explore_1: "overview" } })
    render(<PivotConfigHarness />)
    fireEvent.click(screen.getByRole("button", { name: "Add Pivot" }))
    fireEvent.click(screen.getByRole("button", { name: "Configure Pivot 1" }))

    expect(
      useUIStore.getState().exploreConfiguredPivotIds.explore_1,
    ).toBe("pivot_1")
    // Editor-side navigation never changes the preview pane.
    expect(
      useUIStore.getState().explorePreviewPanes.explore_1,
    ).toBe("overview")

    fireEvent.click(screen.getByRole("button", { name: "Back to pivots" }))
    expect(
      useUIStore.getState().exploreConfiguredPivotIds.explore_1,
    ).toBeNull()
    expect(
      useUIStore.getState().explorePreviewPanes.explore_1,
    ).toBe("overview")
  })

  it("self-clears a stored configured id whose pivot no longer exists", () => {
    useUIStore.setState({
      exploreConfiguredPivotIds: { explore_1: "pivot_ghost" },
    })
    render(<PivotConfigHarness />)

    expect(screen.getByRole("button", { name: "Add Pivot" })).toBeVisible()
    expect(
      useUIStore.getState().exploreConfiguredPivotIds.explore_1,
    ).toBeNull()
  })

  it("adds complete enabled cards and keeps toggle separate from Configure/Back", () => {
    render(<PivotConfigHarness />)

    fireEvent.click(screen.getByRole("button", { name: "Add Pivot" }))
    fireEvent.click(screen.getByRole("button", { name: "Add Pivot" }))

    expect(screen.getByRole("group", { name: "Pivot 1" })).toBeInTheDocument()
    expect(screen.getByRole("group", { name: "Pivot 2" })).toBeInTheDocument()
    expect(screen.getAllByTestId("explore-toggle-card")).toHaveLength(2)
    expect(screen.getByRole("checkbox", { name: "Pivot 1" })).toHaveAttribute(
      "aria-checked",
      "true",
    )
    expect(screen.getAllByTestId("explore-toggle-card")[0]).toHaveAttribute(
      "data-state",
      "enabled",
    )
    expect(screen.getAllByTestId("explore-toggle-card")[0]).toHaveStyle({
      background: "var(--accent-soft)",
    })

    fireEvent.click(screen.getByRole("button", { name: "Configure Pivot 1" }))
    expect(screen.queryByText("Configure Pivot 1")).not.toBeInTheDocument()
    expect(screen.getByRole("textbox", { name: "Pivot name" })).toHaveValue("Pivot 1")
    expect(screen.queryByRole("checkbox", { name: "Pivot 1" })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", { name: "Back to pivots" }))
    expect(screen.getByRole("checkbox", { name: "Pivot 1" })).toHaveAttribute(
      "aria-checked",
      "true",
    )

    fireEvent.click(screen.getByRole("checkbox", { name: "Pivot 1" }))
    expect(screen.getByRole("checkbox", { name: "Pivot 1" })).toHaveAttribute(
      "aria-checked",
      "false",
    )
    expect(screen.getAllByTestId("explore-toggle-card")[0]).toHaveAttribute(
      "data-state",
      "disabled",
    )

    const persisted = JSON.parse(screen.getByTestId("persisted-config").textContent ?? "{}")
    expect(persisted.pivots[0]).toMatchObject({
      version: 1,
      id: "pivot_1",
      name: "Pivot 1",
      enabled: false,
      filters: [],
      columns: [],
      rows: [],
      values: [],
      formulas: [],
      options: { row_grand_totals: true, column_grand_totals: true, sort_by: null },
    })
  })

  it("searches dtype-labelled fields and applies duplicate/default aggregation rules", () => {
    render(<PivotConfigHarness initialConfig={{ pivots: [fullPivot()] }} />)
    fireEvent.click(screen.getByRole("button", { name: "Configure Pivot 1" }))

    fireEvent.change(screen.getByRole("searchbox", { name: "Search pivot fields" }), {
      target: { value: "claim" },
    })
    expect(screen.getByText("Float64")).toBeInTheDocument()
    expect(screen.queryByText("region")).not.toBeInTheDocument()
    fireEvent.change(screen.getByRole("searchbox", { name: "Search pivot fields" }), {
      target: { value: "" },
    })

    const palette = screen.getByRole("group", { name: "Available pivot fields" })
    expect(within(palette).getAllByText("Add to:")).toHaveLength(3)
    expect(within(palette).queryByRole("checkbox")).not.toBeInTheDocument()
    expect(
      screen.queryByRole("button", { name: /Add selected fields to/i }),
    ).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole("button", { name: "Add region to Rows" }))
    expect(screen.getByRole("group", { name: "region in Rows" })).toBeInTheDocument()

    expect(
      screen.getByRole("button", { name: "Add region to Rows" }),
    ).toBeDisabled()
    fireEvent.click(screen.getByRole("button", { name: "Add region to Values" }))

    fireEvent.click(screen.getByRole("button", { name: "Add claims to Values" }))
    fireEvent.click(screen.getByRole("button", { name: "Add claims to Values" }))
    const valueZone = screen.getByRole("group", { name: "Values fields" })
    expect(within(valueZone).getAllByText("claims")).toHaveLength(2)
    expect(
      within(valueZone).getAllByRole("combobox", { name: "Aggregation for claims" }),
    ).toHaveLength(2)
    expect(
      within(valueZone).getAllByRole("combobox", { name: "Aggregation for claims" })[0],
    ).toHaveValue("sum")

    expect(
      within(valueZone).getByRole("combobox", { name: "Aggregation for region" }),
    ).toHaveValue("count")
  })

  it("defines a shared calculated field once and adds it to each pivot's Values", () => {
    const secondPivot = fullPivot({ id: "pivot_2", name: "Pivot 2" })
    render(
      <PivotConfigHarness
        initialConfig={{ pivots: [fullPivot(), secondPivot] }}
        columns={[
          ...upstreamColumns,
          { name: "total_claims", dtype: "Float64" },
        ]}
      />,
    )
    fireEvent.click(screen.getByRole("button", { name: "Configure Pivot 1" }))

    const availableFields = screen.getByRole("group", { name: "Available pivot fields" })
    const formulaSection = screen.getByTestId("pivot-formula-section")
    const fieldAreas = screen.getByTestId("pivot-field-areas")
    expect(
      availableFields.compareDocumentPosition(formulaSection) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy()
    expect(
      formulaSection.compareDocumentPosition(fieldAreas) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy()
    expect(
      screen.queryByText(/Calculated fields are defined once for this Explore node/),
    ).not.toBeInTheDocument()

    const formulaSearch = screen.getByRole("searchbox", { name: "Search formulas" })
    const formulaList = screen.getByRole("group", { name: "Available formulas" })
    expect(formulaList).toHaveClass("max-h-52", "overflow-y-auto")
    expect(formulaList).not.toHaveClass("h-52")
    expect(within(formulaList).getByText("No formulas yet.")).toBeVisible()

    fireEvent.click(screen.getByRole("button", { name: "Add formula" }))
    const newFormulaEditor = screen.getByRole("group", { name: "New formula" })
    expect(
      formulaList.compareDocumentPosition(newFormulaEditor) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy()
    const sourceFieldButton = screen.getByRole("button", {
      name: "Add total_claims to Formula",
    })
    expect(sourceFieldButton).toBeVisible()
    expect(
      screen.queryByRole("button", { name: "Add total_claims to Values" }),
    ).not.toBeInTheDocument()
    expect(screen.queryByText("Source fields")).not.toBeInTheDocument()
    expect(
      screen.queryByText(/Return one aggregate per pivot group/),
    ).not.toBeInTheDocument()
    const expression = screen.getByRole("textbox", {
      name: "Polars expression",
    })
    fireEvent.click(sourceFieldButton)
    expect(expression).toHaveValue('pl.col("total_claims")')

    fireEvent.change(screen.getByRole("textbox", { name: "Formula name" }), {
      target: { value: "Double average" },
    })
    fireEvent.change(expression, {
      target: {
        value: 'pl.col("total_claims").mean() * 2',
      },
    })
    fireEvent.click(screen.getByRole("button", { name: "Save formula" }))

    expect(screen.queryByRole("textbox", { name: "Polars expression" })).not.toBeInTheDocument()
    expect(
      screen.getByRole("button", { name: "Add total_claims to Values" }),
    ).toBeVisible()
    let formulaField = screen.getByRole("group", {
      name: "Calculated field Double average",
    })
    expect(within(formulaList).getByRole("group", {
      name: "Calculated field Double average",
    })).toBe(formulaField)
    fireEvent.change(formulaSearch, { target: { value: "not found" } })
    expect(
      within(formulaList).queryByRole("group", {
        name: "Calculated field Double average",
      }),
    ).not.toBeInTheDocument()
    expect(within(formulaList).getByText("No formulas match your search.")).toBeVisible()
    fireEvent.change(formulaSearch, { target: { value: "double" } })
    expect(within(formulaList).getByRole("group", {
      name: "Calculated field Double average",
    })).toBeVisible()
    fireEvent.change(formulaSearch, { target: { value: "" } })
    formulaField = within(formulaList).getByRole("group", {
      name: "Calculated field Double average",
    })
    expect(within(formulaField).getByText("double_average")).toBeVisible()
    expect(
      within(formulaField).getByRole("button", { name: "Edit formula Double average" }),
    ).toBeVisible()
    fireEvent.click(
      within(formulaField).getByRole("button", { name: "Add Double average to Values" }),
    )
    expect(
      screen.getByRole("group", { name: "Double average in Values" }),
    ).toBeVisible()

    let persisted = JSON.parse(
      screen.getByTestId("persisted-config").textContent ?? "{}",
    )
    expect(persisted.pivots[0].values).toEqual([])
    expect(persisted.pivot_formulas).toMatchObject([{
      id: "formula_1",
      reference: "double_average",
      display_name: "Double average",
      expression: 'pl.col("total_claims").mean() * 2',
    }])
    expect(persisted.pivots[0].formulas).toEqual(["formula_1"])
    expect(persisted.pivots[1].formulas).toEqual([])

    fireEvent.click(screen.getByRole("button", { name: "Back to pivots" }))
    fireEvent.click(screen.getByRole("button", { name: "Configure Pivot 2" }))
    const sharedFormulaField = screen.getByRole("group", {
      name: "Calculated field Double average",
    })
    expect(
      within(sharedFormulaField).getByRole("button", { name: "Add Double average to Values" }),
    ).toBeEnabled()
    expect(within(sharedFormulaField).queryByText(/Missing input:/)).not.toBeInTheDocument()
    expect(screen.queryByRole("textbox", { name: "Polars expression" })).not.toBeInTheDocument()

    fireEvent.click(
      within(sharedFormulaField).getByRole("button", { name: "Add Double average to Values" }),
    )
    expect(screen.getByRole("group", { name: "Double average in Values" })).toBeVisible()

    persisted = JSON.parse(screen.getByTestId("persisted-config").textContent ?? "{}")
    expect(persisted.pivot_formulas).toHaveLength(1)
    expect(persisted.pivots.map((pivot: { formulas: string[] }) => pivot.formulas)).toEqual([
      ["formula_1"],
      ["formula_1"],
    ])

    fireEvent.click(
      screen.getByRole("button", { name: "Edit formula Double average" }),
    )
    expect(
      screen.getByRole("button", { name: "Add total_claims to Formula" }),
    ).toBeVisible()
    fireEvent.change(screen.getByRole("textbox", { name: "Formula name" }), {
      target: { value: "Twice average" },
    })
    fireEvent.change(screen.getByRole("textbox", { name: "Polars expression" }), {
      target: { value: 'pl.col("total_claims").mean() * 2 + 1' },
    })
    fireEvent.click(screen.getByRole("button", { name: "Save formula" }))

    persisted = JSON.parse(screen.getByTestId("persisted-config").textContent ?? "{}")
    expect(persisted.pivot_formulas).toMatchObject([{
      id: "formula_1",
      reference: "double_average",
      display_name: "Twice average",
      expression: 'pl.col("total_claims").mean() * 2 + 1',
    }])
    expect(persisted.pivots.map((pivot: { formulas: string[] }) => pivot.formulas)).toEqual([
      ["formula_1"],
      ["formula_1"],
    ])
    expect(screen.queryByRole("textbox", { name: "Polars expression" })).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole("button", { name: "Back to pivots" }))
    fireEvent.click(screen.getByRole("button", { name: "Configure Pivot 1" }))
    expect(screen.getByRole("group", { name: "Calculated field Twice average" })).toBeVisible()
    expect(screen.getByRole("group", { name: "Twice average in Values" })).toBeVisible()
  })

  it("deletes a shared formula from every pivot without disturbing other Values", () => {
    const formula: PivotFormulaPlacement = {
      id: "formula_1",
      reference: "double_claims",
      display_name: "Double claims",
      expression: 'pl.col("claims").sum() * 2',
      number_format: "general",
      decimal_places: null,
      use_grouping: true,
    }
    const firstPivot = fullPivot({
      values: [{
        id: "value_1",
        field: "claims",
        aggregation: "sum",
        reference: "claims_sum",
        display_name: "Claims",
      }],
      formulas: [formula],
      value_order: ["value_1", "formula_1"],
    })
    const secondPivot = fullPivot({
      id: "pivot_2",
      name: "Pivot 2",
      formulas: [formula],
      value_order: ["formula_1"],
    })
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true)
    render(
      <PivotConfigHarness
        initialConfig={{
          pivot_formulas: [formula],
          pivots: [
            { ...firstPivot, formulas: [formula.id] },
            { ...secondPivot, formulas: [formula.id] },
          ],
        }}
      />,
    )
    fireEvent.click(screen.getByRole("button", { name: "Configure Pivot 1" }))
    fireEvent.click(screen.getByRole("button", { name: "Edit formula Double claims" }))
    fireEvent.click(screen.getByRole("button", { name: "Delete formula Double claims" }))

    expect(confirm).toHaveBeenCalledWith("Delete Double claims from every pivot?")
    const persisted = JSON.parse(
      screen.getByTestId("persisted-config").textContent ?? "{}",
    )
    expect(persisted.pivot_formulas).toEqual([])
    expect(persisted.pivots.map((pivot: ExplorePivotConfig) => pivot.formulas)).toEqual([
      [],
      [],
    ])
    expect(persisted.pivots.map((pivot: ExplorePivotConfig) => pivot.value_order)).toEqual([
      ["value_1"],
      [],
    ])
    expect(screen.queryByRole("group", { name: "Calculated field Double claims" }))
      .not.toBeInTheDocument()
    expect(screen.getByRole("group", { name: "claims in Values" })).toBeVisible()
    confirm.mockRestore()
  })

  it("keeps concise section titles outside their settings boxes after the field grid", () => {
    render(<PivotConfigHarness initialConfig={{ pivots: [fullPivot()] }} />)
    fireEvent.click(screen.getByRole("button", { name: "Configure Pivot 1" }))
    fireEvent.click(screen.getByRole("button", { name: "Add region to Rows" }))
    fireEvent.click(screen.getByRole("button", { name: "Add year to Columns" }))
    fireEvent.click(screen.getByRole("button", { name: "Add claims to Values" }))
    fireEvent.click(screen.getByRole("button", { name: "Add claims to Values" }))

    const areas = screen.getByTestId("pivot-field-areas")
    const numberFormatting = screen.getByTestId("pivot-formatting-section")
    const sorting = screen.getByTestId("pivot-sorting-section")
    const conditionalFormatting = screen.getByTestId("pivot-conditional-formatting-section")
    const sortingHeading = within(sorting).getByRole("heading", { name: "Sorting" })
    const formattingHeading = within(numberFormatting).getByRole("heading", { name: "Formatting" })
    const conditionalFormattingHeading = within(conditionalFormatting).getByRole("heading", {
      name: "Conditional Formatting",
    })
    expect(
      areas.compareDocumentPosition(sorting) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy()
    expect(
      sorting.compareDocumentPosition(numberFormatting) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy()
    expect(
      numberFormatting.compareDocumentPosition(conditionalFormatting) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy()
    expect(screen.queryByText("Configure Pivot 1")).not.toBeInTheDocument()
    expect(screen.getByRole("textbox", { name: "Pivot name" })).toBeVisible()
    for (const heading of [sortingHeading, formattingHeading, conditionalFormattingHeading]) {
      expect(heading.closest(".border")).toBeNull()
      expect(heading.firstElementChild).toHaveClass(
        "text-[11px]",
        "font-bold",
        "uppercase",
        "tracking-[0.08em]",
      )
      expect(heading.firstElementChild).toHaveStyle({ color: "var(--text-muted)" })
    }
    expect(screen.queryByText("Format numeric Columns, Rows, and Values shown in the table."))
      .not.toBeInTheDocument()

    const sortingSettings = sortingHeading.nextElementSibling as HTMLElement
    const formattingSettings = formattingHeading.nextElementSibling as HTMLElement
    const conditionalFormattingSettings = conditionalFormattingHeading.nextElementSibling as HTMLElement
    for (const settings of [sortingSettings, formattingSettings, conditionalFormattingSettings]) {
      expect(settings).toHaveClass("mt-1.5", "rounded-lg", "border", "p-3")
    }
    expect(sorting).not.toHaveClass("border")
    expect(numberFormatting).not.toHaveClass("border")
    expect(conditionalFormatting).not.toHaveClass("border")
    expect(within(areas).queryByRole("combobox", { name: /Label order/i })).not.toBeInTheDocument()
    expect(within(areas).queryByRole("combobox", { name: /Sort rows by/i })).not.toBeInTheDocument()
    expect(within(areas).queryByRole("combobox", { name: /Colour scale/i })).not.toBeInTheDocument()

    const sortingControls = within(sortingSettings).getByTestId("pivot-sorting-controls")
    expect(sortingControls).toHaveClass("grid", "grid-cols-2")
    const sortBy = within(sortingControls).getByRole("combobox", { name: "Sort by" })
    const order = within(sortingControls).getByRole("combobox", { name: "Order" })
    expect(sortBy.closest("label")?.parentElement).toBe(sortingControls)
    expect(order.closest("label")?.parentElement).toBe(sortingControls)
    expect(within(sortBy).getByRole("option", { name: "Default — Row labels" })).toBeInTheDocument()
    fireEvent.change(sortBy, { target: { value: "row_1" } })
    fireEvent.change(order, { target: { value: "descending" } })
    fireEvent.change(sortBy, { target: { value: "value_2" } })

    const addRule = within(conditionalFormattingSettings).getByRole("button", {
      name: "Add conditional formatting rule",
    })
    expect(within(conditionalFormattingSettings).getByText("No conditional formatting rules."))
      .toBeInTheDocument()
    const emptyRules = within(conditionalFormattingSettings).getByText("No conditional formatting rules.")
    expect(
      emptyRules.compareDocumentPosition(addRule) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy()
    fireEvent.click(addRule)
    fireEvent.click(addRule)
    expect(within(conditionalFormattingSettings).queryByText(
      "All compatible Value fields already have rules.",
    )).not.toBeInTheDocument()

    const rules = within(conditionalFormatting).getAllByRole("group", {
      name: /Conditional formatting rule for claims/,
    })
    expect(rules).toHaveLength(2)
    expect(within(rules[0]).getByRole("combobox", {
      name: "Value field for conditional formatting rule 1",
    })).toHaveValue("value_1")
    expect(within(rules[1]).getByRole("combobox", {
      name: "Value field for conditional formatting rule 2",
    })).toHaveValue("value_2")
    const firstSplit = within(rules[0]).getByRole("combobox", {
      name: "Split scale by for conditional formatting rule 1",
    })
    const secondSplit = within(rules[1]).getByRole("combobox", {
      name: "Split scale by for conditional formatting rule 2",
    })
    expect(within(firstSplit).getByRole("option", { name: "None — entire Value" })).toBeInTheDocument()
    expect(within(firstSplit).getByRole("option", { name: "Row — region" })).toBeInTheDocument()
    expect(within(firstSplit).getByRole("option", { name: "Column — year" })).toBeInTheDocument()
    expect(within(firstSplit).queryByRole("option", { name: /Filter|Value — claims/ })).not.toBeInTheDocument()
    fireEvent.change(firstSplit, { target: { value: "row_1" } })
    fireEvent.change(secondSplit, { target: { value: "column_1" } })
    const forwardPreview = within(rules[0]).getByRole("img", {
      name: "Colour scale preview for claims",
    })
    expect(forwardPreview).toBeVisible()
    expect(forwardPreview.children[1]).toHaveStyle({
      background: "linear-gradient(to right, #F8696B, #FFEB84, #63BE7B)",
    })
    expect(within(rules[1]).getByRole("img", {
      name: "Colour scale preview for claims",
    })).toBeVisible()
    expect(addRule).toBeDisabled()
    expect(
      rules[1].compareDocumentPosition(addRule) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy()

    fireEvent.change(
      within(rules[1]).getByRole("combobox", {
        name: "Colour scale for conditional formatting rule 2",
      }),
      { target: { value: "low_green_high_red" } },
    )
    const reversePreview = within(rules[1]).getByRole("img", {
      name: "Colour scale preview for claims",
    })
    expect(reversePreview.children[1]).toHaveStyle({
      background: "linear-gradient(to right, #63BE7B, #FFEB84, #F8696B)",
    })

    const persisted = JSON.parse(screen.getByTestId("persisted-config").textContent ?? "{}")
    expect(persisted.pivots[0].options.sort_by).toBe("value_2")
    expect(persisted.pivots[0].rows[0].sort).toBe("ascending")
    expect(persisted.pivots[0].values.map((value: { sort_rows: string }) => value.sort_rows)).toEqual([
      "none",
      "descending",
    ])
    expect(persisted.pivots[0].values.map((value: { color_scale: string }) => value.color_scale))
      .toEqual(["low_red_high_green", "low_green_high_red"])
    expect(persisted.pivots[0].values.map((value: { color_scale_split_by: string | null }) => value.color_scale_split_by))
      .toEqual(["row_1", "column_1"])

    const valueZone = screen.getByRole("group", { name: "Values fields" })
    const aggregations = within(valueZone).getAllByRole("combobox", {
      name: "Aggregation for claims",
    })
    fireEvent.change(aggregations[0], { target: { value: "count" } })
    expect(within(rules[0]).getByRole("combobox", {
      name: "Colour scale for conditional formatting rule 1",
    })).toBeEnabled()

    fireEvent.click(screen.getByRole("button", { name: "Add region to Values" }))
    fireEvent.click(within(rules[0]).getByRole("button", {
      name: /Remove conditional formatting rule for claims/,
    }))
    expect(within(conditionalFormatting).getAllByRole("group", {
      name: /Conditional formatting rule for claims/,
    })).toHaveLength(1)
    expect(addRule).toBeEnabled()
    fireEvent.click(addRule)

    const currentRules = within(conditionalFormatting).getAllByRole("group", {
      name: /Conditional formatting rule for/,
    })
    expect(currentRules).toHaveLength(2)
    const newRuleValue = within(currentRules[1]).getByRole("combobox", {
      name: "Value field for conditional formatting rule 2",
    })
    fireEvent.change(newRuleValue, { target: { value: "value_3" } })
    expect(
      JSON.parse(screen.getByTestId("persisted-config").textContent ?? "{}")
        .pivots[0].values.map((value: { color_scale: string }) => value.color_scale),
    ).toEqual(["low_red_high_green", "none", "low_green_high_red"])
    expect(
      JSON.parse(screen.getByTestId("persisted-config").textContent ?? "{}")
        .pivots[0].values.map((value: { color_scale_split_by: string | null }) => value.color_scale_split_by),
    ).toEqual([null, null, "column_1"])
    fireEvent.change(
      screen.getByRole("combobox", { name: "Aggregation for region" }),
      { target: { value: "min" } },
    )
    expect(within(conditionalFormatting).getAllByRole("group", {
      name: /Conditional formatting rule for/,
    })).toHaveLength(1)
    expect(
      JSON.parse(screen.getByTestId("persisted-config").textContent ?? "{}")
        .pivots[0].values[2].color_scale_split_by,
    ).toBeNull()
  })

  it("formats displayed placements as numbers, percentages, or currencies without offering Filters", () => {
    const onCommittedUpdate = vi.fn()
    render(
      <PivotConfigHarness
        initialConfig={{
          pivots: [
            fullPivot({
              filters: [{ id: "filter_1", field: "region", members: [] }],
              columns: [{ id: "column_1", field: "year" }],
              rows: [{ id: "row_1", field: "region" }],
              values: [
                {
                  id: "value_1",
                  field: "claims",
                  aggregation: "sum",
                  reference: "claims_sum",
                  display_name: "Claims",
                },
                {
                  id: "value_2",
                  field: "region",
                  aggregation: "count",
                  reference: "region_count",
                  display_name: "Region count",
                },
              ],
            }),
          ],
        }}
        onCommittedUpdate={onCommittedUpdate}
      />,
    )
    fireEvent.click(screen.getByRole("button", { name: "Configure Pivot 1" }))

    const formatting = screen.getByTestId("pivot-formatting-section")
    expect(within(formatting).getByRole("heading", { name: "Formatting" })).toBeVisible()
    expect(within(formatting).getByRole("group", {
      name: "Column 1 — year formatting",
    })).toBeVisible()
    expect(within(formatting).getByRole("group", {
      name: "Row 1 — region formatting",
    })).toHaveTextContent("Not numeric")
    expect(within(formatting).queryByRole("group", {
      name: /Filter 1/,
    })).not.toBeInTheDocument()

    const columnFormat = within(formatting).getByRole("combobox", {
      name: "Number format for Column 1 — year",
    })
    const columnDecimals = within(formatting).getByRole("combobox", {
      name: "Decimal places for Column 1 — year",
    })
    const columnGrouping = within(formatting).getByRole("checkbox", {
      name: "Use thousands separator for Column 1 — year",
    })
    const valueFormat = within(formatting).getByRole("combobox", {
      name: "Number format for Value 1 — Claims",
    })
    const valueDecimals = within(formatting).getByRole("combobox", {
      name: "Decimal places for Value 1 — Claims",
    })
    const countDecimals = within(formatting).getByRole("combobox", {
      name: "Decimal places for Value 2 — Region count",
    })
    const countGrouping = within(formatting).getByRole("checkbox", {
      name: "Use thousands separator for Value 2 — Region count",
    })
    expect(columnFormat).toHaveValue("general")
    expect(columnDecimals).toHaveValue("automatic")
    expect(columnGrouping).not.toBeChecked()
    expect(valueDecimals).toHaveValue("automatic")
    expect(countDecimals).toBeEnabled()
    expect(countGrouping).not.toBeChecked()
    expect(within(columnFormat).getByRole("option", { name: "General" })).toBeVisible()
    expect(within(columnFormat).getByRole("option", { name: "Number" })).toBeVisible()
    expect(within(columnFormat).getByRole("option", { name: "Percentage" })).toBeVisible()
    expect(within(columnFormat).getByRole("option", { name: "Currency (£ GBP)" })).toBeVisible()
    expect(within(columnFormat).getByRole("option", { name: "Currency (US$ USD)" })).toBeVisible()
    expect(within(columnFormat).getByRole("option", { name: "Currency (€ EUR)" })).toBeVisible()
    expect(within(columnDecimals).getByRole("option", { name: "Automatic" })).toBeVisible()
    expect(within(columnDecimals).getByRole("option", { name: "10" })).toBeVisible()

    fireEvent.change(columnFormat, { target: { value: "percent" } })
    expect(columnGrouping).toBeChecked()
    fireEvent.change(columnDecimals, { target: { value: "0" } })
    fireEvent.click(columnGrouping)
    fireEvent.change(valueFormat, { target: { value: "currency_gbp" } })
    fireEvent.change(valueDecimals, { target: { value: "2" } })
    fireEvent.click(countGrouping)

    expect(onCommittedUpdate).toHaveBeenCalledTimes(6)
    const persisted = JSON.parse(screen.getByTestId("persisted-config").textContent ?? "{}")
    expect(persisted.pivots[0].columns[0]).toMatchObject({
      number_format: "percent",
      decimal_places: 0,
      use_grouping: false,
    })
    expect(persisted.pivots[0].rows[0].decimal_places).toBeNull()
    expect(persisted.pivots[0].values[0]).toMatchObject({
      number_format: "currency_gbp",
      decimal_places: 2,
      use_grouping: true,
    })
    expect(persisted.pivots[0].values[1]).toMatchObject({
      number_format: "number",
      decimal_places: null,
      use_grouping: true,
    })
  })

  it("shows every persisted conditional formatting rule without selecting between them", () => {
    render(
      <PivotConfigHarness
        initialConfig={{
          pivots: [
            fullPivot({
              values: [
                {
                  id: "value_1",
                  field: "claims",
                  aggregation: "sum",
                  reference: "claims_sum",
                  display_name: "Claims",
                  sort_rows: "none",
                  color_scale: "low_red_high_green",
                },
                {
                  id: "value_2",
                  field: "region",
                  aggregation: "count",
                  reference: "region_count",
                  display_name: "Regions",
                  sort_rows: "none",
                  color_scale: "low_green_high_red",
                },
              ],
            }),
          ],
        }}
      />,
    )
    fireEvent.click(screen.getByRole("button", { name: "Configure Pivot 1" }))

    const formatting = screen.getByTestId("pivot-conditional-formatting-section")
    expect(within(formatting).getByRole("group", {
      name: "Conditional formatting rule for Claims",
    })).toBeInTheDocument()
    expect(within(formatting).getByRole("group", {
      name: "Conditional formatting rule for Regions",
    })).toBeInTheDocument()
    expect(within(formatting).getAllByRole("img", {
      name: /Colour scale preview/,
    })).toHaveLength(2)
  })

  it("keeps per-field actions in a large scrolling palette", () => {
    const onCommittedUpdate = vi.fn()
    const manyColumns = [
      ...upstreamColumns,
      ...Array.from({ length: 30 }, (_, index) => ({
        name: `field_${index + 1}`,
        dtype: "String",
      })),
    ]
    render(
      <PivotConfigHarness
        initialConfig={{ pivots: [fullPivot()] }}
        columns={manyColumns}
        onCommittedUpdate={onCommittedUpdate}
      />,
    )
    fireEvent.click(screen.getByRole("button", { name: "Configure Pivot 1" }))

    const palette = screen.getByRole("group", {
      name: "Available pivot fields",
    })
    expect(palette).toHaveClass("h-52", "overflow-y-auto")
    expect(
      within(palette).getByRole("button", { name: "Add field_30 to Rows" }),
    ).toBeInTheDocument()
    expect(within(palette).queryByRole("checkbox")).not.toBeInTheDocument()

    fireEvent.click(
      within(palette).getByRole("button", { name: "Add region to Rows" }),
    )
    fireEvent.click(
      within(palette).getByRole("button", { name: "Add year to Rows" }),
    )

    expect(onCommittedUpdate).toHaveBeenCalledTimes(2)
    expect(
      screen.queryByRole("button", { name: /Add selected fields to/i }),
    ).not.toBeInTheDocument()
    const persisted = JSON.parse(
      screen.getByTestId("persisted-config").textContent ?? "{}",
    )
    expect(persisted.pivots[0].rows).toEqual([
      {
        id: "row_1",
        field: "region",
        sort: "ascending",
        number_format: "general",
        decimal_places: null,
        use_grouping: true,
      },
      {
        id: "row_2",
        field: "year",
        sort: "ascending",
        number_format: "general",
        decimal_places: null,
        use_grouping: true,
      },
    ])
  })

  it("commits name, reorder/remove/options without a manual preview action", () => {
    render(
      <PivotConfigHarness
        initialConfig={{
          pivots: [
            fullPivot({
              rows: [
                { id: "row_1", field: "region" },
                { id: "row_2", field: "year" },
              ],
            }),
          ],
        }}
      />,
    )
    fireEvent.click(screen.getByRole("button", { name: "Configure Pivot 1" }))

    const name = screen.getByRole("textbox", { name: "Pivot name" })
    fireEvent.change(name, { target: { value: "Claims analysis" } })
    fireEvent.blur(name)
    fireEvent.keyDown(screen.getByRole("group", { name: "year in Rows" }), {
      key: "ArrowUp",
    })
    expect(
      JSON.parse(screen.getByTestId("persisted-config").textContent ?? "{}")
        .pivots[0].rows,
    ).toEqual([
      {
        id: "row_2",
        field: "year",
        sort: "ascending",
        number_format: "general",
        decimal_places: null,
        use_grouping: true,
      },
      {
        id: "row_1",
        field: "region",
        sort: "ascending",
        number_format: "general",
        decimal_places: null,
        use_grouping: true,
      },
    ])
    fireEvent.click(screen.getByRole("button", { name: "Remove region from Rows" }))
    fireEvent.click(screen.getByRole("checkbox", { name: "Show row grand totals" }))

    expect(
      screen.queryByRole("button", { name: "Update preview" }),
    ).not.toBeInTheDocument()
    const persisted = JSON.parse(screen.getByTestId("persisted-config").textContent ?? "{}")
    expect(persisted.pivots[0]).toMatchObject({
      name: "Claims analysis",
      rows: [{ id: "row_2", field: "year" }],
      options: { row_grand_totals: false, column_grand_totals: true },
    })
  })

  it("loads and persists typed exact filter members", async () => {
    const loadFilterMembers = vi.fn().mockResolvedValue({
      status: "ok",
      field: "region",
      members: [
        { key: { kind: "string", value: "North" }, label: "North", count: 12 },
        { key: { kind: "null", value: null }, label: "(blank)", count: 2 },
      ],
      failure: null,
    })
    render(
      <PivotConfigHarness
        initialConfig={{ pivots: [fullPivot()] }}
        loadFilterMembers={loadFilterMembers}
      />,
    )
    fireEvent.click(screen.getByRole("button", { name: "Configure Pivot 1" }))
    fireEvent.click(screen.getByRole("button", { name: "Add region to Filters" }))
    fireEvent.click(screen.getByRole("button", { name: "Choose members for region" }))

    expect(await screen.findByRole("checkbox", { name: "North (12)" })).toBeInTheDocument()
    fireEvent.click(screen.getByRole("checkbox", { name: "North (12)" }))

    await waitFor(() => {
      const persisted = JSON.parse(screen.getByTestId("persisted-config").textContent ?? "{}")
      expect(persisted.pivots[0].filters[0].members).toEqual([
        { kind: "string", value: "North" },
      ])
    })
    expect(loadFilterMembers).toHaveBeenCalledWith("region", "", expect.any(AbortSignal))
  })

  it("debounces nonempty filter-member searches and only requests the final query", async () => {
    vi.useFakeTimers()
    const loadFilterMembers = vi.fn().mockResolvedValue({
      status: "ok",
      field: "region",
      members: [],
      failure: null,
    })
    render(
      <PivotConfigHarness
        initialConfig={{ pivots: [fullPivot()] }}
        loadFilterMembers={loadFilterMembers}
      />,
    )
    fireEvent.click(screen.getByRole("button", { name: "Configure Pivot 1" }))
    fireEvent.click(screen.getByRole("button", { name: "Add region to Filters" }))
    fireEvent.click(screen.getByRole("button", { name: "Choose members for region" }))
    expect(loadFilterMembers).toHaveBeenCalledTimes(1)

    const search = screen.getByRole("searchbox", { name: "Search members for region" })
    fireEvent.change(search, { target: { value: "N" } })
    fireEvent.change(search, { target: { value: "No" } })
    expect(loadFilterMembers).toHaveBeenCalledTimes(1)

    await act(async () => {
      await vi.advanceTimersByTimeAsync(249)
    })
    expect(loadFilterMembers).toHaveBeenCalledTimes(1)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1)
    })
    expect(loadFilterMembers).toHaveBeenCalledTimes(2)
    expect(loadFilterMembers).toHaveBeenLastCalledWith("region", "No", expect.any(AbortSignal))
  })

  it("discards loaded members as soon as the Explore cache identity changes", async () => {
    let resolveSecond: (value: unknown) => void = () => {}
    const loadFilterMembers = vi
      .fn()
      .mockResolvedValueOnce({
        status: "ok",
        field: "region",
        members: [
          { key: { kind: "string", value: "North" }, label: "North", count: 12 },
        ],
        failure: null,
      })
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            resolveSecond = resolve
          }),
      )
    const { rerender } = render(
      <PivotConfigHarness
        initialConfig={{ pivots: [fullPivot()] }}
        loadFilterMembers={loadFilterMembers}
        currentConfigHash="hash-before"
      />,
    )
    fireEvent.click(screen.getByRole("button", { name: "Configure Pivot 1" }))
    fireEvent.click(screen.getByRole("button", { name: "Add region to Filters" }))
    fireEvent.click(screen.getByRole("button", { name: "Choose members for region" }))
    expect(await screen.findByRole("checkbox", { name: "North (12)" })).toBeInTheDocument()

    // A changed Explore cache identity (a graph or source change) must hide
    // the previous dataset's members immediately, not leave them selectable
    // while the replacement response is in flight.
    rerender(
      <PivotConfigHarness
        initialConfig={{ pivots: [fullPivot()] }}
        loadFilterMembers={loadFilterMembers}
        currentConfigHash="hash-after"
      />,
    )
    expect(screen.queryByRole("checkbox", { name: "North (12)" })).toBeNull()
    expect(screen.getByText(/Loading members/)).toBeVisible()

    resolveSecond({
      status: "ok",
      field: "region",
      members: [{ key: { kind: "string", value: "South" }, label: "South", count: 3 }],
      failure: null,
    })
    expect(await screen.findByRole("checkbox", { name: "South (3)" })).toBeInTheDocument()
  })

  it("keeps loaded members visible when only the loader reference changes", async () => {
    const loaderA = vi.fn().mockResolvedValue({
      status: "ok",
      field: "region",
      members: [
        { key: { kind: "string", value: "North" }, label: "North", count: 12 },
      ],
      failure: null,
    })
    const loaderB = vi.fn().mockReturnValue(new Promise(() => {}))
    const { rerender } = render(
      <PivotConfigHarness
        initialConfig={{ pivots: [fullPivot()] }}
        loadFilterMembers={loaderA}
      />,
    )
    fireEvent.click(screen.getByRole("button", { name: "Configure Pivot 1" }))
    fireEvent.click(screen.getByRole("button", { name: "Add region to Filters" }))
    fireEvent.click(screen.getByRole("button", { name: "Choose members for region" }))
    expect(await screen.findByRole("checkbox", { name: "North (12)" })).toBeInTheDocument()

    // A display-only edit may rebuild callbacks, but with an unchanged
    // Explore cache identity the member list must not blank or become
    // unselectable while any incidental reload is in flight.
    rerender(
      <PivotConfigHarness
        initialConfig={{ pivots: [fullPivot()] }}
        loadFilterMembers={loaderB}
      />,
    )
    expect(screen.getByRole("checkbox", { name: "North (12)" })).toBeInTheDocument()
  })

  it("renders an Excel-style area grid and drags a placement between zones", () => {
    const onCommittedUpdate = vi.fn()
    render(
      <PivotConfigHarness
        initialConfig={{
          pivots: [
            fullPivot({
              columns: [{
                id: "column_1",
                field: "year",
                number_format: "currency_usd",
                decimal_places: 4,
                use_grouping: false,
              }],
              rows: [{ id: "row_1", field: "region" }],
              values: [{
                id: "value_1",
                field: "claims",
                aggregation: "sum",
                reference: "claims_sum",
                display_name: "Claims",
                sort_rows: "none",
                color_scale: "low_red_high_green",
                color_scale_split_by: "column_1",
              }],
            }),
          ],
        }}
        onCommittedUpdate={onCommittedUpdate}
      />,
    )
    fireEvent.click(screen.getByRole("button", { name: "Configure Pivot 1" }))

    expect(screen.getByText("Drag fields between areas below:")).toBeInTheDocument()
    const areas = screen.getByTestId("pivot-field-areas")
    expect(areas).toHaveClass("grid", "grid-cols-2")
    expect(
      Array.from(areas.children).map((area) => area.getAttribute("aria-label")),
    ).toEqual(["Filters fields", "Columns fields", "Rows fields", "Values fields"])

    const source = screen.getByRole("group", { name: "year in Columns" })
    const target = screen.getByRole("group", { name: "region in Rows" })
    expect(source).toHaveAttribute("draggable", "true")

    const dataTransfer = createDragDataTransfer()
    fireEvent.dragStart(source, { dataTransfer })
    expect(dataTransfer.effectAllowed).toBe("move")
    expect(dataTransfer.setData).toHaveBeenCalledWith(
      "application/haute-pivot-placement",
      JSON.stringify({ sourceZone: "columns", placementId: "column_1" }),
    )

    fireEvent.dragOver(target, { dataTransfer })
    expect(dataTransfer.dropEffect).toBe("move")
    expect(target).toHaveAttribute("data-drop-position", "before")
    fireEvent.drop(target, { dataTransfer })

    const persisted = JSON.parse(screen.getByTestId("persisted-config").textContent ?? "{}")
    expect(persisted.pivots[0].columns).toEqual([])
    expect(persisted.pivots[0].values[0].color_scale_split_by).toBe("column_1")
    expect(persisted.pivots[0].rows).toEqual([
      {
        id: "column_1",
        field: "year",
        sort: "ascending",
        number_format: "currency_usd",
        decimal_places: 4,
        use_grouping: false,
      },
      {
        id: "row_1",
        field: "region",
        sort: "ascending",
        number_format: "general",
        decimal_places: null,
        use_grouping: true,
      },
    ])
    expect(onCommittedUpdate).toHaveBeenCalledTimes(1)

    const movedSource = screen.getByRole("group", { name: "year in Rows" })
    const filterTarget = screen.getByRole("group", { name: "Filters fields" })
    const secondTransfer = createDragDataTransfer()
    fireEvent.dragStart(movedSource, { dataTransfer: secondTransfer })
    fireEvent.dragOver(filterTarget, { dataTransfer: secondTransfer })
    fireEvent.drop(filterTarget, { dataTransfer: secondTransfer })

    const movedToFilter = JSON.parse(
      screen.getByTestId("persisted-config").textContent ?? "{}",
    )
    expect(movedToFilter.pivots[0].filters).toEqual([
      { id: "column_1", field: "year", members: [] },
    ])
    expect(movedToFilter.pivots[0].values[0].color_scale_split_by).toBeNull()
    expect(onCommittedUpdate).toHaveBeenCalledTimes(2)

    const filterSource = screen.getByRole("group", { name: "year in Filters" })
    const columnTarget = screen.getByRole("group", { name: "Columns fields" })
    const thirdTransfer = createDragDataTransfer()
    fireEvent.dragStart(filterSource, { dataTransfer: thirdTransfer })
    fireEvent.dragOver(columnTarget, { dataTransfer: thirdTransfer })
    fireEvent.drop(columnTarget, { dataTransfer: thirdTransfer })

    const movedBackToDisplay = JSON.parse(
      screen.getByTestId("persisted-config").textContent ?? "{}",
    )
    expect(movedBackToDisplay.pivots[0].filters).toEqual([])
    expect(movedBackToDisplay.pivots[0].columns).toEqual([
      {
        id: "column_1",
        field: "year",
        number_format: "general",
        decimal_places: null,
        use_grouping: true,
      },
    ])
    expect(movedBackToDisplay.pivots[0].values[0].color_scale_split_by).toBeNull()
    expect(onCommittedUpdate).toHaveBeenCalledTimes(3)
  })

  it("regenerates a semantic Value reference after a field leaves Values", () => {
    render(
      <PivotConfigHarness
        initialConfig={{
          pivots: [
            fullPivot({
              values: [{
                id: "value_1",
                field: "claims",
                aggregation: "sum",
                reference: "claims_sum",
                display_name: "Claims",
              }],
            }),
          ],
        }}
      />,
    )
    fireEvent.click(screen.getByRole("button", { name: "Configure Pivot 1" }))

    const valueSource = screen.getByRole("group", { name: "claims in Values" })
    const rowsTarget = screen.getByRole("group", { name: "Rows fields" })
    const toRows = createDragDataTransfer()
    fireEvent.dragStart(valueSource, { dataTransfer: toRows })
    fireEvent.dragOver(rowsTarget, { dataTransfer: toRows })
    fireEvent.drop(rowsTarget, { dataTransfer: toRows })

    let persisted = JSON.parse(screen.getByTestId("persisted-config").textContent ?? "{}")
    expect(persisted.pivots[0].values).toEqual([])
    expect(persisted.pivots[0].rows[0]).not.toHaveProperty("reference")

    const rowSource = screen.getByRole("group", { name: "claims in Rows" })
    const valuesTarget = screen.getByRole("group", { name: "Values fields" })
    const toValues = createDragDataTransfer()
    fireEvent.dragStart(rowSource, { dataTransfer: toValues })
    fireEvent.dragOver(valuesTarget, { dataTransfer: toValues })
    fireEvent.drop(valuesTarget, { dataTransfer: toValues })

    persisted = JSON.parse(screen.getByTestId("persisted-config").textContent ?? "{}")
    expect(persisted.pivots[0].rows).toEqual([])
    expect(persisted.pivots[0].values[0]).toMatchObject({
      field: "claims",
      aggregation: "sum",
      reference: "claims_sum",
    })
  })

  it("rejects a drag into a duplicate-restricted target zone", () => {
    const onCommittedUpdate = vi.fn()
    render(
      <PivotConfigHarness
        initialConfig={{
          pivots: [
            fullPivot({
              columns: [{ id: "column_1", field: "year" }],
              rows: [{ id: "row_1", field: "year" }],
            }),
          ],
        }}
        onCommittedUpdate={onCommittedUpdate}
      />,
    )
    fireEvent.click(screen.getByRole("button", { name: "Configure Pivot 1" }))

    const source = screen.getByRole("group", { name: "year in Columns" })
    const target = screen.getByRole("group", { name: "Rows fields" })
    const dataTransfer = createDragDataTransfer()
    fireEvent.dragStart(source, { dataTransfer })
    expect(target).toHaveAttribute("data-drop-state", "blocked")
    fireEvent.dragOver(target, { dataTransfer })
    fireEvent.drop(target, { dataTransfer })

    expect(onCommittedUpdate).not.toHaveBeenCalled()
    const persisted = JSON.parse(screen.getByTestId("persisted-config").textContent ?? "{}")
    expect(persisted.pivots[0].columns).toEqual([
      { id: "column_1", field: "year", number_format: "general", decimal_places: null, use_grouping: true },
    ])
    expect(persisted.pivots[0].rows).toEqual([{ id: "row_1", field: "year", number_format: "general", decimal_places: null, use_grouping: true }])
  })

  it("reorders within an area by dragging and omits redundant movement controls", () => {
    const onCommittedUpdate = vi.fn()
    render(
      <PivotConfigHarness
        initialConfig={{
          pivots: [
            fullPivot({
              rows: [
                { id: "row_1", field: "region" },
                { id: "row_2", field: "year" },
              ],
            }),
          ],
        }}
        onCommittedUpdate={onCommittedUpdate}
      />,
    )
    fireEvent.click(screen.getByRole("button", { name: "Configure Pivot 1" }))

    expect(
      screen.queryByRole("combobox", { name: /^Move .* from / }),
    ).not.toBeInTheDocument()
    expect(
      screen.queryByRole("button", { name: /^Move .* (up|down) in / }),
    ).not.toBeInTheDocument()

    const source = screen.getByRole("group", { name: "region in Rows" })
    const target = screen.getByRole("group", { name: "Rows fields" })
    const dataTransfer = createDragDataTransfer()
    fireEvent.dragStart(source, { dataTransfer })
    fireEvent.dragOver(target, { dataTransfer })
    fireEvent.drop(target, { dataTransfer })

    const persisted = JSON.parse(screen.getByTestId("persisted-config").textContent ?? "{}")
    expect(persisted.pivots[0].rows).toEqual([
      {
        id: "row_2",
        field: "year",
        sort: "ascending",
        number_format: "general",
        decimal_places: null,
        use_grouping: true,
      },
      {
        id: "row_1",
        field: "region",
        sort: "ascending",
        number_format: "general",
        decimal_places: null,
        use_grouping: true,
      },
    ])
    expect(onCommittedUpdate).toHaveBeenCalledTimes(1)
  })

  it("reorders mixed Value outputs without moving formulas into another zone", () => {
    const formula: PivotFormulaPlacement = {
      id: "formula_1",
      reference: "claims_per_year",
      display_name: "Claims per year",
      expression: 'pl.lit(1)',
      number_format: "general",
      decimal_places: null,
      use_grouping: true,
    }
    const selectedFormulaPivot = fullPivot({
      values: [
        { id: "value_1", field: "claims", aggregation: "sum", reference: "claims_sum", display_name: "Claims" },
        { id: "value_2", field: "year", aggregation: "count", reference: "year_count", display_name: "Years" },
      ],
      formulas: [formula],
      value_order: ["value_1", "formula_1", "value_2"],
    })
    render(
      <PivotConfigHarness initialConfig={{
        pivot_formulas: [formula],
        pivots: [{ ...selectedFormulaPivot, formulas: ["formula_1"] }],
      }} />,
    )
    fireEvent.click(screen.getByRole("button", { name: "Configure Pivot 1" }))

    const formulaCard = screen.getByRole("group", { name: "Claims per year in Values" })
    const claimsCard = screen.getByRole("group", { name: "claims in Values" })
    expect(formulaCard).toHaveAttribute("draggable", "true")
    expect(formulaCard).toHaveAttribute("tabindex", "0")
    expect(claimsCard.compareDocumentPosition(formulaCard) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()

    fireEvent.keyDown(formulaCard, { key: "ArrowDown" })
    let persisted = JSON.parse(screen.getByTestId("persisted-config").textContent ?? "{}")
    expect(persisted.pivots[0].value_order).toEqual(["value_1", "value_2", "formula_1"])

    fireEvent.keyDown(formulaCard, { key: "ArrowLeft" })
    persisted = JSON.parse(screen.getByTestId("persisted-config").textContent ?? "{}")
    expect(persisted.pivots[0].value_order).toEqual(["value_1", "value_2", "formula_1"])

    const transfer = createDragDataTransfer()
    fireEvent.dragStart(formulaCard, { dataTransfer: transfer })
    for (const zone of ["Filters", "Columns", "Rows"]) {
      expect(screen.getByRole("group", { name: `${zone} fields` })).toHaveAttribute(
        "data-drop-state",
        "blocked",
      )
    }
    fireEvent.dragEnd(formulaCard, { dataTransfer: transfer })

    const reorderTransfer = createDragDataTransfer()
    fireEvent.dragStart(formulaCard, { dataTransfer: reorderTransfer })
    fireEvent.dragOver(claimsCard, { dataTransfer: reorderTransfer })
    fireEvent.drop(claimsCard, { dataTransfer: reorderTransfer })

    persisted = JSON.parse(screen.getByTestId("persisted-config").textContent ?? "{}")
    expect(persisted.pivots[0].value_order).toEqual([
      "formula_1",
      "value_1",
      "value_2",
    ])
  })

  it("appends keyboard-moved fields after the full mixed Values list", () => {
    const formula: PivotFormulaPlacement = {
      id: "formula_1",
      reference: "claims_per_year",
      display_name: "Claims per year",
      expression: "pl.lit(1)",
      number_format: "general",
      decimal_places: null,
      use_grouping: true,
    }
    const configuredPivot = fullPivot({
      rows: [{ id: "row_1", field: "year" }],
      values: [
        {
          id: "value_1",
          field: "claims",
          aggregation: "sum",
          reference: "claims_sum",
          display_name: "Claims",
        },
      ],
      formulas: [formula],
      value_order: ["formula_1", "value_1"],
    })
    render(
      <PivotConfigHarness
        initialConfig={{
          pivot_formulas: [formula],
          pivots: [{ ...configuredPivot, formulas: ["formula_1"] }],
        }}
      />,
    )
    fireEvent.click(screen.getByRole("button", { name: "Configure Pivot 1" }))

    fireEvent.keyDown(screen.getByRole("group", { name: "year in Rows" }), {
      key: "ArrowRight",
    })

    const persisted = JSON.parse(
      screen.getByTestId("persisted-config").textContent ?? "{}",
    )
    expect(persisted.pivots[0].value_order).toEqual([
      "formula_1",
      "value_1",
      "row_1",
    ])
  })

  it("retains missing fields as invalid chips and rejects blank or duplicate names", () => {
    render(
      <PivotConfigHarness
        initialConfig={{
          pivots: [
            fullPivot({ rows: [{ id: "row_1", field: "removed_field" }] }),
            fullPivot({ id: "pivot_2", name: "Other pivot" }),
          ],
        }}
      />,
    )
    fireEvent.click(screen.getByRole("button", { name: "Configure Pivot 1" }))
    expect(screen.getByRole("group", { name: "removed_field in Rows" })).toHaveAttribute("aria-invalid", "true")
    expect(screen.getByText(/no longer available/i)).toBeInTheDocument()

    const name = screen.getByRole("textbox", { name: "Pivot name" })
    fireEvent.change(name, { target: { value: "   " } })
    fireEvent.blur(name)
    expect(screen.getByRole("alert")).toHaveTextContent(/name cannot be blank/i)

    fireEvent.change(name, { target: { value: " other PIVOT " } })
    fireEvent.blur(name)
    expect(screen.getByRole("alert")).toHaveTextContent(/name must be unique/i)
  })

  it("blocks deletion while charts depend on a Pivot and lists those charts", () => {
    const used = fullPivot({
      values: [
        {
          id: "value_1",
          field: "claims",
          aggregation: "sum",
          reference: "claims_sum",
          display_name: "Claims",
        },
      ],
    })
    const free = fullPivot({ id: "pivot_2", name: "Pivot 2" })
    const dependent = {
      ...createExploreChart([]),
      name: "Claims chart",
      pivot_id: used.id,
      value_encodings: seedValueEncodings(used),
    }
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true)
    render(
      <PivotConfigHarness
        initialConfig={{ pivots: [used, free], charts: [dependent] }}
      />,
    )

    expect(screen.getByRole("button", { name: "Delete Pivot 1" })).toBeDisabled()
    expect(screen.getByRole("group", { name: "Pivot 1" })).toHaveTextContent(
      "Used by Claims chart",
    )

    fireEvent.click(screen.getByRole("button", { name: "Delete Pivot 2" }))
    expect(confirm).toHaveBeenCalledTimes(1)
    const persisted = JSON.parse(
      screen.getByTestId("persisted-config").textContent ?? "{}",
    )
    expect(persisted.pivots.map((item: ExplorePivotConfig) => item.id)).toEqual([
      "pivot_1",
    ])
    confirm.mockRestore()
  })

  it("surfaces malformed persisted pivots without destructive controls", () => {
    render(
      <ExplorePivotsConfig
        config={{
          pivots: [
            fullPivot({ id: "pivot_1" }),
            fullPivot({ id: "pivot_1", name: "Other pivot" }),
          ],
        }}
        onUpdate={vi.fn()}
        nodeId="explore_1"
        upstreamColumns={upstreamColumns}
      />,
    )

    expect(screen.getByRole("alert")).toHaveTextContent(/duplicate pivot id/i)
    expect(screen.queryByRole("button", { name: "Add Pivot" })).not.toBeInTheDocument()
  })
})
