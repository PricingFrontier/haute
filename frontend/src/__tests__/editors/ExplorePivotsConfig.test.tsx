import { useState, type ComponentProps } from "react"
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

import ExplorePivotsConfig from "../../panels/editors/ExplorePivotsConfig"
import type { OnUpdateConfig } from "../../panels/editors/_shared"
import {
  createExploreChart,
  seedValueEncodings,
} from "../../panels/explore/chartConfig"
import type { ExplorePivotConfig } from "../../panels/explore/pivotConfig"

afterEach(() => {
  cleanup()
  vi.useRealTimers()
})

const upstreamColumns = [
  { name: "region", dtype: "String" },
  { name: "year", dtype: "Int64" },
  { name: "claims", dtype: "Float64" },
]

function fullPivot(overrides: Partial<ExplorePivotConfig> = {}): ExplorePivotConfig {
  return {
    version: 1,
    id: "pivot_1",
    name: "Pivot 1",
    enabled: true,
    filters: [],
    columns: [],
    rows: [],
    values: [],
    options: { row_grand_totals: true, column_grand_totals: true, sort_by: null },
    ...overrides,
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
        upstreamColumns={columns}
        loadFilterMembers={loadFilterMembers}
        currentConfigHash={currentConfigHash}
      />
      <output data-testid="persisted-config">{JSON.stringify(config)}</output>
    </>
  )
}

describe("ExplorePivotsConfig", () => {
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
    expect(screen.getByRole("heading", { name: "Configure Pivot 1" })).toBeInTheDocument()
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

  it("keeps sorting and conditional formatting in ordered sections after the field grid", () => {
    render(<PivotConfigHarness initialConfig={{ pivots: [fullPivot()] }} />)
    fireEvent.click(screen.getByRole("button", { name: "Configure Pivot 1" }))
    fireEvent.click(screen.getByRole("button", { name: "Add region to Rows" }))
    fireEvent.click(screen.getByRole("button", { name: "Add claims to Values" }))
    fireEvent.click(screen.getByRole("button", { name: "Add claims to Values" }))

    const areas = screen.getByTestId("pivot-field-areas")
    const sorting = screen.getByTestId("pivot-sorting-section")
    const formatting = screen.getByTestId("pivot-conditional-formatting-section")
    expect(areas.compareDocumentPosition(sorting) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(
      sorting.compareDocumentPosition(formatting) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy()
    expect(within(areas).queryByRole("combobox", { name: /Label order/i })).not.toBeInTheDocument()
    expect(within(areas).queryByRole("combobox", { name: /Sort rows by/i })).not.toBeInTheDocument()
    expect(within(areas).queryByRole("combobox", { name: /Colour scale/i })).not.toBeInTheDocument()

    const sortBy = within(sorting).getByRole("combobox", { name: "Sort by" })
    const order = within(sorting).getByRole("combobox", { name: "Order" })
    expect(within(sortBy).getByRole("option", { name: "Default — Row labels" })).toBeInTheDocument()
    fireEvent.change(sortBy, { target: { value: "row_1" } })
    fireEvent.change(order, { target: { value: "descending" } })
    fireEvent.change(sortBy, { target: { value: "value_2" } })

    const addRule = within(formatting).getByRole("button", {
      name: "Add conditional formatting rule",
    })
    expect(within(formatting).getByText("No conditional formatting rules."))
      .toBeInTheDocument()
    fireEvent.click(addRule)
    fireEvent.click(addRule)

    const rules = within(formatting).getAllByRole("group", {
      name: /Conditional formatting rule for claims/,
    })
    expect(rules).toHaveLength(2)
    expect(within(rules[0]).getByRole("combobox", {
      name: "Value field for conditional formatting rule 1",
    })).toHaveValue("value_1")
    expect(within(rules[1]).getByRole("combobox", {
      name: "Value field for conditional formatting rule 2",
    })).toHaveValue("value_2")
    expect(within(rules[0]).getByRole("img", {
      name: "Colour scale preview for claims",
    })).toBeVisible()
    expect(within(rules[1]).getByRole("img", {
      name: "Colour scale preview for claims",
    })).toBeVisible()
    expect(addRule).toBeDisabled()

    fireEvent.change(
      within(rules[1]).getByRole("combobox", {
        name: "Colour scale for conditional formatting rule 2",
      }),
      { target: { value: "low_green_high_red" } },
    )

    const persisted = JSON.parse(screen.getByTestId("persisted-config").textContent ?? "{}")
    expect(persisted.pivots[0].options.sort_by).toBe("value_2")
    expect(persisted.pivots[0].rows[0].sort).toBe("ascending")
    expect(persisted.pivots[0].values.map((value: { sort_rows: string }) => value.sort_rows)).toEqual([
      "none",
      "descending",
    ])
    expect(persisted.pivots[0].values.map((value: { color_scale: string }) => value.color_scale))
      .toEqual(["low_red_high_green", "low_green_high_red"])

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
    expect(within(formatting).getAllByRole("group", {
      name: /Conditional formatting rule for claims/,
    })).toHaveLength(1)
    expect(addRule).toBeEnabled()
    fireEvent.click(addRule)

    const currentRules = within(formatting).getAllByRole("group", {
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
    fireEvent.change(
      screen.getByRole("combobox", { name: "Aggregation for region" }),
      { target: { value: "min" } },
    )
    expect(within(formatting).getAllByRole("group", {
      name: /Conditional formatting rule for/,
    })).toHaveLength(1)
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
                  display_name: "Claims",
                  sort_rows: "none",
                  color_scale: "low_red_high_green",
                },
                {
                  id: "value_2",
                  field: "region",
                  aggregation: "count",
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
      { id: "row_1", field: "region", sort: "ascending" },
      { id: "row_2", field: "year", sort: "ascending" },
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
      { id: "row_2", field: "year", sort: "ascending" },
      { id: "row_1", field: "region", sort: "ascending" },
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
              columns: [{ id: "column_1", field: "year" }],
              rows: [{ id: "row_1", field: "region" }],
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
    expect(persisted.pivots[0].rows).toEqual([
      { id: "column_1", field: "year", sort: "ascending" },
      { id: "row_1", field: "region", sort: "ascending" },
    ])
    expect(onCommittedUpdate).toHaveBeenCalledTimes(1)
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
      { id: "column_1", field: "year" },
    ])
    expect(persisted.pivots[0].rows).toEqual([{ id: "row_1", field: "year" }])
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
      { id: "row_2", field: "year", sort: "ascending" },
      { id: "row_1", field: "region", sort: "ascending" },
    ])
    expect(onCommittedUpdate).toHaveBeenCalledTimes(1)
  })

  it("retains missing fields as invalid chips and rejects duplicate names", () => {
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
        config={{ pivots: [{ id: "pivot_1" }, { id: "pivot_1" }] }}
        onUpdate={vi.fn()}
        upstreamColumns={upstreamColumns}
      />,
    )

    expect(screen.getByRole("alert")).toHaveTextContent(/duplicate pivot id/i)
    expect(screen.queryByRole("button", { name: "Add Pivot" })).not.toBeInTheDocument()
  })
})
