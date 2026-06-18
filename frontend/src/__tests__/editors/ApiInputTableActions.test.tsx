/**
 * Tests for the SHARED FrameTableActions wired into the apiInput editor's
 * tables ("push these onto API inputs as well"):
 *   - per-table Copy emits the columns as tab-separated text;
 *   - per-table Share emits the table's schema as JSON;
 *   - Paste-in replaces the table's columns from tab-separated text.
 */
import { describe, it, expect, vi, afterEach, beforeEach } from "vitest"
import { render, screen, fireEvent, cleanup, waitFor } from "@testing-library/react"
import { useState } from "react"
import ApiInputEditor from "../../panels/editors/ApiInputEditor"

// The editor pulls FileBrowser/SchemaPreview (network) + the api client +
// useSchemaFetch; stub them exactly as the main ApiInputEditor suite does.
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
}))
vi.mock("../../hooks/useSchemaFetch", () => ({
  useSchemaFetch: () => ({ schema: null, setSchema: vi.fn(), loading: false, fetchForPath: vi.fn() }),
}))

const originalClipboardDescriptor = Object.getOwnPropertyDescriptor(navigator, "clipboard")
const originalSecure = Object.getOwnPropertyDescriptor(globalThis, "isSecureContext")
function installClipboard(writeText = vi.fn().mockResolvedValue(undefined)) {
  Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true })
  Object.defineProperty(globalThis, "isSecureContext", { value: true, configurable: true })
  return writeText
}
function restoreClipboard() {
  if (originalClipboardDescriptor) Object.defineProperty(navigator, "clipboard", originalClipboardDescriptor)
  else Reflect.deleteProperty(navigator, "clipboard")
  if (originalSecure) Object.defineProperty(globalThis, "isSecureContext", originalSecure)
  else Reflect.deleteProperty(globalThis as object, "isSecureContext")
}

afterEach(() => {
  cleanup()
  restoreClipboard()
  vi.restoreAllMocks()
})

function StatefulHarness({
  initialConfig,
  onUpdateSpy,
}: {
  initialConfig: Record<string, unknown>
  onUpdateSpy: (k: string | Record<string, unknown>, v?: unknown) => void
}) {
  const [config, setConfig] = useState(initialConfig)
  return (
    <ApiInputEditor
      config={config}
      accentColor="#10b981"
      onUpdate={(k, v) => {
        onUpdateSpy(k, v)
        setConfig((prev) => (typeof k === "string" ? { ...prev, [k]: v } : { ...prev, ...k }))
      }}
    />
  )
}

// No `path` key → no cache/infer buttons → no async noise.
const ONE_TABLE = {
  tables: [
    {
      path: "$[:]",
      label: "policies",
      emit: true,
      columns: [
        { name: "policy_id", path: "$[:].policy_id", type: "int", status: "Inferred", selected: true },
        { name: "premium", path: "$[:].premium", type: "float", status: "Inferred", selected: false },
      ],
    },
  ],
}

const lastConfig = (spy: ReturnType<typeof vi.fn>) =>
  spy.mock.calls[spy.mock.calls.length - 1][0] as {
    tables: { columns: { name: string; path: string; type: string; selected: boolean }[] }[]
  }

describe("ApiInputEditor — wired FrameTableActions", () => {
  beforeEach(() => installClipboard())

  it("per-table Copy emits the columns as tab-separated text", async () => {
    const writeText = installClipboard()
    render(<StatefulHarness initialConfig={ONE_TABLE} onUpdateSpy={vi.fn()} />)
    fireEvent.click(screen.getByTestId("api-input-table-0-table-copy"))
    await waitFor(() => expect(writeText).toHaveBeenCalledTimes(1))
    const tsv = writeText.mock.calls[0][0] as string
    expect(tsv).toBe(
      "name\tpath\ttype\tselected\npolicy_id\t$[:].policy_id\tint\ttrue\npremium\t$[:].premium\tfloat\tfalse",
    )
  })

  it("per-table Share emits the table's schema as JSON", async () => {
    const writeText = installClipboard()
    render(<StatefulHarness initialConfig={ONE_TABLE} onUpdateSpy={vi.fn()} />)
    fireEvent.click(screen.getByTestId("api-input-table-0-table-share"))
    await waitFor(() => expect(writeText).toHaveBeenCalledTimes(1))
    const json = JSON.parse(writeText.mock.calls[0][0] as string)
    expect(json.label).toBe("policies")
    expect(json.columns.map((c: { name: string }) => c.name)).toEqual(["policy_id", "premium"])
  })

  it("Paste-in replaces the table's columns from tab-separated text", () => {
    const onUpdateSpy = vi.fn()
    render(<StatefulHarness initialConfig={ONE_TABLE} onUpdateSpy={onUpdateSpy} />)
    fireEvent.click(screen.getByTestId("api-input-table-0-table-paste-toggle"))
    fireEvent.change(screen.getByTestId("api-input-table-0-table-paste-input"), {
      target: {
        value:
          "name\tpath\ttype\tselected\nfoo\t$[:].foo\tstr\ttrue\nbar\t$[:].bar\tbogus\tfalse",
      },
    })
    fireEvent.click(screen.getByTestId("api-input-table-0-table-paste-apply"))

    const cfg = lastConfig(onUpdateSpy)
    const cols = cfg.tables[0].columns
    expect(cols.map((c) => c.name)).toEqual(["foo", "bar"])
    expect(cols[0]).toMatchObject({ path: "$[:].foo", type: "str", selected: true })
    // Unknown type coerces to "str"; selected parsed false.
    expect(cols[1]).toMatchObject({ path: "$[:].bar", type: "str", selected: false })
  })
})
