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

  it("label and column-name inputs do not share the value-derived-key defect (per-keystroke commits retained, focus kept)", () => {
    // Evidence for the review's verification pass: keys never embedded
    // label/name, so these inputs re-render in place. They keep their
    // original per-keystroke commit behaviour — deliberately unchanged.
    const onUpdateSpy = vi.fn()
    render(<StatefulHarness initialConfig={ONE_TABLE_ONE_COL} onUpdateSpy={onUpdateSpy} />)

    const label = screen.getByTestId("api-input-table-0-label") as HTMLInputElement
    label.focus()
    typeSequence(["policies_a", "policies_ab", "policies_abc"])
    expect(screen.getByTestId("api-input-table-0-label")).toBe(label)
    expect(document.activeElement).toBe(label)
    expect(label.value).toBe("policies_abc")
    // Per-keystroke commits flowed through — one per change event.
    expect(onUpdateSpy).toHaveBeenCalledTimes(3)

    onUpdateSpy.mockClear()
    const name = screen.getByTestId("api-input-table-0-col-0-name") as HTMLInputElement
    name.focus()
    typeSequence(["policy_idx", "policy_idxy"])
    expect(screen.getByTestId("api-input-table-0-col-0-name")).toBe(name)
    expect(document.activeElement).toBe(name)
    expect(name.value).toBe("policy_idxy")
    expect(onUpdateSpy).toHaveBeenCalledTimes(2)
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
