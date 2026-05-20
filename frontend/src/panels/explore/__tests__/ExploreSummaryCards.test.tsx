import { afterEach, describe, expect, it, vi } from "vitest"
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react"
import type { ExploreCacheReport, ExploreColumnStat } from "../../../api/types"
import {
  CategoricalSummaryCard,
  DataQualityCard,
  DatasetSnapshotCard,
  NumericSummaryCard,
} from "../ExploreSummaryCards"

function makeColumn(overrides: Partial<ExploreColumnStat> = {}): ExploreColumnStat {
  return {
    name: "premium",
    dtype: "Float64",
    kind: "Numeric",
    null_count: 0,
    distinct_count: 10,
    ...overrides,
  }
}

function makeReport(overrides: Partial<ExploreCacheReport> = {}): ExploreCacheReport {
  return {
    status: "ok",
    node_id: "explore_1",
    upstream_node_id: "prep_1",
    source: "pricing",
    dataframe_cache_key: "explore_dataset:abc123",
    row_count: 1234,
    column_count: 5,
    generated_at: 1710000000,
    columns: [
      makeColumn({ name: "id", dtype: "Int64", distinct_count: 1234 }),
      makeColumn({
        name: "premium",
        dtype: "Float64",
        distinct_count: 980,
        min_value: "10.5",
        max_value: "999.99",
        zero_count: 0,
        negative_count: 0,
      }),
      makeColumn({ name: "region", dtype: "String", kind: "Text", null_count: 25, distinct_count: 4 }),
      makeColumn({ name: "constant", dtype: "String", kind: "Text", distinct_count: 1 }),
      makeColumn({ name: "policy_id", dtype: "String", kind: "Text", distinct_count: 1234 }),
    ],
    overview_summary: {
      data_quality: {
        issue_count: 1,
        issues: [
          {
            severity: "warning",
            label: "1 columns with missing values",
            detail: "region worst at 2%",
          },
        ],
      },
      categorical_summary: [
        {
          field: "region",
          distinct_count: 4,
          expandable: true,
          values_truncated: false,
          values: [
            { value: "north", count: 20 },
            { value: "south", count: 10 },
          ],
        },
        {
          field: "policy_id",
          distinct_count: 1234,
          expandable: true,
          values_truncated: true,
          values: Array.from({ length: 50 }, (_, index) => ({
            value: `p${String(index + 1).padStart(3, "0")}`,
            count: 50 - index,
          })),
        },
      ],
    },
    ...overrides,
  }
}

afterEach(() => {
  cleanup()
  vi.useRealTimers()
})

describe("Explore summary cards", () => {
  it("renders Dataset Snapshot without repeating schema-level column detail", () => {
    const now = new Date("2026-05-19T12:00:00Z")
    vi.useFakeTimers().setSystemTime(now)
    const generatedAt = Math.floor(now.getTime() / 1000) - 5 * 60

    render(<DatasetSnapshotCard report={makeReport({ generated_at: generatedAt })} />)

    const card = screen.getByTestId("explore-dataset-snapshot-card")
    expect(card).toHaveTextContent("Dataset Snapshot")
    expect(card).toHaveTextContent("1,234")
    expect(card).toHaveTextContent("pricing")
    expect(card).toHaveTextContent("prep_1")
    expect(card).toHaveTextContent("5 min ago")
    expect(card).not.toHaveTextContent("Columns")
  })

  it("renders backend data quality issues without repeating type inventory details", () => {
    render(
      <DataQualityCard
        report={makeReport({
          overview_summary: {
            data_quality: {
              issue_count: 2,
              issues: [
                {
                  severity: "danger",
                  label: "2 columns with missing values",
                  detail: "discount worst at 60%",
                },
                {
                  severity: "warning",
                  label: "1 constant / single-value",
                  detail: "constant",
                },
              ],
            },
            categorical_summary: [],
          },
        })}
      />,
    )

    const card = screen.getByTestId("explore-data-quality-card")
    expect(card).toHaveTextContent("Data Quality")
    expect(card).toHaveTextContent("2 columns with missing values")
    expect(card).toHaveTextContent("discount worst at 60%")
    expect(card).toHaveTextContent("1 constant / single-value")
    expect(card).toHaveTextContent("constant")
    expect(card).not.toHaveTextContent("Numeric")
    expect(card).not.toHaveTextContent("Text")
  })

  it("uses a crisp all-clear quality state when there are no missing cells", () => {
    render(
      <DataQualityCard
        report={makeReport({
          overview_summary: {
            data_quality: { issue_count: 0, issues: [] },
            categorical_summary: [],
          },
        })}
      />,
    )

    const card = screen.getByTestId("explore-data-quality-card")
    expect(card).toHaveTextContent("No obvious missing, constant, negative, or mostly-zero fields.")
    expect(card).not.toHaveTextContent("with missing values")
  })

  it("renders numeric fields only with distribution summaries", () => {
    render(
      <NumericSummaryCard
        report={makeReport({
          row_count: 100,
          columns: [
            makeColumn({
              name: "premium",
              dtype: "Float64",
              null_count: 5,
              distinct_count: 80,
              min_value: "10.5",
              p25_value: "75",
              median_value: "100",
              mean_value: "108.5",
              p75_value: "130",
              max_value: "999.99",
              std_value: "32.4",
              zero_count: 2,
              negative_count: 1,
            }),
            makeColumn({
              name: "claim_count",
              dtype: "Int64",
              null_count: 0,
              distinct_count: 4,
              min_value: "0",
              p25_value: "1",
              median_value: "1",
              mean_value: "1.4",
              p75_value: "2",
              max_value: "3",
              std_value: "0.5",
              zero_count: 40,
              negative_count: 0,
            }),
            makeColumn({
              name: "region",
              dtype: "String",
              kind: "Text",
              distinct_count: 4,
              min_value: "east",
              p25_value: "bad-profile",
              median_value: "bad-profile",
              mean_value: "bad-profile",
              p75_value: "bad-profile",
              max_value: "west",
              std_value: "bad-profile",
              zero_count: 0,
              negative_count: 0,
            }),
            makeColumn({
              name: "created_at",
              dtype: "Datetime(time_unit='us', time_zone=None)",
              kind: "Temporal",
              distinct_count: 12,
              min_value: "2024-01-01",
              max_value: "2024-12-31",
              zero_count: 0,
              negative_count: 0,
            }),
            makeColumn({
              name: "is_active",
              dtype: "Boolean",
              kind: "Boolean",
              distinct_count: 2,
              zero_count: 5,
              negative_count: 0,
            }),
          ],
        })}
      />,
    )

    const card = screen.getByTestId("explore-numeric-summary-card")
    expect(card).toHaveTextContent("Numeric Summary")
    expect(card).toHaveTextContent("2 fields")
    expect(card).toHaveTextContent("premium")
    expect(card).toHaveTextContent("claim_count")
    expect(card).toHaveTextContent("P25")
    expect(card).toHaveTextContent("Median")
    expect(card).toHaveTextContent("Mean")
    expect(card).toHaveTextContent("Std")
    expect(card).toHaveTextContent("5.0%")
    expect(card).toHaveTextContent("80")
    expect(card).toHaveTextContent("10.5")
    expect(card).toHaveTextContent("108.5")
    expect(card).toHaveTextContent("999.99")
    expect(card).toHaveTextContent("32.4")
    expect(card).toHaveTextContent("2")
    expect(card).toHaveTextContent("1")
    expect(card).not.toHaveTextContent("region")
    expect(card).not.toHaveTextContent("bad-profile")
    expect(card).not.toHaveTextContent("created_at")
    expect(card).not.toHaveTextContent("is_active")
  })

  it("keeps all-null numeric fields visible with placeholder stats", () => {
    render(
      <NumericSummaryCard
        report={makeReport({
          row_count: 10,
          columns: [
            makeColumn({
              name: "empty_numeric",
              dtype: "Float64",
              null_count: 10,
              distinct_count: 1,
              min_value: null,
              p25_value: null,
              median_value: null,
              mean_value: null,
              p75_value: null,
              max_value: null,
              std_value: null,
              zero_count: 0,
              negative_count: 0,
            }),
          ],
        })}
      />,
    )

    const card = screen.getByTestId("explore-numeric-summary-card")
    expect(card).toHaveTextContent("1 field")
    expect(card).toHaveTextContent("empty_numeric")
    expect(card).toHaveTextContent("100.0%")
    expect(screen.getAllByTestId("explore-numeric-summary-row")).toHaveLength(1)
    expect(card).not.toHaveTextContent("No numeric fields in this dataset.")
  })

  it("renders an empty numeric summary state when there are no numeric fields", () => {
    render(
      <NumericSummaryCard
        report={makeReport({
          columns: [
            makeColumn({ name: "region", dtype: "String", kind: "Text", distinct_count: 4 }),
            makeColumn({ name: "is_active", dtype: "Boolean", kind: "Boolean", distinct_count: 2 }),
          ],
        })}
      />,
    )

    const card = screen.getByTestId("explore-numeric-summary-card")
    expect(card).toHaveTextContent("No numeric fields in this dataset.")
    expect(card).not.toHaveTextContent("region")
  })

  it("renders categorical fields with distinct counts and expands bounded values in a detail row", () => {
    render(<CategoricalSummaryCard report={makeReport()} />)

    const card = screen.getByTestId("explore-categorical-summary-card")
    expect(card).toHaveTextContent("Categorical Summary")
    expect(card).toHaveTextContent("2 fields")
    expect(screen.getByRole("table", { name: "Categorical field distinct values" }))
      .not.toHaveTextContent("Values")
    expect(card).toHaveTextContent("region")
    expect(card).toHaveTextContent("String")
    expect(card).toHaveTextContent("2.0%")
    expect(card).toHaveTextContent("4")
    expect(card).toHaveTextContent("policy_id")
    expect(card).toHaveTextContent("1,234")
    expect(card).not.toHaveTextContent("High cardinality")
    expect(card).not.toHaveTextContent("north")
    expect(screen.queryByTestId("explore-categorical-values-detail")).not.toBeInTheDocument()

    const expandButton = screen.getByRole("button", { name: /expand region/i })
    expect(expandButton).toHaveAttribute("aria-expanded", "false")

    fireEvent.click(expandButton)

    const detail = screen.getByTestId("explore-categorical-values-detail")
    expect(screen.getByRole("button", { name: /collapse region/i })).toHaveAttribute("aria-expanded", "true")
    expect(detail).toHaveAttribute("colspan", "5")
    expect(detail).toHaveTextContent("Top values")
    expect(detail).toHaveTextContent("region")
    expect(screen.getByRole("list", { name: /region value counts/i })).toBeInTheDocument()
    expect(screen.getByRole("listitem", { name: /north, count 20/i })).toBeInTheDocument()
    expect(detail).toHaveTextContent("north")
    expect(detail).toHaveTextContent("20")
    expect(detail).toHaveTextContent("south")
    expect(detail).toHaveTextContent("10")

    fireEvent.click(screen.getByRole("button", { name: /collapse region/i }))

    expect(screen.queryByTestId("explore-categorical-values-detail")).not.toBeInTheDocument()
  })

  it("expands high-cardinality categorical fields with capped top-value details", () => {
    render(<CategoricalSummaryCard report={makeReport()} />)

    fireEvent.click(screen.getByRole("button", { name: /expand policy_id/i }))

    const detail = screen.getByTestId("explore-categorical-values-detail")
    expect(detail).toHaveTextContent("Top 50 groups")
    expect(detail).toHaveTextContent("policy_id")
    expect(within(detail).getAllByRole("listitem")).toHaveLength(50)
    expect(screen.getByRole("listitem", { name: /p001, count 50/i })).toBeInTheDocument()
    expect(screen.getByRole("listitem", { name: /p050, count 1/i })).toBeInTheDocument()
    expect(within(detail).queryByText("p051")).not.toBeInTheDocument()
  })

  it("renders an empty categorical summary state when there are no non-numeric fields", () => {
    render(
      <CategoricalSummaryCard
        report={makeReport({
          overview_summary: {
            data_quality: { issue_count: 0, issues: [] },
            categorical_summary: [],
          },
        })}
      />,
    )

    const card = screen.getByTestId("explore-categorical-summary-card")
    expect(card).toHaveTextContent("No non-numeric fields in this dataset.")
  })
})
