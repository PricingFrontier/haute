import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { render, screen, fireEvent, cleanup, within } from "@testing-library/react"
import { useState } from "react"

// ---------------------------------------------------------------------------
// Mocks — network mocked only at the fetch-function boundary
// ---------------------------------------------------------------------------

vi.mock("../../../api/client", () => ({
  fetchIoFormats: vi.fn(),
  listFiles: vi.fn(() => Promise.resolve({ items: [] })),
}))

import { fetchIoFormats } from "../../../api/client"
import type { IoFormatCapability } from "../../../api/types"
import { resetIoFormatsCacheForTests } from "../_ioFormats"
import type { OnUpdateConfig } from "../_shared"
import DataOutputEditor from "../DataOutputEditor"

const mockFetchIoFormats = fetchIoFormats as ReturnType<typeof vi.fn>

// ---------------------------------------------------------------------------
// Fixture payload (tests may hard-code format names; components must not)
// ---------------------------------------------------------------------------

const base = {
  extensions: [] as string[],
  unstable: false,
  bounded_read: false,
  needs_schema_when_bounded: false,
  read_available: true,
  write_available: true,
  read_engines_missing: [] as string[],
  write_engines_missing: [] as string[],
}

const FORMATS: IoFormatCapability[] = [
  {
    ...base,
    name: "csv",
    label: "CSV",
    source_kind: "path",
    extensions: [".csv"],
    input_modes: ["scan", "read"],
    output_modes: ["sink", "write"],
    input_arguments: { scan: ["separator"], read: ["separator"] },
    output_arguments: { sink: ["separator"], write: ["separator", "quote_style"] },
  },
  {
    ...base,
    name: "delta",
    label: "Delta Lake",
    source_kind: "path",
    unstable: true,
    write_engines_missing: ["deltalake"],
    input_modes: ["scan"],
    output_modes: ["write"],
    input_arguments: { scan: [] },
    output_arguments: { write: ["mode"] },
  },
  {
    ...base,
    name: "database",
    label: "Database",
    source_kind: "database",
    input_modes: ["read"],
    output_modes: ["write"],
    input_arguments: { read: [] },
    output_arguments: { write: [] },
  },
  {
    ...base,
    name: "records",
    label: "Inline Records",
    source_kind: "inline",
    write_available: false,
    input_modes: ["read"],
    output_modes: [],
    input_arguments: { read: [] },
    output_arguments: {},
  },
]

const defaultProps = () => ({
  config: {} as Record<string, unknown>,
  onUpdate: vi.fn(),
  accentColor: "#009e73",
})

// Non-mocked NodePanel.handleConfigUpdate equivalent: the same shallow
// merge NodePanel performs (see NodePanel.tsx handleConfigUpdate), driven
// through real state so tests can assert the exact merged config object
// (read back from the rendered config-json probe).
function Harness({ initial }: { initial: Record<string, unknown> }) {
  const [config, setConfig] = useState(initial)
  const onUpdate: OnUpdateConfig = (keyOrUpdates, value) => {
    setConfig((current) =>
      typeof keyOrUpdates === "string"
        ? { ...current, [keyOrUpdates]: value }
        : { ...current, ...keyOrUpdates },
    )
  }
  return (
    <>
      <DataOutputEditor config={config} onUpdate={onUpdate} accentColor="#009e73" />
      <pre data-testid="config-json">{JSON.stringify(config)}</pre>
    </>
  )
}

function harnessConfig(): Record<string, unknown> {
  return JSON.parse(screen.getByTestId("config-json").textContent ?? "{}")
}

beforeEach(() => {
  resetIoFormatsCacheForTests()
  mockFetchIoFormats.mockReset()
  mockFetchIoFormats.mockResolvedValue({ formats: FORMATS })
})

afterEach(() => {
  cleanup()
})

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("DataOutputEditor", () => {
  it("renders format options from the capability payload (write-available only)", async () => {
    render(<DataOutputEditor {...defaultProps()} />)
    const select = await screen.findByLabelText("Format")
    const texts = within(select as HTMLElement)
      .getAllByRole("option")
      .map((o) => o.textContent)
    expect(texts).toContain("CSV")
    expect(texts).toContain("Database")
    // write-side engines-missing formats stay selectable but flagged
    expect(texts).toContain("Delta Lake (unstable) — needs one of: deltalake")
    // read-only formats do not appear on the output side
    expect(texts).not.toContain("Inline Records")
  })

  it("shows the engines-missing note for the selected format", async () => {
    const props = defaultProps()
    props.config = { format: "delta" }
    render(<DataOutputEditor {...props} />)
    await screen.findByLabelText("Format")
    expect(screen.getByText(/Missing engine package — needs one of: deltalake/)).toBeInTheDocument()
  })

  it("mode selector uses output modes and persists only an explicit pick", async () => {
    const props = defaultProps()
    props.config = { format: "csv" }
    render(<DataOutputEditor {...props} />)
    const modeSelect = await screen.findByLabelText("Mode")
    expect(modeSelect).toHaveValue("sink")
    expect(props.onUpdate).not.toHaveBeenCalled()
    fireEvent.change(modeSelect, { target: { value: "write" } })
    expect(props.onUpdate).toHaveBeenCalledWith("mode", "write")
  })

  it("drives the real NodePanel merge and preserves unrelated keys exactly", async () => {
    render(
      <Harness
        initial={{
          format: "csv",
          path: "out/old.csv",
          arguments: { separator: "," },
          extra_key: "keep",
        }}
      />,
    )
    const pathInput = await screen.findByLabelText("Path")
    fireEvent.change(pathInput, { target: { value: "out/new.csv" } })
    expect(harnessConfig()).toEqual({
      format: "csv",
      path: "out/new.csv",
      arguments: { separator: "," },
      extra_key: "keep",
    })
  })

  it("surfaces off-spec keys in the unrecognised-keys section instead of dropping them", async () => {
    const props = defaultProps()
    // `records` is an input-side key: off-spec for a path-kind output config
    props.config = { format: "csv", path: "out.csv", records: [{ a: 1 }], not_a_real_key: 123 }
    render(<DataOutputEditor {...props} />)
    await screen.findByLabelText("Format")
    const section = screen.getByTestId("unrecognised-keys")
    expect(within(section).getByText("not_a_real_key")).toBeInTheDocument()
    expect(section.textContent).toContain("not_a_real_key: 123")
    expect(section.textContent).toContain('records: [{"a":1}]')
    // named absence: no editable control for the off-spec key
    expect(screen.queryByLabelText("not_a_real_key")).not.toBeInTheDocument()
    expect(screen.queryByDisplayValue("123")).not.toBeInTheDocument()
  })

  it("shows uri + table fields for database formats", async () => {
    const props = defaultProps()
    props.config = { format: "database" }
    render(<DataOutputEditor {...props} />)
    const uri = await screen.findByLabelText("Connection URI")
    fireEvent.change(uri, { target: { value: "postgres://host/db" } })
    expect(props.onUpdate).toHaveBeenCalledWith("uri", "postgres://host/db")
    const table = screen.getByLabelText("Table")
    fireEvent.change(table, { target: { value: "public.prices" } })
    expect(props.onUpdate).toHaveBeenCalledWith("table", "public.prices")
    // output side has no query field
    expect(screen.queryByLabelText("Query")).not.toBeInTheDocument()
  })

  it("validates argument names against the output arguments for the effective mode", async () => {
    const props = defaultProps()
    props.config = { format: "csv", mode: "write", arguments: { quote_style: "necessary", bogus: 1 } }
    render(<DataOutputEditor {...props} />)
    await screen.findByLabelText("Format")
    expect(screen.getByText(/bogus is not a recognised CSV write argument/)).toBeInTheDocument()
    expect(screen.queryByText(/quote_style is not a recognised/)).not.toBeInTheDocument()
  })

  it("editing an argument value commits the exact merged arguments object", async () => {
    render(<Harness initial={{ format: "csv", path: "out.csv", arguments: { separator: "," } }} />)
    await screen.findByLabelText("Format")
    fireEvent.change(screen.getByLabelText("Argument 1 value"), { target: { value: '";"' } })
    expect(harnessConfig()).toEqual({
      format: "csv",
      path: "out.csv",
      arguments: { separator: ";" },
    })
  })
})
