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
  ApiError: class ApiError extends Error {
    status: number
    detail?: string
    constructor(message: string, status: number, detail?: string) {
      super(message)
      this.status = status
      this.detail = detail
    }
  },
  fetchIoCapabilities: vi.fn(),
  listFiles: vi.fn(() => Promise.resolve({ items: [] })),
  resolveOutputDestination: vi.fn(),
  writeOutput: vi.fn(),
}))

import {
  fetchIoCapabilities,
  resolveOutputDestination,
  writeOutput,
} from "../../../api/client"
import { GraphProvider } from "../../GraphContext"
import DataOutputEditor from "../DataOutputEditor"
import { resetIoCapabilitiesCacheForTests } from "../_ioFormats"
import useOutputWriteStore, {
  resetOutputWriteStoreForTests,
} from "../../../stores/useOutputWriteStore"
import type { SimpleNode } from "../_shared"

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
  allNodes: SimpleNode[] = [],
) {
  const element = (nextConfig = config, nextNodes = allNodes) => (
    <GraphProvider allNodes={nextNodes} edges={[]}>
      <DataOutputEditor
        config={nextConfig}
        onUpdate={onUpdate}
        onReplaceConfig={onReplaceConfig}
        accentColor="#123456"
        nodeId="output-node"
      />
    </GraphProvider>
  )
  return {
    ...render(element()),
    renderElement: element,
    onUpdate,
    onReplaceConfig,
  }
}

beforeEach(() => {
  vi.clearAllMocks()
  resetOutputWriteStoreForTests()
  resetIoCapabilitiesCacheForTests()
  vi.mocked(fetchIoCapabilities).mockResolvedValue({
    schema_version: 1,
    groups,
  })
  vi.mocked(resolveOutputDestination).mockResolvedValue({
    path: "outputs/out.csv",
    format: "csv",
    suffix_mismatch: false,
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
          overwrite: false,
          graph: expect.objectContaining({ nodes: [], edges: [] }),
        }),
      ),
    )
    expect(await screen.findByRole("status")).toHaveTextContent(
      "Wrote output. | 2 rows | out.csv",
    )
  })

  it("shows the resolved destination and warns on an incompatible suffix", async () => {
    vi.mocked(resolveOutputDestination).mockResolvedValueOnce({
      path: "server-authoritative/report.json",
      format: "csv",
      suffix_mismatch: true,
    })
    renderEditor({
      outputType: "file",
      format: "csv",
      mode: "sink",
      path: "out.json",
      arguments: {},
    })

    expect(
      await screen.findByText("Destination: server-authoritative/report.json"),
    ).toBeInTheDocument()
    expect(screen.getByRole("alert")).toHaveTextContent("extension does not match")
  })

  it("shows a failed write as an actionable alert and allows retry", async () => {
    vi.mocked(writeOutput).mockRejectedValueOnce(new Error("Output storage is unavailable"))
    renderEditor({
      outputType: "file",
      format: "csv",
      mode: "sink",
      path: "out.csv",
      arguments: {},
    })

    fireEvent.click(await screen.findByRole("button", { name: "Write" }))

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Output storage is unavailable",
    )
    expect(screen.getByRole("button", { name: "Write" })).toBeEnabled()
  })

  it("matches backend extension rules for a bare dotfile destination", async () => {
    vi.mocked(resolveOutputDestination).mockResolvedValueOnce({
      path: "outputs/.report.csv",
      format: "csv",
      suffix_mismatch: false,
    })
    renderEditor({
      outputType: "file",
      format: "csv",
      mode: "sink",
      path: ".report",
      arguments: {},
    })

    expect(
      await screen.findByText("Destination: outputs/.report.csv"),
    ).toBeInTheDocument()
    expect(screen.queryByText(/extension does not match/i)).not.toBeInTheDocument()
  })

  it("keeps a pending write disabled through remount and retries a 409 with overwrite", async () => {
    let resolveWrite: (value: { status: string; message: string }) => void
    vi.mocked(writeOutput).mockReturnValueOnce(new Promise((resolve) => {
      resolveWrite = resolve
    }) as ReturnType<typeof writeOutput>)
    const view = renderEditor({
      outputType: "file",
      format: "csv",
      mode: "sink",
      path: "out",
      arguments: {},
    })

    const write = await screen.findByRole("button", { name: "Write" })
    fireEvent.click(write)
    view.unmount()
    const remounted = renderEditor({
      outputType: "file",
      format: "csv",
      mode: "sink",
      path: "out",
      arguments: {},
    })
    expect(await screen.findByRole("button", { name: "Writing..." })).toBeDisabled()

    resolveWrite!({ status: "ok", message: "Wrote output." })
    await screen.findByText("Wrote output.")

    remounted.unmount()
    renderEditor({
      outputType: "file",
      format: "csv",
      mode: "sink",
      path: "changed-output",
      arguments: {},
    })
    expect(screen.queryByText("Wrote output.")).not.toBeInTheDocument()

    const { ApiError } = await import("../../../api/client")
    vi.mocked(writeOutput).mockRejectedValueOnce(new ApiError("HTTP 409", 409, "out.csv exists"))
    fireEvent.click(screen.getByRole("button", { name: "Write" }))
    expect(await screen.findByRole("button", { name: "Replace existing file" })).toBeInTheDocument()
    vi.mocked(writeOutput).mockResolvedValueOnce({ status: "ok", message: "Replaced." })
    fireEvent.click(screen.getByRole("button", { name: "Replace existing file" }))
    await waitFor(() => expect(writeOutput).toHaveBeenLastCalledWith(expect.objectContaining({ overwrite: true })))
  })

  it("invalidates overwrite confirmation when any upstream graph input changes", async () => {
    const config = {
      outputType: "file",
      format: "csv",
      mode: "sink",
      path: "out.csv",
      arguments: {},
    }
    const nodes = (upstreamValue: number): SimpleNode[] => [
      {
        id: "upstream",
        data: {
          label: "upstream",
          description: "",
          nodeType: "constant",
          config: { values: [{ name: "value", value: upstreamValue }] },
        },
      },
      {
        id: "output-node",
        data: {
          label: "output",
          description: "",
          nodeType: "dataOutput",
          config,
        },
      },
    ]
    const { rerender, renderElement } = renderEditor(
      config,
      vi.fn(),
      vi.fn(),
      nodes(1),
    )
    const { ApiError } = await import("../../../api/client")
    vi.mocked(writeOutput).mockRejectedValueOnce(
      new ApiError("HTTP 409", 409, "out.csv exists"),
    )

    fireEvent.click(await screen.findByRole("button", { name: "Write" }))
    expect(
      await screen.findByRole("button", { name: "Replace existing file" }),
    ).toBeInTheDocument()

    rerender(renderElement(config, nodes(2)))

    expect(
      screen.queryByRole("button", { name: "Replace existing file" }),
    ).not.toBeInTheDocument()
    expect(writeOutput).toHaveBeenCalledTimes(1)
  })

  it("keeps an older-identity pending write visible while config changes", async () => {
    useOutputWriteStore.getState().begin("output-node", "older-request")

    renderEditor({
      outputType: "file",
      format: "csv",
      mode: "sink",
      path: "new-output.csv",
      arguments: {},
    })

    expect(
      await screen.findByRole("button", { name: "Writing..." }),
    ).toBeDisabled()
    expect(screen.getByRole("status")).toHaveTextContent("Writing output")
  })

  it("does not carry an overwrite grant across editor deletion and recreation", async () => {
    const config = {
      outputType: "file",
      format: "csv",
      mode: "sink",
      path: "out.csv",
      arguments: {},
    }
    const { ApiError } = await import("../../../api/client")
    vi.mocked(writeOutput).mockRejectedValueOnce(
      new ApiError("HTTP 409", 409, "out.csv exists"),
    )
    const view = renderEditor(config)

    fireEvent.click(await screen.findByRole("button", { name: "Write" }))
    expect(
      await screen.findByRole("button", { name: "Replace existing file" }),
    ).toBeInTheDocument()

    view.unmount()
    renderEditor(config)

    expect(
      screen.queryByRole("button", { name: "Replace existing file" }),
    ).not.toBeInTheDocument()
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
