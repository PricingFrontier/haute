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
  fetchIoCapabilities: vi.fn(),
  listFiles: vi.fn(() => Promise.resolve({ items: [] })),
  writeOutput: vi.fn(),
}))

import { fetchIoCapabilities, writeOutput } from "../../../api/client"
import { GraphProvider } from "../../GraphContext"
import DataOutputEditor from "../DataOutputEditor"
import { resetIoCapabilitiesCacheForTests } from "../_ioFormats"

const groups: IoCapabilityGroup[] = [
  {
    name: "file",
    label: "File",
    input_available: true,
    output_available: true,
    cache_modes: ["direct", "snapshot"],
    input_fields: [],
    output_fields: [
      { name: "path", label: "Path", kind: "path", required: true },
    ],
    formats: [
      {
        name: "csv",
        label: "CSV",
        group: "file",
        extensions: [".csv"],
        unstable: false,
        input: null,
        output: {
          modes: ["sink", "write"],
          arguments: { sink: ["separator"], write: ["separator"] },
          engines_missing: [],
          native_sink: true,
          eager_writer: true,
          publication: "atomic_file",
        },
      },
      {
        name: "excel",
        label: "Excel",
        group: "file",
        extensions: [".xlsx"],
        unstable: false,
        input: null,
        output: {
          modes: ["write"],
          arguments: { write: [] },
          engines_missing: ["xlsxwriter"],
          native_sink: false,
          eager_writer: true,
          publication: "atomic_file",
        },
      },
    ],
  },
  {
    name: "database",
    label: "Database",
    input_available: true,
    output_available: true,
    cache_modes: ["snapshot"],
    input_fields: [],
    output_fields: [
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
      { name: "table", label: "Table", kind: "text", required: true },
    ],
    formats: [
      {
        name: "database",
        label: "Database (URI)",
        group: "database",
        extensions: [],
        unstable: false,
        input: null,
        output: {
          modes: ["write"],
          arguments: { write: ["if_table_exists"] },
          engines_missing: [],
          native_sink: false,
          eager_writer: true,
          publication: "transactional",
        },
      },
    ],
  },
  {
    name: "lakehouse",
    label: "Lakehouse",
    input_available: true,
    output_available: true,
    cache_modes: ["direct", "snapshot"],
    input_fields: [],
    output_fields: [
      {
        name: "path",
        label: "Table locator",
        kind: "path",
        required: true,
      },
    ],
    formats: [
      {
        name: "delta",
        label: "Delta Lake",
        group: "lakehouse",
        extensions: [],
        unstable: false,
        input: null,
        output: {
          modes: ["sink", "write"],
          arguments: { sink: [], write: [] },
          engines_missing: [],
          native_sink: true,
          eager_writer: true,
          publication: "transactional",
        },
      },
    ],
  },
  {
    name: "databricks",
    label: "Databricks",
    input_available: true,
    output_available: false,
    cache_modes: ["snapshot"],
    input_fields: [],
    output_fields: [],
    formats: [],
  },
  {
    name: "inline",
    label: "Inline",
    input_available: true,
    output_available: false,
    cache_modes: ["direct"],
    input_fields: [],
    output_fields: [],
    formats: [],
  },
]

function renderEditor(
  config: Record<string, unknown>,
  onUpdate = vi.fn(),
  onReplaceConfig = vi.fn(),
) {
  return {
    ...render(
      <GraphProvider allNodes={[]} edges={[]}>
        <DataOutputEditor
          config={config}
          onUpdate={onUpdate}
          onReplaceConfig={onReplaceConfig}
          accentColor="#123456"
          nodeId="output-node"
        />
      </GraphProvider>,
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
  vi.mocked(writeOutput).mockResolvedValue({
    status: "ok",
    message: "Wrote output.",
    row_count: 2,
    path: "out.csv",
    format: "csv",
  })
})

afterEach(cleanup)

describe("DataOutputEditor", () => {
  it("shows only output-capable groups and atomically replaces provider config", async () => {
    const { onReplaceConfig } = renderEditor({
      code: "must disappear",
      path: "old.csv",
      outputType: "file",
      format: "csv",
      mode: "sink",
      arguments: {},
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
    ])

    fireEvent.change(provider, { target: { value: "database" } })
    expect(onReplaceConfig).toHaveBeenCalledWith({
      outputType: "database",
      format: "database",
      mode: "write",
      arguments: {},
      table: "",
    })
  })

  it("preserves the destination while resetting format-specific arguments", async () => {
    const { onReplaceConfig } = renderEditor({
      outputType: "file",
      format: "csv",
      mode: "sink",
      path: "out.csv",
      arguments: { separator: "|" },
    })

    fireEvent.change(await screen.findByLabelText("Format"), {
      target: { value: "excel" },
    })
    expect(onReplaceConfig).toHaveBeenCalledWith({
      outputType: "file",
      format: "excel",
      mode: "write",
      arguments: {},
      path: "out.csv",
    })
  })

  it("gates explicit writes and sends the current graph context", async () => {
    renderEditor({
      outputType: "file",
      format: "csv",
      path: "out.csv",
      arguments: {},
    })

    const write = await screen.findByRole("button", { name: "Write" })
    expect(write).toBeEnabled()
    fireEvent.click(write)

    await waitFor(() =>
      expect(writeOutput).toHaveBeenCalledWith(
        expect.objectContaining({
          nodeId: "output-node",
          graph: expect.objectContaining({ nodes: [], edges: [] }),
        }),
      ),
    )
    expect(await screen.findByRole("status")).toHaveTextContent(
      "Wrote output. | 2 rows | out.csv",
    )
  })

  it("requires exactly one database locator and clears the other atomically", async () => {
    const { onUpdate } = renderEditor({
      outputType: "database",
      format: "database",
      mode: "write",
      connection: "WAREHOUSE_URI",
      table: "analytics.quotes",
      arguments: {},
    })

    expect(await screen.findByRole("button", { name: "Write" })).toBeEnabled()
    expect(screen.getByText(/transactional publication/i)).toBeInTheDocument()

    const uri = screen.getByLabelText("Credential-free URI")
    fireEvent.change(uri, { target: { value: "sqlite:///quotes.db" } })
    fireEvent.blur(uri)
    expect(onUpdate).toHaveBeenCalledWith({
      uri: "sqlite:///quotes.db",
      connection: "",
    })
  })

  it("surfaces missing output engines and disables writes", async () => {
    renderEditor({
      outputType: "file",
      format: "excel",
      mode: "write",
      path: "out.xlsx",
      arguments: {},
    })

    expect(
      await screen.findByText(/Missing engine package.*xlsxwriter/i),
    ).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Write" })).toBeDisabled()
    expect(screen.getByText(/eager writer.*atomic_file/i)).toBeInTheDocument()
  })

  it("has no Polars editor and blocks configs with inactive keys", async () => {
    renderEditor({
      outputType: "file",
      format: "csv",
      mode: "sink",
      path: "out.csv",
      arguments: {},
      code: "df",
    })

    expect(await screen.findByText("Configuration errors")).toBeInTheDocument()
    expect(screen.getByText(/Unexpected configuration keys: code/)).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Write" })).toBeDisabled()
    expect(screen.queryByLabelText("Polars code")).not.toBeInTheDocument()
  })
})
