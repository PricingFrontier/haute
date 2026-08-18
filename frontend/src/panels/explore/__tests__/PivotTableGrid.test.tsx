import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it } from "vitest"

import type { ExplorePivotResult } from "../../../api/types"
import PivotTableGrid from "../PivotTableGrid"
import type { ExplorePivotConfig } from "../pivotConfig"

const pivot: ExplorePivotConfig = {
  version: 1,
  id: "p1",
  name: "Claims by region",
  enabled: true,
  filters: [],
  rows: [{ id: "r1", field: "region" }],
  columns: [{ id: "c1", field: "year" }],
  values: [
    {
      id: "v1",
      field: "paid",
      aggregation: "sum",
      reference: "paid_sum",
      display_name: "Paid claims",
    },
    {
      id: "v2",
      field: "count",
      aggregation: "count",
      reference: "count_count",
      display_name: "Claim count",
    },
  ],
  formulas: [],
  value_order: ["v1", "v2"],
  options: { row_grand_totals: true, column_grand_totals: true },
}

function result(rowCount = 2): ExplorePivotResult {
  return {
    version: 1,
    node_id: "explore",
    pivot_id: "p1",
    source: "pricing",
    dataframe_cache_key: "cache",
    calculation_key: "calculation",
    row_fields: ["region"],
    column_fields: ["year"],
    values: pivot.values.map(({ id, field, aggregation }) => ({
      id,
      field,
      aggregation,
    })),
    row_paths: Array.from({ length: rowCount }, (_, index) =>
      index === rowCount - 1
        ? { members: [], is_grand_total: true }
        : {
            members: [
              { kind: "string" as const, value: `Region ${index}` },
            ],
            is_grand_total: false,
          },
    ),
    column_paths: [
      {
        members: [{ kind: "integer", value: "2024" }],
        is_grand_total: false,
      },
      { members: [], is_grand_total: true },
    ],
    cells: [
      { row_index: 0, column_index: 0, value_id: "v1", value: null },
      { row_index: 0, column_index: 0, value_id: "v2", value: 4 },
    ],
    warnings: [],
    generated_at: 1,
    execution_metrics: null,
  }
}

describe("PivotTableGrid", () => {
  afterEach(cleanup)

  it("uses configured labels, null markers, and explicit grand totals", () => {
    render(<PivotTableGrid result={result()} pivot={pivot} />)

    expect(
      screen.getByRole("table", { name: "Claims by region results" }),
    ).toBeInTheDocument()
    expect(screen.getAllByText("Paid claims").length).toBeGreaterThan(0)
    expect(screen.getAllByText("Grand total").length).toBeGreaterThan(0)
    expect(screen.getAllByText("\u2014").length).toBeGreaterThan(0)
  })

  it("renders post-aggregation formulas after ordinary Values", () => {
    const formulaPivot: ExplorePivotConfig = {
      ...pivot,
      formulas: [{
        id: "formula_1",
        reference: "average_cost",
        display_name: "Average cost",
        expression: 'pl.col("paid").sum() / pl.col("count").count()',
        number_format: "number",
        decimal_places: 2,
        use_grouping: true,
      }],
      value_order: ["v1", "v2", "formula_1"],
    }
    const formulaResult: ExplorePivotResult = {
      ...result(1),
      row_paths: [{ members: [{ kind: "string", value: "North" }], is_grand_total: false }],
      column_paths: [{ members: [{ kind: "integer", value: "2024" }], is_grand_total: false }],
      values: [
        ...result(1).values,
        { id: "formula_1", field: "average_cost", aggregation: "formula" },
      ],
      cells: [
        { row_index: 0, column_index: 0, value_id: "v1", value: 20 },
        { row_index: 0, column_index: 0, value_id: "v2", value: 4 },
        { row_index: 0, column_index: 0, value_id: "formula_1", value: 5 },
      ],
    }

    render(<PivotTableGrid result={formulaResult} pivot={formulaPivot} />)

    const headers = screen.getAllByRole("columnheader")
    expect(headers.slice(-3).map((header) => header.textContent)).toEqual([
      "Paid claims",
      "Claim count",
      "Average cost",
    ])
    expect(screen.getByRole("cell", { name: "5.00" })).toBeVisible()
  })

  it("keeps row-field headers when the pivot has no column fields", () => {
    const noColumns = result()
    noColumns.column_fields = []
    noColumns.column_paths = [{ members: [], is_grand_total: false }]

    render(<PivotTableGrid result={noColumns} pivot={pivot} />)

    expect(screen.getByRole("columnheader", { name: "region" })).toBeVisible()
    expect(
      screen.getByRole("columnheader", { name: "Paid claims" }),
    ).toBeVisible()
  })

  it("formats currencies and percentages across Columns, Rows, and Values exactly", () => {
    const formattedPivot: ExplorePivotConfig = {
      ...pivot,
      rows: [{
        ...pivot.rows[0],
        number_format: "currency_gbp",
        decimal_places: 2,
        use_grouping: true,
      }],
      columns: [{
        ...pivot.columns[0],
        number_format: "percent",
        decimal_places: 1,
        use_grouping: true,
      }],
      values: [
        {
          ...pivot.values[0],
          number_format: "currency_usd",
          decimal_places: 2,
          use_grouping: true,
        },
        {
          ...pivot.values[1],
          number_format: "currency_eur",
          decimal_places: 0,
          use_grouping: false,
        },
      ],
    }
    const formatted = result(1)
    formatted.row_paths = [{
      members: [{ kind: "decimal", value: "1234.555" }],
      is_grand_total: false,
    }]
    formatted.column_paths = [{
      members: [{ kind: "decimal", value: "0.125" }],
      is_grand_total: false,
    }]
    formatted.cells = [
      {
        row_index: 0,
        column_index: 0,
        value_id: "v1",
        value: "900719925474099312345.125",
      },
      { row_index: 0, column_index: 0, value_id: "v2", value: -2.5 },
    ]

    render(<PivotTableGrid result={formatted} pivot={formattedPivot} />)

    expect(screen.getByRole("rowheader", { name: "£1,234.56" })).toBeVisible()
    expect(screen.getByRole("columnheader", { name: "12.5%" })).toBeVisible()
    expect(screen.getByRole("cell", {
      name: "US$900,719,925,474,099,312,345.13",
    })).toBeVisible()
    expect(screen.getByRole("cell", { name: "-€3" })).toBeVisible()
  })

  it("supports automatic exact Number formatting with optional thousands separators", () => {
    const formattedPivot: ExplorePivotConfig = {
      ...pivot,
      values: [
        {
          ...pivot.values[0],
          number_format: "number",
          decimal_places: null,
          use_grouping: true,
        },
        {
          ...pivot.values[1],
          number_format: "number",
          decimal_places: 2,
          use_grouping: false,
        },
      ],
    }
    const formatted = result(1)
    formatted.cells = [
      {
        row_index: 0,
        column_index: 0,
        value_id: "v1",
        value: "900719925474099312345.1200",
      },
      {
        row_index: 0,
        column_index: 0,
        value_id: "v2",
        value: "1234567.891",
      },
    ]

    render(<PivotTableGrid result={formatted} pivot={formattedPivot} />)

    expect(screen.getByRole("cell", {
      name: "900,719,925,474,099,312,345.1200",
    })).toBeVisible()
    expect(screen.getByRole("cell", { name: "1234567.89" })).toBeVisible()
  })

  it("uses standard automatic precision for percentages and currencies", () => {
    const formattedPivot: ExplorePivotConfig = {
      ...pivot,
      values: [
        {
          ...pivot.values[0],
          number_format: "percent",
          decimal_places: null,
          use_grouping: true,
        },
        {
          ...pivot.values[1],
          number_format: "currency_gbp",
          decimal_places: null,
          use_grouping: true,
        },
      ],
    }
    const formatted = result(1)
    formatted.cells = [
      { row_index: 0, column_index: 0, value_id: "v1", value: "0.50004" },
      { row_index: 0, column_index: 0, value_id: "v2", value: 100 },
    ]

    render(<PivotTableGrid result={formatted} pivot={formattedPivot} />)

    expect(screen.getByRole("cell", { name: "50%" })).toBeVisible()
    expect(screen.getByRole("cell", { name: "£100.00" })).toBeVisible()
  })

  it("keeps Automatic precision and non-numeric typed members unchanged", () => {
    const automatic = result(1)
    automatic.row_paths = [{
      members: [{ kind: "string", value: "001.50" }],
      is_grand_total: false,
    }]
    automatic.column_paths = [{
      members: [{ kind: "decimal", value: "1.2300" }],
      is_grand_total: false,
    }]
    automatic.cells = [
      { row_index: 0, column_index: 0, value_id: "v1", value: "1.2300" },
    ]

    render(<PivotTableGrid result={automatic} pivot={pivot} />)

    expect(screen.getByRole("rowheader", { name: "001.50" })).toBeVisible()
    expect(screen.getByRole("columnheader", { name: "1.2300" })).toBeVisible()
    expect(screen.getByRole("cell", { name: "1.2300" })).toBeVisible()
  })

  it("shows a clear empty state when no groups match", () => {
    const empty = result(0)
    empty.column_paths = []

    render(<PivotTableGrid result={empty} pivot={pivot} />)

    expect(
      screen.getByText("No rows match this pivot configuration."),
    ).toBeVisible()
  })

  it("virtualises result rows while retaining the full scroll height", () => {
    render(<PivotTableGrid result={result(200)} pivot={pivot} />)
    const scroll = screen.getByTestId("pivot-table-scroll")

    expect(screen.queryByText("Region 100")).not.toBeInTheDocument()
    const spacers = scroll.querySelectorAll('tr[aria-hidden="true"] td')
    expect(
      Array.from(spacers).some(
        (cell) => Number.parseInt((cell as HTMLElement).style.height, 10) > 1000,
      ),
    ).toBe(true)

    fireEvent.scroll(scroll, { target: { scrollTop: 32 * 100 } })
    expect(screen.getByText("Region 100")).toBeInTheDocument()
  })

  it("applies a three-stop scale only to ordinary finite numeric body cells", () => {
    const formattedPivot = {
      ...pivot,
      values: [{ ...pivot.values[0], color_scale: "low_red_high_green" as const }, pivot.values[1]],
    }
    const formatted = result(4)
    formatted.row_paths = [
      { members: [{ kind: "string", value: "Low" }], is_grand_total: false },
      { members: [{ kind: "string", value: "Mid" }], is_grand_total: false },
      { members: [{ kind: "string", value: "High" }], is_grand_total: false },
      { members: [], is_grand_total: true },
    ]
    formatted.cells = [
      { row_index: 0, column_index: 0, value_id: "v1", value: 0 },
      { row_index: 1, column_index: 0, value_id: "v1", value: "10" },
      { row_index: 2, column_index: 0, value_id: "v1", value: 20 },
      { row_index: 3, column_index: 0, value_id: "v1", value: 100 },
      { row_index: 0, column_index: 1, value_id: "v1", value: 100 },
      { row_index: 1, column_index: 0, value_id: "v2", value: 3 },
    ]
    const { container } = render(<PivotTableGrid result={formatted} pivot={formattedPivot} />)
    const cells = container.querySelectorAll('td[data-conditional-format="low_red_high_green"]')
    expect(cells).toHaveLength(3)
    expect((cells[0] as HTMLElement).style.background).toBe("rgb(248, 105, 107)")
    expect((cells[1] as HTMLElement).style.background).toBe("rgb(255, 235, 132)")
    expect((cells[2] as HTMLElement).style.background).toBe("rgb(99, 190, 123)")
  })

  it("reverses endpoint colours and uses yellow for equal values", () => {
    const formatted = result(2)
    formatted.column_paths = [
      { members: [{ kind: "integer", value: "2024" }], is_grand_total: false },
      { members: [{ kind: "integer", value: "2025" }], is_grand_total: false },
    ]
    formatted.cells = [
      { row_index: 0, column_index: 0, value_id: "v1", value: 5 },
      { row_index: 0, column_index: 1, value_id: "v1", value: 5 },
    ]
    const reverse = { ...pivot, values: [{ ...pivot.values[0], color_scale: "low_green_high_red" as const }, pivot.values[1]] }
    const { container, rerender } = render(<PivotTableGrid result={formatted} pivot={reverse} />)
    expect((container.querySelector('td[data-conditional-format]') as HTMLElement).style.background).toBe("rgb(255, 235, 132)")
    rerender(<PivotTableGrid result={{ ...formatted, cells: [formatted.cells[0], { row_index: 0, column_index: 1, value_id: "v1", value: 10 }] }} pivot={reverse} />)
    expect((container.querySelectorAll('td[data-conditional-format]')[0] as HTMLElement).style.background).toBe("rgb(99, 190, 123)")
  })

  it("calculates an independent colour domain for each selected Row member", () => {
    const splitPivot: ExplorePivotConfig = {
      ...pivot,
      values: [{
        ...pivot.values[0],
        color_scale: "low_red_high_green",
        color_scale_split_by: "r1",
      }, pivot.values[1]],
    }
    const formatted = result(2)
    formatted.row_paths = [
      { members: [{ kind: "string", value: "Comprehensive" }], is_grand_total: false },
      { members: [{ kind: "string", value: "Third party" }], is_grand_total: false },
    ]
    formatted.column_paths = [
      { members: [{ kind: "integer", value: "2024" }], is_grand_total: false },
      { members: [{ kind: "integer", value: "2025" }], is_grand_total: false },
    ]
    formatted.cells = [
      { row_index: 0, column_index: 0, value_id: "v1", value: 0 },
      { row_index: 0, column_index: 1, value_id: "v1", value: 10 },
      { row_index: 1, column_index: 0, value_id: "v1", value: 100 },
      { row_index: 1, column_index: 1, value_id: "v1", value: 200 },
    ]

    const { container } = render(<PivotTableGrid result={formatted} pivot={splitPivot} />)
    const colours = Array.from(container.querySelectorAll('td[data-conditional-format]'))
      .map((cell) => (cell as HTMLElement).style.background)
    expect(colours).toEqual([
      "rgb(248, 105, 107)",
      "rgb(99, 190, 123)",
      "rgb(248, 105, 107)",
      "rgb(99, 190, 123)",
    ])
  })

  it("calculates an independent colour domain for each selected Column member", () => {
    const splitPivot: ExplorePivotConfig = {
      ...pivot,
      values: [{
        ...pivot.values[0],
        color_scale: "low_red_high_green",
        color_scale_split_by: "c1",
      }, pivot.values[1]],
    }
    const formatted = result(2)
    formatted.row_paths = [
      { members: [{ kind: "string", value: "North" }], is_grand_total: false },
      { members: [{ kind: "string", value: "South" }], is_grand_total: false },
    ]
    formatted.column_paths = [
      { members: [{ kind: "integer", value: "2024" }], is_grand_total: false },
      { members: [{ kind: "integer", value: "2025" }], is_grand_total: false },
    ]
    formatted.cells = [
      { row_index: 0, column_index: 0, value_id: "v1", value: 0 },
      { row_index: 0, column_index: 1, value_id: "v1", value: 100 },
      { row_index: 1, column_index: 0, value_id: "v1", value: 10 },
      { row_index: 1, column_index: 1, value_id: "v1", value: 200 },
    ]

    const { container } = render(<PivotTableGrid result={formatted} pivot={splitPivot} />)
    const colours = Array.from(container.querySelectorAll('td[data-conditional-format]'))
      .map((cell) => (cell as HTMLElement).style.background)
    expect(colours).toEqual([
      "rgb(248, 105, 107)",
      "rgb(248, 105, 107)",
      "rgb(99, 190, 123)",
      "rgb(99, 190, 123)",
    ])
  })
})
