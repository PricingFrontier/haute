/**
 * Contract test for Bundle 3b — cache button repositioned above the
 * Tables editor (schema) in ApiInputEditor.
 *
 * Lives in its own file (rather than the sibling
 * `apiInputBundle3bContract.test.tsx`) because the NodePanel-based
 * banner tests mock `LazyNodeEditors`, but this test renders the real
 * `ApiInputEditor` directly and needs its real dependencies
 * (`useSchemaFetch`, `api/client`, `_shared`).
 *
 * Asserted contract: when the cache button is rendered, it appears in
 * document order BEFORE the Tables editor (the section with
 * `data-testid="api-input-tables"`). The previous layout placed it
 * INSIDE that section, below the table list. Repositioning groups it
 * contextually with the data file selection and gives the schema
 * primary visual weight.
 */
import { describe, it, expect, vi, afterEach, beforeEach } from "vitest"
import { render, screen, cleanup } from "@testing-library/react"

import ApiInputEditor from "../../panels/editors/ApiInputEditor"

vi.mock("../../panels/editors/_shared", async () => {
  const actual = await vi.importActual("../../panels/editors/_shared")
  return {
    ...actual,
    FileBrowser: ({ currentPath, onSelect }: { currentPath?: string; onSelect: (p: string) => void }) => (
      <div data-testid="file-browser">
        <span>{currentPath || ""}</span>
        <button onClick={() => onSelect("test.json")}>Select</button>
      </div>
    ),
    SchemaPreview: () => <div data-testid="schema-preview" />,
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
    schema: initialPath ? { columns: [], preview: [], row_count: 0 } : null,
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

afterEach(cleanup)

describe("Bundle 3b — cache button positioned above the Tables editor", () => {
  it("renders the cache button before the Tables editor in DOM order", () => {
    const config = {
      path: "rating/data/sample.json",
      tables: [
        {
          path: "$[:]",
          label: "row",
          displayPath: null,
          emit: true,
          columns: [
            { name: "col_a", path: "$[:].col_a", type: "int", status: "Inferred", selected: true, levels: null },
          ],
        },
      ],
    }
    render(
      <ApiInputEditor
        config={config as Record<string, unknown>}
        onUpdate={vi.fn()}
        accentColor="#10b981"
        configPath="rating/config/quote_input/sample.json"
      />,
    )

    // Tables editor container — pinned by its existing data-testid.
    const tablesEditor = screen.getByTestId("api-input-tables")
    // Cache button — selected by accessible role + name because the nearby
    // optional-speed hint also mentions "cache as Parquet".
    const cacheButton = screen.getByRole("button", { name: /cache as parquet/i })

    // The cache button must NOT live inside the Tables editor section.
    expect(tablesEditor.contains(cacheButton)).toBe(false)

    // The cache button must appear in document order BEFORE the
    // Tables editor.
    const positionRelativeToCacheButton = cacheButton.compareDocumentPosition(tablesEditor)
    expect(positionRelativeToCacheButton & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })
})
