import { useState, type ComponentProps } from "react"
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

import ExplorePivotsConfig from "../../panels/editors/ExplorePivotsConfig"
import type { OnUpdateConfig } from "../../panels/editors/_shared"
import {
  createExploreChart,
  seedValueEncodings,
} from "../../panels/explore/chartConfig"
import type { ExplorePivotConfig } from "../../panels/explore/pivotConfig"

afterEach(cleanup)

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
    options: { row_grand_totals: true, column_grand_totals: true },
    ...overrides,
  }
}

function PivotConfigHarness({
  initialConfig = {},
  onUpdatePreview,
  onCommittedUpdate,
  loadFilterMembers,
}: {
  initialConfig?: Record<string, unknown>
  onUpdatePreview?: (pivotId: string) => void
  onCommittedUpdate?: () => void
  loadFilterMembers?: ComponentProps<typeof ExplorePivotsConfig>["loadFilterMembers"]
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
        upstreamColumns={upstreamColumns}
        onUpdatePreview={onUpdatePreview}
        loadFilterMembers={loadFilterMembers}
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
    expect(screen.getByRole("checkbox", { name: "Show Pivot 1" })).toBeChecked()

    fireEvent.click(screen.getByRole("button", { name: "Configure Pivot 1" }))
    expect(screen.getByRole("heading", { name: "Configure Pivot 1" })).toBeInTheDocument()
    expect(screen.queryByRole("checkbox", { name: "Show Pivot 1" })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", { name: "Back to pivots" }))
    expect(screen.getByRole("checkbox", { name: "Show Pivot 1" })).toBeChecked()

    fireEvent.click(screen.getByRole("checkbox", { name: "Show Pivot 1" }))
    expect(screen.getByRole("checkbox", { name: "Show Pivot 1" })).not.toBeChecked()

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
      options: { row_grand_totals: true, column_grand_totals: true },
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

    fireEvent.click(screen.getByRole("button", { name: "Add region to Rows" }))
    expect(screen.getByRole("button", { name: "Add region to Rows" })).toBeDisabled()
    expect(screen.getByRole("group", { name: "region in Rows" })).toBeInTheDocument()

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

    fireEvent.click(screen.getByRole("button", { name: "Add region to Values" }))
    expect(
      within(valueZone).getByRole("combobox", { name: "Aggregation for region" }),
    ).toHaveValue("count")
  })

  it("commits name, reorder/remove/options, and invokes preview separately", () => {
    const onUpdatePreview = vi.fn()
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
        onUpdatePreview={onUpdatePreview}
      />,
    )
    fireEvent.click(screen.getByRole("button", { name: "Configure Pivot 1" }))

    const name = screen.getByRole("textbox", { name: "Pivot name" })
    fireEvent.change(name, { target: { value: "Claims analysis" } })
    fireEvent.blur(name)
    fireEvent.click(screen.getByRole("button", { name: "Move year up in Rows" }))
    fireEvent.click(screen.getByRole("button", { name: "Remove region from Rows" }))
    fireEvent.click(screen.getByRole("checkbox", { name: "Show row grand totals" }))
    fireEvent.click(screen.getByRole("button", { name: "Update preview" }))

    expect(onUpdatePreview).toHaveBeenCalledWith("pivot_1")
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

  it("moves a placement between zones in one committed edit", () => {
    const onCommittedUpdate = vi.fn()
    render(
      <PivotConfigHarness
        initialConfig={{
          pivots: [fullPivot({ columns: [{ id: "column_1", field: "year" }] })],
        }}
        onCommittedUpdate={onCommittedUpdate}
      />,
    )
    fireEvent.click(screen.getByRole("button", { name: "Configure Pivot 1" }))

    fireEvent.change(screen.getByRole("combobox", { name: "Move year from Columns" }), {
      target: { value: "rows" },
    })

    const persisted = JSON.parse(screen.getByTestId("persisted-config").textContent ?? "{}")
    expect(persisted.pivots[0].columns).toEqual([])
    expect(persisted.pivots[0].rows).toEqual([{ id: "column_1", field: "year" }])
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
