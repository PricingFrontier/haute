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
      display_name: "Paid claims",
    },
    {
      id: "v2",
      field: "count",
      aggregation: "count",
      display_name: "Claim count",
    },
  ],
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
})
