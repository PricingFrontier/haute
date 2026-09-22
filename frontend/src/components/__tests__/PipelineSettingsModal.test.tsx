/**
 * The pipeline settings pane (preview settings and cache inventory), per `specs/frontend-shared/low-level.md`.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { render, screen, fireEvent, cleanup, waitFor, act } from "@testing-library/react"

const mockFetchCacheNodes = vi.fn()

const mockClearCacheIdentities = vi.fn()

vi.mock("../../api/client", () => ({
  fetchCacheNodes: (...args: unknown[]) => mockFetchCacheNodes(...args),
  clearCacheIdentities: (...args: unknown[]) => mockClearCacheIdentities(...args),
}))

import PipelineSettingsModal from "../PipelineSettingsModal"
import type { CacheNodesResponse } from "../../api/types"
import useGraphStore from "../../stores/useGraphStore"
import useSettingsStore from "../../stores/useSettingsStore"
import useUIStore from "../../stores/useUIStore"

const GIB = 1024 * 1024 * 1024

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

describe("PipelineSettingsModal preview settings", () => {
  beforeEach(() => {
    mockFetchCacheNodes.mockReset()
    mockFetchCacheNodes.mockResolvedValue(nodes())
    useGraphStore.setState({ nodes: [], edges: [], preamble: "", submodels: {} })
    useSettingsStore.setState({ rowLimit: 1000, streamingChunkSize: 500_000 })
  })

  afterEach(cleanup)

  it("puts Preview rows above Chunk rows, ahead of the cached data", async () => {
    render(<PipelineSettingsModal onClose={vi.fn()} />)
    const rows = getRowLimitInput()
    const chunk = getChunkInput()
    expect(rows.compareDocumentPosition(chunk) & 4).toBeTruthy()
    const cached = await screen.findByTestId("cache-node-list")
    expect(chunk.compareDocumentPosition(cached) & 4).toBeTruthy()
  })

  function getRowLimitInput(): HTMLInputElement {
    return screen.getByLabelText(/preview rows/i) as HTMLInputElement
  }

  function getChunkInput(): HTMLInputElement {
    return screen.getByLabelText(/chunk rows/i) as HTMLInputElement
  }

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

  it("chunk input renders with the current streaming chunk size", () => {
    useSettingsStore.setState({ streamingChunkSize: 250_000 })
    render(<PipelineSettingsModal onClose={vi.fn()} />)
    expect(getChunkInput().value).toBe("250000")
  })

  it("chunk input updates the streaming chunk size in the store", () => {
    render(<PipelineSettingsModal onClose={vi.fn()} />)
    fireEvent.change(getChunkInput(), { target: { value: "100000" } })
    expect(useSettingsStore.getState().streamingChunkSize).toBe(100_000)
  })

  it("chunk input clamps sub-1000 values up to 1000", () => {
    render(<PipelineSettingsModal onClose={vi.fn()} />)
    fireEvent.change(getChunkInput(), { target: { value: "5" } })
    expect(useSettingsStore.getState().streamingChunkSize).toBe(1000)
  })

  it("chunk input ignores non-numeric input (no setter call, value preserved)", () => {
    useSettingsStore.setState({ streamingChunkSize: 250_000 })
    render(<PipelineSettingsModal onClose={vi.fn()} />)
    fireEvent.change(getChunkInput(), { target: { value: "abc" } })
    expect(useSettingsStore.getState().streamingChunkSize).toBe(250_000)
  })

  it("chunk input accepts scientific notation (5e5 -> 500000)", () => {
    render(<PipelineSettingsModal onClose={vi.fn()} />)
    fireEvent.change(getChunkInput(), { target: { value: "5e5" } })
    expect(useSettingsStore.getState().streamingChunkSize).toBe(500_000)
  })

  it("chunk input clamps over-max values to the backend bound (10_000_000)", () => {
    render(<PipelineSettingsModal onClose={vi.fn()} />)
    fireEvent.change(getChunkInput(), { target: { value: "15000000" } })
    expect(useSettingsStore.getState().streamingChunkSize).toBe(10_000_000)
  })

  it("chunk input has max attribute matching the backend bound", () => {
    render(<PipelineSettingsModal onClose={vi.fn()} />)
    expect(getChunkInput()).toHaveAttribute("max", "10000000")
  })
})

describe("PipelineSettingsModal calculation mode", () => {
  beforeEach(() => {
    mockFetchCacheNodes.mockReset()
    mockFetchCacheNodes.mockResolvedValue(nodes())
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
