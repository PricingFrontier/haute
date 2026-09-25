/**
 * The Banding editor over the whole dataset (RAT-B02).
 *
 * The editor used to count from preview rows with its own implementation of the
 * banding rules. These pin what it does now: it asks the server about the
 * factor being edited when the point it reads is cached, says plainly which
 * rows its numbers describe, and never shows full-data counts for data that has
 * moved on.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import type { ReactElement } from "react"

import BandingEditor from "../../panels/editors/BandingEditor"
import { GraphProvider } from "../../panels/GraphContext"
import useNodeDataStore from "../../stores/useNodeDataStore"
import useSettingsStore from "../../stores/useSettingsStore"
import type { BandingStatsResponse, NodeDataPointResponse } from "../../api/types"

vi.mock("../../panels/editors/banding/BandingHistogram", () => ({
  BandingHistogram: (props: Record<string, unknown>) => {
    const bins = props.bins as { lower: number; upper: number }[]
    const format = (props.formatValue as ((value: number) => string) | undefined) ?? String
    return (
      <div
        data-testid="banding-histogram"
        data-bins={JSON.stringify(props.bins)}
        data-boundaries={JSON.stringify(props.boundaries)}
        data-end-labels={JSON.stringify([format(bins[0].lower), format(bins[bins.length - 1].upper)])}
      />
    )
  },
}))

vi.mock("../../panels/editors/banding/CategoricalValuePicker", () => ({
  CategoricalValuePicker: (props: Record<string, unknown>) => (
    <div data-testid="categorical-value-picker" data-values={JSON.stringify(props.availableValues)} />
  ),
}))

const mockGetBandingStats = vi.fn()
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
  getBandingStats: (...args: unknown[]) => mockGetBandingStats(...args),
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

const bandingNode = {
  id: "banding_1",
  type: "banding",
  data: { label: "Banding", description: "", nodeType: "banding", config: {} },
}
const sourceNode = {
  id: "source_1",
  type: "dataInput",
  data: { label: "source", description: "", nodeType: "dataInput", config: {} },
}
const edges = [{ id: "e1", source: "source_1", target: "banding_1" }]

function point(
  state: NodeDataPointResponse["state"],
  dataVersion: string | null,
): NodeDataPointResponse {
  return {
    consumer_node_id: "banding_1",
    point: { producer_node_id: "source_1", port_label: null },
    slot_key: SLOT_KEY,
    // A Banding node behind a transform: its data is built, so it can be
    // stale and there is a control to put that right.
    kind: "node_output",
    state,
    demand: ["premium"],
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

function stats(overrides: Partial<BandingStatsResponse> = {}): BandingStatsResponse {
  return {
    status: "ok",
    point: point("current", DATA_VERSION),
    data_version: DATA_VERSION,
    total_rows: 1000,
    null_count: 0,
    non_finite_count: 0,
    minimum: 0,
    maximum: 100,
    bins: [{ lower: 0, upper: 100, count: 1000 }],
    values: [],
    distinct_count: null,
    other_count: null,
    rule_counts: [750, 250],
    unmatched_count: 0,
    ...overrides,
  }
}

const CONFIG = {
  factors: [
    {
      banding: "breakpoints",
      column: "premium",
      outputColumn: "premium_band",
      rules: [
        { boundary: "50", label: "low" },
        { boundary: "", label: "high" },
      ],
      rightClosed: true,
    },
  ],
}

/** Preview rows, so the sample fallback has something to work from. */
const PREVIEW_ROWS = [{ premium: 10 }, { premium: 60 }, { premium: 90 }]

let rerenderEditor: ((nodes: unknown[]) => void) | null = null

function renderEditor(ui: ReactElement) {
  const result = render(
    <GraphProvider allNodes={[sourceNode, bandingNode]} edges={edges} submodels={{}} preamble="">
      {ui}
    </GraphProvider>,
  )
  rerenderEditor = (nodes: unknown[]) =>
    result.rerender(
      <GraphProvider allNodes={nodes as never} edges={edges} submodels={{}} preamble="">
        {ui}
      </GraphProvider>,
    )
  return result
}

/** Re-render the same editor over a changed graph. */
function rerenderWith(nodes: unknown[]) {
  rerenderEditor?.(nodes)
}

function editor(props: Record<string, unknown> = {}) {
  return (
    <BandingEditor
      config={CONFIG}
      onUpdate={vi.fn()}
      inputSources={[]}
      accentColor="#22d3ee"
      previewRows={PREVIEW_ROWS}
      nodeId="banding_1"
      {...props}
    />
  )
}

describe("BandingEditor statistics", () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    useNodeDataStore.getState().reset()
    useSettingsStore.setState({ activeSource: "live" })
    mockGetBandingStats.mockReset()
    mockGetNodeDataPoint.mockReset()
    mockGetNodeDataPoint.mockResolvedValue(point("current", DATA_VERSION))
    mockGetBandingStats.mockResolvedValue(stats())
  })

  afterEach(() => {
    vi.useRealTimers()
    cleanup()
  })

  it("counts the whole dataset once the point it reads is cached", async () => {
    renderEditor(editor())

    await act(async () => {
      vi.advanceTimersByTime(300)
    })

    await waitFor(() => expect(mockGetBandingStats).toHaveBeenCalled())
    expect(mockGetBandingStats.mock.calls[0][0]).toMatchObject({
      node_id: "banding_1",
      source: "live",
      factor: { column: "premium", banding: "breakpoints" },
    })
    expect(await screen.findByText("All rows · 1,000")).toBeInTheDocument()

    // The label is not the point: the counts and the distribution on screen
    // are the server's, not the three preview rows'.
    await waitFor(() =>
      expect(screen.getByTestId("banding-histogram").getAttribute("data-bins")).toBe(
        JSON.stringify([{ lower: 0, upper: 100, count: 1000 }]),
      ),
    )
    // The open-ended band's "Up to" is blank, which is not a boundary at 0.
    expect(screen.getByTestId("banding-histogram").getAttribute("data-boundaries")).toBe("[50]")
    const counts = Array.from(document.querySelectorAll("td"))
      .map((cell) => cell.textContent?.trim() ?? "")
      .filter((text) => /^\d+$/.test(text))
    expect(counts.slice(0, 2)).toEqual(["750", "250"])
  })

  it("offers Generate a Date column's whole-data range, measured in day numbers, as dates", async () => {
    // 2024-01-01 to 18:00 on 2024-03-01, in days since 1970-01-01.
    mockGetBandingStats.mockResolvedValue(
      stats({ minimum: 19723, maximum: 19783.75, bins: [{ lower: 19723, upper: 19783.75, count: 1000 }] }),
    )
    const config = {
      factors: [
        {
          banding: "breakpoints",
          column: "start_date",
          outputColumn: "start_band",
          rules: [
            { boundary: "2024-01-31", label: "January" },
            { boundary: "", label: "Later" },
          ],
          rightClosed: true,
        },
      ],
    }
    renderEditor(
      editor({ config, previewRows: [{ start_date: "2024-01-15" }], upstreamColumns: [{ name: "start_date", dtype: "Date" }] }),
    )

    await act(async () => {
      vi.advanceTimersByTime(300)
    })
    expect(await screen.findByText("All rows · 1,000")).toBeInTheDocument()

    fireEvent.click(screen.getByRole("button", { name: "Generate" }))
    expect(screen.getByLabelText("Start")).toHaveValue("2024-01-01")
    expect(screen.getByLabelText("End")).toHaveValue("2024-03-01")
  })

  it("lists the categorical values the data has, not the sample's", async () => {
    mockGetBandingStats.mockResolvedValue(
      stats({
        bins: [],
        minimum: null,
        maximum: null,
        values: [
          { value: "north", count: 900 },
          { value: "south", count: 100 },
        ],
        distinct_count: 2,
        other_count: 0,
        rule_counts: [],
        unmatched_count: null,
      }),
    )
    const categorical = {
      factors: [
        {
          banding: "categorical",
          column: "premium",
          outputColumn: "premium_band",
          rules: [],
        },
      ],
    }

    renderEditor(editor({ config: categorical }))
    await act(async () => {
      vi.advanceTimersByTime(300)
    })

    await waitFor(() =>
      expect(screen.getByTestId("categorical-value-picker").getAttribute("data-values")).toBe(
        JSON.stringify([
          { value: "north", count: 900 },
          { value: "south", count: 100 },
        ]),
      ),
    )
  })

  it("asks once for a burst of edits, and only about the last of them", async () => {
    const { rerender } = renderEditor(editor())
    await act(async () => {
      vi.advanceTimersByTime(300)
    })
    await waitFor(() => expect(mockGetBandingStats).toHaveBeenCalledTimes(1))
    mockGetBandingStats.mockClear()

    // Three edits inside the debounce window are one question about the last.
    for (const boundary of ["10", "20", "30"]) {
      const next = {
        factors: [
          { ...CONFIG.factors[0], rules: [{ boundary, label: "low" }, { boundary: "", label: "high" }] },
        ],
      }
      rerender(
        <GraphProvider allNodes={[sourceNode, bandingNode]} edges={edges} submodels={{}} preamble="">
          {editor({ config: next })}
        </GraphProvider>,
      )
      await act(async () => {
        vi.advanceTimersByTime(100)
      })
    }
    await act(async () => {
      vi.advanceTimersByTime(300)
    })

    await waitFor(() => expect(mockGetBandingStats).toHaveBeenCalledTimes(1))
    expect(mockGetBandingStats.mock.calls[0][0].factor.rules[0]).toMatchObject({ boundary: "30" })
  })

  it("shows no numbers, and says to Refresh, when nothing is cached", async () => {
    mockGetNodeDataPoint.mockResolvedValue(point("missing", null))

    renderEditor(editor())
    await act(async () => {
      vi.advanceTimersByTime(300)
    })

    expect(await screen.findByText("Not cached · Refresh this node to count all rows")).toBeInTheDocument()
    expect(screen.queryByText(/^Sample/)).toBeNull()
    expect(screen.queryByTestId("banding-histogram")).toBeNull()
    const cells = Array.from(document.querySelectorAll("td")).map((cell) => cell.textContent?.trim() ?? "")
    expect(cells.filter((text) => /^\d+$/.test(text) || text === "…")).toEqual([])
    expect(mockGetBandingStats).not.toHaveBeenCalled()
  })

  it("never shows full-data counts for data that has moved on", async () => {
    mockGetNodeDataPoint.mockResolvedValue(point("stale", DATA_VERSION))

    renderEditor(editor())
    await act(async () => {
      vi.advanceTimersByTime(300)
    })

    expect(
      await screen.findByText("Cached data is out of date · Refresh this node to count all rows"),
    ).toBeInTheDocument()
    expect(mockGetBandingStats).not.toHaveBeenCalled()
  })

  it("shows counts as pending, and no histogram or total, while the whole dataset is counted", async () => {
    mockGetBandingStats.mockImplementation(() => new Promise(() => {}))
    renderEditor(editor())
    await act(async () => {
      vi.advanceTimersByTime(300)
    })

    await waitFor(() => expect(mockGetBandingStats).toHaveBeenCalled())
    expect(screen.getByText("Counting…")).toBeInTheDocument()
    const cells = Array.from(document.querySelectorAll("td")).map((cell) => cell.textContent?.trim() ?? "")
    expect(cells.filter((text) => text === "…")).toHaveLength(2)
    expect(cells.filter((text) => /^\d+$/.test(text))).toEqual([])
    expect(screen.queryByTestId("banding-histogram")).toBeNull()
    expect(document.body.textContent).not.toMatch(/of \d+ rows/)
  })

  it("draws a date column's whole-data distribution in dates", async () => {
    // 2024-01-01 is day 19723 counted from 1970-01-01; 2024-03-01 is 60 days on.
    mockGetBandingStats.mockResolvedValue(
      stats({
        minimum: 19723,
        maximum: 19783,
        bins: [
          { lower: 19723, upper: 19753, count: 2 },
          { lower: 19753, upper: 19783, count: 1 },
        ],
        rule_counts: [2, 1, 0],
      }),
    )
    const dates = {
      factors: [
        {
          banding: "breakpoints",
          column: "start_date",
          outputColumn: "start_band",
          rules: [
            { boundary: "2024-01-31", label: "January" },
            { boundary: "2024-02-29", label: "February" },
            { boundary: "", label: "Later" },
          ],
          rightClosed: true,
        },
      ],
    }
    renderEditor(editor({ config: dates, upstreamColumns: [{ name: "start_date", dtype: "Date" }] }))
    await act(async () => {
      vi.advanceTimersByTime(300)
    })

    const histogram = await screen.findByTestId("banding-histogram")
    expect(JSON.parse(histogram.getAttribute("data-end-labels")!)).toEqual(["2024-01-01", "2024-03-01"])
    // "Up to 2024-01-31" holds all of the 31st, so its band ends where February starts.
    expect(JSON.parse(histogram.getAttribute("data-boundaries")!)).toEqual([19754, 19783])
  })

  it("says whose rows it counts without a cache status of its own", async () => {
    renderEditor(editor())
    await act(async () => {
      vi.advanceTimersByTime(300)
    })

    expect(await screen.findByText("All rows · 1,000")).toBeInTheDocument()
    // The node's own Refresh caches its data; the editor shows no cache control.
    expect(screen.queryByTestId("data-cache-status")).toBeNull()
    expect(screen.queryByText("Cached")).toBeNull()
  })

  it("says why the whole dataset could not be counted, in the server's words", async () => {
    // The client builds an ApiError whose `message` is only "HTTP 422"; what
    // the user needs is the detail the server sent — for bad rules, the message
    // execution itself would give.
    const failure = new ApiError("HTTP 422", 422)
    failure.detail = "Breakpoint has non-numeric boundary 'ten'"
    mockGetBandingStats.mockRejectedValue(failure)

    renderEditor(editor())
    await act(async () => {
      vi.advanceTimersByTime(300)
    })

    await waitFor(() => expect(mockGetBandingStats).toHaveBeenCalled())
    expect(
      await screen.findByText(/non-numeric boundary 'ten'/),
    ).toBeVisible()
    expect(screen.queryByText(/HTTP 422/)).toBeNull()
  })

  it("abandons the answer to a question the editor has moved on from", async () => {
    const aborted: boolean[] = []
    mockGetBandingStats.mockImplementation(
      (args: { signal?: AbortSignal }) =>
        new Promise((resolve, reject) => {
          args.signal?.addEventListener("abort", () => {
            aborted.push(true)
            reject(new DOMException("Aborted", "AbortError"))
          })
          setTimeout(() => resolve(stats()), 5_000)
        }),
    )

    const { rerender } = renderEditor(editor())
    await act(async () => {
      vi.advanceTimersByTime(300)
    })
    await waitFor(() => expect(mockGetBandingStats).toHaveBeenCalledTimes(1))

    const next = {
      factors: [
        { ...CONFIG.factors[0], rules: [{ boundary: "99", label: "low" }] },
      ],
    }
    rerender(
      <GraphProvider allNodes={[sourceNode, bandingNode]} edges={edges} submodels={{}} preamble="">
        {editor({ config: next })}
      </GraphProvider>,
    )
    await act(async () => {
      vi.advanceTimersByTime(300)
    })

    // The superseded request is cancelled rather than left to answer about a
    // factor the user has already changed.
    await waitFor(() => expect(aborted).toHaveLength(1))
    expect(mockGetBandingStats).toHaveBeenCalledTimes(2)
  })

  it("keeps the whole dataset on screen while a categorical edit is counted", async () => {
    const categoricalStats = stats({
      bins: [],
      minimum: null,
      maximum: null,
      values: [
        { value: "north", count: 900 },
        { value: "south", count: 100 },
      ],
      distinct_count: 2,
      other_count: 0,
      rule_counts: [900],
      unmatched_count: 100,
    })
    mockGetBandingStats.mockResolvedValue(categoricalStats)
    const categorical = (rules: { value: string; assignment: string }[]) => ({
      factors: [{ banding: "categorical", column: "premium", outputColumn: "premium_band", rules }],
    })
    const { rerender } = renderEditor(editor({ config: categorical([{ value: "north", assignment: "N" }]) }))
    await act(async () => {
      vi.advanceTimersByTime(300)
    })
    expect(await screen.findByText("All rows · 1,000")).toBeInTheDocument()

    // The answer to the edit is slow; until it comes, the editor must not drop
    // back to the three-row sample.
    mockGetBandingStats.mockImplementation(() => new Promise(() => {}))
    rerender(
      <GraphProvider allNodes={[sourceNode, bandingNode]} edges={edges} submodels={{}} preamble="">
        {editor({
          config: categorical([
            { value: "north", assignment: "N" },
            { value: "south", assignment: "S" },
          ]),
        })}
      </GraphProvider>,
    )
    await act(async () => {
      vi.advanceTimersByTime(300)
    })

    await waitFor(() => expect(mockGetBandingStats).toHaveBeenCalledTimes(2))
    expect(screen.getByText("All rows · 1,000")).toBeInTheDocument()
    expect(screen.queryByText(/^Sample/)).toBeNull()
    // Every value's count is known, so the new rule's is too: no waiting.
    const counts = Array.from(document.querySelectorAll("td"))
      .map((cell) => cell.textContent?.trim() ?? "")
      .filter((text) => /^\d+$/.test(text))
    expect(counts).toEqual(["900", "100"])
    expect(screen.getByText("0 of 1000 rows")).toBeInTheDocument()
  })

  it("does not re-check the data it reads when only its rules change", async () => {
    // In the app the edit lands in the graph as well as the editor's config.
    const withRules = (boundary: string) => {
      const config = {
        factors: [
          { ...CONFIG.factors[0], rules: [{ boundary, label: "low" }, { boundary: "", label: "high" }] },
        ],
      }
      return { config, node: { ...bandingNode, data: { ...bandingNode.data, config } } }
    }
    const first = withRules("50")
    const { rerender } = render(
      <GraphProvider allNodes={[sourceNode, first.node]} edges={edges} submodels={{}} preamble="">
        {editor({ config: first.config })}
      </GraphProvider>,
    )
    await act(async () => {
      vi.advanceTimersByTime(300)
    })
    expect(await screen.findByText("All rows · 1,000")).toBeInTheDocument()
    const pointChecks = mockGetNodeDataPoint.mock.calls.length

    const edited = withRules("40")
    mockGetBandingStats.mockImplementation(() => new Promise(() => {}))
    rerender(
      <GraphProvider allNodes={[sourceNode, edited.node]} edges={edges} submodels={{}} preamble="">
        {editor({ config: edited.config })}
      </GraphProvider>,
    )
    expect(screen.getByText("All rows · 1,000")).toBeInTheDocument()
    await act(async () => {
      vi.advanceTimersByTime(300)
    })
    expect(screen.getByText("All rows · 1,000")).toBeInTheDocument()
    expect(mockGetNodeDataPoint.mock.calls.length).toBe(pointChecks)
  })

  it("shows numeric counts as pending, never the sample's, while an edit is counted", async () => {
    const { rerender } = renderEditor(editor())
    await act(async () => {
      vi.advanceTimersByTime(300)
    })
    expect(await screen.findByText("All rows · 1,000")).toBeInTheDocument()

    mockGetBandingStats.mockImplementation(() => new Promise(() => {}))
    const moved = {
      factors: [
        { ...CONFIG.factors[0], rules: [{ boundary: "40", label: "low" }, { boundary: "", label: "high" }] },
      ],
    }
    rerender(
      <GraphProvider allNodes={[sourceNode, bandingNode]} edges={edges} submodels={{}} preamble="">
        {editor({ config: moved })}
      </GraphProvider>,
    )
    await act(async () => {
      vi.advanceTimersByTime(300)
    })

    await waitFor(() => expect(mockGetBandingStats).toHaveBeenCalledTimes(2))
    expect(screen.getByText("All rows · 1,000")).toBeInTheDocument()
    expect(screen.getByTestId("banding-histogram").getAttribute("data-bins")).toBe(
      JSON.stringify([{ lower: 0, upper: 100, count: 1000 }]),
    )
    const cells = Array.from(document.querySelectorAll("td")).map((cell) => cell.textContent?.trim())
    expect(cells.filter((text) => text === "…")).toHaveLength(2)
    expect(cells.filter((text) => /^\d+$/.test(text ?? ""))).toEqual([])
  })

  it("stops showing whole-dataset counts once an upstream edit leaves them behind", async () => {
    renderEditor(editor())
    await act(async () => {
      vi.advanceTimersByTime(300)
    })
    expect(await screen.findByText("All rows · 1,000")).toBeInTheDocument()

    // An upstream edit changes what this node reads, so the point it asks
    // about is a different question — and the built generation now describes
    // data the graph no longer produces.
    mockGetNodeDataPoint.mockResolvedValue(point("stale", DATA_VERSION))
    const editedSource = {
      ...sourceNode,
      data: { ...sourceNode.data, config: { path: "other.parquet" } },
    }
    await act(async () => {
      rerenderWith([editedSource, bandingNode])
      vi.advanceTimersByTime(300)
    })

    expect(
      await screen.findByText("Cached data is out of date · Refresh this node to count all rows"),
    ).toBeInTheDocument()
    expect(screen.queryByText("All rows · 1,000")).toBeNull()
  })
})
