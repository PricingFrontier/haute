import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { render, screen, fireEvent, cleanup, waitFor, act } from "@testing-library/react"
import UtilityPanel from "../UtilityPanel"

// Mock the CodeEditor (heavy CodeMirror dependency)
vi.mock("../editors/CodeEditor", () => ({
  CodeEditor: ({ defaultValue, onChange, placeholder, errorLine }: { defaultValue?: string; onChange?: (v: string) => void; placeholder?: string; errorLine?: number }) => (
    <textarea
      data-testid="code-editor"
      defaultValue={defaultValue}
      onChange={(e) => onChange?.(e.target.value)}
      placeholder={placeholder}
      data-error-line={errorLine}
    />
  ),
}))

// Mock API client — vi.hoisted runs before vi.mock hoisting
const { mockListFiles, mockReadFile, mockCreateFile, mockUpdateFile, mockDeleteFile, MockApiError } = vi.hoisted(() => {
  class MockApiError extends Error {
    status: number
    detail?: string
    constructor(message: string, status: number, detail?: string) {
      super(message)
      this.name = "ApiError"
      this.status = status
      this.detail = detail
    }
  }
  return {
    mockListFiles: vi.fn(),
    mockReadFile: vi.fn(),
    mockCreateFile: vi.fn(),
    mockUpdateFile: vi.fn(),
    mockDeleteFile: vi.fn(),
    MockApiError,
  }
})

vi.mock("../../api/client", () => ({
  ApiError: MockApiError,
  listUtilityFiles: (...args: unknown[]) => mockListFiles(...args),
  readUtilityFile: (...args: unknown[]) => mockReadFile(...args),
  createUtilityFile: (...args: unknown[]) => mockCreateFile(...args),
  updateUtilityFile: (...args: unknown[]) => mockUpdateFile(...args),
  deleteUtilityFile: (...args: unknown[]) => mockDeleteFile(...args),
}))

describe("UtilityPanel", () => {
  const defaultProps = {
    onClose: vi.fn(),
    onImportAdded: vi.fn(),
  }

  beforeEach(() => {
    vi.clearAllMocks()
    mockListFiles.mockResolvedValue({ files: [] })
  })

  afterEach(cleanup)

  it("renders header and close button", () => {
    render(<UtilityPanel {...defaultProps} />)
    expect(screen.getByText("Utility Scripts")).toBeInTheDocument()
    expect(screen.getByTitle("Close")).toBeInTheDocument()
  })

  it("close button calls onClose", () => {
    render(<UtilityPanel {...defaultProps} />)
    fireEvent.click(screen.getByTitle("Close"))
    expect(defaultProps.onClose).toHaveBeenCalledOnce()
  })

  it("shows empty state when no files", async () => {
    render(<UtilityPanel {...defaultProps} />)
    await waitFor(() => expect(screen.getByText("No utility files yet.")).toBeInTheDocument())
  })

  it("shows create button in empty state", async () => {
    render(<UtilityPanel {...defaultProps} />)
    await waitFor(() => expect(screen.getByText("Create one")).toBeInTheDocument())
  })

  it("loads files on mount", async () => {
    mockListFiles.mockResolvedValue({
      files: [{ name: "features.py", module: "features" }],
    })
    mockReadFile.mockResolvedValue({
      name: "features.py", module: "features", content: "x = 1\n",
    })

    render(<UtilityPanel {...defaultProps} />)
    await waitFor(() => expect(mockListFiles).toHaveBeenCalledOnce())
  })

  it("auto-selects and loads first file", async () => {
    mockListFiles.mockResolvedValue({
      files: [{ name: "features.py", module: "features" }],
    })
    mockReadFile.mockResolvedValue({
      name: "features.py", module: "features", content: "def foo(): pass\n",
    })

    render(<UtilityPanel {...defaultProps} />)
    await waitFor(() => expect(mockReadFile).toHaveBeenCalledWith("features"))
    expect(screen.getByTestId("code-editor")).toHaveValue("def foo(): pass\n")
  })

  it("shows module name in dropdown (no utility/ prefix or .py suffix)", async () => {
    mockListFiles.mockResolvedValue({
      files: [{ name: "features.py", module: "features" }],
    })
    mockReadFile.mockResolvedValue({
      name: "features.py", module: "features", content: "x = 1\n",
    })

    render(<UtilityPanel {...defaultProps} />)
    await waitFor(() => {
      expect(screen.getByText("features")).toBeInTheDocument()
      expect(screen.queryByText("utility/features.py")).toBeNull()
      expect(screen.queryByText("features.py")).toBeNull()
    })
  })

  it("does not show a Save button (auto-save mode)", async () => {
    mockListFiles.mockResolvedValue({
      files: [{ name: "features.py", module: "features" }],
    })
    mockReadFile.mockResolvedValue({
      name: "features.py", module: "features", content: "x = 1\n",
    })

    render(<UtilityPanel {...defaultProps} />)
    await waitFor(() => expect(screen.getByTestId("code-editor")).toBeInTheDocument())
    expect(screen.queryByText("Save")).toBeNull()
    expect(screen.queryByText("Unsaved changes")).toBeNull()
    expect(screen.queryByText("Saved")).toBeNull()
  })

  it("calls onImportAdded on create", async () => {
    mockListFiles.mockResolvedValue({ files: [] })
    mockCreateFile.mockResolvedValue({
      status: "ok", name: "helpers.py", module: "helpers",
      import_line: "from utility.helpers import *",
      error: null,
      error_line: null,
    })

    render(<UtilityPanel {...defaultProps} />)
    await waitFor(() => expect(screen.getByText("No utility files yet.")).toBeInTheDocument())

    fireEvent.click(screen.getByText("Create one"))

    const input = screen.getByPlaceholderText("module_name")
    fireEvent.change(input, { target: { value: "helpers" } })
    fireEvent.submit(input.closest("form")!)

    await waitFor(() => {
      expect(mockCreateFile).toHaveBeenCalledWith({ name: "helpers" })
      expect(defaultProps.onImportAdded).toHaveBeenCalledWith("from utility.helpers import *")
    })
  })
})

describe("UtilityPanel auto-save", () => {
  const defaultProps = {
    onClose: vi.fn(),
    onImportAdded: vi.fn(),
  }

  beforeEach(() => {
    vi.clearAllMocks()
    vi.useFakeTimers({ shouldAdvanceTime: true })
    mockListFiles.mockResolvedValue({
      files: [{ name: "features.py", module: "features" }],
    })
    mockReadFile.mockResolvedValue({
      name: "features.py", module: "features", content: "x = 1\n",
    })
  })

  afterEach(() => {
    // Advance timers to flush any pending debounced saves before cleanup
    vi.advanceTimersByTime(1000)
    // Unmount first (triggers clearTimeout in the component) while fake timers are still active
    cleanup()
    vi.useRealTimers()
  })

  it("auto-saves after debounce when code changes", async () => {
    mockUpdateFile.mockResolvedValue({ status: "ok", name: "features.py", module: "features", import_line: "", error: null, error_line: null })

    render(<UtilityPanel {...defaultProps} />)
    await waitFor(() => expect(screen.getByTestId("code-editor")).toBeInTheDocument())

    fireEvent.change(screen.getByTestId("code-editor"), { target: { value: "x = 2\n" } })

    // Should not have saved yet (within debounce window)
    expect(mockUpdateFile).not.toHaveBeenCalled()

    // Advance past the 500ms debounce
    await act(async () => { vi.advanceTimersByTime(600) })

    await waitFor(() => expect(mockUpdateFile).toHaveBeenCalledWith("features", "x = 2\n"))
  })

  it("debounces rapid edits (only saves last value)", async () => {
    mockUpdateFile.mockResolvedValue({ status: "ok", name: "features.py", module: "features", import_line: "", error: null, error_line: null })

    render(<UtilityPanel {...defaultProps} />)
    await waitFor(() => expect(screen.getByTestId("code-editor")).toBeInTheDocument())

    // Rapid edits
    fireEvent.change(screen.getByTestId("code-editor"), { target: { value: "x = 2\n" } })
    await act(async () => { vi.advanceTimersByTime(200) })
    fireEvent.change(screen.getByTestId("code-editor"), { target: { value: "x = 3\n" } })
    await act(async () => { vi.advanceTimersByTime(200) })
    fireEvent.change(screen.getByTestId("code-editor"), { target: { value: "x = 4\n" } })

    // Advance past debounce for the last edit
    await act(async () => { vi.advanceTimersByTime(600) })

    await waitFor(() => {
      expect(mockUpdateFile).toHaveBeenCalledTimes(1)
      expect(mockUpdateFile).toHaveBeenCalledWith("features", "x = 4\n")
    })
  })

  it("shows syntax error from auto-save", async () => {
    // Item #76: backend emits a flat string detail "Syntax error on line N: <msg>".
    mockUpdateFile.mockRejectedValue(
      new MockApiError("HTTP 400", 400, "Syntax error on line 3: unexpected EOF"),
    )

    render(<UtilityPanel {...defaultProps} />)
    await waitFor(() => expect(screen.getByTestId("code-editor")).toBeInTheDocument())

    fireEvent.change(screen.getByTestId("code-editor"), { target: { value: "def foo(\n" } })
    await act(async () => { vi.advanceTimersByTime(600) })

    await waitFor(() => expect(
      screen.getByText("Syntax error on line 3: unexpected EOF"),
    ).toBeInTheDocument())
  })

  it("clears error when user edits again", async () => {
    mockUpdateFile
      .mockRejectedValueOnce(
        new MockApiError("HTTP 400", 400, "Syntax error on line 3: unexpected EOF"),
      )
      .mockResolvedValueOnce({
        status: "ok", name: "features.py", module: "features", import_line: "",
      })

    render(<UtilityPanel {...defaultProps} />)
    await waitFor(() => expect(screen.getByTestId("code-editor")).toBeInTheDocument())

    // Trigger error
    fireEvent.change(screen.getByTestId("code-editor"), { target: { value: "def foo(\n" } })
    await act(async () => { vi.advanceTimersByTime(600) })
    await waitFor(() => expect(
      screen.getByText("Syntax error on line 3: unexpected EOF"),
    ).toBeInTheDocument())

    // Edit again — error should clear immediately
    fireEvent.change(screen.getByTestId("code-editor"), { target: { value: "def foo(): pass\n" } })
    expect(screen.queryByText("Syntax error on line 3: unexpected EOF")).toBeNull()
  })

  it("shows error from failed auto-save", async () => {
    mockUpdateFile.mockRejectedValue(
      new MockApiError("HTTP 400", 400, "Syntax error on line 1: Save failed"),
    )

    render(<UtilityPanel {...defaultProps} />)
    await waitFor(() => expect(screen.getByTestId("code-editor")).toBeInTheDocument())

    fireEvent.change(screen.getByTestId("code-editor"), { target: { value: "x = 2\n" } })
    await act(async () => { vi.advanceTimersByTime(600) })

    await waitFor(() => expect(
      screen.getByText("Syntax error on line 1: Save failed"),
    ).toBeInTheDocument())
  })

  it("sets error-line attribute on editor when save detail mentions a line", async () => {
    // parseSyntaxError extracts the line number from the flat string via /line (\d+)/.
    mockUpdateFile.mockRejectedValue(
      new MockApiError("HTTP 400", 400, "Syntax error on line 5: unexpected token"),
    )

    render(<UtilityPanel {...defaultProps} />)
    await waitFor(() => expect(screen.getByTestId("code-editor")).toBeInTheDocument())

    fireEvent.change(screen.getByTestId("code-editor"), { target: { value: "bad code\n" } })
    await act(async () => { vi.advanceTimersByTime(600) })

    await waitFor(() => {
      expect(screen.getByTestId("code-editor")).toHaveAttribute("data-error-line", "5")
    })
  })
})

// ── U1: dropdown closes on outside click ───────────────────────────

describe("UtilityPanel dropdown close-on-outside-click", () => {
  const defaultProps = {
    onClose: vi.fn(),
    onImportAdded: vi.fn(),
  }

  beforeEach(() => {
    vi.clearAllMocks()
    mockListFiles.mockResolvedValue({
      files: [
        { name: "helpers.py", module: "helpers" },
        { name: "utils.py", module: "utils" },
      ],
    })
    mockReadFile.mockResolvedValue({
      name: "helpers.py", module: "helpers", content: "x = 1\n",
    })
  })

  afterEach(cleanup)

  it("closes dropdown when clicking outside", async () => {
    render(<UtilityPanel {...defaultProps} />)
    await waitFor(() => expect(screen.getByText("helpers")).toBeInTheDocument())

    // Open the dropdown
    fireEvent.click(screen.getByText("helpers"))
    await waitFor(() => expect(screen.getByText("utils")).toBeInTheDocument())

    // Click outside the dropdown (on the document body)
    fireEvent.mouseDown(document.body)

    // Dropdown items should disappear
    await waitFor(() => {
      // "utils" only appears in the dropdown, not as active module
      expect(screen.queryByText("utils")).not.toBeInTheDocument()
    })
  })
})
