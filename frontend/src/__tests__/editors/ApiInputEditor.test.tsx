/**
 * Render tests for ApiInputEditor.
 *
 * Tests: API banner, preview data label, FileBrowser with extensions filter,
 * cache button visibility, JsonCacheButton states
 * (initial, after build, error on failure).
 */
import { describe, it, expect, vi, afterEach, beforeEach } from "vitest"
import { render, screen, fireEvent, cleanup, waitFor, act } from "@testing-library/react"
import ApiInputEditor from "../../panels/editors/ApiInputEditor"

afterEach(cleanup)

// Mock the shared components that make API calls
vi.mock("../../panels/editors/_shared", async () => {
  const actual = await vi.importActual("../../panels/editors/_shared")
  return {
    ...actual,
    FileBrowser: ({ currentPath, onSelect, extensions }: { currentPath?: string; onSelect: (path: string) => void; extensions?: string }) => (
      <div data-testid="file-browser">
        <span data-testid="current-path">{currentPath || ""}</span>
        <span data-testid="extensions">{extensions || ""}</span>
        <button data-testid="select-file" onClick={() => onSelect("test.json")}>Select</button>
      </div>
    ),
    SchemaPreview: ({ schema }: { schema: unknown }) => (
      <div data-testid="schema-preview">{schema ? "Schema loaded" : "No schema"}</div>
    ),
  }
})

const mockBuildJsonCache = vi.fn()
const mockGetJsonCacheStatus = vi.fn()
const mockGetJsonCacheStatusForSchema = vi.fn()
const mockGetJsonCacheProgress = vi.fn()
const mockDeleteJsonCache = vi.fn()
const mockCancelJsonCache = vi.fn()
const mockInferJsonCacheSchema = vi.fn()

vi.mock("../../api/client", () => ({
  fetchDatabricksSchema: vi.fn(),
  buildJsonCache: (...args: unknown[]) => mockBuildJsonCache(...args),
  getJsonCacheProgress: (...args: unknown[]) => mockGetJsonCacheProgress(...args),
  getJsonCacheStatus: (...args: unknown[]) => mockGetJsonCacheStatus(...args),
  getJsonCacheStatusForSchema: (...args: unknown[]) => mockGetJsonCacheStatusForSchema(...args),
  deleteJsonCache: (...args: unknown[]) => mockDeleteJsonCache(...args),
  cancelJsonCache: (...args: unknown[]) => mockCancelJsonCache(...args),
  inferJsonCacheSchema: (...args: unknown[]) => mockInferJsonCacheSchema(...args),
  ApiError: class ApiError extends Error {
    status: number
    detail?: string
    constructor(message: string, status: number, detail?: string) {
      super(message); this.status = status; this.detail = detail
    }
  },
}))

vi.mock("../../hooks/useSchemaFetch", () => ({
  useSchemaFetch: (initialPath?: string) => ({
    schema: initialPath ? { columns: [{ name: "col1", dtype: "Int64" }, { name: "col2", dtype: "String" }], preview: [], row_count: 10 } : null,
    setSchema: vi.fn(),
    loading: false,
    fetchForPath: vi.fn(),
  }),
}))

beforeEach(() => {
  mockBuildJsonCache.mockReset()
  mockGetJsonCacheStatus.mockReset().mockResolvedValue({ cached: false })
  mockGetJsonCacheStatusForSchema.mockReset().mockResolvedValue({ cached: false })
  mockGetJsonCacheProgress.mockReset().mockResolvedValue({ active: false })
  mockDeleteJsonCache.mockReset()
  mockCancelJsonCache.mockReset()
  mockInferJsonCacheSchema.mockReset()
})

const DEFAULT_PROPS = {
  config: {} as Record<string, unknown>,
  onUpdate: vi.fn(),
  accentColor: "#10b981",
}

describe("ApiInputEditor", () => {
  it("renders API input banner text", () => {
    render(<ApiInputEditor {...DEFAULT_PROPS} />)
    expect(screen.getByText("This node receives live API requests at deploy time")).toBeTruthy()
  })

  it("FileBrowser rendered with .json/.jsonl extensions filter", () => {
    render(<ApiInputEditor {...DEFAULT_PROPS} />)
    expect(screen.getByTestId("file-browser")).toBeTruthy()
    expect(screen.getByTestId("extensions").textContent).toBe(".json,.jsonl")
  })


  it("cache button shown for .json files", () => {
    render(<ApiInputEditor {...DEFAULT_PROPS} config={{ path: "data/input.json" }} />)
    expect(screen.getByText("Cache as Parquet")).toBeTruthy()
  })

  it("cache button shown for .jsonl files", () => {
    render(<ApiInputEditor {...DEFAULT_PROPS} config={{ path: "data/input.jsonl" }} />)
    expect(screen.getByText("Cache as Parquet")).toBeTruthy()
  })

  it("cache button hidden for non-json files", () => {
    render(<ApiInputEditor {...DEFAULT_PROPS} config={{ path: "data/input.parquet" }} />)
    expect(screen.queryByText("Cache as Parquet")).toBeNull()
  })

  it("cache button hidden when no path is set", () => {
    render(<ApiInputEditor {...DEFAULT_PROPS} config={{}} />)
    expect(screen.queryByText("Cache as Parquet")).toBeNull()
  })

  it("JsonCacheButton: shows 'Cache as Parquet' initially when not cached", async () => {
    mockGetJsonCacheStatus.mockResolvedValue({ cached: false })

    render(<ApiInputEditor {...DEFAULT_PROPS} config={{ path: "data/input.json" }} />)

    await waitFor(() => {
      expect(screen.getByText("Cache as Parquet")).toBeTruthy()
    })
  })

  it("JsonCacheButton: shows cache info after successful build", async () => {
    mockGetJsonCacheStatus.mockResolvedValue({ cached: false })
    mockBuildJsonCache.mockResolvedValue({
      cached: true,
      data_path: "data/input.json",
      row_count: 100,
      column_count: 5,
      size_bytes: 2048,
      cached_at: 0,
    })

    // Need at least one emit:true table — the Cache button is disabled
    // when there's no schema source or no emit-true table (T9/T10).
    render(
      <ApiInputEditor
        {...DEFAULT_PROPS}
        config={{
          path: "data/input.json",
          tables: [
            { path: "$[*]", label: "policies", emit: true, columns: [] },
          ],
        }}
      />,
    )

    // Click the cache button
    await act(async () => {
      fireEvent.click(screen.getByText("Cache as Parquet").closest("button")!)
    })

    await waitFor(() => {
      // After successful build, should show "Refresh Cache" instead
      expect(screen.getByText("Refresh Cache")).toBeTruthy()
    })

    // Should show cache stats
    await waitFor(() => {
      expect(screen.getByText("100 rows")).toBeTruthy()
      expect(screen.getByText("5 cols")).toBeTruthy()
    })
  })

  it("JsonCacheButton: sends config_path in status and build requests when provided", async () => {
    // v2 dispatch on the backend reads the on-disk config file; the editor
    // passes config_path so the cache button can name it. The previous
    // flatten_schema-inline path is gone — the backend reads the v2 schema
    // from the file at config_path.
    mockGetJsonCacheStatusForSchema.mockResolvedValue({ cached: false })
    mockBuildJsonCache.mockResolvedValue({
      cached: true,
      data_path: "data/input.json",
      row_count: 1,
      column_count: 1,
      size_bytes: 1024,
      cached_at: 0,
    })

    render(
      <ApiInputEditor
        {...DEFAULT_PROPS}
        config={{
          path: "data/input.json",
          tables: [
            { path: "$[*]", label: "policies", emit: true, columns: [] },
          ],
        }}
        configPath="rating/config/quote_input/api_input.json"
      />,
    )

    await waitFor(() => {
      expect(mockGetJsonCacheStatusForSchema).toHaveBeenCalledWith(
        expect.objectContaining({
          path: "data/input.json",
          config_path: "rating/config/quote_input/api_input.json",
        }),
      )
    })
    expect(mockGetJsonCacheStatus).not.toHaveBeenCalled()

    await act(async () => {
      fireEvent.click(screen.getByText("Cache as Parquet").closest("button")!)
    })

    await waitFor(() => {
      // After v1 removal: cache POST now also carries `volatile_schema`
      // — the editor's in-memory v2. Use objectContaining so the test
      // doesn't pin the full shape of writeV2's output.
      expect(mockBuildJsonCache).toHaveBeenCalledWith(
        expect.objectContaining({
          path: "data/input.json",
          config_path: "rating/config/quote_input/api_input.json",
        }),
      )
      const payload = mockBuildJsonCache.mock.calls[0][0] as Record<string, unknown>
      expect(payload).toHaveProperty("volatile_schema")
    })
  })

  it("JsonCacheButton: shows error on failure", async () => {
    mockGetJsonCacheStatus.mockResolvedValue({ cached: false })
    mockBuildJsonCache.mockRejectedValue(new Error("Failed to build cache"))

    // Cache button is disabled with no emit:true table; give it one
    // so the click path fires and the error bubbles up.
    render(
      <ApiInputEditor
        {...DEFAULT_PROPS}
        config={{
          path: "data/input.json",
          tables: [
            { path: "$[*]", label: "policies", emit: true, columns: [] },
          ],
        }}
      />,
    )

    await act(async () => {
      fireEvent.click(screen.getByText("Cache as Parquet").closest("button")!)
    })

    await waitFor(() => {
      expect(screen.getByText("Failed to build cache")).toBeTruthy()
    })
  })

  it("JsonCacheButton: shows 'Not cached yet' message", async () => {
    mockGetJsonCacheStatus.mockResolvedValue({ cached: false })

    render(<ApiInputEditor {...DEFAULT_PROPS} config={{ path: "data/input.json" }} />)

    await waitFor(() => {
      expect(screen.getByText(/Not cached yet/)).toBeTruthy()
    })
  })

  it("renders Preview Data label", () => {
    render(<ApiInputEditor {...DEFAULT_PROPS} />)
    expect(screen.getByText("Preview Data")).toBeTruthy()
  })


  it("shows SchemaPreview component", () => {
    render(<ApiInputEditor {...DEFAULT_PROPS} />)
    expect(screen.getByTestId("schema-preview")).toBeTruthy()
  })

  // ─── v2 schema surface ──────────────────────────────────────

  it("renders the v2 tables section when config is v2-shaped", () => {
    render(
      <ApiInputEditor
        {...DEFAULT_PROPS}
        config={{ path: "data/input.json", tables: [] }}
      />,
    )
    expect(screen.getByTestId("api-input-tables")).toBeTruthy()
    expect(screen.getByText(/No tables yet/)).toBeTruthy()
  })

  it("Add Table creates a root table with emit=true", () => {
    const onUpdate = vi.fn()
    render(
      <ApiInputEditor
        {...DEFAULT_PROPS}
        onUpdate={onUpdate}
        config={{ path: "data/input.json", tables: [] }}
      />,
    )
    fireEvent.click(screen.getByTestId("api-input-add-table-btn"))
    expect(onUpdate).toHaveBeenCalledWith(expect.objectContaining({
      tables: expect.arrayContaining([
        expect.objectContaining({ path: "$[*]", emit: true }),
      ]),
    }))
  })

  it("ticking a table's emit toggle pushes the change back", () => {
    const onUpdate = vi.fn()
    render(
      <ApiInputEditor
        {...DEFAULT_PROPS}
        onUpdate={onUpdate}
        config={{
          path: "data/input.json",
          tables: [
            {
              path: "$[*]",
              label: "policies",
              emit: false,
              columns: [],
            },
          ],
        }}
      />,
    )
    fireEvent.click(screen.getByTestId("api-input-table-0-emit"))
    expect(onUpdate).toHaveBeenCalledWith(expect.objectContaining({
      tables: expect.arrayContaining([
        expect.objectContaining({ emit: true }),
      ]),
    }))
  })

  it("Add Column appends a column with selected=true and type=str", () => {
    const onUpdate = vi.fn()
    render(
      <ApiInputEditor
        {...DEFAULT_PROPS}
        onUpdate={onUpdate}
        config={{
          path: "data/input.json",
          tables: [
            { path: "$[*]", label: "policies", emit: true, columns: [] },
          ],
        }}
      />,
    )
    fireEvent.click(screen.getByTestId("api-input-table-0-add-col"))
    const lastCall = onUpdate.mock.calls[onUpdate.mock.calls.length - 1][0]
    const newCols = lastCall.tables[0].columns
    expect(newCols).toHaveLength(1)
    expect(newCols[0].selected).toBe(true)
    expect(newCols[0].type).toBe("str")
  })

  it("Infer Tables routes the raw /infer response through readV2 (sanitised, not raw-cast)", async () => {
    // The raw /infer payload carries a junk table (no path) and a column
    // with an unknown type. readV2 must drop the junk table and coerce
    // the bad type to "str" — proving the inferred result is normalised
    // the same way every other read path is, not raw-cast into state.
    mockInferJsonCacheSchema.mockResolvedValue({
      tables: [
        {
          path: "$[*]",
          label: "policies",
          emit: true,
          columns: [
            { name: "policy_id", path: "$[*].policy_id", type: "weird_type", status: "Inferred", selected: true },
          ],
        },
        // junk: no path — readV2 drops it
        { label: "ghost", emit: true, columns: [] },
      ],
    })
    const onUpdate = vi.fn()
    render(
      <ApiInputEditor
        {...DEFAULT_PROPS}
        onUpdate={onUpdate}
        config={{ path: "data/input.json", tables: [] }}
      />,
    )
    await act(async () => {
      fireEvent.click(screen.getByTestId("api-input-infer-btn"))
    })
    await waitFor(() => {
      expect(onUpdate).toHaveBeenCalled()
    })
    const arg = onUpdate.mock.calls[onUpdate.mock.calls.length - 1][0]
    // junk table dropped
    expect(arg.tables.length).toBe(1)
    // bad column type coerced to "str"
    expect(arg.tables[0].columns[0].type).toBe("str")
  })

  it("Infer Tables does NOT clobber existing user tables without confirmation", async () => {
    mockInferJsonCacheSchema.mockResolvedValue({
      tables: [
        { path: "$[*]", label: "inferred_policies", emit: true, columns: [] },
      ],
    })
    const onUpdate = vi.fn()
    render(
      <ApiInputEditor
        {...DEFAULT_PROPS}
        onUpdate={onUpdate}
        config={{
          path: "data/input.json",
          tables: [
            { path: "$[*]", label: "my_renamed", emit: false, columns: [] },
          ],
        }}
      />,
    )
    await act(async () => {
      fireEvent.click(screen.getByTestId("api-input-infer-btn"))
    })
    // Inference ran, but state must NOT have been overwritten yet — a
    // confirmation gate stands between the result and the clobber.
    await waitFor(() => {
      expect(mockInferJsonCacheSchema).toHaveBeenCalled()
    })
    expect(onUpdate).not.toHaveBeenCalled()
    // The confirm affordance is shown.
    expect(screen.getByTestId("api-input-infer-confirm")).toBeTruthy()
  })

  it("confirming a re-infer merges by path, preserving user emit/label/selected for matching tables", async () => {
    mockInferJsonCacheSchema.mockResolvedValue({
      tables: [
        {
          path: "$[*]",
          label: "inferred_policies",
          emit: true,
          columns: [
            { name: "policy_id", path: "$[*].policy_id", type: "int", status: "Inferred", selected: true },
          ],
        },
        {
          path: "$[*].drivers[*]",
          label: "drivers",
          emit: true,
          columns: [],
        },
      ],
    })
    const onUpdate = vi.fn()
    render(
      <ApiInputEditor
        {...DEFAULT_PROPS}
        onUpdate={onUpdate}
        config={{
          path: "data/input.json",
          tables: [
            // user curated the root table: renamed label, emit off
            { path: "$[*]", label: "my_quotes", emit: false, columns: [] },
          ],
        }}
      />,
    )
    await act(async () => {
      fireEvent.click(screen.getByTestId("api-input-infer-btn"))
    })
    await waitFor(() => {
      expect(screen.getByTestId("api-input-infer-confirm")).toBeTruthy()
    })
    await act(async () => {
      fireEvent.click(screen.getByTestId("api-input-infer-confirm"))
    })
    await waitFor(() => {
      expect(onUpdate).toHaveBeenCalled()
    })
    const arg = onUpdate.mock.calls[onUpdate.mock.calls.length - 1][0]
    // Matching-path table keeps user's label + emit choice...
    const root = arg.tables.find((t: { path: string }) => t.path === "$[*]")
    expect(root.label).toBe("my_quotes")
    expect(root.emit).toBe(false)
    // ...but picks up the inferred columns.
    expect(root.columns.length).toBe(1)
    // New inferred table is added.
    expect(arg.tables.find((t: { path: string }) => t.path === "$[*].drivers[*]")).toBeTruthy()
  })

  it("cancelling a re-infer leaves existing tables untouched", async () => {
    mockInferJsonCacheSchema.mockResolvedValue({
      tables: [{ path: "$[*]", label: "inferred", emit: true, columns: [] }],
    })
    const onUpdate = vi.fn()
    render(
      <ApiInputEditor
        {...DEFAULT_PROPS}
        onUpdate={onUpdate}
        config={{
          path: "data/input.json",
          tables: [{ path: "$[*]", label: "mine", emit: false, columns: [] }],
        }}
      />,
    )
    await act(async () => {
      fireEvent.click(screen.getByTestId("api-input-infer-btn"))
    })
    await waitFor(() => {
      expect(screen.getByTestId("api-input-infer-cancel")).toBeTruthy()
    })
    await act(async () => {
      fireEvent.click(screen.getByTestId("api-input-infer-cancel"))
    })
    expect(onUpdate).not.toHaveBeenCalled()
    expect(screen.queryByTestId("api-input-infer-confirm")).toBeNull()
  })

  it("Infer Tables calls the route and writes the result via onUpdate", async () => {
    mockInferJsonCacheSchema.mockResolvedValue({
      tables: [
        {
          path: "$[*]",
          label: "policies",
          emit: true,
          columns: [
            { name: "policy_id", path: "$[*].policy_id", type: "int", status: "Inferred", selected: true },
          ],
        },
        {
          path: "$[*].drivers[*]",
          label: "drivers",
          emit: false,
          columns: [
            { name: "driver_id", path: "$[*].drivers[*].driver_id", type: "int", status: "Inferred", selected: true },
          ],
        },
      ],
    })
    const onUpdate = vi.fn()
    render(
      <ApiInputEditor
        {...DEFAULT_PROPS}
        onUpdate={onUpdate}
        config={{ path: "data/input.json", tables: [] }}
      />,
    )
    await act(async () => {
      fireEvent.click(screen.getByTestId("api-input-infer-btn"))
    })
    await waitFor(() => {
      expect(mockInferJsonCacheSchema).toHaveBeenCalledWith({ path: "data/input.json" })
    })
    await waitFor(() => {
      expect(onUpdate).toHaveBeenCalled()
      const arg = onUpdate.mock.calls[onUpdate.mock.calls.length - 1][0]
      expect(arg.tables.length).toBe(2)
    })
  })

})
