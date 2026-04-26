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

vi.mock("../../api/client", () => ({
  fetchDatabricksSchema: vi.fn(),
  buildJsonCache: (...args: unknown[]) => mockBuildJsonCache(...args),
  getJsonCacheProgress: (...args: unknown[]) => mockGetJsonCacheProgress(...args),
  getJsonCacheStatus: (...args: unknown[]) => mockGetJsonCacheStatus(...args),
  getJsonCacheStatusForSchema: (...args: unknown[]) => mockGetJsonCacheStatusForSchema(...args),
  deleteJsonCache: (...args: unknown[]) => mockDeleteJsonCache(...args),
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

    render(<ApiInputEditor {...DEFAULT_PROPS} config={{ path: "data/input.json" }} />)

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

  it("JsonCacheButton: sends flattenSchema in status and build requests", async () => {
    const flattenSchema = { x: "int" }
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
        config={{ path: "data/input.json", flattenSchema }}
      />,
    )

    await waitFor(() => {
      expect(mockGetJsonCacheStatusForSchema).toHaveBeenCalledWith({
        path: "data/input.json",
        flatten_schema: flattenSchema,
      })
    })
    expect(mockGetJsonCacheStatus).not.toHaveBeenCalled()

    await act(async () => {
      fireEvent.click(screen.getByText("Cache as Parquet").closest("button")!)
    })

    await waitFor(() => {
      expect(mockBuildJsonCache).toHaveBeenCalledWith({
        path: "data/input.json",
        flatten_schema: flattenSchema,
      })
    })
  })

  it("JsonCacheButton: shows error on failure", async () => {
    mockGetJsonCacheStatus.mockResolvedValue({ cached: false })
    mockBuildJsonCache.mockRejectedValue(new Error("Failed to build cache"))

    render(<ApiInputEditor {...DEFAULT_PROPS} config={{ path: "data/input.json" }} />)

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

})
