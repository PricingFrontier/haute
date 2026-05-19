import { afterEach, describe, expect, it } from "vitest"
import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import type { ExploreCacheReport, ExploreColumnStat } from "../../../api/types"
import SchemaTableCard from "../SchemaTableCard"

function makeColumn(overrides: Partial<ExploreColumnStat> = {}): ExploreColumnStat {
  return {
    name: "premium",
    dtype: "Float64",
    null_count: 0,
    distinct_count: 10,
    example_value: "100.0",
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

  it("renders em-dash for null distinct_count", () => {
    render(
      <SchemaTableCard
        report={makeReport({
          columns: [makeColumn({ name: "x", distinct_count: null })],
        })}
      />,
    )
    const row = screen.getByTestId("explore-schema-row-x")
    expect(row.textContent).toContain("—")
  })

  it("renders em-dash for null example_value", () => {
    render(
      <SchemaTableCard
        report={makeReport({
          columns: [makeColumn({ name: "x", example_value: null })],
        })}
      />,
    )
    const row = screen.getByTestId("explore-schema-row-x")
    expect(row.textContent).toContain("—")
  })

  it("marks the null-% cell as high severity when null % > 50", () => {
    render(
      <SchemaTableCard
        report={makeReport({
          row_count: 100,
          columns: [makeColumn({ name: "mostly_null", null_count: 60 })],
        })}
      />,
    )
    const row = screen.getByTestId("explore-schema-row-mostly_null")
    const nullCell = row.querySelector("[data-testid='explore-schema-null-pct']")
    expect(nullCell).not.toBeNull()
    expect(nullCell!.getAttribute("data-null-severity")).toBe("high")
  })

  it("marks the null-% cell as none severity when null % is exactly 0", () => {
    render(
      <SchemaTableCard
        report={makeReport({
          row_count: 100,
          columns: [makeColumn({ name: "no_nulls", null_count: 0 })],
        })}
      />,
    )
    const row = screen.getByTestId("explore-schema-row-no_nulls")
    const nullCell = row.querySelector("[data-testid='explore-schema-null-pct']")
    expect(nullCell).not.toBeNull()
    expect(nullCell!.getAttribute("data-null-severity")).toBe("none")
  })

  it("marks the null-% cell as normal severity for in-between values", () => {
    render(
      <SchemaTableCard
        report={makeReport({
          row_count: 100,
          columns: [makeColumn({ name: "some_nulls", null_count: 20 })],
        })}
      />,
    )
    const row = screen.getByTestId("explore-schema-row-some_nulls")
    const nullCell = row.querySelector("[data-testid='explore-schema-null-pct']")
    expect(nullCell).not.toBeNull()
    expect(nullCell!.getAttribute("data-null-severity")).toBe("normal")
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
      { raw: "col_é", sanitised: "col__" },
      { raw: "col with emoji 🚀", sanitised: "col_with_emoji___" },
    ]
    render(
      <SchemaTableCard
        report={makeReport({
          column_count: names.length,
          columns: names.map((n) => makeColumn({ name: n.raw })),
        })}
      />,
    )
    for (const n of names) {
      const row = screen.getByTestId(`explore-schema-row-${n.sanitised}`)
      expect(row).toBeInTheDocument()
      const nameCell = row.querySelector("[data-testid='explore-schema-name']")
      expect(nameCell!.textContent).toBe(n.raw)
    }
  })

  it("renders em-dash for null % when row_count is 0", () => {
    render(
      <SchemaTableCard
        report={makeReport({
          row_count: 0,
          columns: [makeColumn({ name: "empty", null_count: 0 })],
        })}
      />,
    )
    const row = screen.getByTestId("explore-schema-row-empty")
    const nullCell = row.querySelector("[data-testid='explore-schema-null-pct']")
    expect(nullCell).not.toBeNull()
    expect(nullCell!.textContent).toContain("—")
    expect(nullCell!.textContent).not.toContain("NaN")
  })

  it("header row uses uppercase tracking style", () => {
    render(<SchemaTableCard report={makeReport()} />)
    const nameHeader = screen.getByText("Name")
    expect(nameHeader.className).toContain("text-[10px]")
    expect(nameHeader.className).toContain("font-bold")
    expect(nameHeader.className).toContain("uppercase")
    expect(nameHeader.className).toContain("tracking-[0.08em]")
  })

  it("renders the testid on the outer container", () => {
    render(<SchemaTableCard report={makeReport()} />)
    expect(screen.getByTestId("explore-schema-table-card")).toBeInTheDocument()
  })

  it("formats distinct count with thousands separators", () => {
    render(
      <SchemaTableCard
        report={makeReport({
          columns: [makeColumn({ name: "big", distinct_count: 12345 })],
        })}
      />,
    )
    const row = screen.getByTestId("explore-schema-row-big")
    expect(row.textContent).toContain("12,345")
  })

  it("shows the column count in the header", () => {
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
    expect(screen.getByText(/3 columns/i)).toBeInTheDocument()
  })

  it("truncates long names with title attribute for hover", () => {
    const longName = "a_very_long_column_name_that_exceeds_the_limit_for_sure"
    render(
      <SchemaTableCard
        report={makeReport({
          columns: [makeColumn({ name: longName })],
        })}
      />,
    )
    const row = screen.getByTestId(`explore-schema-row-${longName}`)
    const nameCell = row.querySelector("[data-testid='explore-schema-name']")
    expect(nameCell).not.toBeNull()
    expect(nameCell!.getAttribute("title")).toBe(longName)
    expect(nameCell!.className).toContain("truncate")
  })

  it("truncates long example values with title attribute for hover", () => {
    const longExample = "this is a really long example value that should be truncated visually"
    render(
      <SchemaTableCard
        report={makeReport({
          columns: [makeColumn({ name: "x", example_value: longExample })],
        })}
      />,
    )
    const row = screen.getByTestId("explore-schema-row-x")
    const exampleCell = row.querySelector("[data-testid='explore-schema-example']")
    expect(exampleCell).not.toBeNull()
    expect(exampleCell!.getAttribute("title")).toBe(longExample)
    expect(exampleCell!.className).toContain("truncate")
  })

  it("renders an empty-state row when columns is empty", () => {
    render(<SchemaTableCard report={makeReport({ column_count: 0, columns: [] })} />)
    const empty = screen.getByTestId("explore-schema-empty")
    expect(empty.textContent).toBe("(no columns)")
    expect(empty.tagName).toBe("TD")
  })

  it("colours dtype cells using the shared dtypeColors palette", () => {
    render(
      <SchemaTableCard
        report={makeReport({
          column_count: 2,
          columns: [
            makeColumn({ name: "int_col", dtype: "Int64" }),
            makeColumn({ name: "str_col", dtype: "String" }),
          ],
        })}
      />,
    )
    const intRow = screen.getByTestId("explore-schema-row-int_col")
    const strRow = screen.getByTestId("explore-schema-row-str_col")
    const intDtype = intRow.querySelectorAll("td")[1]
    const strDtype = strRow.querySelectorAll("td")[1]
    expect(intDtype.className).toContain("text-blue-400")
    expect(strDtype.className).toContain("text-amber-400")
  })

  it("limits initially rendered rows for very large schemas", () => {
    const columns = Array.from({ length: 300 }, (_, index) =>
      makeColumn({ name: `col_${index}` }),
    )
    const { container } = render(
      <SchemaTableCard
        report={makeReport({
          column_count: columns.length,
          columns,
        })}
      />,
    )

    expect(container.querySelectorAll("tbody tr")).toHaveLength(50)
    expect(screen.getByText(/Showing 1-50 of 300 columns/i)).toBeInTheDocument()
  })

  it("filters large schemas before applying the render limit", () => {
    const columns = Array.from({ length: 300 }, (_, index) =>
      makeColumn({ name: index === 299 ? "target_column" : `col_${index}` }),
    )
    render(
      <SchemaTableCard
        report={makeReport({
          column_count: columns.length,
          columns,
        })}
      />,
    )

    fireEvent.change(screen.getByRole("searchbox", { name: /search schema columns/i }), {
      target: { value: "target" },
    })

    expect(screen.getByTestId("explore-schema-row-target_column")).toBeInTheDocument()
    expect(screen.queryByTestId("explore-schema-row-col_0")).not.toBeInTheDocument()
    expect(screen.getByText(/Showing 1-1 of 1 matching column/i)).toBeInTheDocument()
  })
})
