import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import type { ExploreColumnStat } from "../../../api/types"
import type { ExploreDataView } from "../exploreDataView"
import ExploreRelationshipsPane from "../ExploreRelationshipsPane"
import { WHOLE_DATA_DEBOUNCE_MS } from "../../editors/shared/useWholeDataAnswer"
import { makeExploreColumnStat, makeNode } from "../../../test-utils/factories"
import useDocumentStatusStore from "../../../stores/useDocumentStatusStore"
import useGraphStore from "../../../stores/useGraphStore"
import useSettingsStore from "../../../stores/useSettingsStore"

const mocks = vi.hoisted(() => ({
  relationships: vi.fn(),
  run: vi.fn(),
  availability: "current" as "current" | "stale" | "missing" | "checking",
  problem: null as { kind: "refused" | "failed"; reason: string } | null,
}))

vi.mock("../../../api/exploreRelationships", () => ({
  getExploreRelationships: mocks.relationships,
}))
vi.mock("../../../hooks/useNodeDataCache", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../../hooks/useNodeDataCache")>()),
  default: () => ({ availability: mocks.availability, problem: mocks.problem, dataVersion: "v1", run: mocks.run }),
}))
const node = { id: "explore", data: { label: "explore", description: "", nodeType: "explore", config: {} } }

/** The pipeline the editor holds: the Explore node behind its source. */
function loadDocument() {
  useGraphStore.getState().loadGraphSnapshot({
    nodes: [
      makeNode("source", "dataInput", { data: { label: "source", nodeType: "dataInput", config: { path: "claims.parquet" } } }),
      makeNode("explore", "explore", { data: { ...node.data } }),
    ],
    edges: [{ id: "e1", source: "source", target: "explore" }],
    preamble: "",
    submodels: {},
  })
}

function column(name: string, kind: ExploreColumnStat["kind"]): ExploreColumnStat {
  return makeExploreColumnStat({
    name,
    dtype: kind === "Numeric" ? "Float64" : "String",
    kind,
    null_count: 0,
    distinct_count: 3,
    unique_ratio: null,
    is_high_cardinality: false,
    is_identifier_candidate: false,
    text_min_length: null,
    text_mean_length: null,
    text_max_length: null,
    temporal_span: null,
  })
}

const report = {
  producer_label: "source",
  source: "live",
  data_version: "v1",
  row_count: 100,
  column_count: 4,
  generated_at: 1,
  columns: [
    column("claims", "Numeric"),
    column("exposure", "Numeric"),
    column("region", "Text"),
    column("age", "Numeric"),
  ],
  overview_summary: { data_quality: { issue_count: 0, issues: [], duplicate_row_count: 0, duplicate_ratio: 0 }, categorical_summary: [] },
} as unknown as ExploreDataView

const answer = {
  status: "ok",
  point: {},
  data_version: "v1",
  total_rows: 100,
  target: "claims",
  weight: null,
  used_rows: 98,
  relationships: [
    {
      feature: "region",
      kind: "categorical",
      strength: 0.4,
      levels_truncated: true,
      levels: [
        { label: "north", kind: "value", rows: 60, weight: 60, target_mean: 1.5 },
        { label: "Other", kind: "other", rows: 38, weight: 38, target_mean: 0.5 },
      ],
    },
    {
      feature: "age",
      kind: "numeric",
      strength: 0.05,
      levels_truncated: false,
      levels: [{ label: "18 to 30", kind: "bin", rows: 98, weight: 98, target_mean: 1 }],
    },
  ],
  key_check: null,
}

function renderPane() {
  return render(
    <ExploreRelationshipsPane node={node} allNodes={[node]} edges={[]} report={report} />,
  )
}

async function settle() {
  await act(async () => {
    vi.advanceTimersByTime(WHOLE_DATA_DEBOUNCE_MS + 1)
    await Promise.resolve()
    await Promise.resolve()
  })
}

describe("ExploreRelationshipsPane", () => {
  beforeEach(() => {
    vi.useFakeTimers()
    useGraphStore.getState().resetForTests()
    useDocumentStatusStore.getState().reset()
    useSettingsStore.setState({ activeSource: "live" })
    loadDocument()
    mocks.relationships.mockReset()
    mocks.availability = "current"
    mocks.problem = null
  })
  afterEach(() => {
    cleanup()
    vi.useRealTimers()
  })

  it("asks nothing until a question is chosen, and offers features only after a target", async () => {
    renderPane()
    await settle()

    expect(mocks.relationships).not.toHaveBeenCalled()
    expect(screen.getByText(/Choose features to relate to a target/)).toBeInTheDocument()
    expect(screen.getByText("Choose a target to relate features to.")).toBeInTheDocument()
    expect(within(screen.getByRole("combobox", { name: "Target" })).getAllByRole("option").map((option) => option.textContent))
      .toEqual(["Choose a numeric target…", "claims", "exposure", "age"])
  })

  it("relates the chosen features to the target and ranks them by strength", async () => {
    mocks.relationships.mockResolvedValue(answer)
    renderPane()

    fireEvent.change(screen.getByRole("combobox", { name: "Target" }), { target: { value: "claims" } })
    const features = screen.getByRole("group", { name: /Features/ })
    expect(within(features).queryByLabelText("claims")).toBeNull()
    fireEvent.click(within(features).getByLabelText("region"))
    fireEvent.click(within(features).getByLabelText("age"))
    await settle()

    expect(mocks.relationships).toHaveBeenCalledTimes(1)
    expect(mocks.relationships).toHaveBeenCalledWith(
      expect.objectContaining({
        // The pipeline the editor holds, read from the graph store.
        graph: expect.objectContaining({
          nodes: [
            expect.objectContaining({ id: "source", data: expect.objectContaining({ config: { path: "claims.parquet" } }) }),
            expect.objectContaining({ id: "explore" }),
          ],
          edges: [expect.objectContaining({ id: "e1", source: "source", target: "explore" })],
        }),
        target: { occurrence: null, node_id: "explore" },
        source: "live",
        target_column: "claims",
        weight: null,
        features: ["region", "age"],
        key_columns: [],
        signal: expect.any(AbortSignal),
      }),
    )
    const rows = screen.getAllByTestId("explore-relationship-row")
    expect(rows.map((row) => within(row).getAllByRole("cell")[0].textContent)).toEqual(["region", "age"])
    expect(within(rows[0]).getByText("40%")).toBeInTheDocument()
    expect(within(rows[0]).getByText("2 (top levels)")).toBeInTheDocument()
    expect(screen.getByText(/over 98 of 100 rows/)).toBeInTheDocument()

    fireEvent.click(screen.getByRole("button", { name: "Show levels of region" }))
    const levels = screen.getByRole("table", { name: "Levels of region" })
    expect(within(levels).getByText("north")).toBeInTheDocument()
    expect(within(levels).getByText("Other")).toBeInTheDocument()
    expect(within(levels).getByText("1.5")).toBeInTheDocument()
  })

  it("supersedes a question when the choices change, aborting the request in flight", async () => {
    const signals: AbortSignal[] = []
    mocks.relationships.mockImplementation(({ signal }: { signal: AbortSignal }) => {
      signals.push(signal)
      return new Promise(() => {})
    })
    renderPane()
    fireEvent.change(screen.getByRole("combobox", { name: "Target" }), { target: { value: "claims" } })
    const features = screen.getByRole("group", { name: /Features/ })
    fireEvent.click(within(features).getByLabelText("region"))
    await settle()
    expect(signals).toHaveLength(1)

    fireEvent.click(within(features).getByLabelText("age"))
    expect(signals[0].aborted).toBe(true)
    await settle()
    expect(signals).toHaveLength(2)
    expect(mocks.relationships).toHaveBeenLastCalledWith(expect.objectContaining({ features: ["region", "age"] }))
  })

  it("reports a key check on its own, without a target", async () => {
    mocks.relationships.mockResolvedValue({
      ...answer,
      target: null,
      used_rows: 0,
      relationships: [],
      key_check: {
        columns: ["region", "age"],
        rows: 100,
        distinct_keys: 97,
        duplicate_rows: 3,
        null_key_rows: 1,
        unique: false,
      },
    })
    renderPane()
    const keys = screen.getByRole("group", { name: /Key columns/ })
    fireEvent.click(within(keys).getByLabelText("region"))
    fireEvent.click(within(keys).getByLabelText("age"))
    await settle()

    expect(mocks.relationships).toHaveBeenLastCalledWith(
      expect.objectContaining({ target_column: null, features: [], key_columns: ["region", "age"] }),
    )
    expect(screen.getByTestId("explore-key-check")).toHaveTextContent(
      "region + age is not unique: 3 duplicate rows, 1 rows with a missing key value (97 distinct of 100 rows).",
    )
  })

  it("waits for cached data instead of asking about a sample", async () => {
    mocks.availability = "missing"
    renderPane()
    const keys = screen.getByRole("group", { name: /Key columns/ })
    fireEvent.click(within(keys).getByLabelText("region"))
    await settle()

    expect(mocks.relationships).not.toHaveBeenCalled()
    expect(screen.getByText("Not cached · Refresh this node to read all rows")).toBeInTheDocument()
  })

  it("says why there is nothing to analyse when the server refuses the node's data point", async () => {
    mocks.availability = "checking"
    mocks.problem = { kind: "refused", reason: "Node 'explore' must have exactly one incoming connection." }
    renderPane()
    fireEvent.click(within(screen.getByRole("group", { name: /Key columns/ })).getByLabelText("region"))
    await settle()

    expect(mocks.relationships).not.toHaveBeenCalled()
    expect(screen.getByText("Node 'explore' must have exactly one incoming connection.")).toBeInTheDocument()
  })

  it("says reading the data failed, never that it is waiting, when its data point cannot be read", async () => {
    mocks.availability = "checking"
    mocks.problem = { kind: "failed", reason: "server down" }
    renderPane()
    fireEvent.click(within(screen.getByRole("group", { name: /Key columns/ })).getByLabelText("region"))
    await settle()

    expect(screen.getByRole("alert")).toHaveTextContent("Couldn't read the data: server down")
    expect(screen.queryByText("Waiting to analyse…")).toBeNull()
  })

  it("offers to cache again when the server has no cached data for the question", async () => {
    mocks.relationships.mockResolvedValue({ ...answer, status: "cache_required", relationships: [] })
    mocks.run.mockResolvedValue(undefined)
    renderPane()
    fireEvent.change(screen.getByRole("combobox", { name: "Target" }), { target: { value: "claims" } })
    fireEvent.click(within(screen.getByRole("group", { name: /Features/ })).getByLabelText("region"))
    await settle()

    expect(screen.getByText(/The server has no cached data for these columns/)).toBeInTheDocument()
    expect(screen.queryByText("Waiting to analyse…")).toBeNull()
    fireEvent.click(screen.getByRole("button", { name: "Cache data" }))
    expect(mocks.run).toHaveBeenCalledOnce()
  })

  it("shows the server's reason when a question is refused", async () => {
    mocks.relationships.mockRejectedValue(Object.assign(new Error("HTTP 422"), { status: 422, detail: "'region' is List(String), which cannot be grouped." }))
    renderPane()
    fireEvent.change(screen.getByRole("combobox", { name: "Target" }), { target: { value: "claims" } })
    fireEvent.click(within(screen.getByRole("group", { name: /Features/ })).getByLabelText("region"))
    await settle()

    expect(screen.getByRole("alert")).toHaveTextContent("'region' is List(String), which cannot be grouped.")
  })
})
