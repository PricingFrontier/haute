/**
 * COMMIT 3 — the frontend INPUT path-grammar validator wired into ApiInputEditor
 * (PATH_GRAMMAR.md). Previously the table/column path inputs only required a
 * non-blank value; the grammar was backend-only and surfaced as a save-time 422.
 * These tests pin that an invalid INPUT path is now caught IN-EDITOR (refused
 * commit + visible error), and that a valid non-canonical path commits AND is
 * persistently highlighted (non-modal, §4) — including a path that arrived from
 * schema inference, surfaced on render with no interaction.
 */
import { describe, it, expect, vi, afterEach } from "vitest"
import { render, screen, fireEvent, cleanup } from "@testing-library/react"
import { useState } from "react"
import ApiInputEditor from "../../panels/editors/ApiInputEditor"

afterEach(cleanup)

// Same mock surface as the main ApiInputEditor suite: stub the API client and
// the schema-fetching shared components so the editor renders without a server.
vi.mock("../../panels/editors/_shared", async () => {
  const actual = await vi.importActual("../../panels/editors/_shared")
  return {
    ...actual,
    FileBrowser: () => <div data-testid="file-browser" />,
    SchemaPreview: () => <div data-testid="schema-preview" />,
  }
})

vi.mock("../../api/client", () => ({
  fetchDatabricksSchema: vi.fn(),
  buildJsonCache: vi.fn(),
  getJsonCacheProgress: vi.fn().mockResolvedValue({ active: false }),
  getJsonCacheStatus: vi.fn().mockResolvedValue({ cached: false }),
  getJsonCacheStatusForSchema: vi.fn().mockResolvedValue({ cached: false }),
  deleteJsonCache: vi.fn(),
  cancelJsonCache: vi.fn(),
  inferJsonCacheSchema: vi.fn(),
  ApiError: class ApiError extends Error {},
}))

vi.mock("../../hooks/useSchemaFetch", () => ({
  useSchemaFetch: () => ({ schema: null, setSchema: vi.fn(), loading: false, fetchForPath: vi.fn() }),
}))

function Harness({ initialConfig }: { initialConfig: Record<string, unknown> }) {
  const [config, setConfig] = useState(initialConfig)
  return (
    <ApiInputEditor
      config={config}
      onUpdate={(keyOrUpdates: string | Record<string, unknown>, value?: unknown) => {
        setConfig((prev) =>
          typeof keyOrUpdates === "string"
            ? { ...prev, [keyOrUpdates]: value }
            : { ...prev, ...keyOrUpdates },
        )
        return { ok: true as const }
      }}
      accentColor="#10b981"
    />
  )
}

// No `path` key → no cache/infer async noise; the tables array makes it v2.
const ONE_TABLE_ONE_COL = {
  tables: [
    {
      path: "$[:].drivers[:]",
      label: "drivers",
      emit: true,
      columns: [
        { name: "driver_id", path: "$[:].drivers[:].driver_id", type: "int", status: "Inferred", selected: true },
      ],
    },
  ],
}

function commit(testId: string, value: string) {
  const input = screen.getByTestId(testId) as HTMLInputElement
  input.focus()
  fireEvent.change(input, { target: { value } })
  fireEvent.blur(input)
}

describe("ApiInputEditor — INPUT path grammar is wired in-editor (not a save-time 422)", () => {
  it("refuses an ungrammatical TABLE path with a visible error (e.g. an index selector)", () => {
    render(<Harness initialConfig={ONE_TABLE_ONE_COL} />)
    commit("api-input-table-0-path", "$[:].drivers[0]")
    expect(screen.getByTestId("api-input-table-0-path-error")).toBeTruthy()
    // The committed (refused) draft is still shown — nothing destructive landed.
    expect((screen.getByTestId("api-input-table-0-path") as HTMLInputElement).value).toBe(
      "$[:].drivers[0]",
    )
  })

  it("refuses a TABLE path that does not end at an array '[:]'", () => {
    render(<Harness initialConfig={ONE_TABLE_ONE_COL} />)
    commit("api-input-table-0-path", "$[:].drivers")
    expect(screen.getByTestId("api-input-table-0-path-error")).toBeTruthy()
  })

  it("accepts a grammatical TABLE path (commits, no error)", () => {
    render(<Harness initialConfig={ONE_TABLE_ONE_COL} />)
    commit("api-input-table-0-path", "$[:].claims[:]")
    expect(screen.queryByTestId("api-input-table-0-path-error")).toBeNull()
    expect((screen.getByTestId("api-input-table-0-path") as HTMLInputElement).value).toBe(
      "$[:].claims[:]",
    )
  })

  it("refuses an ungrammatical COLUMN path (descendant '..')", () => {
    render(<Harness initialConfig={ONE_TABLE_ONE_COL} />)
    commit("api-input-table-0-col-0-path", "$[:].drivers[:]..driver_id")
    expect(screen.getByTestId("api-input-table-0-col-0-path-error")).toBeTruthy()
  })

  it("refuses a COLUMN path that names no leaf (ends at an array)", () => {
    render(<Harness initialConfig={ONE_TABLE_ONE_COL} />)
    commit("api-input-table-0-col-0-path", "$[:].drivers[:]")
    expect(screen.getByTestId("api-input-table-0-col-0-path-error")).toBeTruthy()
  })
})

// A config whose paths arrived from schema inference already non-canonical (a
// real source key spelled with brackets / a non-identifier). The §4 surface must
// highlight these the moment the editor renders — no interaction needed.
const INFERRED_NON_CANONICAL = {
  tables: [
    {
      path: "$[:]['claims'][:]",
      label: "claims",
      emit: true,
      columns: [
        { name: "ref", path: "$[:]['claims'][:]['ref']", type: "str", status: "Inferred", selected: true },
      ],
    },
  ],
}

describe("ApiInputEditor — non-canonical paths are persistently highlighted (§4, non-modal)", () => {
  it("a bracket-form TABLE path commits AND is flagged with its canonical form", () => {
    render(<Harness initialConfig={ONE_TABLE_ONE_COL} />)
    commit("api-input-table-0-path", "$[:]['claims'][:]")
    // It committed (no error)…
    expect(screen.queryByTestId("api-input-table-0-path-error")).toBeNull()
    // …and the persistent, non-modal highlight names the canonical spelling.
    expect(screen.getByTestId("api-input-table-0-path-noncanonical").textContent).toBe(
      "Non-canonical path — assembles identically. Canonical form: $[:].claims[:]",
    )
  })

  it("a canonical TABLE path commits with NO highlight", () => {
    render(<Harness initialConfig={ONE_TABLE_ONE_COL} />)
    commit("api-input-table-0-path", "$[:].claims[:]")
    expect(screen.queryByTestId("api-input-table-0-path-noncanonical")).toBeNull()
  })

  it("highlights inference-introduced non-canonical fields on render, no interaction", () => {
    render(<Harness initialConfig={INFERRED_NON_CANONICAL} />)
    // The table path (bracket identifier) names its canonical form…
    expect(screen.getByTestId("api-input-table-0-path-noncanonical").textContent).toBe(
      "Non-canonical path — assembles identically. Canonical form: $[:].claims[:]",
    )
    // …and the column path (also bracket identifiers) is flagged too.
    expect(screen.getByTestId("api-input-table-0-col-0-path-noncanonical")).toBeTruthy()
  })
})
