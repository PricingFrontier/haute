import { useState } from "react"
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import type { ExplorePivotResult } from "../../api/types"
import ExploreChartsConfig from "../../panels/editors/ExploreChartsConfig"
import type { OnUpdateConfig } from "../../panels/editors/_shared"
import {
  createExploreChart,
  type ExploreChartConfig,
} from "../../panels/explore/chartConfig"
import {
  pivotCalculationIdentity,
  type ExplorePivotConfig,
} from "../../panels/explore/pivotConfig"
import useNodeResultsStore, {
  explorePivotResultKey,
  resetNodeResultsDerivedCaches,
} from "../../stores/useNodeResultsStore"

afterEach(cleanup)

function pivot(
  id: string,
  overrides: Partial<ExplorePivotConfig> = {},
): ExplorePivotConfig {
  return {
    version: 1,
    id,
    name: `Pivot ${id}`,
    enabled: true,
    filters: [],
    columns: [],
    rows: [{ id: `${id}-row`, field: "region" }],
    values: [
      {
        id: `${id}-paid`,
        field: "paid",
        aggregation: "sum",
        display_name: "Paid",
      },
      {
        id: `${id}-count`,
        field: "claim_id",
        aggregation: "count",
        display_name: "Claims",
      },
    ],
    options: { row_grand_totals: true, column_grand_totals: true },
    ...overrides,
  }
}

function chart(
  sourcePivot: ExplorePivotConfig | null = null,
  overrides: Partial<ExploreChartConfig> = {},
): ExploreChartConfig {
  const draft = createExploreChart([])
  return {
    ...draft,
    ...(sourcePivot
      ? {
          pivot_id: sourcePivot.id,
          value_encodings: sourcePivot.values.map((value, index) => ({
            id: `encoding_${index + 1}`,
            value_id: value.id,
            mark: "column" as const,
            axis: "primary" as const,
            stack_group: null,
            color: null,
            data_labels: false,
            markers: false,
          })),
        }
      : {}),
    ...overrides,
  }
}

function result(sourcePivot: ExplorePivotConfig): ExplorePivotResult {
  return {
    version: 1,
    node_id: "explore_1",
    pivot_id: sourcePivot.id,
    source: "pricing",
    dataframe_cache_key: "dataframe-current",
    calculation_key: "backend-calculation",
    row_fields: ["region"],
    column_fields: [],
    values: sourcePivot.values.map(({ id, field, aggregation }) => ({
      id,
      field,
      aggregation,
    })),
    row_paths: [
      {
        members: [{ kind: "string", value: "North" }],
        is_grand_total: false,
      },
    ],
    column_paths: [{ members: [], is_grand_total: false }],
    cells: sourcePivot.values.map((value, index) => ({
      row_index: 0,
      column_index: 0,
      value_id: value.id,
      value: index + 1,
    })),
    warnings: [],
    generated_at: 1,
    execution_metrics: null,
  }
}

function seedFreshPivot(sourcePivot: ExplorePivotConfig) {
  const key = explorePivotResultKey("explore_1", sourcePivot.id)
  useNodeResultsStore.getState().startExplorePivotJob(
    key,
    "pivot-job",
    "explore_1",
    sourcePivot.id,
    "Explore Claims",
    sourcePivot.name,
    pivotCalculationIdentity(sourcePivot),
    "pricing",
    0,
  )
  useNodeResultsStore.getState().completeExplorePivotJob(key, result(sourcePivot))
  useNodeResultsStore.setState({
    exploreResults: {
      explore_1: {
        result: {
          status: "ok",
          node_id: "explore_1",
          upstream_node_id: "source_1",
          source: "pricing",
          dataframe_cache_key: "dataframe-current",
          row_count: 1,
          column_count: 2,
          generated_at: 1,
          columns: [],
          overview_summary: {
            data_quality: {
              issue_count: 0,
              issues: [],
              duplicate_row_count: 0,
              duplicate_ratio: 0,
            },
            categorical_summary: [],
          },
        },
        jobId: "explore-job",
        configHash: "hash",
        source: "pricing",
        structuralVersion: 0,
        nodeLabel: "Explore Claims",
      },
    },
  })
}

function ChartConfigHarness({
  initialConfig = {},
  onCommittedUpdate,
  onShowPivots,
}: {
  initialConfig?: Record<string, unknown>
  onCommittedUpdate?: () => void
  onShowPivots?: () => void
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
      <ExploreChartsConfig
        config={config}
        onUpdate={onUpdate}
        nodeId="explore_1"
        onShowPivots={onShowPivots}
      />
      <output data-testid="persisted-config">{JSON.stringify(config)}</output>
    </>
  )
}

describe("ExploreChartsConfig", () => {
  beforeEach(() => {
    resetNodeResultsDerivedCaches()
    useNodeResultsStore.setState({
      pivotResults: {},
      pivotJobs: {},
      exploreResults: {},
    })
  })

  it("adds complete drafts and keeps toggle separate from Configure and Back", () => {
    render(<ChartConfigHarness />)

    fireEvent.click(screen.getByRole("button", { name: "Add Chart" }))
    fireEvent.click(screen.getByRole("button", { name: "Add Chart" }))

    expect(screen.getByRole("checkbox", { name: "Show Chart 1" })).toBeChecked()
    expect(screen.getByRole("checkbox", { name: "Show Chart 2" })).toBeChecked()

    fireEvent.click(screen.getByRole("button", { name: "Configure Chart 1" }))
    expect(
      screen.getByRole("heading", { name: "Configure Chart 1" }),
    ).toBeVisible()
    expect(screen.queryByRole("checkbox", { name: "Show Chart 1" })).toBeNull()

    fireEvent.click(screen.getByRole("button", { name: "Back to charts" }))
    fireEvent.click(screen.getByRole("checkbox", { name: "Show Chart 2" }))
    expect(screen.getByRole("checkbox", { name: "Show Chart 1" })).toBeChecked()
    expect(screen.getByRole("checkbox", { name: "Show Chart 2" })).not.toBeChecked()

    const persisted = JSON.parse(
      screen.getByTestId("persisted-config").textContent ?? "{}",
    )
    expect(persisted.charts[0]).toMatchObject({
      version: 1,
      id: "chart_1",
      name: "Chart 1",
      enabled: true,
      pivot_id: null,
      kind: "combo",
      value_encodings: [],
      series_overrides: [],
      category: {
        source: "rows",
        include_subtotals: false,
        include_grand_total: false,
        label_rotation: 0,
      },
    })
  })

  it("removes a chart only after explicit confirmation", () => {
    const first = chart(null, { id: "chart_1", name: "Chart 1" })
    const second = chart(null, { id: "chart_2", name: "Chart 2" })
    const confirm = vi.spyOn(window, "confirm").mockReturnValueOnce(false)
    render(
      <ChartConfigHarness initialConfig={{ charts: [first, second] }} />,
    )

    fireEvent.click(screen.getByRole("button", { name: "Delete Chart 1" }))
    expect(screen.getByRole("button", { name: "Configure Chart 1" })).toBeVisible()

    confirm.mockReturnValueOnce(true)
    fireEvent.click(screen.getByRole("button", { name: "Delete Chart 1" }))
    expect(screen.queryByRole("button", { name: "Configure Chart 1" })).toBeNull()
    expect(screen.getByRole("button", { name: "Configure Chart 2" })).toBeVisible()
    confirm.mockRestore()
  })

  it("guides an empty chart workflow to Pivots without mutating config", () => {
    const onShowPivots = vi.fn()
    const draft = chart()
    render(
      <ChartConfigHarness
        initialConfig={{ charts: [draft], pivots: [] }}
        onShowPivots={onShowPivots}
      />,
    )

    fireEvent.click(screen.getByRole("button", { name: `Configure ${draft.name}` }))
    expect(screen.getByText(/requires a pivot/i)).toBeVisible()
    fireEvent.click(screen.getByRole("button", { name: /go to pivots/i }))

    expect(onShowPivots).toHaveBeenCalledTimes(1)
    expect(JSON.parse(screen.getByTestId("persisted-config").textContent ?? "{}")).toEqual({
      charts: [draft],
      pivots: [],
    })
  })

  it("lists hidden pivots, seeds mappings, and exposes inherited fields", () => {
    const hidden = pivot("hidden", { enabled: false })
    const draft = chart()
    render(
      <ChartConfigHarness
        initialConfig={{ charts: [draft], pivots: [hidden] }}
      />,
    )

    fireEvent.click(screen.getByRole("button", { name: `Configure ${draft.name}` }))
    const source = screen.getByRole("combobox", { name: "Source pivot" })
    expect(within(source).getByRole("option", { name: /Hidden/i })).toBeVisible()
    fireEvent.change(source, { target: { value: hidden.id } })

    expect(screen.getByText(/Rows: region/i)).toBeVisible()
    expect(screen.getByText(/Values: Paid, Claims/i)).toBeVisible()
    const persisted = JSON.parse(
      screen.getByTestId("persisted-config").textContent ?? "{}",
    )
    expect(persisted.charts[0].pivot_id).toBe(hidden.id)
    expect(persisted.charts[0].value_encodings).toHaveLength(2)
    expect(persisted.charts[0].series_overrides).toEqual([])
  })

  it("confirms a populated source reset and commits it as one edit", () => {
    const first = pivot("first")
    const second = pivot("second", {
      values: [
        {
          id: "second-average",
          field: "paid",
          aggregation: "average",
          display_name: "Average paid",
        },
      ],
    })
    const configured = chart(first)
    const onCommittedUpdate = vi.fn()
    const confirm = vi.spyOn(window, "confirm").mockReturnValueOnce(false)
    render(
      <ChartConfigHarness
        initialConfig={{ charts: [configured], pivots: [first, second] }}
        onCommittedUpdate={onCommittedUpdate}
      />,
    )

    fireEvent.click(
      screen.getByRole("button", { name: `Configure ${configured.name}` }),
    )
    const source = screen.getByRole("combobox", { name: "Source pivot" })
    fireEvent.change(source, { target: { value: second.id } })
    expect(source).toHaveValue(first.id)
    expect(onCommittedUpdate).not.toHaveBeenCalled()

    confirm.mockReturnValueOnce(true)
    fireEvent.change(source, { target: { value: second.id } })
    expect(onCommittedUpdate).toHaveBeenCalledTimes(1)
    const persisted = JSON.parse(
      screen.getByTestId("persisted-config").textContent ?? "{}",
    )
    expect(persisted.charts[0]).toMatchObject({
      pivot_id: second.id,
      series_overrides: [],
      value_encodings: [
        expect.objectContaining({ value_id: "second-average" }),
      ],
    })
    confirm.mockRestore()
  })

  it("applies a combo preset and commits per-value, axis, legend, and category controls", async () => {
    const sourcePivot = pivot("source")
    const configured = chart(sourcePivot)
    render(
      <ChartConfigHarness
        initialConfig={{ charts: [configured], pivots: [sourcePivot] }}
      />,
    )
    fireEvent.click(
      screen.getByRole("button", { name: `Configure ${configured.name}` }),
    )

    fireEvent.change(screen.getByRole("combobox", { name: "Chart preset" }), {
      target: { value: "column_line_secondary" },
    })
    expect(screen.getByRole("combobox", { name: "Mark for Claims" })).toHaveValue(
      "line",
    )
    expect(screen.getByRole("combobox", { name: "Axis for Claims" })).toHaveValue(
      "secondary",
    )

    fireEvent.click(screen.getByRole("checkbox", { name: "Data labels for Paid" }))
    fireEvent.change(screen.getByRole("combobox", { name: "Legend position" }), {
      target: { value: "right" },
    })
    fireEvent.change(screen.getByRole("combobox", { name: "Category label rotation" }), {
      target: { value: "45" },
    })
    fireEvent.change(screen.getByRole("combobox", { name: "Primary number format" }), {
      target: { value: "currency_gbp" },
    })

    await waitFor(() => {
      const persisted = JSON.parse(
        screen.getByTestId("persisted-config").textContent ?? "{}",
      )
      expect(persisted.charts[0]).toMatchObject({
        category: expect.objectContaining({ label_rotation: 45 }),
        legend: expect.objectContaining({ position: "right" }),
        axes: {
          primary: expect.objectContaining({ number_format: "currency_gbp" }),
          secondary: expect.any(Object),
        },
      })
      expect(persisted.charts[0].value_encodings[0].data_labels).toBe(true)
    })
  })

  it("validates manual bounds and colours before committing complete values", () => {
    const sourcePivot = pivot("source")
    const configured = chart(sourcePivot, {
      axes: {
        primary: {
          title: "",
          minimum: null,
          maximum: 10,
          number_format: "inherit",
        },
        secondary: {
          title: "",
          minimum: null,
          maximum: null,
          number_format: "inherit",
        },
      },
    })
    render(
      <ChartConfigHarness
        initialConfig={{ charts: [configured], pivots: [sourcePivot] }}
      />,
    )
    fireEvent.click(
      screen.getByRole("button", { name: `Configure ${configured.name}` }),
    )

    const minimum = screen.getByRole("spinbutton", { name: "Primary minimum" })
    fireEvent.change(minimum, { target: { value: "12" } })
    fireEvent.blur(minimum)
    expect(screen.getByRole("alert")).toHaveTextContent(/minimum must be less/i)
    let persisted = JSON.parse(
      screen.getByTestId("persisted-config").textContent ?? "{}",
    )
    expect(persisted.charts[0].axes.primary.minimum).toBeNull()

    fireEvent.change(minimum, { target: { value: "-5" } })
    fireEvent.blur(minimum)
    persisted = JSON.parse(
      screen.getByTestId("persisted-config").textContent ?? "{}",
    )
    expect(persisted.charts[0].axes.primary.minimum).toBe(-5)

    const colour = screen.getByRole("textbox", { name: "Colour for Paid" })
    fireEvent.change(colour, { target: { value: "#12" } })
    fireEvent.blur(colour)
    expect(screen.getByRole("alert")).toHaveTextContent(/complete #RRGGBB/i)
    persisted = JSON.parse(
      screen.getByTestId("persisted-config").textContent ?? "{}",
    )
    expect(persisted.charts[0].value_encodings[0].color).toBeNull()

    fireEvent.change(colour, { target: { value: "#aabbcc" } })
    fireEvent.blur(colour)
    persisted = JSON.parse(
      screen.getByTestId("persisted-config").textContent ?? "{}",
    )
    expect(persisted.charts[0].value_encodings[0].color).toBe("#AABBCC")
  })

  it("shows concrete series and creates an exact override from a fresh result", () => {
    const sourcePivot = pivot("source", {
      columns: [{ id: "source-year", field: "year" }],
    })
    const configured = chart(sourcePivot)
    configured.value_encodings[0].id = "override_1"
    const pivotResult = result(sourcePivot)
    pivotResult.column_fields = ["year"]
    pivotResult.column_paths = [
      {
        members: [{ kind: "integer", value: "2024" }],
        is_grand_total: false,
      },
    ]
    seedFreshPivot(sourcePivot)
    const key = explorePivotResultKey("explore_1", sourcePivot.id)
    useNodeResultsStore.setState((state) => ({
      pivotResults: {
        ...state.pivotResults,
        [key]: { ...state.pivotResults[key], result: pivotResult },
      },
    }))

    render(
      <ChartConfigHarness
        initialConfig={{ charts: [configured], pivots: [sourcePivot] }}
      />,
    )
    fireEvent.click(
      screen.getByRole("button", { name: `Configure ${configured.name}` }),
    )

    expect(screen.getByText("2024 · Paid")).toBeVisible()
    fireEvent.click(
      screen.getByRole("button", { name: "Override 2024 · Paid" }),
    )
    expect(screen.getByRole("group", { name: "Override 2024 · Paid" })).toBeVisible()
    let persisted = JSON.parse(
      screen.getByTestId("persisted-config").textContent ?? "{}",
    )
    expect(persisted.charts[0].series_overrides).toHaveLength(1)
    expect(persisted.charts[0].series_overrides[0].id).toBe("override_2")
    expect(persisted.charts[0].series_overrides[0]).not.toHaveProperty(
      "value_id",
    )

    fireEvent.click(
      screen.getByRole("button", {
        name: /Reset .* to Value default/,
      }),
    )
    persisted = JSON.parse(
      screen.getByTestId("persisted-config").textContent ?? "{}",
    )
    expect(persisted.charts[0].series_overrides).toEqual([])
    expect(
      screen.getByRole("button", { name: /Override .* Paid/ }),
    ).toBeVisible()
  })

  it("surfaces malformed persisted charts without destructive controls", () => {
    render(
      <ExploreChartsConfig
        config={{
          charts: [
            { id: "chart_1", enabled: true },
            { id: "chart_1", enabled: false },
          ],
        }}
        onUpdate={vi.fn()}
        nodeId="explore_1"
      />,
    )

    expect(screen.getByRole("alert")).toHaveTextContent(/duplicate chart id/i)
    expect(screen.queryByRole("button", { name: "Add Chart" })).toBeNull()
    expect(screen.queryByRole("checkbox")).toBeNull()
  })
})
