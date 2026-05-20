import { afterEach, describe, expect, it } from "vitest"
import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import type { ExploreCacheReport, ExploreColumnStat } from "../../../api/types"
import SchemaTableCard from "../SchemaTableCard"

function makeColumn(overrides: Partial<ExploreColumnStat> = {}): ExploreColumnStat {
  return {
    name: "premium",
    dtype: "Float64",
    kind: "Numeric",
    null_count: 0,
    distinct_count: 10,
    min_value: "0",
    max_value: "100",
    ...overrides,
  }
}

function makeReport(overrides: Partial<ExploreCacheReport> = {}): ExploreCacheReport {
  return {
    status: "ok",
    node_id: "explore_1",
    upstream_node_id: "source_1",
    source: "pricing",
    dataframe_cache_key: "explore_dataset:abc123",
    row_count: 200,
    column_count: 1,
    generated_at: 1710000000,
    columns: [makeColumn()],
    overview_summary: {
      data_quality: { issue_count: 0, issues: [] },
      categorical_summary: [],
    },
    ...overrides,
  }
}

afterEach(cleanup)

describe("SchemaTableCard", () => {
  it("renders one row per column from report.columns", () => {
    render(
      <SchemaTableCard
        report={makeReport({
          column_count: 3,
          columns: [
            makeColumn({ name: "a" }),
            makeColumn({ name: "b" }),
            makeColumn({ name: "c" }),
          ],
        })}
      />,
    )

    expect(screen.getByTestId("explore-schema-row-a")).toBeInTheDocument()
    expect(screen.getByTestId("explore-schema-row-b")).toBeInTheDocument()
    expect(screen.getByTestId("explore-schema-row-c")).toBeInTheDocument()
  })

  it("does not render automatic grouping signals in the schema card", () => {
    render(
      <SchemaTableCard
        report={makeReport({
          column_count: 4,
          columns: [
            makeColumn({ name: "policy_id", dtype: "Int64", distinct_count: 200 }),
            makeColumn({ name: "premium", dtype: "Float64", distinct_count: 180 }),
            makeColumn({ name: "region", dtype: "String", kind: "Text", distinct_count: 5 }),
            makeColumn({ name: "renewal_flag", dtype: "Boolean", kind: "Boolean", distinct_count: 2 }),
          ],
          overview_summary: {
            data_quality: { issue_count: 0, issues: [] },
            categorical_summary: [],
          },
        })}
      />,
    )

    const card = screen.getByTestId("explore-schema-table-card")
    expect(card).toHaveTextContent("Schema")
    expect(card).toHaveTextContent("4 columns")
    expect(screen.queryByTestId("explore-schema-inventory-summary")).not.toBeInTheDocument()
    expect(card).not.toHaveTextContent("Numeric 2")
    expect(card).not.toHaveTextContent("Text 1")
    expect(card).not.toHaveTextContent(/Likely keys\s*policy_id/)
    expect(card).not.toHaveTextContent(/High cardinality\s*premium/)
    expect(card).not.toHaveTextContent(/Premium\s*premium/)
    expect(screen.getByTestId("explore-schema-row-policy_id")).toBeInTheDocument()
  })

  it("does not render an inventory strip when the backend has no inventory signals", () => {
    render(<SchemaTableCard report={makeReport()} />)

    expect(screen.queryByTestId("explore-schema-inventory-summary")).not.toBeInTheDocument()
  })

  it("limits the initial rendered rows for wide schemas", () => {
    const columns = Array.from({ length: 125 }, (_, index) =>
      makeColumn({ name: `col_${String(index).padStart(3, "0")}` }),
    )

    render(<SchemaTableCard report={makeReport({ column_count: 125, columns })} />)

    expect(document.querySelectorAll("[data-testid^='explore-schema-row-']")).toHaveLength(50)
    expect(screen.getByTestId("explore-schema-row-col_000")).toBeInTheDocument()
    expect(screen.getByTestId("explore-schema-row-col_049")).toBeInTheDocument()
    expect(screen.queryByTestId("explore-schema-row-col_050")).not.toBeInTheDocument()
    expect(screen.getByText(/Showing 1-50 of 125 columns/i)).toBeInTheDocument()
  })

  it("pages through schema rows without rendering the hidden pages", () => {
    const columns = Array.from({ length: 75 }, (_, index) =>
      makeColumn({ name: `col_${String(index).padStart(3, "0")}` }),
    )

    render(<SchemaTableCard report={makeReport({ column_count: 75, columns })} />)

    fireEvent.click(screen.getByRole("button", { name: /next schema columns/i }))

    expect(document.querySelectorAll("[data-testid^='explore-schema-row-']")).toHaveLength(25)
    expect(screen.queryByTestId("explore-schema-row-col_000")).not.toBeInTheDocument()
    expect(screen.getByTestId("explore-schema-row-col_050")).toBeInTheDocument()
    expect(screen.getByTestId("explore-schema-row-col_074")).toBeInTheDocument()
    expect(screen.getByRole("button", { name: /next schema columns/i })).toBeDisabled()
  })

  it("searches across all schema columns before applying the row limit", () => {
    const columns = Array.from({ length: 125 }, (_, index) =>
      makeColumn({ name: `col_${String(index).padStart(3, "0")}` }),
    )

    render(<SchemaTableCard report={makeReport({ column_count: 125, columns })} />)

    fireEvent.change(screen.getByRole("searchbox", { name: /search schema columns/i }), {
      target: { value: "col_120" },
    })

    expect(document.querySelectorAll("[data-testid^='explore-schema-row-']")).toHaveLength(1)
    expect(screen.getByTestId("explore-schema-row-col_120")).toBeInTheDocument()
    expect(screen.queryByTestId("explore-schema-row-col_000")).not.toBeInTheDocument()
  })

  it("formats null percentage to 1 decimal place", () => {
    render(
      <SchemaTableCard
        report={makeReport({
          row_count: 200,
          columns: [makeColumn({ name: "x", null_count: 25 })],
        })}
      />,
    )

    const row = screen.getByTestId("explore-schema-row-x")
    expect(row.textContent).toContain("12.5%")
  })

  it("renders min and max values instead of examples", () => {
    render(
      <SchemaTableCard
        report={makeReport({
          column_count: 2,
          columns: [
            makeColumn({ name: "premium", min_value: "-10", max_value: "25" }),
            makeColumn({
              name: "region",
              dtype: "String",
              kind: "Text",
              min_value: "alpha",
              max_value: "zulu",
            }),
          ],
        })}
      />,
    )

    const premiumRow = screen.getByTestId("explore-schema-row-premium")
    expect(premiumRow.querySelector("[data-testid='explore-schema-min-value']")).toHaveTextContent("-10")
    expect(premiumRow.querySelector("[data-testid='explore-schema-max-value']")).toHaveTextContent("25")

    const regionRow = screen.getByTestId("explore-schema-row-region")
    expect(regionRow.querySelector("[data-testid='explore-schema-min-value']")).toHaveTextContent("alpha")
    expect(regionRow.querySelector("[data-testid='explore-schema-max-value']")).toHaveTextContent("zulu")
    expect(screen.queryByText("Example")).not.toBeInTheDocument()
    expect(screen.queryByTestId("explore-schema-example")).not.toBeInTheDocument()
  })

  it("marks null percentage severity buckets", () => {
    render(
      <SchemaTableCard
        report={makeReport({
          row_count: 100,
          column_count: 3,
          columns: [
            makeColumn({ name: "mostly_null", null_count: 60 }),
            makeColumn({ name: "no_nulls", null_count: 0 }),
            makeColumn({ name: "some_nulls", null_count: 20 }),
          ],
        })}
      />,
    )

    expect(
      screen
        .getByTestId("explore-schema-row-mostly_null")
        .querySelector("[data-testid='explore-schema-null-pct']"),
    ).toHaveAttribute("data-null-severity", "high")
    expect(
      screen
        .getByTestId("explore-schema-row-no_nulls")
        .querySelector("[data-testid='explore-schema-null-pct']"),
    ).toHaveAttribute("data-null-severity", "none")
    expect(
      screen
        .getByTestId("explore-schema-row-some_nulls")
        .querySelector("[data-testid='explore-schema-null-pct']"),
    ).toHaveAttribute("data-null-severity", "normal")
  })

  it("labels the schema table by the Schema heading", () => {
    render(<SchemaTableCard report={makeReport()} />)

    const heading = document.getElementById("explore-schema-card-heading")
    expect(heading).not.toBeNull()
    expect(heading!.textContent).toBe("Schema")
    const table = document.querySelector("table")
    expect(table).not.toBeNull()
    expect(table!.getAttribute("aria-labelledby")).toBe("explore-schema-card-heading")
  })

  it("renders rows with sanitised testid for column names containing special chars", () => {
    const names = [
      { raw: "col with space", sanitised: "col_with_space" },
      { raw: "col.with.dots", sanitised: "col_with_dots" },
      { raw: "col/with/slash", sanitised: "col_with_slash" },
      { raw: "col!with!bang", sanitised: "col_with_bang" },
    ]

    render(
      <SchemaTableCard
        report={makeReport({
          column_count: names.length,
          columns: names.map((name) => makeColumn({ name: name.raw })),
        })}
      />,
    )

    for (const name of names) {
      const row = screen.getByTestId(`explore-schema-row-${name.sanitised}`)
      expect(row).toBeInTheDocument()
      expect(row.querySelector("[data-testid='explore-schema-name']")).toHaveTextContent(name.raw)
    }
  })

  it("renders placeholders for empty row-count and missing stats", () => {
    render(
      <SchemaTableCard
        report={makeReport({
          row_count: 0,
          columns: [
            makeColumn({
              name: "empty",
              null_count: 0,
              distinct_count: null,
              min_value: null,
              max_value: null,
            }),
          ],
        })}
      />,
    )

    const row = screen.getByTestId("explore-schema-row-empty")
    expect(row.textContent).toContain("-")
    expect(row.textContent).not.toContain("NaN")
  })

  it("renders an empty-state row when columns is empty", () => {
    render(<SchemaTableCard report={makeReport({ column_count: 0, columns: [] })} />)

    const empty = screen.getByTestId("explore-schema-empty")
    expect(empty.textContent).toBe("(no columns)")
    expect(empty.tagName).toBe("TD")
    expect(empty).toHaveAttribute("colSpan", "6")
  })

  it("colours dtype cells using the shared dtypeColors palette", () => {
    render(
      <SchemaTableCard
        report={makeReport({
          column_count: 2,
          columns: [
            makeColumn({ name: "int_col", dtype: "Int64" }),
            makeColumn({ name: "str_col", dtype: "String", kind: "Text" }),
          ],
        })}
      />,
    )

    const intDtype = screen.getByTestId("explore-schema-row-int_col").querySelectorAll("td")[1]
    const strDtype = screen.getByTestId("explore-schema-row-str_col").querySelectorAll("td")[1]
    expect(intDtype.className).toContain("text-blue-400")
    expect(strDtype.className).toContain("text-amber-400")
  })
})
