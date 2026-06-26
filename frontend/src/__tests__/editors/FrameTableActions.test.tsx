/**
 * Tests for the SHARED FrameTableActions component + its clipboard/download
 * helpers (shared/tableClipboard).
 *
 * Covered:
 *   - COPY writes the grid as TAB-separated text (header + body), paste-able back;
 *   - SHARE writes the schema-mapping JSON;
 *   - SAVE (JSON/CSV/TSV) triggers a blob download via URL.createObjectURL;
 *   - PASTE-IN parses tab-separated text → rows and hands them to onPaste;
 *   - non-secure / clipboard-absent context disables Copy + Share with a reason.
 */
import { describe, it, expect, vi, afterEach, beforeEach } from "vitest"
import { render, screen, fireEvent, cleanup, waitFor } from "@testing-library/react"
import { FrameTableActions } from "../../panels/editors/FrameTableActions"
import {
  buildTsv,
  buildCsv,
  parsePastedGrid,
} from "../../panels/editors/shared/tableClipboard"

const originalClipboardDescriptor = Object.getOwnPropertyDescriptor(navigator, "clipboard")
const originalSecure = Object.getOwnPropertyDescriptor(globalThis, "isSecureContext")

function installClipboard(writeText = vi.fn().mockResolvedValue(undefined)) {
  Object.defineProperty(navigator, "clipboard", {
    value: { writeText },
    configurable: true,
  })
  // The component gates on a secure context too.
  Object.defineProperty(globalThis, "isSecureContext", {
    value: true,
    configurable: true,
  })
  return writeText
}

function restoreClipboard() {
  if (originalClipboardDescriptor) {
    Object.defineProperty(navigator, "clipboard", originalClipboardDescriptor)
  } else {
    Reflect.deleteProperty(navigator, "clipboard")
  }
  if (originalSecure) {
    Object.defineProperty(globalThis, "isSecureContext", originalSecure)
  } else {
    Reflect.deleteProperty(globalThis as object, "isSecureContext")
  }
}

const GRID = {
  headers: ["column", "path", "enabled"],
  rows: [
    ["policy_id", "$[:].policy_id", "true"],
    ["premium", "$[:].premium", "true"],
  ],
}
const SCHEMA = { outputMapping: [{ source_port: "policies", source_column: "policy_id" }] }

function renderActions(overrides: Partial<React.ComponentProps<typeof FrameTableActions>> = {}) {
  return render(
    <FrameTableActions
      testIdPrefix="t"
      filename="frame"
      getGrid={() => GRID}
      getSchema={() => SCHEMA}
      onPaste={vi.fn()}
      {...overrides}
    />,
  )
}

afterEach(() => {
  cleanup()
  restoreClipboard()
  vi.restoreAllMocks()
})

describe("FrameTableActions — copy / share (clipboard)", () => {
  beforeEach(() => installClipboard())

  it("Copy writes the grid as TAB-separated text (header + body)", async () => {
    const writeText = installClipboard()
    renderActions()
    fireEvent.click(screen.getByTestId("t-copy"))
    await waitFor(() => expect(writeText).toHaveBeenCalledTimes(1))
    const tsv = writeText.mock.calls[0][0] as string
    expect(tsv).toBe(buildTsv([GRID.headers, ...GRID.rows]))
    // Round-trips through the paste parser back to the same matrix.
    expect(parsePastedGrid(tsv)).toEqual([GRID.headers, ...GRID.rows])
    // It IS tab-separated, not comma.
    expect(tsv.split("\n")[1]).toBe("policy_id\t$[:].policy_id\ttrue")
  })

  it("Share writes the schema-mapping JSON", async () => {
    const writeText = installClipboard()
    renderActions()
    fireEvent.click(screen.getByTestId("t-share"))
    await waitFor(() => expect(writeText).toHaveBeenCalledTimes(1))
    expect(JSON.parse(writeText.mock.calls[0][0] as string)).toEqual(SCHEMA)
  })

  it("flashes an acknowledgement after a successful copy", async () => {
    installClipboard()
    renderActions()
    fireEvent.click(screen.getByTestId("t-copy"))
    await waitFor(() => expect(screen.getByTestId("t-ack")).toBeTruthy())
  })
})

describe("FrameTableActions — save (download)", () => {
  beforeEach(() => installClipboard())

  it("Save JSON triggers a blob download of the schema JSON", () => {
    const createObjectURL = vi
      .spyOn(URL, "createObjectURL")
      .mockReturnValue("blob:fake")
    vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => {})
    const clickSpy = vi
      .spyOn(HTMLAnchorElement.prototype, "click")
      .mockImplementation(() => {})
    renderActions()
    fireEvent.click(screen.getByTestId("t-save-json"))
    expect(createObjectURL).toHaveBeenCalledTimes(1)
    const blob = createObjectURL.mock.calls[0][0] as Blob
    expect(blob.type).toBe("application/json")
    expect(clickSpy).toHaveBeenCalledTimes(1)
  })

  it("Save CSV serialises the grid as comma-separated text", async () => {
    const blobs: Blob[] = []
    vi.spyOn(URL, "createObjectURL").mockImplementation((b: Blob | MediaSource) => {
      blobs.push(b as Blob)
      return "blob:fake"
    })
    vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => {})
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {})
    renderActions()
    fireEvent.click(screen.getByTestId("t-save-csv"))
    expect(blobs).toHaveLength(1)
    expect(blobs[0].type).toBe("text/csv")
    const text = await blobs[0].text()
    expect(text).toBe(buildCsv([GRID.headers, ...GRID.rows]))
    expect(text.split("\n")[1]).toBe("policy_id,$[:].policy_id,true")
  })

  it("Save TSV serialises the grid as tab-separated text", async () => {
    const blobs: Blob[] = []
    vi.spyOn(URL, "createObjectURL").mockImplementation((b: Blob | MediaSource) => {
      blobs.push(b as Blob)
      return "blob:fake"
    })
    vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => {})
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {})
    renderActions()
    fireEvent.click(screen.getByTestId("t-save-tsv"))
    const text = await blobs[0].text()
    expect(text).toBe(buildTsv([GRID.headers, ...GRID.rows]))
  })
})

describe("FrameTableActions — paste-in", () => {
  beforeEach(() => installClipboard())

  it("parses tab-separated text into rows and hands them to onPaste", () => {
    const onPaste = vi.fn()
    renderActions({ onPaste })
    fireEvent.click(screen.getByTestId("t-paste-toggle"))
    const input = screen.getByTestId("t-paste-input") as HTMLTextAreaElement
    fireEvent.change(input, {
      target: { value: "alpha\t$[:].a\nbeta\t$[:].b" },
    })
    fireEvent.click(screen.getByTestId("t-paste-apply"))
    expect(onPaste).toHaveBeenCalledTimes(1)
    expect(onPaste.mock.calls[0][0]).toEqual([
      ["alpha", "$[:].a"],
      ["beta", "$[:].b"],
    ])
  })

  it("hides the paste affordance when pasteable=false", () => {
    renderActions({ pasteable: false })
    expect(screen.queryByTestId("t-paste-toggle")).toBeNull()
  })
})

describe("FrameTableActions — non-secure / clipboard-absent guard", () => {
  it("disables Copy and Share when the Clipboard API is unavailable", () => {
    // Default jsdom: no navigator.clipboard, isSecureContext undefined.
    restoreClipboard()
    renderActions()
    expect((screen.getByTestId("t-copy") as HTMLButtonElement).disabled).toBe(true)
    expect((screen.getByTestId("t-share") as HTMLButtonElement).disabled).toBe(true)
    // Save (download) does NOT require the clipboard and stays enabled.
    expect((screen.getByTestId("t-save-json") as HTMLButtonElement).disabled).toBe(false)
  })

  it("disables Copy/Share in an insecure context even when clipboard exists", () => {
    installClipboard()
    Object.defineProperty(globalThis, "isSecureContext", {
      value: false,
      configurable: true,
    })
    renderActions()
    expect((screen.getByTestId("t-copy") as HTMLButtonElement).disabled).toBe(true)
  })
})
