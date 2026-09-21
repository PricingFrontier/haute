/**
 * The cache usage pane, per `specs/frontend-shared/low-level.md`.
 *
 * The acceptance is that it shows both budgets and names the variable behind
 * each limit — the names are what a user changes when a capture is refused,
 * so a pane that showed bars without them would not answer the question it
 * exists for.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { render, screen, fireEvent, cleanup, waitFor, act } from "@testing-library/react"

const mockFetchCacheUsage = vi.fn()
const mockFetchCacheNodes = vi.fn()

vi.mock("../../api/client", () => ({
  fetchCacheUsage: (...args: unknown[]) => mockFetchCacheUsage(...args),
  fetchCacheNodes: (...args: unknown[]) => mockFetchCacheNodes(...args),
}))

import CacheSettingsModal from "../CacheSettingsModal"
import type { CacheNodesResponse, CacheUsageResponse } from "../../api/types"
import useGraphStore from "../../stores/useGraphStore"

const GIB = 1024 * 1024 * 1024

function usage(overrides: Partial<CacheUsageResponse> = {}): CacheUsageResponse {
  return {
    schema_version: 1,
    node_outputs: {
      generations_used: 12,
      generations_limit: 512,
      generations_limit_variable: "HAUTE_NODE_SNAPSHOT_MAX_GENERATIONS",
      bytes_used: 3 * GIB,
      bytes_limit: 40 * GIB,
      bytes_limit_variable: "HAUTE_NODE_SNAPSHOT_MAX_BYTES",
    },
    input_snapshots: {
      generations_used: 4,
      generations_limit: 64,
      generations_limit_variable: "HAUTE_INPUT_CACHE_MAX_GENERATIONS",
      bytes_used: 512 * 1024 * 1024,
      bytes_limit: 20 * GIB,
      bytes_limit_variable: "HAUTE_INPUT_CACHE_MAX_BYTES",
    },
    ...overrides,
  }
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
    retention: "automatic" as const,
    unavailable_reason: null,
    ...overrides,
  }
}

function nodes(overrides: Partial<CacheNodesResponse> = {}): CacheNodesResponse {
  return {
    schema_version: 1,
    source: "live",
    nodes: [nodeEntry(), nodeEntry({ node_id: "source", state: "missing", row_count: null, generations: 0, size_bytes: 0, newest_created_at: null })],
    other: [],
    unattributed_generations: 0,
    unattributed_bytes: 0,
    unmarked_identities: 0,
    ...overrides,
  }
}

describe("CacheSettingsModal", () => {
  beforeEach(() => {
    mockFetchCacheUsage.mockReset()
    mockFetchCacheUsage.mockResolvedValue(usage())
    mockFetchCacheNodes.mockReset()
    mockFetchCacheNodes.mockResolvedValue(nodes())
    useGraphStore.setState({ nodes: [], edges: [], preamble: "", submodels: {} })
  })

  afterEach(cleanup)

  it("shows both budgets' generations and bytes against their limits", async () => {
    render(<CacheSettingsModal onClose={vi.fn()} />)

    const nodeOutputs = await screen.findByTestId("cache-budget-node_outputs")
    expect(nodeOutputs).toHaveTextContent("12 of 512")
    expect(nodeOutputs).toHaveTextContent("3.0 GB of 40 GB")

    const inputSnapshots = screen.getByTestId("cache-budget-input_snapshots")
    expect(inputSnapshots).toHaveTextContent("4 of 64")
    // Ten or more drops the decimal, so a size and its limit stay on one line.
    expect(inputSnapshots).toHaveTextContent("512 MB of 20 GB")
  })

  it("names the environment variable behind each of the four limits", async () => {
    render(<CacheSettingsModal onClose={vi.fn()} />)

    const nodeOutputs = await screen.findByTestId("cache-budget-node_outputs")
    expect(nodeOutputs).toHaveTextContent("HAUTE_NODE_SNAPSHOT_MAX_GENERATIONS")
    expect(nodeOutputs).toHaveTextContent("HAUTE_NODE_SNAPSHOT_MAX_BYTES")

    const inputSnapshots = screen.getByTestId("cache-budget-input_snapshots")
    expect(inputSnapshots).toHaveTextContent("HAUTE_INPUT_CACHE_MAX_GENERATIONS")
    expect(inputSnapshots).toHaveTextContent("HAUTE_INPUT_CACHE_MAX_BYTES")
  })

  it("names whatever variable the server reported, not a hardcoded one", async () => {
    // The server owns these names because it is what reads them. A pane that
    // printed its own copy would keep saying the old name after a rename.
    mockFetchCacheUsage.mockResolvedValue(
      usage({
        node_outputs: {
          ...usage().node_outputs,
          bytes_limit_variable: "HAUTE_RENAMED_NODE_BUDGET",
        },
      }),
    )
    render(<CacheSettingsModal onClose={vi.fn()} />)

    const nodeOutputs = await screen.findByTestId("cache-budget-node_outputs")
    expect(nodeOutputs).toHaveTextContent("HAUTE_RENAMED_NODE_BUDGET")
    expect(nodeOutputs).not.toHaveTextContent("HAUTE_NODE_SNAPSHOT_MAX_BYTES")
  })

  it("reads the usage once on open and again only when asked", async () => {
    // Not polling is the package's design constraint — the endpoint costs what
    // admitting a capture costs — so the test has to let plenty of time pass
    // and prove nothing fired. Without the clock, a `setInterval(refresh, …)`
    // added to the pane would pass this test.
    vi.useFakeTimers({ shouldAdvanceTime: true })
    try {
      render(<CacheSettingsModal onClose={vi.fn()} />)
      await screen.findByTestId("cache-budget-node_outputs")
      expect(mockFetchCacheUsage).toHaveBeenCalledTimes(1)

      await vi.advanceTimersByTimeAsync(10 * 60 * 1000)
      expect(mockFetchCacheUsage).toHaveBeenCalledTimes(1)

      fireEvent.click(screen.getByTestId("cache-usage-refresh"))
      await waitFor(() => expect(mockFetchCacheUsage).toHaveBeenCalledTimes(2))

      await vi.advanceTimersByTimeAsync(10 * 60 * 1000)
      expect(mockFetchCacheUsage).toHaveBeenCalledTimes(2)
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
    render(<CacheSettingsModal onClose={vi.fn()} />)
    await screen.findByTestId("cache-node-join")
    expect(mockFetchCacheNodes).toHaveBeenCalledTimes(1)

    await act(async () => {
      useGraphStore.setState({ edges: [{ id: "e1", source: "a", target: "b" }] as never })
    })
    await act(async () => {
      useGraphStore.setState({ preamble: "import polars as pl" })
    })

    expect(mockFetchCacheNodes).toHaveBeenCalledTimes(1)
    expect(mockFetchCacheUsage).toHaveBeenCalledTimes(1)
  })

  it("sends the graph as it stands when the read is made", async () => {
    render(<CacheSettingsModal onClose={vi.fn()} />)
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
    mockFetchCacheUsage.mockImplementation((options?: { signal?: AbortSignal }) => {
      signal = options?.signal
      return new Promise(() => {})
    })

    const { unmount } = render(<CacheSettingsModal onClose={vi.fn()} />)
    await waitFor(() => expect(mockFetchCacheUsage).toHaveBeenCalledTimes(1))
    const opening = signal
    expect(opening?.aborted).toBe(false)

    unmount()
    expect(opening?.aborted).toBe(true)
  })

  it("abandons a Refresh's read when the pane closes", async () => {
    const { unmount } = render(<CacheSettingsModal onClose={vi.fn()} />)
    await screen.findByTestId("cache-budget-node_outputs")

    let refreshSignal: AbortSignal | undefined
    mockFetchCacheUsage.mockImplementation((options?: { signal?: AbortSignal }) => {
      refreshSignal = options?.signal
      return new Promise(() => {})
    })
    fireEvent.click(screen.getByTestId("cache-usage-refresh"))
    await waitFor(() => expect(refreshSignal).toBeDefined())
    expect(refreshSignal?.aborted).toBe(false)

    unmount()
    expect(refreshSignal?.aborted).toBe(true)
  })

  it("reports a failed read and keeps the last good numbers on screen", async () => {
    render(<CacheSettingsModal onClose={vi.fn()} />)
    await screen.findByTestId("cache-budget-node_outputs")

    mockFetchCacheUsage.mockRejectedValue(new Error("store unreadable"))
    fireEvent.click(screen.getByTestId("cache-usage-refresh"))

    expect(await screen.findByTestId("cache-usage-error")).toHaveTextContent("store unreadable")
    expect(screen.getByTestId("cache-budget-node_outputs")).toHaveTextContent("12 of 512")
  })

  it("lists every node of the pipeline with what it holds", async () => {
    render(<CacheSettingsModal onClose={vi.fn()} />)

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
    render(<CacheSettingsModal onClose={vi.fn()} />)

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
    render(<CacheSettingsModal onClose={vi.fn()} />)

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
    render(<CacheSettingsModal onClose={vi.fn()} />)

    const banding = await screen.findByTestId("cache-node-banding")
    expect(banding).toHaveTextContent("reads Premium join")
    expect(banding).not.toHaveTextContent("MB")
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
    render(<CacheSettingsModal onClose={vi.fn()} />)

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
          },
        ],
      }),
    )
    render(<CacheSettingsModal onClose={vi.fn()} />)

    const other = await screen.findByTestId("cache-other-old_join_2")
    expect(other).toHaveTextContent("old_join_2")
    expect(other).toHaveTextContent("nb_batch")
    expect(other).toHaveTextContent("2 generations")
    expect(other).toHaveTextContent("1.4 GB")
  })

  it("explains a bar that exceeds what the list accounts for", async () => {
    // Unmarked entries are charged to both budgets at admission. Without this
    // line the bars simply look wrong against the rows beneath them.
    mockFetchCacheNodes.mockResolvedValue(nodes({ unmarked_identities: 9 }))
    render(<CacheSettingsModal onClose={vi.fn()} />)

    const footnotes = await screen.findByTestId("cache-node-footnotes")
    expect(footnotes).toHaveTextContent("9 cached entries carry no provider marker")
    expect(footnotes).toHaveTextContent("counted against both budgets")
  })

  it("reports bytes it could not attribute to any node", async () => {
    mockFetchCacheNodes.mockResolvedValue(
      nodes({ unattributed_generations: 1, unattributed_bytes: 5 * 1024 * 1024 }),
    )
    render(<CacheSettingsModal onClose={vi.fn()} />)

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
    render(<CacheSettingsModal onClose={vi.fn()} />)

    expect(await screen.findByTestId("cache-node-orphan")).toHaveTextContent(
      "Node 'orphan' has no input.",
    )
  })

  it("asks for the node report for the active source, once per read", async () => {
    render(<CacheSettingsModal onClose={vi.fn()} />)
    await screen.findByTestId("cache-node-join")

    expect(mockFetchCacheNodes).toHaveBeenCalledTimes(1)
    expect(mockFetchCacheNodes.mock.calls[0][0]).toMatchObject({ source: "live" })

    fireEvent.click(screen.getByTestId("cache-usage-refresh"))
    await waitFor(() => expect(mockFetchCacheNodes).toHaveBeenCalledTimes(2))
  })

  it("says nothing about a combined total, because the budgets are separate", async () => {
    render(<CacheSettingsModal onClose={vi.fn()} />)
    await screen.findByTestId("cache-budget-node_outputs")

    // 16 generations and 3.5 GB would be the sums; neither is a real limit.
    expect(screen.queryByText(/16 of 576/)).toBeNull()
    expect(screen.queryByText(/total/i)).toBeNull()
  })
})
