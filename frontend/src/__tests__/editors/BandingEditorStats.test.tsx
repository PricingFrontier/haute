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

import { runNodeData } from "../../api/client"
import { refreshNodeDataCache } from "../../hooks/useNodeDataCache"
import BandingEditor from "../../panels/editors/BandingEditor"
import { GraphProvider } from "../../panels/GraphContext"
import useNodeDataStore from "../../stores/useNodeDataStore"
import useSettingsStore from "../../stores/useSettingsStore"
import useWorkbenchStore from "../../stores/useWorkbenchStore"
import useGraphStore from "../../stores/useGraphStore"
import type { BandingStatsResponse, NodeDataPointResponse } from "../../api/types"
import type { Node } from "@xyflow/react"
import type { PipelineEdge, SubmodelDefinition } from "../../types/node"
import { toSimpleNode } from "../../hooks/usePanelGraphContext"
import { makeIdentifiedNode } from "../../test-utils/factories"
import { documentRequestScope } from "../../utils/requestGateway"

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
      output_column: "premium_band",
      rules: [
        { boundary: "50", label: "low" },
        { boundary: "", label: "high" },
      ],
      right_closed: true,
    },
  ],
}

/** Preview rows, so the sample fallback has something to work from. */
const PREVIEW_ROWS = [{ premium: 10 }, { premium: 60 }, { premium: 90 }]

let rerenderEditor: ((nodes: (typeof sourceNode)[]) => void) | null = null

/** The canvas nodes as the graph store holds them. */
function storeNodes(nodes: (typeof sourceNode)[]): Node[] {
  return nodes.map((node) => ({ ...node, position: { x: 0, y: 0 } }))
}

/**
 * Render the editor over the pipeline the graph store holds, as App does: a
 * request reads its document from the store, and the panel's provider is handed
 * the same nodes.
 */
function renderEditor(ui: ReactElement) {
  useGraphStore.getState().loadGraphSnapshot({
    nodes: storeNodes([sourceNode, bandingNode]),
    edges: edges as PipelineEdge[],
    preamble: "",
    submodels: {},
  })
  const result = render(
    <GraphProvider requestScope={documentRequestScope} allNodes={[sourceNode, bandingNode]} edges={edges} submodels={{}} preamble="">
      {ui}
    </GraphProvider>,
  )
  rerenderEditor = (nodes) => {
    useGraphStore.getState().setNodes(() => storeNodes(nodes))
    result.rerender(
      <GraphProvider requestScope={documentRequestScope} allNodes={nodes} edges={edges} submodels={{}} preamble="">
        {ui}
      </GraphProvider>,
    )
  }
  return result
}

/** An edit reaching the document, and the same editor re-rendered over it. */
function rerenderWith(nodes: (typeof sourceNode)[]) {
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
    useGraphStore.getState().resetForTests()
    useNodeDataStore.getState().reset()
    useSettingsStore.setState({ activeSource: "live" })
    mockGetBandingStats.mockReset()
    mockGetNodeDataPoint.mockReset()
    vi.mocked(runNodeData).mockReset()
    mockGetNodeDataPoint.mockResolvedValue(point("current", DATA_VERSION))
    mockGetBandingStats.mockResolvedValue(stats())
  })

  afterEach(() => {
    vi.useRealTimers()
    cleanup()
    useWorkbenchStore.setState({ enabled: false })
  })

  it("counts the whole dataset once the point it reads is cached", async () => {
    renderEditor(editor())

    await act(async () => {
      vi.advanceTimersByTime(300)
    })

    await waitFor(() => expect(mockGetBandingStats).toHaveBeenCalled())
    expect(mockGetBandingStats.mock.calls[0][0]).toMatchObject({
      // The pipeline the editor holds.
      graph: {
        nodes: [{ id: "source_1" }, { id: "banding_1" }],
        edges: [{ id: "e1", source: "source_1", target: "banding_1" }],
      },
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
          output_column: "start_band",
          rules: [
            { boundary: "2024-01-31", label: "January" },
            { boundary: "", label: "Later" },
          ],
          right_closed: true,
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
          output_column: "premium_band",
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
        <GraphProvider requestScope={documentRequestScope} allNodes={[sourceNode, bandingNode]} edges={edges} submodels={{}} preamble="">
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

    expect(await screen.findByText("Not cached · Refresh this node to read all rows")).toBeInTheDocument()
    expect(screen.queryByText(/^Sample/)).toBeNull()
    expect(screen.queryByTestId("banding-histogram")).toBeNull()
    const cells = Array.from(document.querySelectorAll("td")).map((cell) => cell.textContent?.trim() ?? "")
    expect(cells.filter((text) => /^\d+$/.test(text) || text === "…")).toEqual([])
    expect(mockGetBandingStats).not.toHaveBeenCalled()
  })

  it("says to Refresh, rather than counting on, when the server finds the data needs caching again", async () => {
    // The point looked current, but its cache went (evicted, say) before the question arrived.
    mockGetBandingStats.mockResolvedValue(stats({ status: "cache_required" }))

    renderEditor(editor())
    await act(async () => {
      vi.advanceTimersByTime(300)
    })

    await waitFor(() => expect(mockGetBandingStats).toHaveBeenCalled())
    expect(await screen.findByText("Not cached · Refresh this node to read all rows")).toBeInTheDocument()
    expect(screen.queryByText("Counting…")).toBeNull()
    const cells = Array.from(document.querySelectorAll("td")).map((cell) => cell.textContent?.trim() ?? "")
    expect(cells.filter((text) => /^\d+$/.test(text) || text === "…")).toEqual([])
  })

  it("re-reads its data point when the server finds the data gone, so Refresh caches it again", async () => {
    // The snapshot went between reading the point and asking for counts.
    mockGetBandingStats.mockImplementationOnce(async () => {
      mockGetNodeDataPoint.mockResolvedValue(point("missing", null))
      return stats({ status: "cache_required" })
    })
    vi.mocked(runNodeData).mockImplementation(async () => {
      mockGetNodeDataPoint.mockResolvedValue(point("current", DATA_VERSION))
      return { status: "completed", job_id: null, cached: false, message: "", point: point("current", DATA_VERSION) } as never
    })

    renderEditor(editor())
    await act(async () => {
      vi.advanceTimersByTime(300)
    })
    expect(await screen.findByText("Not cached · Refresh this node to read all rows")).toBeInTheDocument()
    await waitFor(() => expect(mockGetNodeDataPoint.mock.calls.length).toBeGreaterThan(1))

    await act(async () => {
      refreshNodeDataCache("banding_1")
    })
    expect(runNodeData).toHaveBeenCalledWith(expect.objectContaining({ node_id: "banding_1", refresh: false }))
    await act(async () => {
      vi.advanceTimersByTime(300)
    })
    expect(await screen.findByText("All rows · 1,000")).toBeInTheDocument()
  })

  it("never shows full-data counts for data that has moved on", async () => {
    mockGetNodeDataPoint.mockResolvedValue(point("stale", DATA_VERSION))

    renderEditor(editor())
    await act(async () => {
      vi.advanceTimersByTime(300)
    })

    expect(
      await screen.findByText("Cached data is out of date · Refresh this node to read all rows"),
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
          output_column: "start_band",
          rules: [
            { boundary: "2024-01-31", label: "January" },
            { boundary: "2024-02-29", label: "February" },
            { boundary: "", label: "Later" },
          ],
          right_closed: true,
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
      <GraphProvider requestScope={documentRequestScope} allNodes={[sourceNode, bandingNode]} edges={edges} submodels={{}} preamble="">
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
      factors: [{ banding: "categorical", column: "premium", output_column: "premium_band", rules }],
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
      <GraphProvider requestScope={documentRequestScope} allNodes={[sourceNode, bandingNode]} edges={edges} submodels={{}} preamble="">
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
      <GraphProvider requestScope={documentRequestScope} allNodes={[sourceNode, first.node]} edges={edges} submodels={{}} preamble="">
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
      <GraphProvider requestScope={documentRequestScope} allNodes={[sourceNode, edited.node]} edges={edges} submodels={{}} preamble="">
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
      <GraphProvider requestScope={documentRequestScope} allNodes={[sourceNode, bandingNode]} edges={edges} submodels={{}} preamble="">
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
      await screen.findByText("Cached data is out of date · Refresh this node to read all rows"),
    ).toBeInTheDocument()
    expect(screen.queryByText("All rows · 1,000")).toBeNull()
    // The point it asks about now is the edited pipeline's.
    expect(mockGetNodeDataPoint.mock.lastCall?.[0].graph.nodes[0]).toMatchObject({
      id: "source_1",
      data: { config: { path: "other.parquet" } },
    })
  })

  it("asks nothing and shows no counts in a workbench project, which bands the quote being priced", async () => {
    useWorkbenchStore.setState({ enabled: true })
    renderEditor(editor())
    await act(async () => {
      vi.advanceTimersByTime(1000)
    })

    expect(mockGetNodeDataPoint).not.toHaveBeenCalled()
    expect(mockGetBandingStats).not.toHaveBeenCalled()
    expect(screen.queryByTestId("banding-data-basis")).toBeNull()
    expect(screen.queryByRole("columnheader", { name: "Matches" })).toBeNull()
    expect(screen.queryByText(/of \d+ rows/)).toBeNull()
    expect(screen.queryByTestId("banding-histogram")).toBeNull()
    // The rules are still there to edit.
    expect(screen.getByRole("textbox", { name: "Breakpoint 1 label" })).toHaveValue("low")
  })

  it("says why there is nothing to count when the server finds no data for the node to read", async () => {
    const message = "Node 'Banding' must have exactly one incoming connection to read its data (found 0)."
    mockGetNodeDataPoint.mockRejectedValue(Object.assign(new ApiError(message, 400), { detail: message }))
    renderEditor(editor())
    await act(async () => {
      vi.advanceTimersByTime(1000)
    })

    expect(await screen.findByText(message)).toBeInTheDocument()
    expect(screen.queryByText("Counting…")).toBeNull()
    expect(mockGetBandingStats).not.toHaveBeenCalled()
  })

  it("says reading the data failed, never Counting…, when its data point cannot be read", async () => {
    mockGetNodeDataPoint.mockRejectedValue(
      Object.assign(new ApiError("HTTP 409", 409), { detail: "The workbench is off" }),
    )
    renderEditor(editor())
    await act(async () => {
      vi.advanceTimersByTime(1000)
    })

    expect(await screen.findByText("Couldn't read the data: The workbench is off")).toBeInTheDocument()
    expect(screen.queryByText("Counting…")).toBeNull()
    expect(mockGetBandingStats).not.toHaveBeenCalled()
  })

  it("asks about a node of an open submodel with the whole pipeline and the node's runtime id", async () => {
    // The drilled view beside its own definition names each node twice, which
    // the server refuses; Preview sends the whole pipeline instead, and so does this.
    const definition: SubmodelDefinition = {
      definitionId: "br_rating",
      file: "modules/br_rating.py",
      graph: {
        nodes: [makeIdentifiedNode("banding_1", "banding", { data: { label: "Banding", nodeType: "banding", config: CONFIG } })],
        edges: [],
      },
      inputPorts: [{ name: "policy", targets: [{ nodeId: "banding_1", handleId: null }] }],
      outputPorts: [],
    }
    useGraphStore.getState().loadGraphSnapshot({
      nodes: [
        makeIdentifiedNode("source_1", "dataInput"),
        makeIdentifiedNode("br_rating", "submodel", {
          data: { label: "br_rating", nodeType: "submodel", config: { definition_id: "br_rating", alias: "br_rating" } },
        }),
      ],
      edges: [{ id: "feed", source: "source_1", target: "br_rating", targetHandle: "in__policy" }],
      preamble: "",
      submodels: { br_rating: definition },
    })
    useGraphStore.getState().enterSubmodelView({ instanceId: "br_rating", definitionId: "br_rating" })
    // The canvas App hands the panel: the open submodel's view.
    const view = useGraphStore.getState().nodes.map(toSimpleNode)
    render(
      <GraphProvider requestScope={documentRequestScope} allNodes={view} edges={useGraphStore.getState().edges} submodels={{}} preamble="">
        {editor()}
      </GraphProvider>,
    )
    await act(async () => {
      vi.advanceTimersByTime(300)
    })

    await waitFor(() => expect(mockGetBandingStats).toHaveBeenCalled())
    // The pipeline's own nodes and edges, not the view's, with the definition.
    const whole = {
      nodes: [{ id: "source_1" }, { id: "br_rating" }],
      edges: [{ id: "feed", target: "br_rating" }],
      submodels: { br_rating: { graph: { nodes: [{ id: "banding_1" }] } } },
    }
    expect(mockGetNodeDataPoint.mock.calls[0][0].graph).toMatchObject(whole)
    expect(mockGetNodeDataPoint.mock.calls[0][0].node_id).toBe("submodel_runtime/br_rating/banding_1")
    expect(mockGetBandingStats.mock.calls[0][0].graph).toMatchObject(whole)
    expect(mockGetBandingStats.mock.calls[0][0].node_id).toBe("submodel_runtime/br_rating/banding_1")
    expect(await screen.findByText("All rows · 1,000")).toBeInTheDocument()
  })
})
