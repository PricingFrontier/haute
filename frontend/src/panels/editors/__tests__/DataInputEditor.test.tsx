import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react"
import type { IoCapabilityGroup } from "../../../api/types"

vi.mock("../../../api/client", () => ({
  ApiError: class ApiError extends Error {},
  fetchSchema: vi.fn(),
  fetchIoCapabilities: vi.fn(),
  listFiles: vi.fn(() => Promise.resolve({ items: [] })),
  getWarehouses: vi.fn(() => Promise.resolve({ warehouses: [] })),
  getCatalogs: vi.fn(() => Promise.resolve({ catalogs: [] })),
  getSchemas: vi.fn(() => Promise.resolve({ schemas: [] })),
  getTables: vi.fn(() => Promise.resolve({ tables: [] })),
  buildInputCache: vi.fn(),
  getInputCacheStatus: vi.fn(),
  getInputCacheJob: vi.fn(),
  cancelInputCacheJob: vi.fn(),
  clearInputCache: vi.fn(),
}))

vi.mock("../CodeEditor", () => ({
  CodeEditor: ({
    defaultValue,
    onChange,
  }: {
    defaultValue: string
    onChange: (value: string) => void
  }) => (
    <textarea
      aria-label="Polars code"
      value={defaultValue}
      onChange={(event) => onChange(event.target.value)}
    />
  ),
}))

import {
  buildInputCache,
  fetchSchema,
  fetchIoCapabilities,
  getInputCacheJob,
  getInputCacheStatus,
} from "../../../api/client"
import DataInputEditor from "../DataInputEditor"
import { resetIoCapabilitiesCacheForTests } from "../_ioFormats"

const groups: IoCapabilityGroup[] = [
  {
    name: "file",
    label: "File",
    input_available: true,
    output_available: true,
    cache_modes: ["direct", "snapshot"],
    input_fields: [
      { name: "path", label: "Path", kind: "path", required: true },
    ],
    output_fields: [],
    formats: [
      {
        name: "csv",
        label: "CSV",
        group: "file",
        extensions: [".csv"],
        unstable: false,
        input: {
          modes: ["scan", "read"],
          arguments: { scan: ["separator"], read: ["separator"] },
          engines_missing: [],
          direct_bounded: true,
          needs_schema_when_bounded: true,
          snapshot_build: "bounded",
          cached_read: true,
        },
        output: null,
      },
      {
        name: "json",
        label: "JSON",
        group: "file",
        extensions: [".json"],
        unstable: false,
        input: {
          modes: ["read"],
          arguments: { read: [] },
          engines_missing: [],
          direct_bounded: false,
          needs_schema_when_bounded: false,
          snapshot_build: "admitted_eager",
          cached_read: true,
        },
        output: null,
      },
      {
        name: "excel",
        label: "Excel",
        group: "file",
        extensions: [".xlsx"],
        unstable: false,
        input: {
          modes: ["read"],
          arguments: { read: [] },
          engines_missing: ["fastexcel"],
          direct_bounded: false,
          needs_schema_when_bounded: false,
          snapshot_build: "admitted_eager",
          cached_read: true,
        },
        output: null,
      },
    ],
  },
  {
    name: "database",
    label: "Database",
    input_available: true,
    output_available: true,
    cache_modes: ["snapshot"],
    input_fields: [
      {
        name: "connection",
        label: "Connection environment reference",
        kind: "connection",
        required: false,
      },
      {
        name: "uri",
        label: "Credential-free URI",
        kind: "text",
        required: false,
      },
      { name: "query", label: "Query", kind: "query", required: true },
    ],
    output_fields: [],
    formats: [
      {
        name: "database",
        label: "Database (URI)",
        group: "database",
        extensions: [],
        unstable: false,
        input: {
          modes: [],
          arguments: { snapshot: ["batch_size"] },
          engines_missing: [],
          direct_bounded: false,
          needs_schema_when_bounded: false,
          snapshot_build: "bounded",
          cached_read: true,
        },
        output: null,
      },
    ],
  },
  {
    name: "lakehouse",
    label: "Lakehouse",
    input_available: true,
    output_available: true,
    cache_modes: ["direct", "snapshot"],
    input_fields: [
      {
        name: "path",
        label: "Table locator",
        kind: "path",
        required: true,
      },
    ],
    output_fields: [],
    formats: [
      {
        name: "delta",
        label: "Delta Lake",
        group: "lakehouse",
        extensions: [],
        unstable: false,
        input: {
          modes: ["scan", "read"],
          arguments: { scan: [], read: [] },
          engines_missing: [],
          direct_bounded: true,
          needs_schema_when_bounded: false,
          snapshot_build: "bounded",
          cached_read: true,
        },
        output: null,
      },
    ],
  },
  {
    name: "databricks",
    label: "Databricks",
    input_available: true,
    output_available: false,
    cache_modes: ["snapshot"],
    input_fields: [
      {
        name: "http_path",
        label: "SQL warehouse HTTP path",
        kind: "text",
        required: true,
      },
      { name: "table", label: "Table", kind: "table", required: true },
      {
        name: "query",
        label: "SELECT clause",
        kind: "query",
        required: false,
      },
    ],
    output_fields: [],
    formats: [],
  },
  {
    name: "inline",
    label: "Inline",
    input_available: true,
    output_available: false,
    cache_modes: ["direct"],
    input_fields: [
      { name: "records", label: "Records", kind: "records", required: true },
    ],
    output_fields: [],
    formats: [
      {
        name: "records",
        label: "Inline records",
        group: "inline",
        extensions: [],
        unstable: false,
        input: {
          modes: ["read"],
          arguments: { read: [] },
          engines_missing: [],
          direct_bounded: true,
          needs_schema_when_bounded: false,
          snapshot_build: "unsupported",
          cached_read: false,
        },
        output: null,
      },
    ],
  },
]

const missingSnapshot = {
  schema_version: 1 as const,
  identity_digest: "identity",
  state: "missing" as const,
  freshness: "unknown" as const,
  generation: null,
}

function renderEditor(
  config: Record<string, unknown>,
  onUpdate = vi.fn(),
  onReplaceConfig = vi.fn(),
) {
  return {
    ...render(
      <DataInputEditor
        config={config}
        onUpdate={onUpdate}
        onReplaceConfig={onReplaceConfig}
        accentColor="#123456"
      />,
    ),
    onUpdate,
    onReplaceConfig,
  }
}

beforeEach(() => {
  vi.clearAllMocks()
  resetIoCapabilitiesCacheForTests()
  vi.mocked(fetchIoCapabilities).mockResolvedValue({
    schema_version: 1,
    groups,
  })
  vi.mocked(fetchSchema).mockResolvedValue({
    path: "quotes.csv",
    columns: [
      { name: "policy_id", dtype: "Int64" },
      { name: "premium", dtype: "Float64" },
    ],
    row_count: 2,
    column_count: 2,
    preview: [],
  })
  vi.mocked(getInputCacheStatus).mockResolvedValue(missingSnapshot)
  vi.mocked(buildInputCache).mockResolvedValue({
    schema_version: 1,
    job_id: "job-1",
    identity_digest: "identity",
    status: "running",
    joined: false,
  })
  vi.mocked(getInputCacheJob).mockResolvedValue({
    schema_version: 1,
    job_id: "job-1",
    identity_digest: "identity",
    status: "completed",
    terminal_reason: null,
    message: "Snapshot ready.",
    refresh: false,
    build_class: "bounded",
    progress: {
      phase: "completed",
      rows: 10,
      batches: 1,
      bytes: 100,
      elapsed_seconds: 0.1,
    },
    snapshot: missingSnapshot,
    error_code: null,
  })
})

afterEach(cleanup)

describe("DataInputEditor", () => {
  it("uses the capability schema requirement and preserves other arguments when adopting it", async () => {
    const { onUpdate } = renderEditor({
      inputType: "file",
      cacheMode: "direct",
      format: "csv",
      mode: "scan",
      path: "quotes.csv",
      arguments: { separator: "|", null_values: ["NA"] },
      code: "",
    })

    expect(await screen.findByText("A schema mapping is required for this bounded input.")).toBeInTheDocument()
    fireEvent.click(await screen.findByRole("button", { name: "Use detected schema" }))
    expect(onUpdate).toHaveBeenCalledWith("arguments", {
      separator: "|",
      null_values: ["NA"],
      schema: { policy_id: "Int64", premium: "Float64" },
    })
  })

  it("recovers a failed bounded-schema fetch without changing the path", async () => {
    vi.mocked(fetchSchema).mockRejectedValueOnce(new Error("broken CSV"))
    renderEditor({
      inputType: "file",
      cacheMode: "direct",
      format: "csv",
      mode: "scan",
      path: "quotes.csv",
      arguments: {},
      code: "",
    })

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Could not detect schema: broken CSV",
    )
    fireEvent.click(screen.getByRole("button", { name: "Retry schema" }))

    expect(
      await screen.findByRole("button", { name: "Use detected schema" }),
    ).toBeInTheDocument()
  })

  it("uses backend group order and atomically removes inactive provider keys", async () => {
    const { onReplaceConfig } = renderEditor({
      code: "df",
      path: "old.csv",
      records: [{ old: true }],
    })

    const provider = await screen.findByLabelText("Provider")
    expect(
      Array.from((provider as HTMLSelectElement).options).map(
        (option) => option.text,
      ),
    ).toEqual([
      "Select a provider...",
      "File",
      "Database",
      "Lakehouse",
      "Databricks",
      "Inline",
    ])

    fireEvent.change(provider, { target: { value: "database" } })
    expect(onReplaceConfig).toHaveBeenCalledWith({
      code: "df",
      inputType: "database",
      cacheMode: "snapshot",
      format: "database",
      arguments: {},
      query: "",
    })
  })

  it("preserves provider fields and cache mode when the format changes", async () => {
    const { onReplaceConfig } = renderEditor({
      inputType: "file",
      cacheMode: "snapshot",
      format: "csv",
      mode: "scan",
      path: "quotes.csv",
      arguments: { separator: "|" },
      code: "df.filter(pl.col('x') > 0)",
    })

    fireEvent.change(await screen.findByLabelText("Format"), {
      target: { value: "json" },
    })

    expect(onReplaceConfig).toHaveBeenCalledWith({
      code: "df.filter(pl.col('x') > 0)",
      inputType: "file",
      cacheMode: "snapshot",
      format: "json",
      mode: "read",
      arguments: {},
      path: "quotes.csv",
    })
  })

  it("shows database cache controls and clears the other locator atomically", async () => {
    const { onUpdate } = renderEditor({
      inputType: "database",
      cacheMode: "snapshot",
      format: "database",
      connection: "WAREHOUSE_URI",
      query: "select * from quotes",
      arguments: {},
    })

    expect(await screen.findByText("Snapshot cache")).toBeInTheDocument()
    expect(screen.getByLabelText("Cache mode")).toHaveTextContent(
      "snapshot (required)",
    )
    expect(screen.getByRole("button", { name: "Add argument" })).toBeEnabled()

    const uri = screen.getByLabelText("Credential-free URI")
    fireEvent.change(uri, { target: { value: "sqlite:///quotes.db" } })
    fireEvent.blur(uri)
    expect(onUpdate).toHaveBeenCalledWith({
      uri: "sqlite:///quotes.db",
      connection: "",
    })
  })

  it("keeps the Databricks picker separate from Polars formats", async () => {
    renderEditor({
      inputType: "databricks",
      cacheMode: "snapshot",
      http_path: "/sql/1.0/warehouses/abc",
      table: "catalog.schema.quotes",
      arguments: {},
      code: "df",
    })

    expect(await screen.findByText("SQL Warehouse")).toBeInTheDocument()
    expect(screen.getByText("Snapshot cache")).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Add argument" })).toBeEnabled()
    expect(screen.getByLabelText("Polars code")).toHaveValue("df")
    expect(screen.queryByLabelText("Format")).not.toBeInTheDocument()
  })

  it("removes the optional Databricks SELECT clause when it is cleared", async () => {
    const config = {
      inputType: "databricks",
      cacheMode: "snapshot",
      http_path: "/sql/1.0/warehouses/abc",
      table: "catalog.schema.quotes",
      query: "SELECT id FROM quotes",
      arguments: {},
      code: "df",
    }
    const { onReplaceConfig } = renderEditor(config)

    const query = await screen.findByLabelText("SELECT clause")
    fireEvent.change(query, { target: { value: "   " } })
    fireEvent.blur(query)

    expect(onReplaceConfig).toHaveBeenCalledWith({
      inputType: "databricks",
      cacheMode: "snapshot",
      http_path: "/sql/1.0/warehouses/abc",
      table: "catalog.schema.quotes",
      arguments: {},
      code: "df",
    })
  })

  it("validates inline records and commits one parsed update on blur", async () => {
    const { onUpdate } = renderEditor({
      inputType: "inline",
      cacheMode: "direct",
      format: "records",
      mode: "read",
      records: [],
      arguments: {},
      code: "",
    })

    const records = await screen.findByLabelText("Records")
    fireEvent.change(records, { target: { value: "{}" } })
    expect(screen.getByText("Records must be a JSON array.")).toBeInTheDocument()
    fireEvent.blur(records)
    expect(onUpdate).not.toHaveBeenCalledWith("records", expect.anything())

    fireEvent.change(records, { target: { value: '[{"a": 1}]' } })
    fireEvent.blur(records)
    expect(onUpdate).toHaveBeenCalledWith("records", [{ a: 1 }])
    expect(screen.queryByText("Snapshot cache")).not.toBeInTheDocument()
  })

  it("uses the admitted-eager profile for eager-only snapshot builds", async () => {
    renderEditor({
      inputType: "file",
      cacheMode: "snapshot",
      format: "json",
      mode: "read",
      path: "quotes.json",
      arguments: {},
      code: "",
    })

    const build = await screen.findByRole("button", { name: "Build" })
    expect(
      screen.getByText(/snapshot builds are eager with memory admission/i),
    ).toBeInTheDocument()
    fireEvent.click(build)

    await waitFor(() =>
      expect(buildInputCache).toHaveBeenCalledWith({
        schema_version: 1,
        config: expect.objectContaining({
          inputType: "file",
          format: "json",
        }),
        refresh: false,
        profile: "preview_eager",
      }),
    )
  })

  it("keeps unavailable formats visible but disables snapshot build", async () => {
    renderEditor({
      inputType: "file",
      cacheMode: "snapshot",
      format: "excel",
      mode: "read",
      path: "quotes.xlsx",
      arguments: {},
      code: "",
    })

    expect(
      await screen.findByText(/Missing engine package.*fastexcel/i),
    ).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Build" })).toBeDisabled()
  })
})
