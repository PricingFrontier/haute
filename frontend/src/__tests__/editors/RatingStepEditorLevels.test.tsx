/**
 * The Rating Step editor over the whole dataset (RAT-B03).
 *
 * The editor listed the factor levels it could see in a preview, so a level
 * that appears only outside those rows could not be given a rate and the rows
 * carrying it silently took the table's default. These pin what it does now:
 * it asks the server for the levels of the columns its tables rate on when the
 * point it reads is cached, says which rows those levels came from, and falls
 * back to the preview whenever they cannot describe the data it is looking at.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import type { ReactElement } from "react"

import RatingStepEditor from "../../panels/editors/RatingStepEditor"
import { GraphProvider } from "../../panels/GraphContext"
import useNodeDataStore from "../../stores/useNodeDataStore"
import useSettingsStore from "../../stores/useSettingsStore"
import useUIStore from "../../stores/useUIStore"
import type { NodeDataPointResponse, RatingLevelsResponse } from "../../api/types"

const mockGetRatingLevels = vi.fn()
const mockGetNodeDataPoint = vi.fn()

class ApiError extends Error {
  status: number
  detail?: string

  constructor(message: string, status: number) {
    super(message)
    this.status = status
  }
}

vi.mock("../../api/client", () => ({
  getMlflowDestinations: vi.fn(() =>
    Promise.resolve({
      mlflow_installed: true,
      mlflow_importable: true,
      auto: "local",
      destinations: [],
      detail: "",
    }),
  ),
  getRatingLevels: (...args: unknown[]) => mockGetRatingLevels(...args),
  getNodeDataPoint: (...args: unknown[]) => mockGetNodeDataPoint(...args),
  runNodeData: vi.fn(),
  getNodeDataStatus: vi.fn(),
  cancelNodeData: vi.fn(),
  clearNodeData: vi.fn(),
  clearInputCache: vi.fn(),
  ensureInputSnapshots: vi.fn(),
}))

const SLOT_KEY = "source_1||live"
const DATA_VERSION = "gen-1"

const ratingNode = {
  id: "rating_1",
  type: "ratingStep",
  data: { label: "Rating", description: "", nodeType: "ratingStep", config: {} },
}
const sourceNode = {
  id: "source_1",
  type: "dataInput",
  data: { label: "source", description: "", nodeType: "dataInput", config: {} },
}
const edges = [{ id: "e1", source: "source_1", target: "rating_1" }]

function point(
  state: NodeDataPointResponse["state"],
  dataVersion: string | null,
): NodeDataPointResponse {
  return {
    consumer_node_id: "rating_1",
    point: { producer_node_id: "source_1", port_label: null },
    slot_key: SLOT_KEY,
    // A Rating Step behind a transform: its data is built, so it can be stale
    // and there is a control to put that right.
    kind: "node_output",
    state,
    demand: ["region"],
    data_version: dataVersion,
    row_count: state === "current" ? 1000 : null,
    size_bytes: null,
    retention: null,
    generation: null,
    job: null,
    reads_directly: false,
    build_endpoint: null,
    clear_endpoint: null,
  }
}

function levels(overrides: Partial<RatingLevelsResponse> = {}): RatingLevelsResponse {
  return {
    status: "ok",
    point: point("current", DATA_VERSION),
    data_version: DATA_VERSION,
    total_rows: 1000,
    columns: [
      {
        column: "region",
        values: [
          { value: "North", count: 999 },
          { value: "Orkney", count: 1 },
        ],
        distinct_count: 2,
        null_count: 0,
      },
    ],
    ...overrides,
  }
}

const CONFIG = {
  tables: [
    {
      factors: ["region"],
      outputColumn: "region_factor",
      defaultValue: "1.0",
      entries: [{ region: "North", value: 1.1 }],
    },
  ],
}

/** Preview rows, in which the rare level never appears. */
const PREVIEW_ROWS = [{ region: "North" }, { region: "North" }]
const UPSTREAM_COLUMNS = [{ name: "region", dtype: "str" }]

let rerenderEditor: ((nodes: unknown[]) => void) | null = null

function renderEditor(ui: ReactElement, nodes: unknown[] = [sourceNode, ratingNode]) {
  const result = render(
    <GraphProvider allNodes={nodes as never} edges={edges} submodels={{}} preamble="">
      {ui}
    </GraphProvider>,
  )
  rerenderEditor = (next: unknown[]) =>
    result.rerender(
      <GraphProvider allNodes={next as never} edges={edges} submodels={{}} preamble="">
        {ui}
      </GraphProvider>,
    )
  return result
}

function editor(props: Record<string, unknown> = {}) {
  return (
    <RatingStepEditor
      config={CONFIG}
      onUpdate={vi.fn()}
      inputSources={[]}
      accentColor="#22d3ee"
      previewRows={PREVIEW_ROWS}
      upstreamColumns={UPSTREAM_COLUMNS}
      nodeId="rating_1"
      {...props}
    />
  )
}

describe("RatingStepEditor levels", () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    useNodeDataStore.getState().reset()
    useSettingsStore.setState({ activeSource: "live" })
    useUIStore.setState({ ratingStepEditorSections: {} })
    mockGetRatingLevels.mockReset()
    mockGetNodeDataPoint.mockReset()
    mockGetNodeDataPoint.mockResolvedValue(point("current", DATA_VERSION))
    mockGetRatingLevels.mockResolvedValue(levels())
  })

  afterEach(() => {
    vi.useRealTimers()
    cleanup()
    rerenderEditor = null
  })

  it("offers a level the preview never showed, once the data is cached", async () => {
    renderEditor(editor())

    // The preview has only "North", so that is all there is to rate on.
    expect(screen.queryByRole("rowheader", { name: "Orkney" })).not.toBeInTheDocument()

    await act(async () => {
      vi.advanceTimersByTime(300)
    })

    await waitFor(() => expect(mockGetRatingLevels).toHaveBeenCalled())
    expect(mockGetRatingLevels.mock.calls[0][0]).toMatchObject({
      node_id: "rating_1",
      source: "live",
      columns: ["region"],
    })
    // The rare level is now a row of the table, so it can be given a rate.
    expect(await screen.findByRole("rowheader", { name: "Orkney" })).toBeInTheDocument()
    expect(screen.getByTestId("rating-levels-basis")).toHaveTextContent(
      "Levels from all rows · 1,000",
    )
  })

  it("asks only about the columns its tables rate on", async () => {
    renderEditor(
      editor({
        config: {
          tables: [
            { factors: ["region"], outputColumn: "a", defaultValue: "1.0", entries: [] },
            { factors: ["region", "cover"], outputColumn: "b", defaultValue: "1.0", entries: [] },
          ],
        },
      }),
    )

    await act(async () => {
      vi.advanceTimersByTime(300)
    })

    await waitFor(() => expect(mockGetRatingLevels).toHaveBeenCalled())
    // Each column once, however many tables name it; nothing else is scanned.
    expect(mockGetRatingLevels.mock.calls[0][0].columns).toEqual(["cover", "region"])
  })

  it("does not ask about a banded output, which the banding config names", async () => {
    const bandingNode = {
      id: "banding_1",
      type: "banding",
      data: {
        label: "Banding",
        description: "",
        nodeType: "banding",
        config: {
          factors: [
            {
              banding: "continuous",
              column: "premium",
              outputColumn: "premium_band",
              rules: [{ op1: ">", val1: "0", op2: "", val2: "", assignment: "high" }],
            },
          ],
        },
      },
    }
    renderEditor(
      editor({
        config: {
          tables: [
            {
              factors: ["premium_band"],
              outputColumn: "premium_factor",
              defaultValue: "1.0",
              entries: [],
            },
          ],
        },
      }),
      [sourceNode, bandingNode, ratingNode],
    )

    await act(async () => {
      vi.advanceTimersByTime(300)
    })

    // Its only factor is a band, so there is nothing in the data to ask about.
    expect(mockGetRatingLevels).not.toHaveBeenCalled()
    expect(screen.getByRole("rowheader", { name: "high" })).toBeInTheDocument()
  })

  it("keeps preview levels while the data it reads is not cached", async () => {
    mockGetNodeDataPoint.mockResolvedValue(point("missing", null))

    renderEditor(editor())

    await act(async () => {
      vi.advanceTimersByTime(300)
    })

    expect(mockGetRatingLevels).not.toHaveBeenCalled()
    expect(screen.getByTestId("rating-levels-basis")).toHaveTextContent(
      "Levels from a sample · 2 rows",
    )
    expect(screen.getByRole("rowheader", { name: "North" })).toBeInTheDocument()
  })

  it("stops offering whole-dataset levels once an upstream edit moves the data on", async () => {
    renderEditor(editor())

    await act(async () => {
      vi.advanceTimersByTime(300)
    })
    expect(await screen.findByRole("rowheader", { name: "Orkney" })).toBeInTheDocument()

    // An upstream edit changes what the node reads, so the next answer about
    // its point is a stale one — and the levels read from the old generation
    // are no longer levels of the data being rated.
    mockGetNodeDataPoint.mockResolvedValue(point("stale", DATA_VERSION))
    rerenderEditor?.([
      { ...sourceNode, data: { ...sourceNode.data, config: { path: "other.parquet" } } },
      ratingNode,
    ])

    await act(async () => {
      vi.advanceTimersByTime(300)
    })

    await waitFor(() =>
      expect(screen.getByTestId("rating-levels-basis")).toHaveTextContent(
        "Cached data is out of date",
      ),
    )
    expect(screen.queryByRole("rowheader", { name: "Orkney" })).not.toBeInTheDocument()
  })

  it("adds an arriving level below what is on screen, never above it", async () => {
    // The rows the user can see must not move when the answer lands: they
    // arrive mid-edit, and a row that shifts takes the value meant for its
    // neighbour. The server's count order decides what its cap keeps, not
    // where a row sits.
    mockGetRatingLevels.mockResolvedValue(
      levels({
        columns: [
          {
            column: "region",
            values: [
              { value: "North", count: 900 },
              { value: "South", count: 99 },
              { value: "Orkney", count: 1 },
            ],
            distinct_count: 3,
            null_count: 0,
          },
        ],
      }),
    )

    // The preview shows South first, and "North" already has a rate saved.
    renderEditor(editor({ previewRows: [{ region: "South" }, { region: "North" }] }))
    const before = screen.getAllByRole("rowheader").map(cell => cell.textContent)
    expect(before).toEqual(["South", "North"])

    await act(async () => {
      vi.advanceTimersByTime(300)
    })

    await waitFor(() =>
      expect(screen.getAllByRole("rowheader").map(cell => cell.textContent)).toEqual([
        "South",
        "North",
        "Orkney",
      ]),
    )
  })

  it("will not draw a table too large to edit, or rebuild one", async () => {
    // Whole-dataset levels are real: two high-cardinality factors are a grid
    // no browser draws and nobody fills in by hand.
    const many = (prefix: string) =>
      Array.from({ length: 200 }, (_unused, index) => ({
        value: `${prefix}${index}`,
        count: 200 - index,
      }))
    mockGetRatingLevels.mockResolvedValue(
      levels({
        columns: [
          { column: "region", values: many("r"), distinct_count: 200, null_count: 0 },
          { column: "cover", values: many("c"), distinct_count: 200, null_count: 0 },
          {
            column: "age",
            values: many("a").slice(0, 10),
            distinct_count: 10,
            null_count: 0,
          },
        ],
      }),
    )

    renderEditor(
      editor({
        // No preview rows, so every level counted is one the data reported.
        previewRows: [],
        config: {
          tables: [
            {
              factors: ["region", "cover", "age"],
              outputColumn: "region_factor",
              defaultValue: "1.0",
              entries: [],
            },
          ],
        },
      }),
    )

    await act(async () => {
      vi.advanceTimersByTime(300)
    })

    const notice = await screen.findByTestId("rating-table-too-large")
    // Every dimension counts: 200 x 200 x 10, not the 40,000 that stopping at
    // the cap would report.
    expect(notice).toHaveTextContent("400,000 cells")
    expect(screen.getByRole("button", { name: /Rebuild from factor levels/ })).toBeDisabled()
    // The grid itself is not drawn: the notice is the whole of it.
    expect(screen.queryByRole("table")).not.toBeInTheDocument()
  })

  it("refuses a factor it could not build entries for, rather than saving a broken table", async () => {
    // Committing the factor while keeping the old entries would write a table
    // whose rows have no value for the new factor — a configuration the server
    // rejects, so the pipeline could not run at all.
    const many = (prefix: string) =>
      Array.from({ length: 200 }, (_unused, index) => ({
        value: `${prefix}${index}`,
        count: 200 - index,
      }))
    mockGetRatingLevels.mockResolvedValue(
      levels({
        columns: [
          { column: "region", values: many("r"), distinct_count: 200, null_count: 0 },
          { column: "cover", values: many("c"), distinct_count: 200, null_count: 0 },
        ],
      }),
    )
    const onUpdate = vi.fn()
    renderEditor(
      editor({
        onUpdate,
        config: {
          tables: [
            {
              factors: ["region"],
              outputColumn: "region_factor",
              defaultValue: "1.0",
              entries: [{ region: "r0", value: 1.1 }],
            },
            // A second table so "cover" is asked about and has levels to count.
            { factors: ["cover"], outputColumn: "cover_factor", defaultValue: "1.0", entries: [] },
          ],
        },
      }),
    )

    await act(async () => {
      vi.advanceTimersByTime(300)
    })
    await waitFor(() => expect(mockGetRatingLevels).toHaveBeenCalled())
    onUpdate.mockClear()

    await act(async () => {
      fireEvent.change(screen.getByLabelText("Add factor"), { target: { value: "cover" } })
    })

    expect(onUpdate).not.toHaveBeenCalled()
    expect(await screen.findByTestId("rating-factor-limit")).toHaveTextContent(
      "would be 40,200 cells",
    )
  })

  it("keeps the chosen slice when saving a rate reorders the levels", async () => {
    // Saving a rate for a level moves it in front of the levels that have none,
    // so a slice remembered by position would quietly become a different one.
    mockGetRatingLevels.mockResolvedValue(
      levels({
        columns: [
          {
            column: "region",
            values: [
              { value: "North", count: 900 },
              { value: "South", count: 99 },
              { value: "Orkney", count: 1 },
            ],
            distinct_count: 3,
            null_count: 0,
          },
        ],
      }),
    )
    const table = (entries: Record<string, string | number>[]) => ({
      tables: [
        {
          factors: ["size", "age", "region"],
          outputColumn: "region_factor",
          defaultValue: "1.0",
          entries,
        },
      ],
    })
    const previewRows = [
      { region: "North", size: "S", age: "Y" },
      { region: "North", size: "L", age: "O" },
    ]
    const { rerender } = renderEditor(editor({ config: table([]), previewRows }))

    await act(async () => {
      vi.advanceTimersByTime(300)
    })

    const slice = await screen.findByLabelText("region slice")
    await waitFor(() =>
      expect([...(slice as HTMLSelectElement).options].map(option => option.value)).toEqual([
        "North",
        "South",
        "Orkney",
      ]),
    )
    await act(async () => {
      fireEvent.change(slice, { target: { value: "Orkney" } })
    })

    // A rate saved in that slice puts Orkney among the levels the table knows,
    // which is ahead of South — still only in the data.
    rerender(
      <GraphProvider allNodes={[sourceNode, ratingNode] as never} edges={edges} submodels={{}} preamble="">
        {editor({
          config: table([{ size: "S", age: "Y", region: "Orkney", value: 2.5 }]),
          previewRows,
        })}
      </GraphProvider>,
    )

    const moved = await screen.findByLabelText("region slice")
    expect([...(moved as HTMLSelectElement).options].map(option => option.value)).toEqual([
      "North",
      "Orkney",
      "South",
    ])
    expect((moved as HTMLSelectElement).value).toBe("Orkney")
  })

  it("says nothing about data a table of banded outputs never reads", async () => {
    // Its levels come from the banding config, so there is no sample to name
    // and nothing for the cache control to be about.
    const bandingNode = {
      id: "banding_1",
      type: "banding",
      data: {
        label: "Banding",
        description: "",
        nodeType: "banding",
        config: {
          factors: [
            {
              banding: "continuous",
              column: "premium",
              outputColumn: "premium_band",
              rules: [{ op1: ">", val1: "0", op2: "", val2: "", assignment: "high" }],
            },
          ],
        },
      },
    }
    renderEditor(
      editor({
        config: {
          tables: [
            {
              factors: ["premium_band"],
              outputColumn: "premium_factor",
              defaultValue: "1.0",
              entries: [],
            },
          ],
        },
      }),
      [sourceNode, bandingNode, ratingNode],
    )

    await act(async () => {
      vi.advanceTimersByTime(300)
    })

    expect(screen.queryByTestId("rating-levels-basis")).not.toBeInTheDocument()
    expect(screen.getByRole("rowheader", { name: "high" })).toBeInTheDocument()
  })

  it("says what the server said when the levels cannot be read", async () => {
    // The client puts "HTTP 422" in `message` and the server's explanation in
    // `detail`, so showing `message` would tell the user nothing.
    const failure = new ApiError("HTTP 422", 422)
    failure.detail = "Column 'region' is not in the data this node reads."
    mockGetRatingLevels.mockRejectedValue(failure)

    renderEditor(editor())

    await act(async () => {
      vi.advanceTimersByTime(300)
    })

    const basis = await screen.findByTestId("rating-levels-basis")
    await waitFor(() => expect(basis).toHaveTextContent("is not in the data this node reads"))
    expect(basis).not.toHaveTextContent("HTTP 422")
  })

  it("abandons a request the editor has already superseded", async () => {
    const signals: AbortSignal[] = []
    mockGetRatingLevels.mockImplementation((args: { signal?: AbortSignal }) => {
      if (args.signal) signals.push(args.signal)
      return new Promise<RatingLevelsResponse>(() => {})
    })

    const { rerender } = renderEditor(editor())

    await act(async () => {
      vi.advanceTimersByTime(300)
    })
    await waitFor(() => expect(signals).toHaveLength(1))

    rerender(
      <GraphProvider allNodes={[sourceNode, ratingNode] as never} edges={edges} submodels={{}} preamble="">
        {editor({
          config: {
            tables: [
              { factors: ["cover"], outputColumn: "region_factor", defaultValue: "1.0", entries: [] },
            ],
          },
        })}
      </GraphProvider>,
    )

    await act(async () => {
      vi.advanceTimersByTime(300)
    })

    await waitFor(() => expect(signals).toHaveLength(2))
    expect(signals[0].aborted).toBe(true)
    expect(signals[1].aborted).toBe(false)
  })
})
