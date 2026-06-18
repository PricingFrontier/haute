/**
 * Render tests for ApiInputEditor.
 *
 * Tests: API banner, preview data label, FileBrowser with extensions filter,
 * cache button visibility, JsonCacheButton states
 * (initial, after build, error on failure).
 */
import { describe, it, expect, vi, afterEach, beforeEach } from "vitest"
import { render, screen, fireEvent, cleanup, waitFor, act } from "@testing-library/react"
import { useState } from "react"
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
        expect.objectContaining({ path: "$[:]", emit: true }),
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

// ─── W1.5 — path inputs: focus retention + commit-on-blur ──────────
//
// CODE_REVIEW (frontend editors): the TableBlock/ColumnRow React keys
// embedded the edited path (`${table.path}-${ti}` / `${col.path}-${ci}`),
// so every keystroke in a path input committed to config, round-tripped
// through the parent, changed the key, and remounted the row — the
// input lost focus after each character. Half-typed paths also polluted
// config (churning structuralVersion; a transiently empty path makes
// readV2 drop the whole table). These tests need a *stateful* harness
// that echoes onUpdate back into the config prop, exactly like
// NodePanel does — a plain vi.fn() onUpdate never round-trips and can
// never remount, which is why the original suite missed the defect.

/** Echoes onUpdate back into the `config` prop like NodePanel does. */
function StatefulHarness({
  initialConfig,
  onUpdateSpy,
}: {
  initialConfig: Record<string, unknown>
  onUpdateSpy: (keyOrUpdates: string | Record<string, unknown>, value?: unknown) => void
}) {
  const [config, setConfig] = useState(initialConfig)
  return (
    <ApiInputEditor
      config={config}
      onUpdate={(keyOrUpdates: string | Record<string, unknown>, value?: unknown) => {
        onUpdateSpy(keyOrUpdates, value)
        setConfig((prev) =>
          typeof keyOrUpdates === "string"
            ? { ...prev, [keyOrUpdates]: value }
            : { ...prev, ...keyOrUpdates },
        )
      }}
      accentColor="#10b981"
    />
  )
}

// No `path` key → no cache button / infer button → no async noise; the
// tables array alone makes the config v2-shaped.
const ONE_TABLE_ONE_COL = {
  tables: [
    {
      path: "$[*]",
      label: "policies",
      emit: true,
      columns: [
        {
          name: "policy_id",
          path: "$[*].policy_id",
          type: "int",
          status: "Inferred",
          selected: true,
        },
      ],
    },
  ],
}

const TWO_TABLES = {
  tables: [
    { path: "$[*]", label: "policies", emit: true, columns: [] },
    { path: "$[*].drivers[*]", label: "drivers", emit: false, columns: [] },
  ],
}

/** Dispatches each progressively-longer value at the CURRENTLY focused
 * element — like a real user, whose keystrokes land wherever focus is.
 * Under value-derived keys the first keystroke remounts the row and
 * focus falls to <body>, so subsequent keystrokes go nowhere. */
function typeSequence(values: string[]) {
  for (const v of values) {
    fireEvent.change(document.activeElement as Element, { target: { value: v } })
  }
}

describe("ApiInputEditor — W1.5 path inputs (focus retention, commit discipline)", () => {
  it("table path input keeps focus and accumulates keystrokes without remounting", () => {
    const onUpdateSpy = vi.fn()
    render(<StatefulHarness initialConfig={ONE_TABLE_ONE_COL} onUpdateSpy={onUpdateSpy} />)

    const input = screen.getByTestId("api-input-table-0-path") as HTMLInputElement
    input.focus()
    expect(document.activeElement).toBe(input)

    typeSequence(["$[*].", "$[*].q", "$[*].qu", "$[*].quotes[*]"])

    // Same DOM element — the row was never remounted…
    expect(screen.getByTestId("api-input-table-0-path")).toBe(input)
    // …focus never left it…
    expect(document.activeElement).toBe(input)
    // …and the keystrokes accumulated into the full string.
    expect(input.value).toBe("$[*].quotes[*]")
  })

  it("table path edits do NOT commit per keystroke; blur commits exactly once with the final value", () => {
    const onUpdateSpy = vi.fn()
    render(<StatefulHarness initialConfig={ONE_TABLE_ONE_COL} onUpdateSpy={onUpdateSpy} />)

    const input = screen.getByTestId("api-input-table-0-path") as HTMLInputElement
    input.focus()
    typeSequence(["$[*].", "$[*].q", "$[*].qu", "$[*].quotes[*]"])

    // No half-typed path ever reached the config.
    expect(onUpdateSpy).not.toHaveBeenCalled()

    fireEvent.blur(input)

    // Exactly one commit, carrying only the final value.
    expect(onUpdateSpy).toHaveBeenCalledTimes(1)
    const committedPaths = onUpdateSpy.mock.calls.map(
      (c) => (c[0] as { tables: { path: string }[] }).tables[0].path,
    )
    expect(committedPaths).toEqual(["$[*].quotes[*]"])
  })

  it("Enter commits the table path exactly once and keeps the element focused", () => {
    const onUpdateSpy = vi.fn()
    render(<StatefulHarness initialConfig={ONE_TABLE_ONE_COL} onUpdateSpy={onUpdateSpy} />)

    const input = screen.getByTestId("api-input-table-0-path") as HTMLInputElement
    input.focus()
    typeSequence(["$[*].x", "$[*].xs[*]"])
    expect(onUpdateSpy).not.toHaveBeenCalled()

    fireEvent.keyDown(input, { key: "Enter" })

    expect(onUpdateSpy).toHaveBeenCalledTimes(1)
    expect(
      (onUpdateSpy.mock.calls[0][0] as { tables: { path: string }[] }).tables[0].path,
    ).toBe("$[*].xs[*]")
    // Enter commits in place — same element, still focused, showing the
    // committed value.
    expect(screen.getByTestId("api-input-table-0-path")).toBe(input)
    expect(document.activeElement).toBe(input)
    expect(input.value).toBe("$[*].xs[*]")

    // A later blur must not double-commit the same value.
    fireEvent.blur(input)
    expect(onUpdateSpy).toHaveBeenCalledTimes(1)
  })

  it("column path input keeps focus through a paste-style edit and commits once on blur", () => {
    const onUpdateSpy = vi.fn()
    render(<StatefulHarness initialConfig={ONE_TABLE_ONE_COL} onUpdateSpy={onUpdateSpy} />)

    const input = screen.getByTestId("api-input-table-0-col-0-path") as HTMLInputElement
    input.focus()
    // Paste arrives as a single change event with the full replacement.
    fireEvent.change(input, { target: { value: "$[*].quote.policy_id" } })

    // No remount, no focus loss, no premature commit.
    expect(screen.getByTestId("api-input-table-0-col-0-path")).toBe(input)
    expect(document.activeElement).toBe(input)
    expect(input.value).toBe("$[*].quote.policy_id")
    expect(onUpdateSpy).not.toHaveBeenCalled()

    fireEvent.blur(input)
    expect(onUpdateSpy).toHaveBeenCalledTimes(1)
    const arg = onUpdateSpy.mock.calls[0][0] as {
      tables: { columns: { path: string }[] }[]
    }
    expect(arg.tables[0].columns[0].path).toBe("$[*].quote.policy_id")
  })

  it("blur after editing back to the committed value is a no-op (no churn commit)", () => {
    const onUpdateSpy = vi.fn()
    render(<StatefulHarness initialConfig={ONE_TABLE_ONE_COL} onUpdateSpy={onUpdateSpy} />)

    const input = screen.getByTestId("api-input-table-0-path") as HTMLInputElement
    input.focus()
    typeSequence(["$[*].x", "$[*]"])
    fireEvent.blur(input)

    // The draft equals the committed value — nothing to write; config
    // (and therefore structuralVersion downstream) must not churn.
    expect(onUpdateSpy).not.toHaveBeenCalled()
    expect(input.value).toBe("$[*]")
  })

  it("column-name inputs keep focus while typing and commit atomically on blur", () => {
    // PIN REVISION (W1.3, then W1.9): this test originally pinned BOTH
    // the table-label and column-name inputs committing per keystroke
    // (documenting then-current behaviour). The label half was revised
    // first (labels are handle ids — see the W1.3/W1.4 suite below).
    // The column-name half is now deliberately revised too (W1.9):
    // per-keystroke commits meant backspacing a name to empty committed
    // `name:""`, and `readV2` (apiInputSchema.ts) silently dropped the
    // whole column row — instant UI data loss the backend would also
    // 422 on (blank/duplicate column names are rejected by
    // `validate_v2_schema`). Column names now commit atomically on
    // blur/Enter with validation, exactly like labels and paths. The
    // focus-retention half of the original pin is unchanged.
    const onUpdateSpy = vi.fn()
    render(<StatefulHarness initialConfig={ONE_TABLE_ONE_COL} onUpdateSpy={onUpdateSpy} />)

    const name = screen.getByTestId("api-input-table-0-col-0-name") as HTMLInputElement
    name.focus()
    typeSequence(["policy_idx", "policy_idxy"])
    expect(screen.getByTestId("api-input-table-0-col-0-name")).toBe(name)
    expect(document.activeElement).toBe(name)
    expect(name.value).toBe("policy_idxy")
    // No intermediate name ever reached config…
    expect(onUpdateSpy).not.toHaveBeenCalled()

    fireEvent.blur(name)

    // …blur commits exactly once with the final value.
    expect(onUpdateSpy).toHaveBeenCalledTimes(1)
    expect(
      (onUpdateSpy.mock.calls[0][0] as { tables: { columns: { name: string }[] }[] })
        .tables[0].columns[0].name,
    ).toBe("policy_idxy")
  })

  it("removing the row above an in-progress path edit never leaks the draft into the surviving row", () => {
    // Positional keys mean the surviving row slides into index 0 and is
    // adopted by the component instance that held the dead row's draft.
    // The committed value must win: the stale draft is discarded, never
    // committed into the row that slid up.
    const onUpdateSpy = vi.fn()
    render(<StatefulHarness initialConfig={TWO_TABLES} onUpdateSpy={onUpdateSpy} />)

    const input = screen.getByTestId("api-input-table-0-path") as HTMLInputElement
    input.focus()
    // Half-typed draft in table 0's path — deliberately not blurred.
    fireEvent.change(input, { target: { value: "$[*].HALF" } })
    expect(onUpdateSpy).not.toHaveBeenCalled()

    fireEvent.click(screen.getByTestId("api-input-table-0-remove"))

    // Exactly one commit so far: the removal itself.
    expect(onUpdateSpy).toHaveBeenCalledTimes(1)
    const removal = onUpdateSpy.mock.calls[0][0] as { tables: { path: string }[] }
    expect(removal.tables.map((t) => t.path)).toEqual(["$[*].drivers[*]"])

    // The surviving row shows ITS OWN committed path, not the dead
    // row's half-typed draft…
    const survivor = screen.getByTestId("api-input-table-0-path") as HTMLInputElement
    expect(survivor.value).toBe("$[*].drivers[*]")

    // …and blurring it commits nothing (the stale draft is gone).
    fireEvent.blur(survivor)
    expect(onUpdateSpy).toHaveBeenCalledTimes(1)
  })
})

// ─── W1.3 / W1.4 — port labels: atomic commit + validation ─────────
//
// A table label IS the React Flow handle id and the backend port name
// (`_json_shred` keys runtime frames by raw label; codegen emits it as
// `connect(..., source_port=label)`). Two consequences:
//
//  - W1.3: committing per keystroke made every intermediate string a
//    live handle id — renaming a CONNECTED port destroyed its edges on
//    the first keystroke. Labels must commit atomically (blur/Enter).
//  - W1.4: blank/duplicate labels are hard-rejected by the backend on
//    save (`validate_v2_schema`), so the editor must refuse to commit
//    them and show why — never paper over with synthesized handles.

const TWO_EMIT_TABLES = {
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
      emit: true,
      columns: [
        { name: "driver_id", path: "$[*].drivers[*].driver_id", type: "int", status: "Inferred", selected: true },
      ],
    },
  ],
}

describe("ApiInputEditor — W1.3 port-label commits are atomic", () => {
  it("typing in a label input does not commit per keystroke; blur commits exactly once with the final value", () => {
    const onUpdateSpy = vi.fn()
    render(<StatefulHarness initialConfig={TWO_EMIT_TABLES} onUpdateSpy={onUpdateSpy} />)

    const input = screen.getByTestId("api-input-table-0-label") as HTMLInputElement
    input.focus()
    typeSequence(["quotes_a", "quotes_ab", "quotes_abc"])

    // No intermediate label ever became a live handle id.
    expect(onUpdateSpy).not.toHaveBeenCalled()
    // Focus and accumulation are unaffected by the buffering.
    expect(screen.getByTestId("api-input-table-0-label")).toBe(input)
    expect(document.activeElement).toBe(input)
    expect(input.value).toBe("quotes_abc")

    fireEvent.blur(input)

    // Exactly one commit — one onUpdate, one graph update, one
    // undo-meaningful entry — carrying only the final label.
    expect(onUpdateSpy).toHaveBeenCalledTimes(1)
    const arg = onUpdateSpy.mock.calls[0][0] as { tables: { label: string }[] }
    expect(arg.tables.map((t) => t.label)).toEqual(["quotes_abc", "drivers"])
  })

  it("Enter commits the label exactly once and a later blur does not double-commit", () => {
    const onUpdateSpy = vi.fn()
    render(<StatefulHarness initialConfig={TWO_EMIT_TABLES} onUpdateSpy={onUpdateSpy} />)

    const input = screen.getByTestId("api-input-table-0-label") as HTMLInputElement
    input.focus()
    typeSequence(["quotes"])
    fireEvent.keyDown(input, { key: "Enter" })

    expect(onUpdateSpy).toHaveBeenCalledTimes(1)
    expect(
      (onUpdateSpy.mock.calls[0][0] as { tables: { label: string }[] }).tables[0].label,
    ).toBe("quotes")
    expect(input.value).toBe("quotes")

    fireEvent.blur(input)
    expect(onUpdateSpy).toHaveBeenCalledTimes(1)
  })

  it("blur after editing back to the committed label is a no-op (no churn commit)", () => {
    const onUpdateSpy = vi.fn()
    render(<StatefulHarness initialConfig={TWO_EMIT_TABLES} onUpdateSpy={onUpdateSpy} />)

    const input = screen.getByTestId("api-input-table-0-label") as HTMLInputElement
    input.focus()
    typeSequence(["policiesX", "policies"])
    fireEvent.blur(input)

    expect(onUpdateSpy).not.toHaveBeenCalled()
    expect(input.value).toBe("policies")
  })
})

describe("ApiInputEditor — W1.4 label validation (blank / duplicate / sanitised collision)", () => {
  it("a blanked label shows validation on blur and commits NOTHING (no port_<idx> ever reaches config)", () => {
    const onUpdateSpy = vi.fn()
    render(<StatefulHarness initialConfig={TWO_EMIT_TABLES} onUpdateSpy={onUpdateSpy} />)

    const input = screen.getByTestId("api-input-table-0-label") as HTMLInputElement
    input.focus()
    fireEvent.change(input, { target: { value: "" } })
    fireEvent.blur(input)

    // Refused: the blank label never reached config, so the port and its
    // edges are untouched and no synthesized handle can exist anywhere.
    expect(onUpdateSpy).not.toHaveBeenCalled()
    // Visible validation, and the draft is kept so the user sees what
    // was rejected.
    expect(screen.getByTestId("api-input-table-0-label-error")).toBeTruthy()
    expect(input.value).toBe("")
    expect(input.getAttribute("aria-invalid")).toBe("true")
  })

  it("a duplicate label shows validation and refuses to commit (no __<idx> disambiguation)", () => {
    const onUpdateSpy = vi.fn()
    render(<StatefulHarness initialConfig={TWO_EMIT_TABLES} onUpdateSpy={onUpdateSpy} />)

    const input = screen.getByTestId("api-input-table-0-label") as HTMLInputElement
    input.focus()
    fireEvent.change(input, { target: { value: "drivers" } })
    fireEvent.blur(input)

    expect(onUpdateSpy).not.toHaveBeenCalled()
    const error = screen.getByTestId("api-input-table-0-label-error")
    expect(error.textContent).toMatch(/drivers/)
  })

  it("a sanitised-form collision (backend B2) is rejected before commit", () => {
    // "drivers.x" sanitises to "drivers_x"; if another table is labelled
    // "drivers_x" the backend rejects the save (both would write the
    // same parquet). Surface that here, not as a 422 later.
    const config = {
      tables: [
        { path: "$[*]", label: "policies", emit: true, columns: [] },
        { path: "$[*].d[*]", label: "drivers_x", emit: true, columns: [] },
      ],
    }
    const onUpdateSpy = vi.fn()
    render(<StatefulHarness initialConfig={config} onUpdateSpy={onUpdateSpy} />)

    const input = screen.getByTestId("api-input-table-0-label") as HTMLInputElement
    input.focus()
    fireEvent.change(input, { target: { value: "drivers.x" } })
    fireEvent.blur(input)

    expect(onUpdateSpy).not.toHaveBeenCalled()
    expect(screen.getByTestId("api-input-table-0-label-error").textContent).toMatch(/drivers_x/)
  })

  it("fixing an invalid draft to a valid label clears the error and commits once", () => {
    const onUpdateSpy = vi.fn()
    render(<StatefulHarness initialConfig={TWO_EMIT_TABLES} onUpdateSpy={onUpdateSpy} />)

    const input = screen.getByTestId("api-input-table-0-label") as HTMLInputElement
    input.focus()
    fireEvent.change(input, { target: { value: "" } })
    fireEvent.blur(input)
    expect(screen.getByTestId("api-input-table-0-label-error")).toBeTruthy()
    expect(onUpdateSpy).not.toHaveBeenCalled()

    fireEvent.change(input, { target: { value: "quotes" } })
    fireEvent.blur(input)

    expect(onUpdateSpy).toHaveBeenCalledTimes(1)
    expect(
      (onUpdateSpy.mock.calls[0][0] as { tables: { label: string }[] }).tables[0].label,
    ).toBe("quotes")
    expect(screen.queryByTestId("api-input-table-0-label-error")).toBeNull()
  })

  it("an already-committed duplicate (e.g. loaded from disk) surfaces validation without any interaction", () => {
    // The editor can't prevent invalid configs arriving from legacy
    // files or an infer-merge; it must SHOW the problem (the backend
    // will reject the save until it's fixed).
    const config = {
      tables: [
        { path: "$[*]", label: "dup", emit: true, columns: [] },
        { path: "$[*].b[*]", label: "dup", emit: true, columns: [] },
      ],
    }
    render(<StatefulHarness initialConfig={config} onUpdateSpy={vi.fn()} />)

    expect(screen.getByTestId("api-input-table-0-label-error")).toBeTruthy()
    expect(screen.getByTestId("api-input-table-1-label-error")).toBeTruthy()
  })
})

describe("ApiInputEditor — blank paths are refused, never silently destructive", () => {
  // Folded W1.5 follow-up: PathInput committed "" on a deliberate
  // clear+blur, and `readV2` then silently dropped the whole table (or
  // column) from config. Invalid editor state must surface as
  // validation — never as silent data loss.

  it("clearing a TABLE path and blurring refuses the commit, shows validation, and keeps the table", () => {
    const onUpdateSpy = vi.fn()
    render(<StatefulHarness initialConfig={ONE_TABLE_ONE_COL} onUpdateSpy={onUpdateSpy} />)

    const input = screen.getByTestId("api-input-table-0-path") as HTMLInputElement
    input.focus()
    fireEvent.change(input, { target: { value: "" } })
    fireEvent.blur(input)

    // The destructive commit never happened…
    expect(onUpdateSpy).not.toHaveBeenCalled()
    // …the table row is still there…
    expect(screen.getByTestId("api-input-table-0")).toBeTruthy()
    // …and the user can see why the clear was refused.
    expect(screen.getByTestId("api-input-table-0-path-error")).toBeTruthy()
  })

  it("clearing a COLUMN path and blurring refuses the commit and keeps the column", () => {
    const onUpdateSpy = vi.fn()
    render(<StatefulHarness initialConfig={ONE_TABLE_ONE_COL} onUpdateSpy={onUpdateSpy} />)

    const input = screen.getByTestId("api-input-table-0-col-0-path") as HTMLInputElement
    input.focus()
    fireEvent.change(input, { target: { value: "   " } })
    fireEvent.blur(input)

    expect(onUpdateSpy).not.toHaveBeenCalled()
    expect(screen.getByTestId("api-input-table-0-col-0")).toBeTruthy()
    expect(screen.getByTestId("api-input-table-0-col-0-path-error")).toBeTruthy()
  })

  it("typing a real path after a refused clear commits normally and clears the error", () => {
    const onUpdateSpy = vi.fn()
    render(<StatefulHarness initialConfig={ONE_TABLE_ONE_COL} onUpdateSpy={onUpdateSpy} />)

    const input = screen.getByTestId("api-input-table-0-path") as HTMLInputElement
    input.focus()
    fireEvent.change(input, { target: { value: "" } })
    fireEvent.blur(input)
    expect(onUpdateSpy).not.toHaveBeenCalled()

    fireEvent.change(input, { target: { value: "$[*].quotes[*]" } })
    fireEvent.blur(input)

    expect(onUpdateSpy).toHaveBeenCalledTimes(1)
    expect(
      (onUpdateSpy.mock.calls[0][0] as { tables: { path: string }[] }).tables[0].path,
    ).toBe("$[*].quotes[*]")
    expect(screen.queryByTestId("api-input-table-0-path-error")).toBeNull()
  })
})

// ─── W1.9 — column names: atomic commit + validation ───────────────
//
// Same destructive class as the blank-path defect: column-name inputs
// committed per keystroke, so backspacing a name to empty committed
// `name:""` → `readV2` (apiInputSchema.ts) dropped the column row
// INSTANTLY (silent UI data loss; the backend would also 422 the save —
// `validate_v2_schema` rejects blank column names and duplicate names
// WITHIN a table, see `seen_col_names` scoped per table). Names now
// commit on blur/Enter and invalid candidates are refused with visible
// validation.

const TWO_COLS_AND_SECOND_TABLE = {
  tables: [
    {
      path: "$[*]",
      label: "policies",
      emit: true,
      columns: [
        { name: "policy_id", path: "$[*].policy_id", type: "int", status: "Inferred", selected: true },
        { name: "premium", path: "$[*].premium", type: "float", status: "Inferred", selected: true },
      ],
    },
    {
      path: "$[*].drivers[*]",
      label: "drivers",
      emit: true,
      columns: [
        { name: "driver_id", path: "$[*].drivers[*].driver_id", type: "int", status: "Inferred", selected: true },
      ],
    },
  ],
}

describe("ApiInputEditor — W1.9 column-name validation (blank / duplicate)", () => {
  it("backspacing a name to empty never deletes the column: commit refused, row survives, error shown", () => {
    const onUpdateSpy = vi.fn()
    render(<StatefulHarness initialConfig={ONE_TABLE_ONE_COL} onUpdateSpy={onUpdateSpy} />)

    const name = screen.getByTestId("api-input-table-0-col-0-name") as HTMLInputElement
    name.focus()
    // Backspace the committed "policy_id" down to nothing, then blur.
    typeSequence(["policy_i", "policy", "p", ""])
    fireEvent.blur(name)

    // Nothing destructive reached config…
    expect(onUpdateSpy).not.toHaveBeenCalled()
    // …the column row is still there…
    expect(screen.getByTestId("api-input-table-0-col-0")).toBeTruthy()
    // …with a visible reason for the refusal.
    expect(screen.getByTestId("api-input-table-0-col-0-name-error")).toBeTruthy()
    expect(name.getAttribute("aria-invalid")).toBe("true")
  })

  it("a duplicate name within the SAME table is refused with visible validation", () => {
    const onUpdateSpy = vi.fn()
    render(
      <StatefulHarness initialConfig={TWO_COLS_AND_SECOND_TABLE} onUpdateSpy={onUpdateSpy} />,
    )

    const name = screen.getByTestId("api-input-table-0-col-1-name") as HTMLInputElement
    name.focus()
    fireEvent.change(name, { target: { value: "policy_id" } })
    fireEvent.blur(name)

    expect(onUpdateSpy).not.toHaveBeenCalled()
    expect(
      screen.getByTestId("api-input-table-0-col-1-name-error").textContent,
    ).toMatch(/policy_id/)
  })

  it("the duplicate rule is scoped PER TABLE, exactly like the backend's seen_col_names", () => {
    // `validate_v2_schema` resets its `seen_col_names` set per table:
    // the same column name in two DIFFERENT tables is legal (each table
    // is its own frame). The editor must not over-reject.
    const onUpdateSpy = vi.fn()
    render(
      <StatefulHarness initialConfig={TWO_COLS_AND_SECOND_TABLE} onUpdateSpy={onUpdateSpy} />,
    )

    // Rename drivers.driver_id → policy_id: collides with a name in
    // table 0, but NOT within its own table → commits cleanly.
    const name = screen.getByTestId("api-input-table-1-col-0-name") as HTMLInputElement
    name.focus()
    fireEvent.change(name, { target: { value: "policy_id" } })
    fireEvent.blur(name)

    expect(onUpdateSpy).toHaveBeenCalledTimes(1)
    const arg = onUpdateSpy.mock.calls[0][0] as {
      tables: { columns: { name: string }[] }[]
    }
    expect(arg.tables[1].columns[0].name).toBe("policy_id")
    expect(screen.queryByTestId("api-input-table-1-col-0-name-error")).toBeNull()
  })

  it("a valid rename commits exactly once on Enter, and a later blur does not double-commit", () => {
    const onUpdateSpy = vi.fn()
    render(<StatefulHarness initialConfig={ONE_TABLE_ONE_COL} onUpdateSpy={onUpdateSpy} />)

    const name = screen.getByTestId("api-input-table-0-col-0-name") as HTMLInputElement
    name.focus()
    typeSequence(["policy_ref"])
    expect(onUpdateSpy).not.toHaveBeenCalled()

    fireEvent.keyDown(name, { key: "Enter" })
    expect(onUpdateSpy).toHaveBeenCalledTimes(1)
    expect(
      (onUpdateSpy.mock.calls[0][0] as { tables: { columns: { name: string }[] }[] })
        .tables[0].columns[0].name,
    ).toBe("policy_ref")

    fireEvent.blur(name)
    expect(onUpdateSpy).toHaveBeenCalledTimes(1)
  })

  it("fixing an invalid name draft to a valid one clears the error and commits once", () => {
    const onUpdateSpy = vi.fn()
    render(
      <StatefulHarness initialConfig={TWO_COLS_AND_SECOND_TABLE} onUpdateSpy={onUpdateSpy} />,
    )

    const name = screen.getByTestId("api-input-table-0-col-1-name") as HTMLInputElement
    name.focus()
    fireEvent.change(name, { target: { value: "policy_id" } })
    fireEvent.blur(name)
    expect(screen.getByTestId("api-input-table-0-col-1-name-error")).toBeTruthy()
    expect(onUpdateSpy).not.toHaveBeenCalled()

    fireEvent.change(name, { target: { value: "policy_id_2" } })
    fireEvent.blur(name)

    expect(onUpdateSpy).toHaveBeenCalledTimes(1)
    expect(
      (onUpdateSpy.mock.calls[0][0] as { tables: { columns: { name: string }[] }[] })
        .tables[0].columns[1].name,
    ).toBe("policy_id_2")
    expect(screen.queryByTestId("api-input-table-0-col-1-name-error")).toBeNull()
  })
})
