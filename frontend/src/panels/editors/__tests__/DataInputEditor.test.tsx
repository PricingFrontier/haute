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
import DataInputEditor from "../DataInputEditor"

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
    bounded_read: true,
    input_modes: ["scan", "read"],
    output_modes: ["sink", "write"],
    input_arguments: { scan: ["separator", "has_header"], read: ["separator"] },
    output_arguments: { sink: ["separator"], write: ["separator", "quote_style"] },
  },
  {
    ...base,
    name: "delta",
    label: "Delta Lake",
    source_kind: "path",
    unstable: true,
    read_engines_missing: ["deltalake"],
    write_engines_missing: ["deltalake"],
    input_modes: ["scan"],
    output_modes: ["write"],
    input_arguments: { scan: ["version"] },
    output_arguments: { write: [] },
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
    input_arguments: { read: ["schema"] },
    output_arguments: {},
  },
  {
    ...base,
    name: "write_only",
    label: "Write Only",
    source_kind: "path",
    read_available: false,
    input_modes: [],
    output_modes: ["write"],
    input_arguments: {},
    output_arguments: { write: [] },
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
      <DataInputEditor config={config} onUpdate={onUpdate} accentColor="#009e73" />
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

describe("DataInputEditor", () => {
  it("renders format options from the capability payload (read-available only)", async () => {
    render(<DataInputEditor {...defaultProps()} />)
    const select = await screen.findByLabelText("Format")
    const options = within(select as HTMLElement).getAllByRole("option")
    const texts = options.map((o) => o.textContent)
    expect(texts).toContain("CSV")
    expect(texts).toContain("Database")
    expect(texts).toContain("Inline Records")
    // engines-missing formats are selectable but flagged, not hidden
    expect(texts).toContain("Delta Lake (unstable) — needs one of: deltalake")
    // write-only formats do not appear on the input side
    expect(texts).not.toContain("Write Only")
  })

  it("shows the engines-missing note for the selected format", async () => {
    const props = defaultProps()
    props.config = { format: "delta" }
    render(<DataInputEditor {...props} />)
    await screen.findByLabelText("Format")
    expect(screen.getByText(/Missing engine package — needs one of: deltalake/)).toBeInTheDocument()
    expect(screen.getByText(/Unstable/)).toBeInTheDocument()
  })

  it("selecting a format persists it via onUpdate", async () => {
    const props = defaultProps()
    render(<DataInputEditor {...props} />)
    const select = await screen.findByLabelText("Format")
    fireEvent.change(select, { target: { value: "csv" } })
    expect(props.onUpdate).toHaveBeenCalledWith("format", "csv")
  })

  it("mode selector defaults to the first mode without persisting; explicit pick persists", async () => {
    const props = defaultProps()
    props.config = { format: "csv" }
    render(<DataInputEditor {...props} />)
    const modeSelect = await screen.findByLabelText("Mode")
    expect(modeSelect).toHaveValue("scan")
    // default display alone must not write `mode` into the config
    expect(props.onUpdate).not.toHaveBeenCalled()
    fireEvent.change(modeSelect, { target: { value: "read" } })
    expect(props.onUpdate).toHaveBeenCalledWith("mode", "read")
  })

  it("hides the mode selector for single-mode formats with no persisted mode", async () => {
    const props = defaultProps()
    props.config = { format: "delta" }
    render(<DataInputEditor {...props} />)
    await screen.findByLabelText("Format")
    expect(screen.queryByLabelText("Mode")).not.toBeInTheDocument()
  })

  it("drives the real NodePanel merge and preserves unrelated keys exactly", async () => {
    render(
      <Harness
        initial={{
          format: "csv",
          path: "data/old.csv",
          arguments: { separator: "," },
          legacy_flag: true,
        }}
      />,
    )
    const pathInput = await screen.findByLabelText("Path")
    fireEvent.change(pathInput, { target: { value: "data/new.csv" } })
    expect(harnessConfig()).toEqual({
      format: "csv",
      path: "data/new.csv",
      arguments: { separator: "," },
      legacy_flag: true,
    })
    // a second, different-key update still preserves everything else
    fireEvent.change(screen.getByLabelText("Mode"), { target: { value: "read" } })
    expect(harnessConfig()).toEqual({
      format: "csv",
      mode: "read",
      path: "data/new.csv",
      arguments: { separator: "," },
      legacy_flag: true,
    })
  })

  it("surfaces off-spec keys in the unrecognised-keys section instead of dropping them", async () => {
    const props = defaultProps()
    props.config = { format: "csv", path: "x.csv", legacy_flag: true, uri: "postgres://h/db" }
    render(<DataInputEditor {...props} />)
    await screen.findByLabelText("Format")
    const section = screen.getByTestId("unrecognised-keys")
    // off-spec key
    expect(within(section).getByText("legacy_flag")).toBeInTheDocument()
    expect(section.textContent).toContain("legacy_flag: true")
    // key that belongs to a different source kind (database) is surfaced too
    expect(section.textContent).toContain('uri: "postgres://h/db"')
    // named absence: the off-spec key gets no editable control anywhere
    expect(screen.queryByLabelText("legacy_flag")).not.toBeInTheDocument()
    expect(screen.queryByDisplayValue("postgres://h/db")).not.toBeInTheDocument()
  })

  it("omits the unrecognised-keys section when every key is rendered", async () => {
    const props = defaultProps()
    props.config = { format: "csv", path: "x.csv", arguments: {} }
    render(<DataInputEditor {...props} />)
    await screen.findByLabelText("Format")
    expect(screen.queryByTestId("unrecognised-keys")).not.toBeInTheDocument()
  })

  it("shows uri + query fields for database formats", async () => {
    const props = defaultProps()
    props.config = { format: "database" }
    render(<DataInputEditor {...props} />)
    const uri = await screen.findByLabelText("Connection URI")
    fireEvent.change(uri, { target: { value: "postgres://host/db" } })
    expect(props.onUpdate).toHaveBeenCalledWith("uri", "postgres://host/db")
    const query = screen.getByLabelText("Query")
    fireEvent.change(query, { target: { value: "SELECT 1" } })
    expect(props.onUpdate).toHaveBeenCalledWith("query", "SELECT 1")
  })

  it("shows a validated records textarea for inline formats", async () => {
    const props = defaultProps()
    props.config = { format: "records" }
    render(<DataInputEditor {...props} />)
    const textarea = await screen.findByLabelText("Records")
    fireEvent.change(textarea, { target: { value: '[{"a": 1' } })
    expect(screen.getByText(/Invalid JSON/)).toBeInTheDocument()
    expect(props.onUpdate).not.toHaveBeenCalled()
    fireEvent.change(textarea, { target: { value: '{"a": 1}' } })
    expect(screen.getByText(/Must be a JSON array/)).toBeInTheDocument()
    expect(props.onUpdate).not.toHaveBeenCalled()
    fireEvent.change(textarea, { target: { value: '[{"a": 1}]' } })
    expect(props.onUpdate).toHaveBeenCalledWith("records", [{ a: 1 }])
  })

  it("flags argument names not in the payload's list for the selected format+mode", async () => {
    const props = defaultProps()
    props.config = { format: "csv", arguments: { separator: ",", bogus: 1 } }
    render(<DataInputEditor {...props} />)
    await screen.findByLabelText("Format")
    expect(screen.getByText(/bogus is not a recognised CSV scan argument/)).toBeInTheDocument()
    // the valid name is not flagged
    expect(screen.queryByText(/separator is not a recognised/)).not.toBeInTheDocument()
    // flagged names are still persisted (visible as an editable row)
    expect(screen.getByDisplayValue("bogus")).toBeInTheDocument()
    // datalist offers the payload's argument names for csv+scan
    const datalistOptions = Array.from(document.querySelectorAll("datalist option")).map(
      (o) => (o as HTMLOptionElement).value,
    )
    expect(datalistOptions).toEqual(expect.arrayContaining(["separator", "has_header"]))
  })

  it("adding an argument row commits the exact merged arguments object", async () => {
    render(<Harness initial={{ format: "csv", path: "x.csv", arguments: { separator: "," } }} />)
    await screen.findByLabelText("Format")
    fireEvent.click(screen.getByText("Add argument"))
    fireEvent.change(screen.getByLabelText("Argument 2 name"), { target: { value: "has_header" } })
    fireEvent.change(screen.getByLabelText("Argument 2 value"), { target: { value: "true" } })
    expect(harnessConfig()).toEqual({
      format: "csv",
      path: "x.csv",
      arguments: { separator: ",", has_header: true },
    })
  })
})
