/**
 * The pipeline settings pane (preview rows, the project's pipeline settings and
 * the cache inventory), per `specs/frontend-shared/low-level.md`.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { render, screen, fireEvent, cleanup, waitFor, act } from "@testing-library/react"

const mockFetchCacheNodes = vi.fn()

const mockClearCacheIdentities = vi.fn()

const mockGetPipelineSettings = vi.fn()
const mockPatchPipelineSettings = vi.fn()

vi.mock("../../api/client", () => ({
  fetchCacheNodes: (...args: unknown[]) => mockFetchCacheNodes(...args),
  fetchCacheUsage: () => Promise.resolve({ schema_version: 1, total_bytes: 5 * 1024 ** 3, automatic_bytes: 0, automatic_budget_bytes: 1 }),
  clearCacheIdentities: (...args: unknown[]) => mockClearCacheIdentities(...args),
  getPipelineSettings: (...args: unknown[]) => mockGetPipelineSettings(...args),
  patchPipelineSettings: (...args: unknown[]) => mockPatchPipelineSettings(...args),
}))

import PipelineSettingsModal from "../PipelineSettingsModal"
import type {
  CacheNodesResponse,
  PipelineSettingsResponse,
  PipelineSettingsValues,
} from "../../api/types"
import useGraphStore from "../../stores/useGraphStore"
import useSettingsStore from "../../stores/useSettingsStore"
import useToastStore from "../../stores/useToastStore"
import useUIStore from "../../stores/useUIStore"

const GIB = 1024 * 1024 * 1024

const AUTOMATIC: PipelineSettingsResponse["automatic"] = {
  chunk_rows: 500_000,
  caching: true,
  cache_size_gb: 20,
  preview_memory_gb: 10.31,
  kept_free_gb: 2,
  pipeline_time_limit_minutes: 30,
  modelling_time_limit_minutes: 60,
  optimisation_time_limit_minutes: null,
}

const NOTHING_SET: PipelineSettingsValues = {
  chunk_rows: null,
  caching: null,
  cache_size_gb: null,
  preview_memory_gb: null,
  kept_free_gb: null,
  pipeline_time_limit_minutes: null,
  modelling_time_limit_minutes: null,
  optimisation_time_limit_minutes: null,
}

/** The settings file the mocked server holds; PATCH merges into it. */
let serverSettings: PipelineSettingsValues = NOTHING_SET

function settingsResponse(settings: PipelineSettingsValues = serverSettings): PipelineSettingsResponse {
  return { path: ".haute/pipeline-settings.json", automatic: AUTOMATIC, settings }
}

function serveSettings(settings: Partial<PipelineSettingsValues> = {}) {
  serverSettings = { ...NOTHING_SET, ...settings }
  mockGetPipelineSettings.mockReset()
  mockGetPipelineSettings.mockImplementation(async () => settingsResponse())
  mockPatchPipelineSettings.mockReset()
  mockPatchPipelineSettings.mockImplementation(async (changes: Partial<PipelineSettingsValues>) => {
    serverSettings = { ...serverSettings, ...changes }
    return settingsResponse()
  })
  useSettingsStore.setState({
    pipelineSettings: null,
    pipelineSettingsError: null,
    _confirmedPipelineSettings: null,
    _pendingPipelineSettings: {},
  })
}

function nodeEntry(overrides: Partial<CacheNodesResponse["nodes"][number]> = {}) {
  return {
    node_id: "join",
    kind: "node_output" as const,
    state: "current" as const,
    reads_directly: false,
    reads_from: null,
    shares_snapshot_with: [] as string[],
    row_count: 1_200_000,
    generations: 1,
    size_bytes: 890 * 1024 * 1024,
    newest_created_at: 1_700_000_000,
    build_seconds: 9.9,
    identity_digests: ["digest-join"],
    retention: "automatic" as const,
    unavailable_reason: null,
    ...overrides,
  }
}

function nodes(overrides: Partial<CacheNodesResponse> = {}): CacheNodesResponse {
  return {
    schema_version: 1,
    source: "live",
    nodes: [nodeEntry(), nodeEntry({
        node_id: "source",
        state: "missing",
        row_count: null,
        generations: 0,
        size_bytes: 0,
        newest_created_at: null,
        build_seconds: null,
        // A row that carries nothing names no identities, as the server guarantees.
        identity_digests: [],
      })],
    other: [],
    unattributed_generations: 0,
    unattributed_bytes: 0,
    unmarked_identities: 0,
    ...overrides,
  }
}

describe("PipelineSettingsModal", () => {
  beforeEach(() => {
    mockFetchCacheNodes.mockReset()
    mockFetchCacheNodes.mockResolvedValue(nodes())
    mockClearCacheIdentities.mockReset()
    mockClearCacheIdentities.mockResolvedValue({ schema_version: 1, cleared: [], freed_bytes: 0 })
    serveSettings()
    useGraphStore.setState({ nodes: [], edges: [], preamble: "", submodels: {} })
  })

  afterEach(cleanup)

  it("shows cached inventory without budgets", async () => {
    render(<PipelineSettingsModal onClose={vi.fn()} />)

    expect(await screen.findByRole("heading", { name: "Pipeline settings" })).toBeInTheDocument()
    expect(screen.getByRole("heading", { name: "Cached data" })).toBeInTheDocument()
    expect(screen.getByTestId("cache-node-join")).toHaveTextContent("890 MB")
    expect(screen.getByTestId("cache-node-join")).toHaveTextContent("Cached")
    expect(screen.getByTestId("cache-node-join-clear")).toBeInTheDocument()
    expect(screen.queryAllByRole("meter")).toHaveLength(0)
    expect(screen.queryByText(/This pipeline ·/)).toBeNull()
    expect(screen.queryByText(/HAUTE_|budget|cap/i)).toBeNull()
  })

  it("shows the whole store's size beside the cached-data heading", async () => {
    render(<PipelineSettingsModal onClose={vi.fn()} />)

    expect(await screen.findByTestId("cache-store-size")).toHaveTextContent("5.0 GB cached")
  })

  it("reads the inventory once on open and again only when asked", async () => {
    // Not polling is the package's design constraint — the endpoint costs what
    // admitting a capture costs — so the test has to let plenty of time pass
    // and prove nothing fired. Without the clock, a `setInterval(refresh, …)`
    // added to the pane would pass this test.
    vi.useFakeTimers({ shouldAdvanceTime: true })
    try {
      render(<PipelineSettingsModal onClose={vi.fn()} />)
      await screen.findByTestId("cache-node-join")
      expect(mockFetchCacheNodes).toHaveBeenCalledTimes(1)

      await vi.advanceTimersByTimeAsync(10 * 60 * 1000)
      expect(mockFetchCacheNodes).toHaveBeenCalledTimes(1)

      fireEvent.click(screen.getByTestId("cache-usage-refresh"))
      await waitFor(() => expect(mockFetchCacheNodes).toHaveBeenCalledTimes(2))

      await vi.advanceTimersByTimeAsync(10 * 60 * 1000)
      expect(mockFetchCacheNodes).toHaveBeenCalledTimes(2)
    } finally {
      vi.useRealTimers()
    }
  })

  it("does not re-read when the graph changes under it", async () => {
    // The pane reads the graph at request time rather than subscribing to it.
    // Subscribing would make every edit, resync or load re-issue both requests
    // — each costing the server what admitting a capture costs — which is the
    // polling this surface is specified not to do. The "once on open" test
    // above cannot see this, because it never touches the store.
    render(<PipelineSettingsModal onClose={vi.fn()} />)
    await screen.findByTestId("cache-node-join")
    expect(mockFetchCacheNodes).toHaveBeenCalledTimes(1)

    await act(async () => {
      useGraphStore.setState({ edges: [{ id: "e1", source: "a", target: "b" }] as never })
    })
    await act(async () => {
      useGraphStore.setState({ preamble: "import polars as pl" })
    })

    expect(mockFetchCacheNodes).toHaveBeenCalledTimes(1)
  })

  it("sends the graph as it stands when the read is made", async () => {
    render(<PipelineSettingsModal onClose={vi.fn()} />)
    await screen.findByTestId("cache-node-join")

    await act(async () => {
      useGraphStore.setState({ preamble: "import polars as pl" })
    })
    fireEvent.click(screen.getByTestId("cache-usage-refresh"))
    await waitFor(() => expect(mockFetchCacheNodes).toHaveBeenCalledTimes(2))

    // Not a snapshot taken when the pane opened: the Refresh must report on
    // the graph the user is looking at now.
    const sent = mockFetchCacheNodes.mock.calls[1][0] as { graph: { preamble?: string } }
    expect(sent.graph.preamble).toBe("import polars as pl")
  })

  it("abandons the read in flight when the pane closes", async () => {
    // Including a Refresh's read, which is not the one the opening effect
    // started: whatever is in flight when the pane closes must be abandoned.
    let signal: AbortSignal | undefined
    mockFetchCacheNodes.mockImplementation((_: unknown, options?: { signal?: AbortSignal }) => {
      signal = options?.signal
      return new Promise(() => {})
    })

    const { unmount } = render(<PipelineSettingsModal onClose={vi.fn()} />)
    await waitFor(() => expect(mockFetchCacheNodes).toHaveBeenCalledTimes(1))
    const opening = signal
    expect(opening?.aborted).toBe(false)

    unmount()
    expect(opening?.aborted).toBe(true)
  })

  it("abandons a Refresh's read when the pane closes", async () => {
    const { unmount } = render(<PipelineSettingsModal onClose={vi.fn()} />)
    await screen.findByTestId("cache-node-join")

    let refreshSignal: AbortSignal | undefined
    mockFetchCacheNodes.mockImplementation((_: unknown, options?: { signal?: AbortSignal }) => {
      refreshSignal = options?.signal
      return new Promise(() => {})
    })
    fireEvent.click(screen.getByTestId("cache-usage-refresh"))
    await waitFor(() => expect(refreshSignal).toBeDefined())
    expect(refreshSignal?.aborted).toBe(false)

    unmount()
    expect(refreshSignal?.aborted).toBe(true)
  })

  it("reports a failed read and keeps the last good inventory on screen", async () => {
    render(<PipelineSettingsModal onClose={vi.fn()} />)
    await screen.findByTestId("cache-node-join")

    mockFetchCacheNodes.mockRejectedValue(new Error("store unreadable"))
    fireEvent.click(screen.getByTestId("cache-usage-refresh"))

    expect(await screen.findByTestId("cache-usage-error")).toHaveTextContent("store unreadable")
    expect(screen.getByTestId("cache-node-join")).toHaveTextContent("890 MB")
  })

  it("lists every node of the pipeline with what it holds", async () => {
    render(<PipelineSettingsModal onClose={vi.fn()} />)

    const join = await screen.findByTestId("cache-node-join")
    expect(join).toHaveTextContent("Cached")
    expect(join).toHaveTextContent("890 MB")

    // A node with nothing cached is listed too, rather than being omitted —
    // "not cached" is an answer the user came for.
    const source = screen.getByTestId("cache-node-source")
    expect(source).toHaveTextContent("Not cached")
  })

  it("shows the node's label from the canvas, falling back to its id", async () => {
    useGraphStore.setState({
      nodes: [
        { id: "join", position: { x: 0, y: 0 }, data: { label: "Premium join" } },
      ] as never,
      edges: [],
    })
    render(<PipelineSettingsModal onClose={vi.fn()} />)

    expect(await screen.findByTestId("cache-node-join")).toHaveTextContent("Premium join")
    // No label for this one, so the id stands in rather than an empty cell.
    expect(screen.getByTestId("cache-node-source")).toHaveTextContent("source")
  })

  it("never calls a direct file read 'cached', because nothing is cached", async () => {
    // A Parquet scan is read where it lies. Its point resolves as current, so
    // the state alone would print "Cached" for a node with no cache at all.
    mockFetchCacheNodes.mockResolvedValue(
      nodes({
        nodes: [
          nodeEntry({
            node_id: "quotes",
            kind: "data_input",
            state: "current",
            reads_directly: true,
            generations: 0,
            size_bytes: 0,
          }),
        ],
      }),
    )
    render(<PipelineSettingsModal onClose={vi.fn()} />)

    const row = await screen.findByTestId("cache-node-quotes")
    expect(row).toHaveTextContent("Read directly")
    expect(row).not.toHaveTextContent("Cached")
  })

  it("sends a node that reads elsewhere to the row that holds the data", async () => {
    // Otherwise one join's bytes would be repeated on every consumer's row and
    // the column would sum to more than the store holds.
    useGraphStore.setState({
      nodes: [
        { id: "join", position: { x: 0, y: 0 }, data: { label: "Premium join" } },
      ] as never,
      edges: [],
    })
    mockFetchCacheNodes.mockResolvedValue(
      nodes({
        nodes: [
          nodeEntry({
            node_id: "banding",
            reads_from: "join",
            generations: 0,
            size_bytes: 0,
          }),
        ],
      }),
    )
    render(<PipelineSettingsModal onClose={vi.fn()} />)

    const banding = await screen.findByTestId("cache-node-banding")
    expect(banding).toHaveTextContent("reads Premium join")
    expect(banding).not.toHaveTextContent("MB")
  })

  it("shows cache timing without exposing retained replacement copies", async () => {
    mockFetchCacheNodes.mockResolvedValue(nodes({ nodes: [nodeEntry({ generations: 2 })] }))
    render(<PipelineSettingsModal onClose={vi.fn()} />)

    const join = await screen.findByTestId("cache-node-join")
    expect(join).toHaveTextContent("9.9 s")
    // The date is locale-formatted, so assert the parts that do not vary.
    expect(join.textContent).toMatch(/Nov|14/)
    expect(join).not.toHaveTextContent(/generation/i)
  })

  it("does not claim an instant build when the duration was never recorded", async () => {
    // A store predating the recording has no duration. Absent is not zero.
    mockFetchCacheNodes.mockResolvedValue(
      nodes({ nodes: [nodeEntry({ build_seconds: null, newest_created_at: null })] }),
    )
    render(<PipelineSettingsModal onClose={vi.fn()} />)

    const join = await screen.findByTestId("cache-node-join")
    expect(join).not.toHaveTextContent("0.0 s")
    expect(join.textContent).toContain("-")
  })

  it("clears exactly the identities the row reported, then re-reads", async () => {
    // Re-read rather than patch the row: the server owns the inventory.
    render(<PipelineSettingsModal onClose={vi.fn()} />)
    await screen.findByTestId("cache-node-join")
    expect(mockFetchCacheNodes).toHaveBeenCalledTimes(1)

    fireEvent.click(screen.getByTestId("cache-node-join-clear"))

    await waitFor(() => expect(mockClearCacheIdentities).toHaveBeenCalledWith(["digest-join"]))
    await waitFor(() => expect(mockFetchCacheNodes).toHaveBeenCalledTimes(2))
  })

  it("shows the clear control without needing the row hovered", async () => {
    // It was hover-only once, which meant nobody knew it was there.
    render(<PipelineSettingsModal onClose={vi.fn()} />)

    const clear = await screen.findByTestId("cache-node-join-clear")
    expect(clear.className).not.toContain("opacity-0")
    expect(clear).toBeVisible()
  })

  it("offers no clear on a row that carries nothing", async () => {
    // The control would have nothing to act on, and a row whose bytes are on
    // another row must not appear to own them.
    render(<PipelineSettingsModal onClose={vi.fn()} />)
    await screen.findByTestId("cache-node-source")

    expect(screen.queryByTestId("cache-node-source-clear")).toBeNull()
    expect(screen.getByTestId("cache-node-join-clear")).toBeInTheDocument()
  })

  it("reports a failed clear without dropping what is on screen", async () => {
    mockClearCacheIdentities.mockRejectedValue(new Error("cache is in use"))
    render(<PipelineSettingsModal onClose={vi.fn()} />)
    await screen.findByTestId("cache-node-join")

    fireEvent.click(screen.getByTestId("cache-node-join-clear"))

    expect(await screen.findByTestId("cache-usage-error")).toHaveTextContent("cache is in use")
    expect(screen.getByTestId("cache-node-join")).toHaveTextContent("890 MB")
  })

  it("can clear a row that belongs to no node of this pipeline", async () => {
    // The reason this exists: nothing else in the app can reach these.
    mockFetchCacheNodes.mockResolvedValue(
      nodes({
        other: [
          {
            bucket: "node_output",
            label: "old_join_2",
            node_id: "old_join_2",
            source: "nb_batch",
            generations: 2,
            row_count: 10_000_000,
            size_bytes: 1.4 * GIB,
            newest_created_at: 1_700_000_000,
            build_seconds: 42,
            identity_digests: ["digest-orphan"],
          },
        ],
      }),
    )
    render(<PipelineSettingsModal onClose={vi.fn()} />)

    fireEvent.click(await screen.findByTestId("cache-other-old_join_2-clear"))

    await waitFor(() => expect(mockClearCacheIdentities).toHaveBeenCalledWith(["digest-orphan"]))
  })

  it("labels the columns so a row can be read without a legend", async () => {
    render(<PipelineSettingsModal onClose={vi.fn()} />)
    await screen.findByTestId("cache-node-join")

    const list = screen.getByTestId("cache-node-list")
    for (const heading of ["Node", "Status", "Size", "Cached", "Time"]) {
      expect(list).toHaveTextContent(heading)
    }
  })

  it("names the other readers of a shared snapshot on both rows", async () => {
    // One identity, one set of bytes: the carrier says what it is shared with,
    // the others say whose row to look at, and only one of them has a size.
    mockFetchCacheNodes.mockResolvedValue(
      nodes({
        nodes: [
          nodeEntry({
            node_id: "quotes_a",
            kind: "data_input",
            shares_snapshot_with: ["quotes_b"],
            size_bytes: 491,
          }),
          nodeEntry({
            node_id: "quotes_b",
            kind: "data_input",
            shares_snapshot_with: ["quotes_a"],
            size_bytes: 0,
            generations: 0,
          }),
        ],
      }),
    )
    render(<PipelineSettingsModal onClose={vi.fn()} />)

    expect(await screen.findByTestId("cache-node-quotes_a")).toHaveTextContent(
      "shared with quotes_b",
    )
    expect(screen.getByTestId("cache-node-quotes_b")).toHaveTextContent("same source as quotes_a")
  })

  it("names cached data that belongs to no node of this pipeline", async () => {
    // The whole reason for the second group: a deleted node's cache still
    // occupies the budget and is invisible everywhere else in the app.
    mockFetchCacheNodes.mockResolvedValue(
      nodes({
        other: [
          {
            bucket: "node_output",
            label: "old_join_2",
            node_id: "old_join_2",
            source: "nb_batch",
            generations: 2,
            row_count: 10_000_000,
            size_bytes: 1.4 * GIB,
            newest_created_at: 1_700_000_000,
            build_seconds: 42,
            identity_digests: ["digest-old-join"],
          },
        ],
      }),
    )
    render(<PipelineSettingsModal onClose={vi.fn()} />)

    const other = await screen.findByTestId("cache-other-old_join_2")
    expect(other).toHaveTextContent("old_join_2")
    expect(other).toHaveTextContent("nb_batch")
    expect(other).toHaveTextContent("Stored")
    expect(other).not.toHaveTextContent(/generation/i)
    expect(other).toHaveTextContent("1.4 GB")
  })

  it("reports bytes it could not attribute to any node", async () => {
    mockFetchCacheNodes.mockResolvedValue(
      nodes({ unattributed_generations: 1, unattributed_bytes: 5 * 1024 * 1024 }),
    )
    render(<PipelineSettingsModal onClose={vi.fn()} />)

    expect(await screen.findByTestId("cache-node-footnotes")).toHaveTextContent(
      "5.0 MB could not be attributed",
    )
  })

  it("says why a node has no cache state instead of leaving it blank", async () => {
    mockFetchCacheNodes.mockResolvedValue(
      nodes({
        nodes: [
          nodeEntry({
            node_id: "orphan",
            state: null,
            row_count: null,
            generations: 0,
            size_bytes: 0,
            unavailable_reason: "Node 'orphan' has no input.",
          }),
        ],
      }),
    )
    render(<PipelineSettingsModal onClose={vi.fn()} />)

    expect(await screen.findByTestId("cache-node-orphan")).toHaveTextContent(
      "Node 'orphan' has no input.",
    )
  })

  it("asks for the node report for the active source, once per read", async () => {
    render(<PipelineSettingsModal onClose={vi.fn()} />)
    await screen.findByTestId("cache-node-join")

    expect(mockFetchCacheNodes).toHaveBeenCalledTimes(1)
    expect(mockFetchCacheNodes.mock.calls[0][0]).toMatchObject({ source: "live" })

    fireEvent.click(screen.getByTestId("cache-usage-refresh"))
    await waitFor(() => expect(mockFetchCacheNodes).toHaveBeenCalledTimes(2))
  })

})

describe("PipelineSettingsModal settings fields", () => {
  beforeEach(() => {
    mockFetchCacheNodes.mockReset()
    mockFetchCacheNodes.mockResolvedValue(nodes())
    serveSettings()
    useGraphStore.setState({ nodes: [], edges: [], preamble: "", submodels: {} })
    useSettingsStore.setState({ rowLimit: 1000 })
    useToastStore.setState({ toasts: [], _toastCounter: 0 })
  })

  afterEach(cleanup)

  function field(label: RegExp): HTMLInputElement {
    return screen.getByLabelText(label) as HTMLInputElement
  }

  function getRowLimitInput(): HTMLInputElement {
    return field(/^preview rows$/i)
  }

  function getChunkInput(): HTMLInputElement {
    return field(/^chunk rows$/i)
  }

  async function opened() {
    render(<PipelineSettingsModal onClose={vi.fn()} />)
    await waitFor(() => expect(getChunkInput()).not.toBeDisabled())
  }

  function commit(input: HTMLInputElement, value: string) {
    fireEvent.change(input, { target: { value } })
    fireEvent.blur(input)
  }

  it("orders Preview, Caching, Memory and Time limits ahead of the cached data", async () => {
    await opened()
    const order = [
      getRowLimitInput(),
      getChunkInput(),
      screen.getByRole("switch", { name: "Caching" }),
      field(/^cache size$/i),
      field(/^previews$/i),
      field(/^kept free$/i),
      field(/^pipeline$/i),
      field(/^modelling$/i),
      field(/^optimisation$/i),
      await screen.findByTestId("cache-node-list"),
    ]
    order.slice(1).forEach((element, index) => {
      expect(order[index].compareDocumentPosition(element) & 4).toBeTruthy()
    })
  })

  it("row limit input changes the store value", () => {
    render(<PipelineSettingsModal onClose={vi.fn()} />)
    fireEvent.change(getRowLimitInput(), { target: { value: "500" } })
    expect(useSettingsStore.getState().rowLimit).toBe(500)
  })

  it("row limit clamps negative values to 0", () => {
    render(<PipelineSettingsModal onClose={vi.fn()} />)
    fireEvent.change(getRowLimitInput(), { target: { value: "-50" } })
    expect(useSettingsStore.getState().rowLimit).toBe(0)
  })

  it("row limit treats NaN input as 0", () => {
    render(<PipelineSettingsModal onClose={vi.fn()} />)
    fireEvent.change(getRowLimitInput(), { target: { value: "abc" } })
    expect(useSettingsStore.getState().rowLimit).toBe(0)
  })

  it("row limit input shows current store value", () => {
    useSettingsStore.setState({ rowLimit: 2000 })
    render(<PipelineSettingsModal onClose={vi.fn()} />)
    expect(getRowLimitInput().value).toBe("2000")
  })

  it("keeps the settings disabled and empty until they load", async () => {
    let resolveLoad!: (response: PipelineSettingsResponse) => void
    mockGetPipelineSettings.mockReturnValueOnce(new Promise((resolve) => { resolveLoad = resolve }))
    render(<PipelineSettingsModal onClose={vi.fn()} />)

    expect(getChunkInput()).toBeDisabled()
    expect(getChunkInput().value).toBe("")
    expect(screen.getByRole("switch", { name: "Caching" })).toBeDisabled()

    await act(async () => resolveLoad(settingsResponse()))
    expect(getChunkInput()).not.toBeDisabled()
    expect(getChunkInput().value).toBe("500000")
  })

  it("loads the settings each time the pane opens", async () => {
    const { unmount } = render(<PipelineSettingsModal onClose={vi.fn()} />)
    await waitFor(() => expect(mockGetPipelineSettings).toHaveBeenCalledTimes(1))
    unmount()
    render(<PipelineSettingsModal onClose={vi.fn()} />)
    await waitFor(() => expect(mockGetPipelineSettings).toHaveBeenCalledTimes(2))
  })

  it("shows an automatic figure with the Auto marker and a set value with a reset", async () => {
    serveSettings({ modelling_time_limit_minutes: 90 })
    await opened()

    expect(field(/^previews$/i).value).toBe("10.3")
    expect(screen.getByTestId("pipeline-settings-preview-memory-gb-auto")).toHaveTextContent("Auto")
    expect(field(/^modelling$/i).value).toBe("90")
    expect(screen.queryByTestId("pipeline-settings-modelling-time-limit-minutes-auto")).toBeNull()
    expect(screen.getByRole("button", { name: "Use automatic Modelling" })).toBeInTheDocument()
  })

  it("shows no limit as the Optimisation field's placeholder", async () => {
    await opened()
    expect(field(/^optimisation$/i).value).toBe("")
    expect(field(/^optimisation$/i)).toHaveAttribute("placeholder", "No limit")
  })

  it("does not save on every keystroke", async () => {
    await opened()
    fireEvent.change(getChunkInput(), { target: { value: "100000" } })
    expect(mockPatchPipelineSettings).not.toHaveBeenCalled()
  })

  it("saves one key on blur and on Enter", async () => {
    await opened()
    commit(getChunkInput(), "100000")
    await waitFor(() => expect(mockPatchPipelineSettings).toHaveBeenCalledWith({ chunk_rows: 100_000 }))
    await waitFor(() => expect(getChunkInput().value).toBe("100000"))

    fireEvent.change(field(/^previews$/i), { target: { value: "6.5" } })
    fireEvent.keyDown(field(/^previews$/i), { key: "Enter" })
    await waitFor(() =>
      expect(mockPatchPipelineSettings).toHaveBeenLastCalledWith({ preview_memory_gb: 6.5 }),
    )
  })

  it("clamps chunk rows to the server's bounds", async () => {
    await opened()
    commit(getChunkInput(), "5")
    await waitFor(() => expect(mockPatchPipelineSettings).toHaveBeenCalledWith({ chunk_rows: 1000 }))
    commit(getChunkInput(), "15000000")
    await waitFor(() =>
      expect(mockPatchPipelineSettings).toHaveBeenLastCalledWith({ chunk_rows: 10_000_000 }),
    )
  })

  it.each([
    [/^chunk rows$/i, "abc"],
    [/^previews$/i, "0"],
    [/^previews$/i, "-2"],
    [/^kept free$/i, "-1"],
    [/^pipeline$/i, "0"],
    [/^pipeline$/i, "10081"],
  ])("ignores an invalid draft for %s (%s)", async (label, draft) => {
    await opened()
    const input = field(label)
    const before = input.value
    commit(input, draft)
    expect(mockPatchPipelineSettings).not.toHaveBeenCalled()
    expect(input.value).toBe(before)
  })

  it("accepts keeping nothing free", async () => {
    await opened()
    commit(field(/^kept free$/i), "0")
    await waitFor(() => expect(mockPatchPipelineSettings).toHaveBeenCalledWith({ kept_free_gb: 0 }))
  })

  it("sends nothing for a draft equal to what the field shows", async () => {
    await opened()
    commit(field(/^previews$/i), "10.3")
    commit(getChunkInput(), "500000")
    expect(mockPatchPipelineSettings).not.toHaveBeenCalled()
  })

  it("an empty draft restores automatic when set and is ignored when automatic", async () => {
    serveSettings({ preview_memory_gb: 6 })
    await opened()

    commit(getChunkInput(), "")
    expect(mockPatchPipelineSettings).not.toHaveBeenCalled()
    expect(getChunkInput().value).toBe("500000")

    commit(field(/^previews$/i), "")
    await waitFor(() =>
      expect(mockPatchPipelineSettings).toHaveBeenCalledWith({ preview_memory_gb: null }),
    )
    await waitFor(() => expect(field(/^previews$/i).value).toBe("10.3"))
  })

  it("the reset button restores automatic", async () => {
    serveSettings({ optimisation_time_limit_minutes: 120 })
    await opened()
    expect(field(/^optimisation$/i).value).toBe("120")

    fireEvent.click(screen.getByRole("button", { name: "Use automatic Optimisation" }))

    await waitFor(() =>
      expect(mockPatchPipelineSettings).toHaveBeenCalledWith({ optimisation_time_limit_minutes: null }),
    )
    await waitFor(() => expect(field(/^optimisation$/i).value).toBe(""))
    expect(screen.getByTestId("pipeline-settings-optimisation-time-limit-minutes-auto")).toBeInTheDocument()
  })

  it("restores the shown value and toasts when a save fails", async () => {
    await opened()
    mockPatchPipelineSettings.mockRejectedValueOnce(new Error("disk full"))

    commit(getChunkInput(), "100000")

    await waitFor(() => expect(getChunkInput().value).toBe("500000"))
    expect(useToastStore.getState().toasts.map((toast) => toast.text)).toEqual(["disk full"])
  })

  it("the Caching switch saves at each toggle and disables Cache size while off", async () => {
    await opened()
    const toggle = screen.getByRole("switch", { name: "Caching" })
    expect(toggle).toHaveAttribute("aria-checked", "true")
    expect(field(/^cache size$/i)).not.toBeDisabled()

    fireEvent.click(toggle)
    await waitFor(() => expect(mockPatchPipelineSettings).toHaveBeenCalledWith({ caching: false }))
    await waitFor(() => expect(toggle).toHaveAttribute("aria-checked", "false"))
    expect(field(/^cache size$/i)).toBeDisabled()

    fireEvent.click(toggle)
    await waitFor(() => expect(mockPatchPipelineSettings).toHaveBeenLastCalledWith({ caching: true }))
    await waitFor(() => expect(field(/^cache size$/i)).not.toBeDisabled())
  })

  it("reports a failed load and keeps the settings disabled", async () => {
    mockGetPipelineSettings.mockReset()
    mockGetPipelineSettings.mockRejectedValue(
      new Error(".haute/pipeline-settings.json: preview_memory_gb must be a number of GB greater than 0"),
    )
    render(<PipelineSettingsModal onClose={vi.fn()} />)

    expect(await screen.findByTestId("pipeline-settings-error")).toHaveTextContent(
      "preview_memory_gb must be a number of GB greater than 0",
    )
    expect(getChunkInput()).toBeDisabled()
    expect(field(/^previews$/i)).toBeDisabled()
    expect(screen.getByRole("switch", { name: "Caching" })).toBeDisabled()
  })

  it("names the file the settings are saved in", async () => {
    await opened()
    expect(screen.getByTestId("pipeline-settings-path")).toHaveTextContent(
      "Saved in .haute/pipeline-settings.json",
    )
  })
})

describe("PipelineSettingsModal calculation mode", () => {
  beforeEach(() => {
    mockFetchCacheNodes.mockReset()
    mockFetchCacheNodes.mockResolvedValue(nodes())
    serveSettings()
    useGraphStore.setState({ nodes: [], edges: [], preamble: "", submodels: {} })
    useUIStore.setState({ calculationMode: "automatic" })
  })

  afterEach(() => {
    useUIStore.setState({ calculationMode: "automatic" })
    cleanup()
  })

  it("puts the Calculation choice first, with Automatic selected", async () => {
    render(<PipelineSettingsModal onClose={vi.fn()} />)
    const group = screen.getByRole("radiogroup", { name: "Calculation" })
    const automatic = screen.getByRole("radio", { name: /automatic/i })
    const manual = screen.getByRole("radio", { name: /manual/i })
    expect(automatic).toHaveAttribute("aria-checked", "true")
    expect(manual).toHaveAttribute("aria-checked", "false")
    // One tab stop: the selected option.
    expect(automatic).toHaveAttribute("tabindex", "0")
    expect(manual).toHaveAttribute("tabindex", "-1")
    expect(group.compareDocumentPosition(screen.getByLabelText(/preview rows/i)) & 4).toBeTruthy()
    await screen.findByTestId("cache-node-list")
  })

  it("selecting Manual switches the session's calculation mode", async () => {
    render(<PipelineSettingsModal onClose={vi.fn()} />)
    fireEvent.click(screen.getByRole("radio", { name: /manual/i }))
    expect(useUIStore.getState().calculationMode).toBe("manual")
    expect(screen.getByRole("radio", { name: /manual/i })).toHaveAttribute("aria-checked", "true")
    await screen.findByTestId("cache-node-list")
  })

  it("arrow keys move the selection and focus", async () => {
    render(<PipelineSettingsModal onClose={vi.fn()} />)
    const automatic = screen.getByRole("radio", { name: /automatic/i })
    fireEvent.keyDown(automatic, { key: "ArrowRight" })
    expect(useUIStore.getState().calculationMode).toBe("manual")
    expect(screen.getByRole("radio", { name: /manual/i })).toHaveFocus()
    fireEvent.keyDown(screen.getByRole("radio", { name: /manual/i }), { key: "ArrowRight" })
    expect(useUIStore.getState().calculationMode).toBe("automatic")
    await screen.findByTestId("cache-node-list")
  })
})
