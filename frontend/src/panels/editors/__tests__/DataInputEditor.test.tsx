import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
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
  fetchSchema,
  fetchIoCapabilities,
} from "../../../api/client"
import DataInputEditor from "../DataInputEditor"
import { resetIoCapabilitiesRequestForTests } from "../_ioFormats"

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
        name: "parquet",
        label: "Parquet",
        group: "file",
        extensions: [".parquet"],
        unstable: false,
        input: {
          modes: ["scan"],
          arguments: { scan: [] },
          engines_missing: [],
          cache_mode: "direct",
          direct_bounded: true,
          needs_schema_when_bounded: false,
          snapshot_build: "bounded",
          cached_read: true,
        },
        output: null,
      },
      {
        name: "csv",
        label: "CSV",
        group: "file",
        extensions: [".csv"],
        unstable: false,
        input: {
          modes: ["scan"],
          arguments: { scan: ["separator"] },
          engines_missing: [],
          cache_mode: "snapshot",
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
          cache_mode: "snapshot",
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
          cache_mode: "snapshot",
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
          cache_mode: "snapshot",
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
    cache_modes: ["snapshot"],
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
          cache_mode: "snapshot",
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
    cache_modes: ["snapshot"],
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
          cache_mode: "snapshot",
          direct_bounded: true,
          needs_schema_when_bounded: false,
          snapshot_build: "bounded",
          cached_read: true,
        },
        output: null,
      },
    ],
  },
]

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
  resetIoCapabilitiesRequestForTests()
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
})

afterEach(cleanup)

describe("DataInputEditor", () => {
  it("scans Parquet directly without a one-option mode", async () => {
    renderEditor({
      inputType: "file",
      format: "parquet",
      mode: "scan",
      path: "quotes.parquet",
      arguments: {},
      code: "",
    })

    expect(await screen.findByLabelText("Format")).toHaveValue("parquet")
    expect(screen.queryByLabelText("Mode")).not.toBeInTheDocument()
    expect(screen.queryByText(/Data Input requires cache mode/)).not.toBeInTheDocument()

    fireEvent.click(screen.getByTestId("file-change-btn"))
    expect(screen.queryByRole("textbox", { name: "Path" })).not.toBeInTheDocument()
    expect(screen.getAllByText("quotes.parquet")).toHaveLength(1)
  })

  it("accepts read mode for a Parquet input", async () => {
    renderEditor({
      inputType: "file",
      format: "parquet",
      mode: "read",
      path: "quotes.parquet",
      arguments: {},
      code: "",
    })

    expect(await screen.findByLabelText("Format")).toHaveValue("parquet")
    expect(screen.queryByText("The selected mode is not valid for this format.")).not.toBeInTheDocument()
  })

  it("uses the capability schema requirement and preserves other arguments when adopting it", async () => {
    const { onUpdate } = renderEditor({
      inputType: "file",
      format: "csv",
      mode: "scan",
      path: "quotes.csv",
      arguments: { separator: "|", null_values: ["NA"] },
      code: "",
    })

    expect(await screen.findByText("A schema mapping is required for this bounded input.")).toBeInTheDocument()
    expect(screen.queryByLabelText("Cache mode")).not.toBeInTheDocument()
    fireEvent.click(await screen.findByRole("button", { name: "Use detected schema" }))
    expect(onUpdate).toHaveBeenCalledWith("arguments", {
      separator: "|",
      null_values: ["NA"],
      schema: { policy_id: "Int64", premium: "Float64" },
    })
  })

  it("tolerates a leftover cacheMode key and never migrates it", async () => {
    const onUpdate = vi.fn(() => ({ ok: true as const }))
    renderEditor({
      inputType: "file",
      cacheMode: "direct",
      format: "csv",
      mode: "scan",
      path: "quotes.csv",
      arguments: { schema: { policy_id: "Int64" } },
      code: "",
    }, onUpdate)

    expect(await screen.findByLabelText("Format")).toHaveValue("csv")
    expect(screen.queryByText(/Data Input requires cache mode/)).not.toBeInTheDocument()
    expect(onUpdate).not.toHaveBeenCalledWith("cacheMode", expect.anything())
  })

  it("recovers a failed bounded-schema fetch without changing the path", async () => {
    vi.mocked(fetchSchema).mockRejectedValueOnce(new Error("broken CSV"))
    renderEditor({
      inputType: "file",
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

    const provider = await screen.findByRole("radiogroup", { name: "Provider" })
    expect(
      within(provider).getAllByRole("radio").map((option) => option.textContent),
    ).toEqual([
      "File",
      "Database",
      "Lakehouse",
      "Databricks",
      "Inline",
    ])

    fireEvent.click(within(provider).getByRole("radio", { name: "Database" }))
    expect(onReplaceConfig).toHaveBeenCalledWith({
      code: "df",
      inputType: "database",
      format: "database",
      arguments: {},
      query: "",
    })
  })

  it("preserves provider fields when the format changes", async () => {
    const { onReplaceConfig } = renderEditor({
      inputType: "file",
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
      format: "json",
      mode: "read",
      arguments: {},
      path: "quotes.csv",
    })
  })

  it.each([
    ["an empty list", []],
    ["a populated list", [{ id: "l", kind: "limit", n: 2 }]],
  ])("keeps %s of post-load steps when the format or the provider changes", async (_label, steps) => {
    const { onReplaceConfig } = renderEditor({
      inputType: "file",
      format: "csv",
      mode: "scan",
      path: "quotes.csv",
      arguments: {},
      steps,
    })

    fireEvent.change(await screen.findByLabelText("Format"), {
      target: { value: "json" },
    })
    expect(onReplaceConfig).toHaveBeenLastCalledWith({
      steps,
      inputType: "file",
      format: "json",
      mode: "read",
      arguments: {},
      path: "quotes.csv",
    })

    const provider = await screen.findByRole("radiogroup", { name: "Provider" })
    fireEvent.click(within(provider).getByRole("radio", { name: "Databricks" }))
    expect(onReplaceConfig).toHaveBeenLastCalledWith(
      expect.objectContaining({ steps, inputType: "databricks" }),
    )
  })

  it.each([
    ["a new node's empty step list", { inputType: "file", format: "parquet", mode: "scan", path: "", arguments: {}, steps: [] }],
    ["step editor state", { inputType: "file", format: "csv", mode: "scan", path: "quotes.csv", arguments: {}, steps: [{ id: "f", kind: "filter", match: "all", conditions: [] }], _steps_error: "Step 1: Add at least one condition." }],
    ["a discarded step list on Databricks", { inputType: "databricks", http_path: "/sql/1", table: "cat.schema.t", arguments: {}, code: "", _steps_discarded: "Steps were discarded because the body changed." }],
  ])("reports no configuration error for %s", async (_label, config) => {
    renderEditor(config)
    await screen.findByRole("radiogroup", { name: "Provider" })
    expect(screen.queryByText(/Unexpected configuration keys/)).not.toBeInTheDocument()
  })

  it("still reports a genuinely unknown configuration key", async () => {
    renderEditor({ inputType: "file", format: "csv", mode: "scan", path: "quotes.csv", arguments: {}, stepz: [] })
    await screen.findByLabelText("Format")
    expect(screen.getByText(/Unexpected configuration keys: stepz\./)).toBeInTheDocument()
  })

  it("switches to Parquet without authoring a cache-mode field", async () => {
    const { onReplaceConfig } = renderEditor({
      inputType: "file",
      format: "csv",
      mode: "scan",
      path: "quotes.csv",
      arguments: { separator: "|" },
      code: "df",
    })

    fireEvent.change(await screen.findByLabelText("Format"), {
      target: { value: "parquet" },
    })

    expect(onReplaceConfig).toHaveBeenCalledWith({
      code: "df",
      inputType: "file",
      format: "parquet",
      mode: "scan",
      arguments: {},
      path: "quotes.csv",
    })
  })

  it("clears the other database locator atomically", async () => {
    const { onUpdate } = renderEditor({
      inputType: "database",
      format: "database",
      connection: "WAREHOUSE_URI",
      query: "select * from quotes",
      arguments: {},
    })

    const uri = await screen.findByLabelText("Credential-free URI")
    expect(screen.queryByLabelText("Cache mode")).not.toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Add argument" })).toBeEnabled()

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
      http_path: "/sql/1.0/warehouses/abc",
      table: "catalog.schema.quotes",
      arguments: {},
      code: "df",
    })

    expect(await screen.findByText("SQL Warehouse")).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Add argument" })).toBeEnabled()
    expect(screen.queryByLabelText("Format")).not.toBeInTheDocument()
  })

  it("removes the optional Databricks SELECT clause when it is cleared", async () => {
    const config = {
      inputType: "databricks",
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
      http_path: "/sql/1.0/warehouses/abc",
      table: "catalog.schema.quotes",
      arguments: {},
      code: "df",
    })
  })

  it("validates inline records and commits one parsed update on blur", async () => {
    const { onUpdate } = renderEditor({
      inputType: "inline",
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
    expect(screen.queryByLabelText("Cache mode")).not.toBeInTheDocument()
  })

  it("keeps unavailable formats visible with the missing engine named", async () => {
    renderEditor({
      inputType: "file",
      format: "excel",
      mode: "read",
      path: "quotes.xlsx",
      arguments: {},
      code: "",
    })

    expect(
      await screen.findByText(/Missing engine package.*fastexcel/i),
    ).toBeInTheDocument()
  })
})
