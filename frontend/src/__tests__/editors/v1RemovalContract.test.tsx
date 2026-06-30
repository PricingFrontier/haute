/**
 * Frontend contract tests for the v1-removal pivot (commit 5.5).
 *
 * Per the handover, written FIRST as failing tests (strict TDD).
 * Pairs with `tests/test_v1_removal_contract.py` for the backend half.
 *
 * Test IDs:
 *  - T2  — classifyConfig with v1 surface returns kind:"empty"; no banner
 *  - T5  — ApiInputEditor sends volatile_schema in Cache build POST
 *  - T9/T10 — Cache button inactive + visually distinct in no-op states
 *  - T21 — legacyToV2 not exported from apiInputSchema.ts
 *  - T22 — data-testid="api-input-migration-banner" not present in DOM
 */
import { describe, it, expect, vi, afterEach, beforeEach } from "vitest"
import { render, screen, cleanup, fireEvent, waitFor } from "@testing-library/react"

import ApiInputEditor from "../../panels/editors/ApiInputEditor"

afterEach(cleanup)

// ─── api/client mock — capture cache POST args ───────────────────────

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

vi.mock("../../panels/editors/_shared", async () => {
  const actual = await vi.importActual("../../panels/editors/_shared")
  return {
    ...(actual as object),
    FileBrowser: ({ onSelect }: { onSelect: (p: string) => void }) => (
      <button data-testid="select-file" onClick={() => onSelect("test.json")}>Select</button>
    ),
    SchemaPreview: ({ schema }: { schema: unknown }) => (
      <div data-testid="schema-preview">{schema ? "loaded" : "none"}</div>
    ),
  }
})

vi.mock("../../hooks/useSchemaFetch", () => ({
  useSchemaFetch: (initialPath?: string) => ({
    schema: initialPath ? { columns: [], preview: [], row_count: 0 } : null,
    setSchema: vi.fn(),
    loading: false,
    fetchForPath: vi.fn(),
  }),
}))

beforeEach(() => {
  mockBuildJsonCache.mockReset().mockResolvedValue({
    path: ".haute_cache/x.parquet",
    data_path: "test.json",
    row_count: 1,
    column_count: 1,
    columns: {},
    size_bytes: 100,
    cached_at: Date.now() / 1000,
    cache_seconds: 0.1,
  })
  mockGetJsonCacheStatus.mockReset().mockResolvedValue({ cached: false })
  mockGetJsonCacheStatusForSchema.mockReset().mockResolvedValue({ cached: false })
  mockGetJsonCacheProgress.mockReset().mockResolvedValue({ active: false })
  mockDeleteJsonCache.mockReset()
  mockCancelJsonCache.mockReset()
  mockInferJsonCacheSchema.mockReset().mockResolvedValue({
    tables: [
      { path: "$[:]", label: "quotes", emit: true, columns: [{ name: "id", path: "$[:].id", type: "str" }] },
    ],
  })
})

const DEFAULT_PROPS = {
  onUpdate: vi.fn(),
  accentColor: "#10b981",
  configPath: "config/quote_input/quotes.json",
}

const V1_LIKE_CONFIG = {
  path: "test.json",
  flattenSchema: {
    policy_details: { policy_number: "str", premium: "float" },
  },
}

const V2_TWO_TABLE_CONFIG = {
  path: "test.json",
  tables: [
    {
      path: "$[:]",
      label: "quotes",
      emit: true,
      columns: [{ name: "quote_id", path: "$[:].quote_id", type: "str" as const, selected: true }],
    },
    {
      path: "$[:].drivers[:]",
      label: "drivers",
      emit: true,
      columns: [
        { name: "id", path: "$[:].drivers[:].id", type: "str" as const, selected: true },
        { name: "name", path: "$[:].drivers[:].name", type: "str" as const, selected: true },
      ],
    },
  ],
}


// ─── T2 — classifyConfig + no banner with v1 config ──────────────────

describe("T2 — classifyConfig + no migration banner with v1 config", () => {
  it("classifyConfig with v1 flattenSchema returns kind:'empty' (no v1 kind exists)", async () => {
    const mod = await import("../../panels/editors/apiInputSchema")
    const result = mod.classifyConfig(V1_LIKE_CONFIG)
    // After v1 removal, classifyConfig has no "v1" kind. v1 surfaces
    // are treated as empty (user clicks Infer Tables to populate v2).
    expect(result.kind).toBe("empty")
  })

  it("does not render a migration banner when given a v1 config", () => {
    render(<ApiInputEditor {...DEFAULT_PROPS} config={V1_LIKE_CONFIG} />)
    expect(
      screen.queryByTestId("api-input-migration-banner"),
    ).not.toBeInTheDocument()
  })
})


// ─── T5 — ApiInputEditor sends volatile_schema in Cache POST ─────────

describe("T5 — ApiInputEditor sends volatile_schema in Cache build POST", () => {
  it("invokes buildJsonCache with volatile_schema matching in-memory tables[]", async () => {
    render(<ApiInputEditor {...DEFAULT_PROPS} config={V2_TWO_TABLE_CONFIG} />)

    const cacheBtn = await screen.findByRole("button", { name: /cache as parquet/i })
    fireEvent.click(cacheBtn)

    await waitFor(() => {
      expect(mockBuildJsonCache).toHaveBeenCalledTimes(1)
    })
    const callArgs = mockBuildJsonCache.mock.calls[0]
    // Payload is the first argument by convention (see api/client.ts).
    const payload = callArgs[0] as Record<string, unknown>
    expect(payload).toHaveProperty("volatile_schema")
    // The volatile_schema carries the editor's in-memory tables[].
    const volatile = payload.volatile_schema as { tables?: unknown[] }
    expect(Array.isArray(volatile?.tables)).toBe(true)
    expect(volatile.tables).toHaveLength(2)
    // And NOT the v1 inline-schema field.
    expect(payload).not.toHaveProperty("flatten_schema")
  })
})


// ─── T9/T10 combined — Cache button inactive states ──────────────────

describe("T9/T10 — Cache button inactive + visually distinct in no-op states", () => {
  it("is inactive when there is no schema source (empty config)", async () => {
    render(<ApiInputEditor {...DEFAULT_PROPS} config={{ path: "test.json" }} />)
    const cacheBtn = await screen.findByRole("button", { name: /cache as parquet/i })
    // Disabled-or-equivalent: HTMLButtonElement.disabled is true OR
    // the element is rendered with aria-disabled="true" / has an
    // explicit "inactive" class. The contract per handover: "the
    // rendered element differs from the active state".
    expect(
      (cacheBtn as HTMLButtonElement).disabled ||
        cacheBtn.getAttribute("aria-disabled") === "true",
    ).toBe(true)
  })

  it("is inactive when no tables have emit:true", async () => {
    const cfg_no_emit = {
      path: "test.json",
      tables: [
        {
          path: "$[:]",
          label: "quotes",
          emit: false,  // <- zero emit:true tables
          columns: [{ name: "quote_id", path: "$[:].quote_id", type: "str" as const }],
        },
      ],
    }
    render(<ApiInputEditor {...DEFAULT_PROPS} config={cfg_no_emit} />)
    const cacheBtn = await screen.findByRole("button", { name: /cache as parquet/i })
    expect(
      (cacheBtn as HTMLButtonElement).disabled ||
        cacheBtn.getAttribute("aria-disabled") === "true",
    ).toBe(true)
  })

  it("click does not POST when button is in the inactive state", async () => {
    render(<ApiInputEditor {...DEFAULT_PROPS} config={{ path: "test.json" }} />)
    const cacheBtn = await screen.findByRole("button", { name: /cache as parquet/i })
    fireEvent.click(cacheBtn)
    // Give async handler a chance to fire.
    await new Promise((r) => setTimeout(r, 50))
    expect(mockBuildJsonCache).not.toHaveBeenCalled()
  })

  it("IS active when both schema source AND emit:true exist", async () => {
    render(<ApiInputEditor {...DEFAULT_PROPS} config={V2_TWO_TABLE_CONFIG} />)
    const cacheBtn = await screen.findByRole("button", { name: /cache as parquet/i })
    expect(
      (cacheBtn as HTMLButtonElement).disabled ||
        cacheBtn.getAttribute("aria-disabled") === "true",
    ).toBe(false)
  })
})


// ─── T21 — legacyToV2 not exported ───────────────────────────────────

describe("T21 — legacyToV2 not exported from apiInputSchema.ts", () => {
  it("does not export `legacyToV2`", async () => {
    const mod = await import("../../panels/editors/apiInputSchema")
    expect("legacyToV2" in mod).toBe(false)
  })
})


// ─── T22 — migration banner data-testid not in DOM under any config ──

describe("T22 — api-input-migration-banner not present in DOM", () => {
  it("absent for empty config", () => {
    render(<ApiInputEditor {...DEFAULT_PROPS} config={{}} />)
    expect(
      screen.queryByTestId("api-input-migration-banner"),
    ).not.toBeInTheDocument()
  })

  it("absent for path-only config", () => {
    render(<ApiInputEditor {...DEFAULT_PROPS} config={{ path: "test.json" }} />)
    expect(
      screen.queryByTestId("api-input-migration-banner"),
    ).not.toBeInTheDocument()
  })

  it("absent for v1-style config (flattenSchema-only)", () => {
    render(<ApiInputEditor {...DEFAULT_PROPS} config={V1_LIKE_CONFIG} />)
    expect(
      screen.queryByTestId("api-input-migration-banner"),
    ).not.toBeInTheDocument()
  })

  it("absent for v2 config", () => {
    render(<ApiInputEditor {...DEFAULT_PROPS} config={V2_TWO_TABLE_CONFIG} />)
    expect(
      screen.queryByTestId("api-input-migration-banner"),
    ).not.toBeInTheDocument()
  })

  it("absent for corrupt-mix config (tables + flattenSchema)", () => {
    render(
      <ApiInputEditor
        {...DEFAULT_PROPS}
        config={{ ...V2_TWO_TABLE_CONFIG, flattenSchema: { x: "str" } } as Record<string, unknown>}
      />,
    )
    expect(
      screen.queryByTestId("api-input-migration-banner"),
    ).not.toBeInTheDocument()
  })
})
